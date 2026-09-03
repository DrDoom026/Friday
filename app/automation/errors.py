"""Automation-specific error types (PART 15)."""


class AutomationError(Exception):
    """Base class for every automation-engine failure."""


class TaskNotFoundError(AutomationError):
    """A task was looked up by an id the store does not know."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"no automation task registered under id {task_id!r}")


class TaskValidationError(AutomationError):
    """A task definition is invalid - including embedding a secret."""


class AutomationStoreError(AutomationError):
    """The persistent task store could not be read or written."""
