"""Security Policy: rules that map tool metadata and device trust to decisions."""

from abc import ABC, abstractmethod
from typing import Mapping

from app.core.tools import SideEffect, ToolPermissions
from app.devices.models import TrustState
from app.security.models import (
    PermissionLevel,
    SecurityContext,
    SecurityDecision,
    SecurityEvaluation,
)


def derive_permission_level(permissions: ToolPermissions) -> PermissionLevel:
    """Infer the PermissionLevel from existing Part 2 / Part 6 ToolPermissions metadata.

    - "fs.destructive" scope -> DESTRUCTIVE
    - requires_confirmation -> DESTRUCTIVE (or PRIVILEGED/EXECUTE based on scope)
    - "system.write" scope -> PRIVILEGED / MODIFY
    - side_effect == WRITE -> WRITE
    - side_effect == READ -> READ
    - side_effect == NONE -> READ
    """
    scopes = set(permissions.scopes)

    if "fs.destructive" in scopes:
        return PermissionLevel.DESTRUCTIVE
    if permissions.requires_confirmation:
        if "system.write" in scopes or "service.control" in scopes or "process.terminate" in scopes:
            return PermissionLevel.PRIVILEGED
        return PermissionLevel.DESTRUCTIVE
    if "system.write" in scopes:
        return PermissionLevel.PRIVILEGED
    if permissions.side_effect == SideEffect.WRITE:
        return PermissionLevel.WRITE
    if permissions.side_effect == SideEffect.READ:
        return PermissionLevel.READ
    return PermissionLevel.READ


class BaseSecurityPolicy(ABC):
    """Abstract interface for security policies."""

    @abstractmethod
    def evaluate(
        self,
        permissions: ToolPermissions,
        context: SecurityContext,
    ) -> SecurityEvaluation:
        """Evaluate a tool invocation request and produce a decision."""


class DefaultSecurityPolicy(BaseSecurityPolicy):
    """Safe-by-default security policy.

    Rules:
    1. Revoked devices are always DENIED regardless of the tool.
    2. Untrusted devices are DENIED for anything beyond READ.
    3. READ operations are ALLOWED by default for trusted and unverified devices.
    4. WRITE operations are ALLOWED by default for trusted and unverified devices,
       unless configured otherwise.
    5. Operations with requires_confirmation=True (or DESTRUCTIVE/PRIVILEGED/EXECUTE
       permission levels) return REQUIRE_CONFIRMATION by default.
    """

    def __init__(
        self,
        *,
        allow_write: bool = True,
        strict_device_trust: bool = False,
    ) -> None:
        self.allow_write = allow_write
        self.strict_device_trust = strict_device_trust

    def evaluate(
        self,
        permissions: ToolPermissions,
        context: SecurityContext,
    ) -> SecurityEvaluation:
        level = derive_permission_level(permissions)

        # 1. Device trust checks
        if context.trust_level == TrustState.REVOKED.value:
            return SecurityEvaluation(
                decision=SecurityDecision.DENY,
                tool_name=context.tool_name,
                permission_level=level,
                reason=f"device {context.device_id!r} has revoked trust",
                requires_confirmation=permissions.requires_confirmation,
                policy_applied="device_trust_revoked",
            )

        if context.trust_level == TrustState.UNTRUSTED.value and level != PermissionLevel.READ:
            return SecurityEvaluation(
                decision=SecurityDecision.DENY,
                tool_name=context.tool_name,
                permission_level=level,
                reason=f"device {context.device_id!r} is untrusted and cannot execute non-read tools",
                requires_confirmation=permissions.requires_confirmation,
                policy_applied="device_untrusted_non_read",
            )

        if self.strict_device_trust and context.trust_level == TrustState.UNVERIFIED.value:
            if level != PermissionLevel.READ:
                return SecurityEvaluation(
                    decision=SecurityDecision.DENY,
                    tool_name=context.tool_name,
                    permission_level=level,
                    reason=f"strict policy: device {context.device_id!r} is unverified",
                    requires_confirmation=permissions.requires_confirmation,
                    policy_applied="strict_unverified_deny",
                )

        # 2. Confirmation checks
        # DESTRUCTIVE always requires confirmation. Other levels (WRITE,
        # PRIVILEGED, EXECUTE, ...) only require it when the tool's own
        # metadata declares requires_confirmation - a "system.write"-scoped
        # tool that was genuinely enabled and executing (e.g. git.clone) is
        # not forced into confirmation merely by virtue of its scope.
        if permissions.requires_confirmation or level == PermissionLevel.DESTRUCTIVE:
            return SecurityEvaluation(
                decision=SecurityDecision.REQUIRE_CONFIRMATION,
                tool_name=context.tool_name,
                permission_level=level,
                reason=f"tool {context.tool_name!r} requires confirmation (level={level.value})",
                requires_confirmation=True,
                policy_applied="requires_confirmation",
            )

        # 3. Write checks
        if level in (
            PermissionLevel.WRITE,
            PermissionLevel.MODIFY,
            PermissionLevel.PRIVILEGED,
            PermissionLevel.EXECUTE,
        ):
            if not self.allow_write:
                return SecurityEvaluation(
                    decision=SecurityDecision.DENY,
                    tool_name=context.tool_name,
                    permission_level=level,
                    reason="write operations are disabled by policy",
                    requires_confirmation=False,
                    policy_applied="policy_write_disabled",
                )
            return SecurityEvaluation(
                decision=SecurityDecision.ALLOW,
                tool_name=context.tool_name,
                permission_level=level,
                reason="write operation permitted by default policy",
                requires_confirmation=False,
                policy_applied="default_write_allow",
            )

        # 4. Read checks
        return SecurityEvaluation(
            decision=SecurityDecision.ALLOW,
            tool_name=context.tool_name,
            permission_level=level,
            reason="read-only operation permitted by default policy",
            requires_confirmation=False,
            policy_applied="default_read_allow",
        )
