"""Security Engine: the authorization gate between Core and tool execution.

Replaces the hardcoded "always refuse" stubs from Part 4/6 with real security
decisions based on tool permissions, device trust, and policy rules.
"""

from typing import Any, Mapping

from app.core.context import ToolExecutionContext
from app.core.tools import Tool, ToolPermissions
from app.devices.models import Device, TrustState
from app.security.audit import record_security_decision
from app.security.models import SecurityContext, SecurityDecision, SecurityEvaluation
from app.security.policy import BaseSecurityPolicy, DefaultSecurityPolicy


class SecurityEngine:
    """The decision layer that authorizes every tool execution.

    Evaluates a tool invocation request against policy, logs the decision, and
    returns ALLOW / DENY / REQUIRE_CONFIRMATION.
    """

    def __init__(self, policy: BaseSecurityPolicy | None = None) -> None:
        self._policy = policy if policy is not None else DefaultSecurityPolicy()

    @property
    def policy(self) -> BaseSecurityPolicy:
        return self._policy

    def authorize(
        self,
        tool: Tool,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
        *,
        device: Device | None = None,
    ) -> SecurityEvaluation:
        """Evaluate whether a tool invocation is authorized.

        Args:
            tool: The tool being invoked.
            arguments: The arguments the tool would receive.
            context: Request-scoped execution context.
            device: The device executing the tool (defaults to local/unverified).

        Returns:
            A SecurityEvaluation containing the decision and reasoning.
        """
        # Extract operation from tool metadata if available
        operation = getattr(tool, "operation", "")

        # Build security context from device and request
        device_id = device.device_id if device else "local"
        trust_level = device.trust.value if device else TrustState.UNVERIFIED.value

        # Extract target from arguments (best effort)
        target = self._extract_target(arguments)

        sec_context = SecurityContext(
            tool_name=tool.name,
            device_id=device_id,
            trust_level=trust_level,
            request_id=context.request_id,
            target=target,
        )

        # Evaluate against policy
        evaluation = self._policy.evaluate(tool.permissions, sec_context)

        # Audit the decision
        record_security_decision(
            context,
            tool_name=tool.name,
            operation=operation,
            device_id=device_id,
            trust_level=trust_level,
            permission_level=evaluation.permission_level,
            decision=evaluation.decision,
            target=target,
            reason=evaluation.reason,
            policy_applied=evaluation.policy_applied,
        )

        return evaluation

    def check_tool_authorized(
        self,
        tool: Tool,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
        *,
        device: Device | None = None,
    ) -> bool:
        """Quick authorization check that returns True only for ALLOW decisions.

        This is the method destructive tools should call in their is_authorized() stub.
        REQUIRE_CONFIRMATION is treated as unauthorized when no confirmation channel exists.
        """
        evaluation = self.authorize(tool, arguments, context, device=device)
        return evaluation.decision == SecurityDecision.ALLOW

    @staticmethod
    def _extract_target(arguments: Mapping[str, Any]) -> str | None:
        """Best-effort extraction of what the tool would act on."""
        # Common argument names for targets
        for key in ("path", "pid", "unit", "container_id", "source", "destination", "repository"):
            if key in arguments:
                value = arguments[key]
                if isinstance(value, (str, int)):
                    return str(value)
        return None


# --- Global engine instance -------------------------------------------------

_global_engine: SecurityEngine | None = None


def get_security_engine() -> SecurityEngine:
    """Get the process-wide Security Engine instance."""
    global _global_engine
    if _global_engine is None:
        _global_engine = SecurityEngine()
    return _global_engine


def set_security_engine(engine: SecurityEngine) -> None:
    """Replace the global Security Engine (primarily for testing)."""
    global _global_engine
    _global_engine = engine
