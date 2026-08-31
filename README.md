# FIRDAY

Minimal FastAPI backend. This is PART 0 + PART 0.5: a runnable, containerized
skeleton with a health check, structured logging, centralized error handling,
and env-based config. Nothing else is in scope yet.

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

## Configuration

Config is loaded from environment variables (see `.env.example`):

| Variable   | Default       | Description                       |
|------------|---------------|------------------------------------|
| `APP_ENV`  | `development` | Environment name, echoed in `/health` |
| `LOG_LEVEL`| `INFO`        | Log level for structured logging   |
| `PORT`     | `8000`        | Host port mapped in Docker Compose |

Never commit a real `.env` file — it's git-ignored. Copy `.env.example` to
`.env` and edit locally.
