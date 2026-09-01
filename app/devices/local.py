"""Describing the machine FIRDAY is running on.

The Pi is the one real device in PART 5, so it registers itself at startup.
Its capabilities are not invented here - they are read off the Part 2 tool
registry, so "what this device can do" and "what tools FIRDAY has" cannot
drift apart. Disabled tools are recorded as claimed-but-unavailable, which is
exactly what ``fs.delete`` is until PART 7.
"""

import logging
import platform
import socket

from app.devices.models import (
    Device,
    DeviceCapability,
    DevicePermissions,
    DeviceRegistration,
    NetworkIdentity,
    TransportKind,
)

logger = logging.getLogger("firday.devices.local")

#: Registered tools that exist but always refuse. Kept in step with Part 4.
_DISABLED_TOOL_NAMES = frozenset({"fs.delete", "fs.move", "fs.rename"})

#: A stable id for "this machine", so restarts do not pile up duplicates.
LOCAL_DEVICE_ID = "local"


def local_addresses() -> tuple[str, ...]:
    """Best-effort local addresses via DNS. Never raises.

    Off the startup path by default: ``gethostbyname_ex`` blocks on the
    resolver, which costs a full DNS timeout per call on a host whose own
    hostname does not resolve - exactly the case on a fresh Pi. The addresses
    that matter for a device are the Tailscale ones, and those come from
    Tailscale, not from DNS.
    """
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
    except (OSError, UnicodeError):
        return ()
    return tuple(dict.fromkeys(addresses))


def capabilities_from_tools(tool_registry) -> tuple[DeviceCapability, ...]:
    """Turn the registered tools into capability claims for this device."""
    capabilities = []
    for tool in tool_registry:
        capabilities.append(
            DeviceCapability(
                name=tool.name,
                version=tool.version,
                description=tool.description,
                available=tool.name not in _DISABLED_TOOL_NAMES,
                attributes={"side_effect": tool.permissions.side_effect.value},
            )
        )
    return tuple(capabilities)


def build_local_device(
    tool_registry=None, *, device_id: str = LOCAL_DEVICE_ID, resolve_dns: bool = False
) -> Device:
    """Describe this machine as a device, without contacting Tailscale.

    The Tailscale identity and the trust state are attached separately, by the
    registry's trust policy - this function only reports what the host says
    about itself, which is never a basis for trust.

    ``resolve_dns`` is off by default because this runs during startup and a
    reverse/forward lookup on an unresolvable hostname blocks for the full DNS
    timeout. Tailscale supplies the addresses that matter.
    """
    capabilities: tuple[DeviceCapability, ...] = ()
    if tool_registry is not None:
        capabilities = capabilities_from_tools(tool_registry)

    hostname = socket.gethostname()
    registration = DeviceRegistration(
        name=hostname,
        device_id=device_id,
        platform=platform.system(),
        architecture=platform.machine() or "unknown",
        network=NetworkIdentity(
            hostname=hostname,
            fqdn=socket.getfqdn() if resolve_dns else "",
            addresses=local_addresses() if resolve_dns else (),
        ),
        capabilities=capabilities,
        permissions=DevicePermissions(
            scopes=("fs.read", "fs.write"),
            allow_filesystem=True,
            allow_network=True,
            allow_remote_execution=False,
            requires_confirmation=True,
        ),
        transport=TransportKind.LOCAL,
    )
    return registration.to_device()
