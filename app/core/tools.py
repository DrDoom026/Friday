"""The tool framework: the common interface every FIRDAY capability implements.

A tool declares what it is (name, description, version), what it accepts and
returns (pydantic models, exposed as JSON Schema), and what it would need
permission to do. Concrete tools live in ``app.tools``.
"""

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar, Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.context import ToolExecutionContext
from app.core.errors import ToolExecutionError, ToolValidationError
from app.core.models import ExecutionStatus, ToolResult


class SideEffect(str, Enum):
    """How far a tool reaches beyond itself."""

    NONE = "none"
    READ = "read"
    WRITE = "write"


class ToolPermissions(BaseModel):
    """Declared capability requirements for a tool.

    Metadata only — nothing here is enforced. The permission engine that reads
    these fields and decides ALLOW/DENY arrives in Part 7.
    """

    model_config = ConfigDict(frozen=True)

    side_effect: SideEffect = SideEffect.NONE
    scopes: tuple[str, ...] = ()
    requires_confirmation: bool = False
    network_access: bool = False
    filesystem_access: bool = False


@runtime_checkable
class Tool(Protocol):
    """Something Core can execute on a planner's behalf."""

    name: str
    description: str
    version: str
    permissions: ToolPermissions
    input_model: type[BaseModel]
    output_model: type[BaseModel]

    def input_schema(self) -> dict[str, Any]: ...

    def output_schema(self) -> dict[str, Any]: ...

    def validate_input(self, arguments: Mapping[str, Any]) -> BaseModel: ...

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult: ...


class BaseTool(ABC):
    """Default :class:`Tool` implementation. Subclasses only implement ``run``.

    ``execute`` is the template: validate the arguments, run the tool, time it,
    and wrap whatever happened in a structured :class:`ToolResult`.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    version: ClassVar[str] = "0.1.0"
    permissions: ClassVar[ToolPermissions] = ToolPermissions()
    input_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel]]

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()

    def validate_input(self, arguments: Mapping[str, Any]) -> BaseModel:
        """Coerce raw arguments into the tool's input model.

        Raises :class:`ToolValidationError` if they do not fit the schema.
        """
        try:
            return self.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            raise ToolValidationError(self.name, exc.errors()) from exc

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        log = context.logger()
        started = time.perf_counter()

        # PART 7: Authorize every tool execution through the Security Engine
        from app.security.engine import get_security_engine
        from app.security.models import SecurityDecision

        engine = get_security_engine()
        evaluation = engine.authorize(self, arguments, context)
        if evaluation.decision != SecurityDecision.ALLOW:
            reason = evaluation.reason
            if evaluation.decision == SecurityDecision.REQUIRE_CONFIRMATION:
                reason = (
                    f"tool {self.name!r} requires confirmation but no confirmation channel exists yet "
                    f"(blocked until PART 10/11 implements confirmation UI/channel)"
                )
            log.warning(
                "tool execution blocked by security engine (tool=%s, decision=%s): %s",
                self.name,
                evaluation.decision.value,
                reason,
            )
            return self._failure(reason, started)

        try:
            payload = self.validate_input(arguments)
        except ToolValidationError as exc:
            log.warning("tool input rejected (tool=%s): %s", self.name, exc)
            return self._failure(str(exc), started)

        log.info("tool executing (tool=%s, version=%s)", self.name, self.version)
        try:
            output = await self.run(payload, context)
        except ToolExecutionError as exc:
            log.warning("tool failed (tool=%s): %s", self.name, exc)
            return self._failure(str(exc), started)
        except Exception as exc:  # noqa: BLE001 - a tool must not crash the caller
            log.exception("tool raised unexpectedly (tool=%s)", self.name)
            return self._failure(f"tool {self.name!r} raised {type(exc).__name__}", started)

        log.info("tool succeeded (tool=%s)", self.name)
        return ToolResult(
            tool_name=self.name,
            status=ExecutionStatus.SUCCESS,
            output=output.model_dump(mode="json"),
            duration_ms=self._elapsed_ms(started),
        )

    @abstractmethod
    async def run(self, payload: Any, context: ToolExecutionContext) -> BaseModel:
        """Do the work. ``payload`` is already validated against ``input_model``."""

    def _failure(self, error: str, started: float) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            status=ExecutionStatus.ERROR,
            error=error,
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000


class ToolDescriptor(BaseModel):
    """Serializable summary of a registered tool, for discovery."""

    name: str
    description: str
    version: str
    permissions: ToolPermissions
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_tool(cls, tool: Tool) -> "ToolDescriptor":
        return cls(
            name=tool.name,
            description=tool.description,
            version=tool.version,
            permissions=tool.permissions,
            input_schema=tool.input_schema(),
            output_schema=tool.output_schema(),
        )
