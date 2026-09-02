import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

#: Where filesystem tools may operate when nothing is configured. A single,
#: explicit sandbox under the user's home - never the whole home directory.
DEFAULT_FS_ALLOWED_ROOTS = "~/firday/workspace"

#: Where the PART 8 memory vault lives - a second sandboxed root, separate from
#: the workspace, so FIRDAY's own memory notes are never mixed in with a user's
#: files.
DEFAULT_FS_VAULT_ROOT = "~/firday/vault"

#: Where the Docker Engine listens. Read-only access to this socket is what the
#: PART 6 ``docker.*`` tools need; without it they report Docker as unreachable.
DEFAULT_DOCKER_SOCKET = "/var/run/docker.sock"


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _roots_env(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name) or default
    return tuple(
        os.path.expanduser(part.strip()) for part in raw.split(os.pathsep) if part.strip()
    )


@dataclass(frozen=True)
class Settings:
    app_env: str
    log_level: str
    port: int
    fs_allowed_roots: tuple[str, ...] = field(default_factory=tuple)
    fs_vault_root: str = DEFAULT_FS_VAULT_ROOT
    fs_max_read_bytes: int = 5 * 1024 * 1024
    fs_max_write_bytes: int = 5 * 1024 * 1024
    fs_max_copy_bytes: int = 50 * 1024 * 1024
    fs_max_list_entries: int = 1000
    fs_max_search_results: int = 500
    fs_max_search_depth: int = 12
    # PART 6: high-level software/system tools.
    docker_socket: str = DEFAULT_DOCKER_SOCKET
    system_command_timeout_seconds: float = 10.0
    system_git_timeout_seconds: float = 120.0
    system_max_processes: int = 500
    system_max_log_lines: int = 500
    system_max_ping_count: int = 10


def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        port=_int_env("PORT", 8000),
        fs_allowed_roots=_roots_env("FS_ALLOWED_ROOTS", DEFAULT_FS_ALLOWED_ROOTS),
        fs_vault_root=os.path.expanduser(os.getenv("FS_VAULT_ROOT") or DEFAULT_FS_VAULT_ROOT),
        fs_max_read_bytes=_int_env("FS_MAX_READ_BYTES", 5 * 1024 * 1024),
        fs_max_write_bytes=_int_env("FS_MAX_WRITE_BYTES", 5 * 1024 * 1024),
        fs_max_copy_bytes=_int_env("FS_MAX_COPY_BYTES", 50 * 1024 * 1024),
        fs_max_list_entries=_int_env("FS_MAX_LIST_ENTRIES", 1000),
        fs_max_search_results=_int_env("FS_MAX_SEARCH_RESULTS", 500),
        fs_max_search_depth=_int_env("FS_MAX_SEARCH_DEPTH", 12),
        docker_socket=os.getenv("DOCKER_SOCKET") or DEFAULT_DOCKER_SOCKET,
        system_command_timeout_seconds=_float_env("SYSTEM_COMMAND_TIMEOUT_SECONDS", 10.0),
        system_git_timeout_seconds=_float_env("SYSTEM_GIT_TIMEOUT_SECONDS", 120.0),
        system_max_processes=_int_env("SYSTEM_MAX_PROCESSES", 500),
        system_max_log_lines=_int_env("SYSTEM_MAX_LOG_LINES", 500),
        system_max_ping_count=_int_env("SYSTEM_MAX_PING_COUNT", 10),
    )


settings = get_settings()
