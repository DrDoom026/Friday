"""PART 13 reference adapter: Gmail, via the official Gmail REST API + OAuth2."""

from app.comm.gmail.adapter import GmailAdapter
from app.comm.gmail.client import GmailClient
from app.comm.gmail.errors import GmailAPIError, GmailConfigurationError, GmailError

__all__ = [
    "GmailAdapter",
    "GmailClient",
    "GmailError",
    "GmailConfigurationError",
    "GmailAPIError",
]
