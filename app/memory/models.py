"""Data model for a memory note.

One shape serves every category (preferences, devices, tasks, tool history,
explicit long-term memories, and person notes): fixed frontmatter fields plus
an ``extra`` dict for whatever a category needs on top (a person note's
``name``/``relationship``/``tone``, for instance). A subclass per category
would just be this same dict typed four different ways.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MemoryCategory(str, Enum):
    """What kind of thing a memory note records, and where it lives in the vault."""

    PREFERENCE = "preference"
    DEVICE = "device"
    TASK = "task"
    TOOL_HISTORY = "tool_history"
    MEMORY = "memory"
    PERSON = "person"

    @property
    def folder(self) -> str:
        return _CATEGORY_FOLDERS[self]


_CATEGORY_FOLDERS: dict[MemoryCategory, str] = {
    MemoryCategory.PREFERENCE: "preferences",
    MemoryCategory.DEVICE: "devices",
    MemoryCategory.TASK: "tasks",
    MemoryCategory.TOOL_HISTORY: "tool_history",
    MemoryCategory.MEMORY: "memories",
    MemoryCategory.PERSON: "people",
}

#: Frontmatter keys every note carries, kept out of ``extra``.
BASE_FRONTMATTER_KEYS = frozenset(
    {"id", "category", "tags", "source", "scope", "created_at", "updated_at"}
)


class MemoryNote(BaseModel):
    """One vault note: fixed frontmatter, a markdown body, and category extras."""

    id: str
    category: MemoryCategory
    tags: tuple[str, ...] = ()
    source: str = "system"
    scope: str = "global"
    created_at: str
    updated_at: str
    body: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    def frontmatter(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "category": self.category.value,
            "tags": list(self.tags),
            "source": self.source,
            "scope": self.scope,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        data.update(self.extra)
        return data
