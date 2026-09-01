"""Device selection: how future code picks the right machine for a job.

A :class:`DeviceQuery` is a declarative filter - capability, trust, status,
platform, transport - that the registry applies. It exists now so PART 6+ can
ask "which trusted, online device can do ``fs.read``?" without every caller
re-implementing the same loop.

Selection never grants anything. A query can ask for trusted devices; it cannot
make a device trusted.
"""

from dataclasses import dataclass, field

from app.devices.models import Device, DeviceStatus, Platform, TransportKind, TrustState


@dataclass(frozen=True)
class DeviceQuery:
    """A filter over devices. Every populated field must match (logical AND)."""

    capabilities: tuple[str, ...] = ()
    require_available_capabilities: bool = True
    trust: tuple[TrustState, ...] = ()
    status: tuple[DeviceStatus, ...] = ()
    platform: tuple[Platform, ...] = ()
    transport: tuple[TransportKind, ...] = ()
    name_contains: str = ""
    device_ids: tuple[str, ...] = ()

    @classmethod
    def trusted_online(cls, *capabilities: str) -> "DeviceQuery":
        """The common case: a trusted device we believe is reachable right now."""
        return cls(
            capabilities=tuple(capabilities),
            trust=(TrustState.TRUSTED,),
            status=(DeviceStatus.ONLINE,),
        )

    def matches(self, device: Device) -> bool:
        if self.device_ids and device.device_id not in self.device_ids:
            return False
        if self.trust and device.trust not in self.trust:
            return False
        if self.status and device.status not in self.status:
            return False
        if self.platform and device.platform not in self.platform:
            return False
        if self.transport and device.transport not in self.transport:
            return False
        if self.name_contains and self.name_contains.lower() not in device.name.lower():
            return False
        return all(
            device.has_capability(name, require_available=self.require_available_capabilities)
            for name in self.capabilities
        )

    def describe(self) -> dict:
        """A serializable summary, for logging which query produced a choice."""
        return {
            "capabilities": list(self.capabilities),
            "trust": [t.value for t in self.trust],
            "status": [s.value for s in self.status],
            "platform": [p.value for p in self.platform],
            "transport": [t.value for t in self.transport],
            "name_contains": self.name_contains,
            "device_ids": list(self.device_ids),
        }


@dataclass(frozen=True)
class SelectionResult:
    """What a selection found, and what it was asked for."""

    query: DeviceQuery
    devices: tuple[Device, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        return bool(self.devices)

    def first(self) -> Device | None:
        return self.devices[0] if self.devices else None


def rank(devices: "list[Device] | tuple[Device, ...]") -> list[Device]:
    """Order candidates best-first: trusted before not, online before not.

    A deterministic tiebreak on name keeps selection reproducible, which matters
    once a planner starts choosing devices on its own.
    """
    trust_rank = {
        TrustState.TRUSTED: 0,
        TrustState.UNVERIFIED: 1,
        TrustState.UNTRUSTED: 2,
        TrustState.REVOKED: 3,
    }
    status_rank = {DeviceStatus.ONLINE: 0, DeviceStatus.UNKNOWN: 1, DeviceStatus.OFFLINE: 2}
    return sorted(
        devices,
        key=lambda d: (
            trust_rank.get(d.trust, 9),
            status_rank.get(d.status, 9),
            d.name.lower(),
            d.device_id,
        ),
    )
