"""PART 13: Gmail client/adapter - OAuth handling, message normalization,
outbound construction, and failure behavior. All Gmail API calls are mocked
via ``httpx.MockTransport``; no real credentials or network access needed.
"""

import asyncio
import base64
import json

import httpx
import pytest

from app.comm.gmail.adapter import GmailAdapter
from app.comm.gmail.client import GmailClient
from app.comm.gmail.errors import GmailAPIError, GmailConfigurationError
from app.comm.models import OutboundMessage


def run(coro):
    return asyncio.run(coro)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


FULL_MESSAGE = {
    "id": "msg-1",
    "threadId": "thread-1",
    "labelIds": ["INBOX", "UNREAD"],
    "snippet": "hello there",
    "payload": {
        "headers": [
            {"name": "From", "value": "alice@example.com"},
            {"name": "To", "value": "me@example.com"},
            {"name": "Subject", "value": "Hi"},
        ],
        "mimeType": "text/plain",
        "body": {"data": _b64("hello there, this is the body")},
    },
}


def _handler(responses):
    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        return responses[key](request)

    return handler


def _token_ok(_request):
    return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 3600})


# --- GmailClient: configuration and OAuth ----------------------------------


def test_client_reports_unconfigured_without_all_three_values():
    assert not GmailClient(None, "secret", "refresh").configured
    assert not GmailClient("id", None, "refresh").configured
    assert not GmailClient("id", "secret", None).configured
    assert GmailClient("id", "secret", "refresh").configured


def test_unconfigured_client_raises_configuration_error_before_any_request():
    client = GmailClient(None, None, None)
    with pytest.raises(GmailConfigurationError):
        run(client.list_messages())


def test_configured_client_refreshes_a_token_and_calls_the_api():
    transport = httpx.MockTransport(
        _handler(
            {
                "POST /token": _token_ok,
                "GET /gmail/v1/users/me/messages": lambda r: httpx.Response(
                    200, json={"messages": [{"id": "msg-1", "threadId": "thread-1"}]}
                ),
            }
        )
    )
    client = GmailClient("id", "secret", "refresh", transport=transport)
    refs = run(client.list_messages())
    assert refs == [{"id": "msg-1", "threadId": "thread-1"}]


def test_token_refresh_failure_raises_gmail_api_error():
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "invalid_grant"}))
    client = GmailClient("id", "secret", "bad-refresh", transport=transport)
    with pytest.raises(GmailAPIError):
        run(client.list_messages())


def test_api_failure_after_successful_auth_raises_gmail_api_error():
    transport = httpx.MockTransport(
        _handler(
            {
                "POST /token": _token_ok,
                "GET /gmail/v1/users/me/messages": lambda r: httpx.Response(500, text="boom"),
            }
        )
    )
    client = GmailClient("id", "secret", "refresh", transport=transport)
    with pytest.raises(GmailAPIError):
        run(client.list_messages())


def test_access_token_is_never_exposed_by_the_client(caplog):
    """The client is the only thing that ever sees the token - it must not leak it."""
    transport = httpx.MockTransport(
        _handler(
            {
                "POST /token": _token_ok,
                "GET /gmail/v1/users/me/messages": lambda r: httpx.Response(
                    200, json={"messages": []}
                ),
            }
        )
    )
    client = GmailClient("id", "secret", "a-refresh-token-value", transport=transport)
    with caplog.at_level("DEBUG"):
        run(client.list_messages())
    log_text = caplog.text
    assert "fake-token" not in log_text
    assert "a-refresh-token-value" not in log_text
    assert "secret" not in log_text


# --- GmailAdapter: normalization and outbound construction ------------------


def _adapter_with(handlers) -> GmailAdapter:
    transport = httpx.MockTransport(_handler(handlers))
    return GmailAdapter(GmailClient("id", "secret", "refresh", transport=transport))


def test_get_message_normalizes_headers_and_decodes_the_body():
    adapter = _adapter_with(
        {
            "POST /token": _token_ok,
            "GET /gmail/v1/users/me/messages/msg-1": lambda r: httpx.Response(
                200, json=FULL_MESSAGE
            ),
        }
    )
    message = run(adapter.get_message("msg-1"))
    assert message.platform == "gmail"
    assert message.external_id == "msg-1"
    assert message.thread_id == "thread-1"
    assert message.sender == "alice@example.com"
    assert message.subject == "Hi"
    assert message.body == "hello there, this is the body"


def test_list_messages_uses_snippet_not_full_body():
    adapter = _adapter_with(
        {
            "POST /token": _token_ok,
            "GET /gmail/v1/users/me/messages": lambda r: httpx.Response(
                200, json={"messages": [{"id": "msg-1", "threadId": "thread-1"}]}
            ),
            "GET /gmail/v1/users/me/messages/msg-1": lambda r: httpx.Response(
                200, json=FULL_MESSAGE
            ),
        }
    )
    messages = run(adapter.list_messages(limit=5))
    assert messages[0].body == "hello there"  # the snippet, not the decoded full body


def test_body_is_truncated_to_the_configured_cap():
    huge = {**FULL_MESSAGE, "payload": {**FULL_MESSAGE["payload"], "body": {"data": _b64("x" * 50)}}}
    transport = httpx.MockTransport(
        _handler(
            {
                "POST /token": _token_ok,
                "GET /gmail/v1/users/me/messages/msg-1": lambda r: httpx.Response(200, json=huge),
            }
        )
    )
    adapter = GmailAdapter(GmailClient("id", "secret", "refresh", transport=transport), max_body_chars=10)
    message = run(adapter.get_message("msg-1"))
    assert len(message.body) == 10


def test_send_builds_a_mime_message_and_posts_the_raw_base64url_payload():
    captured = {}

    def capture_send(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "sent-1"})

    adapter = _adapter_with(
        {
            "POST /token": _token_ok,
            "POST /gmail/v1/users/me/messages/send": capture_send,
        }
    )
    message = OutboundMessage(
        platform="gmail", recipient="bob@example.com", subject="Re: Hi", body="reply body",
        in_reply_to="thread-1",
    )
    run(adapter.send(message))

    assert captured["body"]["threadId"] == "thread-1"
    raw = captured["body"]["raw"]
    padded = raw + "=" * (-len(raw) % 4)
    decoded = base64.urlsafe_b64decode(padded).decode()
    assert "To: bob@example.com" in decoded
    assert "Subject: Re: Hi" in decoded
    assert "reply body" in decoded
