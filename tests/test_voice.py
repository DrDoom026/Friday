"""Tests for PART 12a: the voice session WebSocket backend.

Covers the state machine (``app.voice.models``), the protocol dispatcher
(``app.voice.protocol``), and the ``/ws/voice`` endpoint's handshake, device
trust reuse, and lifecycle. No STT/TTS/audio is exercised here - there is
none to exercise yet.
"""

import pytest

from app.devices.service import DeviceService
from app.devices.tailscale import StaticTailscaleDirectory
from app.voice.manager import VoiceSessionManager
from app.voice.models import InvalidVoiceTransitionError, VoiceSession, VoiceState
from app.voice.protocol import handle_message

TAILNET_USER = "voice-tester@github"


def identity(*, node_id="nVOICE123CNTRL", name="laptop.tailnet.ts.net", user=TAILNET_USER, source="whois"):
    from app.devices.models import TailscaleIdentity

    return TailscaleIdentity(
        node_id=node_id, dns_name=name, user_login=user, addresses=("100.64.0.9",), source=source
    )


# --- state machine -----------------------------------------------------


def test_initial_state_is_idle():
    session = VoiceSession(device_id="laptop")
    assert session.state is VoiceState.IDLE


def test_valid_transition_chain_succeeds():
    session = VoiceSession(device_id="laptop")
    for target in (
        VoiceState.WAKE,
        VoiceState.LISTENING,
        VoiceState.PAUSED,
        VoiceState.LISTENING,
        VoiceState.THINKING,
        VoiceState.RESPONDING,
        VoiceState.IDLE,
    ):
        session.transition(target)
        assert session.state is target


def test_invalid_transition_is_rejected():
    session = VoiceSession(device_id="laptop")
    with pytest.raises(InvalidVoiceTransitionError):
        session.transition(VoiceState.THINKING)
    assert session.state is VoiceState.IDLE, "a rejected transition must not mutate state"


# --- protocol dispatcher -------------------------------------------------


def test_handle_message_rejects_unknown_type():
    session = VoiceSession(device_id="laptop")
    response = handle_message(session, {"type": "tool.execute"})
    assert response == {
        "type": "error",
        "code": "UNKNOWN_MESSAGE_TYPE",
        "message": "unknown message type 'tool.execute'",
    }
    assert session.state is VoiceState.IDLE


def test_handle_message_rejects_non_dict():
    session = VoiceSession(device_id="laptop")
    response = handle_message(session, "not a dict")
    assert response["type"] == "error"
    assert response["code"] == "PROTOCOL_ERROR"


def test_handle_message_voice_state_valid_transition():
    session = VoiceSession(device_id="laptop")
    response = handle_message(session, {"type": "voice.state", "state": "wake"})
    assert response == {"type": "voice.state.accepted", "state": "wake"}
    assert session.state is VoiceState.WAKE


def test_handle_message_voice_state_invalid_transition():
    session = VoiceSession(device_id="laptop")
    response = handle_message(session, {"type": "voice.state", "state": "thinking"})
    assert response["type"] == "error"
    assert response["code"] == "INVALID_STATE_TRANSITION"
    assert session.state is VoiceState.IDLE


def test_handle_message_voice_state_unknown_state_name():
    session = VoiceSession(device_id="laptop")
    response = handle_message(session, {"type": "voice.state", "state": "sleeping"})
    assert response == {"type": "error", "code": "INVALID_STATE", "message": "unknown state 'sleeping'"}


def test_handle_message_pause_resume_sugar():
    session = VoiceSession(device_id="laptop")
    session.transition(VoiceState.WAKE)
    session.transition(VoiceState.LISTENING)

    assert handle_message(session, {"type": "voice.pause"}) == {
        "type": "voice.pause.accepted",
        "state": "paused",
    }
    assert handle_message(session, {"type": "voice.resume"}) == {
        "type": "voice.resume.accepted",
        "state": "listening",
    }


def test_handle_message_voice_end_resets_to_idle():
    session = VoiceSession(device_id="laptop")
    session.transition(VoiceState.WAKE)
    session.transition(VoiceState.LISTENING)

    response = handle_message(session, {"type": "voice.end"})
    assert response == {"type": "voice.end.accepted", "state": "idle"}


def test_handle_message_session_end_returns_none():
    session = VoiceSession(device_id="laptop")
    assert handle_message(session, {"type": "session.end"}) is None


# --- session manager ------------------------------------------------------


def test_manager_creates_and_removes_sessions():
    manager = VoiceSessionManager()
    session = manager.create("laptop")

    assert manager.get(session.session_id) is session
    assert len(manager) == 1

    manager.remove(session.session_id)
    assert manager.get(session.session_id) is None
    assert len(manager) == 0


def test_manager_keeps_concurrent_sessions_independent():
    manager = VoiceSessionManager()
    a = manager.create("laptop")
    b = manager.create("phone")

    a.transition(VoiceState.WAKE)

    assert manager.get(a.session_id).state is VoiceState.WAKE
    assert manager.get(b.session_id).state is VoiceState.IDLE
    assert a.session_id != b.session_id


# --- the WebSocket endpoint -------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    source = StaticTailscaleDirectory(
        {"100.64.0.9": identity()},
        self_identity=identity(name="firday.ts.net", source="status"),
    )
    monkeypatch.setattr(main, "devices", DeviceService.create(source=source))
    monkeypatch.setattr(main, "voice_sessions", VoiceSessionManager())
    return TestClient(main.app, raise_server_exceptions=False)


def _register_trusted_device(client, name="laptop") -> str:
    response = client.post(
        "/devices", json={"name": name}, headers={"Tailscale-User-Login": TAILNET_USER}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["trust"] == "trusted"
    return body["device_id"]


def _register_untrusted_device(client, name="stranger") -> str:
    response = client.post("/devices", json={"name": name})
    assert response.status_code == 201
    body = response.json()
    assert body["trust"] != "trusted"
    return body["device_id"]


def test_websocket_endpoint_exists_and_accepts_trusted_device(client):
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id, "client_version": "0.1"})
        accepted = ws.receive_json()

        assert accepted["type"] == "session.accepted"
        assert accepted["device_id"] == device_id
        assert accepted["state"] == "idle"
        assert accepted["session_id"]


def test_websocket_rejects_unknown_device(client):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": "no-such-device"})
        error = ws.receive_json()

        assert error == {
            "type": "error",
            "code": "DEVICE_NOT_TRUSTED",
            "message": "device is not trusted",
        }


def test_websocket_rejects_untrusted_device(client):
    device_id = _register_untrusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        error = ws.receive_json()

        assert error["code"] == "DEVICE_NOT_TRUSTED"


def test_websocket_valid_state_transition_round_trip(client):
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        ws.receive_json()

        ws.send_json({"type": "voice.state", "state": "wake"})
        ack = ws.receive_json()
        assert ack == {"type": "voice.state.accepted", "state": "wake"}


def test_websocket_invalid_state_transition_is_rejected(client):
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        ws.receive_json()

        ws.send_json({"type": "voice.state", "state": "thinking"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "INVALID_STATE_TRANSITION"


def test_websocket_malformed_json_is_rejected_safely(client):
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        ws.receive_json()

        ws.send_text("{not valid json")
        error = ws.receive_json()
        assert error["code"] == "MALFORMED_JSON"

        # the connection must survive a malformed message
        ws.send_json({"type": "voice.state", "state": "wake"})
        ack = ws.receive_json()
        assert ack["type"] == "voice.state.accepted"


def test_websocket_unknown_message_type_is_rejected_safely(client):
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        ws.receive_json()

        ws.send_json({"type": "tool.execute", "tool": "fs.read"})
        error = ws.receive_json()
        assert error["code"] == "UNKNOWN_MESSAGE_TYPE"


def test_websocket_session_end_closes_cleanly(client, monkeypatch):
    import app.main as main

    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        accepted = ws.receive_json()
        session_id = accepted["session_id"]
        assert len(main.voice_sessions) == 1

        ws.send_json({"type": "session.end"})
        ended = ws.receive_json()
        assert ended == {"type": "session.ended", "session_id": session_id}

    assert len(main.voice_sessions) == 0, "session must be cleaned up after session.end"


def test_websocket_disconnect_cleans_up_session(client):
    import app.main as main

    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        ws.receive_json()
        assert len(main.voice_sessions) == 1

    assert len(main.voice_sessions) == 0, "disconnecting without session.end must still clean up"


def test_multiple_simultaneous_sessions_do_not_mix_state(client):
    import app.main as main

    laptop_id = _register_trusted_device(client, name="laptop")
    phone_id = _register_trusted_device(client, name="phone")

    with client.websocket_connect("/ws/voice") as laptop_ws, client.websocket_connect(
        "/ws/voice"
    ) as phone_ws:
        laptop_ws.send_json({"type": "session.start", "device_id": laptop_id})
        laptop_accepted = laptop_ws.receive_json()

        phone_ws.send_json({"type": "session.start", "device_id": phone_id})
        phone_accepted = phone_ws.receive_json()

        assert laptop_accepted["session_id"] != phone_accepted["session_id"]
        assert len(main.voice_sessions) == 2

        laptop_ws.send_json({"type": "voice.state", "state": "wake"})
        laptop_ws.receive_json()

        laptop_session = main.voice_sessions.get(laptop_accepted["session_id"])
        phone_session = main.voice_sessions.get(phone_accepted["session_id"])
        assert laptop_session.state.value == "wake"
        assert phone_session.state.value == "idle"


def test_no_tool_execution_reachable_from_voice_session(client):
    """Every reachable message type is protocol-only - none names a tool call."""
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        ws.receive_json()

        ws.send_json({"type": "tool.execute", "tool": "fs.read", "arguments": {}})
        error = ws.receive_json()
        assert error["code"] == "UNKNOWN_MESSAGE_TYPE"


def test_no_audio_field_is_persisted_on_session(client):
    import app.main as main

    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        accepted = ws.receive_json()

        session = main.voice_sessions.get(accepted["session_id"])
        assert session.transcript == ""
        assert session.audio_state == "idle"
