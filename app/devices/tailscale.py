"""Tailscale identity: FIRDAY's device trust anchor.

Device trust is not a token system. Any device talking to FIRDAY is expected to
already be on the tailnet, so the question "who is this?" is answered by asking
Tailscale, never by checking a secret the caller supplied.

There are two ways to get that answer, and both are supported because which one
is available depends on how FIRDAY is exposed:

``header``
    When FIRDAY sits behind ``tailscale serve`` / ``funnel``, the proxy injects
    identity headers it will not let a client forge. This is the cheapest and
    most direct signal.

``whois``
    When FIRDAY is reached directly on its tailnet address, there are no
    headers - but the peer's source IP is a tailnet IP, and ``tailscale whois``
    resolves it to a node and user via the local ``tailscaled``.

Both are read-only lookups against the local daemon. Shelling out to the CLI is
enough for this milestone, so no Tailscale client library is added; the
:class:`TailscaleIdentitySource` seam means swapping in the LocalAPI socket
later touches one class.
"""

import json
import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Mapping

from app.devices.models import TailscaleIdentity

logger = logging.getLogger("firday.devices.tailscale")

#: Headers injected by ``tailscale serve``/``funnel``. A client cannot set these
#: through the proxy - it strips and replaces them.
HEADER_USER_LOGIN = "tailscale-user-login"
HEADER_USER_NAME = "tailscale-user-name"

DEFAULT_TIMEOUT_SECONDS = 5.0


def _strip_dot(name: str) -> str:
    return name.rstrip(".")


def _addresses(raw: Any) -> tuple[str, ...]:
    """Normalize Tailscale's address lists, which may carry a /32 or /128."""
    if not isinstance(raw, list):
        return ()
    return tuple(str(a).split("/")[0] for a in raw if a)


def identity_from_headers(headers: Mapping[str, str]) -> TailscaleIdentity | None:
    """Build an identity from ``tailscale serve`` headers, or ``None``.

    Header names are matched case-insensitively, as HTTP requires.
    """
    lowered = {str(k).lower(): v for k, v in headers.items()}
    login = (lowered.get(HEADER_USER_LOGIN) or "").strip()
    display = (lowered.get(HEADER_USER_NAME) or "").strip()
    if not login and not display:
        return None
    return TailscaleIdentity(
        user_login=login,
        user_display_name=display,
        source="header",
    )


class TailscaleIdentitySource(ABC):
    """Where Tailscale identities come from. One seam, two implementations."""

    @abstractmethod
    def whois(self, address: str) -> TailscaleIdentity | None:
        """Resolve a tailnet address to the node and user behind it."""

    @abstractmethod
    def self_identity(self) -> TailscaleIdentity | None:
        """The identity of the node FIRDAY itself is running on."""

    @abstractmethod
    def is_available(self) -> bool:
        """True when this source can currently answer lookups."""


class TailscaleCli(TailscaleIdentitySource):
    """Reads identities from the local ``tailscale`` CLI.

    Every call is read-only (``whois``, ``status``) and hits the local daemon,
    so there is no network round trip and nothing to authenticate.
    """

    def __init__(
        self, binary: str = "tailscale", *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.binary = binary
        self.timeout = timeout

    # --- lookups -----------------------------------------------------------

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None

    def whois(self, address: str) -> TailscaleIdentity | None:
        if not address:
            return None
        payload = self._run_json(["whois", "--json", address])
        if payload is None:
            return None
        return self._identity_from_whois(payload)

    def self_identity(self) -> TailscaleIdentity | None:
        payload = self._run_json(["status", "--json"])
        if not payload:
            return None
        node = payload.get("Self")
        if not isinstance(node, dict):
            return None
        users = payload.get("User") or {}
        profile = users.get(str(node.get("UserID"))) or {}
        return TailscaleIdentity(
            node_id=str(node.get("ID") or ""),
            dns_name=_strip_dot(str(node.get("DNSName") or "")),
            hostname=str(node.get("HostName") or ""),
            addresses=_addresses(node.get("TailscaleIPs")),
            user_login=str(profile.get("LoginName") or ""),
            user_display_name=str(profile.get("DisplayName") or ""),
            os=str(node.get("OS") or ""),
            online=node.get("Online"),
            source="status",
        )

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _identity_from_whois(payload: dict) -> TailscaleIdentity | None:
        node = payload.get("Node")
        if not isinstance(node, dict):
            return None
        hostinfo = node.get("Hostinfo") or {}
        profile = payload.get("UserProfile") or {}
        return TailscaleIdentity(
            node_id=str(node.get("StableID") or ""),
            dns_name=_strip_dot(str(node.get("Name") or "")),
            hostname=str(hostinfo.get("Hostname") or ""),
            addresses=_addresses(node.get("Addresses")),
            user_login=str(profile.get("LoginName") or ""),
            user_display_name=str(profile.get("DisplayName") or ""),
            os=str(hostinfo.get("OS") or ""),
            source="whois",
        )

    def _run_json(self, args: list[str]) -> dict | None:
        """Run a CLI subcommand and parse its JSON, or return ``None``.

        A missing binary, a daemon that is down, a timeout, an unknown peer
        (``peer not found``) and malformed output all mean the same thing to the
        caller: Tailscale could not identify this. None of them are fatal - the
        device simply stays unverified.
        """
        if not self.is_available():
            logger.debug("tailscale binary %r not found", self.binary)
            return None
        try:
            completed = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("tailscale %s failed: %s", " ".join(args), exc)
            return None

        output = (completed.stdout or "").strip()
        if completed.returncode != 0 or not output:
            logger.debug(
                "tailscale %s returned %s: %s",
                " ".join(args),
                completed.returncode,
                (completed.stderr or output or "").strip()[:200],
            )
            return None
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            # `whois` prints a bare "peer not found" for an unknown address.
            logger.debug("tailscale %s returned non-JSON: %s", " ".join(args), output[:120])
            return None
        return parsed if isinstance(parsed, dict) else None


class StaticTailscaleDirectory(TailscaleIdentitySource):
    """An in-memory source. For tests and for deployments without Tailscale."""

    def __init__(
        self,
        identities: Mapping[str, TailscaleIdentity] | None = None,
        *,
        self_identity: TailscaleIdentity | None = None,
        available: bool = True,
    ) -> None:
        self._identities = dict(identities or {})
        self._self = self_identity
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def whois(self, address: str) -> TailscaleIdentity | None:
        return self._identities.get(address) if self._available else None

    def self_identity(self) -> TailscaleIdentity | None:
        return self._self if self._available else None

    def add(self, address: str, identity: TailscaleIdentity) -> None:
        self._identities[address] = identity


class TailscaleResolver:
    """Resolves the identity behind a request, preferring headers over whois."""

    def __init__(self, source: TailscaleIdentitySource | None = None) -> None:
        self.source = source if source is not None else TailscaleCli()

    def resolve(
        self, *, headers: Mapping[str, str] | None = None, peer_address: str | None = None
    ) -> TailscaleIdentity | None:
        """Identify the caller, or return ``None`` if Tailscale cannot.

        Headers win when present: behind ``tailscale serve`` the peer address is
        the proxy's, not the client's. Falling back to ``whois`` on the source
        address covers a direct tailnet connection. When headers name a user and
        whois also resolves the address, the two are merged so the record keeps
        both the node and the proxy-asserted login.
        """
        from_headers = identity_from_headers(headers or {})
        from_whois = self.source.whois(peer_address) if peer_address else None

        if from_headers and from_whois:
            return from_whois.model_copy(
                update={
                    "user_login": from_headers.user_login or from_whois.user_login,
                    "user_display_name": (
                        from_headers.user_display_name or from_whois.user_display_name
                    ),
                    "source": "header+whois",
                }
            )
        return from_headers or from_whois
