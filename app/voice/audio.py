"""Per-utterance audio accumulation (PART 12b).

One :class:`AudioBuffer` lives for exactly one ``audio.start``..``audio.end``
span, held as a local variable in the WebSocket connection's own coroutine
(see ``app.main.voice_websocket``). It is never stored on the
:class:`~app.voice.models.VoiceSession`, never shared across sessions, and is
discarded the moment the utterance ends or the socket closes - nothing here
writes audio to disk.
"""

from dataclasses import dataclass, field

#: The only wire format 12b accepts: mono, 16-bit signed little-endian PCM.
#: Future clients must resample/encode to this before sending ``audio.start``.
SUPPORTED_FORMAT = "pcm16"
SUPPORTED_CHANNELS = 1


class AudioLimitExceededError(Exception):
    """A session pushed more audio than the configured per-utterance limit."""


@dataclass
class AudioBuffer:
    """Accumulates binary audio frames up to ``max_bytes``."""

    sample_rate: int
    max_bytes: int
    total_bytes: int = 0
    _chunks: list[bytes] = field(default_factory=list, repr=False)

    def append(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        if self.total_bytes > self.max_bytes:
            raise AudioLimitExceededError(
                f"audio exceeded the {self.max_bytes}-byte limit for one utterance"
            )
        self._chunks.append(chunk)

    def pcm_bytes(self) -> bytes:
        return b"".join(self._chunks)
