"""Tests for PART 6: the high-level software and system tools.

Three things are checked for every tool:

- every read-only tool's happy path, executed for real against this host;
- every state-changing tool's refusal, with the "not yet authorized" shape
  Part 4 established;
- that a refusal really is inert - the process is still running, the container
  was never contacted, the working tree never moved.

The last of those is the point of the file. A deny-stub that returns the right
JSON while quietly doing the work would pass a naive test, so each refusal is
paired with an assertion about the real system: a child process spawned for the
purpose is still alive, a fake Docker engine recorded zero requests, a cloned
repository is still on the commit it was on.
"""

import asyncio
import http.server
import json
import logging
import os
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from app.core.context import RequestContext, ToolExecutionContext
from app.core.models import ExecutionStatus
from app.core.registry import build_default_registry
from app.core.tools import SideEffect, Tool
from app.fs.policy import FilesystemPolicy
from app.system import procfs
from app.system.audit import SYSTEM_AUDIT_LOGGER
from app.system.command import (
    CommandResult,
    is_available,
    reject_option_like,
    run_command,
)
from app.system.docker_api import DockerClient, demultiplex
from app.system.errors import CommandNotAvailableError, InvalidTargetError
from app.tools.system import DISABLED_TOOL_NAMES, ENABLED_TOOL_NAMES
from app.tools.system.base import NOT_AUTHORIZED_REASON
from app.tools.system.docker import (
    ContainerLogsTool,
    InspectContainerTool,
    ListContainersTool,
    ListImagesTool,
    RestartContainerTool,
    StartContainerTool,
    StopContainerTool,
)
from app.tools.system.git import (
    GitBranchesTool,
    GitCloneTool,
    GitPullTool,
    GitStatusTool,
    validate_clone_url,
)
from app.tools.system.network import (
    DnsTool,
    NetworkInterfacesTool,
    NetworkRoutesTool,
    PingTool,
    read_resolv_conf,
)
from app.tools.system.processes import (
    InspectProcessTool,
    ListProcessesTool,
    TerminateProcessTool,
)
from app.tools.system.services import (
    RestartServiceTool,
    ServiceStatusTool,
    StartServiceTool,
    StopServiceTool,
    parse_show,
    systemd_available,
    validate_unit,
)


def run(coro):
    return asyncio.run(coro)


def context_for(tool_name: str, request_id: str = "corr-sys") -> ToolExecutionContext:
    return ToolExecutionContext.for_tool(RequestContext.create(request_id=request_id), tool_name)


def call(tool, arguments, request_id="corr-sys"):
    """Execute an already-constructed tool and return its ToolResult."""
    return run(tool.execute(arguments, context_for(tool.name, request_id)))


def succeeds(result):
    assert result.status is ExecutionStatus.SUCCESS, result.error
    return result.output


needs_systemd = pytest.mark.skipif(
    not systemd_available(), reason="no systemd manager on this host"
)
needs_ip = pytest.mark.skipif(not is_available("ip"), reason="iproute2 is not installed")
needs_ping = pytest.mark.skipif(not is_available("ping"), reason="ping is not installed")
needs_git = pytest.mark.skipif(not is_available("git"), reason="git is not installed")


@pytest.fixture
def sandbox(tmp_path):
    """An allowed root for the tools that resolve paths through the Part 4 policy."""
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def policy(sandbox):
    return FilesystemPolicy([sandbox])


# ===========================================================================
# The command runner
# ===========================================================================


def test_run_command_captures_output_and_exit_code():
    result = run_command(["/bin/sh", "-c", "printf hello; exit 3"])
    assert isinstance(result, CommandResult)
    assert result.stdout == "hello"
    assert result.returncode == 3
    assert result.ok is False


def test_run_command_raises_for_a_missing_binary():
    with pytest.raises(CommandNotAvailableError):
        run_command(["firday-no-such-binary"])


def test_run_command_kills_a_command_that_outlives_its_timeout():
    from app.system.errors import CommandTimedOutError

    with pytest.raises(CommandTimedOutError):
        run_command(["/bin/sh", "-c", "sleep 5"], timeout=0.2)


def test_run_command_never_uses_a_shell():
    """Metacharacters are inert because there is no shell to interpret them."""
    result = run_command(["/bin/echo", "a; rm -rf /", "&&", "$(whoami)"])
    assert result.stdout.strip() == "a; rm -rf / && $(whoami)"


@pytest.mark.parametrize(
    "value", ["--upload-pack=evil", "-x", "with\nnewline", "", "   ", "with\x00nul"]
)
def test_option_like_arguments_are_refused(value):
    with pytest.raises(InvalidTargetError):
        reject_option_like("host", value)


def test_acceptable_arguments_pass_through():
    assert reject_option_like("host", "example.com") == "example.com"


# ===========================================================================
# PROCESSES
# ===========================================================================


def test_proc_list_reports_this_process():
    output = succeeds(call(ListProcessesTool(), {"max_entries": 10000}))
    pids = {p["pid"] for p in output["processes"]}
    assert os.getpid() in pids
    assert output["total_visible"] >= output["process_count"]


def test_proc_list_filters_by_name_substring():
    output = succeeds(call(ListProcessesTool(), {"name_contains": "python"}))
    assert output["processes"]
    for entry in output["processes"]:
        assert "python" in entry["name"].lower() or "python" in entry["command"].lower()


def test_proc_list_filters_by_user():
    me = procfs.read_process(os.getpid())
    output = succeeds(call(ListProcessesTool(), {"user": me.user}))
    assert output["processes"]
    assert {p["user"] for p in output["processes"]} == {me.user}


def test_proc_list_truncates_and_says_so():
    output = succeeds(call(ListProcessesTool(), {"max_entries": 1}))
    assert output["process_count"] == 1
    assert output["truncated"] is True


def test_proc_list_sorts_on_the_requested_key():
    output = succeeds(call(ListProcessesTool(), {"sort_by": "rss_bytes", "descending": True}))
    sizes = [p["rss_bytes"] for p in output["processes"]]
    assert sizes == sorted(sizes, reverse=True)


def test_proc_inspect_describes_this_process():
    output = succeeds(call(InspectProcessTool(), {"pid": os.getpid()}))
    assert output["process"]["pid"] == os.getpid()
    assert output["process"]["state_description"]
    assert output["executable"]
    assert output["working_directory"] == os.getcwd()
    assert output["open_file_count"] >= 1


def test_proc_inspect_reports_a_missing_pid_as_an_error():
    free = _unused_pid()
    result = call(InspectProcessTool(), {"pid": free})
    assert result.status is ExecutionStatus.ERROR
    assert f"no process with pid {free}" in result.error


def test_proc_inspect_rejects_a_nonsense_pid():
    result = call(InspectProcessTool(), {"pid": 0})
    assert result.status is ExecutionStatus.ERROR
    assert "invalid arguments" in result.error


def _unused_pid() -> int:
    """A PID that is not in use. Racy in principle, stable enough in practice."""
    taken = set(procfs.iter_pids())
    for candidate in range(4_000_000, 4_000_500):
        if candidate not in taken:
            return candidate
    raise AssertionError("could not find an unused pid")


@pytest.fixture
def live_child():
    """A real child process, cleaned up however the test leaves it."""
    child = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        yield child
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_proc_terminate_refuses_with_a_not_yet_authorized_result(live_child):
    result = call(TerminateProcessTool(), {"pid": live_child.pid, "signal": "TERM"})

    assert result.status is ExecutionStatus.ERROR
    assert "not yet authorized" in result.error
    assert result.output["authorized"] is False
    assert result.output["reason"] == NOT_AUTHORIZED_REASON
    assert "PART 7" in result.output["blocked_until"]
    assert result.output["domain"] == "process"
    assert result.output["targets"] == [str(live_child.pid)]
    assert result.duration_ms is not None


def test_proc_terminate_does_not_signal_the_process(live_child):
    """The real assertion: the child is spawned, refused, and still running."""
    assert live_child.poll() is None

    call(TerminateProcessTool(), {"pid": live_child.pid, "signal": "KILL"})

    time.sleep(0.2)
    assert live_child.poll() is None, "the child process was signalled"
    assert procfs.read_process(live_child.pid) is not None


def test_proc_terminate_denies_before_validating_input():
    result = call(TerminateProcessTool(), {"nonsense": True})
    assert result.status is ExecutionStatus.ERROR
    assert "not yet authorized" in result.error


def test_proc_terminate_authorization_stub_always_denies():
    tool = TerminateProcessTool()
    assert tool.is_authorized({"pid": 1}, context_for(tool.name)) is False


# ===========================================================================
# SERVICES
# ===========================================================================


def test_parse_show_reads_key_value_lines():
    parsed = parse_show("Id=docker.service\nActiveState=active\nDescription=A=B\n\n")
    assert parsed["Id"] == "docker.service"
    assert parsed["ActiveState"] == "active"
    assert parsed["Description"] == "A=B"


@pytest.mark.parametrize("unit", ["../etc/passwd", "/usr/bin/x", "-h", "two words"])
def test_validate_unit_refuses_a_unit_that_is_not_a_bare_name(unit):
    with pytest.raises(InvalidTargetError):
        validate_unit(unit)


@needs_systemd
def test_service_status_reports_an_unknown_unit_as_absent():
    output = succeeds(call(ServiceStatusTool(), {"unit": "firday-definitely-absent"}))
    assert output["exists"] is False
    assert output["active"] is False
    assert output["load_state"] == "not-found"


@needs_systemd
def test_service_status_reports_a_real_unit():
    unit = _some_loaded_unit()
    output = succeeds(call(ServiceStatusTool(), {"unit": unit}))
    assert output["exists"] is True
    assert output["unit"].startswith(unit.removesuffix(".service"))
    assert output["load_state"] == "loaded"
    assert output["active_state"] in ("active", "inactive", "failed", "activating")
    assert output["description"]


def _some_loaded_unit() -> str:
    """A unit that is certainly loaded on any systemd host."""
    for candidate in ("systemd-journald.service", "systemd-logind.service", "dbus.service"):
        probe = run_command(["systemctl", "show", "--property=LoadState", candidate])
        if "LoadState=loaded" in probe.stdout:
            return candidate
    pytest.skip("no well-known unit is loaded on this host")


def test_service_status_says_so_where_there_is_no_systemd(monkeypatch):
    monkeypatch.setattr("app.tools.system.services.is_available", lambda _: False)
    result = call(ServiceStatusTool(), {"unit": "docker"})
    assert result.status is ExecutionStatus.ERROR
    assert "not available" in result.error


SERVICE_CONTROL_TOOLS = [StartServiceTool, StopServiceTool, RestartServiceTool]


@pytest.mark.parametrize("tool_cls", SERVICE_CONTROL_TOOLS)
def test_service_control_tools_refuse(tool_cls):
    result = call(tool_cls(), {"unit": "docker"})
    assert result.status is ExecutionStatus.ERROR
    assert "not yet authorized" in result.error
    assert result.output["authorized"] is False
    assert result.output["domain"] == "service"
    assert result.output["targets"] == ["docker"]


@pytest.mark.parametrize("tool_cls", SERVICE_CONTROL_TOOLS)
def test_service_control_tools_never_run_systemctl(tool_cls, monkeypatch):
    """Nothing is executed at all - not a denied command, no command."""

    async def explode(*args, **kwargs):
        raise AssertionError("a disabled service tool tried to run a command")

    monkeypatch.setattr("app.tools.system.services.run_command_async", explode)
    result = call(tool_cls(), {"unit": "docker"})
    assert "not yet authorized" in result.error


@needs_systemd
@pytest.mark.parametrize("tool_cls", SERVICE_CONTROL_TOOLS)
def test_service_control_tools_leave_a_real_unit_untouched(tool_cls):
    unit = _some_loaded_unit()
    before = run_command(["systemctl", "show", "--property=ActiveState,MainPID", unit]).stdout

    call(tool_cls(), {"unit": unit})

    after = run_command(["systemctl", "show", "--property=ActiveState,MainPID", unit]).stdout
    assert after == before, f"{unit} changed state"


# ===========================================================================
# DOCKER
# ===========================================================================


class _FakeEngineHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def address_string(self) -> str:  # unix sockets have no peer address
        return "unix"

    def log_message(self, *args) -> None:  # keep the test output clean
        pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        self.server.requests.append((method, self.path))
        route = self.path.split("?")[0]
        body = self.server.routes.get(route)
        if body is None:
            self._respond(404, json.dumps({"message": f"no such route {route}"}).encode())
            return
        self._respond(200, body if isinstance(body, bytes) else json.dumps(body).encode())

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _FakeEngineServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class FakeDockerEngine:
    """A real unix socket speaking real HTTP, answering canned Docker JSON.

    Hermetic, so the docker tools' parsing is covered on a host with no Docker,
    and - the reason it exists - it records every request it receives, so a
    disabled tool can be shown to have sent nothing at all.
    """

    def __init__(self, routes: dict) -> None:
        self.directory = tempfile.mkdtemp(prefix="firday-docker-")
        self.socket_path = os.path.join(self.directory, "docker.sock")
        self.server = _FakeEngineServer(self.socket_path, _FakeEngineHandler)
        self.server.routes = routes
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def requests(self) -> list:
        return self.server.requests

    def client(self) -> DockerClient:
        return DockerClient(self.socket_path, timeout=5)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.directory, ignore_errors=True)


CONTAINER_JSON = [
    {
        "Id": "abc123def4567890",
        "Names": ["/firday-api"],
        "Image": "friday-api:latest",
        "State": "running",
        "Status": "Up 3 minutes",
        "Created": 1_756_700_000,
        "Command": "uvicorn app.main:app",
        "Ports": [{"IP": "0.0.0.0", "PrivatePort": 8000, "PublicPort": 8000, "Type": "tcp"}],
    }
]

INSPECT_JSON = {
    "Id": "abc123def4567890",
    "Name": "/firday-api",
    "Created": "2026-09-01T10:00:00Z",
    "RestartCount": 2,
    "State": {
        "Status": "running",
        "Running": True,
        "ExitCode": 0,
        "StartedAt": "2026-09-01T10:00:01Z",
        "FinishedAt": "0001-01-01T00:00:00Z",
        "Health": {"Status": "healthy"},
    },
    "Config": {"Image": "friday-api:latest", "Cmd": ["uvicorn"], "Labels": {"role": "api"}},
    "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
    "NetworkSettings": {
        "Ports": {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}]},
        "Networks": {"bridge": {}},
    },
    "Mounts": [{"Source": "/host/workspace", "Destination": "/data/workspace", "RW": True}],
}

IMAGES_JSON = [
    {
        "Id": "sha256:c9f76531ec0312345",
        "RepoTags": ["friday-api:latest"],
        "Size": 254_303_102,
        "Created": 1_756_690_000,
        "Containers": 1,
    },
    {"Id": "sha256:0000aaaa1111", "RepoTags": ["<none>:<none>"], "Size": 10, "Created": 0},
]


def _framed(*frames: tuple[int, str]) -> bytes:
    """Docker's multiplexed log framing: 8-byte header then payload."""
    out = b""
    for stream, text in frames:
        payload = text.encode()
        out += bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload
    return out


@pytest.fixture
def engine():
    fake = FakeDockerEngine(
        {
            "/containers/json": CONTAINER_JSON,
            "/containers/firday-api/json": INSPECT_JSON,
            "/containers/firday-api/logs": _framed(
                (1, "started\n"), (2, "a warning\n"), (1, "serving\n")
            ),
            "/images/json": IMAGES_JSON,
        }
    )
    try:
        yield fake
    finally:
        fake.close()


def test_demultiplex_unframes_a_multiplexed_stream():
    assert demultiplex(_framed((1, "out\n"), (2, "err\n"))) == "out\nerr\n"


def test_demultiplex_passes_through_an_unframed_tty_stream():
    assert demultiplex(b"plain tty output\n") == "plain tty output\n"


def test_demultiplex_handles_an_empty_stream():
    assert demultiplex(b"") == ""


def test_docker_containers_lists_containers(engine):
    output = succeeds(call(ListContainersTool(engine.client()), {"all": True}))
    [container] = output["containers"]
    assert container["name"] == "firday-api"
    assert container["id"] == "abc123def456"
    assert container["state"] == "running"
    assert container["ports"] == ["0.0.0.0:8000->8000/tcp"]
    assert output["container_count"] == 1


def test_docker_inspect_describes_a_container(engine):
    output = succeeds(call(InspectContainerTool(engine.client()), {"container": "firday-api"}))
    assert output["name"] == "firday-api"
    assert output["running"] is True
    assert output["restart_count"] == 2
    assert output["restart_policy"] == "unless-stopped"
    assert output["health"] == "healthy"
    assert output["networks"] == ["bridge"]
    assert output["mounts"] == ["/host/workspace:/data/workspace"]
    assert output["labels"] == {"role": "api"}


def test_docker_logs_returns_demultiplexed_lines(engine):
    output = succeeds(
        call(ContainerLogsTool(engine.client()), {"container": "firday-api", "tail": 10})
    )
    assert output["lines"] == ["started", "a warning", "serving"]
    assert output["line_count"] == 3


def test_docker_logs_clamps_tail_to_the_configured_cap(engine):
    from app.config import settings

    output = succeeds(
        call(
            ContainerLogsTool(engine.client()),
            {"container": "firday-api", "tail": settings.system_max_log_lines + 500},
        )
    )
    assert output["tail"] == settings.system_max_log_lines
    assert output["truncated"] is True


def test_docker_images_lists_images_and_drops_untagged_tags(engine):
    output = succeeds(call(ListImagesTool(engine.client()), {}))
    assert output["image_count"] == 2
    tagged = next(i for i in output["images"] if i["tags"])
    assert tagged["tags"] == ["friday-api:latest"]
    assert tagged["id"] == "c9f76531ec03"
    assert tagged["size_bytes"] == 254_303_102


def test_docker_inspect_reports_an_unknown_container_as_an_error(engine):
    result = call(InspectContainerTool(engine.client()), {"container": "nope"})
    assert result.status is ExecutionStatus.ERROR
    assert "404" in result.error


def test_docker_tools_report_an_unreachable_engine(tmp_path):
    client = DockerClient(str(tmp_path / "absent.sock"), timeout=1)
    result = call(ListContainersTool(client), {})
    assert result.status is ExecutionStatus.ERROR
    assert "Docker is not reachable" in result.error


DOCKER_CONTROL_TOOLS = [StartContainerTool, StopContainerTool, RestartContainerTool]


@pytest.mark.parametrize("tool_cls", DOCKER_CONTROL_TOOLS)
def test_docker_control_tools_refuse(tool_cls, engine):
    result = call(tool_cls(engine.client()), {"container": "firday-api"})
    assert result.status is ExecutionStatus.ERROR
    assert "not yet authorized" in result.error
    assert result.output["authorized"] is False
    assert result.output["domain"] == "docker"
    assert result.output["targets"] == ["firday-api"]


@pytest.mark.parametrize("tool_cls", DOCKER_CONTROL_TOOLS)
def test_docker_control_tools_never_contact_the_engine(tool_cls, engine):
    """A real engine is listening and recording. It hears nothing."""
    succeeds(call(ListContainersTool(engine.client()), {}))
    assert len(engine.requests) == 1

    call(tool_cls(engine.client()), {"container": "firday-api"})

    assert len(engine.requests) == 1, f"the disabled tool called {engine.requests[1:]}"
    assert all(method == "GET" for method, _ in engine.requests)


def test_the_docker_client_offers_no_way_to_post():
    """Defence in depth: the read-only client has no write verb to misuse."""
    assert not hasattr(DockerClient, "post")
    assert not hasattr(DockerClient, "put")
    assert not hasattr(DockerClient, "delete")


# ===========================================================================
# NETWORK
# ===========================================================================


@needs_ip
def test_net_interfaces_reports_loopback():
    output = succeeds(call(NetworkInterfacesTool(), {}))
    loopback = next(i for i in output["interfaces"] if i["loopback"])
    assert loopback["name"] == "lo"
    assert loopback["up"] is True
    assert "127.0.0.1" in [a["address"] for a in loopback["addresses"]]
    assert output["interface_count"] == len(output["interfaces"])


@needs_ip
def test_net_interfaces_can_ask_for_one_interface():
    output = succeeds(call(NetworkInterfacesTool(), {"name": "lo"}))
    assert [i["name"] for i in output["interfaces"]] == ["lo"]


@needs_ip
def test_net_interfaces_refuses_an_option_like_name():
    result = call(NetworkInterfacesTool(), {"name": "-h"})
    assert result.status is ExecutionStatus.ERROR
    assert "not acceptable" in result.error


@needs_ip
def test_net_routes_lists_the_routing_table():
    output = succeeds(call(NetworkRoutesTool(), {"family": "inet"}))
    assert output["route_count"] == len(output["routes"])
    assert all(r["family"] == "inet" for r in output["routes"])
    if output["default_gateway"]:
        assert output["default_interface"]


def test_net_tools_say_so_where_iproute2_is_absent(monkeypatch):
    monkeypatch.setattr("app.tools.system.network.is_available", lambda _: False)
    result = call(NetworkRoutesTool(), {})
    assert result.status is ExecutionStatus.ERROR
    assert "iproute2" in result.error


@needs_ping
def test_net_ping_reaches_loopback():
    output = succeeds(call(PingTool(), {"host": "127.0.0.1", "count": 1}))
    assert output["method"] == "icmp"
    assert output["reachable"] is True
    assert output["packets_sent"] == 1
    assert output["packets_received"] == 1
    assert output["packet_loss_percent"] == 0.0
    assert output["rtt_avg_ms"] is not None


@needs_ping
def test_net_ping_reports_an_unresolvable_host_as_unreachable():
    output = succeeds(
        call(PingTool(), {"host": "firday.invalid", "count": 1, "timeout_seconds": 5})
    )
    assert output["reachable"] is False
    assert output["detail"]


def test_net_ping_probes_a_tcp_port_that_is_listening():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        output = succeeds(call(PingTool(), {"host": "127.0.0.1", "port": port}))

    assert output["method"] == "tcp"
    assert output["reachable"] is True
    assert output["port"] == port
    assert output["rtt_avg_ms"] is not None


def test_net_ping_reports_a_closed_tcp_port_as_unreachable():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    output = succeeds(
        call(PingTool(), {"host": "127.0.0.1", "port": port, "timeout_seconds": 2})
    )
    assert output["reachable"] is False
    assert output["packet_loss_percent"] == 100.0


def test_net_ping_refuses_an_option_like_host():
    result = call(PingTool(), {"host": "-c1000"})
    assert result.status is ExecutionStatus.ERROR
    assert "not acceptable" in result.error


def test_net_dns_resolves_localhost():
    output = succeeds(call(DnsTool(), {"host": "localhost"}))
    assert output["resolved"] is True
    assert "127.0.0.1" in [a["address"] for a in output["addresses"]] or "::1" in [
        a["address"] for a in output["addresses"]
    ]
    assert output["lookup_ms"] > 0
    assert output["error"] is None


def test_net_dns_reports_a_failed_lookup_without_failing_the_tool():
    output = succeeds(call(DnsTool(), {"host": "firday-nothing-here.invalid"}))
    assert output["resolved"] is False
    assert output["addresses"] == []
    assert output["error"]


def test_net_dns_reports_the_configured_nameservers(tmp_path):
    conf = tmp_path / "resolv.conf"
    conf.write_text("# comment\nnameserver 1.1.1.1\nnameserver 9.9.9.9\nsearch lan example\n")
    assert read_resolv_conf(conf) == (["1.1.1.1", "9.9.9.9"], ["lan", "example"])


def test_net_dns_tolerates_a_missing_resolv_conf(tmp_path):
    assert read_resolv_conf(tmp_path / "absent") == ([], [])


# ===========================================================================
# GIT
# ===========================================================================


GIT_ENV = {
    "GIT_AUTHOR_NAME": "FIRDAY Test",
    "GIT_AUTHOR_EMAIL": "test@firday.invalid",
    "GIT_COMMITTER_NAME": "FIRDAY Test",
    "GIT_COMMITTER_EMAIL": "test@firday.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": "/tmp",
}


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        env=GIT_ENV,
        check=True,
    )
    return result.stdout.strip()


def make_repo(path: Path, *, initial: str = "hello") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "--quiet", "--initial-branch=main")
    (path / "README.md").write_text(initial)
    git(path, "add", "README.md")
    git(path, "commit", "--quiet", "-m", "initial commit")
    return path


@pytest.fixture
def repo(sandbox):
    return make_repo(sandbox / "repo")


@needs_git
def test_git_status_reports_a_clean_repository(policy, repo):
    output = succeeds(call(GitStatusTool(policy=policy), {"path": str(repo)}))
    assert output["repository"] == str(repo)
    assert output["branch"] == "main"
    assert output["clean"] is True
    assert output["changes"] == []
    assert output["commit"]
    assert output["detached"] is False


@needs_git
def test_git_status_reports_staged_unstaged_and_untracked_changes(policy, repo):
    (repo / "README.md").write_text("changed")
    (repo / "staged.txt").write_text("new")
    git(repo, "add", "staged.txt")
    (repo / "untracked.txt").write_text("loose")

    output = succeeds(call(GitStatusTool(policy=policy), {"path": str(repo)}))

    assert output["clean"] is False
    by_path = {c["path"]: c for c in output["changes"]}
    assert by_path["README.md"]["unstaged"] is True
    assert by_path["staged.txt"]["staged"] is True
    assert by_path["untracked.txt"]["untracked"] is True
    assert output["change_count"] == 3


@needs_git
def test_git_status_caps_the_number_of_reported_changes(policy, repo):
    for index in range(5):
        (repo / f"file{index}.txt").write_text("x")

    output = succeeds(call(GitStatusTool(policy=policy), {"path": str(repo), "max_changes": 2}))
    assert len(output["changes"]) == 2
    assert output["change_count"] == 5
    assert output["truncated"] is True


@needs_git
def test_git_status_refuses_a_path_outside_the_sandbox(policy, tmp_path):
    outside = make_repo(tmp_path / "outside-repo")
    result = call(GitStatusTool(policy=policy), {"path": str(outside)})
    assert result.status is ExecutionStatus.ERROR
    assert "not allowed" in result.error


@needs_git
def test_git_status_refuses_a_directory_that_is_not_a_repository(policy, sandbox):
    plain = sandbox / "plain"
    plain.mkdir()
    result = call(GitStatusTool(policy=policy), {"path": str(plain)})
    assert result.status is ExecutionStatus.ERROR
    assert "not a git repository" in result.error


@needs_git
def test_git_branches_lists_local_branches(policy, repo):
    git(repo, "branch", "feature")

    output = succeeds(call(GitBranchesTool(policy=policy), {"path": str(repo)}))

    names = {b["name"] for b in output["branches"]}
    assert names == {"main", "feature"}
    assert output["current_branch"] == "main"
    current = next(b for b in output["branches"] if b["current"])
    assert current["name"] == "main"
    assert current["subject"] == "initial commit"
    assert current["commit"]


@needs_git
def test_git_branches_includes_remote_tracking_branches(policy, sandbox):
    origin = make_repo(sandbox / "origin")
    clone = sandbox / "clone"
    subprocess.run(
        ["git", "clone", "--quiet", str(origin), str(clone)], check=True, env=GIT_ENV
    )

    output = succeeds(call(GitBranchesTool(policy=policy), {"path": str(clone)}))

    remotes = [b for b in output["branches"] if b["remote"]]
    assert any(b["name"].startswith("origin/") for b in remotes)

    local_only = succeeds(
        call(GitBranchesTool(policy=policy), {"path": str(clone), "include_remote": False})
    )
    assert all(not b["remote"] for b in local_only["branches"])


@needs_git
def test_git_clone_creates_a_working_copy(policy, sandbox):
    origin = make_repo(sandbox / "origin")
    destination = sandbox / "cloned"

    output = succeeds(
        call(
            GitCloneTool(policy=policy),
            {"url": str(origin), "destination": str(destination)},
        )
    )

    assert output["destination"] == str(destination)
    assert output["branch"] == "main"
    assert output["commit"] == git(origin, "rev-parse", "HEAD")
    assert (destination / "README.md").read_text() == "hello"
    assert (destination / ".git").is_dir()


@needs_git
def test_git_clone_refuses_an_existing_non_empty_directory(policy, sandbox):
    origin = make_repo(sandbox / "origin")
    destination = sandbox / "occupied"
    destination.mkdir()
    (destination / "keep.txt").write_text("do not overwrite me")

    result = call(
        GitCloneTool(policy=policy), {"url": str(origin), "destination": str(destination)}
    )

    assert result.status is ExecutionStatus.ERROR
    assert "already exists" in result.error
    assert (destination / "keep.txt").read_text() == "do not overwrite me"
    assert sorted(p.name for p in destination.iterdir()) == ["keep.txt"]


@needs_git
def test_git_clone_refuses_an_existing_empty_directory(policy, sandbox):
    """Like fs.copy, clone declines any destination that is already there."""
    origin = make_repo(sandbox / "origin")
    destination = sandbox / "empty"
    destination.mkdir()

    result = call(
        GitCloneTool(policy=policy), {"url": str(origin), "destination": str(destination)}
    )

    assert result.status is ExecutionStatus.ERROR
    assert "already exists" in result.error
    assert list(destination.iterdir()) == []


@needs_git
def test_git_clone_refuses_a_destination_outside_the_sandbox(policy, sandbox, tmp_path):
    origin = make_repo(sandbox / "origin")
    outside = tmp_path / "escaped"

    result = call(
        GitCloneTool(policy=policy), {"url": str(origin), "destination": str(outside)}
    )

    assert result.status is ExecutionStatus.ERROR
    assert "not allowed" in result.error
    assert not outside.exists()


@needs_git
def test_git_clone_refuses_a_missing_parent_unless_asked(policy, sandbox):
    origin = make_repo(sandbox / "origin")
    nested = sandbox / "a" / "b" / "clone"

    refused = call(
        GitCloneTool(policy=policy), {"url": str(origin), "destination": str(nested)}
    )
    assert refused.status is ExecutionStatus.ERROR
    assert "create_parents" in refused.error

    output = succeeds(
        call(
            GitCloneTool(policy=policy),
            {"url": str(origin), "destination": str(nested), "create_parents": True},
        )
    )
    assert output["commit"]
    assert (nested / "README.md").exists()


@needs_git
def test_git_clone_leaves_nothing_behind_when_it_fails(policy, sandbox):
    destination = sandbox / "doomed"
    result = call(
        GitCloneTool(policy=policy),
        {"url": str(sandbox / "no-such-repo"), "destination": str(destination)},
    )
    assert result.status is ExecutionStatus.ERROR
    assert not destination.exists()


@pytest.mark.parametrize(
    "url",
    [
        "ext::sh -c 'touch /tmp/pwned'",
        "EXT::whoami",
        "--upload-pack=evil",
        "not a url",
        "ftp://example.com/repo.git",
    ],
)
def test_git_clone_refuses_a_dangerous_url(url):
    with pytest.raises(InvalidTargetError):
        validate_clone_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo.git",
        "ssh://git@github.com/owner/repo.git",
        "git@github.com:owner/repo.git",
        "/srv/git/repo.git",
        "file:///srv/git/repo.git",
    ],
)
def test_git_clone_accepts_ordinary_urls(url):
    assert validate_clone_url(url) == url


@pytest.fixture
def clone_behind_origin(sandbox):
    """A clone whose origin has moved on, so a pull would visibly change it."""
    origin = make_repo(sandbox / "origin")
    clone = sandbox / "behind"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True, env=GIT_ENV)

    (origin / "NEW.md").write_text("added upstream")
    git(origin, "add", "NEW.md")
    git(origin, "commit", "--quiet", "-m", "upstream commit")
    return clone


@needs_git
def test_git_pull_refuses(policy, clone_behind_origin):
    result = call(GitPullTool(policy=policy), {"path": str(clone_behind_origin)})

    assert result.status is ExecutionStatus.ERROR
    assert "not yet authorized" in result.error
    assert result.output["authorized"] is False
    assert result.output["domain"] == "git"
    assert result.output["operation"] == "pull"
    assert "PART 7" in result.output["blocked_until"]


@needs_git
def test_git_pull_does_not_move_the_working_tree(policy, clone_behind_origin):
    """The real assertion: origin is a commit ahead, and the clone stays put."""
    before = git(clone_behind_origin, "rev-parse", "HEAD")
    before_files = sorted(p.name for p in clone_behind_origin.iterdir())

    call(GitPullTool(policy=policy), {"path": str(clone_behind_origin)})

    assert git(clone_behind_origin, "rev-parse", "HEAD") == before
    assert sorted(p.name for p in clone_behind_origin.iterdir()) == before_files
    assert not (clone_behind_origin / "NEW.md").exists()


@needs_git
def test_git_pull_never_runs_git(policy, repo, monkeypatch):
    async def explode(*args, **kwargs):
        raise AssertionError("git.pull tried to run a command")

    monkeypatch.setattr("app.tools.system.git.run_command_async", explode)
    result = call(GitPullTool(policy=policy), {"path": str(repo)})
    assert "not yet authorized" in result.error


# ===========================================================================
# Audit logging
# ===========================================================================


def test_a_successful_system_operation_is_audited_with_the_correlation_id(caplog):
    with caplog.at_level(logging.INFO, logger=SYSTEM_AUDIT_LOGGER):
        call(InspectProcessTool(), {"pid": os.getpid()}, request_id="corr-proc")

    [record] = [r for r in caplog.records if r.name == SYSTEM_AUDIT_LOGGER]
    assert record.request_id == "corr-proc"
    assert record.levelno == logging.INFO
    assert "domain=process" in record.message
    assert "op=inspect" in record.message
    assert "decision=allowed" in record.message
    assert "outcome=success" in record.message
    assert f"target={os.getpid()}" in record.message


def test_a_refusal_is_audited_as_not_authorized(caplog, live_child):
    with caplog.at_level(logging.INFO, logger=SYSTEM_AUDIT_LOGGER):
        call(TerminateProcessTool(), {"pid": live_child.pid}, request_id="corr-kill")

    [record] = [r for r in caplog.records if r.name == SYSTEM_AUDIT_LOGGER]
    assert record.request_id == "corr-kill"
    assert record.levelno == logging.WARNING
    assert "decision=denied" in record.message
    assert "outcome=not_authorized" in record.message


@needs_git
def test_a_sandbox_denial_is_audited_as_a_denial(policy, tmp_path, caplog):
    outside = make_repo(tmp_path / "outside-repo")
    with caplog.at_level(logging.INFO, logger=SYSTEM_AUDIT_LOGGER):
        call(GitStatusTool(policy=policy), {"path": str(outside)}, request_id="corr-deny")

    [record] = [r for r in caplog.records if r.name == SYSTEM_AUDIT_LOGGER]
    assert record.levelno == logging.WARNING
    assert "decision=denied" in record.message
    assert "outcome=denied" in record.message


def test_every_attempt_produces_exactly_one_audit_record(caplog):
    with caplog.at_level(logging.INFO, logger=SYSTEM_AUDIT_LOGGER):
        call(InspectProcessTool(), {"pid": os.getpid()})
        call(InspectProcessTool(), {"pid": _unused_pid()})
        call(DnsTool(), {"host": "localhost"})

    records = [r for r in caplog.records if r.name == SYSTEM_AUDIT_LOGGER]
    assert [r.message.split()[3] for r in records] == ["op=inspect", "op=inspect", "op=dns"]


# ===========================================================================
# Registration
# ===========================================================================


def test_all_twenty_two_system_tools_are_registered():
    registry = build_default_registry()
    assert len(ENABLED_TOOL_NAMES) + len(DISABLED_TOOL_NAMES) == 22
    for name in ENABLED_TOOL_NAMES + DISABLED_TOOL_NAMES:
        assert name in registry, name


def test_enabled_system_tools_are_real_tools_with_schemas():
    registry = build_default_registry()
    for name in ENABLED_TOOL_NAMES:
        tool = registry.get(name)
        assert isinstance(tool, Tool), name
        assert tool.description and "DISABLED" not in tool.description, name
        assert tool.output_schema()["properties"], name
        assert any(s.startswith("system.") for s in tool.permissions.scopes), name


def test_read_only_system_tools_declare_a_read_side_effect():
    registry = build_default_registry()
    read_only = [n for n in ENABLED_TOOL_NAMES if n != "git.clone"]
    for name in read_only:
        assert registry.get(name).permissions.side_effect is SideEffect.READ, name
        assert registry.get(name).permissions.requires_confirmation is False, name


def test_git_clone_is_the_one_enabled_tool_that_writes():
    tool = build_default_registry().get("git.clone")
    assert tool.permissions.side_effect is SideEffect.WRITE
    assert tool.permissions.filesystem_access is True
    assert tool.permissions.network_access is True


def test_disabled_system_tools_are_still_real_registered_tools():
    registry = build_default_registry()
    for name in DISABLED_TOOL_NAMES:
        tool = registry.get(name)
        assert isinstance(tool, Tool), name
        assert "DISABLED" in tool.description, name
        assert "PART 7" in tool.description, name
        assert tool.input_schema()["properties"], name
        assert tool.output_schema()["properties"]["authorized"], name
        assert tool.permissions.side_effect is SideEffect.WRITE, name
        assert tool.permissions.requires_confirmation is True, name
        assert "system.write" in tool.permissions.scopes, name


def test_every_disabled_system_tool_refuses_whatever_it_is_given():
    registry = build_default_registry()
    for name in DISABLED_TOOL_NAMES:
        result = run(registry.get(name).execute({}, context_for(name)))
        assert result.status is ExecutionStatus.ERROR, name
        assert "not yet authorized" in result.error, name
        assert result.output["authorized"] is False, name
        assert result.output["blocked_until"] == "PART 7 - Security/Permission Engine", name


def test_the_part_6_deny_reason_matches_the_part_4_milestone():
    from app.tools.filesystem.destructive import BLOCKED_UNTIL as FS_BLOCKED_UNTIL

    from app.tools.system.base import BLOCKED_UNTIL as SYSTEM_BLOCKED_UNTIL

    assert SYSTEM_BLOCKED_UNTIL == FS_BLOCKED_UNTIL


def test_tools_endpoint_exposes_the_system_tools():
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/tools").json()
    by_name = {t["name"]: t for t in body}

    for name in ENABLED_TOOL_NAMES + DISABLED_TOOL_NAMES:
        assert name in by_name, name

    assert by_name["proc.list"]["permissions"]["side_effect"] == "read"
    assert "name_contains" in by_name["proc.list"]["input_schema"]["properties"]

    terminate = by_name["proc.terminate"]
    assert terminate["permissions"]["requires_confirmation"] is True
    assert "DISABLED" in terminate["description"]
    assert "authorized" in terminate["output_schema"]["properties"]
    assert "pid" in terminate["input_schema"]["properties"]


def test_the_local_device_reports_disabled_system_tools_as_unavailable():
    from app.devices.local import build_local_device

    device = build_local_device(build_default_registry())
    unavailable = {c.name for c in device.capabilities if not c.available}
    assert set(DISABLED_TOOL_NAMES) <= unavailable
    for name in ENABLED_TOOL_NAMES:
        assert name not in unavailable, name
