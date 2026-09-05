"""Laptop voice client configuration (PART 12d).

Mirrors ``app.config``'s style (env vars, ``load_dotenv``, plain
``@dataclass``) but is deliberately a separate, tiny config - this client
runs on a different machine than the FIRDAY server and must not import
``app.*``.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

#: The wire format/channel contract the server (app.voice.audio) requires.
#: Not configurable - it is the protocol, not a preference.
AUDIO_FORMAT = "pcm16"
AUDIO_CHANNELS = 1


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ClientConfig:
    #: e.g. "wss://pi.tailnet.ts.net/ws/voice" - the Tailscale address of the
    #: existing FIRDAY /ws/voice endpoint. Never a public/internet listener.
    server_ws_url: str
    #: This machine's stable logical identity, registered/trusted via the
    #: existing Part 5 device flow (POST /devices) - not invented here.
    device_id: str
    #: Must match the server's STT_SAMPLE_RATE (app/config.py default 16000).
    sample_rate: int = 16000
    #: Picovoice Porcupine. Empty access_key/keyword_path is a clean,
    #: reported misconfiguration - never a fabricated detector.
    porcupine_access_key: str = ""
    porcupine_keyword_path: str = ""
    #: Simple energy-threshold VAD for end-of-utterance detection.
    silence_rms_threshold: int = 500
    silence_duration_seconds: float = 1.2
    max_utterance_seconds: float = 30.0
    #: Reconnect backoff bounds - never an aggressive/uncontrolled retry loop.
    reconnect_base_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0


def load_config() -> ClientConfig:
    return ClientConfig(
        server_ws_url=os.getenv("FIRDAY_VOICE_WS_URL", "ws://localhost:8000/ws/voice"),
        device_id=os.getenv("FIRDAY_DEVICE_ID", "laptop"),
        sample_rate=_int_env("FIRDAY_VOICE_SAMPLE_RATE", 16000),
        porcupine_access_key=os.getenv("PORCUPINE_ACCESS_KEY", ""),
        porcupine_keyword_path=os.getenv("PORCUPINE_KEYWORD_PATH", ""),
        silence_rms_threshold=_int_env("FIRDAY_VOICE_SILENCE_RMS_THRESHOLD", 500),
        silence_duration_seconds=_float_env("FIRDAY_VOICE_SILENCE_DURATION_SECONDS", 1.2),
        max_utterance_seconds=_float_env("FIRDAY_VOICE_MAX_UTTERANCE_SECONDS", 30.0),
        reconnect_base_seconds=_float_env("FIRDAY_VOICE_RECONNECT_BASE_SECONDS", 1.0),
        reconnect_max_seconds=_float_env("FIRDAY_VOICE_RECONNECT_MAX_SECONDS", 30.0),
    )
