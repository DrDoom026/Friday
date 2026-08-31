"""Unit tests for the Part 1 orchestration flow (mock planner, no tools)."""

import asyncio
import logging

import pytest

from app.core.context import RequestContext
from app.core.models import ExecutionStatus, FirdayRequest, Plan
from app.core.orchestrator import Core
from app.core.planner import MockPlanner, Planner
from app.core.tools import Tool


def run(coro):
    return asyncio.run(coro)


# --- context / correlation IDs ---------------------------------------------


def test_context_generates_request_id_when_none_supplied():
    context = RequestContext.create(source="test")
    assert context.request_id
    assert context.source == "test"


def test_context_generates_unique_ids_per_request():
    assert RequestContext.create().request_id != RequestContext.create().request_id


def test_context_reuses_supplied_request_id():
    context = RequestContext.create(request_id="caller-abc")
    assert context.request_id == "caller-abc"


def test_context_falls_back_when_supplied_id_is_blank():
    context = RequestContext.create(request_id="   ")
    assert context.request_id.strip()
    assert context.request_id != "   "


def test_context_truncates_overlong_supplied_id():
    context = RequestContext.create(request_id="x" * 500)
    assert len(context.request_id) == 128


def test_context_logger_stamps_request_id(caplog):
    context = RequestContext.create(request_id="corr-1")
    with caplog.at_level(logging.INFO, logger="firday.test"):
        context.logger("firday.test").info("hello")
    assert [r.request_id for r in caplog.records] == ["corr-1"]


# --- mock planner ----------------------------------------------------------


def test_mock_planner_satisfies_planner_protocol():
    assert isinstance(MockPlanner(), Planner)


def test_mock_planner_returns_canned_plan():
    plan = run(MockPlanner().plan(FirdayRequest(input="ping"), RequestContext.create()))
    assert isinstance(plan, Plan)
    assert plan.planner_name == "mock"
    assert "ping" in plan.summary
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_name == "noop"


def test_mock_planner_is_deterministic():
    request = FirdayRequest(input="same input")
    first = run(MockPlanner().plan(request, RequestContext.create()))
    second = run(MockPlanner().plan(request, RequestContext.create()))
    assert first == second


# --- Core ------------------------------------------------------------------


def test_core_returns_response_carrying_request_id():
    context = RequestContext.create(request_id="corr-2")
    response = run(Core(MockPlanner()).handle(FirdayRequest(input="hi"), context))
    assert response.request_id == "corr-2"
    assert response.output == "[mock] Acknowledged: hi"


def test_core_emits_not_executed_result_per_planned_step():
    response = run(Core(MockPlanner()).handle(FirdayRequest(input="hi"), RequestContext.create()))
    assert len(response.results) == len(response.plan.steps)
    assert all(r.status is ExecutionStatus.NOT_EXECUTED for r in response.results)
    assert all(r.output is None and r.error is None for r in response.results)


def test_core_uses_the_injected_planner():
    class StubPlanner:
        name = "stub"

        async def plan(self, request, context):
            return Plan(planner_name=self.name, summary="stubbed", steps=[])

    response = run(Core(StubPlanner()).handle(FirdayRequest(input="hi"), RequestContext.create()))
    assert response.plan.planner_name == "stub"
    assert response.output == "stubbed"
    assert response.results == []


def test_core_logs_lifecycle_tagged_with_request_id(caplog):
    context = RequestContext.create(request_id="corr-3")
    with caplog.at_level(logging.INFO, logger="firday.core"):
        run(Core(MockPlanner()).handle(FirdayRequest(input="hi"), context))

    messages = [r.getMessage() for r in caplog.records]
    assert any("request entering core" in m for m in messages)
    assert any("planner invoked" in m for m in messages)
    assert any("response returned" in m for m in messages)
    assert all(r.request_id == "corr-3" for r in caplog.records)


def test_core_logs_and_reraises_planner_failure(caplog):
    class BrokenPlanner:
        name = "broken"

        async def plan(self, request, context):
            raise RuntimeError("planner exploded")

    context = RequestContext.create(request_id="corr-4")
    with caplog.at_level(logging.ERROR, logger="firday.core"):
        with pytest.raises(RuntimeError, match="planner exploded"):
            run(Core(BrokenPlanner()).handle(FirdayRequest(input="hi"), context))

    assert any("planner failed" in r.getMessage() for r in caplog.records)
    assert all(r.request_id == "corr-4" for r in caplog.records)


# --- tool abstraction (interface only in Part 1) ---------------------------


def test_tool_protocol_is_satisfied_by_a_conforming_object():
    class ConformingTool:
        name = "noop"
        description = "does nothing"

        async def execute(self, arguments, context):  # pragma: no cover - not run in Part 1
            raise NotImplementedError

    assert isinstance(ConformingTool(), Tool)


def test_tool_protocol_rejects_non_conforming_object():
    class NotATool:
        pass

    assert not isinstance(NotATool(), Tool)
