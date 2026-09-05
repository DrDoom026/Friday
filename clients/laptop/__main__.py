"""Entry point: ``python -m clients.laptop`` (PART 12d).

Wires the real microphone/speaker/Porcupine/WebSocket implementations from
environment configuration and runs the wake loop until interrupted. See
``clients/laptop/README.md`` for setup.
"""

import asyncio
import logging
import sys

from clients.laptop.audio_io import SounddeviceMicrophone, SounddeviceSpeaker
from clients.laptop.config import load_config
from clients.laptop.porcupine_wakeword import PorcupineWakeWordDetector
from clients.laptop.transport import WebSocketTransport
from clients.laptop.voice_client import VoiceClient
from clients.laptop.wakeword import WakeWordUnavailableError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("firday.voice.client")


def main() -> int:
    config = load_config()

    client = VoiceClient(
        config,
        wakeword=PorcupineWakeWordDetector(
            access_key=config.porcupine_access_key, keyword_path=config.porcupine_keyword_path
        ),
        mic=SounddeviceMicrophone(sample_rate=config.sample_rate),
        speaker=SounddeviceSpeaker(),
        transport_factory=lambda: WebSocketTransport(config.server_ws_url),
        on_transcript=lambda text: logger.info("heard: %s", text),
        on_response=lambda text: logger.info("FIRDAY: %s", text),
    )

    logger.info("listening for the wake word (device_id=%s)", config.device_id)
    try:
        asyncio.run(client.run_forever())
    except KeyboardInterrupt:
        return 0
    except WakeWordUnavailableError as exc:
        logger.error("wake-word detector unavailable: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
