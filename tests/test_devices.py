"""Tests for PART 5: device / remote machine management.

Covers the device model and capability claims, the registry, trust state
transitions anchored on Tailscale identity, status tracking, selection, the
transport interface, and the API surface. The Tailscale lookups are exercised
two ways: against a fake directory (deterministic, no tailnet needed) and
against the real CLI parser fed recorded ``tailscale`` JSON.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.devices.errors import (
    DeviceAlreadyRegisteredError,
    DeviceNotFoundError,
    TransportNotImplementedError,
)
from app.devices.local import build_local_device, capabilities_from_tools
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
from app.devices.registry import DeviceRegistry
from app.devices.selection import DeviceQuery, rank
from app.devices.service import DeviceService
from app.devices.tailscale import (
    StaticTailscaleDirectory,
    TailscaleCli,
    TailscaleResolver,
    identity_from_headers,
)
from app.devices.transport import (
    AgentTransport,
    LocalTransport,
    SshTransport,
    TailscaleTransport,
    TransportRegistry,
)
from app.devices.trust import (
    AlwaysUnverifiedPolicy,
    TailnetTrustPolicy,
    revoke,
)

TAILNET_USER = "drdoom026@github"


def identity(
    *, node_id="nABC123CNTRL", name="pi.tailnet.ts.net", user=TAILNET_USER, source="whois", **kw
) -> TailscaleIdentity:
    return TailscaleIdentity(
        node_id=node_id, dns_name=name, user_login=user, source=source, **kw
    )


def registration(name="pi", **kw) -> DeviceRegistration:
    return DeviceRegistration(name=name, **kw)


@pytest.fixture
def registry() -> DeviceRegistry:
    return DeviceRegistry(trust_policy=TailnetTrustPolicy(expected_user_login=TAILNET_USER))


# --- the device model ------------------------------------------------------


def test_registration_builds_a_device_with_a_generated_id():
    device = registration(platform="Linux", architecture="aarch64").to_device()

    assert device.device_id
    assert device.name == "pi"
    assert device.platform is Platform.LINUX
    assert device.architecture == "aarch64"


def test_registration_can_reuse_an_explicit_id():
    assert registration(device_id="pi-01").to_device().device_id == "pi-01"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Linux", Platform.LINUX),
        ("linux", Platform.LINUX),
        ("Darwin", Platform.DARWIN),
        ("macOS", Platform.DARWIN),
        ("win32", Platform.WINDOWS),
        ("Windows", Platform.WINDOWS),
        ("android", Platform.ANDROID),
        ("iOS", Platform.IOS),
        ("", Platform.UNKNOWN),
        (None, Platform.UNKNOWN),
        ("plan9", Platform.UNKNOWN),
    ],
)
def test_platform_coercion_normalizes_the_many_spellings(raw, expected):
    assert Platform.coerce(raw) is expected


def test_a_new_device_starts_unverified_and_unknown():
    device = registration().to_device()

    assert device.trust is TrustState.UNVERIFIED
    assert device.status is DeviceStatus.UNKNOWN
    assert device.last_seen is None


def test_a_device_cannot_assert_its_own_trust_or_tailscale_identity():
    """Trust is derived, never supplied. The field is not on the input model."""
    assert "trust" not in DeviceRegistration.model_fields
    assert "tailscale" not in DeviceRegistration.model_fields


def test_device_carries_every_required_field():
    device = registration().to_device()

    for field in (
        "device_id",
        "name",
        "platform",
        "architecture",
        "network",
        "tailscale",
        "capabilities",
        "status",
        "trust",
        "permissions",
        "last_seen",
    ):
        assert hasattr(device, field), field


def test_permissions_are_immutable_declared_metadata():
    permissions = DevicePermissions(scopes=("fs.read",), allow_filesystem=True)

    with pytest.raises(Exception):
        permissions.scopes = ("fs.write",)


def test_permissions_default_to_the_most_restrictive_shape():
    permissions = DevicePermissions()

    assert permissions.scopes == ()
    assert not permissions.allow_filesystem
    assert not permissions.allow_network
    assert not permissions.allow_remote_execution
    assert permissions.requires_confirmation


# --- the capability model --------------------------------------------------


def test_capability_records_a_claim_with_details():
    capability = DeviceCapability(
        name="fs.read", version="1.0.0", description="read a file", attributes={"max_mb": 5}
    )

    assert capability.name == "fs.read"
    assert capability.available
    assert capability.attributes["max_mb"] == 5


def test_capability_is_immutable():
    with pytest.raises(Exception):
        DeviceCapability(name="fs.read").name = "fs.write"


def test_has_capability_checks_claims():
    device = registration(
        capabilities=(DeviceCapability(name="fs.read"), DeviceCapability(name="echo"))
    ).to_device()

    assert device.has_capability("fs.read")
    assert device.has_capability("echo")
    assert not device.has_capability("fs.write")
    assert device.capability_names() == ("fs.read", "echo")


def test_an_unavailable_capability_is_claimed_but_not_usable():
    device = registration(
        capabilities=(DeviceCapability(name="fs.delete", available=False),)
    ).to_device()

    assert not device.has_capability("fs.delete")
    assert device.has_capability("fs.delete", require_available=False)


def test_local_capabilities_come_from_the_tool_registry():
    from app.core.registry import build_default_registry

    capabilities = capabilities_from_tools(build_default_registry())
    by_name = {c.name: c for c in capabilities}

    assert "echo" in by_name
    assert "fs.read" in by_name
    assert by_name["fs.read"].available
    assert not by_name["fs.delete"].available, "a Part 4 disabled tool is not an available claim"


# --- the registry ----------------------------------------------------------


def test_register_and_get_a_device(registry):
    device = registry.register(registration(device_id="pi-01").to_device())

    assert registry.get("pi-01") is device
    assert "pi-01" in registry
    assert len(registry) == 1


def test_get_raises_for_an_unknown_id(registry):
    with pytest.raises(DeviceNotFoundError):
        registry.get("nope")


def test_try_get_returns_none_for_an_unknown_id(registry):
    assert registry.try_get("nope") is None


def test_registry_rejects_a_duplicate_id(registry):
    registry.register(registration(device_id="pi-01").to_device())

    with pytest.raises(DeviceAlreadyRegisteredError):
        registry.register(registration(device_id="pi-01").to_device())


def test_registry_allows_explicit_replacement(registry):
    registry.register(registration(name="old", device_id="pi-01").to_device())
    registry.register(registration(name="new", device_id="pi-01").to_device(), replace=True)

    assert registry.get("pi-01").name == "new"
    assert len(registry) == 1


def test_unregister_removes_a_device(registry):
    registry.register(registration(device_id="pi-01").to_device())

    removed = registry.unregister("pi-01")

    assert removed.device_id == "pi-01"
    assert "pi-01" not in registry
    with pytest.raises(DeviceNotFoundError):
        registry.unregister("pi-01")


def test_update_changes_fields(registry):
    registry.register(registration(device_id="pi-01").to_device())

    updated = registry.update("pi-01", name="renamed", architecture="aarch64")

    assert updated.name == "renamed"
    assert updated.architecture == "aarch64"


def test_update_refuses_to_set_trust(registry):
    registry.register(registration(device_id="pi-01").to_device())

    with pytest.raises(ValueError, match="trust is set by"):
        registry.update("pi-01", trust=TrustState.TRUSTED)


def test_update_rejects_an_unknown_field(registry):
    registry.register(registration(device_id="pi-01").to_device())

    with pytest.raises(ValueError, match="no field"):
        registry.update("pi-01", nonsense=True)


def test_find_by_name_and_by_tailscale_node(registry):
    registry.register(registration(name="Pi", device_id="pi-01").to_device(), identity=identity())

    assert registry.find_by_name("pi").device_id == "pi-01"
    assert registry.find_by_tailscale_node("nABC123CNTRL").device_id == "pi-01"
    assert registry.find_by_name("absent") is None
    assert registry.find_by_tailscale_node("nOPE") is None


def test_describe_is_json_serializable(registry):
    registry.register(registration(device_id="pi-01").to_device(), identity=identity())

    described = registry.describe()

    assert described[0]["device_id"] == "pi-01"
    assert described[0]["trust"] == "trusted"
    assert isinstance(described[0]["registered_at"], str)


# --- trust state -----------------------------------------------------------


def test_a_tailnet_identity_makes_a_device_trusted(registry):
    device = registry.register(registration().to_device(), identity=identity())

    assert device.trust is TrustState.TRUSTED
    assert "identified by Tailscale" in device.trust_reason
    assert device.tailscale.node_id == "nABC123CNTRL"


def test_no_identity_leaves_a_device_unverified(registry):
    device = registry.register(registration().to_device())

    assert device.trust is TrustState.UNVERIFIED
    assert "no Tailscale identity" in device.trust_reason


def test_an_unidentifiable_response_leaves_a_device_unverified(registry):
    empty = TailscaleIdentity(source="whois")

    device = registry.register(registration().to_device(), identity=empty)

    assert device.trust is TrustState.UNVERIFIED


def test_a_different_tailnet_user_is_untrusted(registry):
    device = registry.register(
        registration().to_device(), identity=identity(user="someone-else@github")
    )

    assert device.trust is TrustState.UNTRUSTED
    assert "not the expected" in device.trust_reason


def test_an_unscoped_policy_trusts_any_identified_node():
    unscoped = DeviceRegistry(trust_policy=TailnetTrustPolicy())

    device = unscoped.register(
        registration().to_device(), identity=identity(user="anyone@github")
    )

    assert device.trust is TrustState.TRUSTED


def test_trust_transitions_unverified_to_trusted_on_re_evaluation(registry):
    device = registry.register(registration(device_id="pi-01").to_device())
    assert device.trust is TrustState.UNVERIFIED

    decision = registry.evaluate_trust("pi-01", identity())

    assert decision.state is TrustState.TRUSTED
    assert registry.get("pi-01").trust is TrustState.TRUSTED


def test_trust_transitions_trusted_to_revoked(registry):
    device = registry.register(registration(device_id="pi-01").to_device(), identity=identity())
    assert device.trust is TrustState.TRUSTED

    decision = revoke(device, "laptop was lost")

    assert decision.state is TrustState.REVOKED
    assert device.trust_reason == "laptop was lost"


def test_revoked_trust_is_sticky_across_re_evaluation(registry):
    device = registry.register(registration(device_id="pi-01").to_device(), identity=identity())
    revoke(device)

    decision = registry.evaluate_trust("pi-01", identity())

    assert decision.state is TrustState.REVOKED, "a re-evaluation must not silently re-trust"
    assert registry.get("pi-01").trust is TrustState.REVOKED


def test_is_trusted_checks_without_mutating():
    policy = TailnetTrustPolicy(expected_user_login=TAILNET_USER)
    device = registration().to_device()

    assert policy.is_trusted(device, identity())
    assert device.trust is TrustState.UNVERIFIED, "a check must not write"


def test_the_fallback_policy_never_grants_trust():
    """Without Tailscale the answer is 'unverified' - never a substitute check."""
    policy = AlwaysUnverifiedPolicy()
    device = registration().to_device()

    decision = policy.evaluate(device, identity())

    assert decision.state is TrustState.UNVERIFIED
    assert not policy.is_trusted(device, identity())


def test_registry_lists_only_trusted_devices(registry):
    registry.register(registration(name="a", device_id="a").to_device(), identity=identity())
    registry.register(registration(name="b", device_id="b").to_device())

    assert [d.device_id for d in registry.trusted()] == ["a"]


# --- status tracking -------------------------------------------------------


def test_mark_seen_sets_last_seen_and_online(registry):
    registry.register(registration(device_id="pi-01").to_device())

    device = registry.mark_seen("pi-01")

    assert device.status is DeviceStatus.ONLINE
    assert device.last_seen is not None


def test_set_status_records_offline(registry):
    registry.register(registration(device_id="pi-01").to_device())

    assert registry.set_status("pi-01", DeviceStatus.OFFLINE).status is DeviceStatus.OFFLINE


def test_a_device_never_seen_is_stale():
    assert registration().to_device().is_stale()


def test_a_freshly_seen_device_is_not_stale():
    device = registration().to_device()
    device.mark_seen()

    assert not device.is_stale(stale_after_seconds=60)


def test_staleness_respects_the_window():
    device = registration().to_device()
    device.mark_seen(at=datetime.now(timezone.utc) - timedelta(seconds=120))

    assert device.is_stale(stale_after_seconds=60)
    assert not device.is_stale(stale_after_seconds=300)


def test_refresh_downgrades_a_stale_online_device_to_unknown(registry):
    registry.register(registration(device_id="pi-01").to_device())
    registry.mark_seen("pi-01", at=datetime.now(timezone.utc) - timedelta(hours=1))

    changed = registry.refresh_statuses(stale_after_seconds=60)

    assert [d.device_id for d in changed] == ["pi-01"]
    assert registry.get("pi-01").status is DeviceStatus.UNKNOWN, "silence is unknown, not offline"


def test_refresh_leaves_a_fresh_device_online(registry):
    registry.register(registration(device_id="pi-01").to_device())
    registry.mark_seen("pi-01")

    assert registry.refresh_statuses(stale_after_seconds=600) == []
    assert registry.get("pi-01").status is DeviceStatus.ONLINE


# --- selection -------------------------------------------------------------


@pytest.fixture
def populated(registry) -> DeviceRegistry:
    trusted_online = registration(
        name="pi",
        device_id="pi",
        platform="Linux",
        capabilities=(DeviceCapability(name="fs.read"), DeviceCapability(name="echo")),
    ).to_device()
    registry.register(trusted_online, identity=identity())
    registry.mark_seen("pi")

    unverified = registration(
        name="laptop",
        device_id="laptop",
        platform="Darwin",
        capabilities=(DeviceCapability(name="fs.read"),),
    ).to_device()
    registry.register(unverified)

    offline = registration(
        name="zen",
        device_id="zen",
        platform="Windows",
        capabilities=(DeviceCapability(name="echo"),),
        transport=TransportKind.SSH,
    ).to_device()
    registry.register(offline, identity=identity(node_id="nZEN"))
    registry.set_status("zen", DeviceStatus.OFFLINE)
    return registry


def test_select_by_capability(populated):
    result = populated.select(DeviceQuery(capabilities=("fs.read",)))

    assert {d.device_id for d in result.devices} == {"pi", "laptop"}
    assert result.found


def test_select_by_multiple_capabilities_requires_all(populated):
    result = populated.select(DeviceQuery(capabilities=("fs.read", "echo")))

    assert [d.device_id for d in result.devices] == ["pi"]


def test_select_by_trust_state(populated):
    result = populated.select(DeviceQuery(trust=(TrustState.UNVERIFIED,)))

    assert [d.device_id for d in result.devices] == ["laptop"]


def test_select_by_status(populated):
    result = populated.select(DeviceQuery(status=(DeviceStatus.OFFLINE,)))

    assert [d.device_id for d in result.devices] == ["zen"]


def test_select_by_platform_and_transport(populated):
    assert [d.device_id for d in populated.select(DeviceQuery(platform=(Platform.DARWIN,))).devices] == [
        "laptop"
    ]
    assert [
        d.device_id for d in populated.select(DeviceQuery(transport=(TransportKind.SSH,))).devices
    ] == ["zen"]


def test_trusted_online_convenience_query(populated):
    result = populated.select(DeviceQuery.trusted_online("fs.read"))

    assert [d.device_id for d in result.devices] == ["pi"]


def test_select_one_returns_the_best_candidate(populated):
    assert populated.select_one(DeviceQuery(capabilities=("fs.read",))).device_id == "pi"


def test_a_query_matching_nothing_is_empty(populated):
    result = populated.select(DeviceQuery(capabilities=("does.not.exist",)))

    assert not result.found
    assert result.first() is None


def test_ranking_puts_trusted_and_online_first(populated):
    assert [d.device_id for d in populated.devices()][0] == "pi"


def test_rank_is_deterministic():
    a = registration(name="b", device_id="1").to_device()
    b = registration(name="a", device_id="2").to_device()

    assert [d.device_id for d in rank([a, b])] == ["2", "1"]


# --- Tailscale identity ----------------------------------------------------


def test_identity_from_serve_headers():
    resolved = identity_from_headers(
        {"Tailscale-User-Login": "someone@github", "Tailscale-User-Name": "Someone"}
    )

    assert resolved.user_login == "someone@github"
    assert resolved.user_display_name == "Someone"
    assert resolved.source == "header"


def test_identity_headers_are_case_insensitive():
    assert identity_from_headers({"tailscale-user-login": "a@github"}).user_login == "a@github"


def test_no_identity_headers_yields_none():
    assert identity_from_headers({"user-agent": "curl"}) is None
    assert identity_from_headers({}) is None


def test_resolver_falls_back_to_whois_when_there_are_no_headers():
    source = StaticTailscaleDirectory({"100.64.0.1": identity()})

    resolved = TailscaleResolver(source).resolve(headers={}, peer_address="100.64.0.1")

    assert resolved.node_id == "nABC123CNTRL"
    assert resolved.source == "whois"


def test_resolver_prefers_headers_and_merges_the_node(self_check=None):
    source = StaticTailscaleDirectory({"100.64.0.1": identity(user="node-user@github")})

    resolved = TailscaleResolver(source).resolve(
        headers={"Tailscale-User-Login": "proxy-user@github"}, peer_address="100.64.0.1"
    )

    assert resolved.user_login == "proxy-user@github", "the proxy assertion wins"
    assert resolved.node_id == "nABC123CNTRL", "the node is still recorded"
    assert resolved.source == "header+whois"


def test_resolver_returns_none_for_an_unknown_peer():
    source = StaticTailscaleDirectory({})

    assert TailscaleResolver(source).resolve(peer_address="8.8.8.8") is None


def test_resolver_returns_none_when_tailscale_is_unavailable():
    source = StaticTailscaleDirectory({"100.64.0.1": identity()}, available=False)

    assert TailscaleResolver(source).resolve(peer_address="100.64.0.1") is None


def test_cli_parses_real_whois_json():
    """Recorded from `tailscale whois --json` against a live tailnet."""
    payload = {
        "Node": {
            "StableID": "ng9fxmTa1Q11CNTRL",
            "Name": "sherlock-void.tailc0d211.ts.net.",
            "Addresses": ["100.104.228.90/32", "fd7a:115c:a1e0::8e36:e45b/128"],
            "Hostinfo": {"Hostname": "sherlock-void", "OS": "linux"},
        },
        "UserProfile": {"LoginName": "DrDoom026@github", "DisplayName": "DrDoom026"},
    }

    resolved = TailscaleCli._identity_from_whois(payload)

    assert resolved.node_id == "ng9fxmTa1Q11CNTRL"
    assert resolved.dns_name == "sherlock-void.tailc0d211.ts.net", "trailing dot stripped"
    assert resolved.addresses == ("100.104.228.90", "fd7a:115c:a1e0::8e36:e45b"), "CIDR stripped"
    assert resolved.user_login == "DrDoom026@github"
    assert resolved.os == "linux"
    assert resolved.is_identified()


def test_cli_handles_a_whois_response_without_a_node():
    assert TailscaleCli._identity_from_whois({}) is None


def test_cli_lookup_of_an_unknown_peer_is_none(monkeypatch):
    """`tailscale whois` prints a bare 'peer not found', not JSON."""
    cli = TailscaleCli()
    monkeypatch.setattr(cli, "_run_json", lambda args: None)

    assert cli.whois("8.8.8.8") is None


def test_cli_reports_unavailable_when_the_binary_is_missing():
    assert not TailscaleCli(binary="definitely-not-tailscale").is_available()
    assert TailscaleCli(binary="definitely-not-tailscale").whois("100.64.0.1") is None


def test_service_falls_back_to_unverified_without_tailscale():
    service = DeviceService.create(source=StaticTailscaleDirectory(available=False))

    device = service.register(registration())

    assert device.trust is TrustState.UNVERIFIED
    assert isinstance(service.registry.trust_policy, AlwaysUnverifiedPolicy)


def test_service_scopes_trust_to_its_own_tailnet_user():
    source = StaticTailscaleDirectory(
        {"100.64.0.1": identity(user="stranger@github")},
        self_identity=identity(name="firday.ts.net", user=TAILNET_USER, source="status"),
    )
    service = DeviceService.create(source=source)

    stranger = service.register(registration(name="stranger"), peer_address="100.64.0.1")

    assert stranger.trust is TrustState.UNTRUSTED


# --- transports ------------------------------------------------------------


def test_local_transport_is_implemented_and_reachable():
    transport = LocalTransport()
    probe = asyncio.run(transport.probe(registration().to_device()))

    assert transport.implemented
    assert probe.reachable
    assert probe.kind is TransportKind.LOCAL


@pytest.mark.parametrize("cls", [SshTransport, TailscaleTransport, AgentTransport])
def test_remote_transports_are_stubs_that_refuse(cls):
    transport = cls()

    assert not transport.implemented
    with pytest.raises(TransportNotImplementedError) as exc:
        asyncio.run(transport.probe(registration().to_device()))
    assert "PART 7" in str(exc.value)


def test_transport_registry_resolves_by_device():
    transports = TransportRegistry()
    local = registration(transport=TransportKind.LOCAL).to_device()
    remote = registration(transport=TransportKind.SSH).to_device()

    assert isinstance(transports.for_device(local), LocalTransport)
    assert isinstance(transports.for_device(remote), SshTransport)


def test_only_local_is_implemented():
    assert TransportRegistry().implemented_kinds() == (TransportKind.LOCAL,)


# --- the local device ------------------------------------------------------


def test_build_local_device_describes_this_machine():
    from app.core.registry import build_default_registry

    device = build_local_device(build_default_registry())

    assert device.device_id == "local"
    assert device.name
    assert device.platform is not Platform.UNKNOWN
    assert device.architecture != "unknown"
    assert device.transport is TransportKind.LOCAL
    assert device.has_capability("fs.read")


def test_build_local_device_does_not_assert_its_own_trust():
    device = build_local_device()

    assert device.trust is TrustState.UNVERIFIED
    assert device.tailscale is None


def test_local_device_registration_is_idempotent():
    source = StaticTailscaleDirectory(self_identity=identity(source="status"))
    service = DeviceService.create(source=source)

    service.register_local_device()
    service.register_local_device()

    assert len(service.registry) == 1, "re-registering must not duplicate the local device"
    assert service.registry.get("local").trust is TrustState.TRUSTED


# --- the API ---------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    source = StaticTailscaleDirectory(
        {"100.64.0.1": identity(user=TAILNET_USER)},
        self_identity=identity(name="firday.ts.net", user=TAILNET_USER, source="status"),
    )
    monkeypatch.setattr(main, "devices", DeviceService.create(source=source))
    return TestClient(main.app, raise_server_exceptions=False)


def test_post_devices_registers_and_returns_the_device(client):
    response = client.post("/devices", json={"name": "laptop", "platform": "Darwin"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "laptop"
    assert body["platform"] == "darwin"
    assert body["device_id"]


def test_post_devices_echoes_the_correlation_id(client):
    response = client.post(
        "/devices", json={"name": "laptop"}, headers={"X-Request-ID": "device-trace"}
    )

    assert response.headers["X-Request-ID"] == "device-trace"


def test_post_devices_derives_trust_from_serve_headers(client):
    response = client.post(
        "/devices",
        json={"name": "laptop"},
        headers={"Tailscale-User-Login": TAILNET_USER},
    )

    assert response.json()["trust"] == "trusted"


def test_post_devices_ignores_a_client_supplied_trust_field(client):
    response = client.post("/devices", json={"name": "laptop", "trust": "trusted"})

    assert response.status_code == 201
    assert response.json()["trust"] == "unverified", "a device cannot assert its own trust"


def test_post_devices_rejects_a_missing_name(client):
    assert client.post("/devices", json={}).status_code == 422


def test_get_devices_lists_registered_devices(client):
    client.post("/devices", json={"name": "laptop"})
    client.post("/devices", json={"name": "phone"})

    body = client.get("/devices").json()

    assert {d["name"] for d in body} == {"laptop", "phone"}


def test_get_devices_filters_by_capability(client):
    client.post(
        "/devices", json={"name": "laptop", "capabilities": [{"name": "fs.read"}]}
    )
    client.post("/devices", json={"name": "phone"})

    body = client.get("/devices", params={"capability": "fs.read"}).json()

    assert [d["name"] for d in body] == ["laptop"]


def test_get_devices_filters_by_trust(client):
    client.post("/devices", json={"name": "trusted-one"}, headers={"Tailscale-User-Login": TAILNET_USER})
    client.post("/devices", json={"name": "unverified-one"})

    body = client.get("/devices", params={"trust": "trusted"}).json()

    assert [d["name"] for d in body] == ["trusted-one"]


def test_get_one_device(client):
    created = client.post("/devices", json={"name": "laptop"}).json()

    body = client.get(f"/devices/{created['device_id']}").json()

    assert body["device_id"] == created["device_id"]


def test_get_an_unknown_device_is_404(client):
    response = client.get("/devices/nope")

    assert response.status_code == 404
    assert "no device registered" in response.json()["error"]


def test_heartbeat_updates_last_seen(client):
    created = client.post("/devices", json={"name": "laptop"}).json()

    body = client.post(f"/devices/{created['device_id']}/heartbeat").json()

    assert body["status"] == "online"
    assert body["last_seen"] is not None


def test_heartbeat_for_an_unknown_device_is_404(client):
    assert client.post("/devices/nope/heartbeat").status_code == 404


def test_startup_registers_the_local_device(client, tmp_path, monkeypatch):
    import dataclasses

    import app.main as main

    # Point the Part 4 sandbox at a scratch root so this exercises startup,
    # not whatever state the developer's real workspace happens to be in.
    monkeypatch.setattr(
        main,
        "settings",
        dataclasses.replace(
            main.settings,
            fs_allowed_roots=(str(tmp_path / "workspace"),),
            fs_vault_root=str(tmp_path / "vault"),
        ),
    )

    with client:
        body = client.get("/devices").json()

    local = [d for d in body if d["device_id"] == "local"]
    assert local, "the machine FIRDAY runs on must register itself at startup"
    assert local[0]["transport"] == "local"


def test_health_still_works_alongside_device_management(client):
    assert client.get("/health").status_code == 200
