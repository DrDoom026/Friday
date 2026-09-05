"""Porcupine implementation of :class:`WakeWordDetector` (PART 12d).

``pvporcupine`` is only ever imported lazily, inside
:meth:`PorcupineWakeWordDetector._load_handle` - importing this module must
not require the package (or an access key/model) to be present; only the
first ``process`` call does. The Porcupine handle loads once and is reused
for every subsequent frame, never recreated per call.
"""

import logging

from clients.laptop.wakeword import WakeWordDetector, WakeWordUnavailableError

logger = logging.getLogger("firday.voice.client.wakeword")


class PorcupineWakeWordDetector(WakeWordDetector):
    """Detects the "Friday" wake word via a custom Porcupine keyword model.

    ``access_key`` and ``keyword_path`` are operator-supplied configuration
    (never hard-coded, never logged) - see ``clients/laptop/README.md`` for
    how to obtain a Picovoice access key and a "Friday" keyword file.
    """

    def __init__(self, *, access_key: str, keyword_path: str) -> None:
        self._access_key = access_key
        self._keyword_path = keyword_path
        self._handle = None

    def _load_handle(self):
        if not self._access_key:
            raise WakeWordUnavailableError(
                "no Picovoice access key is configured (PORCUPINE_ACCESS_KEY)"
            )
        if not self._keyword_path:
            raise WakeWordUnavailableError(
                "no wake-word model is configured (PORCUPINE_KEYWORD_PATH)"
            )
        try:
            import pvporcupine
        except ImportError as exc:
            raise WakeWordUnavailableError("pvporcupine is not installed on this machine") from exc

        try:
            return pvporcupine.create(
                access_key=self._access_key, keyword_paths=[self._keyword_path]
            )
        except Exception as exc:  # noqa: BLE001 - never leak the access key/model path
            logger.exception("failed to initialize Porcupine")
            raise WakeWordUnavailableError("failed to initialize the wake-word detector") from exc

    def _ensure_handle(self):
        if self._handle is None:
            self._handle = self._load_handle()
        return self._handle

    @property
    def frame_length(self) -> int:
        return self._ensure_handle().frame_length

    def process(self, frame: bytes) -> bool:
        handle = self._ensure_handle()
        samples = _pcm16_to_int_list(frame)
        return handle.process(samples) >= 0

    def close(self) -> None:
        if self._handle is not None:
            self._handle.delete()
            self._handle = None


def _pcm16_to_int_list(frame: bytes) -> list[int]:
    """Unpack raw little-endian PCM16 bytes into signed ints, no numpy needed."""
    import array

    values = array.array("h")
    values.frombytes(frame)
    return values.tolist()
