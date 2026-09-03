"""Provider-independent message shapes for communication adapters (PART 13).

FIRDAY Core only ever sees these two models - never a Gmail (or future
Telegram/SMS) API payload directly.
"""

from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    """One message arriving from an external communication platform."""

    platform: str
    external_id: str
    thread_id: str | None = None
    sender: str
    recipient: str | None = None
    subject: str | None = None
    body: str
    metadata: dict[str, str] = Field(default_factory=dict)


class OutboundMessage(BaseModel):
    """One message FIRDAY sends back out through an adapter.

    ``in_reply_to`` carries whatever the adapter needs to thread the reply
    (Gmail's ``threadId``) - callers should not need to know the shape.
    """

    platform: str
    recipient: str
    body: str
    subject: str | None = None
    in_reply_to: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
