"""Errors raised by the PART 9 LLM layer."""


class LLMError(Exception):
    """Base class for every LLM-layer failure."""


class LLMProviderError(LLMError):
    """A provider call failed after the configured retries were exhausted.

    Never includes secrets - only status codes / exception class names.
    """


class LLMMalformedResponseError(LLMError):
    """The LLM's response could not be parsed as a structured tool request."""
