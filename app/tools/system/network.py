"""Network diagnostic tools: interfaces, routes, connectivity and DNS.

All four observe and all four run for real. Nothing here changes a route, an
address or a resolver - the ``ip`` calls are ``show`` only, and the reachability
probes open a socket and close it.

Two different mechanisms, chosen per tool rather than uniformly:

``net.interfaces`` / ``net.routes``
    ``ip -j``. The kernel's own view, emitted as JSON so there is no column
    scraping. ``iproute2`` is in the deployed image for this reason.

``net.ping`` / ``net.dns``
    The standard library. ``getaddrinfo`` is the same resolver the rest of
    FIRDAY uses, which is the point of a DNS diagnostic, and a TCP probe needs
    no binary at all - so ``net.ping`` still answers on a host with no ``ping``.
"""

import asyncio
import re
import socket
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.registry import register_tool
from app.system.command import is_available, reject_option_like, run_command_async
from app.system.errors import CommandNotAvailableError, SystemToolError
from app.tools.system.base import SystemTool, read_permissions

DOMAIN = "network"

NETWORK_READ = read_permissions("network.read")
NETWORK_PROBE = read_permissions("network.read", "network.probe", network=True)

IP_BINARY = "ip"
PING_BINARY = "ping"
RESOLV_CONF = Path("/etc/resolv.conf")

IP_HINT = "network tools need iproute2; install it or add it to the image"


async def _ip_json(arguments: list[str], timeout: float) -> list[dict]:
    """Run ``ip -j <arguments>`` and return its parsed array."""
    import json

    if not is_available(IP_BINARY):
        raise CommandNotAvailableError(IP_BINARY, IP_HINT)

    result = await run_command_async([IP_BINARY, "-json", *arguments], timeout=timeout)
    result.require_ok()
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SystemToolError(f"`ip {' '.join(arguments)}` did not return JSON: {exc}") from exc
    return [entry for entry in payload if isinstance(entry, dict)]


def _timeout() -> float:
    from app.config import settings

    return settings.system_command_timeout_seconds


# --- net.interfaces --------------------------------------------------------


class InterfaceAddress(BaseModel):
    family: str
    address: str
    prefix_length: int | None = None
    scope: str = ""


class NetworkInterface(BaseModel):
    name: str
    index: int | None = None
    state: str = ""
    up: bool = False
    loopback: bool = False
    mtu: int | None = None
    mac_address: str | None = None
    flags: list[str] = Field(default_factory=list)
    addresses: list[InterfaceAddress] = Field(default_factory=list)


class InterfacesInput(BaseModel):
    include_down: bool = Field(True, description="Include interfaces that are not up.")
    name: str | None = Field(None, description="Only report this interface.")


class InterfacesOutput(BaseModel):
    interfaces: list[NetworkInterface]
    interface_count: int


@register_tool
class NetworkInterfacesTool(SystemTool):
    name = "net.interfaces"
    domain = DOMAIN
    operation = "interfaces"
    description = "List this host's network interfaces and their addresses."
    version = "1.0.0"
    permissions = NETWORK_READ
    input_model = InterfacesInput
    output_model = InterfacesOutput

    def audited_targets(self, payload: InterfacesInput) -> list[str]:
        return [payload.name] if payload.name else []

    async def operate(
        self, payload: InterfacesInput, context: ToolExecutionContext
    ) -> InterfacesOutput:
        arguments = ["address", "show"]
        if payload.name:
            arguments.append(reject_option_like("name", payload.name))

        entries = await _ip_json(arguments, _timeout())
        interfaces = [self._interface(entry) for entry in entries]
        if not payload.include_down:
            interfaces = [i for i in interfaces if i.up]

        return InterfacesOutput(interfaces=interfaces, interface_count=len(interfaces))

    @staticmethod
    def _interface(entry: dict) -> NetworkInterface:
        flags = [str(f) for f in (entry.get("flags") or [])]
        state = str(entry.get("operstate", ""))
        return NetworkInterface(
            name=str(entry.get("ifname", "")),
            index=entry.get("ifindex"),
            state=state,
            up="UP" in flags or state == "UP",
            loopback="LOOPBACK" in flags,
            mtu=entry.get("mtu"),
            mac_address=entry.get("address"),
            flags=flags,
            addresses=[
                InterfaceAddress(
                    family=str(info.get("family", "")),
                    address=str(info.get("local", "")),
                    prefix_length=info.get("prefixlen"),
                    scope=str(info.get("scope", "")),
                )
                for info in (entry.get("addr_info") or [])
                if isinstance(info, dict) and info.get("local")
            ],
        )


# --- net.routes ------------------------------------------------------------


class Route(BaseModel):
    destination: str
    gateway: str | None = None
    interface: str = ""
    family: str = "inet"
    protocol: str = ""
    scope: str = ""
    preferred_source: str | None = None
    metric: int | None = None


class RoutesInput(BaseModel):
    family: Literal["inet", "inet6", "all"] = Field(
        "inet", description="Address family to report."
    )


class RoutesOutput(BaseModel):
    routes: list[Route]
    route_count: int
    default_gateway: str | None = None
    default_interface: str | None = None


@register_tool
class NetworkRoutesTool(SystemTool):
    name = "net.routes"
    domain = DOMAIN
    operation = "routes"
    description = "List this host's IP routing table."
    version = "1.0.0"
    permissions = NETWORK_READ
    input_model = RoutesInput
    output_model = RoutesOutput

    async def operate(self, payload: RoutesInput, context: ToolExecutionContext) -> RoutesOutput:
        families = ("inet", "inet6") if payload.family == "all" else (payload.family,)

        routes: list[Route] = []
        for family in families:
            flag = ["-6"] if family == "inet6" else ["-4"]
            for entry in await _ip_json([*flag, "route", "show"], _timeout()):
                routes.append(
                    Route(
                        destination=str(entry.get("dst", "")),
                        gateway=entry.get("gateway"),
                        interface=str(entry.get("dev", "")),
                        family=family,
                        protocol=str(entry.get("protocol", "")),
                        scope=str(entry.get("scope", "")),
                        preferred_source=entry.get("prefsrc"),
                        metric=entry.get("metric"),
                    )
                )

        default = next((r for r in routes if r.destination == "default"), None)
        return RoutesOutput(
            routes=routes,
            route_count=len(routes),
            default_gateway=default.gateway if default else None,
            default_interface=default.interface if default else None,
        )


# --- net.ping --------------------------------------------------------------

PING_STATS = re.compile(
    r"(\d+) packets transmitted, (\d+) (?:packets )?received.*?(\d+(?:\.\d+)?)% packet loss",
    re.DOTALL,
)
PING_RTT = re.compile(
    r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
    r"([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)"
)


class PingInput(BaseModel):
    host: str = Field(..., min_length=1, description="Hostname or IP address to probe.")
    count: int = Field(2, ge=1, description="Echo requests to send. Clamped to the cap.")
    timeout_seconds: float = Field(5.0, gt=0, le=60, description="Overall deadline.")
    port: int | None = Field(
        None,
        ge=1,
        le=65535,
        description="When set, probe with a TCP connection to this port instead of ICMP. "
        "Use where ICMP is blocked or `ping` is unavailable.",
    )


class PingOutput(BaseModel):
    host: str
    method: Literal["icmp", "tcp"]
    reachable: bool
    packets_sent: int = 0
    packets_received: int = 0
    packet_loss_percent: float | None = None
    rtt_min_ms: float | None = None
    rtt_avg_ms: float | None = None
    rtt_max_ms: float | None = None
    resolved_address: str | None = None
    port: int | None = None
    detail: str = ""


@register_tool
class PingTool(SystemTool):
    name = "net.ping"
    domain = DOMAIN
    operation = "ping"
    description = (
        "Check connectivity to a host with ICMP echo requests, or with a TCP "
        "connection when a port is given."
    )
    version = "1.0.0"
    permissions = NETWORK_PROBE
    input_model = PingInput
    output_model = PingOutput

    def audited_targets(self, payload: PingInput) -> list[str]:
        return [payload.host if payload.port is None else f"{payload.host}:{payload.port}"]

    async def operate(self, payload: PingInput, context: ToolExecutionContext) -> PingOutput:
        host = reject_option_like("host", payload.host)
        if payload.port is not None:
            return await self._tcp_probe(host, payload)
        return await self._icmp_probe(host, payload)

    async def _icmp_probe(self, host: str, payload: PingInput) -> PingOutput:
        from app.config import settings

        if not is_available(PING_BINARY):
            raise CommandNotAvailableError(
                PING_BINARY, "pass a `port` to probe with TCP instead, which needs no binary"
            )

        count = min(payload.count, settings.system_max_ping_count)
        deadline = max(1, int(payload.timeout_seconds))
        result = await run_command_async(
            [PING_BINARY, "-n", "-c", str(count), "-w", str(deadline), "--", host],
            # Give the subprocess a moment past ping's own deadline to exit.
            timeout=payload.timeout_seconds + 5,
        )

        sent = received = 0
        loss: float | None = None
        stats = PING_STATS.search(result.stdout)
        if stats:
            sent, received, loss = int(stats[1]), int(stats[2]), float(stats[3])

        rtt = PING_RTT.search(result.stdout)
        # A non-zero exit with no statistics block means ping never got as far
        # as sending - an unknown host, or no permission for a raw socket.
        detail = _last_line(result.stdout if stats else (result.stderr or result.stdout))

        return PingOutput(
            host=host,
            method="icmp",
            reachable=received > 0,
            packets_sent=sent,
            packets_received=received,
            packet_loss_percent=loss,
            rtt_min_ms=float(rtt[1]) if rtt else None,
            rtt_avg_ms=float(rtt[2]) if rtt else None,
            rtt_max_ms=float(rtt[3]) if rtt else None,
            resolved_address=_first_address(result.stdout),
            detail=detail,
        )

    async def _tcp_probe(self, host: str, payload: PingInput) -> PingOutput:
        def probe() -> tuple[bool, float, str | None, str]:
            started = time.perf_counter()
            try:
                with socket.create_connection(
                    (host, payload.port), timeout=payload.timeout_seconds
                ) as connection:
                    peer = connection.getpeername()[0]
                return True, (time.perf_counter() - started) * 1000, peer, "connected"
            except OSError as exc:
                return False, (time.perf_counter() - started) * 1000, None, str(exc)

        reachable, elapsed, peer, detail = await asyncio.to_thread(probe)
        return PingOutput(
            host=host,
            method="tcp",
            reachable=reachable,
            packets_sent=1,
            packets_received=1 if reachable else 0,
            packet_loss_percent=0.0 if reachable else 100.0,
            rtt_min_ms=elapsed if reachable else None,
            rtt_avg_ms=elapsed if reachable else None,
            rtt_max_ms=elapsed if reachable else None,
            resolved_address=peer,
            port=payload.port,
            detail=detail,
        )


def _first_address(stdout: str) -> str | None:
    match = re.search(r"PING [^ ]+ \(([^)]+)\)", stdout)
    return match[1] if match else None


def _last_line(text: str) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no response"


# --- net.dns ---------------------------------------------------------------


class ResolvedAddress(BaseModel):
    address: str
    family: str
    socket_type: str = ""


class DnsInput(BaseModel):
    host: str = Field(..., min_length=1, description="Hostname to resolve, or IP to reverse.")
    reverse: bool = Field(
        True, description="Also attempt a reverse lookup when the answer is an address."
    )
    timeout_seconds: float = Field(5.0, gt=0, le=60, description="Deadline for the lookup.")


class DnsOutput(BaseModel):
    host: str
    resolved: bool
    canonical_name: str = ""
    addresses: list[ResolvedAddress] = Field(default_factory=list)
    reverse_names: list[str] = Field(default_factory=list)
    nameservers: list[str] = Field(default_factory=list)
    search_domains: list[str] = Field(default_factory=list)
    lookup_ms: float = 0.0
    error: str | None = None


def read_resolv_conf(path: Path = RESOLV_CONF) -> tuple[list[str], list[str]]:
    """The configured nameservers and search domains, best effort."""
    nameservers: list[str] = []
    search: list[str] = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return nameservers, search

    for line in lines:
        parts = line.split("#", 1)[0].split()
        if not parts:
            continue
        if parts[0] == "nameserver" and len(parts) > 1:
            nameservers.append(parts[1])
        elif parts[0] in ("search", "domain"):
            search.extend(parts[1:])
    return nameservers, search


_FAMILY_NAMES = {socket.AF_INET: "inet", socket.AF_INET6: "inet6"}
_SOCKET_TYPE_NAMES = {socket.SOCK_STREAM: "tcp", socket.SOCK_DGRAM: "udp"}


@register_tool
class DnsTool(SystemTool):
    name = "net.dns"
    domain = DOMAIN
    operation = "dns"
    description = (
        "Resolve a hostname through this host's configured resolver, and report "
        "the nameservers it used."
    )
    version = "1.0.0"
    permissions = NETWORK_PROBE
    input_model = DnsInput
    output_model = DnsOutput

    def audited_targets(self, payload: DnsInput) -> list[str]:
        return [payload.host]

    async def operate(self, payload: DnsInput, context: ToolExecutionContext) -> DnsOutput:
        host = reject_option_like("host", payload.host)
        nameservers, search = read_resolv_conf()

        started = time.perf_counter()
        try:
            entries = await asyncio.wait_for(
                asyncio.to_thread(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM),
                timeout=payload.timeout_seconds,
            )
            error = None
        except asyncio.TimeoutError:
            entries, error = [], f"lookup did not finish within {payload.timeout_seconds:g}s"
        except socket.gaierror as exc:
            entries, error = [], f"{exc.strerror or exc}"
        elapsed = (time.perf_counter() - started) * 1000

        addresses: list[ResolvedAddress] = []
        canonical = ""
        seen: set[tuple[str, str]] = set()
        for family, socket_type, _proto, canonname, sockaddr in entries:
            address = str(sockaddr[0])
            key = (address, _FAMILY_NAMES.get(family, str(family)))
            if key in seen:
                continue
            seen.add(key)
            canonical = canonical or (canonname or "")
            addresses.append(
                ResolvedAddress(
                    address=address,
                    family=_FAMILY_NAMES.get(family, str(family)),
                    socket_type=_SOCKET_TYPE_NAMES.get(socket_type, ""),
                )
            )

        reverse_names: list[str] = []
        if payload.reverse and addresses:
            reverse_names = await self._reverse(addresses[0].address, payload.timeout_seconds)

        return DnsOutput(
            host=host,
            resolved=bool(addresses),
            canonical_name=canonical,
            addresses=addresses,
            reverse_names=reverse_names,
            nameservers=nameservers,
            search_domains=search,
            lookup_ms=elapsed,
            error=error,
        )

    @staticmethod
    async def _reverse(address: str, timeout: float) -> list[str]:
        def lookup() -> list[str]:
            try:
                name, aliases, _ = socket.gethostbyaddr(address)
            except OSError:
                return []
            return [name, *aliases]

        try:
            return await asyncio.wait_for(asyncio.to_thread(lookup), timeout=timeout)
        except asyncio.TimeoutError:
            return []
