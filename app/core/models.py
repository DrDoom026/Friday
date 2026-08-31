"""Data models for the FIRDAY orchestration flow.

Part 1 scope: request in, plan out, response back. Nothing executes yet, but
the execution/result shape is defined here so Part 2 can fill it in.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FirdayRequest(BaseModel):
    """A single unit of work submitted to FIRDAY."""

    input: str = Field(..., min_length=1, description="Natural-language instruction.")
    metadata: dict[str, str] = Field(
        default_factory=dict, description="Caller-supplied, request-scoped extras."
    )


class PlanStep(BaseModel):
    """One intended tool invocation. Intent only — nothing runs it in Part 1."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class Plan(BaseModel):
    """What a planner decided should happen for a request."""

    planner_name: str
    summary: str
    steps: list[PlanStep] = Field(default_factory=list)


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"
    NOT_EXECUTED = "not_executed"


class ToolResult(BaseModel):
    """The outcome of a tool execution.

    Part 1 emits one of these per planned step with ``NOT_EXECUTED`` so the
    contract is exercised end to end before any tool exists.
    """

    tool_name: str
    status: ExecutionStatus
    output: Any | None = None
    error: str | None = None
    duration_ms: float | None = None


class FirdayResponse(BaseModel):
    """What FIRDAY Core returns for a request."""

    request_id: str
    output: str
    plan: Plan
    results: list[ToolResult] = Field(default_factory=list)
