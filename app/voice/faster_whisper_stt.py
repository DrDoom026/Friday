"""``faster-whisper`` implementation of the :class:`SpeechToText` abstraction.

``faster-whisper`` (and its ``numpy``/``ctranslate2`` dependencies) is only
ever imported lazily, inside :meth:`FasterWhisperSTT._load_model`. Importing
this module - and building the app - must not require the package to be
installed; only calling ``transcribe`` does. The model itself loads once, on
first use, and is reused for every subsequent utterance.
"""

import asyncio
import logging

from app.voice.stt import STTError, SpeechToText, Transcript

logger = logging.getLogger("firday.voice.stt")


class FasterWhisperSTT(SpeechToText):
    """Local CPU transcription, sized for a Raspberry Pi 4 (tiny/int8 by default)."""

    def __init__(
        self,
        *,
        model_size: str = "tiny",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._timeout_seconds = timeout_seconds
        self._model = None
        self._load_lock = asyncio.Lock()

    def _load_model(self):
        """Blocking model load - always run via ``asyncio.to_thread``."""
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTError("faster-whisper is not installed on this machine") from exc
        logger.info(
            "loading faster-whisper model (model=%s, device=%s, compute_type=%s)",
            self._model_size,
            self._device,
            self._compute_type,
        )
        return WhisperModel(self._model_size, device=self._device, compute_type=self._compute_type)

    async def _ensure_model(self):
        if self._model is None:
            async with self._load_lock:
                if self._model is None:  # re-check: another waiter may have loaded it
                    self._model = await asyncio.to_thread(self._load_model)
        return self._model

    def _run_inference(self, model, samples) -> tuple[str, str | None]:
        segments, info = model.transcribe(samples, language=self._language)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        language = getattr(info, "language", None) or self._language
        return text, language

    async def transcribe(
        self,
        audio: bytes,
        *,
        sample_rate: int,
        session_id: str,
        device_id: str,
    ) -> Transcript:
        if not audio:
            raise STTError("no audio was captured for this utterance")

        try:
            import numpy as np
        except ImportError as exc:
            raise STTError("numpy is not installed on this machine") from exc

        model = await self._ensure_model()
        samples = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0

        try:
            text, language = await asyncio.wait_for(
                asyncio.to_thread(self._run_inference, model, samples),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise STTError("transcription timed out") from exc
        except STTError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak model internals to a client
            logger.exception("faster-whisper transcription failed")
            raise STTError("transcription failed") from exc

        return Transcript(text=text, session_id=session_id, device_id=device_id, language=language)
