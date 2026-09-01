"""System tools (PART 6).

Importing this package registers all twenty-two operations across five
domains. Fourteen of them execute for real:

    proc.list         proc.inspect
    service.status
    docker.containers docker.inspect  docker.logs  docker.images
    net.interfaces    net.routes      net.ping     net.dns
    git.status        git.branches    git.clone

The remaining eight change system state. They are registered and schema
complete, and their authorization stub always denies until PART 7:

    proc.terminate
    service.start     service.stop    service.restart
    docker.start      docker.stop     docker.restart
    git.pull

The split is the one Part 4 drew: observing runs, changing waits. ``git.clone``
sits on the enabled side for the same reason ``fs.copy`` does - it only ever
creates something new, and refuses any destination that already exists.
"""

from app.tools.system import docker, git, network, processes, services
from app.tools.system.base import (
    BLOCKED_UNTIL,
    NOT_AUTHORIZED_REASON,
    DeniedSystemTool,
    NotAuthorizedOutput,
    SystemTool,
)

#: Tools that actually execute in Part 6.
ENABLED_TOOL_NAMES = (
    "docker.containers",
    "docker.images",
    "docker.inspect",
    "docker.logs",
    "git.branches",
    "git.clone",
    "git.status",
    "net.dns",
    "net.interfaces",
    "net.ping",
    "net.routes",
    "proc.inspect",
    "proc.list",
    "service.status",
)

#: Tools that are registered but always refuse until Part 7.
DISABLED_TOOL_NAMES = (
    "docker.restart",
    "docker.start",
    "docker.stop",
    "git.pull",
    "proc.terminate",
    "service.restart",
    "service.start",
    "service.stop",
)

__all__ = [
    "BLOCKED_UNTIL",
    "DISABLED_TOOL_NAMES",
    "DeniedSystemTool",
    "ENABLED_TOOL_NAMES",
    "NOT_AUTHORIZED_REASON",
    "NotAuthorizedOutput",
    "SystemTool",
    "docker",
    "git",
    "network",
    "processes",
    "services",
]
