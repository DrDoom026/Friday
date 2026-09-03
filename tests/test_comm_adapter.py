"""PART 13: the provider-independent communication adapter abstraction.

A future adapter (Telegram/SMS/...) just needs to satisfy
``CommunicationAdapter`` - this pins the shape without any Gmail specifics.
"""

from app.comm.adapter import CommunicationAdapter
from app.comm.models import InboundMessage, OutboundMessage


class FakeAdapter:
    """The minimal thing that satisfies the protocol."""

    platform = "fake"

    async def fetch_new(self, limit: int = 10) -> list[InboundMessage]:
        return [
            InboundMessage(
                platform=self.platform, external_id="1", sender="a@example.com", body="hi"
            )
        ]

    async def send(self, message: OutboundMessage) -> None:
        return None


def test_fake_adapter_satisfies_the_protocol():
    adapter = FakeAdapter()
    assert isinstance(adapter, CommunicationAdapter)


def test_inbound_message_requires_sender_and_body():
    message = InboundMessage(platform="fake", external_id="1", sender="a@example.com", body="hi")
    assert message.thread_id is None
    assert message.metadata == {}


def test_outbound_message_carries_reply_threading():
    message = OutboundMessage(
        platform="fake", recipient="a@example.com", body="reply", in_reply_to="thread-1"
    )
    assert message.in_reply_to == "thread-1"


async def _run(adapter: FakeAdapter) -> list[InboundMessage]:
    return await adapter.fetch_new()


def test_adapter_fetch_new_returns_normalized_messages():
    import asyncio

    messages = asyncio.run(_run(FakeAdapter()))
    assert messages[0].platform == "fake"
    assert messages[0].body == "hi"
