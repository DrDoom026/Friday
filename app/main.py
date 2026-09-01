import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.context import RequestContext
from app.core.models import FirdayRequest, FirdayResponse
from app.core.orchestrator import Core
from app.core.planner import MockPlanner
from app.core.registry import build_default_registry
from app.core.tools import ToolDescriptor
from app.devices.errors import DeviceNotFoundError
from app.devices.models import Device, DeviceRegistration, DeviceStatus, TrustState
from app.devices.selection import DeviceQuery
from app.devices.service import DeviceService
from app.fs.bootstrap import SandboxConfigurationError, ensure_sandbox_ready
from app.logging_config import configure_logging

configure_logging(settings.log_level)
logger = logging.getLogger("firday")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate the filesystem sandbox before serving a single request.

    The sandbox is otherwise built lazily, so a missing or misconfigured root
    would only surface as an opaque failure on the first ``fs.*`` call. Boot
    here instead: create a missing root, or refuse to start with a readable
    explanation of what is wrong and how to fix it.
    """
    try:
        ensure_sandbox_ready(settings.fs_allowed_roots)
    except SandboxConfigurationError as exc:
        logger.critical("startup aborted - %s", exc)
        raise

    # The machine FIRDAY runs on is a device like any other, and the only one
    # with a real transport in Part 5. Registering it here means /devices is
    # answerable from boot and the Tailscale trust anchor is exercised at
    # startup rather than on first use.
    devices.register_local_device(core.registry)
    yield


app = FastAPI(title="FIRDAY", lifespan=lifespan)

core = Core(planner=MockPlanner(), registry=build_default_registry())
devices = DeviceService.create()


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


@app.post("/devices", response_model=Device, status_code=201)
async def register_device(
    payload: DeviceRegistration,
    request: Request,
    response: Response,
    x_request_id: str | None = Header(default=None),
) -> Device:
    """Register a device. Its trust state comes from Tailscale, not the payload."""
    context = RequestContext.create(request_id=x_request_id, source="http")
    peer = request.client.host if request.client else None
    device = devices.register(payload, headers=request.headers, peer_address=peer)
    context.logger().info(
        "device registered via API (device=%s, name=%s, trust=%s, peer=%s)",
        device.device_id,
        device.name,
        device.trust.value,
        peer or "-",
    )
    response.headers["X-Request-ID"] = context.request_id
    return device


@app.get("/devices", response_model=list[Device])
async def list_devices(
    capability: list[str] | None = Query(default=None),
    trust: TrustState | None = Query(default=None),
    status: DeviceStatus | None = Query(default=None),
) -> list[Device]:
    """List devices, best-first, optionally filtered by capability/trust/status."""
    query = DeviceQuery(
        capabilities=tuple(capability or ()),
        trust=(trust,) if trust else (),
        status=(status,) if status else (),
    )
    return list(devices.registry.select(query).devices)


@app.get("/devices/{device_id}", response_model=Device)
async def get_device(device_id: str) -> Device:
    try:
        return devices.registry.get(device_id)
    except DeviceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/devices/{device_id}/heartbeat", response_model=Device)
async def device_heartbeat(device_id: str) -> Device:
    """Record that a device is alive: refreshes ``last_seen`` and status."""
    try:
        return devices.registry.mark_seen(device_id)
    except DeviceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
