"""WebSocket transport to the existing ``/ws/voice`` endpoint (PART 12d).

Speaks the exact protocol Parts 12a-12c already define - this module is not
a second protocol, just this process's connection to it. Never a public
listener: ``server_ws_url`` is expected to be a Tailscale address (see
``clients/laptop/config.py``). ``websockets`` is imported lazily, so
importing this module never requires the package installed.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("firday.voice.client.transport")


class TransportClosedError(Exception):
    """The connection failed to open, or closed while in use."""


@dataclass(frozen=True)
class IncomingMessage:
    """One message received: a decoded JSON object, or a raw audio frame."""

    kind: str  # "json" | "bytes"
    payload: Any


class Transport(ABC):
    """What :class:`~clients.laptop.voice_client.VoiceClient` needs from a connection."""

    @abstractmethod
    async def connect(self) -> None:
        """Raises :class:`TransportClosedError` on failure."""

    @abstractmethod
    async def close(self) -> None:
        """Safe to call multiple times, and even if never connected."""

    @abstractmethod
    async def send_json(self, message: dict) -> None: ...

    @abstractmethod
    async def send_bytes(self, data: bytes) -> None: ...

    @abstractmethod
    async def recv(self) -> IncomingMessage:
        """Raises :class:`TransportClosedError` when the connection ends."""


class WebSocketTransport(Transport):
    def __init__(self, url: str) -> None:
        self._url = url
        self._ws = None

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise TransportClosedError("the 'websockets' package is not installed") from exc
        try:
            self._ws = await websockets.connect(self._url)
        except Exception as exc:  # noqa: BLE001 - any failure means "could not connect"
            raise TransportClosedError(f"could not connect to {self._url}") from exc

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def send_json(self, message: dict) -> None:
        await self._send(json.dumps(message))

    async def send_bytes(self, data: bytes) -> None:
        await self._send(data)

    async def _send(self, data) -> None:
        if self._ws is None:
            raise TransportClosedError("not connected")
        try:
            await self._ws.send(data)
        except Exception as exc:  # noqa: BLE001
            raise TransportClosedError("connection closed while sending") from exc

    async def recv(self) -> IncomingMessage:
        if self._ws is None:
            raise TransportClosedError("not connected")
        try:
            raw = await self._ws.recv()
        except Exception as exc:  # noqa: BLE001
            raise TransportClosedError("connection closed while receiving") from exc
        if isinstance(raw, bytes):
            return IncomingMessage("bytes", raw)
        return IncomingMessage("json", json.loads(raw))
