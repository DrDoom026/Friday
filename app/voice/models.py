"""Voice session model and state machine (PART 12a).

The server is authoritative over ``VoiceSession.state`` - a client can only
*request* a transition (see ``app.voice.protocol``), never assert one. No
audio, transcript text, or session data is persisted; sessions live only in
process memory for the lifetime of the WebSocket connection.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def new_session_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VoiceState(str, Enum):
    """Where a voice session is in its listen/think/respond cycle."""

    IDLE = "idle"
    WAKE = "wake"
    LISTENING = "listening"
    PAUSED = "paused"
    THINKING = "thinking"
    RESPONDING = "responding"


#: The intended future lifecycle is IDLE -> WAKE -> LISTENING -> (PAUSED <->
#: LISTENING) -> THINKING -> RESPONDING -> IDLE. ``voice.end`` additionally
#: allows a reset straight back to IDLE from any active state - a hangup, not
#: a step in the ladder.
VALID_TRANSITIONS: dict[VoiceState, frozenset[VoiceState]] = {
    VoiceState.IDLE: frozenset({VoiceState.WAKE}),
    VoiceState.WAKE: frozenset({VoiceState.LISTENING, VoiceState.IDLE}),
    VoiceState.LISTENING: frozenset(
        {VoiceState.PAUSED, VoiceState.THINKING, VoiceState.IDLE}
    ),
    VoiceState.PAUSED: frozenset({VoiceState.LISTENING, VoiceState.IDLE}),
    VoiceState.THINKING: frozenset({VoiceState.RESPONDING, VoiceState.IDLE}),
    VoiceState.RESPONDING: frozenset({VoiceState.IDLE}),
}


class InvalidVoiceTransitionError(Exception):
    """A client requested a transition the state machine does not allow."""

    def __init__(self, current: VoiceState, target: VoiceState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot transition from {current.value!r} to {target.value!r}")


class VoiceSession(BaseModel):
    """One connected voice client's session state.

    ``transcript`` and ``audio_state`` are metadata placeholders for 12b/12c -
    12a never writes anything but the empty default into them.
    """

    session_id: str = Field(default_factory=new_session_id)
    device_id: str
    state: VoiceState = VoiceState.IDLE
    transcript: str = ""
    audio_state: str = "idle"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def transition(self, target: VoiceState) -> None:
        """Move to ``target``, or raise if the state machine forbids it."""
        if target not in VALID_TRANSITIONS.get(self.state, frozenset()):
            raise InvalidVoiceTransitionError(self.state, target)
        self.state = target
        self.updated_at = utcnow()
