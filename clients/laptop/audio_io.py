"""Blocking microphone/speaker I/O (PART 12d).

Kept synchronous and minimal - :class:`~clients.laptop.voice_client.VoiceClient`
dispatches these through a thread (``asyncio.to_thread``) rather than
teaching this module async at all. ``sounddevice`` is imported lazily, so
importing this module never requires the package to be installed; only
opening a real stream does.
"""

from abc import ABC, abstractmethod


class MicrophoneSource(ABC):
    """Captures mono PCM16 audio, one fixed-size frame per call."""

    @abstractmethod
    def read_frame(self, frame_length: int) -> bytes:
        """Block until exactly ``frame_length`` int16 samples are captured."""


class SpeakerSink(ABC):
    """Plays back mono PCM16 audio, blocking until playback finishes."""

    @abstractmethod
    def play(self, pcm_bytes: bytes, *, sample_rate: int) -> None:
        """Play ``pcm_bytes`` (raw little-endian int16 mono) at ``sample_rate``."""


class SounddeviceMicrophone(MicrophoneSource):
    """The default Linux microphone, via ``sounddevice`` (PortAudio)."""

    def __init__(self, *, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._stream = None
        self._blocksize = None

    def _ensure_stream(self, frame_length: int):
        if self._stream is None or self._blocksize != frame_length:
            self.close()
            import sounddevice as sd

            self._stream = sd.InputStream(
                samplerate=self._sample_rate, channels=1, dtype="int16", blocksize=frame_length
            )
            self._stream.start()
            self._blocksize = frame_length
        return self._stream

    def read_frame(self, frame_length: int) -> bytes:
        stream = self._ensure_stream(frame_length)
        data, _overflowed = stream.read(frame_length)
        return data.tobytes()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class SounddeviceSpeaker(SpeakerSink):
    """The default Linux speaker, via ``sounddevice`` (PortAudio)."""

    def play(self, pcm_bytes: bytes, *, sample_rate: int) -> None:
        import numpy as np
        import sounddevice as sd

        samples = np.frombuffer(pcm_bytes, dtype="<i2")
        sd.play(samples, samplerate=sample_rate, blocking=True)
