"""Tests for PART 9: the hybrid LLM layer.

Covers the privacy gate, provider clients (Ollama + OmniRoute), the LLM
planner's structured output, and that LLM-originated tool requests still go
through Core -> Security Engine exactly like any other plan.
"""

import asyncio
import json

import httpx
import pytest

from app.core.context import RequestContext
from app.core.models import ExecutionStatus, FirdayRequest, Plan, ToolResult
from app.core.orchestrator import Core
from app.core.registry import ToolRegistry
from app.core.tools import BaseTool, SideEffect, ToolPermissions
from app.fs.policy import FilesystemPolicy
from app.llm import privacy
from app.llm.errors import LLMProviderError
from app.llm.planner import LLMPlanner
from app.llm.providers import OllamaClient, OmniRouteClient
from app.memory.models import MemoryCategory
from app.memory.service import MemoryService
from app.security.engine import SecurityEngine, get_security_engine, set_security_engine
from app.security.policy import DefaultSecurityPolicy
from app.tools.echo import EchoTool


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_security_engine():
    """Every tool's execute() reads the process-wide engine; keep tests isolated."""
    original = get_security_engine()
    yield
    set_security_engine(original)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Retry backoff sleeps for real seconds; skip that in tests."""

    async def _instant_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)


def _mock_transport(handler):
    return httpx.MockTransport(handler)


# ===========================================================================
# Privacy / sensitivity gate (deterministic)
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "read the file at /home/sherlock/secrets.txt",
        "my api_key = sk-abcdefghijklmnopqrstuvwxyz",
        "here is AKIAABCDEFGHIJKLMNOP for AWS",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIB...",
        "check ~/.ssh/id_rsa for the key",
        "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_is_sensitive_detects_private_data(text):
    assert privacy.is_sensitive(text)


@pytest.mark.parametrize(
    "text",
    [
        "what's the weather like",
        "list files in the workspace",
        "hello there",
        "Email from Alice <alice@example.com>: Test\n\nHi there, ~cheers!",
        "Email from Bob <bob@example.com>: Re: hello\n\nSee you ~5pm today",
    ],
)
def test_is_sensitive_allows_benign_text(text):
    assert not privacy.is_sensitive(text)


def test_redact_removes_sensitive_spans_but_keeps_the_rest():
    out = privacy.redact("path is /home/sherlock/x and that's it")
    assert "/home/sherlock" not in out
    assert "[REDACTED]" in out
    assert "and that's it" in out


# ===========================================================================
# Ollama client (local, intent classification only)
# ===========================================================================


def test_ollama_client_is_configurable():
    client = OllamaClient("http://pi.local:11434", "tinyllama:1.1b")
    assert client._base_url == "http://pi.local:11434"
    assert client._model == "tinyllama:1.1b"


def test_ollama_classify_intent_returns_matched_category():
    def handler(request):
        assert request.url.path == "/api/generate"
        return httpx.Response(200, json={"response": "tool_request please"})

    client = OllamaClient("http://x", "m", transport=_mock_transport(handler))
    result = run(client.classify_intent("do something", ("tool_request", "chat")))
    assert result == "tool_request"


def test_ollama_classify_intent_fails_safe_when_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    client = OllamaClient("http://x", "m", transport=_mock_transport(handler))
    result = run(client.classify_intent("hi", ("tool_request", "chat")))
    assert result == "unknown"


# ===========================================================================
# OmniRoute client (cloud, OpenAI-compatible /v1/chat/completions only)
# ===========================================================================


def test_omniroute_complete_returns_message_content():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert "Bearer secret-key" == request.headers["authorization"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "hello back"}}]}
        )

    client = OmniRouteClient(
        "http://x", "auto", "secret-key", transport=_mock_transport(handler)
    )
    result = run(client.complete([{"role": "user", "content": "hi"}]))
    assert result == "hello back"


def test_omniroute_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = OmniRouteClient(
        "http://x", "auto", None, max_retries=2, transport=_mock_transport(handler)
    )
    result = run(client.complete([{"role": "user", "content": "hi"}]))
    assert result == "ok"
    assert calls["n"] == 3


def test_omniroute_raises_after_retries_exhausted():
    def handler(request):
        return httpx.Response(500)

    client = OmniRouteClient(
        "http://x", "auto", None, max_retries=1, transport=_mock_transport(handler)
    )
    with pytest.raises(LLMProviderError):
        run(client.complete([{"role": "user", "content": "hi"}]))


def test_omniroute_rate_limit_fails_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429)

    client = OmniRouteClient(
        "http://x", "auto", None, max_retries=5, transport=_mock_transport(handler)
    )
    with pytest.raises(LLMProviderError):
        run(client.complete([{"role": "user", "content": "hi"}]))
    assert calls["n"] == 1  # no uncontrolled retries against an exhausted rate limit


def test_omniroute_never_logs_the_api_key(caplog):
    def handler(request):
        return httpx.Response(500)

    client = OmniRouteClient(
        "http://x",
        "auto",
        "super-secret-value",
        max_retries=0,
        transport=_mock_transport(handler),
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(LLMProviderError):
            run(client.complete([{"role": "user", "content": "hi"}]))

    for record in caplog.records:
        assert "super-secret-value" not in record.getMessage()


# ===========================================================================
# LLM planner: structured plan output, privacy gate, memory, malformed responses
# ===========================================================================


class _StubCloud:
    """A cloud client stand-in that returns a canned reply or raises."""

    def __init__(self, reply: str | None = None, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.last_messages: list[dict[str, str]] | None = None
        self.call_count = 0

    async def complete(self, messages):
        self.call_count += 1
        self.last_messages = messages
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def registry():
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


def test_planner_blocks_sensitive_requests_without_calling_cloud(registry):
    cloud = _StubCloud(reply="should never be used")
    planner = LLMPlanner(cloud, registry)
    request = FirdayRequest(input="read /home/sherlock/.ssh/id_rsa for me")

    plan = run(planner.plan(request, RequestContext.create()))

    assert plan.steps == []
    assert cloud.last_messages is None


def test_planner_produces_structured_tool_step(registry):
    reply = json.dumps(
        {"tool_name": "echo", "arguments": {"message": "hi"}, "summary": "echoing"}
    )
    cloud = _StubCloud(reply=reply)
    planner = LLMPlanner(cloud, registry)

    plan = run(planner.plan(FirdayRequest(input="echo hi"), RequestContext.create()))

    assert len(plan.steps) == 1
    assert plan.steps[0].tool_name == "echo"
    assert plan.steps[0].arguments == {"message": "hi"}


def test_planner_handles_malformed_llm_response(registry):
    cloud = _StubCloud(reply="not json at all")
    planner = LLMPlanner(cloud, registry)

    plan = run(planner.plan(FirdayRequest(input="do a thing"), RequestContext.create()))

    assert plan.steps == []  # never guesses a tool/arguments from garbage


def test_system_prompt_requires_a_literal_reply_when_tool_name_is_null():
    """Regression: the model must be told ``summary`` IS the message to the
    user for a conversational turn, not a description of one (e.g. "Greet the
    user with a friendly hello." instead of "Hello! How can I help you?")."""
    from app.llm.planner import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT.lower()
    assert "tool_name is null" in prompt
    assert "exact" in prompt and ("final message" in prompt or "reply" in prompt)
    assert "written directly to them" in prompt
    assert "must not be an instruction" in prompt or "not be an instruction" in prompt
    # the exact failure mode reported from the real Pi must be named as wrong
    assert "greet the user" in prompt


def test_planner_null_tool_name_passes_the_literal_reply_through_as_summary(registry):
    """The parsing contract is unchanged by the prompt fix: whatever the model
    puts in ``summary`` for a null tool_name still becomes ``plan.summary``
    verbatim - this pins the JSON contract, not model behavior."""
    reply = json.dumps(
        {"tool_name": None, "arguments": {}, "summary": "Hello! How can I help you today?"}
    )
    cloud = _StubCloud(reply=reply)
    planner = LLMPlanner(cloud, registry)

    plan = run(planner.plan(FirdayRequest(input="hello"), RequestContext.create()))

    assert plan.steps == []
    assert plan.summary == "Hello! How can I help you today?"


def test_core_returns_the_conversational_summary_directly_without_calling_finalize(registry):
    """Pins Core's documented zero-step behavior (unchanged by this fix):
    with no tool steps/results, Core never calls ``finalize`` - the planner's
    ``summary`` (now the literal reply, per the tightened prompt) is the
    response as-is."""
    reply = json.dumps(
        {"tool_name": None, "arguments": {}, "summary": "Hello! How can I help you today?"}
    )
    cloud = _StubCloud(reply=reply)
    planner = LLMPlanner(cloud, registry)
    core = Core(planner=planner, registry=registry)

    response = run(
        core.handle(FirdayRequest(input="hello"), RequestContext.create(), execute=True)
    )

    assert response.output == "Hello! How can I help you today?"
    assert response.results == []
    assert cloud.call_count == 1  # plan() only - finalize() never invoked


def test_planner_handles_provider_failure_gracefully(registry):
    cloud = _StubCloud(error=LLMProviderError("boom"))
    planner = LLMPlanner(cloud, registry)

    plan = run(planner.plan(FirdayRequest(input="do a thing"), RequestContext.create()))

    assert plan.steps == []
    assert plan.summary


def test_planner_memory_failure_fails_safe_not_open(registry):
    class BrokenMemory:
        async def search(self, **kwargs):
            raise RuntimeError("vault unreadable")

    cloud = _StubCloud(reply=json.dumps({"tool_name": None, "arguments": {}, "summary": "ok"}))
    planner = LLMPlanner(cloud, registry, memory=BrokenMemory())

    plan = run(planner.plan(FirdayRequest(input="what do you remember"), RequestContext.create()))

    assert plan.steps == []  # broken memory never turns into unrestricted tool access
    assert cloud.last_messages is not None  # planning still proceeded, just without memory


@pytest.fixture
def vault_memory(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    policy = FilesystemPolicy([root])
    service = MemoryService(vault_root=str(root), policy=policy)
    run(
        service.store(
            MemoryCategory.PREFERENCE,
            "coffee",
            "User prefers dark roast coffee in the morning.",
        )
    )
    return service


def test_planner_retrieves_relevant_memory_into_the_prompt(registry, vault_memory):
    cloud = _StubCloud(reply=json.dumps({"tool_name": None, "arguments": {}, "summary": "ok"}))
    planner = LLMPlanner(cloud, registry, memory=vault_memory)

    run(planner.plan(FirdayRequest(input="what do I like to drink"), RequestContext.create()))

    system_message = cloud.last_messages[0]["content"]
    assert "dark roast" in system_message


def test_finalize_redacts_sensitive_tool_output_before_cloud_call(registry):
    cloud = _StubCloud(reply="done")
    planner = LLMPlanner(cloud, registry)
    results = [
        ToolResult(
            tool_name="fs.read",
            status=ExecutionStatus.SUCCESS,
            output={"content": "password=hunter2 at /home/sherlock/.env"},
        )
    ]

    run(planner.finalize(results, RequestContext.create()))

    sent = cloud.last_messages[1]["content"]
    assert "hunter2" not in sent
    assert "/home/sherlock" not in sent


def test_finalize_falls_back_gracefully_on_provider_failure(registry):
    cloud = _StubCloud(error=LLMProviderError("down"))
    planner = LLMPlanner(cloud, registry)
    results = [ToolResult(tool_name="echo", status=ExecutionStatus.SUCCESS, output={"x": 1})]

    output = run(planner.finalize(results, RequestContext.create()))

    assert "echo" in output


# ===========================================================================
# Core integration: LLM-originated tool requests still pass through
# SecurityEngine when actually executed. DENY / REQUIRE_CONFIRMATION block.
# ===========================================================================


from pydantic import BaseModel  # noqa: E402


class _NoopInput(BaseModel):
    pass


class _NoopOutput(BaseModel):
    ran: bool = True


class _ConfirmTool(BaseTool):
    """Stub tool whose permissions force REQUIRE_CONFIRMATION."""

    name = "stub.confirm"
    description = "stub"
    permissions = ToolPermissions(side_effect=SideEffect.WRITE, requires_confirmation=True)
    input_model = _NoopInput
    output_model = _NoopOutput

    async def run(self, payload, context):
        raise AssertionError("must never run: blocked before execution")


def test_llm_plan_step_still_requires_confirmation_when_executed():
    """A tool the LLM asked for that needs confirmation is never auto-run."""
    from app.core.models import PlanStep

    reg = ToolRegistry()
    reg.register(_ConfirmTool())
    plan = Plan(
        planner_name="llm",
        summary="do the risky thing",
        steps=[PlanStep(tool_name="stub.confirm")],
    )

    class OneShotPlanner:
        name = "llm"

        async def plan(self, request, context):
            return plan

    core = Core(OneShotPlanner(), registry=reg)
    response = run(
        core.handle(FirdayRequest(input="do it"), RequestContext.create(), execute=True)
    )

    assert len(response.results) == 1
    assert response.results[0].status == ExecutionStatus.ERROR
    assert "confirmation" in response.results[0].error.lower()


def test_llm_plan_step_denied_by_policy_is_never_executed():
    """A DENY decision from the Security Engine blocks execution outright."""
    from app.core.models import PlanStep

    class _WriteTool(BaseTool):
        name = "stub.write"
        description = "stub"
        permissions = ToolPermissions(side_effect=SideEffect.WRITE)
        input_model = _NoopInput
        output_model = _NoopOutput

        async def run(self, payload, context):
            raise AssertionError("must never run: denied before execution")

    reg2 = ToolRegistry()
    reg2.register(_WriteTool())
    set_security_engine(SecurityEngine(DefaultSecurityPolicy(allow_write=False)))

    plan = Plan(
        planner_name="llm", summary="write something", steps=[PlanStep(tool_name="stub.write")]
    )

    class OneShotPlanner:
        name = "llm"

        async def plan(self, request, context):
            return plan

    core = Core(OneShotPlanner(), registry=reg2)
    response = run(
        core.handle(FirdayRequest(input="do it"), RequestContext.create(), execute=True)
    )

    assert response.results[0].status == ExecutionStatus.ERROR


def test_core_execute_defaults_to_false_preserving_part1_contract():
    """Default handle() behavior (no execute kwarg) is unchanged from Part 1/2."""
    from app.core.planner import MockPlanner

    core = Core(MockPlanner())
    response = run(core.handle(FirdayRequest(input="hi"), RequestContext.create()))
    assert response.results[0].status == ExecutionStatus.NOT_EXECUTED
