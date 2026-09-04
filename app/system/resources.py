"""Host resource readers for the PART 11 dashboard.

Same philosophy as :mod:`app.system.procfs`: read straight out of ``/proc``,
no new dependency (``psutil`` is not installed and is not worth adding for
three numbers). Every reader is best-effort and never raises - a dashboard
tile that can't read a proc file shows "unavailable", it does not break the
rest of the page.
"""

import os
import shutil
import time
from pathlib import Path

from app.system.procfs import PROC_ROOT, boot_time


def uptime_seconds() -> float | None:
    boot = boot_time()
    if boot <= 0:
        return None
    return max(0.0, time.time() - boot)


def cpu_percent() -> float | None:
    """1-minute load average as a percentage of available cores.

    A load-average proxy, not a sampled instantaneous reading (that needs two
    ``/proc/stat`` reads a tick apart). Good enough for a dashboard gauge.
    """
    try:
        load1, _, _ = os.getloadavg()
    except OSError:
        return None
    cores = os.cpu_count() or 1
    return round(min(100.0, (load1 / cores) * 100.0), 1)


def memory_percent() -> float | None:
    try:
        raw = (PROC_ROOT / "meminfo").read_text()
    except OSError:
        return None
    values: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        if key not in ("MemTotal", "MemAvailable"):
            continue
        digits = parts[1].strip().split()[0]
        if digits.isdigit():
            values[key] = int(digits)
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total:
        return None
    used = max(0, total - (available or 0))
    return round((used / total) * 100.0, 1)


def vault_percent(vault_root: str) -> float | None:
    """Disk usage percentage of the filesystem backing the Obsidian vault."""
    try:
        usage = shutil.disk_usage(Path(vault_root).expanduser())
    except OSError:
        return None
    if usage.total == 0:
        return None
    return round((usage.used / usage.total) * 100.0, 1)
