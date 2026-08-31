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

## Configuration

Config is loaded from environment variables (see `.env.example`):

| Variable   | Default       | Description                       |
|------------|---------------|------------------------------------|
| `APP_ENV`  | `development` | Environment name, echoed in `/health` |
| `LOG_LEVEL`| `INFO`        | Log level for structured logging   |
| `PORT`     | `8000`        | Host port mapped in Docker Compose |

Never commit a real `.env` file — it's git-ignored. Copy `.env.example` to
`.env` and edit locally.
