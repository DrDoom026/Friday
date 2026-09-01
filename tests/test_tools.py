"""Tests for the Part 2 tool framework: registry, validation and the echo tool."""

import asyncio

import pytest
from pydantic import BaseModel

from app.core.context import RequestContext, ToolExecutionContext
from app.core.errors import (
    ToolAlreadyRegisteredError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from app.core.models import ExecutionStatus
from app.core.registry import ToolRegistry, build_default_registry
from app.core.tools import BaseTool, SideEffect, Tool, ToolDescriptor, ToolPermissions
from app.tools.echo import EchoInput, EchoOutput, EchoTool


def run(coro):
    return asyncio.run(coro)


def make_context(tool_name: str = "echo") -> ToolExecutionContext:
    return ToolExecutionContext.for_tool(RequestContext.create(request_id="corr-t"), tool_name)


# --- ToolExecutionContext --------------------------------------------------


def test_execution_context_inherits_the_request_correlation_id():
    request = RequestContext.create(request_id="corr-abc")
    context = ToolExecutionContext.for_tool(request, "echo")
    assert context.request_id == "corr-abc"
    assert context.tool_name == "echo"


def test_execution_context_has_a_unique_invocation_id():
    request = RequestContext.create(request_id="corr-abc")
    first = ToolExecutionContext.for_tool(request, "echo")
    second = ToolExecutionContext.for_tool(request, "echo")
    assert first.invocation_id != second.invocation_id


def test_execution_context_logger_stamps_the_request_id(caplog):
    import logging

    context = ToolExecutionContext.for_tool(RequestContext.create(request_id="corr-z"), "echo")
    with caplog.at_level(logging.INFO, logger="firday.tool"):
        context.logger().info("running")
    assert [r.request_id for r in caplog.records] == ["corr-z"]


# --- registry --------------------------------------------------------------


def test_registry_registers_and_gets_by_name():
    registry = ToolRegistry()
    tool = registry.register(EchoTool())
    assert registry.get("echo") is tool
    assert "echo" in registry
    assert len(registry) == 1
    assert registry.names() == ["echo"]


def test_registry_get_raises_for_unknown_name():
    with pytest.raises(ToolNotFoundError, match="echo"):
        ToolRegistry().get("echo")


def test_registry_try_get_returns_none_for_unknown_name():
    assert ToolRegistry().try_get("nope") is None


def test_registry_rejects_duplicate_names():
    registry = ToolRegistry()
    registry.register(EchoTool())
    with pytest.raises(ToolAlreadyRegisteredError, match="echo"):
        registry.register(EchoTool())


def test_registry_allows_explicit_replacement():
    registry = ToolRegistry()
    registry.register(EchoTool())
    replacement = registry.register(EchoTool(), replace=True)
    assert registry.get("echo") is replacement
    assert len(registry) == 1


def test_registry_unregisters():
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.unregister("echo")
    assert "echo" not in registry
    with pytest.raises(ToolNotFoundError):
        registry.unregister("echo")


def test_registry_describe_exposes_metadata_and_schemas():
    registry = ToolRegistry()
    registry.register(EchoTool())
    [descriptor] = registry.describe()
    assert isinstance(descriptor, ToolDescriptor)
    assert descriptor.name == "echo"
    assert descriptor.version == "1.0.0"
    assert descriptor.permissions.side_effect is SideEffect.NONE
    assert "message" in descriptor.input_schema["properties"]
    assert "length" in descriptor.output_schema["properties"]


def test_registry_iteration_is_sorted_by_name():
    class ZebraTool(EchoTool):
        name = "zebra"

    registry = ToolRegistry()
    registry.register(ZebraTool())
    registry.register(EchoTool())
    assert [t.name for t in registry] == ["echo", "zebra"]


# --- discovery -------------------------------------------------------------


def test_build_default_registry_discovers_the_echo_tool():
    registry = build_default_registry()
    assert "echo" in registry
    assert registry.get("echo").name == "echo"


def test_echo_remains_the_only_tool_outside_a_family():
    """Every tool added since Part 2 belongs to a named family; `echo` stands alone."""
    families = ("docker.", "fs.", "git.", "net.", "proc.", "service.")
    names = build_default_registry().names()
    assert [n for n in names if not n.startswith(families)] == ["echo"]


# --- the echo tool ---------------------------------------------------------


def test_echo_tool_satisfies_the_tool_protocol():
    assert isinstance(EchoTool(), Tool)


def test_echo_tool_declares_its_identity_and_permissions():
    tool = EchoTool()
    assert tool.name == "echo"
    assert tool.description
    assert tool.version == "1.0.0"
    assert tool.permissions == ToolPermissions(side_effect=SideEffect.NONE)
    assert tool.permissions.network_access is False
    assert tool.permissions.filesystem_access is False
    assert tool.permissions.requires_confirmation is False


def test_echo_tool_exposes_json_schemas():
    tool = EchoTool()
    assert tool.input_schema()["properties"]["message"]["type"] == "string"
    assert tool.output_schema()["properties"]["length"]["type"] == "integer"


def test_echo_tool_executes_and_returns_a_success_result():
    result = run(EchoTool().execute({"message": "hello"}, make_context()))
    assert result.status is ExecutionStatus.SUCCESS
    assert result.tool_name == "echo"
    assert result.output == {"message": "hello", "length": 5}
    assert result.error is None
    assert result.duration_ms is not None and result.duration_ms >= 0


def test_echo_tool_output_matches_its_declared_output_model():
    result = run(EchoTool().execute({"message": "hi"}, make_context()))
    assert EchoOutput.model_validate(result.output) == EchoOutput(message="hi", length=2)


# --- input validation ------------------------------------------------------


def test_validate_input_coerces_into_the_input_model():
    payload = EchoTool().validate_input({"message": "hello"})
    assert isinstance(payload, EchoInput)
    assert payload.message == "hello"


def test_validate_input_raises_on_missing_field():
    with pytest.raises(ToolValidationError, match="echo"):
        EchoTool().validate_input({})


def test_validate_input_raises_on_wrong_type():
    with pytest.raises(ToolValidationError) as exc_info:
        EchoTool().validate_input({"message": 123})
    assert exc_info.value.name == "echo"
    assert exc_info.value.errors


def test_validate_input_enforces_field_constraints():
    with pytest.raises(ToolValidationError):
        EchoTool().validate_input({"message": ""})


def test_execute_returns_an_error_result_instead_of_raising_on_bad_input():
    result = run(EchoTool().execute({}, make_context()))
    assert result.status is ExecutionStatus.ERROR
    assert result.output is None
    assert "invalid arguments" in result.error


# --- failure handling in BaseTool.execute ----------------------------------


class _ExplodingTool(BaseTool):
    name = "exploding"
    description = "always fails"
    input_model = EchoInput
    output_model = EchoOutput

    async def run(self, payload, context):
        raise ToolExecutionError(self.name, "deliberate failure")


class _CrashingTool(_ExplodingTool):
    name = "crashing"

    async def run(self, payload, context):
        raise RuntimeError("unexpected boom")


def test_declared_tool_failure_becomes_a_structured_error_result():
    result = run(_ExplodingTool().execute({"message": "x"}, make_context("exploding")))
    assert result.status is ExecutionStatus.ERROR
    assert "deliberate failure" in result.error


def test_unexpected_exception_is_contained_as_an_error_result():
    result = run(_CrashingTool().execute({"message": "x"}, make_context("crashing")))
    assert result.status is ExecutionStatus.ERROR
    assert "RuntimeError" in result.error


# --- error types -----------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ToolNotFoundError("x"),
        ToolAlreadyRegisteredError("x"),
        ToolValidationError("x"),
        ToolExecutionError("x", "boom"),
    ],
)
def test_every_tool_error_derives_from_tool_error(error):
    assert isinstance(error, ToolError)
    assert "x" in str(error)


# --- permission metadata is metadata only ----------------------------------


def test_permissions_are_immutable_metadata():
    permissions = ToolPermissions(side_effect=SideEffect.WRITE, scopes=("fs.write",))
    with pytest.raises(Exception):
        permissions.side_effect = SideEffect.NONE
    assert permissions.scopes == ("fs.write",)


def test_permissions_default_to_the_most_restrictive_shape():
    permissions = ToolPermissions()
    assert permissions.side_effect is SideEffect.NONE
    assert permissions.scopes == ()
    assert not any(
        (
            permissions.network_access,
            permissions.filesystem_access,
            permissions.requires_confirmation,
        )
    )


def test_base_tool_subclass_only_needs_run():
    class MinimalTool(BaseTool):
        name = "minimal"
        description = "minimal"
        input_model = EchoInput
        output_model = EchoOutput

        async def run(self, payload, context):
            return EchoOutput(message=payload.message, length=len(payload.message))

    tool = MinimalTool()
    assert tool.version == "0.1.0"
    assert isinstance(tool.permissions, ToolPermissions)
    assert isinstance(tool.input_model, type) and issubclass(tool.input_model, BaseModel)
    assert run(tool.execute({"message": "ok"}, make_context("minimal"))).status is (
        ExecutionStatus.SUCCESS
    )
