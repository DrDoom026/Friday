"""PART 13: Gmail tools go through the same Security Engine gate as every
other tool - reading is allowed by default, sending requires confirmation
and is blocked (no confirmation channel exists yet), and an untrusted
device is denied non-read access. No real Gmail credentials are used;
``comm.gmail.list``/``read`` hit a mocked transport, and ``comm.gmail.send``
never reaches the network at all because Security Engine blocks it first.
"""

import asyncio

import httpx
import pytest

from app.core.context import RequestContext, ToolExecutionContext
from app.core.models import ExecutionStatus, ToolResult
from app.core.registry import build_default_registry
from app.devices.models import Device, TrustState
from app.security.engine import SecurityEngine
from app.security.models import SecurityDecision
import app.tools.communication.gmail as gmail_tools


def run(coro):
    return asyncio.run(coro)


def context_for(tool_name: str) -> ToolExecutionContext:
    return ToolExecutionContext.for_tool(RequestContext.create(request_id="corr-gmail"), tool_name)


def test_gmail_tools_are_registered():
    registry = build_default_registry()
    for name in ("comm.gmail.list", "comm.gmail.read", "comm.gmail.send"):
        assert name in registry


def test_send_requires_confirmation_and_is_blocked_by_default():
    """No confirmation channel exists yet - send must never silently execute."""
    registry = build_default_registry()
    tool = registry.get("comm.gmail.send")
    result = run(
        tool.execute(
            {"to": "bob@example.com", "body": "hi"}, context_for("comm.gmail.send")
        )
    )
    assert result.status == ExecutionStatus.ERROR
    assert "confirmation" in (result.error or "").lower()


def test_send_never_reaches_the_network_when_blocked(monkeypatch):
    """The block happens before ``run`` - the adapter/client are never built."""
    registry = build_default_registry()
    tool = registry.get("comm.gmail.send")

    def fail_if_called():
        raise AssertionError("build_adapter should not be called for a blocked tool")

    monkeypatch.setattr(gmail_tools, "build_adapter", fail_if_called)
    result = run(
        tool.execute(
            {"to": "bob@example.com", "body": "hi"}, context_for("comm.gmail.send")
        )
    )
    assert result.status == ExecutionStatus.ERROR


def test_untrusted_device_is_denied_read_write_alike_for_gmail_send():
    engine = SecurityEngine()
    tool = build_default_registry().get("comm.gmail.send")
    device = Device(name="untrusted-box", trust=TrustState.UNTRUSTED)
    evaluation = engine.authorize(tool, {"to": "x@example.com", "body": "hi"}, context_for(tool.name), device=device)
    assert evaluation.decision == SecurityDecision.DENY


def test_untrusted_device_may_still_read_gmail():
    engine = SecurityEngine()
    tool = build_default_registry().get("comm.gmail.list")
    device = Device(name="untrusted-box", trust=TrustState.UNTRUSTED)
    evaluation = engine.authorize(tool, {}, context_for(tool.name), device=device)
    assert evaluation.decision == SecurityDecision.ALLOW


def test_list_runs_through_security_engine_and_the_mocked_gmail_api(monkeypatch):
    def fake_build_adapter():
        from app.comm.gmail.adapter import GmailAdapter
        from app.comm.gmail.client import GmailClient

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            if request.url.path == "/gmail/v1/users/me/messages":
                return httpx.Response(200, json={"messages": [{"id": "m1"}]})
            return httpx.Response(
                200,
                json={
                    "id": "m1",
                    "threadId": "t1",
                    "snippet": "hi there",
                    "payload": {"headers": [{"name": "From", "value": "a@example.com"}]},
                },
            )

        client = GmailClient("id", "secret", "refresh", transport=httpx.MockTransport(handler))
        return GmailAdapter(client)

    monkeypatch.setattr(gmail_tools, "build_adapter", fake_build_adapter)

    registry = build_default_registry()
    tool = registry.get("comm.gmail.list")
    result = run(tool.execute({}, context_for(tool.name)))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.output["messages"][0]["sender"] == "a@example.com"


def test_gmail_read_output_is_redacted_before_reaching_the_cloud_llm():
    """Same generic Part 9 output filter every tool gets - proven here against
    a Gmail-shaped result, since an email body is exactly the kind of content
    that must never reach a cloud model unredacted."""
    from app.llm.planner import LLMPlanner

    tool_result = ToolResult(
        tool_name="comm.gmail.read",
        status=ExecutionStatus.SUCCESS,
        output={
            "external_id": "m1",
            "sender": "alice@example.com",
            "subject": "creds",
            "body": "here is my password=hunter2, also path /home/sherlock/.ssh/id_rsa",
        },
    )
    filtered = LLMPlanner._filter_result(tool_result)
    assert "hunter2" not in filtered["output"]
    assert "/home/sherlock" not in filtered["output"]


def test_missing_credentials_produce_a_clean_error_not_a_crash():
    registry = build_default_registry()
    tool = registry.get("comm.gmail.list")
    # Real settings in the test environment have no Gmail credentials configured.
    result = run(tool.execute({}, context_for(tool.name)))
    assert result.status == ExecutionStatus.ERROR
    assert "not configured" in (result.error or "").lower()
