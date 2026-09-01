"""Wiring for device management: registry + trust policy + transports.

One object the API layer can hold, so ``app.main`` stays as thin as it is for
Part 1 and Part 2. It owns the decision made outside this codebase - trust
comes from Tailscale - and degrades honestly when Tailscale is not there.
"""

import logging
from typing import Mapping

from app.devices.local import build_local_device
from app.devices.models import Device, DeviceRegistration, DeviceStatus, TailscaleIdentity
from app.devices.registry import DeviceRegistry
from app.devices.tailscale import TailscaleCli, TailscaleIdentitySource, TailscaleResolver
from app.devices.transport import TransportRegistry
from app.devices.trust import AlwaysUnverifiedPolicy, TailnetTrustPolicy, TrustPolicy

logger = logging.getLogger("firday.devices")


class DeviceService:
    """Registry, trust policy, identity resolver and transports in one place."""

    def __init__(
        self,
        registry: DeviceRegistry,
        resolver: TailscaleResolver,
        transports: TransportRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.resolver = resolver
        self.transports = transports or TransportRegistry()

    @classmethod
    def create(
        cls,
        *,
        source: TailscaleIdentitySource | None = None,
        scope_to_self_user: bool = True,
    ) -> "DeviceService":
        """Build the service, discovering the tailnet user FIRDAY runs as.

        When Tailscale is reachable, trust is scoped to that user's tailnet.
        When it is not, the policy falls back to one that never grants trust -
        never to a substitute token check.
        """
        identity_source = source if source is not None else TailscaleCli()
        resolver = TailscaleResolver(identity_source)

        policy: TrustPolicy
        self_identity = identity_source.self_identity() if identity_source.is_available() else None
        if self_identity is None:
            logger.warning(
                "tailscale identity unavailable; devices will stay unverified "
                "(no token fallback is used by design)"
            )
            policy = AlwaysUnverifiedPolicy()
        else:
            expected = self_identity.user_login if scope_to_self_user else ""
            policy = TailnetTrustPolicy(expected_user_login=expected)
            logger.info(
                "tailscale trust anchor ready (node=%s, user=%s)",
                self_identity.dns_name or self_identity.node_id,
                self_identity.user_login or "-",
            )

        return cls(registry=DeviceRegistry(trust_policy=policy), resolver=resolver)

    # --- registration ------------------------------------------------------

    def identify(
        self, *, headers: Mapping[str, str] | None = None, peer_address: str | None = None
    ) -> TailscaleIdentity | None:
        """Ask Tailscale who is calling."""
        return self.resolver.resolve(headers=headers, peer_address=peer_address)

    def register(
        self,
        registration: DeviceRegistration,
        *,
        headers: Mapping[str, str] | None = None,
        peer_address: str | None = None,
        replace: bool = True,
    ) -> Device:
        """Register a device, deriving its identity and trust from Tailscale."""
        identity = self.identify(headers=headers, peer_address=peer_address)
        device = registration.to_device()
        device.mark_seen(status=DeviceStatus.ONLINE)
        return self.registry.register(device, replace=replace, identity=identity)

    def register_local_device(self, tool_registry=None) -> Device:
        """Register the machine FIRDAY is running on, identified via Tailscale."""
        device = build_local_device(tool_registry)
        identity = self.resolver.source.self_identity()
        if identity is not None and not device.network.addresses:
            # Tailscale already knows this node's addresses and name; use those
            # rather than paying a DNS lookup for a worse answer.
            device.network = device.network.model_copy(
                update={
                    "addresses": identity.addresses,
                    "fqdn": identity.dns_name or device.network.fqdn,
                }
            )
        device.mark_seen(status=DeviceStatus.ONLINE)
        registered = self.registry.register(device, replace=True, identity=identity)
        logger.info(
            "local device registered (device=%s, name=%s, platform=%s, arch=%s, trust=%s)",
            registered.device_id,
            registered.name,
            registered.platform.value,
            registered.architecture,
            registered.trust.value,
        )
        return registered
