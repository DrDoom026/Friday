"""Persistent JSON task registry (PART 15).

Deliberately not the Part 8 vault: automation task definitions are
structured, machine-managed config, not human conversational memory, so
dumping them into Markdown notes would blur that boundary for no benefit.
A dedicated JSON file under an approved persistent path is the simplest safe
mechanism that satisfies "survives a container restart" without a database.

Writes are atomic (temp file + ``os.replace``) so a crash mid-write cannot
leave a half-written, corrupt task file behind.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

from app.automation.errors import AutomationStoreError
from app.automation.models import AutomationTask

logger = logging.getLogger("firday.automation.store")


class AutomationStore:
    """Loads/saves the full task list as one JSON array at ``path``."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[AutomationTask]:
        """Read every persisted task. Missing file -> no tasks yet (cold start)."""
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AutomationStoreError(
                f"automation task store at {self._path} is unreadable/corrupt: {exc}"
            ) from exc
        try:
            return [AutomationTask.model_validate(item) for item in raw]
        except Exception as exc:  # noqa: BLE001 - surfaced as one clear store error
            raise AutomationStoreError(
                f"automation task store at {self._path} contains an invalid task: {exc}"
            ) from exc

    def save(self, tasks: list[AutomationTask]) -> None:
        """Atomically overwrite the store with ``tasks``."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([t.model_dump(mode="json") for t in tasks], indent=2)

        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            with_cleanup = Path(tmp_path)
            if with_cleanup.exists():
                with_cleanup.unlink(missing_ok=True)
            raise AutomationStoreError(
                f"could not write automation task store at {self._path}: {exc}"
            ) from exc
        logger.debug("automation task store saved (path=%s, tasks=%d)", self._path, len(tasks))
