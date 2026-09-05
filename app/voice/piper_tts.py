"""Piper implementation of the :class:`TextToSpeech` abstraction.

``piper-tts`` is only ever imported lazily, inside
:meth:`PiperTTS._load_voice` - building the app and importing this module
must not require the package (or a model file) to be present; only calling
``synthesize`` does. The voice loads once, on first use, and is reused for
every later utterance - never reloaded per request.
"""

import asyncio
import logging
from typing import AsyncIterator

from app.voice.tts import AudioFormat, TTSError, TextToSpeech

logger = logging.getLogger("firday.voice.tts")

#: Sentinel pushed onto the producer/consumer queue to mark end-of-stream.
_DONE = object()


class PiperTTS(TextToSpeech):
    """Local CPU synthesis via a Piper ``.onnx`` voice model.

    ``model_path`` and ``config_path`` are operator-supplied configuration
    (PART 12c settings), never hard-coded - there is no default voice
    shipped with FIRDAY. ``sample_rate`` must match the configured voice
    model; it is declared up front (not read from the model) so
    ``voice.response.start`` can advertise the format before synthesis runs.
    """

    def __init__(
        self,
        *,
        model_path: str,
        config_path: str | None = None,
        sample_rate: int = 22050,
        timeout_seconds: float = 20.0,
        max_input_chars: int = 1000,
    ) -> None:
        self._model_path = model_path
        self._config_path = config_path
        self._sample_rate = sample_rate
        self._timeout_seconds = timeout_seconds
        self._max_input_chars = max_input_chars
        self._voice = None
        self._load_lock = asyncio.Lock()

    @property
    def output_format(self) -> AudioFormat:
        return AudioFormat(sample_rate=self._sample_rate)

    def _load_voice(self):
        """Blocking model load - always run via ``asyncio.to_thread``."""
        if not self._model_path:
            raise TTSError("no Piper voice model is configured (TTS_MODEL_PATH)")
        try:
            from piper.voice import PiperVoice
        except ImportError as exc:
            raise TTSError("piper-tts is not installed on this machine") from exc
        logger.info("loading piper voice (model=%s)", self._model_path)
        return PiperVoice.load(self._model_path, config_path=self._config_path)

    async def _ensure_voice(self):
        if self._voice is None:
            async with self._load_lock:
                if self._voice is None:  # re-check: another waiter may have loaded it
                    self._voice = await asyncio.to_thread(self._load_voice)
        return self._voice

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if not text or not text.strip():
            raise TTSError("no text to synthesize")
        if len(text) > self._max_input_chars:
            raise TTSError(
                f"text exceeds the {self._max_input_chars}-character limit for one utterance"
            )

        voice = await self._ensure_voice()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def produce() -> None:
            try:
                for chunk in voice.synthesize_stream_raw(text):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as exc:  # noqa: BLE001 - surfaced as TTSError below
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        producer = asyncio.create_task(asyncio.to_thread(produce))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=self._timeout_seconds)
                except asyncio.TimeoutError as exc:
                    raise TTSError("speech synthesis timed out") from exc
                if item is _DONE:
                    return
                if isinstance(item, Exception):
                    logger.exception("piper synthesis failed", exc_info=item)
                    raise TTSError("speech synthesis failed") from item
                yield item
        finally:
            await producer
