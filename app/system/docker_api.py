"""The Docker Engine API, over its local unix socket.

No Docker SDK and no ``docker`` CLI. The engine speaks plain HTTP over
``/var/run/docker.sock``, and ``http.client`` speaks plain HTTP over any socket
it is handed, so the whole client is a connection class that dials AF_UNIX
instead of AF_INET. That keeps the dependency list unchanged and keeps the
container image free of a CLI binary whose libc has to match the host's.

Only the read endpoints are exposed here. There is deliberately no ``post`` -
start, stop and restart are refused by their tools, and nothing in this module
would let them through even if a tool tried.
"""

import http.client
import json
import logging
import socket
import urllib.parse
from typing import Any

from app.system.errors import DockerApiError, DockerUnavailableError

logger = logging.getLogger("firday.system.docker")

DEFAULT_SOCKET_PATH = "/var/run/docker.sock"
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Docker multiplexes a non-TTY container's stdout and stderr into one stream,
#: framed by an 8-byte header: [stream type, 0, 0, 0, big-endian length].
STREAM_HEADER_SIZE = 8
STREAM_NAMES = {0: "stdin", 1: "stdout", 2: "stderr"}


class _UnixSocketConnection(http.client.HTTPConnection):
    """An ``HTTPConnection`` that dials a unix socket path."""

    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


def demultiplex(raw: bytes) -> str:
    """Turn a Docker log stream into text, unframing it when it is multiplexed.

    A TTY container's logs are already plain bytes; a non-TTY container's are
    framed. The frame header is recognised rather than assumed, so this is safe
    to apply to either.
    """
    if not raw:
        return ""

    chunks: list[str] = []
    offset = 0
    while offset + STREAM_HEADER_SIZE <= len(raw):
        header = raw[offset : offset + STREAM_HEADER_SIZE]
        if header[0] not in STREAM_NAMES or header[1:4] != b"\x00\x00\x00":
            # Not a frame header - this stream is unframed from here on.
            break
        length = int.from_bytes(header[4:8], "big")
        start = offset + STREAM_HEADER_SIZE
        end = start + length
        if end > len(raw):
            break
        chunks.append(raw[start:end].decode("utf-8", "replace"))
        offset = end

    if not chunks:
        return raw.decode("utf-8", "replace")
    if offset < len(raw):
        chunks.append(raw[offset:].decode("utf-8", "replace"))
    return "".join(chunks)


class DockerClient:
    """A read-only client for the local Docker Engine."""

    def __init__(
        self, socket_path: str | None = None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.socket_path = socket_path or DEFAULT_SOCKET_PATH
        self.timeout = timeout

    def is_available(self) -> bool:
        """True when the socket exists and something is listening on it."""
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(self.timeout)
            probe.connect(self.socket_path)
            probe.close()
        except OSError:
            return False
        return True

    # --- requests ----------------------------------------------------------

    def get_bytes(self, path: str, params: "dict[str, Any] | None" = None) -> bytes:
        """GET an engine endpoint and return its raw body."""
        query = urllib.parse.urlencode(
            {k: _query_value(v) for k, v in (params or {}).items() if v is not None}
        )
        url = f"{path}?{query}" if query else path

        connection = _UnixSocketConnection(self.socket_path, self.timeout)
        try:
            connection.request("GET", url, headers={"Host": "docker", "Accept": "*/*"})
            response = connection.getresponse()
            body = response.read()
            status = response.status
        except (OSError, http.client.HTTPException) as exc:
            raise DockerUnavailableError(self.socket_path, str(exc)) from exc
        finally:
            connection.close()

        if status >= 400:
            raise DockerApiError(path, status, _error_message(body))
        return body

    def get_json(self, path: str, params: "dict[str, Any] | None" = None) -> Any:
        """GET an engine endpoint and parse its JSON body."""
        body = self.get_bytes(path, params)
        try:
            return json.loads(body or b"null")
        except json.JSONDecodeError as exc:
            raise DockerApiError(path, 200, f"response was not JSON: {exc}") from exc

    # --- endpoints ---------------------------------------------------------

    def version(self) -> dict:
        payload = self.get_json("/version")
        return payload if isinstance(payload, dict) else {}

    def containers(self, *, all_containers: bool = False, limit: int | None = None) -> list[dict]:
        payload = self.get_json("/containers/json", {"all": all_containers, "limit": limit})
        return [c for c in (payload or []) if isinstance(c, dict)]

    def inspect_container(self, reference: str) -> dict:
        payload = self.get_json(f"/containers/{urllib.parse.quote(reference, safe='')}/json")
        if not isinstance(payload, dict):
            raise DockerApiError(f"/containers/{reference}/json", 200, "unexpected response shape")
        return payload

    def container_logs(
        self,
        reference: str,
        *,
        tail: int = 100,
        timestamps: bool = False,
        stdout: bool = True,
        stderr: bool = True,
    ) -> bytes:
        return self.get_bytes(
            f"/containers/{urllib.parse.quote(reference, safe='')}/logs",
            {
                "stdout": stdout,
                "stderr": stderr,
                "tail": tail,
                "timestamps": timestamps,
                "follow": False,
            },
        )

    def images(self, *, all_images: bool = False) -> list[dict]:
        payload = self.get_json("/images/json", {"all": all_images})
        return [i for i in (payload or []) if isinstance(i, dict)]


def _query_value(value: Any) -> str:
    """Docker's query parameters are strings; booleans are ``true``/``false``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _error_message(body: bytes) -> str:
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return (body or b"").decode("utf-8", "replace").strip()[:300] or "no detail"
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])[:300]
    return str(payload)[:300]


_default_client: DockerClient | None = None


def get_default_client() -> DockerClient:
    """The process-wide client built from settings, created on first use."""
    global _default_client
    if _default_client is None:
        from app.config import settings

        _default_client = DockerClient(
            settings.docker_socket, timeout=settings.system_command_timeout_seconds
        )
    return _default_client


def set_default_client(client: DockerClient | None) -> None:
    """Replace the process-wide client. Intended for tests and startup wiring."""
    global _default_client
    _default_client = client
