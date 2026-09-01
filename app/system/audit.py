"""Audit trail for system operations.

The same contract Part 4 established for the filesystem, applied to processes,
services, containers, the network and git: every attempt - allowed or denied,
successful or failed - produces exactly one record, on the
``firday.system.audit`` logger, carrying the request's correlation ID.

It is a sibling of :mod:`app.fs.audit` rather than a generalization of it. The
two record different things - a filesystem attempt is about paths, a system
attempt is about a pid, a unit, a container or a repository - and collapsing
them into one shape would make both vaguer than they are.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from app.core.context import ToolExecutionContext

SYSTEM_AUDIT_LOGGER = "firday.system.audit"


@dataclass(frozen=True)
class SystemAuditEvent:
    """One system operation attempt, as recorded."""

    domain: str
    operation: str
    tool_name: str
    request_id: str
    invocation_id: str
    targets: tuple[str, ...]
    allowed: bool
    outcome: str
    detail: str | None = None
    timestamp: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def message(self) -> str:
        parts = [
            "system audit",
            f"domain={self.domain}",
            f"op={self.operation}",
            f"tool={self.tool_name}",
            f"decision={'allowed' if self.allowed else 'denied'}",
            f"outcome={self.outcome}",
            f"target={'|'.join(self.targets) or '-'}",
            f"invocation_id={self.invocation_id}",
        ]
        if self.detail:
            parts.append(f"detail={self.detail}")
        return " ".join(parts)


def record_attempt(
    context: ToolExecutionContext,
    *,
    domain: str,
    operation: str,
    targets: "list[str] | tuple[str, ...] | None" = None,
    allowed: bool,
    outcome: str,
    detail: str | None = None,
) -> SystemAuditEvent:
    """Log one system operation attempt and return the record that was written.

    Denials and errors are logged at WARNING so they stand out in the stream;
    successful operations are logged at INFO.
    """
    event = SystemAuditEvent(
        domain=domain,
        operation=operation,
        tool_name=context.tool_name,
        request_id=context.request_id,
        invocation_id=context.invocation_id,
        targets=tuple(str(t) for t in (targets or ())),
        allowed=allowed,
        outcome=outcome,
        detail=detail,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    log = context.logger(SYSTEM_AUDIT_LOGGER)
    extra = {"system_audit": event.as_dict()}
    if allowed and outcome == "success":
        log.info(event.message(), extra=extra)
    else:
        log.warning(event.message(), extra=extra)
    return event
