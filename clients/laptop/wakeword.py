"""Local wake-word detection abstraction (PART 12d).

``WakeWordDetector`` is the only thing the voice client depends on - never
Porcupine directly. Runs entirely on this device: raw microphone audio is
fed in frame-by-frame and never leaves the machine here, regardless of
which implementation is behind the abstraction.
"""

from abc import ABC, abstractmethod


class WakeWordUnavailableError(Exception):
    """The configured detector cannot run - missing key/model/package.

    Preferred over silently returning "never detected": a caller should
    surface this and refuse to pretend wake-word detection is active.
    """


class WakeWordDetector(ABC):
    """Feed raw PCM16 mono frames in; get a wake/no-wake decision back."""

    @property
    @abstractmethod
    def frame_length(self) -> int:
        """Exact number of int16 samples ``process`` expects per call."""

    @abstractmethod
    def process(self, frame: bytes) -> bool:
        """Return ``True`` if the wake word ("Friday") was detected in ``frame``.

        Raises :class:`WakeWordUnavailableError` if the detector cannot run
        at all (bad config, missing package/model) - never fabricates a
        "no wake" result to hide a broken detector.
        """

    def close(self) -> None:
        """Release any native resources. Safe to call multiple times."""
