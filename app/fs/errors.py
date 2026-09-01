"""Errors raised by the filesystem policy.

All of them derive from ``ToolError`` so the tool framework's existing
handling applies, and from ``FilesystemPolicyError`` so a tool can catch the
whole family in one clause and audit it as a denial.
"""

from app.core.errors import ToolError


class FilesystemPolicyError(ToolError):
    """A filesystem request was refused before it could touch the disk."""


class PathNotAllowedError(FilesystemPolicyError):
    """A path resolved outside every allowed root."""

    def __init__(self, path: str, reason: str = "resolves outside the allowed roots") -> None:
        self.path = path
        super().__init__(f"path {path!r} is not allowed: {reason}")


class PathTraversalError(PathNotAllowedError):
    """A path used ``..`` (or an equivalent trick) to climb out of a root."""

    def __init__(self, path: str, reason: str = "path traversal is not permitted") -> None:
        super().__init__(path, reason)


class SystemPathError(PathNotAllowedError):
    """A path landed on a protected system location, allowed root or not."""

    def __init__(self, path: str, protected: str) -> None:
        self.protected = protected
        super().__init__(path, f"{protected!r} is a protected system path")


class FileTooLargeError(FilesystemPolicyError):
    """An operation exceeded a configured size limit."""

    def __init__(self, path: str, size: int, limit: int) -> None:
        self.path = path
        self.size = size
        self.limit = limit
        super().__init__(
            f"{path!r} is {size} bytes, which exceeds the {limit} byte limit for this operation"
        )


class OperationNotAuthorizedError(FilesystemPolicyError):
    """A destructive operation was refused: nothing can authorize it yet."""

    def __init__(self, operation: str, reason: str) -> None:
        self.operation = operation
        self.reason = reason
        super().__init__(f"operation {operation!r} is not yet authorized: {reason}")
