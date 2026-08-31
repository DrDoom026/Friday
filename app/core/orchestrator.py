"""FIRDAY Core: the orchestration service.

Request -> Core -> planner -> (future) tool execution -> result -> response.
"""

from app.core.context import RequestContext
from app.core.models import ExecutionStatus, FirdayRequest, FirdayResponse, Plan, ToolResult
from app.core.planner import Planner


class Core:
    """Receives a request, invokes the planner, returns a response."""

    def __init__(self, planner: Planner) -> None:
        self._planner = planner

    async def handle(self, request: FirdayRequest, context: RequestContext) -> FirdayResponse:
        log = context.logger("firday.core")
        log.info("request entering core (source=%s)", context.source)

        log.info("planner invoked (planner=%s)", self._planner.name)
        try:
            plan = await self._planner.plan(request, context)
        except Exception:
            # Centralized error handling in app.main turns this into a 500.
            log.exception("planner failed (planner=%s)", self._planner.name)
            raise
        log.info("planner returned plan (steps=%d)", len(plan.steps))

        results = self._pending_results(plan)

        response = FirdayResponse(
            request_id=context.request_id,
            output=plan.summary,
            plan=plan,
            results=results,
        )
        log.info("response returned (elapsed_ms=%.2f)", context.elapsed_ms())
        return response

    @staticmethod
    def _pending_results(plan: Plan) -> list[ToolResult]:
        """Placeholder results for planned steps. Part 2 replaces this with real execution."""
        return [
            ToolResult(
                tool_name=step.tool_name,
                status=ExecutionStatus.NOT_EXECUTED,
                output=None,
                error=None,
            )
            for step in plan.steps
        ]
