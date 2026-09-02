"""Destructive filesystem tools: delete, move and rename.

These are deliberately inert. Each one is a real, registered tool with a real
input schema, so a planner can see it and address it - but its authorization
check is a stub that always denies, and ``execute`` returns that denial before
input validation, before path resolution, and before any code that could touch
the disk runs.

The gate opens in PART 7, when the Security/Permission Engine exists to make an
actual decision. Until then :meth:`DestructiveFilesystemTool.is_authorized`
returns ``False``, unconditionally, and the operation methods below are
unreachable - they raise rather than act if anything ever reaches them.
"""

import time
from typing import Any, ClassVar, Mapping

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.models import ExecutionStatus, ToolResult
from app.core.registry import register_tool
from app.fs.audit import record_attempt
from app.fs.errors import OperationNotAuthorizedError
from app.tools.filesystem.base import DESTRUCTIVE_PERMISSIONS, FilesystemTool, PathInput

#: Why every destructive filesystem operation is refused right now.
NOT_AUTHORIZED_REASON = (
    "destructive filesystem operations are gated on the Security/Permission Engine "
    "(PART 7), which does not exist yet"
)

#: The milestone that will supply a real authorization decision.
BLOCKED_UNTIL = "PART 7 - Security/Permission Engine"


class NotAuthorizedOutput(BaseModel):
    """What a disabled destructive tool returns instead of doing its job."""

    authorized: bool = False
    operation: str
    reason: str = NOT_AUTHORIZED_REASON
    blocked_until: str = BLOCKED_UNTIL
    paths: list[str] = Field(default_factory=list)


class DestructiveFilesystemTool(FilesystemTool):
    """A filesystem tool that is implemented, registered, and refuses to run.

    ``execute`` is overridden rather than ``run``: the denial is returned before
    the framework's validate-then-run template starts, so there is no code path
    from a caller to the underlying operation.
    """

    output_model: ClassVar[type[BaseModel]] = NotAuthorizedOutput
    permissions = DESTRUCTIVE_PERMISSIONS

    #: Argument names holding paths, used for audit records on unvalidated input.
    path_arguments: ClassVar[tuple[str, ...]] = ("path",)

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
        paths = self._raw_paths(arguments)

        if evaluation.decision == SecurityDecision.REQUIRE_CONFIRMATION:
            reason = (
                f"tool {self.name!r} requires confirmation but no confirmation channel exists yet "
                f"(blocked until PART 10/11 implements confirmation UI/channel)"
            )
        else:
            reason = evaluation.reason

        record_attempt(
            context,
            operation=self.operation,
            paths=paths,
            resolved_paths=(),
            allowed=False,
            outcome="not_authorized",
            detail=reason,
        )
        context.logger().warning(
            "destructive tool refused (tool=%s, decision=%s)", self.name, evaluation.decision.value
        )

        return ToolResult(
            tool_name=self.name,
            status=ExecutionStatus.ERROR,
            output=NotAuthorizedOutput(
                operation=self.operation,
                paths=paths,
                reason=reason,
                blocked_until="PART 10/11 - Confirmation UI/Channel" if evaluation.decision == SecurityDecision.REQUIRE_CONFIRMATION else BLOCKED_UNTIL,
            ).model_dump(mode="json"),
            error=reason,
            duration_ms=self._elapsed_ms(started),
        )

    async def operate(self, payload: Any, context: ToolExecutionContext) -> BaseModel:
        """Unreachable in Part 4. Refuses rather than acts if it is ever called."""
        raise OperationNotAuthorizedError(self.operation, NOT_AUTHORIZED_REASON)

    def _raw_paths(self, arguments: Mapping[str, Any]) -> list[str]:
        """Best-effort path extraction from unvalidated arguments, for the audit record."""
        values = [arguments.get(key) for key in self.path_arguments]
        return [str(value) for value in values if isinstance(value, (str, bytes))]


# --- fs.delete -------------------------------------------------------------


class DeleteInput(PathInput):
    recursive: bool = Field(False, description="Required to delete a non-empty directory.")


@register_tool
class DeleteTool(DestructiveFilesystemTool):
    name = "fs.delete"
    operation = "delete"
    description = (
        "Delete a file or directory. DISABLED: always refuses, pending the "
        "Security/Permission Engine (PART 7)."
    )
    version = "0.1.0"
    input_model = DeleteInput


# --- fs.move ---------------------------------------------------------------


class MoveInput(BaseModel):
    source: str = Field(..., min_length=1, description="Absolute path to move from.")
    destination: str = Field(..., min_length=1, description="Absolute path to move to.")


@register_tool
class MoveTool(DestructiveFilesystemTool):
    name = "fs.move"
    operation = "move"
    description = (
        "Move a file or directory to another path. DISABLED: always refuses, "
        "pending the Security/Permission Engine (PART 7)."
    )
    version = "0.1.0"
    input_model = MoveInput
    path_arguments = ("source", "destination")


# --- fs.rename -------------------------------------------------------------


class RenameInput(PathInput):
    new_name: str = Field(
        ..., min_length=1, description="New base name, within the same directory."
    )


@register_tool
class RenameTool(DestructiveFilesystemTool):
    name = "fs.rename"
    operation = "rename"
    description = (
        "Rename a file or directory in place. DISABLED: always refuses, pending "
        "the Security/Permission Engine (PART 7)."
    )
    version = "0.1.0"
    input_model = RenameInput
