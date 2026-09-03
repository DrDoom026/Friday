"""Gmail tools (PART 13): the Tool Framework surface over the Gmail adapter.

The adapter (:mod:`app.comm.gmail`) only knows how to talk to Gmail's API.
Whether a call is allowed to happen at all is decided the same way as every
other FIRDAY tool - by ``BaseTool.execute`` calling the Security Engine
before ``run`` ever executes. Sending is declared ``requires_confirmation``,
so it is authorized as ``REQUIRE_CONFIRMATION`` and blocked automatically
(no confirmation channel exists yet, per PART 7/9/10) - the same behavior
every other confirmable tool gets, not a Gmail-specific bypass.
"""

from pydantic import BaseModel, Field

from app.comm.gmail.adapter import GmailAdapter
from app.comm.gmail.client import GmailClient
from app.comm.gmail.errors import GmailAPIError, GmailConfigurationError
from app.comm.models import OutboundMessage
from app.config import settings
from app.core.context import ToolExecutionContext
from app.core.errors import ToolExecutionError
from app.core.registry import register_tool
from app.core.tools import BaseTool, SideEffect, ToolPermissions

READ_PERMISSIONS = ToolPermissions(
    side_effect=SideEffect.READ,
    scopes=("comm.gmail.read",),
    network_access=True,
)

SEND_PERMISSIONS = ToolPermissions(
    side_effect=SideEffect.WRITE,
    scopes=("comm.gmail.send",),
    requires_confirmation=True,
    network_access=True,
)


def build_adapter() -> GmailAdapter:
    """One adapter per call - the client is stateless besides a cached token."""
    client = GmailClient(
        settings.gmail_client_id,
        settings.gmail_client_secret,
        settings.gmail_refresh_token,
        timeout_seconds=settings.gmail_request_timeout_seconds,
    )
    return GmailAdapter(client)


def _raise_as_tool_error(name: str, exc: Exception) -> None:
    raise ToolExecutionError(name, str(exc)) from exc


class GmailListInput(BaseModel):
    query: str = Field(default="", max_length=200, description="Gmail search query, e.g. 'is:unread'.")
    max_results: int = Field(default=10, ge=1, le=50)


class GmailMessageSummary(BaseModel):
    external_id: str
    thread_id: str | None = None
    sender: str
    subject: str | None = None
    snippet: str


class GmailListOutput(BaseModel):
    messages: list[GmailMessageSummary]


@register_tool
class GmailListTool(BaseTool):
    name = "comm.gmail.list"
    description = "List recent Gmail messages - sender, subject, and snippet only, no full body."
    version = "1.0.0"
    permissions = READ_PERMISSIONS
    input_model = GmailListInput
    output_model = GmailListOutput

    async def run(self, payload: GmailListInput, context: ToolExecutionContext) -> GmailListOutput:
        adapter = build_adapter()
        try:
            found = await adapter.list_messages(query=payload.query, limit=payload.max_results)
        except (GmailConfigurationError, GmailAPIError) as exc:
            _raise_as_tool_error(self.name, exc)
        return GmailListOutput(
            messages=[
                GmailMessageSummary(
                    external_id=m.external_id,
                    thread_id=m.thread_id,
                    sender=m.sender,
                    subject=m.subject,
                    snippet=m.body,
                )
                for m in found
            ]
        )


class GmailReadInput(BaseModel):
    message_id: str = Field(..., min_length=1)


class GmailReadOutput(BaseModel):
    external_id: str
    thread_id: str | None = None
    sender: str
    subject: str | None = None
    body: str


@register_tool
class GmailReadTool(BaseTool):
    name = "comm.gmail.read"
    description = "Read one Gmail message's sender, subject, and body by its id."
    version = "1.0.0"
    permissions = READ_PERMISSIONS
    input_model = GmailReadInput
    output_model = GmailReadOutput

    async def run(self, payload: GmailReadInput, context: ToolExecutionContext) -> GmailReadOutput:
        adapter = build_adapter()
        try:
            message = await adapter.get_message(payload.message_id)
        except (GmailConfigurationError, GmailAPIError) as exc:
            _raise_as_tool_error(self.name, exc)
        return GmailReadOutput(
            external_id=message.external_id,
            thread_id=message.thread_id,
            sender=message.sender,
            subject=message.subject,
            body=message.body,
        )


class GmailSendInput(BaseModel):
    to: str = Field(..., min_length=3)
    subject: str = Field(default="")
    body: str = Field(..., min_length=1)
    in_reply_to: str | None = Field(default=None, description="Gmail threadId to reply within.")


class GmailSendOutput(BaseModel):
    sent: bool
    to: str


@register_tool
class GmailSendTool(BaseTool):
    """Send or reply to a Gmail message.

    Declared ``requires_confirmation`` - the Security Engine blocks this
    before ``run`` is ever reached, so ``run`` only executes once PART 10/11
    implements a real confirmation channel. It is not "auto-send" today.
    """

    name = "comm.gmail.send"
    description = "Send or reply to a Gmail message. Requires confirmation before it runs."
    version = "1.0.0"
    permissions = SEND_PERMISSIONS
    input_model = GmailSendInput
    output_model = GmailSendOutput

    async def run(self, payload: GmailSendInput, context: ToolExecutionContext) -> GmailSendOutput:
        adapter = build_adapter()
        message = OutboundMessage(
            platform="gmail",
            recipient=payload.to,
            subject=payload.subject,
            body=payload.body,
            in_reply_to=payload.in_reply_to,
        )
        try:
            await adapter.send(message)
        except (GmailConfigurationError, GmailAPIError) as exc:
            _raise_as_tool_error(self.name, exc)
        return GmailSendOutput(sent=True, to=payload.to)
