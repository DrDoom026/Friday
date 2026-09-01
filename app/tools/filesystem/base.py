"""Shared base class and helpers for the filesystem tools.

Every filesystem tool is an ordinary Part 2 tool: it declares an input model,
an output model and its permissions, and implements one method. What this base
adds on top of :class:`~app.core.tools.BaseTool` is the two things no
filesystem tool may skip - resolving each path through the
:class:`~app.fs.policy.FilesystemPolicy`, and emitting exactly one audit record
per attempt, carrying the request's correlation ID.
"""

import os
from abc import abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.errors import ToolExecutionError
from app.core.tools import BaseTool, SideEffect, ToolPermissions
from app.fs.audit import record_attempt
from app.fs.errors import FilesystemPolicyError
from app.fs.policy import FilesystemPolicy, get_default_policy

READ_PERMISSIONS = ToolPermissions(
    side_effect=SideEffect.READ,
    scopes=("fs.read",),
    filesystem_access=True,
)

WRITE_PERMISSIONS = ToolPermissions(
    side_effect=SideEffect.WRITE,
    scopes=("fs.write",),
    filesystem_access=True,
)

DESTRUCTIVE_PERMISSIONS = ToolPermissions(
    side_effect=SideEffect.WRITE,
    scopes=("fs.write", "fs.destructive"),
    requires_confirmation=True,
    filesystem_access=True,
)

PathKind = Literal["file", "directory", "symlink", "other"]


class FileEntry(BaseModel):
    """Metadata for one path. Symlinks are described, never followed."""

    name: str
    path: str
    type: PathKind
    size_bytes: int
    modified: str
    is_symlink: bool = False


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def classify(stat_result: os.stat_result, *, is_symlink: bool) -> PathKind:
    import stat as stat_module

    if is_symlink:
        return "symlink"
    if stat_module.S_ISDIR(stat_result.st_mode):
        return "directory"
    if stat_module.S_ISREG(stat_result.st_mode):
        return "file"
    return "other"


def describe(path: Path) -> FileEntry:
    """Build a :class:`FileEntry` from ``lstat`` - the link itself, not its target."""
    info = path.lstat()
    is_symlink = path.is_symlink()
    return FileEntry(
        name=path.name or str(path),
        path=str(path),
        type=classify(info, is_symlink=is_symlink),
        size_bytes=info.st_size,
        modified=_iso(info.st_mtime),
        is_symlink=is_symlink,
    )


class FilesystemTool(BaseTool):
    """Base for every filesystem tool: policy-checked paths, audited attempts."""

    operation: ClassVar[str]

    def __init__(self, policy: FilesystemPolicy | None = None) -> None:
        self._policy = policy

    @property
    def policy(self) -> FilesystemPolicy:
        """The sandbox this tool resolves against - the process default unless injected."""
        return self._policy if self._policy is not None else get_default_policy()

    # --- path helpers ------------------------------------------------------

    def resolve(self, raw: str) -> Path:
        """Canonicalize and authorize one path, or raise a policy error."""
        return self.policy.resolve(raw)

    def require_exists(self, path: Path, raw: str) -> Path:
        if not path.exists() and not path.is_symlink():
            raise ToolExecutionError(self.name, f"{raw!r} does not exist")
        return path

    def require_directory(self, path: Path, raw: str) -> Path:
        self.require_exists(path, raw)
        if not path.is_dir():
            raise ToolExecutionError(self.name, f"{raw!r} is not a directory")
        return path

    def require_file(self, path: Path, raw: str) -> Path:
        self.require_exists(path, raw)
        if not path.is_file():
            raise ToolExecutionError(self.name, f"{raw!r} is not a regular file")
        return path

    # --- execution template ------------------------------------------------

    def audited_paths(self, payload: Any) -> list[str]:
        """Raw paths this invocation is about. Overridden by two-path tools."""
        return [str(getattr(payload, "path", ""))]

    async def run(self, payload: Any, context: ToolExecutionContext) -> BaseModel:
        raw_paths = self.audited_paths(payload)
        try:
            output = await self.operate(payload, context)
        except FilesystemPolicyError as exc:
            self._audit(context, raw_paths, allowed=False, outcome="denied", detail=str(exc))
            raise ToolExecutionError(self.name, str(exc)) from exc
        except OSError as exc:
            detail = f"{type(exc).__name__}: {exc.strerror or exc}"
            self._audit(context, raw_paths, allowed=True, outcome="error", detail=detail)
            raise ToolExecutionError(self.name, detail) from exc
        except Exception as exc:  # noqa: BLE001 - audit, then let the framework handle it
            self._audit(
                context,
                raw_paths,
                allowed=True,
                outcome="error",
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise

        self._audit(context, raw_paths, allowed=True, outcome="success")
        return output

    @abstractmethod
    async def operate(self, payload: Any, context: ToolExecutionContext) -> BaseModel:
        """Do the filesystem work. Paths must still be resolved through the policy."""

    def _audit(
        self,
        context: ToolExecutionContext,
        raw_paths: list[str],
        *,
        allowed: bool,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        resolved = [p for p in (self.policy.try_resolve(raw) for raw in raw_paths) if p]
        record_attempt(
            context,
            operation=self.operation,
            paths=raw_paths,
            resolved_paths=resolved,
            allowed=allowed,
            outcome=outcome,
            detail=detail,
        )


class PathInput(BaseModel):
    """The common single-path input."""

    path: str = Field(..., min_length=1, description="Absolute path inside an allowed root.")
