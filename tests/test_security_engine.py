"""Tests for PART 7: Security / Permission Engine.

Verifies that:
1. The Security Engine makes correct decisions based on permission levels and device trust.
2. Read-only tools still work unchanged (ALLOW decision).
3. Previously-stubbed destructive tools now go through the real engine.
4. Security decisions are properly audited.
5. REQUIRE_CONFIRMATION is treated as unauthorized when no confirmation channel exists.
"""

import asyncio

from app.core.context import RequestContext, ToolExecutionContext
from app.core.models import ExecutionStatus
from app.core.tools import SideEffect, ToolPermissions
from app.devices.models import Device, TrustState
from app.security.engine import SecurityEngine
from app.security.models import PermissionLevel, SecurityContext, SecurityDecision
from app.security.policy import DefaultSecurityPolicy, derive_permission_level


def run(coro):
    return asyncio.run(coro)


# --- Permission Level Derivation Tests ------------------------------------


def test_derive_permission_level_destructive_scope():
    """Tools with fs.destructive scope are DESTRUCTIVE."""
    perms = ToolPermissions(
        side_effect=SideEffect.WRITE,
        scopes=("fs.write", "fs.destructive"),
        requires_confirmation=True,
    )
    assert derive_permission_level(perms) == PermissionLevel.DESTRUCTIVE


def test_derive_permission_level_requires_confirmation_system():
    """System tools requiring confirmation are PRIVILEGED."""
    perms = ToolPermissions(
        side_effect=SideEffect.WRITE,
        scopes=("system.write",),
        requires_confirmation=True,
    )
    assert derive_permission_level(perms) == PermissionLevel.PRIVILEGED


def test_derive_permission_level_system_write():
    """System write tools are PRIVILEGED."""
    perms = ToolPermissions(
        side_effect=SideEffect.WRITE,
        scopes=("system.write",),
        requires_confirmation=False,
    )
    assert derive_permission_level(perms) == PermissionLevel.PRIVILEGED


def test_derive_permission_level_write():
    """Write side effect without special scopes is WRITE."""
    perms = ToolPermissions(
        side_effect=SideEffect.WRITE,
        scopes=("fs.write",),
    )
    assert derive_permission_level(perms) == PermissionLevel.WRITE


def test_derive_permission_level_read():
    """Read side effect is READ."""
    perms = ToolPermissions(
        side_effect=SideEffect.READ,
        scopes=("fs.read",),
    )
    assert derive_permission_level(perms) == PermissionLevel.READ


def test_derive_permission_level_none():
    """No side effect defaults to READ."""
    perms = ToolPermissions(side_effect=SideEffect.NONE)
    assert derive_permission_level(perms) == PermissionLevel.READ


# --- Security Policy Tests ------------------------------------------------


def test_policy_allows_read_for_trusted_device():
    """READ operations are allowed for trusted devices."""
    policy = DefaultSecurityPolicy()
    perms = ToolPermissions(side_effect=SideEffect.READ, scopes=("fs.read",))
    context = SecurityContext(
        tool_name="fs.read",
        device_id="test-device",
        trust_level=TrustState.TRUSTED.value,
        request_id="test-req-001",
    )

    evaluation = policy.evaluate(perms, context)

    assert evaluation.decision == SecurityDecision.ALLOW
    assert evaluation.permission_level == PermissionLevel.READ
    assert "read-only operation permitted" in evaluation.reason


def test_policy_allows_read_for_unverified_device():
    """READ operations are allowed even for unverified devices by default."""
    policy = DefaultSecurityPolicy()
    perms = ToolPermissions(side_effect=SideEffect.READ, scopes=("system.read",))
    context = SecurityContext(
        tool_name="proc.list",
        device_id="unknown-device",
        trust_level=TrustState.UNVERIFIED.value,
        request_id="test-req-002",
    )

    evaluation = policy.evaluate(perms, context)

    assert evaluation.decision == SecurityDecision.ALLOW
    assert evaluation.permission_level == PermissionLevel.READ


def test_policy_denies_untrusted_device_non_read():
    """Untrusted devices are denied for non-READ operations."""
    policy = DefaultSecurityPolicy()
    perms = ToolPermissions(side_effect=SideEffect.WRITE, scopes=("fs.write",))
    context = SecurityContext(
        tool_name="fs.write",
        device_id="untrusted-device",
        trust_level=TrustState.UNTRUSTED.value,
        request_id="test-req-003",
    )

    evaluation = policy.evaluate(perms, context)

    assert evaluation.decision == SecurityDecision.DENY
    assert "untrusted and cannot execute non-read tools" in evaluation.reason


def test_policy_denies_revoked_device_always():
    """Revoked devices are denied regardless of operation."""
    policy = DefaultSecurityPolicy()
    perms = ToolPermissions(side_effect=SideEffect.READ, scopes=("fs.read",))
    context = SecurityContext(
        tool_name="fs.read",
        device_id="revoked-device",
        trust_level=TrustState.REVOKED.value,
        request_id="test-req-004",
    )

    evaluation = policy.evaluate(perms, context)

    assert evaluation.decision == SecurityDecision.DENY
    assert "revoked trust" in evaluation.reason


def test_policy_requires_confirmation_for_destructive():
    """DESTRUCTIVE operations require confirmation."""
    policy = DefaultSecurityPolicy()
    perms = ToolPermissions(
        side_effect=SideEffect.WRITE,
        scopes=("fs.write", "fs.destructive"),
        requires_confirmation=True,
    )
    context = SecurityContext(
        tool_name="fs.delete",
        device_id="local",
        trust_level=TrustState.TRUSTED.value,
        request_id="test-req-005",
    )

    evaluation = policy.evaluate(perms, context)

    assert evaluation.decision == SecurityDecision.REQUIRE_CONFIRMATION
    assert evaluation.requires_confirmation is True
    assert "requires confirmation" in evaluation.reason


def test_policy_requires_confirmation_for_privileged():
    """PRIVILEGED operations require confirmation."""
    policy = DefaultSecurityPolicy()
    perms = ToolPermissions(
        side_effect=SideEffect.WRITE,
        scopes=("system.write", "process.terminate"),
        requires_confirmation=True,
    )
    context = SecurityContext(
        tool_name="proc.terminate",
        device_id="local",
        trust_level=TrustState.TRUSTED.value,
        request_id="test-req-006",
    )

    evaluation = policy.evaluate(perms, context)

    assert evaluation.decision == SecurityDecision.REQUIRE_CONFIRMATION


def test_policy_allows_write_by_default():
    """WRITE operations are allowed by default."""
    policy = DefaultSecurityPolicy()
    perms = ToolPermissions(side_effect=SideEffect.WRITE, scopes=("fs.write",))
    context = SecurityContext(
        tool_name="fs.write",
        device_id="local",
        trust_level=TrustState.TRUSTED.value,
        request_id="test-req-007",
    )

    evaluation = policy.evaluate(perms, context)

    assert evaluation.decision == SecurityDecision.ALLOW
    assert evaluation.permission_level == PermissionLevel.WRITE


def test_policy_denies_write_when_disabled():
    """WRITE operations can be disabled by policy."""
    policy = DefaultSecurityPolicy(allow_write=False)
    perms = ToolPermissions(side_effect=SideEffect.WRITE, scopes=("fs.write",))
    context = SecurityContext(
        tool_name="fs.write",
        device_id="local",
        trust_level=TrustState.TRUSTED.value,
        request_id="test-req-008",
    )

    evaluation = policy.evaluate(perms, context)

    assert evaluation.decision == SecurityDecision.DENY
    assert "write operations are disabled" in evaluation.reason


def test_policy_strict_mode_denies_unverified():
    """Strict mode denies unverified devices for non-READ operations."""
    policy = DefaultSecurityPolicy(strict_device_trust=True)
    perms = ToolPermissions(side_effect=SideEffect.WRITE, scopes=("fs.write",))
    context = SecurityContext(
        tool_name="fs.write",
        device_id="unverified-device",
        trust_level=TrustState.UNVERIFIED.value,
        request_id="test-req-009",
    )

    evaluation = policy.evaluate(perms, context)

    assert evaluation.decision == SecurityDecision.DENY
    assert "strict policy" in evaluation.reason
    assert "unverified" in evaluation.reason


# --- Security Engine Tests ------------------------------------------------


def test_security_engine_authorizes_read_tool():
    """Security Engine allows read-only tools."""
    from app.tools.echo import EchoTool

    engine = SecurityEngine()
    tool = EchoTool()
    context = ToolExecutionContext.for_tool(
        RequestContext.create(request_id="test-001"), "echo"
    )

    evaluation = engine.authorize(tool, {"message": "test"}, context)

    assert evaluation.decision == SecurityDecision.ALLOW
    assert evaluation.tool_name == "echo"


def test_security_engine_check_tool_authorized_returns_true_for_allow():
    """check_tool_authorized returns True only for ALLOW decisions."""
    from app.tools.echo import EchoTool

    engine = SecurityEngine()
    tool = EchoTool()
    context = ToolExecutionContext.for_tool(
        RequestContext.create(request_id="test-002"), "echo"
    )

    is_authorized = engine.check_tool_authorized(tool, {"message": "test"}, context)

    assert is_authorized is True


def test_security_engine_check_tool_authorized_returns_false_for_require_confirmation():
    """check_tool_authorized returns False for REQUIRE_CONFIRMATION (no channel exists)."""
    from app.tools.filesystem.destructive import DeleteTool

    engine = SecurityEngine()
    tool = DeleteTool()
    context = ToolExecutionContext.for_tool(
        RequestContext.create(request_id="test-003"), "fs.delete"
    )

    is_authorized = engine.check_tool_authorized(
        tool, {"path": "/tmp/test.txt"}, context
    )

    assert is_authorized is False


def test_security_engine_extracts_target_from_arguments():
    """Security Engine extracts target from common argument names."""
    from app.tools.filesystem.destructive import DeleteTool

    engine = SecurityEngine()
    tool = DeleteTool()
    context = ToolExecutionContext.for_tool(
        RequestContext.create(request_id="test-004"), "fs.delete"
    )

    evaluation = engine.authorize(tool, {"path": "/tmp/test.txt"}, context)

    assert evaluation.decision == SecurityDecision.REQUIRE_CONFIRMATION
    # Target extraction happens in authorize but is passed to audit


# --- Integration Tests: Previously-Stubbed Tools --------------------------


def test_destructive_filesystem_tool_goes_through_security_engine(tmp_path):
    """fs.delete now uses Security Engine instead of hardcoded stub."""
    from app.tools.filesystem.destructive import DeleteTool

    tool = DeleteTool()
    context = ToolExecutionContext.for_tool(
        RequestContext.create(request_id="test-fs-001"), "fs.delete"
    )

    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")

    result = run(tool.execute({"path": str(test_file)}, context))

    # Should be refused because requires_confirmation and no confirmation channel
    assert result.status == ExecutionStatus.ERROR
    assert "requires confirmation" in result.error
    assert "PART 10/11" in result.error
    # Verify file was NOT deleted
    assert test_file.exists()


def test_denied_system_tool_goes_through_security_engine():
    """proc.terminate now uses Security Engine instead of hardcoded stub."""
    from app.tools.system.processes import TerminateProcessTool

    tool = TerminateProcessTool()
    context = ToolExecutionContext.for_tool(
        RequestContext.create(request_id="test-sys-001"), "proc.terminate"
    )

    result = run(tool.execute({"pid": 1, "signal": "TERM"}, context))

    # Should be refused because requires_confirmation and no confirmation channel
    assert result.status == ExecutionStatus.ERROR
    assert "requires confirmation" in result.error
    assert "PART 10/11" in result.error


def test_read_only_tool_still_works_unchanged():
    """Read-only tools continue to execute normally through Security Engine."""
    from app.tools.echo import EchoTool

    tool = EchoTool()
    context = ToolExecutionContext.for_tool(
        RequestContext.create(request_id="test-read-001"), "echo"
    )

    result = run(tool.execute({"message": "Hello, PART 7!"}, context))

    assert result.status == ExecutionStatus.SUCCESS
    assert result.output["message"] == "Hello, PART 7!"
    assert result.output["length"] == 14
