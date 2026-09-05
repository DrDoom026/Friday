"""The laptop voice client's orchestration loop (PART 12d).

I/O only: this module never plans, executes a tool, or calls an LLM. Every
utterance's text - transcript and final response - comes from the existing
FIRDAY Core request path on the Pi (Parts 12a-12c); this client only feeds
audio in and plays audio/text back. The server (``app.voice``) remains
authoritative over session state - this client drives the same
``voice.state`` messages any client would, it does not run a second state
machine.

Flow, one wake::

    local wake-word loop (mic never leaves this machine)
      -> "Friday" detected
      -> connect /ws/voice (bounded exponential backoff)
      -> session.start -> voice.state wake -> voice.state listening
      -> audio.start -> mic frames -> silence/duration -> audio.end
      -> voice.transcript / voice.response (text callbacks)
      -> voice.response.start -> audio frames (played) -> voice.response.end
      -> back to the local wake-word loop
"""

import audioop
import logging
from typing import Callable

from clients.laptop.audio_io import MicrophoneSource, SpeakerSink
from clients.laptop.config import AUDIO_CHANNELS, AUDIO_FORMAT, ClientConfig
from clients.laptop.transport import Transport, TransportClosedError
from clients.laptop.wakeword import WakeWordDetector

logger = logging.getLogger("firday.voice.client")

#: 20ms frames - a conventional VAD granularity, small enough for a
#: responsive silence cutoff without flooding the socket with tiny sends.
_FRAME_SECONDS = 0.02


def _is_silence(frame: bytes, rms_threshold: int) -> bool:
    if not frame:
        return True
    return audioop.rms(frame, 2) < rms_threshold


class VoiceClient:
    """Wires a wake-word detector, microphone, speaker and transport together."""

    def __init__(
        self,
        config: ClientConfig,
        *,
        wakeword: WakeWordDetector,
        mic: MicrophoneSource,
        speaker: SpeakerSink,
        transport_factory: Callable[[], Transport],
        on_transcript: Callable[[str], None] | None = None,
        on_response: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._wakeword = wakeword
        self._mic = mic
        self._speaker = speaker
        self._transport_factory = transport_factory
        self._on_transcript = on_transcript or (lambda text: None)
        self._on_response = on_response or (lambda text: None)
        self._response_sample_rate = config.sample_rate

    async def run_forever(self) -> None:
        """Wake, handle one utterance, repeat - until cancelled.

        Raises :class:`~clients.laptop.wakeword.WakeWordUnavailableError` if
        the detector cannot run at all; the caller should surface that
        clearly rather than have this loop silently never wake.
        """
        while True:
            await self._wait_for_wake()
            await self._handle_utterance()

    async def _wait_for_wake(self) -> None:
        import asyncio

        frame_length = self._wakeword.frame_length
        while True:
            frame = await asyncio.to_thread(self._mic.read_frame, frame_length)
            if self._wakeword.process(frame):
                logger.info("wake word detected")
                return

    async def _handle_utterance(self) -> None:
        self._response_sample_rate = self._config.sample_rate
        transport = self._transport_factory()

        try:
            await self._connect_with_backoff(transport)
        except TransportClosedError:
            logger.error("could not reach FIRDAY after repeated retries; returning to wake loop")
            return

        try:
            session_id = await self._handshake(transport)
            if session_id is None:
                return
            await self._stream_utterance(transport)
            await self._receive_response(transport)
        except TransportClosedError:
            logger.warning("voice session disconnected; returning to local wake loop")
        finally:
            await transport.close()

    async def _connect_with_backoff(self, transport: Transport, *, max_attempts: int = 5) -> None:
        import asyncio

        delay = self._config.reconnect_base_seconds
        for attempt in range(max_attempts):
            try:
                await transport.connect()
                return
            except TransportClosedError:
                if attempt == max_attempts - 1:
                    raise
                logger.warning("voice connection attempt %d failed; retrying in %.1fs", attempt + 1, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._config.reconnect_max_seconds)

    async def _send_state(self, transport: Transport, state: str) -> bool:
        await transport.send_json({"type": "voice.state", "state": state})
        msg = await transport.recv()
        return msg.kind == "json" and msg.payload.get("type") == "voice.state.accepted"

    async def _handshake(self, transport: Transport) -> str | None:
        await transport.send_json(
            {
                "type": "session.start",
                "device_id": self._config.device_id,
                "client": "laptop",
                "protocol_version": "1",
            }
        )
        msg = await transport.recv()
        if msg.kind != "json" or msg.payload.get("type") != "session.accepted":
            logger.warning(
                "voice session rejected: %s", msg.payload if msg.kind == "json" else "<binary>"
            )
            return None

        if not await self._send_state(transport, "wake"):
            return None
        if not await self._send_state(transport, "listening"):
            return None
        return msg.payload["session_id"]

    async def _stream_utterance(self, transport: Transport) -> None:
        import asyncio

        await transport.send_json(
            {
                "type": "audio.start",
                "format": AUDIO_FORMAT,
                "sample_rate": self._config.sample_rate,
                "channels": AUDIO_CHANNELS,
            }
        )

        frame_samples = max(1, int(self._config.sample_rate * _FRAME_SECONDS))
        silence_frames_needed = max(1, int(self._config.silence_duration_seconds / _FRAME_SECONDS))
        max_frames = max(1, int(self._config.max_utterance_seconds / _FRAME_SECONDS))
        silence_run = 0

        for _ in range(max_frames):
            frame = await asyncio.to_thread(self._mic.read_frame, frame_samples)
            await transport.send_bytes(frame)
            if _is_silence(frame, self._config.silence_rms_threshold):
                silence_run += 1
                if silence_run >= silence_frames_needed:
                    break
            else:
                silence_run = 0

        await transport.send_json({"type": "audio.end"})

    async def _receive_response(self, transport: Transport) -> None:
        while True:
            msg = await transport.recv()

            if msg.kind == "bytes":
                self._speaker.play(msg.payload, sample_rate=self._response_sample_rate)
                continue

            payload = msg.payload
            msg_type = payload.get("type")
            if msg_type == "voice.transcript":
                self._on_transcript(payload.get("text", ""))
            elif msg_type == "voice.response":
                self._on_response(payload.get("text", ""))
            elif msg_type == "voice.response.start":
                self._response_sample_rate = payload.get("sample_rate", self._config.sample_rate)
            elif msg_type == "voice.response.end":
                return
            elif msg_type == "error":
                logger.warning("voice error (%s): %s", payload.get("code"), payload.get("message"))
                return
            # any other/unknown message type is ignored, not fatal
