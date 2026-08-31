"""Tool abstraction.

Interface only for Part 1 — no concrete tools and no registry (that is Part 2).
"""

from typing import Any, Mapping, Protocol, runtime_checkable

from app.core.context import RequestContext
from app.core.models import ToolResult


@runtime_checkable
class Tool(Protocol):
    """Something Core can execute on a planner's behalf."""

    name: str
    description: str

    async def execute(
        self, arguments: Mapping[str, Any], context: RequestContext
    ) -> ToolResult: ...
