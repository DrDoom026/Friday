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

#: PART 9 local inference backend (Ollama). Role: intent classification and a
#: best-effort assist only - never FIRDAY's main reasoning model.
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:0.5b"

#: PART 9 cloud routing (OmniRoute). A dumb OpenAI-compatible pipe in front of
#: Groq -> Gemini (AI Studio) -> Cerebras; provider priority is OmniRoute's own
#: configuration, not FIRDAY's. FIRDAY only ever calls /v1/chat/completions.
DEFAULT_OMNIROUTE_BASE_URL = "http://localhost:3333"
DEFAULT_OMNIROUTE_MODEL = "auto"


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
    # PART 9: hybrid LLM layer.
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    omniroute_base_url: str = DEFAULT_OMNIROUTE_BASE_URL
    omniroute_model: str = DEFAULT_OMNIROUTE_MODEL
    omniroute_api_key: str | None = None
    llm_request_timeout_seconds: float = 20.0
    llm_max_retries: int = 2
    llm_max_context_chars: int = 4000
    llm_memory_top_k: int = 3
    # PART 10: API-layer authentication, separate from device trust (Tailscale)
    # and tool authorization (Security Engine). Empty by default so existing
    # deployments/tests are unaffected until an operator opts in.
    api_keys: tuple[str, ...] = ()
    # PART 13: Gmail OAuth (official Gmail REST API + OAuth2 refresh token).
    # None by default - the adapter/tools must start cleanly without these and
    # only fail, clearly, when a Gmail operation actually runs.
    gmail_client_id: str | None = None
    gmail_client_secret: str | None = None
    gmail_refresh_token: str | None = None
    gmail_request_timeout_seconds: float = 10.0

    @property
    def fs_all_roots(self) -> tuple[str, ...]:
        """The effective allowed roots: the workspace root(s) plus the vault.

        Single source of truth for "everywhere the filesystem sandbox may
        operate" - both startup (:func:`app.fs.bootstrap.ensure_sandbox_ready`)
        and the lazy default-policy fallback (:func:`app.fs.policy.get_default_policy`)
        must build from this, or the two can silently disagree about whether
        the vault is in bounds.
        """
        return self.fs_allowed_roots + (self.fs_vault_root,)


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
        ollama_base_url=os.getenv("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL,
        ollama_model=os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
        omniroute_base_url=os.getenv("OMNIROUTE_BASE_URL") or DEFAULT_OMNIROUTE_BASE_URL,
        omniroute_model=os.getenv("OMNIROUTE_MODEL") or DEFAULT_OMNIROUTE_MODEL,
        omniroute_api_key=os.getenv("OMNIROUTE_API_KEY") or None,
        llm_request_timeout_seconds=_float_env("LLM_REQUEST_TIMEOUT_SECONDS", 20.0),
        llm_max_retries=_int_env("LLM_MAX_RETRIES", 2),
        llm_max_context_chars=_int_env("LLM_MAX_CONTEXT_CHARS", 4000),
        llm_memory_top_k=_int_env("LLM_MEMORY_TOP_K", 3),
        api_keys=tuple(k.strip() for k in (os.getenv("FIRDAY_API_KEYS") or "").split(",") if k.strip()),
        gmail_client_id=os.getenv("GMAIL_CLIENT_ID") or None,
        gmail_client_secret=os.getenv("GMAIL_CLIENT_SECRET") or None,
        gmail_refresh_token=os.getenv("GMAIL_REFRESH_TOKEN") or None,
        gmail_request_timeout_seconds=_float_env("GMAIL_REQUEST_TIMEOUT_SECONDS", 10.0),
    )


settings = get_settings()
