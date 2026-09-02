"""Security decision models and permission levels."""

from enum import Enum

from pydantic import BaseModel, Field


class PermissionLevel(str, Enum):
    """How far a tool reaches into the system.

    Ordered from least to most privilege. The Security Engine uses this to
    determine the default policy: READ operations allow by default, while
    DESTRUCTIVE operations deny or require confirmation.
    """

    READ = "read"
    WRITE = "write"
    MODIFY = "modify"
    EXECUTE = "execute"
    PRIVILEGED = "privileged"
    DESTRUCTIVE = "destructive"


class SecurityDecision(str, Enum):
    """What the Security Engine decided about a tool invocation."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


class SecurityContext(BaseModel):
    """The context the Security Engine evaluates to make a decision.

    Captures who is asking (device/actor), what they want (tool + arguments),
    and where the request came from (correlation ID for audit trail).
    """

    tool_name: str
    device_id: str = Field(default="local", description="Device executing the tool.")
    trust_level: str = Field(
        default="unverified", description="Device trust state from Part 5."
    )
    request_id: str = Field(..., description="Correlation ID from the request context.")
    target: str | None = Field(None, description="What the tool would act on, when known.")


class SecurityEvaluation(BaseModel):
    """The Security Engine's decision and reasoning for one tool invocation."""

    decision: SecurityDecision
    tool_name: str
    permission_level: PermissionLevel
    reason: str = Field(..., description="Why this decision was made.")
    requires_confirmation: bool = Field(
        False, description="Whether the tool's metadata requires confirmation."
    )
    policy_applied: str = Field(
        "default", description="Which policy rule produced this decision."
    )
