"""PART 9 LLM provider clients.

Two roles, kept deliberately separate:

- :class:`OllamaClient` - local inference (Ollama). Intent classification
  only. Best-effort: if it is unreachable, callers fall back to "unknown"
  rather than failing the request.
- :class:`OmniRouteClient` - a dumb OpenAI-compatible routing pipe in front
  of Groq -> Gemini -> Cerebras. FIRDAY only ever calls
  ``/v1/chat/completions``; provider priority is OmniRoute's own
  configuration, not something this client implements or needs to know
  about. No MCP, A2A, or router-managed tool execution is used.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.llm.errors import LLMProviderError

logger = logging.getLogger("firday.llm.providers")

# Treated as immediately fatal (no retry): a rate limit will not clear inside
# a single request's retry budget, so retrying just adds uncontrolled load.
_NO_RETRY_STATUS = {429}


class OllamaClient:
    """Local model client. Short prompts, classification only."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._transport = transport

    async def classify_intent(self, text: str, categories: tuple[str, ...]) -> str:
        """Best-effort single-word classification. Returns "unknown" on any failure."""
        prompt = (
            "Classify the request into exactly one category: "
            f"{', '.join(categories)}. Reply with only the category word.\n\n"
            f"Request: {text[:500]}"
        )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json={"model": self._model, "prompt": prompt, "stream": False},
                )
                response.raise_for_status()
                raw = str(response.json().get("response", "")).strip().lower()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("ollama classification unavailable (%s)", type(exc).__name__)
            return "unknown"

        for category in categories:
            if category in raw:
                return category
        return "unknown"


class OmniRouteClient:
    """Cloud routing client. OpenAI-compatible ``/v1/chat/completions`` only."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None,
        *,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._transport = transport

    async def complete(self, messages: list[dict[str, str]]) -> str:
        """Return the assistant's reply text, or raise :class:`LLMProviderError`."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        last_error: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    response = await client.post(
                        f"{self._base_url}/v1/chat/completions",
                        headers=headers,
                        json={"model": self._model, "messages": messages},
                    )
                    if response.status_code in _NO_RETRY_STATUS:
                        logger.warning(
                            "omniroute rate-limited (status=%d) - failing without retry",
                            response.status_code,
                        )
                        raise LLMProviderError("rate limit exhausted")
                    response.raise_for_status()
                    return self._extract_content(response.json())
                except LLMProviderError:
                    raise
                except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                    last_error = exc
                    logger.warning(
                        "omniroute call failed (attempt=%d/%d, error=%s)",
                        attempt + 1,
                        self._max_retries + 1,
                        type(exc).__name__,
                    )
                    if attempt < self._max_retries:
                        await asyncio.sleep(min(2**attempt, 8))

        raise LLMProviderError(
            f"omniroute unavailable after {self._max_retries + 1} attempt(s): "
            f"{type(last_error).__name__ if last_error else 'unknown error'}"
        )

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str:
        return str(payload["choices"][0]["message"]["content"])
