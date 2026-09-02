"""Shared base classes for the PART 6 system tools.

Every system tool is an ordinary Part 2 tool: an input model, an output model,
declared permissions, one method. What this base adds is the two things no
system tool may skip - one audit record per attempt, carrying the request's
correlation ID, and the deny-stub shape that keeps every state-changing
operation inert until PART 7.

The split is the same one Part 4 drew across the filesystem. A tool that only
observes runs for real. A tool that changes something - kills a process, starts
a unit, stops a container, moves a working tree - is registered, schema
complete, and refuses, because there is nothing yet that could authorize it.
"""

import time
from abc import abstractmethod
from typing import Any, ClassVar, Mapping

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.errors import ToolExecutionError
from app.core.models import ExecutionStatus, ToolResult
from app.core.tools import BaseTool, SideEffect, ToolPermissions
from app.fs.errors import FilesystemPolicyError
from app.fs.policy import FilesystemPolicy, get_default_policy
from app.system.audit import record_attempt
from app.system.errors import SystemOperationNotAuthorizedError, SystemToolError

# The milestone that will supply a real authorization decision. Imported from
# Part 4 rather than restated, so the two families cannot drift apart.
from app.tools.filesystem.destructive import BLOCKED_UNTIL

#: Why every state-changing system operation is refused right now.
NOT_AUTHORIZED_REASON = (
    "state-changing system operations are gated on the Security/Permission Engine "
    "(PART 7), which does not exist yet"
)

__all__ = [
    "BLOCKED_UNTIL",
    "NOT_AUTHORIZED_REASON",
    "DeniedSystemTool",
    "NotAuthorizedOutput",
    "SystemTool",
    "read_permissions",
    "control_permissions",
]


def read_permissions(
    *scopes: str, network: bool = False, filesystem: bool = False
) -> ToolPermissions:
    """Permissions for a tool that only observes."""
    return ToolPermissions(
        side_effect=SideEffect.READ,
        scopes=("system.read", *scopes),
        network_access=network,
        filesystem_access=filesystem,
    )


def control_permissions(
    *scopes: str, network: bool = False, filesystem: bool = False
) -> ToolPermissions:
    """Permissions for a tool that changes system state. Always confirmable."""
    return ToolPermissions(
        side_effect=SideEffect.WRITE,
        scopes=("system.write", *scopes),
        requires_confirmation=True,
        network_access=network,
        filesystem_access=filesystem,
    )


class SystemTool(BaseTool):
    """Base for every system tool: audited attempts, uniform error handling.

    ``policy`` is accepted by every system tool even though only the ``git.*``
    ones consult it, so that construction is uniform and a test can inject a
    sandbox without knowing which family it is holding.
    """

    domain: ClassVar[str]
    operation: ClassVar[str]

    def __init__(self, policy: FilesystemPolicy | None = None) -> None:
        self._policy = policy

    @property
    def policy(self) -> FilesystemPolicy:
        """The Part 4 sandbox - the process default unless one was injected."""
        return self._policy if self._policy is not None else get_default_policy()

    # --- execution template ------------------------------------------------

    def audited_targets(self, payload: Any) -> list[str]:
        """What this invocation is about - a pid, a unit, a container, a path."""
        return []

    async def run(self, payload: Any, context: ToolExecutionContext) -> BaseModel:
        targets = self.audited_targets(payload)
        try:
            output = await self.operate(payload, context)
        except FilesystemPolicyError as exc:
            self._audit(context, targets, allowed=False, outcome="denied", detail=str(exc))
            raise ToolExecutionError(self.name, str(exc)) from exc
        except SystemToolError as exc:
            self._audit(context, targets, allowed=True, outcome="error", detail=str(exc))
            raise ToolExecutionError(self.name, str(exc)) from exc
        except OSError as exc:
            detail = f"{type(exc).__name__}: {exc.strerror or exc}"
            self._audit(context, targets, allowed=True, outcome="error", detail=detail)
            raise ToolExecutionError(self.name, detail) from exc
        except Exception as exc:  # noqa: BLE001 - audit, then let the framework handle it
            self._audit(
                context,
                targets,
                allowed=True,
                outcome="error",
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise

        self._audit(context, targets, allowed=True, outcome="success")
        return output

    @abstractmethod
    async def operate(self, payload: Any, context: ToolExecutionContext) -> BaseModel:
        """Do the work. ``payload`` is already validated against ``input_model``."""

    def _audit(
        self,
        context: ToolExecutionContext,
        targets: list[str],
        *,
        allowed: bool,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        record_attempt(
            context,
            domain=self.domain,
            operation=self.operation,
            targets=targets,
            allowed=allowed,
            outcome=outcome,
            detail=detail,
        )


class NotAuthorizedOutput(BaseModel):
    """What a disabled system tool returns instead of doing its job."""

    authorized: bool = False
    domain: str
    operation: str
    reason: str = NOT_AUTHORIZED_REASON
    blocked_until: str = BLOCKED_UNTIL
    targets: list[str] = Field(default_factory=list)


class DeniedSystemTool(SystemTool):
    """A system tool that is implemented, registered, and refuses to run.

    ``execute`` is overridden rather than ``run``: the denial is returned before
    the framework's validate-then-run template starts, so there is no code path
    from a caller to a signal, a unit, a container or a working tree. The
    operation body below refuses too, as defence in depth, in case a future
    change ever reaches it.
    """

    output_model: ClassVar[type[BaseModel]] = NotAuthorizedOutput

    #: Argument names naming what would be acted on, for the audit record.
    target_arguments: ClassVar[tuple[str, ...]] = ()

    def is_authorized(self, arguments: Mapping[str, Any], context: ToolExecutionContext) -> bool:
        """Authorization check via the Security Engine (PART 7)."""
        from app.security.engine import get_security_engine

        engine = get_security_engine()
        return engine.check_tool_authorized(self, arguments, context)

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        started = time.perf_counter()

        # Get full security evaluation
        from app.security.engine import get_security_engine
        from app.security.models import SecurityDecision

        engine = get_security_engine()
        evaluation = engine.authorize(self, arguments, context)

        if evaluation.decision == SecurityDecision.ALLOW:
            return await super().execute(arguments, context)

        # Tool is denied or requires confirmation (which doesn't exist yet)
        targets = self._raw_targets(arguments)

        if evaluation.decision == SecurityDecision.REQUIRE_CONFIRMATION:
            reason = (
                f"tool {self.name!r} requires confirmation but no confirmation channel exists yet "
                f"(blocked until PART 10/11 implements confirmation UI/channel)"
            )
        else:
            reason = evaluation.reason

        self._audit(
            context,
            targets,
            allowed=False,
            outcome="not_authorized",
            detail=reason,
        )
        context.logger().warning(
            "state-changing tool refused (tool=%s, decision=%s)", self.name, evaluation.decision.value
        )

        return ToolResult(
            tool_name=self.name,
            status=ExecutionStatus.ERROR,
            output=NotAuthorizedOutput(
                domain=self.domain,
                operation=self.operation,
                targets=targets,
                reason=reason,
                blocked_until="PART 10/11 - Confirmation UI/Channel" if evaluation.decision == SecurityDecision.REQUIRE_CONFIRMATION else BLOCKED_UNTIL,
            ).model_dump(mode="json"),
            error=reason,
            duration_ms=self._elapsed_ms(started),
        )

    async def operate(self, payload: Any, context: ToolExecutionContext) -> BaseModel:
        """Unreachable in Part 6. Refuses rather than acts if it is ever called."""
        raise SystemOperationNotAuthorizedError(self.operation, NOT_AUTHORIZED_REASON)

    def _raw_targets(self, arguments: Mapping[str, Any]) -> list[str]:
        """Best-effort target extraction from unvalidated arguments, for the audit."""
        values = [arguments.get(key) for key in self.target_arguments]
        return [str(value) for value in values if isinstance(value, (str, bytes, int))]
