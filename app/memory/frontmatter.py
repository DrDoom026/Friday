"""Parse and render the YAML-frontmatter + markdown-body note format.

    ---
    id: ...
    category: ...
    ---

    body text

Kept to this one flat shape (no nested structures) so Obsidian renders it as a
plain properties panel and PyYAML's default dumper never has to guess at
formatting.
"""

from typing import Any

import yaml

_DELIMITER = "---"


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Split ``text`` into (frontmatter dict, body). No frontmatter -> ``({}, text)``."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _DELIMITER:
        return {}, text

    for i in range(1, len(lines)):
        if lines[i].strip() == _DELIMITER:
            data = yaml.safe_load("".join(lines[1:i])) or {}
            if not isinstance(data, dict):
                return {}, text
            body = "".join(lines[i + 1 :]).lstrip("\n")
            return data, body

    return {}, text  # unterminated frontmatter block - treat the whole thing as body


def render(frontmatter: dict[str, Any], body: str) -> str:
    """Render frontmatter + body back into the on-disk note format."""
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"{_DELIMITER}\n{fm_text}\n{_DELIMITER}\n\n{body.strip()}\n"
