"""Built-in FIRDAY tools.

Importing this package registers every built-in tool into
``app.core.registry.default_registry``. Part 2 ships exactly one: ``echo``.
"""

from app.tools.echo import EchoTool

__all__ = ["EchoTool"]
