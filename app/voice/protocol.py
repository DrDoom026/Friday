"""The small JSON message protocol spoken over ``/ws/voice`` (PART 12a).

Deliberately tiny: session start/end and state-transition requests only. No
audio message type exists yet - that is 12b's ``audio.chunk``, layered on top
of this without needing to redesign it.

``handle_message`` never touches the Tool Framework, Security Engine, or
Core/LLM - a voice session carries zero tool authority by itself.
"""

from typing import Any

from app.voice.models import InvalidVoiceTransitionError, VoiceSession, VoiceState

#: message type -> the state it requests (voice.state's target is read from
#: the message body itself, so it is handled separately).
_SUGAR_TRANSITIONS = {
    "voice.pause": VoiceState.PAUSED,
    "voice.resume": VoiceState.LISTENING,
    "voice.end": VoiceState.IDLE,
}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


def _apply_transition(session: VoiceSession, target: VoiceState, ack_type: str) -> dict[str, Any]:
    try:
        session.transition(target)
    except InvalidVoiceTransitionError as exc:
        return _error("INVALID_STATE_TRANSITION", str(exc))
    return {"type": ack_type, "state": session.state.value}


def handle_message(session: VoiceSession, message: Any) -> dict[str, Any] | None:
    """Handle one decoded client message against ``session``.

    Returns the response to send back, or ``None`` when the client asked to
    end the session (``session.end`` - the caller closes the socket).
    """
    if not isinstance(message, dict) or "type" not in message:
        return _error("PROTOCOL_ERROR", "message must be a JSON object with a 'type'")

    msg_type = message["type"]

    if msg_type == "session.end":
        return None

    if msg_type == "voice.state":
        raw_state = message.get("state")
        try:
            target = VoiceState(raw_state)
        except ValueError:
            return _error("INVALID_STATE", f"unknown state {raw_state!r}")
        return _apply_transition(session, target, "voice.state.accepted")

    if msg_type in _SUGAR_TRANSITIONS:
        return _apply_transition(session, _SUGAR_TRANSITIONS[msg_type], f"{msg_type}.accepted")

    return _error("UNKNOWN_MESSAGE_TYPE", f"unknown message type {msg_type!r}")
