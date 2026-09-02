"""FIRDAY Core: the orchestration service.

Request -> Core -> planner -> tool resolution -> (future) execution -> response.
"""

from app.core.context import RequestContext
from app.core.errors import ToolValidationError
from app.core.models import ExecutionStatus, FirdayRequest, FirdayResponse, Plan, ToolResult
from app.core.planner import Planner
from app.core.registry import ToolRegistry, build_default_registry
from app.memory.service import MemoryService


class Core:
    """Receives a request, invokes the planner, returns a response."""

    def __init__(
        self,
        planner: Planner,
        registry: ToolRegistry | None = None,
        memory: MemoryService | None = None,
    ) -> None:
        self._planner = planner
        self._registry = build_default_registry() if registry is None else registry
        # PART 8: available to the orchestrator; not yet handed to the planner
        # (MockPlanner takes no memory argument - that wiring is PART 9).
        self._memory = MemoryService() if memory is None else memory

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def memory(self) -> MemoryService:
        return self._memory

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

        results = self._resolve_steps(plan, context)

        response = FirdayResponse(
            request_id=context.request_id,
            output=plan.summary,
            plan=plan,
            results=results,
        )
        log.info("response returned (elapsed_ms=%.2f)", context.elapsed_ms())
        return response

    def _resolve_steps(self, plan: Plan, context: RequestContext) -> list[ToolResult]:
        """Resolve each planned step against the registry without running it.

        Part 2 goes as far as "the tool exists and these arguments fit its
        schema", then stops at NOT_EXECUTED. Running the tool is a later part.
        """
        log = context.logger("firday.core")
        results: list[ToolResult] = []

        for step in plan.steps:
            tool = self._registry.try_get(step.tool_name)
            if tool is None:
                log.warning("planned tool is not registered (tool=%s)", step.tool_name)
                results.append(
                    ToolResult(
                        tool_name=step.tool_name,
                        status=ExecutionStatus.ERROR,
                        error=f"no tool registered under name {step.tool_name!r}",
                    )
                )
                continue

            try:
                tool.validate_input(step.arguments)
            except ToolValidationError as exc:
                log.warning("planned arguments rejected (tool=%s): %s", step.tool_name, exc)
                results.append(
                    ToolResult(
                        tool_name=step.tool_name,
                        status=ExecutionStatus.ERROR,
                        error=str(exc),
                    )
                )
                continue

            log.info("planned step resolved (tool=%s, version=%s)", tool.name, tool.version)
            results.append(
                ToolResult(tool_name=step.tool_name, status=ExecutionStatus.NOT_EXECUTED)
            )

        return results
