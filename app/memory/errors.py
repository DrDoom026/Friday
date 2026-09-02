"""Errors raised by the memory service."""

from app.memory.models import MemoryCategory


class MemoryError(Exception):
    """Base class for every memory service failure."""


class MemoryNotFoundError(MemoryError):
    """No note exists under this category/id."""

    def __init__(self, category: MemoryCategory, note_id: str) -> None:
        self.category = category
        self.note_id = note_id
        super().__init__(f"no {category.value} memory found for id {note_id!r}")


class MemoryStorageError(MemoryError):
    """The vault refused a write - a policy denial, a full disk, and so on."""
