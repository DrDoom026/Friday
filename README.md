# FIRDAY

Minimal FastAPI backend.

- **PART 0 + 0.5** — a runnable, containerized skeleton with a health check,
  structured logging, centralized error handling, and env-based config.
- **PART 1** — FIRDAY Core: an orchestrator that runs a request through a
  planner abstraction and returns a response. The only planner is a mock that
  returns a canned plan; no LLM, no tools, nothing executes yet.

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

| Method | Path       | Description                                        |
|--------|------------|----------------------------------------------------|
| `GET`  | `/health`  | Liveness check.                                     |
| `POST` | `/request` | Runs input through Core and returns the mock plan. |

`POST /request` generates a correlation ID per request, threads it through the
lifecycle logs, and returns it in the body and the `X-Request-ID` header. Send
your own `X-Request-ID` header to reuse an existing trace ID.

```bash
curl -X POST http://127.0.0.1:8000/request \
  -H 'Content-Type: application/json' \
  -d '{"input": "summarise my inbox"}'
```

Each planned step comes back in `results` with status `not_executed` — real
tool execution lands in a later part.

## Configuration

Config is loaded from environment variables (see `.env.example`):

| Variable   | Default       | Description                       |
|------------|---------------|------------------------------------|
| `APP_ENV`  | `development` | Environment name, echoed in `/health` |
| `LOG_LEVEL`| `INFO`        | Log level for structured logging   |
| `PORT`     | `8000`        | Host port mapped in Docker Compose |

Never commit a real `.env` file — it's git-ignored. Copy `.env.example` to
`.env` and edit locally.
