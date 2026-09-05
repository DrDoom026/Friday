"""Text-to-speech abstraction (PART 12c).

Mirrors ``app.voice.stt``: the voice pipeline depends only on
``TextToSpeech`` - never on Piper directly. Swapping in another local or
cloud engine later means adding one more class here, not touching
``VoiceSession``, the WebSocket protocol, or FIRDAY Core.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from app.voice.audio import SUPPORTED_CHANNELS, SUPPORTED_FORMAT


class TTSError(Exception):
    """Speech synthesis failed - unavailable model, bad input, timeout, etc.

    Callers turn this into a structured WebSocket error; the message must
    never leak a stack trace or internal path.
    """


@dataclass(frozen=True)
class AudioFormat:
    """The canonical wire format for synthesized audio: mono PCM16.

    The same encoding/channel contract 12b defined for the client's
    *incoming* audio (see ``app.voice.audio``) applies to the server's
    *outgoing* audio - one format, both directions.
    """

    sample_rate: int
    encoding: str = SUPPORTED_FORMAT
    channels: int = SUPPORTED_CHANNELS


class TextToSpeech(ABC):
    """Streams response text as raw PCM audio chunks."""

    @property
    @abstractmethod
    def output_format(self) -> AudioFormat:
        """The format every chunk yielded by ``synthesize`` is encoded in."""

    @abstractmethod
    def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Stream ``text`` as one or more raw PCM chunks, in order.

        Raises :class:`TTSError` on empty/oversized input, an unavailable
        model, or any synthesis failure.
        """
