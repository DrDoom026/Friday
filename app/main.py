import json
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, Query, Request, Response, WebSocket
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketDisconnect

import asyncio

from app.api_auth import require_api_key
from app.automation.errors import TaskNotFoundError, TaskValidationError
from app.automation.models import (
    AutomationTask,
    AutomationTaskCreate,
    AutomationTaskUpdate,
    ExecutionRecord,
)
from app.automation.runner import TaskRunner
from app.automation.service import AutomationService
from app.automation.store import AutomationStore
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
from app.devices.local import LOCAL_DEVICE_ID
from app.devices.models import Device, DeviceRegistration, DeviceStatus, TrustState
from app.devices.selection import DeviceQuery
from app.devices.service import DeviceService
from app.fs.bootstrap import SandboxConfigurationError, ensure_sandbox_ready
from app.logging_config import configure_logging
from app.memory.service import MemoryService
from app.system import resources
from app.voice.faster_whisper_stt import FasterWhisperSTT
from app.voice.manager import VoiceSessionManager
from app.voice.piper_tts import PiperTTS
from app.voice.pipeline import append_audio_chunk, finish_audio, start_audio
from app.voice.protocol import handle_message as handle_voice_message

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

    # PART 15: background poll loop for scheduled/recurring/cron tasks.
    # Deliberately the smallest possible worker - one asyncio task woken on
    # a fixed interval, not a framework. Manual runs (POST .../run) do not
    # depend on this loop at all.
    poll_task = asyncio.create_task(_automation_poll_loop())
    try:
        yield
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass


async def _automation_poll_loop() -> None:
    while True:
        try:
            await automation_runner.run_due_tasks()
        except Exception:  # noqa: BLE001 - a bad tick must never kill the loop
            logger.exception("automation poll tick failed")
        await asyncio.sleep(settings.automation_poll_interval_seconds)


app = FastAPI(title="FIRDAY", lifespan=lifespan)

_registry = build_default_registry()
core = Core(planner=_build_planner(_registry), registry=_registry)
devices = DeviceService.create()
voice_sessions = VoiceSessionManager()
# PART 12b: constructing this loads no model yet - FasterWhisperSTT only
# loads faster-whisper's model lazily, on the first utterance transcribed.
voice_stt = FasterWhisperSTT(
    model_size=settings.stt_model,
    device=settings.stt_device,
    compute_type=settings.stt_compute_type,
    language=settings.stt_language,
    timeout_seconds=settings.stt_timeout_seconds,
)
# PART 12c: likewise, no Piper model is loaded until the first response is
# synthesized. An unconfigured TTS_MODEL_PATH fails cleanly per-utterance
# (structured TTS_FAILED), it does not stop the app from starting.
voice_tts = PiperTTS(
    model_path=settings.tts_model_path,
    config_path=settings.tts_config_path,
    sample_rate=settings.tts_sample_rate,
    timeout_seconds=settings.tts_timeout_seconds,
    max_input_chars=settings.tts_max_input_chars,
)
automation_service = AutomationService(AutomationStore(settings.automation_store_path))
automation_runner = TaskRunner(core, automation_service, devices=devices.registry)

# PART 11: the web dashboard is a static, self-contained view layer. It is
# built from source (frontend/, a Vite + React + Three.js project) into this
# directory; FastAPI only ever serves the resulting static files off disk -
# no template engine, no Node server in production. `html=True` serves
# `index.html` for the directory root.
_DASHBOARD_DIR = Path(__file__).parent / "static" / "dashboard"
app.mount("/dashboard", StaticFiles(directory=_DASHBOARD_DIR, html=True), name="dashboard")


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
    "/automation/tasks",
    response_model=AutomationTask,
    status_code=201,
    dependencies=[Depends(require_api_key)],
)
async def create_automation_task(payload: AutomationTaskCreate) -> AutomationTask:
    """Create a task. Validation (including secret rejection) lives in the service."""
    try:
        return automation_service.create(payload)
    except TaskValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/automation/tasks",
    response_model=list[AutomationTask],
    dependencies=[Depends(require_api_key)],
)
async def list_automation_tasks() -> list[AutomationTask]:
    return automation_service.list()


@app.get(
    "/automation/tasks/{task_id}",
    response_model=AutomationTask,
    dependencies=[Depends(require_api_key)],
)
async def get_automation_task(task_id: str) -> AutomationTask:
    try:
        return automation_service.get(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put(
    "/automation/tasks/{task_id}",
    response_model=AutomationTask,
    dependencies=[Depends(require_api_key)],
)
async def update_automation_task(task_id: str, payload: AutomationTaskUpdate) -> AutomationTask:
    try:
        return automation_service.update(task_id, payload)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete(
    "/automation/tasks/{task_id}",
    status_code=204,
    dependencies=[Depends(require_api_key)],
)
async def delete_automation_task(task_id: str) -> Response:
    try:
        automation_service.delete(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@app.post(
    "/automation/tasks/{task_id}/run",
    response_model=ExecutionRecord,
    dependencies=[Depends(require_api_key)],
)
async def run_automation_task(task_id: str) -> ExecutionRecord:
    """Manual execution. Goes through the exact same Security Engine path as
    a scheduled firing - this is not a confirmation bypass or a shortcut."""
    try:
        return await automation_runner.run_now(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    "/docker/{operation}",
    response_model=ToolResult,
    dependencies=[Depends(require_api_key)],
)
async def run_docker_operation(
    operation: str,
    arguments: dict = Body(default_factory=dict),
    x_request_id: str | None = Header(default=None),
) -> ToolResult:
    """Run one Docker operation (containers/inspect/logs/images/start/stop/restart).

    PART 11: the dashboard's containers panel needs the same "call one named
    tool by HTTP" shape PART 10 already gave ``/files/{operation}``. Restricting
    the tool name to the ``docker.`` namespace means this endpoint can only
    ever reach Docker tools - never any other tool family. Goes through
    ``Core.execute_tool``, so the Security Engine authorizes it exactly as it
    would a planned step; ``docker.restart`` is a permanently-denied tool
    (PART 6), so this endpoint reports that denial, it does not lift it.
    """
    context = RequestContext.create(request_id=x_request_id, source="http")
    tool_name = f"docker.{operation}"
    if core.registry.try_get(tool_name) is None:
        raise HTTPException(status_code=404, detail=f"no docker operation {operation!r}")
    return await core.execute_tool(tool_name, arguments, context)


@app.get("/system/status", dependencies=[Depends(require_api_key)])
async def system_status() -> dict:
    """PART 11: the dashboard's system panel.

    Everything here is a non-secret gauge or boolean - no provider keys,
    tokens, or OAuth material ever leaves this endpoint. ``cpu_percent`` is a
    load-average proxy (see ``app.system.resources``), not a sampled reading.
    """
    local = devices.registry.try_get(LOCAL_DEVICE_ID)
    return {
        "uptime_seconds": resources.uptime_seconds(),
        "cpu_percent": resources.cpu_percent(),
        "memory_percent": resources.memory_percent(),
        "vault_percent": resources.vault_percent(MemoryService().vault_root),
        "tailscale_connected": bool(local and local.trust == TrustState.TRUSTED),
        "planner_mode": "llm" if hasattr(core.planner, "finalize") else "mock",
        "omniroute_configured": bool(settings.omniroute_api_key),
        "gmail_configured": bool(
            settings.gmail_client_id and settings.gmail_client_secret and settings.gmail_refresh_token
        ),
    }


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


async def _voice_send_error(websocket: WebSocket, code: str, message: str) -> None:
    await websocket.send_json({"type": "error", "code": code, "message": message})


async def _voice_handshake(websocket: WebSocket):
    """Accept the connection and perform the ``session.start`` handshake.

    Trust is Part 5's, not re-implemented here: the device must already be a
    known, ``TRUSTED`` device (see ``app.devices.trust``). Tailscale
    reachability alone is not enough - an unregistered or untrusted
    ``device_id`` never gets a session, no matter how it reached this socket.
    Returns the created session, or ``None`` after rejecting and closing.
    """
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
    except WebSocketDisconnect:
        return None

    try:
        handshake = json.loads(raw)
    except ValueError:
        await _voice_send_error(websocket, "MALFORMED_JSON", "handshake must be valid JSON")
        await websocket.close(code=4400)
        return None

    if not isinstance(handshake, dict) or handshake.get("type") != "session.start":
        await _voice_send_error(websocket, "PROTOCOL_ERROR", "expected a session.start message")
        await websocket.close(code=4400)
        return None

    device_id = handshake.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        await _voice_send_error(websocket, "INVALID_HANDSHAKE", "device_id is required")
        await websocket.close(code=4400)
        return None

    device = devices.registry.try_get(device_id)
    if device is None or not device.is_trusted():
        # Deliberately the same rejection for "unknown" and "known but
        # untrusted" - do not let a caller distinguish the two.
        await _voice_send_error(websocket, "DEVICE_NOT_TRUSTED", "device is not trusted")
        await websocket.close(code=4401)
        return None

    session = voice_sessions.create(device_id)
    await websocket.send_json(
        {
            "type": "session.accepted",
            "session_id": session.session_id,
            "device_id": session.device_id,
            "state": session.state.value,
        }
    )
    return session


@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket) -> None:
    """PART 12a/12b: voice session transport, plus audio -> STT -> Core.

    ``audio.start``/binary frames/``audio.end`` are handled here; a
    completed utterance is transcribed (``app.voice.pipeline``) and handed
    to the same ``Core.handle`` that ``POST /request`` uses - no parallel
    LLM path, no direct tool execution from this socket. The audio buffer
    for the current utterance is a local variable: it never outlives this
    connection and is never written to disk.
    """
    session = await _voice_handshake(websocket)
    if session is None:
        return
    audio_buffer = None
    try:
        while True:
            try:
                frame = await websocket.receive()
            except WebSocketDisconnect:
                return
            if frame["type"] == "websocket.disconnect":
                return

            if frame.get("bytes") is not None:
                if audio_buffer is None:
                    await _voice_send_error(
                        websocket, "AUDIO_NOT_STARTED", "send audio.start before audio data"
                    )
                    continue
                error = append_audio_chunk(audio_buffer, frame["bytes"])
                if error is not None:
                    audio_buffer = None
                    await websocket.send_json(error)
                continue

            raw = frame.get("text")
            if raw is None:
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                await _voice_send_error(websocket, "MALFORMED_JSON", "message must be valid JSON")
                continue

            msg_type = message.get("type") if isinstance(message, dict) else None

            if msg_type == "audio.start":
                audio_buffer, response = start_audio(
                    session,
                    message,
                    expected_sample_rate=settings.stt_sample_rate,
                    max_bytes=settings.stt_max_audio_bytes,
                )
                await websocket.send_json(response)
                continue

            if msg_type == "audio.end":
                buffer, audio_buffer = audio_buffer, None
                async for event in finish_audio(
                    session, buffer, stt=voice_stt, core=core, tts=voice_tts
                ):
                    if event.kind == "json":
                        await websocket.send_json(event.payload)
                    else:
                        await websocket.send_bytes(event.payload)
                continue

            response = handle_voice_message(session, message)
            if response is None:
                await websocket.send_json(
                    {"type": "session.ended", "session_id": session.session_id}
                )
                return
            await websocket.send_json(response)
    finally:
        voice_sessions.remove(session.session_id)
