"""Low-level Gmail REST API + OAuth2 client (PART 13).

Talks to Google's official endpoints over plain HTTPS - no browser
automation, no scraping, no google-api-python-client dependency (FIRDAY
already depends on httpx for the Part 9 LLM providers; this follows the same
pattern). Never logs, returns, or otherwise surfaces the access or refresh
token - only Gmail message data.
"""

import logging
import time
from typing import Any

import httpx

from app.comm.gmail.errors import GmailAPIError, GmailConfigurationError

logger = logging.getLogger("firday.comm.gmail")

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

#: Refresh this many seconds before actual expiry, to absorb request latency.
_EXPIRY_SKEW_SECONDS = 30.0

#: Binding to an IPv4 local address forces httpcore/anyio to resolve and
#: connect over IPv4 only, instead of AF_UNSPEC. Works around Docker
#: containers (e.g. Raspberry Pi deployments) where dual-stack DNS
#: resolution via getaddrinfo(AF_UNSPEC) fails even though IPv4 alone
#: resolves and connects fine. Does not affect TLS SNI/hostname or cert
#: verification, which httpx derives from the URL, not the local address.
def _ipv4_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(local_address="0.0.0.0")


class GmailClient:
    """Thin wrapper over the official Gmail REST API."""

    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        refresh_token: str | None,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._timeout = timeout_seconds
        self._transport = transport if transport is not None else _ipv4_transport()
        self._access_token: str | None = None
        self._access_token_expiry: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret and self._refresh_token)

    def _require_configured(self) -> None:
        if not self.configured:
            raise GmailConfigurationError(
                "Gmail OAuth is not configured - set GMAIL_CLIENT_ID, "
                "GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN"
            )

    async def _access_token_value(self) -> str:
        self._require_configured()
        if self._access_token and time.monotonic() < self._access_token_expiry:
            return self._access_token

        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            try:
                response = await client.post(
                    TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                token = payload["access_token"]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                logger.warning("gmail token refresh failed (%s)", type(exc).__name__)
                raise GmailAPIError("Gmail authentication failed") from exc

        self._access_token = token
        self._access_token_expiry = (
            time.monotonic() + float(payload.get("expires_in", 3600)) - _EXPIRY_SKEW_SECONDS
        )
        return token

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = await self._access_token_value()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            try:
                response = await client.request(
                    method, f"{API_BASE}{path}", headers=headers, **kwargs
                )
                response.raise_for_status()
                return response.json() if response.content else {}
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("gmail api call failed (%s %s): %s", method, path, type(exc).__name__)
                raise GmailAPIError(f"Gmail API request failed: {type(exc).__name__}") from exc

    async def list_messages(self, *, query: str = "", max_results: int = 10) -> list[dict]:
        """Return bare ``{id, threadId}`` references - no content."""
        params: dict[str, Any] = {"maxResults": max_results}
        if query:
            params["q"] = query
        data = await self._request("GET", "/messages", params=params)
        return data.get("messages", [])

    async def get_message(self, message_id: str, *, format: str = "full") -> dict:
        return await self._request("GET", f"/messages/{message_id}", params={"format": format})

    async def send_message(self, raw_base64url: str, *, thread_id: str | None = None) -> dict:
        body: dict[str, Any] = {"raw": raw_base64url}
        if thread_id:
            body["threadId"] = thread_id
        return await self._request("POST", "/messages/send", json=body)
