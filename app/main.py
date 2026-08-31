import logging

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.context import RequestContext
from app.core.models import FirdayRequest, FirdayResponse
from app.core.orchestrator import Core
from app.core.planner import MockPlanner
from app.core.registry import build_default_registry
from app.core.tools import ToolDescriptor
from app.logging_config import configure_logging

configure_logging(settings.log_level)
logger = logging.getLogger("firday")

app = FastAPI(title="FIRDAY")

core = Core(planner=MockPlanner(), registry=build_default_registry())


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    logger.warning("HTTP exception on %s %s: %s", request.method, request.url.path, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": "An unexpected error occurred."},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.app_env}


@app.post("/request", response_model=FirdayResponse)
async def handle_request(
    payload: FirdayRequest,
    response: Response,
    x_request_id: str | None = Header(default=None),
) -> FirdayResponse:
    context = RequestContext.create(request_id=x_request_id, source="http")
    result = await core.handle(payload, context)
    response.headers["X-Request-ID"] = context.request_id
    return result


@app.get("/tools", response_model=list[ToolDescriptor])
async def list_tools() -> list[ToolDescriptor]:
    return core.registry.describe()
