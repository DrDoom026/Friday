"""Running an external command, without a shell and without surprises.

Part 3 - a generic shell tool - was skipped by design, and this is not a way
back to it. Nothing here accepts a command string: every caller passes a fixed
argument vector whose executable is a constant in FIRDAY's own source, and the
only caller-supplied parts are individual arguments. ``shell=False`` is not a
default that can be overridden, so there is no quoting or metacharacter
handling to get wrong.

What is left is option injection - an argument like ``--upload-pack=...`` being
read as a flag rather than a value - so :func:`reject_option_like` is applied
to every caller-supplied argument before it reaches an argv.
"""

import asyncio
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass

from app.system.errors import (
    CommandFailedError,
    CommandNotAvailableError,
    CommandTimedOutError,
    InvalidTargetError,
)

logger = logging.getLogger("firday.system.command")

DEFAULT_TIMEOUT_SECONDS = 10.0

#: Caller-supplied values longer than this are refused outright.
MAX_ARGUMENT_LENGTH = 2048


@dataclass(frozen=True)
class CommandResult:
    """What one external command did."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def require_ok(self) -> "CommandResult":
        """Return self, or raise :class:`CommandFailedError` on a non-zero exit."""
        if not self.ok:
            raise CommandFailedError(self.argv, self.returncode, self.stderr)
        return self


def is_available(binary: str) -> bool:
    """True when ``binary`` can be found on PATH."""
    return shutil.which(binary) is not None


def require_available(binary: str, hint: str = "") -> str:
    """Resolve ``binary`` on PATH or raise :class:`CommandNotAvailableError`."""
    resolved = shutil.which(binary)
    if resolved is None:
        raise CommandNotAvailableError(binary, hint)
    return resolved


def reject_option_like(field: str, value: str) -> str:
    """Refuse a caller-supplied argv value that could be mistaken for an option.

    Returns the value unchanged when it is acceptable.
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidTargetError(field, str(value), "must be a non-empty string")
    if len(value) > MAX_ARGUMENT_LENGTH:
        raise InvalidTargetError(field, value[:64] + "...", "is too long")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise InvalidTargetError(field, value, "contains a control character")
    if value.startswith("-"):
        raise InvalidTargetError(field, value, "may not start with '-'; that reads as an option")
    return value


def run_command(
    argv: "list[str] | tuple[str, ...]",
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
    env: "dict[str, str] | None" = None,
    check: bool = False,
) -> CommandResult:
    """Run ``argv`` and capture its output.

    Raises :class:`CommandNotAvailableError` when the executable is missing and
    :class:`CommandTimedOutError` when it outlives ``timeout``. A non-zero exit
    is returned rather than raised unless ``check`` is set - for several of
    these commands (``ping`` on an unreachable host, ``systemctl show`` on an
    unknown unit) a non-zero exit is the answer, not a failure.
    """
    argv = tuple(str(part) for part in argv)
    if not argv:
        raise ValueError("run_command needs at least an executable")
    require_available(argv[0])

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("command timed out (argv=%s, timeout=%s)", " ".join(argv), timeout)
        raise CommandTimedOutError(argv, timeout) from exc
    except OSError as exc:
        raise CommandNotAvailableError(argv[0], str(exc)) from exc

    result = CommandResult(
        argv=argv,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        duration_ms=(time.perf_counter() - started) * 1000,
    )
    logger.debug("command finished (argv=%s, rc=%s)", " ".join(argv), result.returncode)
    return result.require_ok() if check else result


async def run_command_async(
    argv: "list[str] | tuple[str, ...]", **kwargs
) -> CommandResult:
    """:func:`run_command` on a worker thread, so a tool never blocks the loop."""
    return await asyncio.to_thread(run_command, argv, **kwargs)
