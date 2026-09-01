"""Device registry: register devices and look them up.

Deliberately shaped like Part 2's :class:`~app.core.registry.ToolRegistry` -
``register`` / ``get`` / ``try_get`` / ``unregister`` / ``names`` / ``describe``
plus the container dunders - so the two registries read the same way. The
differences are the ones devices actually need: mutable state (status, trust,
``last_seen``) and querying by capability rather than only by id.

Not thread-safe, matching ToolRegistry. Registration happens on request, which
FastAPI serialises per event loop iteration for this in-memory store.
"""

import logging
from datetime import datetime
from typing import Iterator

from app.devices.errors import DeviceAlreadyRegisteredError, DeviceNotFoundError
from app.devices.models import (
    DEFAULT_STALE_AFTER_SECONDS,
    Device,
    DeviceStatus,
    TailscaleIdentity,
    TrustState,
)
from app.devices.selection import DeviceQuery, SelectionResult, rank
from app.devices.trust import TrustDecision, TrustPolicy

logger = logging.getLogger("firday.devices.registry")


class DeviceRegistry:
    """An id -> device mapping with status, trust and capability queries."""

    def __init__(self, trust_policy: TrustPolicy | None = None) -> None:
        self._devices: dict[str, Device] = {}
        self._trust_policy = trust_policy

    @property
    def trust_policy(self) -> TrustPolicy | None:
        return self._trust_policy

    # --- registration ------------------------------------------------------

    def register(
        self,
        device: Device,
        *,
        replace: bool = False,
        identity: TailscaleIdentity | None = None,
    ) -> Device:
        """Add a device, evaluating its trust if a policy is configured.

        Raises :class:`DeviceAlreadyRegisteredError` on an id clash.
        """
        if device.device_id in self._devices and not replace:
            raise DeviceAlreadyRegisteredError(device.device_id)

        if self._trust_policy is not None:
            self._trust_policy.apply(device, identity)
        elif identity is not None:
            device.tailscale = identity

        self._devices[device.device_id] = device
        logger.info(
            "device registered (device=%s, name=%s, trust=%s, transport=%s)",
            device.device_id,
            device.name,
            device.trust.value,
            device.transport.value,
        )
        return device

    def unregister(self, device_id: str) -> Device:
        """Remove a device. Raises :class:`DeviceNotFoundError` if absent."""
        device = self.get(device_id)
        del self._devices[device_id]
        logger.info("device unregistered (device=%s, name=%s)", device_id, device.name)
        return device

    # --- lookup ------------------------------------------------------------

    def get(self, device_id: str) -> Device:
        """Look up a device. Raises :class:`DeviceNotFoundError` if absent."""
        try:
            return self._devices[device_id]
        except KeyError:
            raise DeviceNotFoundError(device_id) from None

    def try_get(self, device_id: str) -> Device | None:
        """Look up a device, returning ``None`` instead of raising."""
        return self._devices.get(device_id)

    def find_by_name(self, name: str) -> Device | None:
        """First device with this exact name, case-insensitively."""
        wanted = name.strip().lower()
        for device in self:
            if device.name.lower() == wanted:
                return device
        return None

    def find_by_tailscale_node(self, node_id: str) -> Device | None:
        """The device carrying this Tailscale node id, if one is registered."""
        for device in self:
            if device.tailscale and device.tailscale.node_id == node_id:
                return device
        return None

    def ids(self) -> list[str]:
        return sorted(self._devices)

    def names(self) -> list[str]:
        return sorted(d.name for d in self._devices.values())

    def devices(self) -> list[Device]:
        """Every device, best-first.

        Not named ``list``: a method called ``list`` shadows the builtin inside
        the class body, breaking every ``-> list[...]`` annotation below it on
        Python versions that evaluate annotations eagerly (< 3.14).
        """
        return rank(list(self._devices.values()))

    def describe(self) -> list[dict]:
        """Serializable summaries, for the API and for logging."""
        return [d.model_dump(mode="json") for d in self.devices()]

    # --- selection ---------------------------------------------------------

    def select(self, query: DeviceQuery) -> SelectionResult:
        """Every device matching ``query``, best-first."""
        matched = rank([d for d in self._devices.values() if query.matches(d)])
        return SelectionResult(query=query, devices=tuple(matched))

    def select_one(self, query: DeviceQuery) -> Device | None:
        """The best device matching ``query``, or ``None``."""
        return self.select(query).first()

    # --- mutation ----------------------------------------------------------

    def update(self, device_id: str, **fields) -> Device:
        """Apply field updates to a registered device.

        ``trust`` is not settable here on purpose - it comes from a trust
        evaluation or an explicit revocation, never from a caller's assertion.
        """
        if "trust" in fields:
            raise ValueError("trust is set by a TrustPolicy or revoke(), not by update()")
        device = self.get(device_id)
        for key, value in fields.items():
            if not hasattr(device, key):
                raise ValueError(f"device has no field {key!r}")
            setattr(device, key, value)
        return device

    def mark_seen(
        self,
        device_id: str,
        *,
        at: datetime | None = None,
        status: DeviceStatus | None = None,
    ) -> Device:
        """Record that we just heard from a device."""
        device = self.get(device_id)
        device.mark_seen(at=at, status=status)
        return device

    def set_status(self, device_id: str, status: DeviceStatus) -> Device:
        device = self.get(device_id)
        device.status = status
        return device

    def refresh_statuses(
        self,
        *,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        now: datetime | None = None,
    ) -> list[Device]:
        """Downgrade devices we have not heard from to ``UNKNOWN``.

        Not ``OFFLINE``: a silent device has not been observed down, only not
        observed. Returns the devices whose status changed.
        """
        changed: list[Device] = []
        for device in self._devices.values():
            if device.status is DeviceStatus.ONLINE and device.is_stale(
                stale_after_seconds=stale_after_seconds, now=now
            ):
                device.status = DeviceStatus.UNKNOWN
                changed.append(device)
                logger.info(
                    "device went stale (device=%s, last_seen=%s)",
                    device.device_id,
                    device.last_seen,
                )
        return changed

    # --- trust -------------------------------------------------------------

    def evaluate_trust(
        self, device_id: str, identity: TailscaleIdentity | None = None
    ) -> TrustDecision:
        """Re-evaluate one device's trust against the configured policy."""
        if self._trust_policy is None:
            raise ValueError("this registry has no trust policy configured")
        return self._trust_policy.apply(self.get(device_id), identity)

    def trusted(self) -> list[Device]:
        return [d for d in self.devices() if d.trust is TrustState.TRUSTED]

    # --- containers --------------------------------------------------------

    def __contains__(self, device_id: object) -> bool:
        return device_id in self._devices

    def __iter__(self) -> Iterator[Device]:
        return iter(self.devices())

    def __len__(self) -> int:
        return len(self._devices)


default_device_registry = DeviceRegistry()
