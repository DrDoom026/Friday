"""Process tools: list, inspect and terminate.

``proc.list`` and ``proc.inspect`` observe and run for real. ``proc.terminate``
sends a signal, which is not something FIRDAY may decide on its own yet, so it
is registered and refuses.

Everything is read out of ``/proc`` - see :mod:`app.system.procfs` for why.
"""

import asyncio

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.registry import register_tool
from app.system import procfs
from app.system.errors import ProcessNotFoundError
from app.tools.system.base import (
    DeniedSystemTool,
    SystemTool,
    control_permissions,
    read_permissions,
)

DOMAIN = "process"

PROCESS_READ = read_permissions("process.read")
PROCESS_CONTROL = control_permissions("process.terminate")

#: Signals a caller may name. Refused for now regardless; the list exists so the
#: schema is honest about what the tool will accept once PART 7 allows it.
ALLOWED_SIGNALS = ("TERM", "INT", "HUP", "QUIT", "KILL", "USR1", "USR2")


class ProcessSummary(BaseModel):
    """One process, as reported by ``proc.list``."""

    pid: int
    ppid: int
    name: str
    state: str
    state_description: str
    user: str
    uid: int
    command: str
    threads: int
    rss_bytes: int
    vms_bytes: int
    cpu_seconds: float
    nice: int
    started_at: str


def _summary(info: procfs.ProcessInfo) -> ProcessSummary:
    return ProcessSummary(**vars(info))


# --- proc.list -------------------------------------------------------------


class ProcessListInput(BaseModel):
    name_contains: str | None = Field(
        None, description="Case-insensitive substring matched against name and command line."
    )
    user: str | None = Field(None, description="Only processes owned by this username.")
    sort_by: str = Field(
        "pid", pattern="^(pid|name|rss_bytes|cpu_seconds|started_at)$", description="Sort key."
    )
    descending: bool = Field(False, description="Reverse the sort.")
    max_entries: int | None = Field(
        None, ge=1, description="Cap on returned processes. Clamped to the configured limit."
    )


class ProcessListOutput(BaseModel):
    processes: list[ProcessSummary]
    process_count: int
    total_visible: int
    truncated: bool = False


@register_tool
class ListProcessesTool(SystemTool):
    name = "proc.list"
    domain = DOMAIN
    operation = "list"
    description = "List the processes visible to FIRDAY, optionally filtered by name or user."
    version = "1.0.0"
    permissions = PROCESS_READ
    input_model = ProcessListInput
    output_model = ProcessListOutput

    async def operate(
        self, payload: ProcessListInput, context: ToolExecutionContext
    ) -> ProcessListOutput:
        found = await asyncio.to_thread(procfs.read_processes)
        total = len(found)

        if payload.name_contains:
            needle = payload.name_contains.lower()
            found = [
                p for p in found if needle in p.name.lower() or needle in p.command.lower()
            ]
        if payload.user:
            found = [p for p in found if p.user == payload.user]

        found.sort(key=lambda p: getattr(p, payload.sort_by), reverse=payload.descending)

        limit = self._limit(payload.max_entries)
        return ProcessListOutput(
            processes=[_summary(p) for p in found[:limit]],
            process_count=min(len(found), limit),
            total_visible=total,
            truncated=len(found) > limit,
        )

    @staticmethod
    def _limit(requested: int | None) -> int:
        from app.config import settings

        limit = settings.system_max_processes
        return min(limit, requested) if requested is not None else limit


# --- proc.inspect ----------------------------------------------------------


class ProcessInspectInput(BaseModel):
    pid: int = Field(..., ge=1, description="PID of the process to inspect.")


class ProcessInspectOutput(BaseModel):
    process: ProcessSummary
    executable: str | None = None
    working_directory: str | None = None
    command_arguments: list[str] = Field(default_factory=list)
    open_file_count: int | None = None
    children: list[int] = Field(default_factory=list)
    unreadable: list[str] = Field(
        default_factory=list,
        description="Fields this process's owner did not permit FIRDAY to read.",
    )


@register_tool
class InspectProcessTool(SystemTool):
    name = "proc.inspect"
    domain = DOMAIN
    operation = "inspect"
    description = "Return detailed information about one process by PID."
    version = "1.0.0"
    permissions = PROCESS_READ
    input_model = ProcessInspectInput
    output_model = ProcessInspectOutput

    def audited_targets(self, payload: ProcessInspectInput) -> list[str]:
        return [str(payload.pid)]

    async def operate(
        self, payload: ProcessInspectInput, context: ToolExecutionContext
    ) -> ProcessInspectOutput:
        detail = await asyncio.to_thread(procfs.read_process_detail, payload.pid)
        if detail is None:
            raise ProcessNotFoundError(payload.pid)

        return ProcessInspectOutput(
            process=_summary(detail.info),
            executable=detail.executable,
            working_directory=detail.working_directory,
            command_arguments=list(detail.command_arguments),
            open_file_count=detail.open_file_count,
            children=list(detail.children),
            unreadable=list(detail.unreadable),
        )


# --- proc.terminate --------------------------------------------------------


class ProcessTerminateInput(BaseModel):
    pid: int = Field(..., ge=1, description="PID of the process to signal.")
    signal: str = Field(
        "TERM",
        description="Signal name without the SIG prefix, e.g. 'TERM' or 'KILL'.",
    )


@register_tool
class TerminateProcessTool(DeniedSystemTool):
    name = "proc.terminate"
    domain = DOMAIN
    operation = "terminate"
    description = (
        "Send a termination signal to a process. DISABLED: always refuses, "
        "pending the Security/Permission Engine (PART 7)."
    )
    version = "0.1.0"
    permissions = PROCESS_CONTROL
    input_model = ProcessTerminateInput
    target_arguments = ("pid",)
