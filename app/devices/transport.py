"""How FIRDAY would reach a device.

Interface only, plus the one implementation that is real today: the Pi FIRDAY
runs on is reachable as ``local``. SSH, Tailscale-remote and agent transports
are declared so the shape exists and a device can name one, but every one of
them refuses.

Note what a transport does *not* have: a method that runs a command. Remote
execution is explicitly out of scope for PART 5, so the interface stops at
"can I reach this device?". The execution surface arrives with the permission
engine that would gate it, in PART 7.
"""

import logging
import platform
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.devices.errors import TransportNotImplementedError
from app.devices.models import Device, TransportKind

logger = logging.getLogger("firday.devices.transport")

#: The milestone that will supply gated remote execution.
BLOCKED_UNTIL = "PART 7 - Security/Permission Engine"


@dataclass(frozen=True)
class TransportProbe:
    """The result of asking a transport whether a device is reachable."""

    kind: TransportKind
    reachable: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind.value, "reachable": self.reachable, "detail": self.detail}


class Transport(ABC):
    """A way to reach a device. Reachability only - nothing executes."""

    kind: TransportKind
    implemented: bool = False

    @abstractmethod
    async def probe(self, device: Device) -> TransportProbe:
        """Report whether ``device`` is reachable over this transport."""

    def describe(self) -> dict:
        return {"kind": self.kind.value, "implemented": self.implemented}


class LocalTransport(Transport):
    """The machine FIRDAY is running on. The only real transport in PART 5."""

    kind = TransportKind.LOCAL
    implemented = True

    async def probe(self, device: Device) -> TransportProbe:
        return TransportProbe(
            kind=self.kind,
            reachable=True,
            detail=f"local process on {socket.gethostname()} ({platform.machine()})",
        )


class _UnimplementedTransport(Transport):
    """Declared, addressable, and refuses. Mirrors the disabled fs.* tools."""

    implemented = False

    async def probe(self, device: Device) -> TransportProbe:
        raise TransportNotImplementedError(self.kind.value, BLOCKED_UNTIL)


class SshTransport(_UnimplementedTransport):
    """Reach a device over SSH. Not implemented - PART 5 stubs it deliberately."""

    kind = TransportKind.SSH


class TailscaleTransport(_UnimplementedTransport):
    """Reach a device over Tailscale SSH / a tailnet address. Not implemented."""

    kind = TransportKind.TAILSCALE


class AgentTransport(_UnimplementedTransport):
    """Reach a FIRDAY agent running on the device. Not implemented."""

    kind = TransportKind.AGENT


class TransportRegistry:
    """Maps a :class:`TransportKind` to the transport that handles it."""

    def __init__(self, transports: "list[Transport] | None" = None) -> None:
        chosen = transports if transports is not None else [
            LocalTransport(),
            SshTransport(),
            TailscaleTransport(),
            AgentTransport(),
        ]
        self._transports: dict[TransportKind, Transport] = {t.kind: t for t in chosen}

    def get(self, kind: TransportKind) -> Transport:
        try:
            return self._transports[kind]
        except KeyError:
            raise TransportNotImplementedError(str(kind), BLOCKED_UNTIL) from None

    def for_device(self, device: Device) -> Transport:
        return self.get(device.transport)

    def implemented_kinds(self) -> tuple[TransportKind, ...]:
        return tuple(k for k, t in sorted(self._transports.items()) if t.implemented)

    def describe(self) -> list[dict]:
        return [self._transports[k].describe() for k in sorted(self._transports)]

    def __contains__(self, kind: object) -> bool:
        return kind in self._transports

    def __len__(self) -> int:
        return len(self._transports)
