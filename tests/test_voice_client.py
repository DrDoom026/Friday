"""Tests for PART 12d: the laptop wake-word client.

No real microphone, speaker, Porcupine, or network is ever touched - the
wake-word/transport/audio abstractions are exercised through small fakes,
mirroring the pattern already used for the server-side STT/TTS tests.
"""

import asyncio

import pytest

from clients.laptop.config import AUDIO_CHANNELS, AUDIO_FORMAT, ClientConfig
from clients.laptop.porcupine_wakeword import PorcupineWakeWordDetector
from clients.laptop.transport import IncomingMessage, Transport, TransportClosedError
from clients.laptop.voice_client import VoiceClient
from clients.laptop.wakeword import WakeWordDetector, WakeWordUnavailableError

FRAME_LENGTH = 320  # 20ms @ 16kHz
SILENCE_FRAME = b"\x00\x00" * FRAME_LENGTH
LOUD_FRAME = (b"\x10\x27") * FRAME_LENGTH  # 0x2710 = 10000, well above threshold


def make_config(**overrides) -> ClientConfig:
    base = dict(
        server_ws_url="ws://pi.test/ws/voice",
        device_id="laptop",
        sample_rate=16000,
        silence_rms_threshold=500,
        silence_duration_seconds=0.04,  # 2 frames of 20ms
        max_utterance_seconds=0.2,  # 10 frames max
        reconnect_base_seconds=0.001,
        reconnect_max_seconds=0.004,
    )
    base.update(overrides)
    return ClientConfig(**base)


class FakeWakeWordDetector(WakeWordDetector):
    """Wakes on the Nth frame fed to it (default: the first)."""

    def __init__(self, *, frame_length: int = FRAME_LENGTH, wake_on_call: int = 1):
        self._frame_length = frame_length
        self._wake_on_call = wake_on_call
        self.calls = 0

    @property
    def frame_length(self) -> int:
        return self._frame_length

    def process(self, frame: bytes) -> bool:
        self.calls += 1
        return self.calls == self._wake_on_call


class FakeMic:
    """Returns a fixed queue of frames, then loud frames forever."""

    def __init__(self, frames=()):
        self._frames = list(frames)
        self.reads = 0

    def read_frame(self, frame_length: int) -> bytes:
        self.reads += 1
        if self._frames:
            return self._frames.pop(0)
        return LOUD_FRAME[: frame_length * 2]


class FakeSpeaker:
    def __init__(self):
        self.played: list[bytes] = []

    def play(self, pcm_bytes: bytes, *, sample_rate: int) -> None:
        self.played.append(pcm_bytes)


class FakeTransport(Transport):
    """Scripted server: replays a canned list of incoming messages and
    records every outgoing one. ``connect_error`` simulates connect() always
    failing (for reconnect-backoff tests)."""

    def __init__(self, script=(), *, connect_error: bool = False):
        self._script = list(script)
        self.connect_error = connect_error
        self.connected = False
        self.closed = False
        self.sent: list = []

    async def connect(self) -> None:
        if self.connect_error:
            raise TransportClosedError("simulated connect failure")
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def send_json(self, message: dict) -> None:
        self.sent.append(("json", message))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(("bytes", data))

    async def recv(self) -> IncomingMessage:
        if not self._script:
            raise TransportClosedError("simulated disconnect")
        return self._script.pop(0)


def _accepted(session_id="s1"):
    return IncomingMessage("json", {"type": "session.accepted", "session_id": session_id, "device_id": "laptop", "state": "idle"})


def _state_ack(state):
    return IncomingMessage("json", {"type": "voice.state.accepted", "state": state})


def _full_success_script(text="hello", response="hi there", audio_chunks=(b"\x01\x02",)):
    script = [
        _accepted(),
        _state_ack("wake"),
        _state_ack("listening"),
        IncomingMessage("json", {"type": "voice.transcript", "session_id": "s1", "text": text}),
        IncomingMessage("json", {"type": "voice.response", "text": response}),
        IncomingMessage(
            "json",
            {
                "type": "voice.response.start",
                "session_id": "s1",
                "utterance_id": "u1",
                "encoding": "pcm16",
                "sample_rate": 22050,
                "channels": 1,
            },
        ),
    ]
    script += [IncomingMessage("bytes", chunk) for chunk in audio_chunks]
    script.append(
        IncomingMessage(
            "json", {"type": "voice.response.end", "session_id": "s1", "utterance_id": "u1", "status": "ok"}
        )
    )
    return script


def make_client(*, wakeword=None, mic=None, speaker=None, transport=None, config=None, **kwargs):
    config = config or make_config()
    wakeword = wakeword or FakeWakeWordDetector()
    mic = mic or FakeMic()
    speaker = speaker or FakeSpeaker()
    transport = transport if transport is not None else FakeTransport(_full_success_script())
    client = VoiceClient(
        config,
        wakeword=wakeword,
        mic=mic,
        speaker=speaker,
        transport_factory=lambda: transport,
        **kwargs,
    )
    return client, transport


# --- WakeWordDetector abstraction -------------------------------------------


def test_wakeword_detector_is_an_abstract_base():
    with pytest.raises(TypeError):
        WakeWordDetector()


def test_porcupine_detector_conforms_to_the_abstraction():
    assert issubclass(PorcupineWakeWordDetector, WakeWordDetector)


def test_porcupine_missing_access_key_raises_unavailable():
    detector = PorcupineWakeWordDetector(access_key="", keyword_path="/some/friday.ppn")
    with pytest.raises(WakeWordUnavailableError):
        detector.process(b"\x00\x00")


def test_porcupine_missing_keyword_path_raises_unavailable():
    detector = PorcupineWakeWordDetector(access_key="abc123", keyword_path="")
    with pytest.raises(WakeWordUnavailableError):
        detector.process(b"\x00\x00")


def test_porcupine_missing_package_raises_unavailable(monkeypatch):
    detector = PorcupineWakeWordDetector(access_key="abc123", keyword_path="/some/friday.ppn")

    def fake_load(self):
        raise WakeWordUnavailableError("pvporcupine is not installed on this machine")

    monkeypatch.setattr(
        "clients.laptop.porcupine_wakeword.PorcupineWakeWordDetector._load_handle", fake_load
    )
    with pytest.raises(WakeWordUnavailableError):
        detector.process(b"\x00\x00")


def test_porcupine_handle_is_created_once(monkeypatch):
    load_calls = {"count": 0}

    class FakeHandle:
        frame_length = 512

        def process(self, samples):
            return -1

    def fake_load(self):
        load_calls["count"] += 1
        return FakeHandle()

    detector = PorcupineWakeWordDetector(access_key="abc123", keyword_path="/some/friday.ppn")
    monkeypatch.setattr(
        "clients.laptop.porcupine_wakeword.PorcupineWakeWordDetector._load_handle", fake_load
    )

    frame = b"\x00\x00" * 512
    detector.process(frame)
    detector.process(frame)
    assert detector.frame_length == 512

    assert load_calls["count"] == 1


# --- wake -> voice session --------------------------------------------------


def test_wake_detection_triggers_a_voice_session():
    client, transport = make_client(wakeword=FakeWakeWordDetector(wake_on_call=1))
    asyncio.run(asyncio.wait_for(_run_once(client), timeout=2))
    assert transport.connected is True
    assert transport.sent[0] == (
        "json",
        {"type": "session.start", "device_id": "laptop", "client": "laptop", "protocol_version": "1"},
    )


async def _run_once(client: VoiceClient):
    await client._wait_for_wake()
    await client._handle_utterance()


def test_pre_wake_microphone_audio_is_never_sent():
    mic = FakeMic(frames=[SILENCE_FRAME, SILENCE_FRAME, LOUD_FRAME])
    wakeword = FakeWakeWordDetector(wake_on_call=3)
    client, transport = make_client(mic=mic, wakeword=wakeword)

    asyncio.run(asyncio.wait_for(_run_once(client), timeout=2))

    assert wakeword.calls == 3
    # nothing was sent to the transport until after wake was detected
    assert transport.sent[0][0] == "json"
    assert transport.sent[0][1]["type"] == "session.start"


def test_active_session_audio_is_sent_as_binary_frames():
    mic = FakeMic()  # loud frames -> streams until max_utterance_seconds
    client, transport = make_client(mic=mic)

    asyncio.run(asyncio.wait_for(_run_once(client), timeout=2))

    audio_sent = [payload for kind, payload in transport.sent if kind == "bytes"]
    assert len(audio_sent) >= 1
    assert all(isinstance(chunk, (bytes, bytearray)) for chunk in audio_sent)


def test_canonical_audio_format_is_declared_in_audio_start():
    client, transport = make_client(config=make_config(sample_rate=16000))
    asyncio.run(asyncio.wait_for(_run_once(client), timeout=2))

    starts = [m for kind, m in transport.sent if kind == "json" and m.get("type") == "audio.start"]
    assert starts == [
        {"type": "audio.start", "format": AUDIO_FORMAT, "sample_rate": 16000, "channels": AUDIO_CHANNELS}
    ]


def test_device_identity_is_included_in_the_handshake():
    client, transport = make_client(config=make_config(device_id="the-laptop"))
    asyncio.run(asyncio.wait_for(_run_once(client), timeout=2))

    start = next(m for kind, m in transport.sent if kind == "json" and m.get("type") == "session.start")
    assert start["device_id"] == "the-laptop"


def test_untrusted_device_is_rejected_cleanly():
    script = [
        IncomingMessage("json", {"type": "error", "code": "DEVICE_NOT_TRUSTED", "message": "device is not trusted"})
    ]
    transport = FakeTransport(script)
    client, _ = make_client(transport=transport)

    asyncio.run(asyncio.wait_for(_run_once(client), timeout=2))

    # no audio.start/binary frames were ever sent after rejection
    assert not any(kind == "bytes" for kind, _ in transport.sent)
    assert transport.closed is True


def test_reconnect_backoff_is_bounded_and_exponential(monkeypatch):
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    transport = FakeTransport(connect_error=True)
    client, _ = make_client(
        transport=transport, config=make_config(reconnect_base_seconds=1.0, reconnect_max_seconds=4.0)
    )

    asyncio.run(asyncio.wait_for(client._handle_utterance(), timeout=2))

    assert delays == [1.0, 2.0, 4.0, 4.0]  # capped at reconnect_max_seconds, bounded attempt count


def test_disconnect_mid_utterance_returns_to_safe_state():
    script = [_accepted(), _state_ack("wake"), _state_ack("listening")]  # then disconnects
    transport = FakeTransport(script)
    client, _ = make_client(transport=transport, mic=FakeMic())

    # must not raise
    asyncio.run(asyncio.wait_for(client._handle_utterance(), timeout=2))
    assert transport.closed is True


def test_confirmation_gated_response_is_only_ever_spoken_not_acted_on():
    confirmation_text = "The email is waiting for confirmation."
    script = _full_success_script(response=confirmation_text, audio_chunks=())
    transport = FakeTransport(script)

    heard = []
    client, _ = make_client(transport=transport, on_response=heard.append)

    asyncio.run(asyncio.wait_for(client._handle_utterance(), timeout=2))

    assert heard == [confirmation_text]
    # the client's public surface has no tool/execution entry point at all
    assert not any(name.startswith("execute") or "tool" in name.lower() for name in dir(VoiceClient))


def test_two_clients_do_not_share_transport_or_speaker_state():
    client_a, transport_a = make_client(config=make_config(device_id="laptop"))
    speaker_b = FakeSpeaker()
    client_b, transport_b = make_client(
        config=make_config(device_id="phone-companion"), speaker=speaker_b
    )

    asyncio.run(asyncio.wait_for(_run_once(client_a), timeout=2))
    asyncio.run(asyncio.wait_for(_run_once(client_b), timeout=2))

    start_a = next(m for k, m in transport_a.sent if k == "json" and m["type"] == "session.start")
    start_b = next(m for k, m in transport_b.sent if k == "json" and m["type"] == "session.start")
    assert start_a["device_id"] == "laptop"
    assert start_b["device_id"] == "phone-companion"
    assert speaker_b.played, "the second client's own speaker must have received its own audio"
