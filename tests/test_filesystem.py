"""Tests for PART 4: the filesystem tools and the sandbox they run inside.

Covers each tool's happy path, path traversal rejection, system path
protection, size limit enforcement, audit logging, and the deny-stub that keeps
delete/move/rename inert until PART 7.
"""

import asyncio
import logging
import os

import pytest

from app.core.context import RequestContext, ToolExecutionContext
from app.core.errors import ToolExecutionError
from app.core.models import ExecutionStatus
from app.core.registry import build_default_registry
from app.core.tools import SideEffect, Tool
from app.fs.audit import AUDIT_LOGGER
from app.fs.errors import (
    FileTooLargeError,
    PathNotAllowedError,
    PathTraversalError,
    SystemPathError,
)
from app.fs.policy import FilesystemLimits, FilesystemPolicy
from app.tools.filesystem import DISABLED_TOOL_NAMES, ENABLED_TOOL_NAMES
from app.tools.filesystem.content import ReadFileTool, WriteFileTool
from app.tools.filesystem.destructive import (
    DeleteInput,
    DeleteTool,
    MoveTool,
    RenameTool,
)
from app.tools.filesystem.inspect import ListDirectoryTool, SearchTool, StatTool
from app.tools.filesystem.manage import CopyTool, MakeDirectoryTool


def run(coro):
    return asyncio.run(coro)


def context_for(tool_name: str, request_id: str = "corr-fs") -> ToolExecutionContext:
    return ToolExecutionContext.for_tool(RequestContext.create(request_id=request_id), tool_name)


TIGHT_LIMITS = FilesystemLimits(
    max_read_bytes=1024,
    max_write_bytes=1024,
    max_copy_bytes=2048,
    max_list_entries=1000,
    max_search_results=50,
    max_search_depth=5,
)


@pytest.fixture
def sandbox(tmp_path):
    """An allowed root, with a sibling directory that is deliberately outside it."""
    root = tmp_path / "workspace"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("do not read me")
    return root


@pytest.fixture
def policy(sandbox):
    return FilesystemPolicy([sandbox], limits=TIGHT_LIMITS)


@pytest.fixture
def wide_policy():
    """A policy whose allowed root is '/' - only the system path rules protect it."""
    return FilesystemPolicy(["/"])


def call(tool_cls, policy, arguments, request_id="corr-fs"):
    tool = tool_cls(policy=policy)
    return run(tool.execute(arguments, context_for(tool.name, request_id)))


# ===========================================================================
# Policy: path resolution, traversal, system paths
# ===========================================================================


def test_policy_accepts_a_path_inside_an_allowed_root(policy, sandbox):
    assert policy.resolve(str(sandbox / "notes.txt")) == sandbox / "notes.txt"


def test_policy_accepts_the_root_itself(policy, sandbox):
    assert policy.resolve(str(sandbox)) == sandbox


def test_policy_rejects_a_relative_path(policy):
    with pytest.raises(PathNotAllowedError, match="absolute"):
        policy.resolve("notes.txt")


def test_policy_rejects_a_path_outside_every_root(policy, tmp_path):
    with pytest.raises(PathNotAllowedError):
        policy.resolve(str(tmp_path / "outside" / "secret.txt"))


def test_policy_rejects_dotdot_traversal(policy, sandbox):
    with pytest.raises(PathTraversalError, match="traversal"):
        policy.resolve(str(sandbox) + "/../outside/secret.txt")


def test_policy_rejects_deeply_nested_traversal(policy, sandbox):
    with pytest.raises(PathTraversalError):
        policy.resolve(str(sandbox / "a" / "b") + "/../../../../etc/passwd")


def test_policy_rejects_a_symlink_that_escapes_the_root(policy, sandbox, tmp_path):
    escape = sandbox / "escape"
    escape.symlink_to(tmp_path / "outside")
    with pytest.raises(PathNotAllowedError):
        policy.resolve(str(escape / "secret.txt"))


def test_policy_rejects_a_symlink_pointing_at_a_system_path(policy, sandbox):
    link = sandbox / "passwd-link"
    link.symlink_to("/etc/passwd")
    with pytest.raises(SystemPathError):
        policy.resolve(str(link))


def test_policy_protects_system_paths_even_inside_an_allowed_root(wide_policy):
    """The root is '/', so /etc is 'inside' it - and must still be refused."""
    assert wide_policy.roots == (__import__("pathlib").Path("/"),)
    with pytest.raises(SystemPathError, match="/etc"):
        wide_policy.resolve("/etc/passwd")


@pytest.mark.parametrize(
    "path", ["/etc/shadow", "/usr/bin/python3", "/proc/self/environ", "/sys/kernel", "/root/.bashrc"]
)
def test_policy_refuses_every_protected_system_location(wide_policy, path):
    with pytest.raises(SystemPathError):
        wide_policy.resolve(path)


def test_policy_refuses_credential_directories_anywhere(policy, sandbox):
    with pytest.raises(SystemPathError, match=".ssh"):
        policy.resolve(str(sandbox / ".ssh" / "id_rsa"))


def test_policy_rejects_a_nul_byte(policy, sandbox):
    with pytest.raises(PathNotAllowedError, match="NUL"):
        policy.resolve(str(sandbox / "notes.txt") + "\x00.png")


def test_policy_rejects_an_empty_path(policy):
    with pytest.raises(PathNotAllowedError):
        policy.resolve("   ")


def test_policy_refuses_to_be_built_on_a_system_root():
    with pytest.raises(ValueError, match="protected system path"):
        FilesystemPolicy(["/etc/firday"])


def test_policy_needs_at_least_one_root():
    with pytest.raises(ValueError):
        FilesystemPolicy([])


def test_policy_enforce_size_raises_past_the_limit(policy):
    with pytest.raises(FileTooLargeError, match="exceeds"):
        policy.enforce_size("/x", 2048, 1024)


def test_policy_describe_is_serializable(policy, sandbox):
    described = policy.describe()
    assert described["allowed_roots"] == [str(sandbox)]
    assert "/etc" in described["protected_system_paths"]
    assert described["limits"]["max_read_bytes"] == 1024


# ===========================================================================
# Happy paths
# ===========================================================================


def test_list_returns_directory_contents(policy, sandbox):
    (sandbox / "a.txt").write_text("a")
    (sandbox / "sub").mkdir()

    result = call(ListDirectoryTool, policy, {"path": str(sandbox)})
    assert result.status is ExecutionStatus.SUCCESS
    assert [e["name"] for e in result.output["entries"]] == ["a.txt", "sub"]
    assert {e["type"] for e in result.output["entries"]} == {"file", "directory"}
    assert result.output["truncated"] is False


def test_list_hides_dotfiles_unless_asked(policy, sandbox):
    (sandbox / ".hidden").write_text("x")
    (sandbox / "visible.txt").write_text("x")

    assert [e["name"] for e in call(ListDirectoryTool, policy, {"path": str(sandbox)}).output["entries"]] == [
        "visible.txt"
    ]
    shown = call(ListDirectoryTool, policy, {"path": str(sandbox), "include_hidden": True})
    assert [e["name"] for e in shown.output["entries"]] == [".hidden", "visible.txt"]


def test_list_truncates_at_the_requested_cap(policy, sandbox):
    for i in range(5):
        (sandbox / f"f{i}.txt").write_text("x")
    result = call(ListDirectoryTool, policy, {"path": str(sandbox), "max_entries": 2})
    assert result.output["entry_count"] == 2
    assert result.output["truncated"] is True


def test_stat_describes_a_file(policy, sandbox):
    target = sandbox / "notes.txt"
    target.write_text("hello")

    result = call(StatTool, policy, {"path": str(target)})
    assert result.status is ExecutionStatus.SUCCESS
    assert result.output["type"] == "file"
    assert result.output["size_bytes"] == 5
    assert result.output["exists"] is True
    assert result.output["mode"].startswith("0o")
    assert result.output["is_symlink"] is False


def test_stat_describes_a_directory(policy, sandbox):
    (sandbox / "one.txt").write_text("1")
    result = call(StatTool, policy, {"path": str(sandbox)})
    assert result.output["type"] == "directory"
    assert result.output["entry_count"] == 1


def test_search_finds_files_by_pattern(policy, sandbox):
    (sandbox / "notes.txt").write_text("x")
    (sandbox / "image.png").write_text("x")
    nested = sandbox / "deep" / "deeper"
    nested.mkdir(parents=True)
    (nested / "buried.txt").write_text("x")

    result = call(SearchTool, policy, {"path": str(sandbox), "pattern": "*.txt"})
    assert result.status is ExecutionStatus.SUCCESS
    assert sorted(e["name"] for e in result.output["matches"]) == ["buried.txt", "notes.txt"]


def test_search_can_stay_shallow(policy, sandbox):
    (sandbox / "top.txt").write_text("x")
    (sandbox / "deep").mkdir()
    (sandbox / "deep" / "low.txt").write_text("x")

    result = call(SearchTool, policy, {"path": str(sandbox), "pattern": "*.txt", "recursive": False})
    assert [e["name"] for e in result.output["matches"]] == ["top.txt"]


def test_search_can_include_directories(policy, sandbox):
    (sandbox / "logs").mkdir()
    result = call(
        SearchTool,
        policy,
        {"path": str(sandbox), "pattern": "log*", "include_directories": True, "include_files": False},
    )
    assert [e["name"] for e in result.output["matches"]] == ["logs"]


def test_search_does_not_follow_symlinks_out_of_the_sandbox(policy, sandbox, tmp_path):
    (sandbox / "escape").symlink_to(tmp_path / "outside")
    result = call(SearchTool, policy, {"path": str(sandbox), "pattern": "*.txt"})
    assert result.output["matches"] == []


def test_read_returns_file_contents(policy, sandbox):
    target = sandbox / "notes.txt"
    target.write_text("line one\nline two\n")

    result = call(ReadFileTool, policy, {"path": str(target)})
    assert result.status is ExecutionStatus.SUCCESS
    assert result.output["content"] == "line one\nline two\n"
    assert result.output["line_count"] == 2
    assert result.output["encoding"] == "utf-8"


def test_write_creates_a_new_file(policy, sandbox):
    target = sandbox / "new.txt"
    result = call(WriteFileTool, policy, {"path": str(target), "content": "hello"})

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output["created"] is True
    assert result.output["bytes_written"] == 5
    assert target.read_text() == "hello"


def test_write_refuses_to_clobber_by_default(policy, sandbox):
    target = sandbox / "new.txt"
    target.write_text("original")

    result = call(WriteFileTool, policy, {"path": str(target), "content": "replacement"})
    assert result.status is ExecutionStatus.ERROR
    assert "already exists" in result.error
    assert target.read_text() == "original"


def test_write_overwrites_and_appends_when_asked(policy, sandbox):
    target = sandbox / "new.txt"
    call(WriteFileTool, policy, {"path": str(target), "content": "one"})
    call(WriteFileTool, policy, {"path": str(target), "content": "two", "mode": "overwrite"})
    call(WriteFileTool, policy, {"path": str(target), "content": "-three", "mode": "append"})
    assert target.read_text() == "two-three"


def test_write_creates_parents_only_on_request(policy, sandbox):
    target = sandbox / "a" / "b" / "deep.txt"

    refused = call(WriteFileTool, policy, {"path": str(target), "content": "x"})
    assert refused.status is ExecutionStatus.ERROR
    assert "create_parents" in refused.error

    made = call(WriteFileTool, policy, {"path": str(target), "content": "x", "create_parents": True})
    assert made.status is ExecutionStatus.SUCCESS
    assert target.read_text() == "x"


def test_mkdir_creates_a_directory(policy, sandbox):
    target = sandbox / "reports"
    result = call(MakeDirectoryTool, policy, {"path": str(target)})

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output["created"] is True
    assert target.is_dir()


def test_mkdir_is_idempotent_only_with_exist_ok(policy, sandbox):
    target = sandbox / "reports"
    target.mkdir()

    assert call(MakeDirectoryTool, policy, {"path": str(target)}).status is ExecutionStatus.ERROR
    ok = call(MakeDirectoryTool, policy, {"path": str(target), "exist_ok": True})
    assert ok.status is ExecutionStatus.SUCCESS
    assert ok.output["existed"] is True


def test_mkdir_creates_intermediate_directories_on_request(policy, sandbox):
    target = sandbox / "x" / "y" / "z"
    assert call(MakeDirectoryTool, policy, {"path": str(target)}).status is ExecutionStatus.ERROR
    assert call(MakeDirectoryTool, policy, {"path": str(target), "parents": True}).status is (
        ExecutionStatus.SUCCESS
    )
    assert target.is_dir()


def test_copy_duplicates_a_file(policy, sandbox):
    source = sandbox / "a.txt"
    source.write_text("payload")
    destination = sandbox / "b.txt"

    result = call(CopyTool, policy, {"source": str(source), "destination": str(destination)})
    assert result.status is ExecutionStatus.SUCCESS
    assert result.output["files_copied"] == 1
    assert destination.read_text() == "payload"
    assert source.exists()


def test_copy_duplicates_a_directory_tree(policy, sandbox):
    source = sandbox / "tree"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "leaf.txt").write_text("leaf")

    result = call(CopyTool, policy, {"source": str(source), "destination": str(sandbox / "clone")})
    assert result.status is ExecutionStatus.SUCCESS
    assert result.output["type"] == "directory"
    assert (sandbox / "clone" / "nested" / "leaf.txt").read_text() == "leaf"


def test_copy_never_overwrites(policy, sandbox):
    source = sandbox / "a.txt"
    source.write_text("new")
    destination = sandbox / "b.txt"
    destination.write_text("existing")

    result = call(CopyTool, policy, {"source": str(source), "destination": str(destination)})
    assert result.status is ExecutionStatus.ERROR
    assert "will not overwrite" in result.error
    assert destination.read_text() == "existing"


def test_copy_refuses_a_destination_inside_the_source(policy, sandbox):
    source = sandbox / "tree"
    source.mkdir()
    result = call(
        CopyTool, policy, {"source": str(source), "destination": str(source / "inner")}
    )
    assert result.status is ExecutionStatus.ERROR
    assert "inside the source" in result.error


# ===========================================================================
# Path traversal and sandbox escape, through the tools
# ===========================================================================


@pytest.mark.parametrize(
    ("tool_cls", "arguments"),
    [
        (ListDirectoryTool, {"path": "{outside}"}),
        (StatTool, {"path": "{outside}/secret.txt"}),
        (SearchTool, {"path": "{outside}", "pattern": "*"}),
        (ReadFileTool, {"path": "{outside}/secret.txt"}),
        (WriteFileTool, {"path": "{outside}/planted.txt", "content": "x"}),
        (MakeDirectoryTool, {"path": "{outside}/planted"}),
    ],
)
def test_tools_refuse_paths_outside_the_allowed_roots(policy, tmp_path, tool_cls, arguments):
    outside = str(tmp_path / "outside")
    resolved_args = {k: v.format(outside=outside) if isinstance(v, str) else v for k, v in arguments.items()}

    result = call(tool_cls, policy, resolved_args)
    assert result.status is ExecutionStatus.ERROR
    assert "is not allowed" in result.error
    assert not (tmp_path / "outside" / "planted.txt").exists()
    assert not (tmp_path / "outside" / "planted").exists()


def test_read_refuses_a_dotdot_escape(policy, sandbox):
    result = call(ReadFileTool, policy, {"path": str(sandbox) + "/../outside/secret.txt"})
    assert result.status is ExecutionStatus.ERROR
    assert "traversal" in result.error


def test_read_refuses_a_symlink_escape(policy, sandbox, tmp_path):
    (sandbox / "escape").symlink_to(tmp_path / "outside" / "secret.txt")
    result = call(ReadFileTool, policy, {"path": str(sandbox / "escape")})
    assert result.status is ExecutionStatus.ERROR
    assert "is not allowed" in result.error


def test_copy_refuses_a_destination_outside_the_root(policy, sandbox, tmp_path):
    source = sandbox / "a.txt"
    source.write_text("payload")
    destination = tmp_path / "outside" / "leaked.txt"

    result = call(CopyTool, policy, {"source": str(source), "destination": str(destination)})
    assert result.status is ExecutionStatus.ERROR
    assert not destination.exists()


# ===========================================================================
# System path protection, through the tools
# ===========================================================================


@pytest.mark.parametrize(
    ("tool_cls", "arguments"),
    [
        (ListDirectoryTool, {"path": "/etc"}),
        (StatTool, {"path": "/etc/passwd"}),
        (ReadFileTool, {"path": "/etc/passwd"}),
        (SearchTool, {"path": "/usr", "pattern": "*"}),
        (WriteFileTool, {"path": "/etc/firday-planted.conf", "content": "x"}),
        (MakeDirectoryTool, {"path": "/etc/firday-planted"}),
    ],
)
def test_tools_refuse_system_paths_even_when_the_root_allows_them(wide_policy, tool_cls, arguments):
    """Root is '/', so only the system path rules stand between the tool and /etc."""
    result = call(tool_cls, wide_policy, arguments)
    assert result.status is ExecutionStatus.ERROR
    assert "protected system path" in result.error
    assert not os.path.exists("/etc/firday-planted.conf")
    assert not os.path.exists("/etc/firday-planted")


def test_read_refuses_a_credential_directory(policy, sandbox):
    (sandbox / ".ssh").mkdir()
    (sandbox / ".ssh" / "id_rsa").write_text("PRIVATE KEY")

    result = call(ReadFileTool, policy, {"path": str(sandbox / ".ssh" / "id_rsa")})
    assert result.status is ExecutionStatus.ERROR
    assert ".ssh" in result.error


# ===========================================================================
# Size limits
# ===========================================================================


def test_read_refuses_a_file_over_the_limit(policy, sandbox):
    target = sandbox / "big.txt"
    target.write_text("x" * 2048)  # limit is 1024

    result = call(ReadFileTool, policy, {"path": str(target)})
    assert result.status is ExecutionStatus.ERROR
    assert "exceeds the 1024 byte limit" in result.error


def test_read_honours_a_lower_per_call_limit(policy, sandbox):
    target = sandbox / "medium.txt"
    target.write_text("x" * 100)

    assert call(ReadFileTool, policy, {"path": str(target)}).status is ExecutionStatus.SUCCESS
    tight = call(ReadFileTool, policy, {"path": str(target), "max_bytes": 10})
    assert tight.status is ExecutionStatus.ERROR
    assert "exceeds the 10 byte limit" in tight.error


def test_read_limit_cannot_be_raised_by_the_caller(policy, sandbox):
    target = sandbox / "big.txt"
    target.write_text("x" * 2048)

    result = call(ReadFileTool, policy, {"path": str(target), "max_bytes": 10_000_000})
    assert result.status is ExecutionStatus.ERROR
    assert "exceeds the 1024 byte limit" in result.error


def test_write_refuses_content_over_the_limit(policy, sandbox):
    target = sandbox / "big.txt"
    result = call(WriteFileTool, policy, {"path": str(target), "content": "x" * 2048})

    assert result.status is ExecutionStatus.ERROR
    assert "exceeds the 1024 byte limit" in result.error
    assert not target.exists()


def test_append_counts_what_is_already_on_disk(policy, sandbox):
    target = sandbox / "log.txt"
    target.write_text("x" * 900)

    result = call(WriteFileTool, policy, {"path": str(target), "content": "y" * 200, "mode": "append"})
    assert result.status is ExecutionStatus.ERROR
    assert "exceeds the 1024 byte limit" in result.error
    assert target.stat().st_size == 900


def test_copy_refuses_a_file_over_the_copy_limit(policy, sandbox):
    source = sandbox / "big.bin"
    source.write_text("x" * 4096)  # copy limit is 2048
    destination = sandbox / "copy.bin"

    result = call(CopyTool, policy, {"source": str(source), "destination": str(destination)})
    assert result.status is ExecutionStatus.ERROR
    assert "exceeds the 2048 byte limit" in result.error
    assert not destination.exists()


def test_copy_refuses_a_tree_over_the_copy_limit(policy, sandbox):
    source = sandbox / "tree"
    source.mkdir()
    for i in range(3):
        (source / f"part{i}.bin").write_text("x" * 1024)

    result = call(CopyTool, policy, {"source": str(source), "destination": str(sandbox / "clone")})
    assert result.status is ExecutionStatus.ERROR
    assert "exceeds the 2048 byte limit" in result.error
    assert not (sandbox / "clone").exists()


# ===========================================================================
# Destructive tools: registered, schema-complete, and refusing to run
# ===========================================================================


DESTRUCTIVE_CASES = [
    (DeleteTool, {"path": "{target}"}),
    (MoveTool, {"source": "{target}", "destination": "{moved}"}),
    (RenameTool, {"path": "{target}", "new_name": "renamed.txt"}),
]


@pytest.mark.parametrize(("tool_cls", "arguments"), DESTRUCTIVE_CASES)
def test_destructive_tools_refuse_with_a_not_yet_authorized_result(
    policy, sandbox, tool_cls, arguments
):
    target = sandbox / "precious.txt"
    target.write_text("still here")
    resolved = {
        k: v.format(target=str(target), moved=str(sandbox / "moved.txt")) for k, v in arguments.items()
    }

    result = call(tool_cls, policy, resolved)

    assert result.status is ExecutionStatus.ERROR
    assert "requires confirmation but no confirmation channel exists" in result.error
    assert "PART 10/11" in result.output["blocked_until"]
    assert result.output["authorized"] is False
    assert "requires confirmation" in result.output["reason"]
    assert result.duration_ms is not None


@pytest.mark.parametrize(("tool_cls", "arguments"), DESTRUCTIVE_CASES)
def test_destructive_tools_do_not_touch_the_filesystem(policy, sandbox, tool_cls, arguments):
    target = sandbox / "precious.txt"
    target.write_text("still here")
    resolved = {
        k: v.format(target=str(target), moved=str(sandbox / "moved.txt")) for k, v in arguments.items()
    }

    call(tool_cls, policy, resolved)

    assert target.exists()
    assert target.read_text() == "still here"
    assert not (sandbox / "moved.txt").exists()
    assert not (sandbox / "renamed.txt").exists()
    assert sorted(p.name for p in sandbox.iterdir()) == ["precious.txt"]


@pytest.mark.parametrize(("tool_cls", "_arguments"), DESTRUCTIVE_CASES)
def test_destructive_tools_deny_before_validating_input(policy, tool_cls, _arguments):
    """The denial short-circuits everything - even garbage arguments get it."""
    result = call(tool_cls, policy, {"nonsense": True})
    assert result.status is ExecutionStatus.ERROR
    assert "requires confirmation but no confirmation channel exists" in result.error


@pytest.mark.parametrize(("tool_cls", "_arguments"), DESTRUCTIVE_CASES)
def test_destructive_tools_authorization_stub_always_denies(policy, tool_cls, _arguments):
    tool = tool_cls(policy=policy)
    assert tool.is_authorized({}, context_for(tool.name)) is False


@pytest.mark.parametrize(("tool_cls", "_arguments"), DESTRUCTIVE_CASES)
def test_destructive_tools_are_still_real_registered_tools(tool_cls, _arguments):
    registry = build_default_registry()
    tool = registry.get(tool_cls.name)

    assert isinstance(tool, Tool)
    assert tool.description
    assert "DISABLED" in tool.description
    assert tool.input_schema()["properties"]
    assert tool.output_schema()["properties"]["authorized"]
    assert tool.permissions.requires_confirmation is True
    assert tool.permissions.side_effect is SideEffect.WRITE
    assert "fs.destructive" in tool.permissions.scopes


def test_destructive_operate_refuses_even_if_reached_directly(policy, sandbox):
    """Defence in depth: the operation body itself refuses, not just ``execute``."""
    target = sandbox / "precious.txt"
    target.write_text("still here")
    tool = DeleteTool(policy=policy)

    with pytest.raises(ToolExecutionError, match="not yet authorized"):
        run(tool.run(DeleteInput(path=str(target)), context_for(tool.name)))

    assert target.exists()


# ===========================================================================
# Audit logging
# ===========================================================================


def test_successful_operation_is_audited_with_the_correlation_id(policy, sandbox, caplog):
    target = sandbox / "notes.txt"
    target.write_text("hello")

    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        call(ReadFileTool, policy, {"path": str(target)}, request_id="corr-read")

    [record] = [r for r in caplog.records if r.name == AUDIT_LOGGER]
    assert record.request_id == "corr-read"
    assert "op=read" in record.message
    assert "decision=allowed" in record.message
    assert "outcome=success" in record.message
    assert str(target) in record.message


def test_denied_operation_is_audited_as_a_denial(policy, tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        call(ReadFileTool, policy, {"path": str(tmp_path / "outside" / "secret.txt")}, "corr-deny")

    [record] = [r for r in caplog.records if r.name == AUDIT_LOGGER]
    assert record.levelno == logging.WARNING
    assert record.request_id == "corr-deny"
    assert "decision=denied" in record.message
    assert "outcome=denied" in record.message
    assert "is not allowed" in record.message


def test_destructive_refusal_is_audited(policy, sandbox, caplog):
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        call(DeleteTool, policy, {"path": str(sandbox / "precious.txt")}, "corr-del")

    [record] = [r for r in caplog.records if r.name == AUDIT_LOGGER]
    assert record.request_id == "corr-del"
    assert "op=delete" in record.message
    assert "outcome=not_authorized" in record.message


def test_every_attempt_produces_exactly_one_audit_record(policy, sandbox, caplog):
    (sandbox / "a.txt").write_text("a")
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        call(ListDirectoryTool, policy, {"path": str(sandbox)})
        call(StatTool, policy, {"path": str(sandbox / "a.txt")})
        call(ReadFileTool, policy, {"path": str(sandbox / "missing.txt")})

    records = [r for r in caplog.records if r.name == AUDIT_LOGGER]
    assert [r.message.split()[2] for r in records] == ["op=list", "op=stat", "op=read"]


def test_copy_audits_both_paths(policy, sandbox, caplog):
    source = sandbox / "a.txt"
    source.write_text("x")

    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        call(CopyTool, policy, {"source": str(source), "destination": str(sandbox / "b.txt")})

    [record] = [r for r in caplog.records if r.name == AUDIT_LOGGER]
    assert str(source) in record.message
    assert str(sandbox / "b.txt") in record.message


# ===========================================================================
# Registration
# ===========================================================================


def test_all_ten_filesystem_tools_are_registered():
    registry = build_default_registry()
    for name in ENABLED_TOOL_NAMES + DISABLED_TOOL_NAMES:
        assert name in registry, name


def test_enabled_filesystem_tools_declare_filesystem_access():
    registry = build_default_registry()
    for name in ENABLED_TOOL_NAMES:
        assert registry.get(name).permissions.filesystem_access is True


def test_read_only_tools_declare_a_read_side_effect():
    registry = build_default_registry()
    for name in ("fs.list", "fs.stat", "fs.search", "fs.read"):
        assert registry.get(name).permissions.side_effect is SideEffect.READ


def test_tools_endpoint_exposes_the_filesystem_tools():
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/tools").json()
    by_name = {t["name"]: t for t in body}

    for name in ENABLED_TOOL_NAMES + DISABLED_TOOL_NAMES:
        assert name in by_name, name

    assert by_name["fs.read"]["permissions"]["side_effect"] == "read"
    assert by_name["fs.read"]["permissions"]["filesystem_access"] is True
    assert "path" in by_name["fs.read"]["input_schema"]["properties"]

    delete = by_name["fs.delete"]
    assert delete["permissions"]["requires_confirmation"] is True
    assert "DISABLED" in delete["description"]
    assert "authorized" in delete["output_schema"]["properties"]
