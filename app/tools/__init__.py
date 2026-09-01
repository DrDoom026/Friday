"""Built-in FIRDAY tools.

Importing this package registers every built-in tool into
``app.core.registry.default_registry``:

- ``echo`` - the Part 2 demo tool.
- ``fs.*`` - the Part 4 filesystem tools.
- ``proc.* service.* docker.* net.* git.*`` - the Part 6 system tools.
"""

from app.tools import filesystem, system
from app.tools.echo import EchoTool

__all__ = ["EchoTool", "filesystem", "system"]
