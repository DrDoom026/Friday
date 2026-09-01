"""Audit trail for filesystem operations.

Every attempt - allowed or denied, successful or failed - produces exactly one
record on the ``firday.fs.audit`` logger. Records are emitted through the
request context's logger adapter, so each one carries the same correlation ID
the request has had since Part 1.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.context import ToolExecutionContext

AUDIT_LOGGER = "firday.fs.audit"


@dataclass(frozen=True)
class AuditEvent:
    """One filesystem operation attempt, as recorded."""

    operation: str
    tool_name: str
    request_id: str
    invocation_id: str
    paths: tuple[str, ...]
    resolved_paths: tuple[str, ...]
    allowed: bool
    outcome: str
    detail: str | None = None
    timestamp: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def message(self) -> str:
        parts = [
            "fs audit",
            f"op={self.operation}",
            f"tool={self.tool_name}",
            f"decision={'allowed' if self.allowed else 'denied'}",
            f"outcome={self.outcome}",
            f"path={'|'.join(self.paths) or '-'}",
            f"resolved={'|'.join(self.resolved_paths) or '-'}",
            f"invocation_id={self.invocation_id}",
        ]
        if self.detail:
            parts.append(f"detail={self.detail}")
        return " ".join(parts)


def record_attempt(
    context: ToolExecutionContext,
    *,
    operation: str,
    paths: "list[str] | tuple[str, ...]",
    allowed: bool,
    outcome: str,
    resolved_paths: "list[Path | str] | tuple[Path | str, ...] | None" = None,
    detail: str | None = None,
) -> AuditEvent:
    """Log one filesystem attempt and return the record that was written.

    Denials and errors are logged at WARNING so they stand out in the stream;
    successful operations are logged at INFO.
    """
    event = AuditEvent(
        operation=operation,
        tool_name=context.tool_name,
        request_id=context.request_id,
        invocation_id=context.invocation_id,
        paths=tuple(str(p) for p in paths),
        resolved_paths=tuple(str(p) for p in (resolved_paths or ())),
        allowed=allowed,
        outcome=outcome,
        detail=detail,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    log = context.logger(AUDIT_LOGGER)
    extra = {"fs_audit": event.as_dict()}
    if allowed and outcome == "success":
        log.info(event.message(), extra=extra)
    else:
        log.warning(event.message(), extra=extra)
    return event
