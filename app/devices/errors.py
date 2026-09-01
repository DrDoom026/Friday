"""Device management error types.

Mirrors :mod:`app.core.errors`: everything raised by the device layer derives
from ``DeviceError`` so a caller can catch the whole family with one clause.
"""


class DeviceError(Exception):
    """Base class for every device management failure."""


class DeviceNotFoundError(DeviceError, KeyError):
    """A device was looked up by an id the registry does not know."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        super().__init__(f"no device registered under id {device_id!r}")

    def __str__(self) -> str:
        # KeyError.__str__ would repr the message; keep it readable.
        return self.args[0]


class DeviceAlreadyRegisteredError(DeviceError):
    """An id collision at registration time."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        super().__init__(f"a device is already registered under id {device_id!r}")


class TransportNotImplementedError(DeviceError, NotImplementedError):
    """A transport exists as an interface but has no implementation yet."""

    def __init__(self, kind: str, blocked_until: str) -> None:
        self.kind = kind
        self.blocked_until = blocked_until
        super().__init__(
            f"the {kind!r} transport is not implemented yet (blocked until {blocked_until})"
        )

    def __str__(self) -> str:
        return self.args[0]


class TailscaleUnavailableError(DeviceError):
    """The Tailscale CLI could not be consulted for an identity lookup."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"tailscale identity lookup unavailable: {reason}")
