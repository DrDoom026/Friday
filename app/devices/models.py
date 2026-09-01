"""Data models for device / remote machine management (PART 5).

A device is any machine FIRDAY knows about - starting with the Pi it runs on.
This module is the vocabulary: what a device *is*, what it *claims it can do*,
whether we currently believe it is reachable, and how much we trust it.

Trust and permissions are recorded here but nothing enforces them. The
Security/Permission Engine that reads these fields and decides ALLOW/DENY is
PART 7, exactly as ``ToolPermissions`` is declared-but-unenforced in Part 2.
"""

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: How long after ``last_seen`` a device stops counting as freshly observed.
DEFAULT_STALE_AFTER_SECONDS = 300


def new_device_id() -> str:
    """A device id, in the same shape as Part 1's correlation ids."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Platform(str, Enum):
    """The device's operating system family."""

    LINUX = "linux"
    DARWIN = "darwin"
    WINDOWS = "windows"
    ANDROID = "android"
    IOS = "ios"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, raw: str | None) -> "Platform":
        """Best-effort normalization of the many spellings of an OS name.

        Tailscale reports ``macOS``/``iOS``, ``platform.system()`` reports
        ``Darwin``, and Python's ``sys.platform`` reports ``darwin``/``win32``.
        """
        text = (raw or "").strip().lower()
        if not text:
            return cls.UNKNOWN
        if text.startswith("linux"):
            return cls.LINUX
        if text in {"darwin", "macos", "mac os x", "osx"} or text.startswith("mac"):
            return cls.DARWIN
        if text.startswith("win"):
            return cls.WINDOWS
        if text.startswith("android"):
            return cls.ANDROID
        if text in {"ios", "ipados"}:
            return cls.IOS
        return cls.UNKNOWN


class DeviceStatus(str, Enum):
    """What we currently believe about the device's reachability.

    ``UNKNOWN`` is deliberately distinct from ``OFFLINE``: it means we have not
    heard from the device recently enough to say, not that we observed it down.
    """

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class TrustState(str, Enum):
    """How far this device's identity has been established.

    ``REVOKED`` is sticky - a trust evaluation never silently upgrades out of
    it, only an explicit operator action does.
    """

    TRUSTED = "trusted"
    UNVERIFIED = "unverified"
    UNTRUSTED = "untrusted"
    REVOKED = "revoked"


class TransportKind(str, Enum):
    """How FIRDAY would reach the device. Only ``LOCAL`` is implemented."""

    LOCAL = "local"
    SSH = "ssh"
    TAILSCALE = "tailscale"
    AGENT = "agent"


class DeviceCapability(BaseModel):
    """One thing a device claims it can do.

    A claim, not a guarantee: PART 5 records capabilities so future code can
    select devices by them. Nothing here executes anything.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="Capability id, e.g. 'fs.read'.")
    version: str = Field("1.0.0", description="Version of the capability contract.")
    description: str = Field("", description="Human-readable summary of the claim.")
    available: bool = Field(True, description="False when claimed but currently unusable.")
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Free-form details, e.g. limits or paths."
    )


class DevicePermissions(BaseModel):
    """What a device would be allowed to do.

    Declared metadata only, in the same spirit as ``ToolPermissions``. PART 7
    is the first thing that will read these to make a decision.
    """

    model_config = ConfigDict(frozen=True)

    scopes: tuple[str, ...] = ()
    allow_filesystem: bool = False
    allow_network: bool = False
    allow_remote_execution: bool = False
    requires_confirmation: bool = True


class NetworkIdentity(BaseModel):
    """How the device is addressed on the network, Tailscale aside."""

    model_config = ConfigDict(frozen=True)

    hostname: str = Field("", description="Reported hostname.")
    fqdn: str = Field("", description="Fully qualified name, when known.")
    addresses: tuple[str, ...] = Field((), description="Known IP addresses.")

    def primary_address(self) -> str | None:
        return self.addresses[0] if self.addresses else None


class TailscaleIdentity(BaseModel):
    """A device's identity as Tailscale reports it.

    This is FIRDAY's trust anchor. It is only ever built from something
    Tailscale told us - a serve/funnel identity header, or a LocalAPI/CLI
    lookup - never from a field the caller supplied about itself.
    """

    model_config = ConfigDict(frozen=True)

    node_id: str = Field("", description="Stable node id, e.g. 'nABC...CNTRL'.")
    dns_name: str = Field("", description="MagicDNS name, trailing dot stripped.")
    hostname: str = Field("", description="Tailscale's view of the hostname.")
    addresses: tuple[str, ...] = Field((), description="Tailscale IPs for the node.")
    user_login: str = Field("", description="Tailnet user login, e.g. 'someone@github'.")
    user_display_name: str = Field("", description="Tailnet user display name.")
    os: str = Field("", description="OS string as Tailscale reports it.")
    online: bool | None = Field(None, description="Tailscale's own online flag, when known.")
    source: str = Field(
        "unknown", description="How this was obtained: 'header', 'whois', 'status' or 'test'."
    )

    def address(self) -> str | None:
        return self.addresses[0] if self.addresses else None

    def is_identified(self) -> bool:
        """True when Tailscale gave us something that actually names a node."""
        return bool(self.node_id or self.dns_name or self.user_login)


class Device(BaseModel):
    """A machine FIRDAY knows about."""

    device_id: str = Field(default_factory=new_device_id)
    name: str = Field(..., min_length=1, description="Human-facing device name.")
    platform: Platform = Platform.UNKNOWN
    architecture: str = Field("unknown", description="e.g. 'x86_64', 'aarch64'.")
    network: NetworkIdentity = Field(default_factory=NetworkIdentity)
    tailscale: TailscaleIdentity | None = None
    capabilities: tuple[DeviceCapability, ...] = ()
    status: DeviceStatus = DeviceStatus.UNKNOWN
    trust: TrustState = TrustState.UNVERIFIED
    permissions: DevicePermissions = Field(default_factory=DevicePermissions)
    transport: TransportKind = TransportKind.LOCAL
    last_seen: datetime | None = None
    registered_at: datetime = Field(default_factory=utcnow)
    trust_reason: str = Field("", description="Why the trust state is what it is.")

    # --- capabilities ------------------------------------------------------

    def capability_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.capabilities)

    def has_capability(self, name: str, *, require_available: bool = True) -> bool:
        """True when the device claims ``name`` (and, by default, claims it usable)."""
        return any(
            c.name == name and (c.available or not require_available) for c in self.capabilities
        )

    # --- status ------------------------------------------------------------

    def mark_seen(self, *, at: datetime | None = None, status: DeviceStatus | None = None) -> None:
        """Record an observation: refresh ``last_seen`` and the status."""
        self.last_seen = at or utcnow()
        self.status = status or DeviceStatus.ONLINE

    def is_stale(
        self, *, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS, now: datetime | None = None
    ) -> bool:
        """True when we have not observed the device recently enough to be sure."""
        if self.last_seen is None:
            return True
        reference = now or utcnow()
        return reference - self.last_seen > timedelta(seconds=stale_after_seconds)

    # --- trust -------------------------------------------------------------

    def is_trusted(self) -> bool:
        return self.trust is TrustState.TRUSTED


class DeviceRegistration(BaseModel):
    """What a caller supplies to register a device.

    Note what is absent: ``trust`` and the Tailscale identity. A device does not
    get to assert either about itself - FIRDAY derives them from Tailscale.
    """

    name: str = Field(..., min_length=1)
    device_id: str | None = Field(None, description="Reuse an id, or omit for a new one.")
    platform: str = Field("unknown", description="OS name; normalized into a Platform.")
    architecture: str = "unknown"
    network: NetworkIdentity = Field(default_factory=NetworkIdentity)
    capabilities: tuple[DeviceCapability, ...] = ()
    permissions: DevicePermissions = Field(default_factory=DevicePermissions)
    transport: TransportKind = TransportKind.LOCAL

    def to_device(self) -> Device:
        """Build the stored device. Trust stays unverified until evaluated."""
        return Device(
            device_id=self.device_id or new_device_id(),
            name=self.name,
            platform=Platform.coerce(self.platform),
            architecture=self.architecture,
            network=self.network,
            capabilities=self.capabilities,
            permissions=self.permissions,
            transport=self.transport,
        )
