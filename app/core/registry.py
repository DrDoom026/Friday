"""Tool registry: register tools and look them up by name."""

import logging
from typing import Iterator

from app.core.errors import ToolAlreadyRegisteredError, ToolNotFoundError
from app.core.tools import Tool, ToolDescriptor

logger = logging.getLogger("firday.registry")


class ToolRegistry:
    """A name -> tool mapping. Not thread-safe; registration happens at import."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, replace: bool = False) -> Tool:
        """Add a tool. Raises :class:`ToolAlreadyRegisteredError` on a name clash."""
        if tool.name in self._tools and not replace:
            raise ToolAlreadyRegisteredError(tool.name)
        self._tools[tool.name] = tool
        logger.info("tool registered (tool=%s, version=%s)", tool.name, tool.version)
        return tool

    def unregister(self, name: str) -> None:
        """Remove a tool. Raises :class:`ToolNotFoundError` if it is not present."""
        if name not in self._tools:
            raise ToolNotFoundError(name)
        del self._tools[name]

    def get(self, name: str) -> Tool:
        """Look up a tool. Raises :class:`ToolNotFoundError` if it is not present."""
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(name) from None

    def try_get(self, name: str) -> Tool | None:
        """Look up a tool, returning ``None`` instead of raising."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[ToolDescriptor]:
        return [ToolDescriptor.from_tool(self._tools[name]) for name in self.names()]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools[name] for name in self.names())

    def __len__(self) -> int:
        return len(self._tools)


default_registry = ToolRegistry()


def register_tool(cls):
    """Class decorator: instantiate a tool and add it to ``default_registry``.

    This is the discovery mechanism — importing a module that uses it makes its
    tools available, and ``build_default_registry`` triggers those imports.
    """
    default_registry.register(cls())
    return cls


def build_default_registry() -> ToolRegistry:
    """Import the built-in tools package and return the populated registry."""
    import app.tools  # noqa: F401  - import for the decorator side effect

    return default_registry
