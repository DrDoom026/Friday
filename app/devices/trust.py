"""How a device's trust state is determined and checked.

The decision was made outside this file: trust comes from Tailscale identity,
not from a FIRDAY-issued token. This module encodes that policy.

The rules, in order:

1. ``REVOKED`` is sticky. An evaluation never lifts it - only an explicit
   operator action does.
2. A device Tailscale can actually identify, on our tailnet, is ``TRUSTED``.
3. A device whose Tailscale identity names a *different* tailnet user than the
   one FIRDAY runs as is ``UNTRUSTED`` - it is on the tailnet, but it is not us.
4. Anything else - no identity, an unidentifiable one, or Tailscale being
   unreachable - is ``UNVERIFIED``. Absence of proof is not proof of absence,
   so this is not the same as ``UNTRUSTED``.

Nothing here grants a device the right to *do* anything. Trust is a recorded
state; the engine that turns it into ALLOW/DENY is PART 7.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.devices.models import Device, TailscaleIdentity, TrustState, utcnow

logger = logging.getLogger("firday.devices.trust")


@dataclass(frozen=True)
class TrustDecision:
    """The outcome of one trust evaluation, with the reason recorded."""

    state: TrustState
    reason: str
    source: str = "tailscale"
    evaluated_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.evaluated_at is None:
            object.__setattr__(self, "evaluated_at", utcnow())

    @property
    def trusted(self) -> bool:
        return self.state is TrustState.TRUSTED


class TrustPolicy(ABC):
    """Decides what trust state a device is in."""

    @abstractmethod
    def evaluate(
        self, device: Device, identity: TailscaleIdentity | None = None
    ) -> TrustDecision:
        """Determine the device's trust state, given what Tailscale reported."""

    def is_trusted(self, device: Device, identity: TailscaleIdentity | None = None) -> bool:
        """Check trust without mutating anything."""
        return self.evaluate(device, identity).trusted

    def apply(self, device: Device, identity: TailscaleIdentity | None = None) -> TrustDecision:
        """Evaluate and write the result onto the device."""
        decision = self.evaluate(device, identity)
        if identity is not None:
            device.tailscale = identity
        device.trust = decision.state
        device.trust_reason = decision.reason
        logger.info(
            "device trust evaluated (device=%s, trust=%s, reason=%s)",
            device.device_id,
            decision.state.value,
            decision.reason,
        )
        return decision


class TailnetTrustPolicy(TrustPolicy):
    """Trust anchored on Tailscale identity.

    ``expected_user_login`` scopes trust to a single tailnet user - normally the
    login FIRDAY itself runs as, discovered from ``tailscale status``. Leave it
    empty to trust any identifiable node on the tailnet.
    """

    def __init__(self, expected_user_login: str = "") -> None:
        self.expected_user_login = (expected_user_login or "").strip().lower()

    def evaluate(
        self, device: Device, identity: TailscaleIdentity | None = None
    ) -> TrustDecision:
        if device.trust is TrustState.REVOKED:
            return TrustDecision(
                TrustState.REVOKED,
                "trust was explicitly revoked; re-registration is required",
                source="operator",
            )

        resolved = identity if identity is not None else device.tailscale
        if resolved is None:
            return TrustDecision(
                TrustState.UNVERIFIED, "no Tailscale identity was resolved for this device"
            )
        if not resolved.is_identified():
            return TrustDecision(
                TrustState.UNVERIFIED,
                "Tailscale returned no node id, DNS name or user for this device",
            )

        login = (resolved.user_login or "").strip().lower()
        if self.expected_user_login and login and login != self.expected_user_login:
            return TrustDecision(
                TrustState.UNTRUSTED,
                f"tailnet user {resolved.user_login!r} is not the expected "
                f"{self.expected_user_login!r}",
            )

        identified_as = resolved.dns_name or resolved.node_id or resolved.user_login
        return TrustDecision(
            TrustState.TRUSTED,
            f"identified by Tailscale as {identified_as!r} via {resolved.source}",
        )


class AlwaysUnverifiedPolicy(TrustPolicy):
    """Fallback when Tailscale is not available at all in this deployment.

    Deliberately never grants trust: if FIRDAY cannot ask Tailscale who someone
    is, the honest answer is "unverified", not "trusted because there is no
    other check". This is the class that must NOT quietly become a token system.
    """

    def evaluate(
        self, device: Device, identity: TailscaleIdentity | None = None
    ) -> TrustDecision:
        if device.trust is TrustState.REVOKED:
            return TrustDecision(
                TrustState.REVOKED, "trust was explicitly revoked", source="operator"
            )
        return TrustDecision(
            TrustState.UNVERIFIED,
            "Tailscale identity is unavailable in this deployment",
            source="none",
        )


def revoke(device: Device, reason: str = "revoked by operator") -> TrustDecision:
    """Explicitly revoke a device's trust. The one way into ``REVOKED``."""
    decision = TrustDecision(TrustState.REVOKED, reason, source="operator")
    device.trust = decision.state
    device.trust_reason = decision.reason
    logger.warning("device trust revoked (device=%s, reason=%s)", device.device_id, reason)
    return decision
