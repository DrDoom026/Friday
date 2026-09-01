"""Device / remote machine management (PART 5).

What FIRDAY knows about the machines it can act on: a device model, a registry,
a trust abstraction anchored on Tailscale identity, status tracking, capability
claims, selection, and a transport interface whose only real implementation is
``local``.

Nothing here executes anything on a device, and nothing here enforces trust -
trust is a recorded state, and the engine that turns it into ALLOW/DENY is
PART 7.
"""

from app.devices.errors import (
    DeviceAlreadyRegisteredError,
    DeviceError,
    DeviceNotFoundError,
    TailscaleUnavailableError,
    TransportNotImplementedError,
)
from app.devices.local import LOCAL_DEVICE_ID, build_local_device, capabilities_from_tools
from app.devices.models import (
    Device,
    DeviceCapability,
    DevicePermissions,
    DeviceRegistration,
    DeviceStatus,
    NetworkIdentity,
    Platform,
    TailscaleIdentity,
    TransportKind,
    TrustState,
)
from app.devices.registry import DeviceRegistry, default_device_registry
from app.devices.selection import DeviceQuery, SelectionResult, rank
from app.devices.service import DeviceService
from app.devices.tailscale import (
    StaticTailscaleDirectory,
    TailscaleCli,
    TailscaleIdentitySource,
    TailscaleResolver,
    identity_from_headers,
)
from app.devices.transport import (
    AgentTransport,
    LocalTransport,
    SshTransport,
    TailscaleTransport,
    Transport,
    TransportProbe,
    TransportRegistry,
)
from app.devices.trust import (
    AlwaysUnverifiedPolicy,
    TailnetTrustPolicy,
    TrustDecision,
    TrustPolicy,
    revoke,
)

__all__ = [
    "AgentTransport",
    "AlwaysUnverifiedPolicy",
    "Device",
    "DeviceAlreadyRegisteredError",
    "DeviceCapability",
    "DeviceError",
    "DeviceNotFoundError",
    "DevicePermissions",
    "DeviceQuery",
    "DeviceRegistration",
    "DeviceRegistry",
    "DeviceService",
    "DeviceStatus",
    "LOCAL_DEVICE_ID",
    "LocalTransport",
    "NetworkIdentity",
    "Platform",
    "SelectionResult",
    "SshTransport",
    "StaticTailscaleDirectory",
    "TailnetTrustPolicy",
    "TailscaleCli",
    "TailscaleIdentity",
    "TailscaleIdentitySource",
    "TailscaleResolver",
    "TailscaleTransport",
    "TailscaleUnavailableError",
    "Transport",
    "TransportKind",
    "TransportNotImplementedError",
    "TransportProbe",
    "TransportRegistry",
    "TrustDecision",
    "TrustPolicy",
    "TrustState",
    "build_local_device",
    "capabilities_from_tools",
    "default_device_registry",
    "identity_from_headers",
    "rank",
    "revoke",
]
