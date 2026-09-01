"""Errors raised by the system tools and the machinery underneath them.

All of them derive from ``ToolError``, so the Part 2 framework's existing
handling applies, and from :class:`SystemToolError`, so a tool can catch the
whole family in one clause and audit it.
"""

from app.core.errors import ToolError


class SystemToolError(ToolError):
    """A system operation could not be completed."""


class CommandNotAvailableError(SystemToolError):
    """A required external binary is not on this host.

    Not a crash and not a bug: FIRDAY runs in a slim container as readily as on
    a full Pi, and a missing binary is a fact about the environment that the
    caller deserves to be told plainly.
    """

    def __init__(self, binary: str, hint: str = "") -> None:
        self.binary = binary
        message = f"required command {binary!r} is not available on this host"
        super().__init__(f"{message} ({hint})" if hint else message)


class CommandFailedError(SystemToolError):
    """An external command ran and exited non-zero."""

    def __init__(self, argv: "tuple[str, ...] | list[str]", returncode: int, stderr: str) -> None:
        self.argv = tuple(argv)
        self.returncode = returncode
        self.stderr = stderr
        detail = (stderr or "").strip().splitlines()
        summary = detail[0][:300] if detail else "no error output"
        super().__init__(f"{self.argv[0]!r} exited {returncode}: {summary}")


class CommandTimedOutError(SystemToolError):
    """An external command outlived its timeout and was killed."""

    def __init__(self, argv: "tuple[str, ...] | list[str]", timeout: float) -> None:
        self.argv = tuple(argv)
        self.timeout = timeout
        super().__init__(f"{self.argv[0]!r} did not finish within {timeout:g}s and was killed")


class InvalidTargetError(SystemToolError):
    """A caller-supplied value was refused before it could reach a command.

    Every system tool builds an argument vector, never a shell string, so there
    is no quoting to get wrong - but a value that looks like an option would
    still be read as one, so those are rejected here.
    """

    def __init__(self, field: str, value: str, reason: str) -> None:
        self.field = field
        self.value = value
        super().__init__(f"{field} {value!r} is not acceptable: {reason}")


class DockerUnavailableError(SystemToolError):
    """The Docker Engine could not be reached."""

    def __init__(self, socket_path: str, reason: str) -> None:
        self.socket_path = socket_path
        super().__init__(f"Docker is not reachable on {socket_path!r}: {reason}")


class DockerApiError(SystemToolError):
    """The Docker Engine answered, and the answer was an error."""

    def __init__(self, path: str, status: int, message: str) -> None:
        self.path = path
        self.status = status
        super().__init__(f"Docker API {path} returned {status}: {message}")


class ProcessNotFoundError(SystemToolError):
    """No process with the given PID exists."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        super().__init__(f"no process with pid {pid}")


class SystemOperationNotAuthorizedError(SystemToolError):
    """A state-changing system operation was refused: nothing can authorize it yet."""

    def __init__(self, operation: str, reason: str) -> None:
        self.operation = operation
        self.reason = reason
        super().__init__(f"operation {operation!r} is not yet authorized: {reason}")
