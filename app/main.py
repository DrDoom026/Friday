import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Body, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from app.api_auth import require_api_key
from app.comm.gmail.adapter import GmailAdapter
from app.comm.gmail.client import GmailClient
from app.comm.gmail.errors import GmailAPIError, GmailConfigurationError
from app.config import settings
from app.core.context import RequestContext
from app.core.models import FirdayRequest, FirdayResponse, ToolResult
from app.core.orchestrator import Core
from app.core.planner import MockPlanner, Planner
from app.core.registry import ToolRegistry, build_default_registry
from app.core.tools import ToolDescriptor
from app.devices.errors import DeviceNotFoundError
from app.devices.models import Device, DeviceRegistration, DeviceStatus, TrustState
from app.devices.selection import DeviceQuery
from app.devices.service import DeviceService
from app.fs.bootstrap import SandboxConfigurationError, ensure_sandbox_ready
from app.logging_config import configure_logging
from app.memory.service import MemoryService

configure_logging(settings.log_level)
logger = logging.getLogger("firday")


def _build_planner(registry: ToolRegistry) -> Planner:
    """PART 9: opt in to the real LLM planner with ``FIRDAY_PLANNER=llm``.

    Defaults to the Part 1 mock planner so existing behavior/tests are
    unaffected unless an operator explicitly configures OmniRoute.
    """
    if os.getenv("FIRDAY_PLANNER", "mock").strip().lower() != "llm":
        return MockPlanner()

    from app.llm.planner import LLMPlanner
    from app.llm.providers import OllamaClient, OmniRouteClient

    cloud = OmniRouteClient(
        settings.omniroute_base_url,
        settings.omniroute_model,
        settings.omniroute_api_key,
        timeout_seconds=settings.llm_request_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    local = OllamaClient(settings.ollama_base_url, settings.ollama_model)
    return LLMPlanner(
        cloud,
        registry,
        local=local,
        memory=MemoryService(),
        memory_top_k=settings.llm_memory_top_k,
        max_context_chars=settings.llm_max_context_chars,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate the filesystem sandbox before serving a single request.

    The sandbox is otherwise built lazily, so a missing or misconfigured root
    would only surface as an opaque failure on the first ``fs.*`` call. Boot
    here instead: create a missing root, or refuse to start with a readable
    explanation of what is wrong and how to fix it.
    """
    try:
        ensure_sandbox_ready(settings.fs_all_roots)
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

_registry = build_default_registry()
core = Core(planner=_build_planner(_registry), registry=_registry)
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


@app.post("/request", response_model=FirdayResponse, dependencies=[Depends(require_api_key)])
async def handle_request(
    payload: FirdayRequest,
    response: Response,
    x_request_id: str | None = Header(default=None),
) -> FirdayResponse:
    context = RequestContext.create(request_id=x_request_id, source="http")
    # Only PART 9's LLM planner (which defines finalize) gets real execution;
    # the Part 1 mock planner keeps its NOT_EXECUTED contract unchanged.
    execute = hasattr(core.planner, "finalize")
    result = await core.handle(payload, context, execute=execute)
    response.headers["X-Request-ID"] = context.request_id
    return result


@app.get(
    "/tools", response_model=list[ToolDescriptor], dependencies=[Depends(require_api_key)]
)
async def list_tools() -> list[ToolDescriptor]:
    return core.registry.describe()


@app.post(
    "/files/{operation}",
    response_model=ToolResult,
    dependencies=[Depends(require_api_key)],
)
async def run_file_operation(
    operation: str,
    arguments: dict = Body(default_factory=dict),
    x_request_id: str | None = Header(default=None),
) -> ToolResult:
    """Run one filesystem operation (list/stat/search/read/write/mkdir/copy).

    Delegates straight to Core, which resolves ``fs.{operation}`` against the
    tool registry and runs it through the same Security Engine gate as any
    planned step - the disabled destructive ops (delete/move/rename) still
    come back as a clean "not authorized" ``ToolResult`` rather than a route
    doing anything special. Restricting the tool name to the ``fs.`` namespace
    here means this endpoint can only ever reach filesystem tools, never any
    other tool family.
    """
    context = RequestContext.create(request_id=x_request_id, source="http")
    tool_name = f"fs.{operation}"
    if core.registry.try_get(tool_name) is None:
        raise HTTPException(status_code=404, detail=f"no filesystem operation {operation!r}")
    return await core.execute_tool(tool_name, arguments, context)


@app.post(
    "/comm/gmail/poll",
    response_model=list[FirdayResponse],
    dependencies=[Depends(require_api_key)],
)
async def poll_gmail(limit: int = Query(default=5, ge=1, le=20)) -> list[FirdayResponse]:
    """Pull unread Gmail, run each message through Core, return the responses.

    This is the PART 13 inbound path made concrete: Gmail -> GmailAdapter ->
    a normalized ``FirdayRequest`` -> ``Core.handle`` (planner, Security
    Engine, tools) -> ``FirdayResponse``. No polling loop/scheduler - this is
    an on-demand trigger, same as ``POST /request``; automatic scheduling is
    PART 15's. Nothing here sends a reply: if the planner picks
    ``comm.gmail.send`` it still hits Security Engine's REQUIRE_CONFIRMATION
    gate like any other confirmable tool.
    """
    client = GmailClient(
        settings.gmail_client_id,
        settings.gmail_client_secret,
        settings.gmail_refresh_token,
        timeout_seconds=settings.gmail_request_timeout_seconds,
    )
    adapter = GmailAdapter(client)
    try:
        messages = await adapter.fetch_new(limit=limit)
    except GmailConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GmailAPIError as exc:
        raise HTTPException(status_code=502, detail="Gmail API request failed") from exc

    execute = hasattr(core.planner, "finalize")
    responses = []
    for message in messages:
        context = RequestContext.create(source="gmail")
        request = FirdayRequest(
            input=f"Email from {message.sender}: {message.subject or '(no subject)'}\n\n{message.body}",
            metadata={
                "platform": "gmail",
                "message_id": message.external_id,
                "thread_id": message.thread_id or "",
            },
        )
        responses.append(await core.handle(request, context, execute=execute))
    return responses


@app.post(
    "/devices", response_model=Device, status_code=201, dependencies=[Depends(require_api_key)]
)
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


@app.get("/devices", response_model=list[Device], dependencies=[Depends(require_api_key)])
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


@app.get(
    "/devices/{device_id}", response_model=Device, dependencies=[Depends(require_api_key)]
)
async def get_device(device_id: str) -> Device:
    try:
        return devices.registry.get(device_id)
    except DeviceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/devices/{device_id}/heartbeat",
    response_model=Device,
    dependencies=[Depends(require_api_key)],
)
async def device_heartbeat(device_id: str) -> Device:
    """Record that a device is alive: refreshes ``last_seen`` and status."""
    try:
        return devices.registry.mark_seen(device_id)
    except DeviceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
