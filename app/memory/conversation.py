"""Temporary conversation context: in-memory only, never persisted.

Scoped to a single request/session. Nothing writes it to the vault; it is
meant to be created when a session starts and dropped when it ends.
"""

from dataclasses import dataclass, field


@dataclass
class ConversationContext:
    """The messages exchanged so far in one session. Gone when the process forgets it."""

    session_id: str
    _messages: list[dict[str, str]] = field(default_factory=list)

    def add_message(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})

    @property
    def messages(self) -> tuple[dict[str, str], ...]:
        return tuple(self._messages)

    def clear(self) -> None:
        self._messages.clear()
