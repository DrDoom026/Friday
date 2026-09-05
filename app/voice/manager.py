"""In-memory voice session lifecycle (PART 12a).

Shaped like ``app.devices.registry.DeviceRegistry``: a plain id -> object
dict, no persistence, no database - a Pi-sized amount of state for a handful
of concurrent voice clients.
"""

from app.voice.models import VoiceSession


class VoiceSessionManager:
    """Creates, looks up, and removes voice sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}

    def create(self, device_id: str) -> VoiceSession:
        session = VoiceSession(device_id=device_id)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> VoiceSession | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> VoiceSession | None:
        return self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)
