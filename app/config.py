import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

#: Where filesystem tools may operate when nothing is configured. A single,
#: explicit sandbox under the user's home - never the whole home directory.
DEFAULT_FS_ALLOWED_ROOTS = "~/firday/workspace"


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
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
    fs_max_read_bytes: int = 5 * 1024 * 1024
    fs_max_write_bytes: int = 5 * 1024 * 1024
    fs_max_copy_bytes: int = 50 * 1024 * 1024
    fs_max_list_entries: int = 1000
    fs_max_search_results: int = 500
    fs_max_search_depth: int = 12


def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        port=_int_env("PORT", 8000),
        fs_allowed_roots=_roots_env("FS_ALLOWED_ROOTS", DEFAULT_FS_ALLOWED_ROOTS),
        fs_max_read_bytes=_int_env("FS_MAX_READ_BYTES", 5 * 1024 * 1024),
        fs_max_write_bytes=_int_env("FS_MAX_WRITE_BYTES", 5 * 1024 * 1024),
        fs_max_copy_bytes=_int_env("FS_MAX_COPY_BYTES", 50 * 1024 * 1024),
        fs_max_list_entries=_int_env("FS_MAX_LIST_ENTRIES", 1000),
        fs_max_search_results=_int_env("FS_MAX_SEARCH_RESULTS", 500),
        fs_max_search_depth=_int_env("FS_MAX_SEARCH_DEPTH", 12),
    )


settings = get_settings()
