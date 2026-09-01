"""Filesystem tools (PART 4).

Importing this package registers all ten operations. Seven of them execute,
subject to the sandbox in :mod:`app.fs.policy`:

    fs.list  fs.stat  fs.search  fs.read  fs.write  fs.mkdir  fs.copy

The remaining three are registered and schema-complete but disabled - their
authorization stub always denies until PART 7:

    fs.delete  fs.move  fs.rename
"""

from app.tools.filesystem.base import FilesystemTool
from app.tools.filesystem.content import ReadFileTool, WriteFileTool
from app.tools.filesystem.destructive import (
    BLOCKED_UNTIL,
    NOT_AUTHORIZED_REASON,
    DeleteTool,
    DestructiveFilesystemTool,
    MoveTool,
    NotAuthorizedOutput,
    RenameTool,
)
from app.tools.filesystem.inspect import ListDirectoryTool, SearchTool, StatTool
from app.tools.filesystem.manage import CopyTool, MakeDirectoryTool

#: Tools that actually execute in Part 4.
ENABLED_TOOL_NAMES = (
    "fs.copy",
    "fs.list",
    "fs.mkdir",
    "fs.read",
    "fs.search",
    "fs.stat",
    "fs.write",
)

#: Tools that are registered but always refuse until Part 7.
DISABLED_TOOL_NAMES = ("fs.delete", "fs.move", "fs.rename")

__all__ = [
    "BLOCKED_UNTIL",
    "NOT_AUTHORIZED_REASON",
    "CopyTool",
    "DeleteTool",
    "DestructiveFilesystemTool",
    "DISABLED_TOOL_NAMES",
    "ENABLED_TOOL_NAMES",
    "FilesystemTool",
    "ListDirectoryTool",
    "MakeDirectoryTool",
    "MoveTool",
    "NotAuthorizedOutput",
    "ReadFileTool",
    "RenameTool",
    "SearchTool",
    "StatTool",
    "WriteFileTool",
]
