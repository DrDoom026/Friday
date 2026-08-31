"""Request-scoped context threaded through the orchestration flow."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

MAX_REQUEST_ID_LENGTH = 128


class _RequestLoggerAdapter(logging.LoggerAdapter):
    """Stamps every record with the correlation ID for this request."""

    def process(self, msg, kwargs):
        extra = dict(kwargs.get("extra") or {})
        extra["request_id"] = self.extra["request_id"]
        kwargs["extra"] = extra
        return msg, kwargs


def new_request_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class RequestContext:
    """Carries request-scoped information through Core, planner and tools."""

    request_id: str
    source: str = "unknown"
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(cls, request_id: str | None = None, source: str = "unknown") -> "RequestContext":
        """Build a context, reusing a caller-supplied correlation ID when usable."""
        candidate = (request_id or "").strip()[:MAX_REQUEST_ID_LENGTH]
        return cls(request_id=candidate or new_request_id(), source=source)

    def elapsed_ms(self) -> float:
        delta = datetime.now(timezone.utc) - self.received_at
        return delta.total_seconds() * 1000

    def logger(self, name: str = "firday") -> logging.LoggerAdapter:
        return _RequestLoggerAdapter(logging.getLogger(name), {"request_id": self.request_id})
