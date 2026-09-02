"""PART 9 privacy / sensitivity gate.

Deterministic, regex-based. Never a paid/cloud LLM call - this must run
before any cloud request is built, and again on every tool result before it
is folded into a cloud request. Erring toward over-detection is the correct
failure mode here; erring toward under-detection is not.
"""

import re

_PATTERNS: tuple[re.Pattern, ...] = (
    # Private filesystem paths.
    re.compile(r"/home/[\w.\-]+"),
    re.compile(r"/root(?:/|$)"),
    re.compile(r"~[/\w.\-]*"),
    re.compile(r"\.ssh[/\\][\w.\-]*"),
    re.compile(r"\bid_rsa\b|\bid_ed25519\b"),
    re.compile(r"\.env\b"),
    # Credential / token key=value or key: value pairs.
    re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|"
        r"bearer|private[_-]?key)\b\s*[:=]\s*\S+"
    ),
    # Common secret token shapes.
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),  # OpenAI/Anthropic-style secret key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub token
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),  # JWT
    # PEM private key blocks.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def is_sensitive(text: str) -> bool:
    """True if ``text`` contains anything that must not reach a cloud provider."""
    return any(pattern.search(text) for pattern in _PATTERNS)


def redact(text: str) -> str:
    """Replace every sensitive span with a fixed marker.

    Used as the output privacy filter: a tool result must pass through this
    before it is included in any cloud LLM request.
    """
    redacted = text
    for pattern in _PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
