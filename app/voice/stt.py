"""Speech-to-text abstraction (PART 12b).

``SpeechToText`` is the only thing the voice pipeline depends on - not
faster-whisper directly. Swapping in whisper.cpp, a cloud STT, or another
local model later means writing one more class here, not touching
``VoiceSession`` or FIRDAY Core.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class STTError(Exception):
    """Transcription failed - unavailable model, bad audio, timeout, etc.

    Callers turn this into a structured WebSocket error; the message must
    never leak a stack trace or internal path.
    """


@dataclass(frozen=True)
class Transcript:
    """One utterance's recognized text. Never carries the audio itself."""

    text: str
    session_id: str
    device_id: str
    language: str | None = None
    created_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.created_at is None:
            object.__setattr__(self, "created_at", utcnow())


class SpeechToText(ABC):
    """Turns raw PCM audio into a :class:`Transcript`."""

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        *,
        sample_rate: int,
        session_id: str,
        device_id: str,
    ) -> Transcript:
        """Transcribe one utterance's mono PCM16 audio.

        Raises :class:`STTError` on empty audio, an unavailable model, or any
        inference failure.
        """
