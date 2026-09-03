"""FIRDAY Core: the orchestration service.

Request -> Core -> planner -> tool resolution -> (future) execution -> response.
"""

from app.core.context import RequestContext, ToolExecutionContext
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
    def planner(self) -> Planner:
        return self._planner

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def memory(self) -> MemoryService:
        return self._memory

    async def handle(
        self, request: FirdayRequest, context: RequestContext, *, execute: bool = False
    ) -> FirdayResponse:
        """Run one request through the planner and, optionally, real tool execution.

        ``execute=False`` (the default) preserves the Part 1/2 contract: steps
        are resolved against the registry but never run (``NOT_EXECUTED``).
        ``execute=True`` is PART 9's real flow - each step actually runs
        through :meth:`Tool.execute`, which is where the Security Engine
        authorizes (or blocks) it. A planner that also defines ``finalize``
        (e.g. :class:`app.llm.planner.LLMPlanner`) gets a chance to turn the
        results into the final response text; any other planner keeps using
        its plan summary as the response output.
        """
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

        results = await self._resolve_steps(plan, context, execute=execute)

        output = plan.summary
        finalize = getattr(self._planner, "finalize", None)
        if execute and results and callable(finalize):
            try:
                output = await finalize(results, context)
            except Exception:
                log.exception("planner finalize failed; falling back to plan summary")

        response = FirdayResponse(
            request_id=context.request_id,
            output=output,
            plan=plan,
            results=results,
        )
        log.info("response returned (elapsed_ms=%.2f)", context.elapsed_ms())
        return response

    async def execute_tool(
        self, tool_name: str, arguments: dict, context: RequestContext
    ) -> ToolResult:
        """Run one named tool directly, outside the planner.

        Used by API routes (PART 10) that expose a single known tool - e.g.
        filesystem operations - without going through planning. Still routes
        through :meth:`Tool.execute`, so the Security Engine authorizes it
        exactly as it would a planned step.
        """
        tool = self._registry.try_get(tool_name)
        if tool is None:
            return ToolResult(
                tool_name=tool_name,
                status=ExecutionStatus.ERROR,
                error=f"no tool registered under name {tool_name!r}",
            )
        return await tool.execute(arguments, ToolExecutionContext.for_tool(context, tool.name))

    async def _resolve_steps(
        self, plan: Plan, context: RequestContext, *, execute: bool
    ) -> list[ToolResult]:
        """Resolve each planned step against the registry.

        Without ``execute``, this goes as far as "the tool exists and these
        arguments fit its schema", then stops at ``NOT_EXECUTED`` (Part 2).
        With ``execute``, each step actually runs through
        :meth:`Tool.execute`, which authorizes it via the Security Engine
        before doing anything (Part 7/9) - DENY and REQUIRE_CONFIRMATION are
        never executed.
        """
        log = context.logger("firday.core")
        results: list[ToolResult] = []

        for step in plan.steps:
            if execute:
                results.append(await self.execute_tool(step.tool_name, step.arguments, context))
                continue

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
