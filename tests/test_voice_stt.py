"""Tests for PART 12b: speech-to-text integration on top of the 12a voice
session backend.

``faster-whisper`` itself is never required to run these - the real package
is mocked out via ``FasterWhisperSTT._load_model``/inference internals, and
most tests use a tiny in-memory fake that conforms to the ``SpeechToText``
abstraction. This keeps the suite fast and independent of any downloaded
model, per PART 12b's testing requirements.
"""

import asyncio

import pytest

from app.core.models import ExecutionStatus, FirdayResponse, Plan, ToolResult
from app.devices.service import DeviceService
from app.devices.tailscale import StaticTailscaleDirectory
from app.voice.audio import AudioBuffer, AudioLimitExceededError
from app.voice.faster_whisper_stt import FasterWhisperSTT
from app.voice.manager import VoiceSessionManager
from app.voice.models import VoiceSession, VoiceState
from app.voice.pipeline import append_audio_chunk, finish_audio, start_audio
from app.voice.stt import STTError, SpeechToText, Transcript
from app.voice.tts import AudioFormat, TTSError, TextToSpeech

TAILNET_USER = "voice-stt-tester@github"


def identity(*, node_id="nSTT123CNTRL", name="laptop.tailnet.ts.net", user=TAILNET_USER, source="whois"):
    from app.devices.models import TailscaleIdentity

    return TailscaleIdentity(
        node_id=node_id, dns_name=name, user_login=user, addresses=("100.64.0.9",), source=source
    )


class FakeSTT(SpeechToText):
    """Conforms to the abstraction without touching faster-whisper at all."""

    def __init__(self, text: str = "turn on the lights", *, fail: bool = False):
        self.text = text
        self.fail = fail
        self.calls: list[bytes] = []

    async def transcribe(self, audio, *, sample_rate, session_id, device_id) -> Transcript:
        self.calls.append(audio)
        if self.fail:
            raise STTError("synthetic failure")
        if not audio:
            raise STTError("no audio was captured for this utterance")
        return Transcript(text=self.text, session_id=session_id, device_id=device_id)


class FakeTTS(TextToSpeech):
    """A no-op TTS conforming to the abstraction - 12b tests don't care about
    speech synthesis, only that Core's response text was reached correctly."""

    def __init__(self, chunks=(b"chunk-a", b"chunk-b"), *, sample_rate: int = 22050, fail: bool = False):
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


async def _collect(agen):
    return [item async for item in agen]


class FakeCore:
    """Stands in for ``app.core.orchestrator.Core`` - records what it received."""

    class _MockPlanner:
        name = "mock"

    def __init__(self):
        self.planner = self._MockPlanner()
        self.requests: list = []

    async def handle(self, request, context, *, execute=False):
        self.requests.append((request, context, execute))
        return FirdayResponse(
            request_id=context.request_id,
            output=f"heard: {request.input}",
            plan=Plan(planner_name="mock", summary="ok"),
            results=[],
        )


# --- STT abstraction / faster-whisper conformance --------------------------


def test_speech_to_text_is_an_abstract_base():
    with pytest.raises(TypeError):
        SpeechToText()


def test_faster_whisper_stt_conforms_to_the_abstraction():
    assert issubclass(FasterWhisperSTT, SpeechToText)


def test_faster_whisper_model_config_is_respected(monkeypatch):
    captured = {}

    class FakeWhisperModel:
        def __init__(self, model_size, device, compute_type):
            captured["model_size"] = model_size
            captured["device"] = device
            captured["compute_type"] = compute_type

        def transcribe(self, samples, language=None):
            return [], type("Info", (), {"language": "en"})()

    stt = FasterWhisperSTT(model_size="base", device="cpu", compute_type="int8")
    monkeypatch.setattr(
        "app.voice.faster_whisper_stt.FasterWhisperSTT._load_model",
        lambda self: FakeWhisperModel(self._model_size, self._device, self._compute_type),
    )

    asyncio.run(
        stt.transcribe(b"\x00\x00" * 16000, sample_rate=16000, session_id="s1", device_id="laptop")
    )

    assert captured == {"model_size": "base", "device": "cpu", "compute_type": "int8"}


def test_faster_whisper_model_is_loaded_once(monkeypatch):
    load_calls = {"count": 0}

    class Segment:
        text = "hello"

    class FakeWhisperModel:
        def transcribe(self, samples, language=None):
            return [Segment()], type("Info", (), {"language": "en"})()

    def fake_load(self):
        load_calls["count"] += 1
        return FakeWhisperModel()

    stt = FasterWhisperSTT()
    monkeypatch.setattr("app.voice.faster_whisper_stt.FasterWhisperSTT._load_model", fake_load)

    async def run_three():
        for _ in range(3):
            await stt.transcribe(
                b"\x00\x00" * 16000, sample_rate=16000, session_id="s1", device_id="laptop"
            )

    asyncio.run(run_three())

    assert load_calls["count"] == 1, "the model must be reused across utterances, not reloaded"


def test_faster_whisper_transcribe_produces_transcript(monkeypatch):
    class Segment:
        def __init__(self, text):
            self.text = text

    class FakeWhisperModel:
        def transcribe(self, samples, language=None):
            return [Segment(" turn "), Segment("on the lights ")], type(
                "Info", (), {"language": "en"}
            )()

    stt = FasterWhisperSTT()
    monkeypatch.setattr(
        "app.voice.faster_whisper_stt.FasterWhisperSTT._load_model", lambda self: FakeWhisperModel()
    )

    transcript = asyncio.run(
        stt.transcribe(b"\x00\x00" * 16000, sample_rate=16000, session_id="s1", device_id="laptop")
    )

    assert transcript.text == "turn on the lights"
    assert transcript.session_id == "s1"
    assert transcript.device_id == "laptop"
    assert transcript.language == "en"


def test_faster_whisper_empty_audio_raises_stt_error():
    stt = FasterWhisperSTT()
    with pytest.raises(STTError):
        asyncio.run(stt.transcribe(b"", sample_rate=16000, session_id="s1", device_id="laptop"))


def test_faster_whisper_missing_package_raises_stt_error(monkeypatch):
    def fake_load(self):
        raise STTError("faster-whisper is not installed on this machine")

    stt = FasterWhisperSTT()
    monkeypatch.setattr("app.voice.faster_whisper_stt.FasterWhisperSTT._load_model", fake_load)

    with pytest.raises(STTError):
        asyncio.run(
            stt.transcribe(b"\x00\x00" * 16000, sample_rate=16000, session_id="s1", device_id="laptop")
        )


def test_faster_whisper_inference_failure_is_wrapped(monkeypatch):
    class FakeWhisperModel:
        def transcribe(self, samples, language=None):
            raise RuntimeError("internal model panic with a secret path /etc/shadow")

    stt = FasterWhisperSTT()
    monkeypatch.setattr(
        "app.voice.faster_whisper_stt.FasterWhisperSTT._load_model", lambda self: FakeWhisperModel()
    )

    with pytest.raises(STTError) as excinfo:
        asyncio.run(
            stt.transcribe(b"\x00\x00" * 16000, sample_rate=16000, session_id="s1", device_id="laptop")
        )
    assert "/etc/shadow" not in str(excinfo.value), "internal errors must not leak to the client"


# --- audio buffer -----------------------------------------------------------


def test_audio_buffer_accumulates_chunks():
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1000)
    buffer.append(b"abcd")
    buffer.append(b"efgh")
    assert buffer.pcm_bytes() == b"abcdefgh"
    assert buffer.total_bytes == 8


def test_audio_buffer_enforces_max_bytes():
    buffer = AudioBuffer(sample_rate=16000, max_bytes=4)
    with pytest.raises(AudioLimitExceededError):
        buffer.append(b"too many bytes")


# --- pipeline: audio.start / audio.end / Core handoff ------------------------


def _listening_session() -> VoiceSession:
    session = VoiceSession(device_id="laptop")
    session.transition(VoiceState.WAKE)
    session.transition(VoiceState.LISTENING)
    return session


def test_start_audio_requires_listening_state():
    session = VoiceSession(device_id="laptop")  # still IDLE
    buffer, response = start_audio(
        session,
        {"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1},
        expected_sample_rate=16000,
        max_bytes=1_000_000,
    )
    assert buffer is None
    assert response["code"] == "INVALID_STATE_TRANSITION"


def test_start_audio_rejects_unsupported_format():
    session = _listening_session()
    buffer, response = start_audio(
        session,
        {"type": "audio.start", "format": "mp3", "sample_rate": 16000, "channels": 1},
        expected_sample_rate=16000,
        max_bytes=1_000_000,
    )
    assert buffer is None
    assert response["code"] == "UNSUPPORTED_AUDIO_FORMAT"


def test_start_audio_accepts_valid_handshake():
    session = _listening_session()
    buffer, response = start_audio(
        session,
        {"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1},
        expected_sample_rate=16000,
        max_bytes=1_000_000,
    )
    assert response == {"type": "audio.start.accepted"}
    assert buffer.sample_rate == 16000
    assert buffer.max_bytes == 1_000_000


def test_append_audio_chunk_reports_overflow():
    buffer = AudioBuffer(sample_rate=16000, max_bytes=4)
    assert append_audio_chunk(buffer, b"ab") is None
    error = append_audio_chunk(buffer, b"cdef")
    assert error["code"] == "AUDIO_LIMIT_EXCEEDED"


def test_finish_audio_without_start_is_a_safe_error():
    session = _listening_session()
    events = asyncio.run(
        _collect(finish_audio(session, None, stt=FakeSTT(), core=FakeCore(), tts=FakeTTS()))
    )
    assert [e.payload for e in events] == [
        {
            "type": "error",
            "code": "AUDIO_NOT_STARTED",
            "message": "no audio.start was sent for this utterance",
        }
    ]
    assert session.state is VoiceState.LISTENING


def test_finish_audio_transcribes_and_reaches_core():
    session = _listening_session()
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)
    buffer.append(b"\x00\x01" * 100)

    fake_stt = FakeSTT(text="send an email to alex")
    fake_core = FakeCore()
    fake_tts = FakeTTS()

    events = asyncio.run(
        _collect(finish_audio(session, buffer, stt=fake_stt, core=fake_core, tts=fake_tts))
    )
    payloads = [e.payload for e in events if e.kind == "json"]

    assert payloads[0] == {
        "type": "voice.transcript",
        "session_id": session.session_id,
        "text": "send an email to alex",
    }
    assert payloads[1] == {"type": "voice.response", "text": "heard: send an email to alex"}

    # the transcript reached Core's normal request path, not a shortcut
    assert len(fake_core.requests) == 1
    request, context, execute = fake_core.requests[0]
    assert request.input == "send an email to alex"
    assert request.metadata["source"] == "voice"
    assert request.metadata["session_id"] == session.session_id
    assert request.metadata["device_id"] == "laptop"

    # and Core's response text - not the transcript - is what got spoken
    assert fake_tts.calls == ["heard: send an email to alex"]

    assert session.state is VoiceState.IDLE
    assert session.transcript == "send an email to alex"
    assert fake_stt.calls == [buffer.pcm_bytes()]


def test_finish_audio_empty_buffer_is_handled_safely():
    session = _listening_session()
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)  # never appended to

    events = asyncio.run(
        _collect(finish_audio(session, buffer, stt=FakeSTT(), core=FakeCore(), tts=FakeTTS()))
    )

    assert events[0].payload["code"] == "STT_FAILED"
    assert session.state is VoiceState.IDLE


def test_finish_audio_stt_failure_produces_structured_error_and_resets_state():
    session = _listening_session()
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)
    buffer.append(b"\x00\x01" * 100)

    events = asyncio.run(
        _collect(
            finish_audio(session, buffer, stt=FakeSTT(fail=True), core=FakeCore(), tts=FakeTTS())
        )
    )

    assert [e.payload for e in events] == [
        {"type": "error", "code": "STT_FAILED", "message": "synthetic failure"}
    ]
    assert session.state is VoiceState.IDLE


def test_finish_audio_never_calls_a_tool_directly():
    """FakeCore.handle is the only entry point reached - nothing here can call
    a named tool, the registry, or the Security Engine on its own."""
    session = _listening_session()
    buffer = AudioBuffer(sample_rate=16000, max_bytes=1_000_000)
    buffer.append(b"\x00\x01" * 100)

    class ToolCallingCore(FakeCore):
        async def execute_tool(self, *args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("voice pipeline must never call a tool directly")

    fake_core = ToolCallingCore()
    asyncio.run(_collect(finish_audio(session, buffer, stt=FakeSTT(), core=fake_core, tts=FakeTTS())))
    assert len(fake_core.requests) == 1


# --- session/buffer isolation ------------------------------------------------


def test_manager_sessions_carry_independent_transcripts():
    manager = VoiceSessionManager()
    a = manager.create("laptop")
    b = manager.create("phone")

    a.transition(VoiceState.WAKE)
    a.transition(VoiceState.LISTENING)
    a.transition(VoiceState.THINKING)
    a.transcript = "for laptop only"

    assert manager.get(a.session_id).transcript == "for laptop only"
    assert manager.get(b.session_id).transcript == ""


# --- the WebSocket endpoint: full audio round trip --------------------------


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
    monkeypatch.setattr(main, "voice_stt", FakeSTT(text="what is the weather"))
    monkeypatch.setattr(main, "voice_tts", FakeTTS())
    monkeypatch.setattr(main, "core", FakeCore())
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


def test_websocket_full_audio_round_trip(client):
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _start_session(ws, device_id)

        ws.send_json(
            {"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1}
        )
        assert ws.receive_json() == {"type": "audio.start.accepted"}

        ws.send_bytes(b"\x00\x01" * 100)
        ws.send_bytes(b"\x02\x03" * 100)

        ws.send_json({"type": "audio.end"})
        transcript_event = ws.receive_json()
        response_event = ws.receive_json()

        assert transcript_event == {
            "type": "voice.transcript",
            "session_id": transcript_event["session_id"],
            "text": "what is the weather",
        }
        assert response_event == {"type": "voice.response", "text": "heard: what is the weather"}


def test_websocket_audio_before_listening_is_rejected(client):
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "session.start", "device_id": device_id})
        ws.receive_json()  # still IDLE

        ws.send_json(
            {"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1}
        )
        error = ws.receive_json()
        assert error["code"] == "INVALID_STATE_TRANSITION"


def test_websocket_binary_frame_before_audio_start_is_rejected(client):
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _start_session(ws, device_id)

        ws.send_bytes(b"\x00\x01")
        error = ws.receive_json()
        assert error["code"] == "AUDIO_NOT_STARTED"


def test_websocket_audio_limit_exceeded_keeps_connection_alive(client, monkeypatch):
    import dataclasses

    import app.main as main

    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, stt_max_audio_bytes=10))
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _start_session(ws, device_id)

        ws.send_json(
            {"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1}
        )
        ws.receive_json()

        ws.send_bytes(b"0123456789ABCDEF")  # 16 bytes > the 10-byte limit
        error = ws.receive_json()
        assert error["code"] == "AUDIO_LIMIT_EXCEEDED"

        # the connection is still usable afterwards
        ws.send_json({"type": "voice.state", "state": "paused"})
        ack = ws.receive_json()
        assert ack == {"type": "voice.state.accepted", "state": "paused"}


def test_websocket_multiple_sessions_do_not_mix_audio_or_transcripts(client):
    laptop_id = _register_trusted_device(client, name="laptop")
    phone_id = _register_trusted_device(client, name="phone")

    with client.websocket_connect("/ws/voice") as laptop_ws, client.websocket_connect(
        "/ws/voice"
    ) as phone_ws:
        _start_session(laptop_ws, laptop_id)
        _start_session(phone_ws, phone_id)

        laptop_ws.send_json(
            {"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1}
        )
        laptop_ws.receive_json()
        laptop_ws.send_bytes(b"\x01\x01" * 50)
        laptop_ws.send_json({"type": "audio.end"})
        laptop_transcript = laptop_ws.receive_json()
        laptop_ws.receive_json()

        phone_ws.send_json(
            {"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1}
        )
        phone_ws.receive_json()
        phone_ws.send_bytes(b"\x02\x02" * 50)
        phone_ws.send_json({"type": "audio.end"})
        phone_transcript = phone_ws.receive_json()
        phone_ws.receive_json()

        assert laptop_transcript["session_id"] != phone_transcript["session_id"]
        assert laptop_transcript["text"] == "what is the weather"
        assert phone_transcript["text"] == "what is the weather"


def test_websocket_disconnect_mid_utterance_cleans_up(client):
    import app.main as main

    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _start_session(ws, device_id)
        ws.send_json(
            {"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1}
        )
        ws.receive_json()
        ws.send_bytes(b"\x00\x01" * 10)
        assert len(main.voice_sessions) == 1

    assert len(main.voice_sessions) == 0, "disconnect mid-utterance must still remove the session"


def test_websocket_stt_failure_is_a_structured_error(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "voice_stt", FakeSTT(fail=True))
    device_id = _register_trusted_device(client)

    with client.websocket_connect("/ws/voice") as ws:
        _start_session(ws, device_id)
        ws.send_json(
            {"type": "audio.start", "format": "pcm16", "sample_rate": 16000, "channels": 1}
        )
        ws.receive_json()
        ws.send_bytes(b"\x00\x01" * 10)
        ws.send_json({"type": "audio.end"})

        error = ws.receive_json()
        assert error == {"type": "error", "code": "STT_FAILED", "message": "synthetic failure"}
