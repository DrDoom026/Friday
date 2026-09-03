"""PART 10: API-layer authentication.

This is a distinct boundary from the others already in FIRDAY:

    network authentication  (Tailscale, outside this process)
        != device trust      (app.devices - who is this machine)
        != API authorization (this module - is this caller allowed to call FIRDAY at all)
        != tool authorization (app.security - is this specific tool call allowed)

A missing/invalid key never reaches Core, the planner, or any tool - it is
rejected before any of that runs. When no keys are configured
(``FIRDAY_API_KEYS`` unset), the API stays open, matching the existing
opt-in pattern for the Part 9 LLM planner so current deployments and tests
are unaffected.
"""

from fastapi import Header, HTTPException

from app.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.api_keys:
        return
    if x_api_key not in settings.api_keys:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
