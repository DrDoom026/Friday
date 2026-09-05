"""Tests for PART 12c: text-to-speech integration on top of the 12a/12b voice
session backend.

Piper itself is never required to run these - ``PiperVoice`` is mocked out
via ``PiperTTS._load_voice``, and most tests use a tiny in-memory fake that
conforms to the ``TextToSpeech`` abstraction. This keeps the suite fast and
independent of any downloaded voice model.
"""

import asyncio

import pytest

from app.core.models import FirdayResponse, Plan
from app.devices.service import DeviceService
from app.devices.tailscale import StaticTailscaleDirectory
from app.voice.audio import AudioBuffer
from app.voice.manager import VoiceSessionManager
from app.voice.models import VoiceSession, VoiceState
from app.voice.piper_tts import PiperTTS
from app.voice.pipeline import VoiceEvent, finish_audio, synthesize_response
from app.voice.stt import SpeechToText, Transcript
from app.voice.tts import AudioFormat, TTSError, TextToSpeech

TAILNET_USER = "voice-tts-tester@github"


def identity(*, node_id="nTTS123CNTRL", name="laptop.tailnet.ts.net", user=TAILNET_USER, source="whois"):
    from app.devices.models import TailscaleIdentity

    return TailscaleIdentity(
        node_id=node_id, dns_name=name, user_login=user, addresses=("100.64.0.9",), source=source
    )


class FakeSTT(SpeechToText):
    def __init__(self, text: str = "what time is it"):
        self.text = text

    async def transcribe(self, audio, *, sample_rate, session_id, device_id) -> Transcript:
        return Transcript(text=self.text, session_id=session_id, device_id=device_id)


class FakeTTS(TextToSpeech):
    def __init__(self, chunks=(b"aaaa", b"bbbb"), *, sample_rate: int = 22050, fail: bool = False):
        self.chunks = list(chunks)
        self._sample_rate = sample_rate
        self.fail = fail
        self.calls: list[str] = []

    @property
    def output_format(self) -> AudioFormat:
        return AudioFormat(sample_rate=self._sample_rate)

    async def synthesize(self, text: str):
        self.calls.append(text)
        if self.fail:
            raise TTSError("synthetic tts failure")
        for chunk in self.chunks:
            yield chunk


class FakeCore:
    class _MockPlanner:
        name = "mock"

    def __init__(self, output: str = "it is noon"):
        self.planner = self._MockPlanner()
        self.output = output
        self.requests: list = []

    async def handle(self, request, context, *, execute=False):
        self.requests.append((request, context, execute))
        return FirdayResponse(
            request_id=context.request_id,
            output=self.output,
            plan=Plan(planner_name="mock", summary="ok"),
            results=[],
        )


class ConfirmationCore(FakeCore):
    """Stands in for a real request whose planned tool needed confirmation -
    the exact text a real Security Engine REQUIRE_CONFIRMATION would produce."""

    def __init__(self):
        super().__init__(output="The email is waiting for confirmation.")


async def _collect(agen):
    return [item async for item in agen]


def _listening_session() -> VoiceSession:
    session = VoiceSession(device_id="laptop")
    session.transition(VoiceState.WAKE)
    session.transition(VoiceState.LISTENING)
    return session


# --- TTS abstraction ---------------------------------------------------


def test_text_to_speech_is_an_abstract_base():
    with pytest.raises(TypeError):
        TextToSpeech()


def test_piper_tts_conforms_to_the_abstraction():
    assert issubclass(PiperTTS, TextToSpeech)


def test_audio_format_is_the_shared_pcm16_mono_contract():
    fmt = AudioFormat(sample_rate=22050)
    assert fmt.encoding == "pcm16"
    assert fmt.channels == 1
    assert fmt.sample_rate == 22050


# --- Piper adapter -------------------------------------------------------


def test_piper_output_format_reflects_configured_sample_rate():
    tts = PiperTTS(model_path="/models/en_US-voice.onnx", sample_rate=16000)
    assert tts.output_format == AudioFormat(sample_rate=16000)


def test_piper_unconfigured_model_path_raises_tts_error():
    tts = PiperTTS(model_path="")
    with pytest.raises(TTSError):
        asyncio.run(_collect(tts.synthesize("hello")))


def test_piper_missing_package_raises_tts_error(monkeypatch):
    def fake_load(self):
        raise TTSError("piper-tts is not installed on this machine")

    tts = PiperTTS(model_path="/models/en_US-voice.onnx")
    monkeypatch.setattr("app.voice.piper_tts.PiperTTS._load_voice", fake_load)

    with pytest.raises(TTSError):
        asyncio.run(_collect(tts.synthesize("hello")))


def test_piper_synthesize_streams_configured_model_chunks(monkeypatch):
    load_calls = {"count": 0}

    class FakeVoice:
        def synthesize_stream_raw(self, text):
            yield b"\x01\x02"
            yield b"\x03\x04"

    def fake_load(self):
        load_calls["count"] += 1
        return FakeVoice()

    tts = PiperTTS(model_path="/models/en_US-voice.onnx")
    monkeypatch.setattr("app.voice.piper_tts.PiperTTS._load_voice", fake_load)

    chunks = asyncio.run(_collect(tts.synthesize("hello there")))

    assert chunks == [b"\x01\x02", b"\x03\x04"]


def test_piper_voice_is_loaded_once_across_utterances(monkeypatch):
    load_calls = {"count": 0}

    class FakeVoice:
        def synthesize_stream_raw(self, text):
            yield b"\x00\x00"

    def fake_load(self):
        load_calls["count"] += 1
        return FakeVoice()

    tts = PiperTTS(model_path="/models/en_US-voice.onnx")
    monkeypatch.setattr("app.voice.piper_tts.PiperTTS._load_voice", fake_load)

    async def run_three():
        for _ in range(3):
            await _collect(tts.synthesize("hi"))

    asyncio.run(run_three())

    assert load_calls["count"] == 1, "the voice must be reused across utterances, not reloaded"


def test_piper_empty_text_raises_tts_error():
    tts = PiperTTS(model_path="/models/en_US-voice.onnx")
    with pytest.raises(TTSError):
        asyncio.run(_collect(tts.synthesize("   ")))


def test_piper_rejects_input_over_the_configured_character_limit():
    tts = PiperTTS(model_path="/models/en_US-voice.onnx", max_input_chars=10)
    with pytest.raises(TTSError):
        asyncio.run(_collect(tts.synthesize("this sentence is far too long")))


def test_piper_inference_failure_is_wrapped_without_leaking_internals(monkeypatch):
    class FakeVoice:
        def synthesize_stream_raw(self, text):
            raise RuntimeError("internal onnxruntime panic at /etc/shadow")
            yield  # pragma: no cover - unreachable, makes this a generator

    tts = PiperTTS(model_path="/models/en_US-voice.onnx")
    monkeypatch.setattr("app.voice.piper_tts.PiperTTS._load_voice", lambda self: FakeVoice())

    with pytest.raises(TTSError) as excinfo:
        asyncio.run(_collect(tts.synthesize("hello")))
    assert "/etc/shadow" not in str(excinfo.value)


# --- synthesize_response: protocol framing ----------------------------------


def test_synthesize_response_emits_start_audio_end_in_order():
    session = _listening_session()
    session.transition(VoiceState.THINKING)
    session.transition(VoiceState.RESPONDING)

    fake_tts = FakeTTS(chunks=(b"one", b"two"), sample_rate=22050)
    events = asyncio.run(_collect(synthesize_response(session, "hello there", tts=fake_tts)))

    assert events[0].kind == "json"
    assert events[0].payload["type"] == "voice.response.start"
    assert events[0].payload["session_id"] == session.session_id
    assert events[0].payload["encoding"] == "pcm16"
    assert events[0].payload["sample_rate"] == 22050
    assert events[0].payload["channels"] == 1
    utterance_id = events[0].payload["utterance_id"]

    assert events[1] == VoiceEvent(kind="bytes", payload=b"one")
    assert events[2] == VoiceEvent(kind="bytes", payload=b"two")

    assert events[-1].kind == "json"
    assert events[-1].payload == {
        "type": "voice.response.end",
        "session_id": session.session_id,
        "utterance_id": utterance_id,
        "status": "ok",
    }
    assert fake_tts.calls == ["hello there"]


def test_synthesize_response_tts_failure_is_structured_and_ends_the_utterance():
    session = _listening_session()
    session.transition(VoiceState.THINKING)
    session.transition(VoiceState.RESPONDING)

    fake_tts = FakeTTS(fail=True)
    events = asyncio.run(_collect(synthesize_response(session, "hello", tts=fake_tts)))

    kinds = [(e.kind, e.payload.get("type") if e.kind == "json" else None) for e in events]
    assert kinds[0] == ("json", "voice.response.start")
    assert kinds[1] == ("json", "error")
    assert events[1].payload["code"] == "TTS_FAILED"
    assert kinds[-1] == ("json", "voice.response.end")
    assert events[-1].payload["status"] == "error"


# --- finish_audio: full state machine + TTS integration ---------------------


def test_finish_audio_moves_through_thinking_responding_back_to_idle():
    session = _listening_session()
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)
    buffer.append(b"\x00\x01" * 100)

    events = asyncio.run(
        _collect(
            finish_audio(session, buffer, stt=FakeSTT(), core=FakeCore(), tts=FakeTTS())
        )
    )

    payload_types = [e.payload["type"] for e in events if e.kind == "json"]
    assert payload_types == [
        "voice.transcript",
        "voice.response",
        "voice.response.start",
        "voice.response.end",
    ]
    assert session.state is VoiceState.IDLE


def test_finish_audio_speaks_cores_final_text_not_the_transcript():
    session = _listening_session()
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)
    buffer.append(b"\x00\x01" * 100)

    fake_tts = FakeTTS()
    core = FakeCore(output="The email is waiting for confirmation.")

    asyncio.run(_collect(finish_audio(session, buffer, stt=FakeSTT(), core=core, tts=fake_tts)))

    assert fake_tts.calls == ["The email is waiting for confirmation."]


def test_finish_audio_tts_failure_does_not_crash_and_still_reaches_idle():
    session = _listening_session()
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)
    buffer.append(b"\x00\x01" * 100)

    events = asyncio.run(
        _collect(
            finish_audio(session, buffer, stt=FakeSTT(), core=FakeCore(), tts=FakeTTS(fail=True))
        )
    )

    error_events = [e for e in events if e.kind == "json" and e.payload.get("code") == "TTS_FAILED"]
    assert len(error_events) == 1
    assert session.state is VoiceState.IDLE


def test_finish_audio_never_reaches_tts_when_core_never_ran():
    """STT failure must short-circuit before RESPONDING/TTS entirely."""
    session = _listening_session()
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)  # empty -> STT_FAILED

    fake_tts = FakeTTS()
    asyncio.run(
        _collect(finish_audio(session, buffer, stt=FakeSTT(), core=FakeCore(), tts=fake_tts))
    )

    assert fake_tts.calls == []
    assert session.state is VoiceState.IDLE


def test_confirmation_gated_response_text_is_still_what_gets_spoken():
    """A REQUIRE_CONFIRMATION response's exact wording is preserved end to end -
    12c must not alter, summarize, or bypass what the Security Engine produced."""
    session = _listening_session()
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)
    buffer.append(b"\x00\x01" * 100)

    fake_tts = FakeTTS()
    events = asyncio.run(
        _collect(
            finish_audio(
                session,
                buffer,
                stt=FakeSTT(text="send this email"),
                core=ConfirmationCore(),
                tts=fake_tts,
            )
        )
    )

    response_event = next(e for e in events if e.kind == "json" and e.payload.get("type") == "voice.response")
    assert response_event.payload["text"] == "The email is waiting for confirmation."
    assert fake_tts.calls == ["The email is waiting for confirmation."]


# --- session isolation -------------------------------------------------------


def test_two_sessions_do_not_share_tts_state():
    manager = VoiceSessionManager()
    a = manager.create("laptop")
    b = manager.create("phone")
    for s in (a, b):
        s.transition(VoiceState.WAKE)
        s.transition(VoiceState.LISTENING)

    buffer_a = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)
    buffer_a.append(b"\x00\x01" * 50)
    buffer_b = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)
    buffer_b.append(b"\x02\x03" * 50)

    tts_a, tts_b = FakeTTS(), FakeTTS()
    asyncio.run(
        _collect(finish_audio(a, buffer_a, stt=FakeSTT(text="for laptop"), core=FakeCore(), tts=tts_a))
    )
    asyncio.run(
        _collect(finish_audio(b, buffer_b, stt=FakeSTT(text="for phone"), core=FakeCore(), tts=tts_b))
    )

    assert tts_a.calls != tts_b.calls or a.session_id != b.session_id
    assert a.transcript == "for laptop"
    assert b.transcript == "for phone"
    assert a.state is VoiceState.IDLE
    assert b.state is VoiceState.IDLE


# --- the WebSocket endpoint --------------------------------------------------


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
    monkeypatch.setattr(main, "voice_stt", FakeSTT(text="what time is it"))
    monkeypatch.setattr(main, "voice_tts", FakeTTS(chunks=(b"\x11\x22", b"\x33\x44")))
    monkeypatch.setattr(main, "core", FakeCore(output="it is noon"))
    return TestClient(main.app, raise_server_exceptions=False)


def _register_trusted_device(client, name="laptop") -> str:
    response = client.post(
        "/devices", json={"name": name}, headers={"Tailscale-User-Login": TAILNET_USER}
    )
    assert response.status_code == 201
    return response.json()["device_id"]


def _start_session(ws, device_id):
    ws.send_json({"type": "session.start", "device_id": device_id})
    ws.receive_json()
    ws.send_json({"type": "voice.state", "state": "wake"})
    ws.receive_json()
    ws.send_json({"type": "voice.state", "state": "listening"})
    ws.receive_json()


def _speak_one_utterance(ws, device_id):
    _start_session(ws, device_id)
    ws.send_json({"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1})
    ws.receive_json()
    ws.send_bytes(b"\x00\x01" * 50)
    ws.send_json({"type": "audio.end"})


def test_websocket_full_response_audio_round_trip(client):
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _speak_one_utterance(ws, device_id)

        transcript = ws.receive_json()
        response = ws.receive_json()
        start = ws.receive_json()
        chunk_a = ws.receive_bytes()
        chunk_b = ws.receive_bytes()
        end = ws.receive_json()

        assert transcript["type"] == "voice.transcript"
        assert response == {"type": "voice.response", "text": "it is noon"}
        assert start["type"] == "voice.response.start"
        assert start["encoding"] == "pcm16"
        assert start["channels"] == 1
        assert start["utterance_id"] == end["utterance_id"]
        assert chunk_a == b"\x11\x22"
        assert chunk_b == b"\x33\x44"
        assert end == {
            "type": "voice.response.end",
            "session_id": start["session_id"],
            "utterance_id": start["utterance_id"],
            "status": "ok",
        }


def test_websocket_tts_failure_is_a_structured_error_and_keeps_session_alive(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "voice_tts", FakeTTS(fail=True))
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _speak_one_utterance(ws, device_id)

        ws.receive_json()  # voice.transcript
        ws.receive_json()  # voice.response
        ws.receive_json()  # voice.response.start
        error = ws.receive_json()
        end = ws.receive_json()

        assert error == {"type": "error", "code": "TTS_FAILED", "message": "synthetic tts failure"}
        assert end["status"] == "error"

        # the connection must still be usable
        ws.send_json({"type": "voice.state", "state": "wake"})
        ack = ws.receive_json()
        assert ack == {"type": "voice.state.accepted", "state": "wake"}


def test_websocket_very_long_response_text_is_rejected_by_tts_limit(client, monkeypatch):
    import app.main as main

    class HugeCore(FakeCore):
        def __init__(self):
            super().__init__(output="x" * 5000)

    monkeypatch.setattr(main, "core", HugeCore())
    monkeypatch.setattr(main, "voice_tts", PiperTTS(model_path="/models/en_US-voice.onnx", max_input_chars=1000))
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _speak_one_utterance(ws, device_id)

        ws.receive_json()  # voice.transcript
        ws.receive_json()  # voice.response
        ws.receive_json()  # voice.response.start
        error = ws.receive_json()
        end = ws.receive_json()

        assert error["code"] == "TTS_FAILED"
        assert "limit" in error["message"]
        assert end["status"] == "error"


def test_websocket_multiple_sessions_do_not_mix_response_audio(client):
    laptop_id = _register_trusted_device(client, name="laptop")
    phone_id = _register_trusted_device(client, name="phone")

    with client.websocket_connect("/ws/voice") as laptop_ws, client.websocket_connect(
        "/ws/voice"
    ) as phone_ws:
        _speak_one_utterance(laptop_ws, laptop_id)
        _speak_one_utterance(phone_ws, phone_id)

        laptop_ws.receive_json()
        laptop_transcript_session = laptop_ws.receive_json()

        phone_ws.receive_json()
        phone_transcript_session = phone_ws.receive_json()

        # each socket's own response.start carries its own session_id
        laptop_start = laptop_ws.receive_json()
        phone_start = phone_ws.receive_json()
        assert laptop_start["session_id"] != phone_start["session_id"]


def test_websocket_disconnect_during_response_audio_cleans_up(client):
    import app.main as main

    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _speak_one_utterance(ws, device_id)
        ws.receive_json()  # voice.transcript
        assert len(main.voice_sessions) == 1
        # disconnect mid-synthesis (before draining response.start/audio/end)

    assert len(main.voice_sessions) == 0


def test_websocket_no_tool_execution_reachable_via_tts_path(client):
    """Even the response-audio path only ever reaches the same finish_audio /
    Core.handle call - no new tool-execution entry point was added."""
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _speak_one_utterance(ws, device_id)
        ws.receive_json()
        ws.receive_json()
        ws.receive_json()
        ws.receive_bytes()
        ws.receive_bytes()
        ws.receive_json()

        ws.send_json({"type": "tool.execute", "tool": "fs.read", "arguments": {}})
        error = ws.receive_json()
        assert error["code"] == "UNKNOWN_MESSAGE_TYPE"
