"""Reading process state straight out of ``/proc``.

No ``ps``, and no new dependency. ``ps`` is not in the slim image FIRDAY
deploys on, and its output format is a moving target across distributions,
whereas ``/proc`` is the kernel's own interface and is stable. Reading it
directly also keeps the honest limitation visible: inside a container this sees
the container's PID namespace, which is exactly the set of processes FIRDAY
could act on from there.

Every reader here tolerates a process exiting mid-read. A PID that vanishes
between ``listdir`` and ``open`` is normal, not an error, and yields ``None``.
"""

import os
import pwd
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROC_ROOT = Path("/proc")

#: The single-letter state codes in ``/proc/<pid>/stat``, spelled out.
STATE_NAMES = {
    "R": "running",
    "S": "sleeping",
    "D": "disk-sleep",
    "Z": "zombie",
    "T": "stopped",
    "t": "tracing-stop",
    "W": "paging",
    "X": "dead",
    "x": "dead",
    "K": "wakekill",
    "P": "parked",
    "I": "idle",
}


def clock_ticks() -> int:
    return int(os.sysconf("SC_CLK_TCK"))


def page_size() -> int:
    return int(os.sysconf("SC_PAGE_SIZE"))


def boot_time() -> float:
    """Seconds since the epoch at which this kernel booted, from ``/proc/stat``."""
    try:
        for line in (PROC_ROOT / "stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _username(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


@dataclass(frozen=True)
class ProcessInfo:
    """One process, as ``/proc`` describes it."""

    pid: int
    ppid: int
    name: str
    state: str
    state_description: str
    uid: int
    user: str
    command: str
    threads: int
    rss_bytes: int
    vms_bytes: int
    cpu_seconds: float
    nice: int
    started_at: str


@dataclass(frozen=True)
class ProcessDetail:
    """A process, plus the parts only worth paying for on a single lookup."""

    info: ProcessInfo
    executable: str | None = None
    working_directory: str | None = None
    command_arguments: tuple[str, ...] = ()
    open_file_count: int | None = None
    children: tuple[int, ...] = ()
    unreadable: tuple[str, ...] = field(default_factory=tuple)


def iter_pids() -> "list[int]":
    """Every PID currently visible in this namespace, ascending."""
    pids = []
    try:
        entries = os.listdir(PROC_ROOT)
    except OSError:
        return []
    for entry in entries:
        if entry.isdigit():
            pids.append(int(entry))
    return sorted(pids)


def _read_cmdline(pid: int) -> tuple[str, ...]:
    try:
        raw = (PROC_ROOT / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ()
    return tuple(part.decode("utf-8", "replace") for part in raw.split(b"\x00") if part)


def _parse_stat(raw: str) -> "list[str] | None":
    """Split ``/proc/<pid>/stat`` after the comm field.

    ``comm`` is parenthesized and may itself contain spaces and parentheses, so
    the split has to be on the *last* ``)``, not the first.
    """
    close = raw.rfind(")")
    if close == -1:
        return None
    return raw[close + 2 :].split()


def read_process(pid: int, *, boot: float | None = None) -> ProcessInfo | None:
    """Describe one process, or ``None`` if it is gone or unreadable."""
    base = PROC_ROOT / str(pid)
    try:
        raw = (base / "stat").read_text()
        uid = os.stat(base).st_uid
    except (OSError, ValueError):
        return None

    fields = _parse_stat(raw)
    if fields is None or len(fields) < 22:
        return None

    open_paren = raw.find("(")
    close_paren = raw.rfind(")")
    name = raw[open_paren + 1 : close_paren] if 0 <= open_paren < close_paren else ""

    def number(index: int, default: int = 0) -> int:
        try:
            return int(fields[index])
        except (IndexError, ValueError):
            return default

    ticks = clock_ticks() or 100
    start_ticks = number(19)
    boot = boot_time() if boot is None else boot
    arguments = _read_cmdline(pid)

    state = fields[0]
    return ProcessInfo(
        pid=pid,
        ppid=number(1),
        name=name,
        state=state,
        state_description=STATE_NAMES.get(state, "unknown"),
        uid=uid,
        user=_username(uid),
        command=" ".join(arguments) or f"[{name}]",
        threads=number(17),
        rss_bytes=number(21) * page_size(),
        vms_bytes=number(20),
        cpu_seconds=(number(11) + number(12)) / ticks,
        nice=number(16),
        started_at=_iso(boot + start_ticks / ticks) if boot else "",
    )


def read_processes() -> "list[ProcessInfo]":
    """Describe every visible process. Ones that exit mid-scan are dropped."""
    boot = boot_time()
    found = []
    for pid in iter_pids():
        info = read_process(pid, boot=boot)
        if info is not None:
            found.append(info)
    return found


def _readlink(path: Path, unreadable: list[str]) -> str | None:
    try:
        return os.readlink(path)
    except OSError:
        unreadable.append(path.name)
        return None


def read_process_detail(pid: int) -> ProcessDetail | None:
    """Describe one process in full, or ``None`` if it is gone.

    Anything the caller's uid may not see - another user's ``cwd``, ``exe`` or
    file descriptors - is reported as unreadable rather than raising. Being
    unprivileged is a normal condition, not a failure.
    """
    info = read_process(pid)
    if info is None:
        return None

    base = PROC_ROOT / str(pid)
    unreadable: list[str] = []

    open_files: int | None
    try:
        open_files = len(os.listdir(base / "fd"))
    except OSError:
        open_files = None
        unreadable.append("fd")

    children: tuple[int, ...] = ()
    try:
        raw = (base / "task" / str(pid) / "children").read_text()
        children = tuple(int(part) for part in raw.split())
    except (OSError, ValueError):
        pass

    return ProcessDetail(
        info=info,
        executable=_readlink(base / "exe", unreadable),
        working_directory=_readlink(base / "cwd", unreadable),
        command_arguments=_read_cmdline(pid),
        open_file_count=open_files,
        children=children,
        unreadable=tuple(unreadable),
    )
