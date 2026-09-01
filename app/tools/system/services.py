"""Service tools: status, start, stop and restart.

``service.status`` asks systemd and runs for real. Start, stop and restart
change what is running on the machine, so they are registered and refuse.

The status query is ``systemctl show``, not ``systemctl status``: ``show``
emits stable ``Key=Value`` lines meant for machines, exits zero for an unknown
unit (reporting ``LoadState=not-found``), and needs no pager handling. Where
there is no systemd - notably inside FIRDAY's own container - the tool says so
plainly rather than pretending. That is the same graceful degradation Part 5
applies to Tailscale.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.registry import register_tool
from app.system.command import is_available, reject_option_like, run_command_async
from app.system.errors import CommandNotAvailableError, InvalidTargetError
from app.tools.system.base import (
    DeniedSystemTool,
    SystemTool,
    control_permissions,
    read_permissions,
)

DOMAIN = "service"

SERVICE_READ = read_permissions("service.read")
SERVICE_CONTROL = control_permissions("service.control")

SYSTEMCTL = "systemctl"

#: Marker systemd creates on a booted system. Present means there is a manager
#: to talk to; a container with the binary but no manager fails without it.
SYSTEMD_RUNTIME_MARKER = Path("/run/systemd/system")

#: Properties requested from ``systemctl show``. Keeping the list explicit means
#: the parsed output cannot quietly change shape under us.
SHOW_PROPERTIES = (
    "Id",
    "Description",
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "MainPID",
    "ExecMainStartTimestamp",
    "FragmentPath",
    "NRestarts",
    "Result",
)


def systemd_available() -> bool:
    """True when there is a systemd manager on this host to query."""
    return is_available(SYSTEMCTL) and SYSTEMD_RUNTIME_MARKER.exists()


def require_systemd() -> None:
    if not is_available(SYSTEMCTL):
        raise CommandNotAvailableError(
            SYSTEMCTL, "service tools need systemd; this looks like a container"
        )
    if not SYSTEMD_RUNTIME_MARKER.exists():
        raise CommandNotAvailableError(
            SYSTEMCTL,
            f"{SYSTEMCTL} is present but {SYSTEMD_RUNTIME_MARKER} is missing, "
            "so no systemd manager is running here",
        )


def validate_unit(unit: str) -> str:
    """Refuse a unit name that could be read as an option or a path."""
    reject_option_like("unit", unit)
    if "/" in unit or " " in unit:
        raise InvalidTargetError("unit", unit, "must be a bare unit name")
    return unit


def parse_show(stdout: str) -> dict[str, str]:
    """Parse ``systemctl show`` output into a mapping."""
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


class ServiceStatusInput(BaseModel):
    unit: str = Field(
        ...,
        min_length=1,
        description="Unit name, e.g. 'docker' or 'tailscaled.service'. "
        "systemd appends '.service' when no suffix is given.",
    )


class ServiceStatusOutput(BaseModel):
    unit: str
    exists: bool
    active: bool
    description: str = ""
    load_state: str = ""
    active_state: str = ""
    sub_state: str = ""
    unit_file_state: str = ""
    main_pid: int | None = None
    started_at: str | None = None
    fragment_path: str | None = None
    restart_count: int | None = None
    result: str | None = None


@register_tool
class ServiceStatusTool(SystemTool):
    name = "service.status"
    domain = DOMAIN
    operation = "status"
    description = "Report a systemd unit's load, active and sub state."
    version = "1.0.0"
    permissions = SERVICE_READ
    input_model = ServiceStatusInput
    output_model = ServiceStatusOutput

    def audited_targets(self, payload: ServiceStatusInput) -> list[str]:
        return [payload.unit]

    async def operate(
        self, payload: ServiceStatusInput, context: ToolExecutionContext
    ) -> ServiceStatusOutput:
        require_systemd()
        unit = validate_unit(payload.unit)

        result = await run_command_async(
            [
                SYSTEMCTL,
                "show",
                "--system",
                "--no-pager",
                f"--property={','.join(SHOW_PROPERTIES)}",
                unit,
            ],
            timeout=self._timeout(),
        )
        values = parse_show(result.stdout)
        if not values:
            result.require_ok()

        load_state = values.get("LoadState", "")
        active_state = values.get("ActiveState", "")
        main_pid = _int_or_none(values.get("MainPID"))
        started = values.get("ExecMainStartTimestamp") or None

        return ServiceStatusOutput(
            unit=values.get("Id") or unit,
            exists=load_state not in ("not-found", ""),
            active=active_state == "active",
            description=values.get("Description", ""),
            load_state=load_state,
            active_state=active_state,
            sub_state=values.get("SubState", ""),
            unit_file_state=values.get("UnitFileState", ""),
            main_pid=main_pid or None,
            started_at=started,
            fragment_path=values.get("FragmentPath") or None,
            restart_count=_int_or_none(values.get("NRestarts")),
            result=values.get("Result") or None,
        )

    @staticmethod
    def _timeout() -> float:
        from app.config import settings

        return settings.system_command_timeout_seconds


def _int_or_none(raw: str | None) -> int | None:
    try:
        return int(raw) if raw is not None and raw != "" else None
    except ValueError:
        return None


# --- service.start / service.stop / service.restart ------------------------


class ServiceControlInput(BaseModel):
    unit: str = Field(..., min_length=1, description="Unit name to act on.")


class _DeniedServiceTool(DeniedSystemTool):
    domain = DOMAIN
    version = "0.1.0"
    permissions = SERVICE_CONTROL
    input_model = ServiceControlInput
    target_arguments = ("unit",)


@register_tool
class StartServiceTool(_DeniedServiceTool):
    name = "service.start"
    operation = "start"
    description = (
        "Start a systemd unit. DISABLED: always refuses, pending the "
        "Security/Permission Engine (PART 7)."
    )


@register_tool
class StopServiceTool(_DeniedServiceTool):
    name = "service.stop"
    operation = "stop"
    description = (
        "Stop a systemd unit. DISABLED: always refuses, pending the "
        "Security/Permission Engine (PART 7)."
    )


@register_tool
class RestartServiceTool(_DeniedServiceTool):
    name = "service.restart"
    operation = "restart"
    description = (
        "Restart a systemd unit. DISABLED: always refuses, pending the "
        "Security/Permission Engine (PART 7)."
    )
