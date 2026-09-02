"""Audit trail for security / permission decisions.

Every tool evaluation - whether allowed, denied, or requiring confirmation -
produces an audit log record on the ``firday.security.audit`` logger carrying
correlation ID, device, tool, permission level, decision, and rationale.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging

from app.core.context import ToolExecutionContext
from app.security.models import PermissionLevel, SecurityDecision

SECURITY_AUDIT_LOGGER = "firday.security.audit"


@dataclass(frozen=True)
class SecurityAuditEvent:
    """One security engine evaluation, as recorded."""

    tool_name: str
    operation: str
    request_id: str
    invocation_id: str
    device_id: str
    trust_level: str
    permission_level: str
    decision: str
    target: str | None = None
    reason: str | None = None
    policy_applied: str = "default"
    timestamp: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def message(self) -> str:
        parts = [
            "security audit",
            f"tool={self.tool_name}",
            f"op={self.operation or '-'}",
            f"device={self.device_id}",
            f"trust={self.trust_level}",
            f"level={self.permission_level}",
            f"decision={self.decision}",
            f"target={self.target or '-'}",
            f"policy={self.policy_applied}",
            f"invocation_id={self.invocation_id}",
        ]
        if self.reason:
            parts.append(f"reason={self.reason}")
        return " ".join(parts)


def record_security_decision(
    context: ToolExecutionContext,
    *,
    tool_name: str,
    operation: str = "",
    device_id: str = "local",
    trust_level: str = "unverified",
    permission_level: PermissionLevel,
    decision: SecurityDecision,
    target: str | None = None,
    reason: str | None = None,
    policy_applied: str = "default",
) -> SecurityAuditEvent:
    """Log one security evaluation attempt and return the record that was written."""
    event = SecurityAuditEvent(
        tool_name=tool_name,
        operation=operation,
        request_id=context.request_id,
        invocation_id=context.invocation_id,
        device_id=device_id,
        trust_level=trust_level,
        permission_level=permission_level.value,
        decision=decision.value,
        target=target,
        reason=reason,
        policy_applied=policy_applied,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    log = context.logger(SECURITY_AUDIT_LOGGER)
    extra = {"security_audit": event.as_dict()}

    if decision == SecurityDecision.ALLOW:
        log.info(event.message(), extra=extra)
    else:
        log.warning(event.message(), extra=extra)

    return event
