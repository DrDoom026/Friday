"""Docker tools: containers, inspect, logs, images, start, stop and restart.

The four read tools run for real against the local engine. Start, stop and
restart change what is running, so they are registered and refuse.

Everything goes through :class:`~app.system.docker_api.DockerClient`, which
speaks the engine's HTTP API over its unix socket and exposes no way to POST.
The refusal is therefore not the only thing standing between a caller and a
stopped container - there is no code path to one at all.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.core.context import ToolExecutionContext
from app.core.registry import register_tool
from app.system.command import reject_option_like
from app.system.docker_api import DockerClient, demultiplex, get_default_client
from app.system.errors import DockerUnavailableError
from app.tools.system.base import (
    DeniedSystemTool,
    SystemTool,
    control_permissions,
    read_permissions,
)

DOMAIN = "docker"

DOCKER_READ = read_permissions("docker.read")
DOCKER_CONTROL = control_permissions("docker.control")


class DockerTool(SystemTool):
    """A system tool that talks to the local Docker engine."""

    def __init__(self, client: DockerClient | None = None, *, policy=None) -> None:
        super().__init__(policy=policy)
        self._client = client

    @property
    def client(self) -> DockerClient:
        """The engine client - the process default unless one was injected."""
        return self._client if self._client is not None else get_default_client()

    def require_engine(self) -> DockerClient:
        client = self.client
        if not client.is_available():
            raise DockerUnavailableError(
                client.socket_path,
                "socket is missing or nothing is listening; mount it read-only "
                "into the container to enable the docker.* tools",
            )
        return client


def _name(raw: Any) -> str:
    """Container names come back from the engine with a leading slash."""
    names = raw if isinstance(raw, list) else []
    return str(names[0]).lstrip("/") if names else ""


def _iso(epoch: Any) -> str:
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _ports(raw: Any) -> list[str]:
    entries = []
    for port in raw if isinstance(raw, list) else []:
        if not isinstance(port, dict):
            continue
        private = f"{port.get('PrivatePort', '')}/{port.get('Type', 'tcp')}"
        public = port.get("PublicPort")
        host = port.get("IP") or ""
        entries.append(f"{host}:{public}->{private}" if public else private)
    return sorted(set(entries))


# --- docker.containers -----------------------------------------------------


class ContainerSummary(BaseModel):
    id: str
    name: str
    image: str
    state: str
    status: str
    created_at: str = ""
    command: str = ""
    ports: list[str] = Field(default_factory=list)


class ContainerListInput(BaseModel):
    all: bool = Field(False, description="Include stopped containers, not just running ones.")
    max_entries: int | None = Field(None, ge=1, description="Cap on returned containers.")


class ContainerListOutput(BaseModel):
    containers: list[ContainerSummary]
    container_count: int
    truncated: bool = False


@register_tool
class ListContainersTool(DockerTool):
    name = "docker.containers"
    domain = DOMAIN
    operation = "containers"
    description = "List Docker containers on this host."
    version = "1.0.0"
    permissions = DOCKER_READ
    input_model = ContainerListInput
    output_model = ContainerListOutput

    async def operate(
        self, payload: ContainerListInput, context: ToolExecutionContext
    ) -> ContainerListOutput:
        client = self.require_engine()
        raw = await asyncio.to_thread(client.containers, all_containers=payload.all)

        containers = [
            ContainerSummary(
                id=str(entry.get("Id", ""))[:12],
                name=_name(entry.get("Names")),
                image=str(entry.get("Image", "")),
                state=str(entry.get("State", "")),
                status=str(entry.get("Status", "")),
                created_at=_iso(entry.get("Created")),
                command=str(entry.get("Command", "")),
                ports=_ports(entry.get("Ports")),
            )
            for entry in raw
        ]
        containers.sort(key=lambda c: c.name)

        limit = payload.max_entries or len(containers)
        return ContainerListOutput(
            containers=containers[:limit],
            container_count=min(len(containers), limit),
            truncated=len(containers) > limit,
        )


# --- docker.inspect --------------------------------------------------------


class ContainerReferenceInput(BaseModel):
    container: str = Field(..., min_length=1, description="Container name or id.")


class ContainerInspectOutput(BaseModel):
    id: str
    name: str
    image: str
    state: str
    status: str
    running: bool
    restart_count: int = 0
    exit_code: int | None = None
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    restart_policy: str = ""
    command: list[str] = Field(default_factory=list)
    ports: list[str] = Field(default_factory=list)
    mounts: list[str] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    health: str | None = None


def _inspect_ports(network_settings: dict) -> list[str]:
    entries = []
    for private, bindings in (network_settings.get("Ports") or {}).items():
        if not bindings:
            entries.append(str(private))
            continue
        for binding in bindings:
            host = binding.get("HostIp") or ""
            entries.append(f"{host}:{binding.get('HostPort', '')}->{private}")
    return sorted(set(entries))


@register_tool
class InspectContainerTool(DockerTool):
    name = "docker.inspect"
    domain = DOMAIN
    operation = "inspect"
    description = "Return detailed information about one Docker container."
    version = "1.0.0"
    permissions = DOCKER_READ
    input_model = ContainerReferenceInput
    output_model = ContainerInspectOutput

    def audited_targets(self, payload: ContainerReferenceInput) -> list[str]:
        return [payload.container]

    async def operate(
        self, payload: ContainerReferenceInput, context: ToolExecutionContext
    ) -> ContainerInspectOutput:
        client = self.require_engine()
        reference = reject_option_like("container", payload.container)
        raw = await asyncio.to_thread(client.inspect_container, reference)

        state = raw.get("State") or {}
        config = raw.get("Config") or {}
        host_config = raw.get("HostConfig") or {}
        network_settings = raw.get("NetworkSettings") or {}
        health = (state.get("Health") or {}).get("Status")

        return ContainerInspectOutput(
            id=str(raw.get("Id", ""))[:12],
            name=str(raw.get("Name", "")).lstrip("/"),
            image=str(config.get("Image", "")),
            state=str(state.get("Status", "")),
            status=str(state.get("Status", "")),
            running=bool(state.get("Running")),
            restart_count=int(raw.get("RestartCount") or 0),
            exit_code=state.get("ExitCode"),
            created_at=str(raw.get("Created", "")),
            started_at=str(state.get("StartedAt", "")),
            finished_at=str(state.get("FinishedAt", "")),
            restart_policy=str((host_config.get("RestartPolicy") or {}).get("Name", "")),
            command=[str(part) for part in (config.get("Cmd") or [])],
            ports=_inspect_ports(network_settings),
            mounts=[
                f"{m.get('Source', '')}:{m.get('Destination', '')}"
                + (":ro" if m.get("RW") is False else "")
                for m in (raw.get("Mounts") or [])
                if isinstance(m, dict)
            ],
            networks=sorted((network_settings.get("Networks") or {}).keys()),
            labels={str(k): str(v) for k, v in (config.get("Labels") or {}).items()},
            health=str(health) if health else None,
        )


# --- docker.logs -----------------------------------------------------------


class ContainerLogsInput(BaseModel):
    container: str = Field(..., min_length=1, description="Container name or id.")
    tail: int = Field(100, ge=1, description="Lines from the end. Clamped to the configured cap.")
    timestamps: bool = Field(False, description="Prefix each line with its timestamp.")
    stdout: bool = True
    stderr: bool = True


class ContainerLogsOutput(BaseModel):
    container: str
    lines: list[str]
    line_count: int
    tail: int
    truncated: bool = False


@register_tool
class ContainerLogsTool(DockerTool):
    name = "docker.logs"
    domain = DOMAIN
    operation = "logs"
    description = "Read the tail of a Docker container's logs."
    version = "1.0.0"
    permissions = DOCKER_READ
    input_model = ContainerLogsInput
    output_model = ContainerLogsOutput

    def audited_targets(self, payload: ContainerLogsInput) -> list[str]:
        return [payload.container]

    async def operate(
        self, payload: ContainerLogsInput, context: ToolExecutionContext
    ) -> ContainerLogsOutput:
        from app.config import settings

        client = self.require_engine()
        reference = reject_option_like("container", payload.container)
        tail = min(payload.tail, settings.system_max_log_lines)

        raw = await asyncio.to_thread(
            client.container_logs,
            reference,
            tail=tail,
            timestamps=payload.timestamps,
            stdout=payload.stdout,
            stderr=payload.stderr,
        )
        text = demultiplex(raw)
        lines = text.splitlines()

        return ContainerLogsOutput(
            container=reference,
            lines=lines,
            line_count=len(lines),
            tail=tail,
            truncated=payload.tail > tail,
        )


# --- docker.images ---------------------------------------------------------


class ImageSummary(BaseModel):
    id: str
    tags: list[str] = Field(default_factory=list)
    size_bytes: int = 0
    created_at: str = ""
    containers: int | None = None


class ImageListInput(BaseModel):
    all: bool = Field(False, description="Include intermediate layers.")
    max_entries: int | None = Field(None, ge=1, description="Cap on returned images.")


class ImageListOutput(BaseModel):
    images: list[ImageSummary]
    image_count: int
    truncated: bool = False


@register_tool
class ListImagesTool(DockerTool):
    name = "docker.images"
    domain = DOMAIN
    operation = "images"
    description = "List the Docker images present on this host."
    version = "1.0.0"
    permissions = DOCKER_READ
    input_model = ImageListInput
    output_model = ImageListOutput

    async def operate(
        self, payload: ImageListInput, context: ToolExecutionContext
    ) -> ImageListOutput:
        client = self.require_engine()
        raw = await asyncio.to_thread(client.images, all_images=payload.all)

        images = [
            ImageSummary(
                id=str(entry.get("Id", "")).removeprefix("sha256:")[:12],
                tags=[str(t) for t in (entry.get("RepoTags") or []) if t != "<none>:<none>"],
                size_bytes=int(entry.get("Size") or 0),
                created_at=_iso(entry.get("Created")),
                containers=(
                    None if entry.get("Containers") in (None, -1) else int(entry["Containers"])
                ),
            )
            for entry in raw
        ]
        images.sort(key=lambda i: (i.tags[0] if i.tags else i.id))

        limit = payload.max_entries or len(images)
        return ImageListOutput(
            images=images[:limit],
            image_count=min(len(images), limit),
            truncated=len(images) > limit,
        )


# --- docker.start / docker.stop / docker.restart ---------------------------


class ContainerControlInput(BaseModel):
    container: str = Field(..., min_length=1, description="Container name or id to act on.")


class _DeniedDockerTool(DeniedSystemTool):
    domain = DOMAIN
    version = "0.1.0"
    permissions = DOCKER_CONTROL
    input_model = ContainerControlInput
    target_arguments = ("container",)


@register_tool
class StartContainerTool(_DeniedDockerTool):
    name = "docker.start"
    operation = "start"
    description = (
        "Start a Docker container. DISABLED: always refuses, pending the "
        "Security/Permission Engine (PART 7)."
    )


@register_tool
class StopContainerTool(_DeniedDockerTool):
    name = "docker.stop"
    operation = "stop"
    description = (
        "Stop a Docker container. DISABLED: always refuses, pending the "
        "Security/Permission Engine (PART 7)."
    )


@register_tool
class RestartContainerTool(_DeniedDockerTool):
    name = "docker.restart"
    operation = "restart"
    description = (
        "Restart a Docker container. DISABLED: always refuses, pending the "
        "Security/Permission Engine (PART 7)."
    )
