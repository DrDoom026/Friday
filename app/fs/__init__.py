"""Filesystem security layer.

Everything a filesystem tool needs before it is allowed to touch a byte:
the path policy (allowed roots, traversal defence, protected system paths,
size limits), the audit trail that records every attempt, and the startup
check that refuses to boot on an unusable sandbox.
"""

from app.fs.audit import AuditEvent, record_attempt
from app.fs.bootstrap import SandboxConfigurationError, ensure_sandbox_ready
from app.fs.errors import (
    FileTooLargeError,
    FilesystemPolicyError,
    OperationNotAuthorizedError,
    PathNotAllowedError,
    PathTraversalError,
    SystemPathError,
)
from app.fs.policy import (
    DEFAULT_DENIED_NAMES,
    DEFAULT_SYSTEM_PATHS,
    FilesystemLimits,
    FilesystemPolicy,
    get_default_policy,
)

__all__ = [
    "AuditEvent",
    "record_attempt",
    "SandboxConfigurationError",
    "ensure_sandbox_ready",
    "DEFAULT_DENIED_NAMES",
    "DEFAULT_SYSTEM_PATHS",
    "FileTooLargeError",
    "FilesystemLimits",
    "FilesystemPolicy",
    "FilesystemPolicyError",
    "OperationNotAuthorizedError",
    "PathNotAllowedError",
    "PathTraversalError",
    "SystemPathError",
    "get_default_policy",
]
