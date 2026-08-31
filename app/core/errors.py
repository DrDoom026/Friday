"""Tool-specific error types.

Everything raised by the tool framework derives from ``ToolError`` so callers
can catch the whole family with one except clause.
"""

from typing import Any


class ToolError(Exception):
    """Base class for every tool framework failure."""


class ToolNotFoundError(ToolError, KeyError):
    """A tool was looked up by a name the registry does not know."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"no tool registered under name {name!r}")

    def __str__(self) -> str:
        # KeyError.__str__ would repr the message; keep it readable.
        return self.args[0]


class ToolAlreadyRegisteredError(ToolError):
    """A name collision at registration time."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"a tool is already registered under name {name!r}")


class ToolValidationError(ToolError):
    """Arguments did not match a tool's input schema."""

    def __init__(self, name: str, errors: list[dict[str, Any]] | None = None) -> None:
        self.name = name
        self.errors = errors or []
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', ())) or '<root>'}: {e.get('msg', '')}"
            for e in self.errors
        )
        super().__init__(
            f"invalid arguments for tool {name!r}" + (f" ({detail})" if detail else "")
        )


class ToolExecutionError(ToolError):
    """A tool failed while running. Tools raise this to report a clean failure."""

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        super().__init__(f"tool {name!r} failed: {message}")
