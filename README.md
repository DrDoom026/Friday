# FIRDAY

Minimal FastAPI backend.

- **PART 0 + 0.5** — a runnable, containerized skeleton with a health check,
  structured logging, centralized error handling, and env-based config.
- **PART 1** — FIRDAY Core: an orchestrator that runs a request through a
  planner abstraction and returns a response. The only planner is a mock that
  returns a canned plan; no LLM, nothing executes yet.
- **PART 2** — Tool framework: the common interface every capability will
  implement (name, description, version, input/output schemas, permission
  metadata, validation, execution, structured results), plus a registry and one
  harmless demo tool (`echo`). Permission metadata is declared but not
  enforced — that is a later part.
- **PART 4** — Filesystem: ten registered `fs.*` tools behind a sandbox
  (allowed roots, traversal and symlink-escape defence, protected system paths,
  size limits) with an audit record for every attempt. `fs.delete`, `fs.move`
  and `fs.rename` are registered but disabled until Part 7.

  PART 3 (a generic shell tool) is intentionally skipped by design.

## Requirements

- Python 3.12+ (or Docker)
- Docker + Docker Compose (for containerized run)

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env

uvicorn app.main:app --reload
```

Check it:

```bash
curl http://127.0.0.1:8000/health
```

## Run tests

```bash
source .venv/bin/activate
pytest -v
```

## Run via Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Check it:

```bash
curl http://127.0.0.1:8000/health
```

## Endpoints

| Method | Path       | Description                                             |
|--------|------------|---------------------------------------------------------|
| `GET`  | `/health`  | Liveness check.                                          |
| `GET`  | `/tools`   | Lists registered tools with schemas and permissions.     |
| `POST` | `/request` | Runs input through Core and returns the mock plan.      |

`POST /request` generates a correlation ID per request, threads it through the
lifecycle logs, and returns it in the body and the `X-Request-ID` header. Send
your own `X-Request-ID` header to reuse an existing trace ID.

```bash
curl -X POST http://127.0.0.1:8000/request \
  -H 'Content-Type: application/json' \
  -d '{"input": "summarise my inbox"}'
```

Core resolves every planned step against the tool registry — the tool must
exist and the arguments must validate against its input schema — then stops at
status `not_executed`. A step naming an unknown tool, or carrying arguments the
schema rejects, comes back as `error`. Actually running the tool lands in a
later part.

## Adding a tool

Subclass `BaseTool`, declare the metadata and schemas, implement `run`, and
decorate with `@register_tool`. See `app/tools/echo.py` for the reference
implementation.

```python
@register_tool
class EchoTool(BaseTool):
    name = "echo"
    description = "Returns the message it was given. Has no side effects."
    version = "1.0.0"
    permissions = ToolPermissions(side_effect=SideEffect.NONE)
    input_model = EchoInput
    output_model = EchoOutput

    async def run(self, payload: EchoInput, context: ToolExecutionContext) -> EchoOutput:
        return EchoOutput(message=payload.message, length=len(payload.message))
```

Import it from `app/tools/__init__.py` and `build_default_registry()` will
discover it. `BaseTool.execute` handles validation, timing, and wrapping the
outcome in a `ToolResult` — subclasses only implement `run`.

## Filesystem tools (PART 4)

Every filesystem operation is a registered tool. Seven of them execute:

| Tool        | Does                                              |
|-------------|---------------------------------------------------|
| `fs.list`   | List a directory's contents                        |
| `fs.stat`   | Metadata for one path                              |
| `fs.search` | Find entries by name pattern under a root          |
| `fs.read`   | Read a text file                                   |
| `fs.write`  | Create, overwrite or append to a file              |
| `fs.mkdir`  | Create a directory                                 |
| `fs.copy`   | Copy a file or tree (never overwrites)             |

Three are registered, schema-complete, and **disabled** — every call returns a
`not yet authorized` error result without touching the disk:

| Tool         | Blocked until                          |
|--------------|-----------------------------------------|
| `fs.delete`  | PART 7 — Security/Permission Engine      |
| `fs.move`    | PART 7 — Security/Permission Engine      |
| `fs.rename`  | PART 7 — Security/Permission Engine      |

Their authorization check (`DestructiveFilesystemTool.is_authorized`) is a stub
that always denies. It is the single place Part 7 will replace.

### The sandbox

`app/fs/policy.py` resolves every path before any tool touches it:

1. The path must be absolute, NUL-free and of sane length.
2. A `..` component is refused outright, not normalized away.
3. The path is canonicalized, collapsing `.`, `..` and every symlink.
4. The canonical path must sit inside a configured allowed root.
5. The canonical path must not sit inside a protected system location
   (`/etc`, `/usr`, `/proc`, `/sys`, `/root`, `/var`, …), nor pass through a
   credential directory (`.ssh`, `.gnupg`, `.aws`, …) — this holds even when a
   root was misconfigured to contain one.

Reads, writes, appends and copies are additionally capped by byte limits.

### Audit trail

Every attempt — allowed or denied — writes exactly one record to the
`firday.fs.audit` logger, carrying the operation, the raw and canonical paths,
the decision, the outcome and the request's correlation ID from Part 1:

```json
{"timestamp": "...", "level": "WARNING", "logger": "firday.fs.audit",
 "message": "fs audit op=read tool=fs.read decision=denied outcome=denied path=/etc/passwd resolved=- invocation_id=811d3a1a... detail=path '/etc/passwd' is not allowed: '/etc' is a protected system path",
 "request_id": "live-fs-demo"}
```

## Configuration

Config is loaded from environment variables (see `.env.example`):

| Variable   | Default       | Description                       |
|------------|---------------|------------------------------------|
| `APP_ENV`  | `development` | Environment name, echoed in `/health` |
| `LOG_LEVEL`| `INFO`        | Log level for structured logging   |
| `PORT`     | `8000`        | Host port mapped in Docker Compose |
| `FS_ALLOWED_ROOTS` | `~/firday/workspace` | Colon-separated absolute roots the `fs.*` tools may touch |
| `FS_MAX_READ_BYTES` | `5242880` | Largest file `fs.read` will load |
| `FS_MAX_WRITE_BYTES` | `5242880` | Largest payload `fs.write` will write |
| `FS_MAX_COPY_BYTES` | `52428800` | Largest file or tree `fs.copy` will duplicate |
| `FS_MAX_LIST_ENTRIES` | `1000` | Cap on entries returned by `fs.list` |
| `FS_MAX_SEARCH_RESULTS` | `500` | Cap on matches returned by `fs.search` |
| `FS_MAX_SEARCH_DEPTH` | `12` | How deep `fs.search` will recurse |

Never commit a real `.env` file — it's git-ignored. Copy `.env.example` to
`.env` and edit locally.
