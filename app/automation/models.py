"""PART 15 automation engine: task, trigger, condition, and execution models.

Trigger/condition abstraction:

- A trigger only answers "is this task due, and what caused it" - it never
  touches tools or security (kept out of trigger classes on purpose, per the
  Part 15 spec).
- A condition only answers "should this due task actually run right now" -
  independent of the trigger that fired it.

Both are closed, discriminated (``kind``) pydantic unions so a task
definition round-trips through JSON without a class registry, and so a
future trigger/condition type is additive (new union member), not a
redesign of :class:`AutomationTask` or the runner.
"""

from datetime import datetime, time, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, Field

from app.devices.models import TrustState

#: Execution history is bounded per task - enough to see recent behavior,
#: never an unbounded audit log living inside the task definition itself.
MAX_HISTORY = 20


# ===========================================================================
# Triggers
# ===========================================================================


class ScheduledTrigger(BaseModel):
    """Fires once, at a specific point in time."""

    kind: Literal["scheduled"] = "scheduled"
    run_at: datetime

    @property
    def one_shot(self) -> bool:
        return True

    def is_due(self, *, now: datetime, last_run_at: datetime | None) -> bool:
        return last_run_at is None and now >= self.run_at


class RecurringTrigger(BaseModel):
    """Fires every ``interval_seconds``, indefinitely, until disabled."""

    kind: Literal["recurring"] = "recurring"
    interval_seconds: float = Field(..., gt=0)

    @property
    def one_shot(self) -> bool:
        return False

    def is_due(self, *, now: datetime, last_run_at: datetime | None) -> bool:
        if last_run_at is None:
            return True
        return (now - last_run_at).total_seconds() >= self.interval_seconds


class CronTrigger(BaseModel):
    """Minimal cron-style recurring schedule.

    Deliberately not a full cron parser - only ``*`` (any) or an explicit
    comma-separated list of integers per field is supported. That covers
    "every day at 09:00", "at minute 0 and 30 of every hour", etc. without
    the step/range syntax a real cron grammar needs and this project has no
    dependency that already provides for free.
    """

    kind: Literal["cron"] = "cron"
    minute: str = "*"
    hour: str = "*"
    day_of_month: str = "*"
    month: str = "*"
    day_of_week: str = "*"

    @property
    def one_shot(self) -> bool:
        return False

    @staticmethod
    def _matches(field: str, value: int) -> bool:
        if field.strip() == "*":
            return True
        try:
            allowed = {int(part.strip()) for part in field.split(",") if part.strip()}
        except ValueError:
            return False
        return value in allowed

    def matches(self, when: datetime) -> bool:
        return (
            self._matches(self.minute, when.minute)
            and self._matches(self.hour, when.hour)
            and self._matches(self.day_of_month, when.day)
            and self._matches(self.month, when.month)
            and self._matches(self.day_of_week, when.isoweekday() % 7)  # 0=Sunday
        )

    def is_due(self, *, now: datetime, last_run_at: datetime | None) -> bool:
        if not self.matches(now):
            return False
        # Fire once per matching minute, not on every poll within it.
        if last_run_at is not None and last_run_at.replace(second=0, microsecond=0) == now.replace(
            second=0, microsecond=0
        ):
            return False
        return True


class EventTrigger(BaseModel):
    """Interface-only trigger for a future (Part 14/16) event source.

    Part 15 ships no real event source: nothing ever calls this with a
    matching ``pending_event``, so it never fires on its own. It exists so a
    later event source can hand the runner an event without any change to
    task persistence, conditions, security integration, execution history,
    or the runner itself - only this trigger's ``is_due`` gains an argument.
    """

    kind: Literal["event"] = "event"
    event_type: str

    @property
    def one_shot(self) -> bool:
        return False

    def is_due(
        self, *, now: datetime, last_run_at: datetime | None, pending_event: str | None = None
    ) -> bool:
        return pending_event is not None and pending_event == self.event_type


Trigger = Annotated[
    Union[ScheduledTrigger, RecurringTrigger, CronTrigger, EventTrigger],
    Field(discriminator="kind"),
]


# ===========================================================================
# Conditions
# ===========================================================================


class TimeWindowCondition(BaseModel):
    """Passes only while local time falls within ``[start, end)``.

    Handles an overnight window (``start > end``, e.g. 22:00-06:00) by
    wrapping around midnight.
    """

    kind: Literal["time_window"] = "time_window"
    start: time
    end: time

    def evaluate(self, *, now: datetime, device=None) -> tuple[bool, str]:
        current = now.time()
        if self.start <= self.end:
            passed = self.start <= current < self.end
        else:
            passed = current >= self.start or current < self.end
        reason = (
            f"current time {current.isoformat(timespec='minutes')} "
            f"{'is' if passed else 'is not'} within window "
            f"{self.start.isoformat(timespec='minutes')}-{self.end.isoformat(timespec='minutes')}"
        )
        return passed, reason


class DeviceTrustCondition(BaseModel):
    """Passes only if the named device is currently at ``required_trust``."""

    kind: Literal["device_trust"] = "device_trust"
    device_id: str
    required_trust: TrustState = TrustState.TRUSTED

    def evaluate(self, *, now: datetime, device=None) -> tuple[bool, str]:
        if device is None:
            return False, f"device {self.device_id!r} is not registered"
        if device.trust != self.required_trust:
            return False, (
                f"device {self.device_id!r} trust is {device.trust.value!r}, "
                f"required {self.required_trust.value!r}"
            )
        return True, f"device {self.device_id!r} trust is {self.required_trust.value!r}"


Condition = Annotated[
    Union[TimeWindowCondition, DeviceTrustCondition], Field(discriminator="kind")
]


# ===========================================================================
# Execution history
# ===========================================================================


class ExecutionOutcome(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    REQUIRE_CONFIRMATION = "require_confirmation"
    CONDITION_FAILED = "condition_failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ExecutionRecord(BaseModel):
    """One recorded attempt to run a task. Bounded, redacted, never raw secrets."""

    execution_id: str
    request_id: str
    task_id: str
    tool_name: str
    triggered_by: str = "schedule"  # "schedule" | "manual"
    started_at: datetime
    finished_at: datetime | None = None
    outcome: ExecutionOutcome
    security_decision: str | None = None
    attempts: int = 1
    retries: int = 0
    result_summary: str | None = None
    error_summary: str | None = None


# ===========================================================================
# Task
# ===========================================================================


def _new_task_id() -> str:
    return uuid4().hex


class AutomationTask(BaseModel):
    """A persistent, schedulable unit of work.

    Execution always follows: Trigger -> Condition -> Security Engine ->
    Tool Framework -> Named Tool. Nothing here bypasses that path - the task
    only names a trigger, a condition, and the ``tool_name``/``tool_input``
    pair the runner hands to the existing framework unchanged.
    """

    task_id: str = Field(default_factory=_new_task_id)
    name: str
    description: str = ""
    trigger: Trigger
    condition: Condition | None = None
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    retry_count: int = Field(default=0, ge=0, le=10)
    retry_delay_seconds: float = Field(default=30.0, ge=0)
    timeout_seconds: float = Field(default=30.0, gt=0)
    last_run_at: datetime | None = None
    history: list[ExecutionRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def record(self, entry: ExecutionRecord) -> None:
        self.history.append(entry)
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]


class AutomationTaskCreate(BaseModel):
    """Request body for ``POST /automation/tasks``."""

    name: str
    description: str = ""
    trigger: Trigger
    condition: Condition | None = None
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    retry_count: int = Field(default=0, ge=0, le=10)
    retry_delay_seconds: float = Field(default=30.0, ge=0)
    timeout_seconds: float = Field(default=30.0, gt=0)


class AutomationTaskUpdate(BaseModel):
    """Request body for ``PUT /automation/tasks/{task_id}``. All fields optional."""

    name: str | None = None
    description: str | None = None
    trigger: Trigger | None = None
    condition: Condition | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    enabled: bool | None = None
    retry_count: int | None = Field(default=None, ge=0, le=10)
    retry_delay_seconds: float | None = Field(default=None, ge=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
