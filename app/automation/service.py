"""Automation task CRUD + persistence (PART 15).

Owns the in-memory task map and is the only writer of the JSON store, so
every mutation (create/update/delete/enable/disable/history append) is
persisted and logged in one place. Contains no scheduling, security, or tool
logic - that is the runner's job (:mod:`app.automation.runner`).
"""

import json
import logging
from datetime import datetime, timezone

from app.automation.errors import TaskNotFoundError, TaskValidationError
from app.automation.models import (
    AutomationTask,
    AutomationTaskCreate,
    AutomationTaskUpdate,
    ExecutionRecord,
)
from app.automation.store import AutomationStore
from app.llm.privacy import is_sensitive

logger = logging.getLogger("firday.automation")


def _reject_secrets(name: str, description: str, tool_input: dict) -> None:
    """Refuse to persist a task whose definition embeds a credential/secret.

    Reuses the existing Part 9 privacy gate rather than a second detector -
    a task definition is exactly the kind of place a pasted API key or path
    would otherwise end up sitting in cleartext on disk.
    """
    haystack = f"{name}\n{description}\n{json.dumps(tool_input, default=str)}"
    if is_sensitive(haystack):
        raise TaskValidationError(
            "task definition appears to contain a credential/secret and was rejected; "
            "secrets belong in environment configuration, never in a task definition"
        )


class AutomationService:
    """CRUD over :class:`AutomationTask`, backed by an :class:`AutomationStore`."""

    def __init__(self, store: AutomationStore) -> None:
        self._store = store
        self._tasks: dict[str, AutomationTask] = {}
        self.reload()

    def reload(self) -> None:
        """(Re)load tasks from disk. Used at startup and to simulate a restart."""
        self._tasks = {t.task_id: t for t in self._store.load()}
        logger.info("automation tasks loaded (count=%d)", len(self._tasks))

    def _save(self) -> None:
        self._store.save(list(self._tasks.values()))

    def list(self) -> list[AutomationTask]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at)

    def get(self, task_id: str) -> AutomationTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def try_get(self, task_id: str) -> AutomationTask | None:
        return self._tasks.get(task_id)

    def create(self, payload: AutomationTaskCreate) -> AutomationTask:
        _reject_secrets(payload.name, payload.description, payload.tool_input)
        task = AutomationTask(**payload.model_dump())
        self._tasks[task.task_id] = task
        self._save()
        logger.info("automation task created (task_id=%s, name=%s)", task.task_id, task.name)
        return task

    def update(self, task_id: str, payload: AutomationTaskUpdate) -> AutomationTask:
        task = self.get(task_id)
        changes = payload.model_dump(exclude_unset=True)

        name = changes.get("name", task.name)
        description = changes.get("description", task.description)
        tool_input = changes.get("tool_input", task.tool_input)
        _reject_secrets(name, description, tool_input)

        updated = task.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
        self._tasks[task_id] = updated
        self._save()
        if "enabled" in changes and changes["enabled"] != task.enabled:
            logger.info(
                "automation task %s (task_id=%s)",
                "enabled" if changes["enabled"] else "disabled",
                task_id,
            )
        else:
            logger.info("automation task updated (task_id=%s)", task_id)
        return updated

    def delete(self, task_id: str) -> None:
        if task_id not in self._tasks:
            raise TaskNotFoundError(task_id)
        del self._tasks[task_id]
        self._save()
        logger.info("automation task deleted (task_id=%s)", task_id)

    def set_last_run(self, task_id: str, when: datetime) -> None:
        task = self.get(task_id)
        task.last_run_at = when
        self._save()

    def append_history(self, task_id: str, record: ExecutionRecord) -> None:
        task = self.get(task_id)
        task.record(record)
        self._save()
