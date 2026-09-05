"""Wires ``audio.start``/``audio.end`` into STT, the existing FIRDAY Core
request path, and (PART 12c) TTS (PART 12b/12c).

This is the only place voice audio turns into a :class:`FirdayRequest`, and
it calls nothing but :meth:`Core.handle` - the same entry point
``POST /request`` uses. No parallel LLM/planner path, no direct provider
calls, no tool execution: whatever the Security Engine and privacy gate
would do for an HTTP request, they do here too. TTS only ever sees the
final response text Core already produced.
"""

import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.core.context import RequestContext
from app.core.models import FirdayRequest
from app.core.orchestrator import Core
from app.voice.audio import SUPPORTED_CHANNELS, SUPPORTED_FORMAT, AudioBuffer, AudioLimitExceededError
from app.voice.models import InvalidVoiceTransitionError, VoiceSession, VoiceState
from app.voice.stt import STTError, SpeechToText, Transcript
from app.voice.tts import TTSError, TextToSpeech


@dataclass(frozen=True)
class VoiceEvent:
    """One thing to send over the WebSocket: a JSON message or a raw audio frame."""

    kind: str  # "json" | "bytes"
    payload: Any


def _json_event(payload: dict[str, Any]) -> VoiceEvent:
    return VoiceEvent("json", payload)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


def start_audio(
    session: VoiceSession, message: dict, *, expected_sample_rate: int, max_bytes: int
) -> tuple[AudioBuffer | None, dict[str, Any]]:
    """Handle ``audio.start``. Returns the new buffer (or ``None``) and the reply."""
    if session.state is not VoiceState.LISTENING:
        return None, _error(
            "INVALID_STATE_TRANSITION", "audio can only be started while listening"
        )

    if message.get("format") != SUPPORTED_FORMAT:
        return None, _error(
            "UNSUPPORTED_AUDIO_FORMAT", f"format must be {SUPPORTED_FORMAT!r}"
        )
    if message.get("sample_rate") != expected_sample_rate:
        return None, _error(
            "UNSUPPORTED_AUDIO_FORMAT", f"sample_rate must be {expected_sample_rate}"
        )
    if message.get("channels") != SUPPORTED_CHANNELS:
        return None, _error(
            "UNSUPPORTED_AUDIO_FORMAT", f"channels must be {SUPPORTED_CHANNELS}"
        )

    buffer = AudioBuffer(sample_rate=expected_sample_rate, max_bytes=max_bytes)
    return buffer, {"type": "audio.start.accepted"}


def append_audio_chunk(buffer: AudioBuffer, chunk: bytes) -> dict[str, Any] | None:
    """Append one binary frame. Returns an error reply on overflow, else ``None``."""
    try:
        buffer.append(chunk)
    except AudioLimitExceededError as exc:
        return _error("AUDIO_LIMIT_EXCEEDED", str(exc))
    return None


async def finish_audio(
    session: VoiceSession,
    buffer: AudioBuffer | None,
    *,
    stt: SpeechToText,
    core: Core,
    tts: TextToSpeech,
) -> AsyncIterator[VoiceEvent]:
    """Handle ``audio.end``: transcribe, run the transcript through Core, speak
    the response.

    Always leaves the session in a valid, stable state (``IDLE``) before
    returning, even when transcription or synthesis fails.
    """
    if buffer is None:
        yield _json_event(_error("AUDIO_NOT_STARTED", "no audio.start was sent for this utterance"))
        return

    try:
        session.transition(VoiceState.THINKING)
    except InvalidVoiceTransitionError as exc:
        yield _json_event(_error("INVALID_STATE_TRANSITION", str(exc)))
        return

    try:
        transcript = await _transcribe(stt, buffer, session)
    except STTError as exc:
        session.transition(VoiceState.IDLE)
        yield _json_event(_error("STT_FAILED", str(exc)))
        return

    session.transcript = transcript.text
    yield _json_event(
        {"type": "voice.transcript", "session_id": transcript.session_id, "text": transcript.text}
    )

    response = await _run_through_core(core, transcript)
    yield _json_event({"type": "voice.response", "text": response.output})

    try:
        session.transition(VoiceState.RESPONDING)
    except InvalidVoiceTransitionError as exc:
        yield _json_event(_error("INVALID_STATE_TRANSITION", str(exc)))
        return

    async for event in synthesize_response(session, response.output, tts=tts):
        yield event

    session.transition(VoiceState.IDLE)


async def synthesize_response(
    session: VoiceSession, text: str, *, tts: TextToSpeech
) -> AsyncIterator[VoiceEvent]:
    """Speak ``text`` (Core's already-produced final response) over the socket.

    Emits ``voice.response.start`` (format metadata), then binary PCM audio
    frames, then ``voice.response.end``. A synthesis failure is reported as a
    structured error - it never crashes the session or fabricates audio.
    """
    utterance_id = uuid.uuid4().hex
    fmt = tts.output_format
    yield _json_event(
        {
            "type": "voice.response.start",
            "session_id": session.session_id,
            "utterance_id": utterance_id,
            "encoding": fmt.encoding,
            "sample_rate": fmt.sample_rate,
            "channels": fmt.channels,
        }
    )

    status = "ok"
    try:
        async for chunk in tts.synthesize(text):
            yield VoiceEvent("bytes", chunk)
    except TTSError as exc:
        status = "error"
        yield _json_event(_error("TTS_FAILED", str(exc)))

    yield _json_event(
        {
            "type": "voice.response.end",
            "session_id": session.session_id,
            "utterance_id": utterance_id,
            "status": status,
        }
    )


async def _transcribe(stt: SpeechToText, buffer: AudioBuffer, session: VoiceSession) -> Transcript:
    if buffer.total_bytes == 0:
        raise STTError("no audio was captured for this utterance")
    return await stt.transcribe(
        buffer.pcm_bytes(),
        sample_rate=buffer.sample_rate,
        session_id=session.session_id,
        device_id=session.device_id,
    )


async def _run_through_core(core: Core, transcript: Transcript):
    """Hand the transcript to Core exactly as ``POST /request`` would."""
    request = FirdayRequest(
        input=transcript.text,
        metadata={
            "source": "voice",
            "session_id": transcript.session_id,
            "device_id": transcript.device_id,
        },
    )
    context = RequestContext.create(source="voice")
    execute = hasattr(core.planner, "finalize")
    return await core.handle(request, context, execute=execute)
