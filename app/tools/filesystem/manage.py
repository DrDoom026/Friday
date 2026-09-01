"""Additive filesystem tools: mkdir and copy.

Both only ever add to the filesystem. Copy refuses to overwrite an existing
destination outright - replacing something is destructive, and destructive
operations wait for the permission engine in Part 7.
"""

import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.errors import ToolExecutionError
from app.core.registry import register_tool
from app.tools.filesystem.base import WRITE_PERMISSIONS, FilesystemTool, PathInput

# --- fs.mkdir --------------------------------------------------------------


class MkdirInput(PathInput):
    parents: bool = Field(False, description="Create missing intermediate directories.")
    exist_ok: bool = Field(False, description="Succeed instead of failing if it already exists.")


class MkdirOutput(BaseModel):
    path: str
    created: bool
    existed: bool


@register_tool
class MakeDirectoryTool(FilesystemTool):
    name = "fs.mkdir"
    operation = "mkdir"
    description = "Create a directory inside an allowed root."
    version = "1.0.0"
    permissions = WRITE_PERMISSIONS
    input_model = MkdirInput
    output_model = MkdirOutput

    async def operate(self, payload: MkdirInput, context: ToolExecutionContext) -> MkdirOutput:
        target = self.resolve(payload.path)

        if target.exists():
            if not payload.exist_ok:
                raise ToolExecutionError(self.name, f"{payload.path!r} already exists")
            if not target.is_dir():
                raise ToolExecutionError(self.name, f"{payload.path!r} is not a directory")
            return MkdirOutput(path=str(target), created=False, existed=True)

        if not payload.parents and not target.parent.exists():
            raise ToolExecutionError(
                self.name,
                f"parent directory {str(target.parent)!r} does not exist; pass parents=true",
            )

        target.mkdir(parents=payload.parents, exist_ok=payload.exist_ok)
        return MkdirOutput(path=str(target), created=True, existed=False)


# --- fs.copy ---------------------------------------------------------------


class CopyInput(BaseModel):
    source: str = Field(..., min_length=1, description="Absolute path to copy from.")
    destination: str = Field(..., min_length=1, description="Absolute path to copy to.")
    create_parents: bool = Field(False, description="Create the destination's parents.")


class CopyOutput(BaseModel):
    source: str
    destination: str
    type: str
    files_copied: int
    bytes_copied: int


@register_tool
class CopyTool(FilesystemTool):
    name = "fs.copy"
    operation = "copy"
    description = (
        "Copy a file or directory to a new path inside an allowed root. "
        "Refuses to overwrite an existing destination."
    )
    version = "1.0.0"
    permissions = WRITE_PERMISSIONS
    input_model = CopyInput
    output_model = CopyOutput

    def audited_paths(self, payload: CopyInput) -> list[str]:
        return [payload.source, payload.destination]

    async def operate(self, payload: CopyInput, context: ToolExecutionContext) -> CopyOutput:
        source = self.require_exists(self.resolve(payload.source), payload.source)
        destination = self.resolve(payload.destination)

        if destination.exists():
            raise ToolExecutionError(
                self.name,
                f"{payload.destination!r} already exists; copy will not overwrite",
            )
        if source == destination:
            raise ToolExecutionError(self.name, "source and destination are the same path")

        parent = destination.parent
        if not parent.exists():
            if not payload.create_parents:
                raise ToolExecutionError(
                    self.name,
                    f"destination parent {str(parent)!r} does not exist; "
                    "pass create_parents=true to create it",
                )
            self.resolve(str(parent))
            parent.mkdir(parents=True, exist_ok=True)

        if source.is_dir():
            if destination.is_relative_to(source):
                raise ToolExecutionError(
                    self.name, "destination is inside the source directory"
                )
            files, total = self._measure_tree(source)
            self.policy.enforce_size(source, total, self.policy.limits.max_copy_bytes)
            shutil.copytree(source, destination, symlinks=True, ignore=self._ignore_denied)
            return CopyOutput(
                source=str(source),
                destination=str(destination),
                type="directory",
                files_copied=files,
                bytes_copied=total,
            )

        size = source.stat().st_size
        self.policy.enforce_size(source, size, self.policy.limits.max_copy_bytes)
        shutil.copy2(source, destination)
        return CopyOutput(
            source=str(source),
            destination=str(destination),
            type="file",
            files_copied=1,
            bytes_copied=size,
        )

    def _ignore_denied(self, directory: str, names: list[str]) -> set[str]:
        """Skip anything the policy refuses - notably symlinks pointing outside."""
        return {name for name in names if not self.policy.is_allowed(os.path.join(directory, name))}

    def _measure_tree(self, root: Path) -> tuple[int, int]:
        files = 0
        total = 0
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(current)
            dirnames[:] = [d for d in dirnames if self.policy.is_allowed(str(here / d))]
            for name in filenames:
                candidate = here / name
                if not self.policy.is_allowed(str(candidate)):
                    continue
                files += 1
                total += candidate.lstat().st_size
        return files, total
