"""Planner abstraction and the Part 1 mock implementation."""

from typing import Protocol, runtime_checkable

from app.core.context import RequestContext
from app.core.models import FirdayRequest, Plan, PlanStep


@runtime_checkable
class Planner(Protocol):
    """Turns a request into a plan. Real reasoning arrives in a later part."""

    name: str

    async def plan(self, request: FirdayRequest, context: RequestContext) -> Plan: ...


class MockPlanner:
    """Returns a canned, deterministic plan. No reasoning, no LLM calls."""

    name = "mock"

    async def plan(self, request: FirdayRequest, context: RequestContext) -> Plan:
        return Plan(
            planner_name=self.name,
            summary=f"[mock] Acknowledged: {request.input}",
            steps=[
                PlanStep(
                    tool_name="echo",
                    arguments={"message": request.input},
                    rationale="Mock planner always routes to the echo demo tool.",
                )
            ],
        )
