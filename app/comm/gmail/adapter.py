"""Gmail reference adapter (PART 13).

Normalizes Gmail API payloads into :class:`~app.comm.models.InboundMessage`
and builds outbound Gmail sends from :class:`~app.comm.models.OutboundMessage`
- the only Gmail-specific code in FIRDAY. Implements
:class:`~app.comm.adapter.CommunicationAdapter`; also exposes
``list_messages``/``get_message`` for the Gmail read tools, which are
provider-specific and not part of the generic adapter interface.
"""

import base64
from email.mime.text import MIMEText

from app.comm.gmail.client import GmailClient
from app.comm.models import InboundMessage, OutboundMessage

#: Cap on normalized message bodies. Keeps a single email from ever handing
#: an unbounded amount of private content to a caller (and, downstream, to
#: the Part 9 privacy filter / cloud LLM).
MAX_BODY_CHARS = 4000


def _header(payload: dict, name: str) -> str:
    for h in payload.get("payload", {}).get("headers", []) or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_text(part: dict) -> str:
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        data = part["body"]["data"]
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    for sub in part.get("parts", []) or []:
        found = _extract_text(sub)
        if found:
            return found
    return ""


class GmailAdapter:
    """Reads/sends Gmail on behalf of a Tool - never calls Core or an LLM itself."""

    platform = "gmail"

    def __init__(self, client: GmailClient, *, max_body_chars: int = MAX_BODY_CHARS) -> None:
        self._client = client
        self._max_body_chars = max_body_chars

    async def fetch_new(self, limit: int = 10) -> list[InboundMessage]:
        """Unread messages, normalized with full body - for Core-driven processing."""
        refs = await self._client.list_messages(query="is:unread", max_results=limit)
        return [await self.get_message(ref["id"]) for ref in refs]

    async def list_messages(self, *, query: str = "", limit: int = 10) -> list[InboundMessage]:
        """Lightweight listing: sender/subject/snippet only, no full body fetch."""
        refs = await self._client.list_messages(query=query, max_results=limit)
        result = []
        for ref in refs:
            payload = await self._client.get_message(ref["id"], format="metadata")
            result.append(self._normalize(payload, body_override=payload.get("snippet", "")))
        return result

    async def get_message(self, message_id: str) -> InboundMessage:
        payload = await self._client.get_message(message_id, format="full")
        return self._normalize(payload)

    async def send(self, message: OutboundMessage) -> None:
        mime = MIMEText(message.body)
        mime["To"] = message.recipient
        if message.subject:
            mime["Subject"] = message.subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        await self._client.send_message(raw, thread_id=message.in_reply_to)

    def _normalize(self, payload: dict, *, body_override: str | None = None) -> InboundMessage:
        body = body_override if body_override is not None else _extract_text(payload.get("payload", {}))
        if not body:
            body = payload.get("snippet", "")
        return InboundMessage(
            platform=self.platform,
            external_id=payload.get("id", ""),
            thread_id=payload.get("threadId"),
            sender=_header(payload, "From"),
            recipient=_header(payload, "To"),
            subject=_header(payload, "Subject"),
            body=body[: self._max_body_chars],
            metadata={"label_ids": ",".join(payload.get("labelIds", []) or [])},
        )
