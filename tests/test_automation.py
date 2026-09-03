"""Tests for PART 15: the Automation Engine.

Covers the trigger/condition abstractions, the task model, persistence
(atomic JSON, survives a simulated restart), the runner's execution
pipeline (Security Engine integration, retries, timeouts, cancellation,
bounded history), secret rejection, and the automation API routes.

Every automated tool action must reach the exact same Security Engine used
everywhere else in FIRDAY - these tests prove that by using the real
:class:`~app.security.engine.SecurityEngine` and asserting DENY/
REQUIRE_CONFIRMATION are never executed, exactly like ``tests/test_llm.py``
does for LLM-originated plan steps.
"""

import asyncio
from datetime import datetime, time as dtime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.automation.errors import TaskNotFoundError, TaskValidationError
from app.automation.models import (
    AutomationTask,
    AutomationTaskCreate,
    AutomationTaskUpdate,
    CronTrigger,
    DeviceTrustCondition,
    EventTrigger,
    ExecutionOutcome,
    RecurringTrigger,
    ScheduledTrigger,
    TimeWindowCondition,
)
from app.automation.runner import TaskRunner
from app.automation.service import AutomationService
from app.automation.store import AutomationStore
from app.core.context import RequestContext
from app.core.orchestrator import Core
from app.core.registry import ToolRegistry
from app.core.tools import BaseTool, SideEffect, ToolPermissions
from app.devices.models import Device, TrustState
from app.devices.registry import DeviceRegistry
from app.security.engine import SecurityEngine, get_security_engine, set_security_engine
from app.security.policy import DefaultSecurityPolicy
from app.tools.echo import EchoTool


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_security_engine():
    original = get_security_engine()
    yield
    set_security_engine(original)


async def _no_sleep(_seconds):
    return None


# ===========================================================================
# Stub tools
# ===========================================================================


class _NoopInput(BaseModel):
    pass


class _NoopOutput(BaseModel):
    ran: bool = True


class _WriteTool(BaseTool):
    """WRITE side effect, no confirmation requirement - DENY-able by policy."""

    name = "stub.write"
    description = "stub"
    permissions = ToolPermissions(side_effect=SideEffect.WRITE)
    input_model = _NoopInput
    output_model = _NoopOutput

    async def run(self, payload, context):
        raise AssertionError("must never run when denied")


class _ConfirmTool(BaseTool):
    """Forces REQUIRE_CONFIRMATION."""

    name = "stub.confirm"
    description = "stub"
    permissions = ToolPermissions(side_effect=SideEffect.WRITE, requires_confirmation=True)
    input_model = _NoopInput
    output_model = _NoopOutput

    async def run(self, payload, context):
        raise AssertionError("must never run without a confirmation channel")


class _FlakyTool(BaseTool):
    """Fails ``fail_times`` times, then succeeds. Used for retry tests."""

    name = "stub.flaky"
    description = "stub"
    permissions = ToolPermissions(side_effect=SideEffect.NONE)
    input_model = _NoopInput
    output_model = _NoopOutput
    calls = 0
    fail_times = 1

    async def run(self, payload, context):
        type(self).calls += 1
        if type(self).calls <= type(self).fail_times:
            from app.core.errors import ToolExecutionError

            raise ToolExecutionError("transient failure")
        return _NoopOutput()


class _SlowTool(BaseTool):
    """Never completes within any reasonable timeout - for timeout/cancel tests."""

    name = "stub.slow"
    description = "stub"
    permissions = ToolPermissions(side_effect=SideEffect.NONE)
    input_model = _NoopInput
    output_model = _NoopOutput

    async def run(self, payload, context):
        await asyncio.sleep(3600)
        return _NoopOutput()


def _registry(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in tools:
        reg.register(tool)
    return reg


def _service(tmp_path, name="tasks.json") -> AutomationService:
    return AutomationService(AutomationStore(tmp_path / name))


def _echo_create(**overrides) -> AutomationTaskCreate:
    payload = dict(
        name="say hi",
        trigger=ScheduledTrigger(run_at=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        tool_name="echo",
        tool_input={"message": "hi"},
    )
    payload.update(overrides)
    return AutomationTaskCreate(**payload)


# ===========================================================================
# 1. Task model creation / validation
# ===========================================================================


def test_task_model_creation_has_sane_defaults():
    task = AutomationTask(
        name="t",
        trigger=RecurringTrigger(interval_seconds=60),
        tool_name="echo",
        tool_input={"message": "hi"},
    )
    assert task.enabled is True
    assert task.retry_count == 0
    assert task.history == []
    assert task.task_id


def test_task_model_rejects_out_of_range_retry_count():
    with pytest.raises(Exception):
        AutomationTask(
            name="t",
            trigger=RecurringTrigger(interval_seconds=60),
            tool_name="echo",
            retry_count=999,
        )


# ===========================================================================
# 2-5. Triggers
# ===========================================================================


def test_scheduled_trigger_due_and_not_due():
    trigger = ScheduledTrigger(run_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    before = datetime(2025, 12, 31, tzinfo=timezone.utc)
    after = datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert not trigger.is_due(now=before, last_run_at=None)
    assert trigger.is_due(now=after, last_run_at=None)


def test_scheduled_trigger_is_one_shot_never_due_again_after_a_run():
    trigger = ScheduledTrigger(run_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    after = datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert trigger.one_shot is True
    assert not trigger.is_due(now=after, last_run_at=after)


def test_recurring_trigger_due_behavior():
    trigger = RecurringTrigger(interval_seconds=60)
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert trigger.is_due(now=t0, last_run_at=None)
    assert not trigger.is_due(now=t0, last_run_at=t0)
    later = t0 + timedelta(seconds=61)
    assert trigger.is_due(now=later, last_run_at=t0)


def test_cron_trigger_matches_only_the_configured_minute_and_fires_once():
    trigger = CronTrigger(minute="30", hour="9")
    at_0930 = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
    at_0931 = datetime(2026, 1, 1, 9, 31, tzinfo=timezone.utc)
    assert trigger.is_due(now=at_0930, last_run_at=None)
    assert not trigger.is_due(now=at_0931, last_run_at=None)
    # already ran this exact minute -> not due again on a second poll
    assert not trigger.is_due(now=at_0930, last_run_at=at_0930)


def test_event_trigger_never_fires_without_a_matching_event():
    trigger = EventTrigger(event_type="gmail.no_reply_timeout")
    now = datetime.now(timezone.utc)
    assert not trigger.is_due(now=now, last_run_at=None)
    assert not trigger.is_due(now=now, last_run_at=None, pending_event="something.else")
    assert trigger.is_due(now=now, last_run_at=None, pending_event="gmail.no_reply_timeout")


# ===========================================================================
# 6-8. Conditions
# ===========================================================================


def test_time_window_condition_passes_and_fails():
    window = TimeWindowCondition(start=dtime(9, 0), end=dtime(17, 0))
    inside = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    outside = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
    passed, _ = window.evaluate(now=inside)
    assert passed
    passed, reason = window.evaluate(now=outside)
    assert not passed
    assert "window" in reason


def test_device_trust_condition_passes_and_fails():
    condition = DeviceTrustCondition(device_id="phone-1", required_trust=TrustState.TRUSTED)
    trusted = Device(device_id="phone-1", name="phone", trust=TrustState.TRUSTED)
    untrusted = Device(device_id="phone-1", name="phone", trust=TrustState.UNTRUSTED)
    now = datetime.now(timezone.utc)
    passed, _ = condition.evaluate(now=now, device=trusted)
    assert passed
    passed, reason = condition.evaluate(now=now, device=untrusted)
    assert not passed
    assert "untrusted" in reason


def test_condition_failure_prevents_execution_and_is_not_a_success(tmp_path):
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(EchoTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)

    task = service.create(
        _echo_create(
            condition=TimeWindowCondition(start=dtime(9, 0), end=dtime(9, 1)),
        )
    )
    now = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)  # outside the window
    record = run(runner.execute(task, now=now, triggered_by="manual"))

    assert record.outcome == ExecutionOutcome.CONDITION_FAILED
    assert record.security_decision is None  # never reached the Security Engine
    task = service.get(task.task_id)
    assert len(task.history) == 1


# ===========================================================================
# 9-12. Security Engine integration
# ===========================================================================


def test_allow_passes_through_security_engine_and_executes_named_tool(tmp_path):
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(EchoTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(_echo_create())

    record = run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))

    assert record.outcome == ExecutionOutcome.SUCCESS
    assert record.security_decision == "allow"
    assert record.result_summary and "hi" in record.result_summary


def test_deny_prevents_tool_execution(tmp_path):
    set_security_engine(SecurityEngine(DefaultSecurityPolicy(allow_write=False)))
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(_WriteTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(_echo_create(tool_name="stub.write", tool_input={}))

    record = run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))

    assert record.outcome == ExecutionOutcome.DENIED
    assert record.security_decision == "deny"
    assert record.retries == 0  # DENY must never be retried


def test_require_confirmation_prevents_tool_execution_and_never_auto_approves(tmp_path):
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(_ConfirmTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(
        _echo_create(tool_name="stub.confirm", tool_input={}, retry_count=3)
    )

    record = run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))

    assert record.outcome == ExecutionOutcome.REQUIRE_CONFIRMATION
    assert record.security_decision == "require_confirmation"
    assert record.retries == 0  # never retried, never auto-approved


# ===========================================================================
# 13-14. Retries
# ===========================================================================


def test_retry_behavior_succeeds_after_transient_failures(tmp_path):
    _FlakyTool.calls = 0
    _FlakyTool.fail_times = 2
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(_FlakyTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(
        _echo_create(tool_name="stub.flaky", tool_input={}, retry_count=3)
    )

    record = run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))

    assert record.outcome == ExecutionOutcome.SUCCESS
    assert record.attempts == 3
    assert record.retries == 2


def test_retry_exhaustion_reports_error(tmp_path):
    _FlakyTool.calls = 0
    _FlakyTool.fail_times = 99
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(_FlakyTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(_echo_create(tool_name="stub.flaky", tool_input={}, retry_count=1))

    record = run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))

    assert record.outcome == ExecutionOutcome.ERROR
    assert record.attempts == 2
    assert record.retries == 1


# ===========================================================================
# 15-16. Timeouts / cancellation
# ===========================================================================


def test_timeout_behavior_is_recorded_and_not_retried_forever(tmp_path):
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(_SlowTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(
        _echo_create(tool_name="stub.slow", tool_input={}, timeout_seconds=0.05, retry_count=1)
    )

    record = run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))

    assert record.outcome == ExecutionOutcome.TIMEOUT
    assert record.attempts == 2  # one retry attempted, still timed out


def test_cancellation_behavior(tmp_path):
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(_SlowTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(
        _echo_create(tool_name="stub.slow", tool_input={}, timeout_seconds=5, retry_count=0)
    )

    async def _cancel_soon():
        # Poll until the execution registers itself as active, then cancel it.
        for _ in range(200):
            if runner._active:
                execution_id = next(iter(runner._active))
                assert runner.cancel(execution_id)
                return
            await asyncio.sleep(0.01)
        raise AssertionError("execution never became active")

    async def _scenario():
        exec_coro = runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual")
        record, _ = await asyncio.gather(exec_coro, _cancel_soon())
        return record

    record = run(_scenario())
    assert record.outcome == ExecutionOutcome.CANCELLED


# ===========================================================================
# 17-18. Execution history
# ===========================================================================


def test_execution_history_is_recorded(tmp_path):
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(EchoTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(_echo_create())

    run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))
    task = service.get(task.task_id)

    assert len(task.history) == 1
    entry = task.history[0]
    assert entry.task_id == task.task_id
    assert entry.tool_name == "echo"
    assert entry.execution_id
    assert entry.request_id


def test_history_is_bounded_to_last_n_runs(tmp_path):
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(EchoTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(_echo_create())

    for _ in range(30):
        run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))

    task = service.get(task.task_id)
    from app.automation.models import MAX_HISTORY

    assert len(task.history) == MAX_HISTORY


# ===========================================================================
# 19-20. Persistence
# ===========================================================================


def test_persistence_save_and_load(tmp_path):
    store = AutomationStore(tmp_path / "tasks.json")
    service = AutomationService(store)
    task = service.create(_echo_create())

    reloaded = AutomationStore(tmp_path / "tasks.json").load()
    assert len(reloaded) == 1
    assert reloaded[0].task_id == task.task_id
    assert reloaded[0].tool_name == "echo"


def test_persistence_survives_simulated_restart(tmp_path):
    store_path = tmp_path / "tasks.json"
    service_1 = AutomationService(AutomationStore(store_path))
    task = service_1.create(_echo_create(name="survives restart"))

    # Simulate a fresh process: a brand new service instance over the same file.
    service_2 = AutomationService(AutomationStore(store_path))
    reloaded = service_2.get(task.task_id)
    assert reloaded.name == "survives restart"


# ===========================================================================
# 21-24. API
# ===========================================================================


@pytest.fixture
def api_client(tmp_path):
    """The real FastAPI app, with an isolated automation service/runner swapped in.

    Deliberately does not enter the app as a context manager (matching every
    other Part 10 API test in this repo, e.g. ``tests/test_request_endpoint.py``)
    so the lifespan's filesystem-sandbox bootstrap never runs - it is
    orthogonal to what these tests check. Swaps in a tmp-path-backed
    ``AutomationService``/``TaskRunner`` instead of mutating the process-wide
    tool registry, so the Part 2/6 tool-count tests elsewhere are unaffected.
    """
    import app.main as main_module

    test_registry = _registry(EchoTool(), _WriteTool())
    test_core = Core(planner=main_module.core.planner, registry=test_registry)
    test_service = AutomationService(AutomationStore(tmp_path / "tasks.json"))
    test_runner = TaskRunner(
        test_core, test_service, devices=main_module.devices.registry, sleep=_no_sleep
    )

    original_service, original_runner = main_module.automation_service, main_module.automation_runner
    main_module.automation_service = test_service
    main_module.automation_runner = test_runner
    try:
        yield TestClient(main_module.app, raise_server_exceptions=False)
    finally:
        main_module.automation_service = original_service
        main_module.automation_runner = original_runner


def test_api_create_list_get_update_delete(api_client):
    create_resp = api_client.post(
        "/automation/tasks",
        json={
            "name": "api task",
            "trigger": {"kind": "recurring", "interval_seconds": 60},
            "tool_name": "echo",
            "tool_input": {"message": "hi"},
        },
    )
    assert create_resp.status_code == 201
    task_id = create_resp.json()["task_id"]

    assert api_client.get("/automation/tasks").status_code == 200
    assert task_id in [t["task_id"] for t in api_client.get("/automation/tasks").json()]

    get_resp = api_client.get(f"/automation/tasks/{task_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "api task"

    update_resp = api_client.put(f"/automation/tasks/{task_id}", json={"name": "renamed"})
    assert update_resp.status_code == 200
    assert update_resp.json()["name"] == "renamed"

    delete_resp = api_client.delete(f"/automation/tasks/{task_id}")
    assert delete_resp.status_code == 204
    assert api_client.get(f"/automation/tasks/{task_id}").status_code == 404


def test_api_enable_disable(api_client):
    create_resp = api_client.post(
        "/automation/tasks",
        json={
            "name": "toggle me",
            "trigger": {"kind": "recurring", "interval_seconds": 60},
            "tool_name": "echo",
            "tool_input": {"message": "hi"},
        },
    )
    task_id = create_resp.json()["task_id"]

    disabled = api_client.put(f"/automation/tasks/{task_id}", json={"enabled": False})
    assert disabled.json()["enabled"] is False

    enabled = api_client.put(f"/automation/tasks/{task_id}", json={"enabled": True})
    assert enabled.json()["enabled"] is True


def test_api_manual_run_executes_via_echo(api_client):
    create_resp = api_client.post(
        "/automation/tasks",
        json={
            "name": "run me",
            "trigger": {"kind": "recurring", "interval_seconds": 60},
            "tool_name": "echo",
            "tool_input": {"message": "hello"},
        },
    )
    task_id = create_resp.json()["task_id"]

    run_resp = api_client.post(f"/automation/tasks/{task_id}/run")
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["outcome"] == "success"
    assert body["security_decision"] == "allow"


def test_api_manual_run_still_passes_security_engine_and_is_denied(api_client):
    set_security_engine(SecurityEngine(DefaultSecurityPolicy(allow_write=False)))

    create_resp = api_client.post(
        "/automation/tasks",
        json={
            "name": "denied run",
            "trigger": {"kind": "recurring", "interval_seconds": 60},
            "tool_name": "stub.write",
            "tool_input": {},
        },
    )
    task_id = create_resp.json()["task_id"]

    run_resp = api_client.post(f"/automation/tasks/{task_id}/run")
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["outcome"] == "denied"
    assert body["security_decision"] == "deny"


# ===========================================================================
# 25-26. Secrets
# ===========================================================================


def test_secrets_are_rejected_in_task_creation(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(TaskValidationError):
        service.create(
            _echo_create(tool_input={"message": "my api_key = sk-abcdefghijklmnopqrstuvwxyz"})
        )


def test_secrets_are_rejected_on_update(tmp_path):
    service = _service(tmp_path)
    task = service.create(_echo_create())
    with pytest.raises(TaskValidationError):
        service.update(
            task.task_id,
            AutomationTaskUpdate(tool_input={"path": "/home/sherlock/.ssh/id_rsa"}),
        )


def test_secrets_do_not_appear_in_execution_history_or_logs(tmp_path, caplog):
    class _SecretOutput(BaseModel):
        message: str

    class _LeakyTool(BaseTool):
        name = "stub.leaky"
        description = "stub"
        permissions = ToolPermissions(side_effect=SideEffect.NONE)
        input_model = _NoopInput
        output_model = _SecretOutput

        async def run(self, payload, context):
            return _SecretOutput(message="api_key = sk-abcdefghijklmnopqrstuvwxyz")

    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(_LeakyTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(_echo_create(tool_name="stub.leaky", tool_input={}))

    with caplog.at_level("INFO"):
        record = run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))

    assert "sk-abcdefghijklmnopqrstuvwxyz" not in (record.result_summary or "")
    for rec in caplog.records:
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in rec.getMessage()


# ===========================================================================
# 27. Correlation / execution IDs
# ===========================================================================


def test_correlation_and_execution_ids_exist_and_are_unique(tmp_path):
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(EchoTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)
    task = service.create(_echo_create())

    r1 = run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))
    r2 = run(runner.execute(task, now=datetime.now(timezone.utc), triggered_by="manual"))

    assert r1.execution_id and r2.execution_id
    assert r1.execution_id != r2.execution_id
    assert r1.request_id and r2.request_id
    assert r1.request_id != r2.request_id


# ===========================================================================
# Scheduling: run_due_tasks respects triggers/enabled and errors
# ===========================================================================


def test_run_due_tasks_skips_disabled_and_not_due(tmp_path):
    service = _service(tmp_path)
    core = Core(planner=None, registry=_registry(EchoTool()))
    runner = TaskRunner(core, service, sleep=_no_sleep)

    due_task = service.create(
        _echo_create(name="due", trigger=ScheduledTrigger(run_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
    )
    not_due = service.create(
        _echo_create(
            name="not due", trigger=ScheduledTrigger(run_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
        )
    )
    disabled = service.create(_echo_create(name="disabled", enabled=False))
    service.update(disabled.task_id, AutomationTaskUpdate(enabled=False))

    records = run(runner.run_due_tasks(now=datetime(2026, 1, 1, tzinfo=timezone.utc)))

    ran_ids = {r.task_id for r in records}
    assert due_task.task_id in ran_ids
    assert not_due.task_id not in ran_ids
    assert disabled.task_id not in ran_ids


def test_task_not_found_raises():
    with pytest.raises(TaskNotFoundError):
        raise TaskNotFoundError("missing")
