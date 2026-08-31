"""Core <-> registry wiring, and the /tools discovery endpoint."""

from fastapi.testclient import TestClient

from app.core.context import RequestContext
from app.core.models import ExecutionStatus, FirdayRequest, Plan, PlanStep
from app.core.orchestrator import Core
from app.core.planner import MockPlanner
from app.core.registry import ToolRegistry
from app.main import app
from app.tools.echo import EchoTool
from tests.test_core import run

client = TestClient(app, raise_server_exceptions=False)


class FixedPlanner:
    """Planner that emits whatever steps the test hands it."""

    name = "fixed"

    def __init__(self, *steps: PlanStep) -> None:
        self._steps = list(steps)

    async def plan(self, request, context) -> Plan:
        return Plan(planner_name=self.name, summary="fixed", steps=self._steps)


def test_core_defaults_to_the_discovered_registry():
    assert "echo" in Core(MockPlanner()).registry


def test_core_resolves_a_planned_step_against_the_registry():
    registry = ToolRegistry()
    registry.register(EchoTool())
    core = Core(MockPlanner(), registry=registry)

    response = run(core.handle(FirdayRequest(input="hello"), RequestContext.create()))
    [result] = response.results
    assert result.tool_name == "echo"
    assert result.status is ExecutionStatus.NOT_EXECUTED
    assert result.error is None


def test_core_flags_a_step_naming_an_unregistered_tool():
    core = Core(FixedPlanner(PlanStep(tool_name="ghost")), registry=ToolRegistry())

    [result] = run(
        core.handle(FirdayRequest(input="x"), RequestContext.create())
    ).results
    assert result.status is ExecutionStatus.ERROR
    assert "ghost" in result.error


def test_core_flags_a_step_whose_arguments_fail_validation():
    registry = ToolRegistry()
    registry.register(EchoTool())
    core = Core(FixedPlanner(PlanStep(tool_name="echo", arguments={})), registry=registry)

    [result] = run(
        core.handle(FirdayRequest(input="x"), RequestContext.create())
    ).results
    assert result.status is ExecutionStatus.ERROR
    assert "invalid arguments" in result.error


def test_core_does_not_execute_the_tool_in_part_2():
    """Resolution stops at NOT_EXECUTED - no tool output leaks into the response."""
    response = run(Core(MockPlanner()).handle(FirdayRequest(input="hi"), RequestContext.create()))
    assert all(r.status is ExecutionStatus.NOT_EXECUTED for r in response.results)
    assert all(r.output is None for r in response.results)


# --- /tools discovery endpoint ---------------------------------------------


def test_tools_endpoint_lists_the_registered_tool():
    response = client.get("/tools")
    assert response.status_code == 200

    body = response.json()
    assert [t["name"] for t in body] == ["echo"]

    echo = body[0]
    assert echo["version"] == "1.0.0"
    assert echo["description"]
    assert echo["permissions"]["side_effect"] == "none"
    assert echo["permissions"]["network_access"] is False
    assert "message" in echo["input_schema"]["properties"]
    assert "length" in echo["output_schema"]["properties"]


def test_request_endpoint_plan_references_the_registered_tool():
    body = client.post("/request", json={"input": "hello"}).json()
    step = body["plan"]["steps"][0]
    assert step["tool_name"] in [t["name"] for t in client.get("/tools").json()]
    assert step["arguments"] == {"message": "hello"}
    assert body["results"][0]["status"] == "not_executed"


def test_health_still_works_alongside_the_tool_framework():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
