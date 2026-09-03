"""The communication adapter interface (PART 13).

An adapter's only job is translating between one external platform's
protocol and FIRDAY's provider-independent message shapes. It is not a
second brain:

- It never calls a FIRDAY tool directly - a Core-driven caller turns an
  ``InboundMessage`` into a :class:`~app.core.models.FirdayRequest` and runs
  it through ``Core.handle``, which is the only path to the Security Engine
  and the Tool Framework.
- It never calls an LLM (Ollama/OmniRoute) directly - that only happens
  inside Core's planner.
- Anything externally visible it does (sending a reply) is itself invoked
  from inside a registered Tool's ``run``, so it is still gated by the
  Security Engine like any other tool.

A future adapter (Telegram, SMS, ...) implements this same interface without
FIRDAY Core changing at all.
"""

from typing import Protocol, runtime_checkable

from app.comm.models import InboundMessage, OutboundMessage


@runtime_checkable
class CommunicationAdapter(Protocol):
    """What Core-driven code needs from any communication platform."""

    #: Short, stable identity - e.g. ``"gmail"``. Stamped onto every message.
    platform: str

    async def fetch_new(self, limit: int = 10) -> list[InboundMessage]:
        """Return up to ``limit`` new inbound messages, normalized."""
        ...

    async def send(self, message: OutboundMessage) -> None:
        """Deliver one outbound message through the platform."""
        ...
