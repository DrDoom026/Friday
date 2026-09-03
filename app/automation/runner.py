"""PART 15 task runner: the automation engine's orchestration layer.

Pipeline, exactly as specified:

    Trigger -> Condition -> Task execution -> Security Engine ->
    Tool Framework -> Named Tool -> Verification/result ->
    Execution history -> Structured notification/logging

This module never talks to a tool directly and never re-implements
authorization. It calls the *same* :func:`app.security.engine.get_security_engine`
used everywhere else to classify the decision for history/logging, and then
runs the tool through the *same* :meth:`app.core.orchestrator.Core.execute_tool`
used by the planner and the ``/files`` route - which authorizes again itself
inside :meth:`~app.core.tools.BaseTool.execute`. There is no second, weaker
authorization path: a DENY or REQUIRE_CONFIRMATION decision here is the real
decision, not a preview of one the tool could still bypass.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import uuid4

from app.automation.models import AutomationTask, ExecutionOutcome, ExecutionRecord
from app.automation.service import AutomationService
from app.core.context import RequestContext, ToolExecutionContext
from app.core.models import ExecutionStatus
from app.core.orchestrator import Core
from app.devices.registry import DeviceRegistry
from app.llm.privacy import redact
from app.security.engine import get_security_engine
from app.security.models import SecurityDecision

logger = logging.getLogger("firday.automation.runner")

#: Bounded length for result/error text folded into execution history/logs.
_SUMMARY_MAX_CHARS = 500


def _summarize(text: str | None) -> str | None:
    """Redact then truncate - history/logs must never hold raw secrets."""
    if not text:
        return text
    return redact(text)[:_SUMMARY_MAX_CHARS]


class TaskRunner:
    """Determines which enabled tasks are due and executes them."""

    def __init__(
        self,
        core: Core,
        service: AutomationService,
        *,
        devices: DeviceRegistry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._core = core
        self._service = service
        self._devices = devices
        self._clock = clock
        self._sleep = sleep
        self._active: dict[str, "asyncio.Task"] = {}

    # --- scheduling ----------------------------------------------------

    async def run_due_tasks(self, now: datetime | None = None) -> list[ExecutionRecord]:
        """Run every enabled task whose trigger is currently due."""
        moment = now if now is not None else self._clock()
        records = []
        for task in self._service.list():
            if not task.enabled:
                continue
            if not task.trigger.is_due(now=moment, last_run_at=task.last_run_at):
                continue
            records.append(await self.execute(task, now=moment, triggered_by="schedule"))
        return records

    async def run_now(self, task_id: str) -> ExecutionRecord:
        """Manual execution through the API. NOT a security bypass.

        Still evaluates the task's condition and still goes through the same
        Security Engine -> Tool Framework path as a scheduled firing - the
        only difference is what caused it, which is recorded on the history
        entry (``triggered_by="manual"``).
        """
        task = self._service.get(task_id)
        return await self.execute(task, now=self._clock(), triggered_by="manual")

    def cancel(self, execution_id: str) -> bool:
        """Cancel a currently-running execution, if one is in flight."""
        running = self._active.get(execution_id)
        if running is None or running.done():
            return False
        running.cancel()
        return True

    # --- execution -------------------------------------------------------

    async def execute(
        self, task: AutomationTask, *, now: datetime, triggered_by: str
    ) -> ExecutionRecord:
        execution_id = uuid4().hex
        request_context = RequestContext.create(source="automation")
        log = request_context.logger("firday.automation.runner")
        log.info(
            "automation task triggered (task_id=%s, tool=%s, triggered_by=%s)",
            task.task_id,
            task.tool_name,
            triggered_by,
        )

        if triggered_by == "schedule":
            self._service.set_last_run(task.task_id, now)

        device = self._devices.try_get(task.condition.device_id) if (
            self._devices is not None and task.condition is not None and task.condition.kind == "device_trust"
        ) else None

        if task.condition is not None:
            passed, reason = task.condition.evaluate(now=now, device=device)
            if not passed:
                log.info(
                    "automation condition failed (task_id=%s, reason=%s)", task.task_id, reason
                )
                record = ExecutionRecord(
                    execution_id=execution_id,
                    request_id=request_context.request_id,
                    task_id=task.task_id,
                    tool_name=task.tool_name,
                    triggered_by=triggered_by,
                    started_at=now,
                    finished_at=self._clock(),
                    outcome=ExecutionOutcome.CONDITION_FAILED,
                    error_summary=_summarize(reason),
                )
                self._service.append_history(task.task_id, record)
                return record
            log.info("automation condition passed (task_id=%s, reason=%s)", task.task_id, reason)

        return await self._run_with_retries(task, request_context, execution_id, now, triggered_by, log)

    async def _run_with_retries(
        self,
        task: AutomationTask,
        request_context: RequestContext,
        execution_id: str,
        started_at: datetime,
        triggered_by: str,
        log: logging.LoggerAdapter,
    ) -> ExecutionRecord:
        max_attempts = 1 + task.retry_count
        outcome = ExecutionOutcome.ERROR
        security_decision: str | None = None
        result_summary: str | None = None
        error_summary: str | None = None
        attempt = 0

        tool = self._core.registry.try_get(task.tool_name)
        if tool is None:
            error_summary = f"no tool registered under name {task.tool_name!r}"
            log.warning("automation task references unknown tool (task_id=%s, tool=%s)", task.task_id, task.tool_name)
            return self._finalize(
                task, execution_id, request_context, triggered_by, started_at,
                ExecutionOutcome.ERROR, None, None, error_summary, attempts=0, retries=0,
            )

        while attempt < max_attempts:
            attempt += 1
            log.info(
                "automation execution started (task_id=%s, tool=%s, attempt=%d/%d)",
                task.task_id, task.tool_name, attempt, max_attempts,
            )

            exec_ctx = ToolExecutionContext.for_tool(request_context, tool.name)
            evaluation = get_security_engine().authorize(tool, task.tool_input, exec_ctx)
            security_decision = evaluation.decision.value
            log.info(
                "automation security decision (task_id=%s, tool=%s, decision=%s)",
                task.task_id, task.tool_name, security_decision,
            )

            if evaluation.decision == SecurityDecision.DENY:
                log.warning(
                    "automation execution blocked by DENY (task_id=%s, tool=%s, reason=%s)",
                    task.task_id, task.tool_name, evaluation.reason,
                )
                outcome = ExecutionOutcome.DENIED
                error_summary = _summarize(evaluation.reason)
                break

            if evaluation.decision == SecurityDecision.REQUIRE_CONFIRMATION:
                log.warning(
                    "automation execution blocked pending confirmation (task_id=%s, tool=%s)",
                    task.task_id, task.tool_name,
                )
                outcome = ExecutionOutcome.REQUIRE_CONFIRMATION
                error_summary = _summarize(evaluation.reason)
                break

            try:
                result = await self._run_tool(task, request_context, execution_id)
            except asyncio.TimeoutError:
                log.warning(
                    "automation execution timed out (task_id=%s, tool=%s, timeout=%.1fs)",
                    task.task_id, task.tool_name, task.timeout_seconds,
                )
                outcome = ExecutionOutcome.TIMEOUT
                error_summary = f"execution timed out after {task.timeout_seconds}s"
                if attempt < max_attempts:
                    log.info("automation execution retrying (task_id=%s, attempt=%d)", task.task_id, attempt + 1)
                    await self._sleep(task.retry_delay_seconds)
                    continue
                break
            except asyncio.CancelledError:
                log.warning("automation execution cancelled (task_id=%s, tool=%s)", task.task_id, task.tool_name)
                outcome = ExecutionOutcome.CANCELLED
                error_summary = "execution was cancelled"
                break

            if result.status == ExecutionStatus.SUCCESS:
                log.info("automation execution succeeded (task_id=%s, tool=%s)", task.task_id, task.tool_name)
                outcome = ExecutionOutcome.SUCCESS
                result_summary = _summarize(
                    json.dumps(result.output, default=str) if result.output is not None else None
                )
                break

            log.warning(
                "automation execution failed (task_id=%s, tool=%s, error=%s)",
                task.task_id, task.tool_name, result.error,
            )
            outcome = ExecutionOutcome.ERROR
            error_summary = _summarize(result.error)
            if attempt < max_attempts:
                log.info("automation execution retrying (task_id=%s, attempt=%d)", task.task_id, attempt + 1)
                await self._sleep(task.retry_delay_seconds)
                continue
            break

        return self._finalize(
            task, execution_id, request_context, triggered_by, started_at,
            outcome, security_decision, result_summary, error_summary,
            attempts=attempt, retries=max(attempt - 1, 0),
        )

    async def _run_tool(self, task: AutomationTask, request_context: RequestContext, execution_id: str):
        inner = asyncio.ensure_future(
            self._core.execute_tool(task.tool_name, task.tool_input, request_context)
        )
        self._active[execution_id] = inner
        try:
            return await asyncio.wait_for(asyncio.shield(inner), timeout=task.timeout_seconds)
        finally:
            self._active.pop(execution_id, None)

    def _finalize(
        self,
        task: AutomationTask,
        execution_id: str,
        request_context: RequestContext,
        triggered_by: str,
        started_at: datetime,
        outcome: ExecutionOutcome,
        security_decision: str | None,
        result_summary: str | None,
        error_summary: str | None,
        *,
        attempts: int,
        retries: int,
    ) -> ExecutionRecord:
        record = ExecutionRecord(
            execution_id=execution_id,
            request_id=request_context.request_id,
            task_id=task.task_id,
            tool_name=task.tool_name,
            triggered_by=triggered_by,
            started_at=started_at,
            finished_at=self._clock(),
            outcome=outcome,
            security_decision=security_decision,
            attempts=max(attempts, 1),
            retries=retries,
            result_summary=result_summary,
            error_summary=error_summary,
        )
        self._service.append_history(task.task_id, record)
        return record
