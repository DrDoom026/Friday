"""Read-only filesystem tools: list, stat and search.

None of these modify anything. They still route every path through the policy
and audit every attempt, because "read" is a decision too.
"""

import os
from fnmatch import fnmatchcase
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.registry import register_tool
from app.tools.filesystem.base import (
    READ_PERMISSIONS,
    FileEntry,
    FilesystemTool,
    PathInput,
    _iso,
    describe,
)

# --- fs.list ---------------------------------------------------------------


class ListInput(PathInput):
    include_hidden: bool = Field(False, description="Include dot-prefixed entries.")
    max_entries: int | None = Field(
        None, ge=1, description="Cap on returned entries. Clamped to the policy limit."
    )


class ListOutput(BaseModel):
    path: str
    entries: list[FileEntry]
    entry_count: int
    truncated: bool = False


@register_tool
class ListDirectoryTool(FilesystemTool):
    name = "fs.list"
    operation = "list"
    description = "List the contents of a directory inside an allowed root."
    version = "1.0.0"
    permissions = READ_PERMISSIONS
    input_model = ListInput
    output_model = ListOutput

    async def operate(self, payload: ListInput, context: ToolExecutionContext) -> ListOutput:
        target = self.require_directory(self.resolve(payload.path), payload.path)

        limit = self.policy.limits.max_list_entries
        if payload.max_entries is not None:
            limit = min(limit, payload.max_entries)

        names = sorted(os.listdir(target))
        if not payload.include_hidden:
            names = [n for n in names if not n.startswith(".")]

        entries = [describe(target / name) for name in names[:limit]]
        return ListOutput(
            path=str(target),
            entries=entries,
            entry_count=len(entries),
            truncated=len(names) > limit,
        )


# --- fs.stat ---------------------------------------------------------------


class StatOutput(BaseModel):
    path: str
    exists: bool
    type: str
    size_bytes: int
    modified: str
    accessed: str
    changed: str
    mode: str = Field(..., description="Permission bits in octal, e.g. '0o644'.")
    is_symlink: bool
    symlink_target: str | None = Field(
        None, description="Only reported when the target is itself inside an allowed root."
    )
    entry_count: int | None = Field(None, description="Number of entries, for directories.")


@register_tool
class StatTool(FilesystemTool):
    name = "fs.stat"
    operation = "stat"
    description = "Return metadata for a file or directory inside an allowed root."
    version = "1.0.0"
    permissions = READ_PERMISSIONS
    input_model = PathInput
    output_model = StatOutput

    async def operate(self, payload: PathInput, context: ToolExecutionContext) -> StatOutput:
        target = self.require_exists(self.resolve(payload.path), payload.path)
        entry = describe(target)
        info = target.lstat()

        symlink_target: str | None = None
        if entry.is_symlink:
            literal = os.readlink(target)
            resolved = literal if os.path.isabs(literal) else str(target.parent / literal)
            if self.policy.is_allowed(resolved):
                symlink_target = resolved

        entry_count = None
        if entry.type == "directory":
            entry_count = len(os.listdir(target))

        return StatOutput(
            path=str(target),
            exists=True,
            type=entry.type,
            size_bytes=entry.size_bytes,
            modified=entry.modified,
            accessed=_iso(info.st_atime),
            changed=_iso(info.st_ctime),
            mode=oct(info.st_mode & 0o7777),
            is_symlink=entry.is_symlink,
            symlink_target=symlink_target,
            entry_count=entry_count,
        )


# --- fs.search -------------------------------------------------------------


class SearchInput(BaseModel):
    path: str = Field(..., min_length=1, description="Absolute directory to search under.")
    pattern: str = Field("*", min_length=1, description="Glob matched against entry names.")
    recursive: bool = True
    case_sensitive: bool = False
    include_files: bool = True
    include_directories: bool = False
    max_results: int | None = Field(None, ge=1, description="Clamped to the policy limit.")
    max_depth: int | None = Field(None, ge=0, description="Clamped to the policy limit.")


class SearchOutput(BaseModel):
    path: str
    pattern: str
    matches: list[FileEntry]
    match_count: int
    truncated: bool = False
    directories_scanned: int = 0


@register_tool
class SearchTool(FilesystemTool):
    name = "fs.search"
    operation = "search"
    description = "Find files or directories by name pattern under an allowed root."
    version = "1.0.0"
    permissions = READ_PERMISSIONS
    input_model = SearchInput
    output_model = SearchOutput

    async def operate(self, payload: SearchInput, context: ToolExecutionContext) -> SearchOutput:
        root = self.require_directory(self.resolve(payload.path), payload.path)

        limits = self.policy.limits
        max_results = limits.max_search_results
        if payload.max_results is not None:
            max_results = min(max_results, payload.max_results)
        max_depth = limits.max_search_depth if payload.recursive else 0
        if payload.max_depth is not None:
            max_depth = min(max_depth, payload.max_depth)

        pattern = payload.pattern if payload.case_sensitive else payload.pattern.lower()
        matches: list[FileEntry] = []
        scanned = 0
        truncated = False

        for current, dirnames, filenames in os.walk(root, followlinks=False):
            scanned += 1
            here = Path(current)
            depth = len(here.relative_to(root).parts)

            # Never descend into anything the policy would refuse, and stop at max depth.
            dirnames[:] = sorted(
                name for name in dirnames if self.policy.is_allowed(str(here / name))
            )
            if depth >= max_depth:
                dirnames[:] = []

            candidates: list[str] = []
            if payload.include_directories:
                candidates.extend(dirnames)
            if payload.include_files:
                candidates.extend(sorted(filenames))

            for name in candidates:
                probe = name if payload.case_sensitive else name.lower()
                if not fnmatchcase(probe, pattern):
                    continue
                candidate = here / name
                if not self.policy.is_allowed(str(candidate)):
                    continue
                if len(matches) >= max_results:
                    truncated = True
                    break
                matches.append(describe(candidate))

            if truncated:
                break

        return SearchOutput(
            path=str(root),
            pattern=payload.pattern,
            matches=matches,
            match_count=len(matches),
            truncated=truncated,
            directories_scanned=scanned,
        )
