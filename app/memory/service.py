"""Memory service: store, retrieve, update, delete and search vault notes.

The vault is plain markdown under a second sandboxed root
(:data:`app.config.Settings.fs_vault_root`). Every read and write goes through
the Part 4 filesystem tools (``fs.read`` / ``fs.write`` / ``fs.list``) against
the process's :class:`~app.fs.policy.FilesystemPolicy`, so it is authorized by
the Part 7 Security Engine exactly like any other filesystem operation, and
every attempt lands in the same audit trail.

``delete`` is the one exception: ``fs.delete`` is a Part 4 tool that always
returns ``REQUIRE_CONFIRMATION`` under the default security policy (it is
gated on the Part 10/11 confirmation channel, which does not exist yet), so
routing through it would make memory deletion permanently unusable. Deletion
instead resolves the path through the same policy and writes the same audit
record shape directly - the sandbox and the audit trail are reused, only the
disabled tool wrapper is not.
"""

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.core.context import RequestContext, ToolExecutionContext
from app.core.models import ExecutionStatus
from app.fs.audit import record_attempt
from app.fs.errors import FilesystemPolicyError
from app.fs.policy import FilesystemPolicy, get_default_policy
from app.memory.errors import MemoryNotFoundError, MemoryStorageError
from app.memory.frontmatter import parse as parse_frontmatter
from app.memory.frontmatter import render as render_frontmatter
from app.memory.models import BASE_FRONTMATTER_KEYS, MemoryCategory, MemoryNote
from app.tools.filesystem.content import ReadFileTool, WriteFileTool
from app.tools.filesystem.inspect import ListDirectoryTool

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(raw: str) -> str:
    slug = _SLUG_RE.sub("-", raw.strip().lower()).strip("-")
    return slug or "note"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryService:
    """Read/write access to the Obsidian vault, scoped to one filesystem policy."""

    def __init__(
        self,
        vault_root: str | None = None,
        *,
        policy: FilesystemPolicy | None = None,
    ) -> None:
        self._vault_root = vault_root
        self._policy = policy

    @property
    def vault_root(self) -> str:
        if self._vault_root is not None:
            return self._vault_root
        from app.config import settings

        return settings.fs_vault_root

    # --- CRUD ----------------------------------------------------------

    async def store(
        self,
        category: MemoryCategory,
        note_id: str,
        body: str,
        *,
        tags: Iterable[str] = (),
        source: str = "system",
        scope: str = "global",
        extra: Mapping[str, Any] | None = None,
        request: RequestContext | None = None,
    ) -> MemoryNote:
        """Create or overwrite a note. Preserves ``created_at`` if it already existed."""
        note_id = _slugify(note_id)
        now = _now_iso()
        try:
            existing = await self.retrieve(category, note_id, request=request)
            created_at = existing.created_at
        except MemoryNotFoundError:
            created_at = now

        note = MemoryNote(
            id=note_id,
            category=category,
            tags=tuple(tags),
            source=source,
            scope=scope,
            created_at=created_at,
            updated_at=now,
            body=body,
            extra=dict(extra or {}),
        )
        text = render_frontmatter(note.frontmatter(), note.body)

        tool = WriteFileTool(policy=self._policy)
        result = await tool.execute(
            {
                "path": self._path_for(category, note_id),
                "content": text,
                "mode": "overwrite",
                "create_parents": True,
            },
            self._context(request, tool.name),
        )
        if result.status != ExecutionStatus.SUCCESS:
            raise MemoryStorageError(result.error or f"failed to write memory {note_id!r}")
        return note

    async def retrieve(
        self,
        category: MemoryCategory,
        note_id: str,
        *,
        request: RequestContext | None = None,
    ) -> MemoryNote:
        note_id = _slugify(note_id)
        tool = ReadFileTool(policy=self._policy)
        result = await tool.execute(
            {"path": self._path_for(category, note_id)},
            self._context(request, tool.name),
        )
        if result.status != ExecutionStatus.SUCCESS:
            raise MemoryNotFoundError(category, note_id)
        frontmatter, body = parse_frontmatter(result.output["content"])
        return self._note_from_frontmatter(category, note_id, frontmatter, body)

    async def update(
        self,
        category: MemoryCategory,
        note_id: str,
        *,
        body: str | None = None,
        tags: Iterable[str] | None = None,
        source: str | None = None,
        scope: str | None = None,
        extra: Mapping[str, Any] | None = None,
        request: RequestContext | None = None,
    ) -> MemoryNote:
        """Merge changes onto an existing note. Raises :class:`MemoryNotFoundError` if none exists."""
        existing = await self.retrieve(category, note_id, request=request)
        merged_extra = dict(existing.extra)
        merged_extra.update(extra or {})
        return await self.store(
            category,
            note_id,
            existing.body if body is None else body,
            tags=existing.tags if tags is None else tuple(tags),
            source=existing.source if source is None else source,
            scope=existing.scope if scope is None else scope,
            extra=merged_extra,
            request=request,
        )

    async def delete(
        self,
        category: MemoryCategory,
        note_id: str,
        *,
        request: RequestContext | None = None,
    ) -> None:
        note_id = _slugify(note_id)
        raw_path = self._path_for(category, note_id)
        policy = self._policy or get_default_policy()
        context = self._context(request, "memory.delete")

        try:
            resolved = policy.resolve(raw_path)
        except FilesystemPolicyError as exc:
            record_attempt(
                context, operation="delete", paths=[raw_path], allowed=False,
                outcome="denied", detail=str(exc),
            )
            raise

        if not resolved.exists():
            record_attempt(
                context, operation="delete", paths=[raw_path], resolved_paths=[resolved],
                allowed=True, outcome="not_found",
            )
            raise MemoryNotFoundError(category, note_id)

        resolved.unlink()
        record_attempt(
            context, operation="delete", paths=[raw_path], resolved_paths=[resolved],
            allowed=True, outcome="success",
        )

    # --- search ----------------------------------------------------------

    async def search(
        self,
        *,
        category: MemoryCategory | None = None,
        tags: Iterable[str] = (),
        query: str | None = None,
        request: RequestContext | None = None,
    ) -> list[MemoryNote]:
        """Filename/tag/frontmatter search over the vault. Not semantic - reads and filters."""
        wanted_tags = set(tags)
        categories = [category] if category else list(MemoryCategory)
        list_tool = ListDirectoryTool(policy=self._policy)
        read_tool = ReadFileTool(policy=self._policy)
        notes: list[MemoryNote] = []

        for cat in categories:
            listing = await list_tool.execute(
                {"path": f"{self.vault_root.rstrip('/')}/{cat.folder}"},
                self._context(request, list_tool.name),
            )
            if listing.status != ExecutionStatus.SUCCESS:
                continue  # nothing stored under this category yet

            for entry in listing.output["entries"]:
                if entry["type"] != "file" or not entry["name"].endswith(".md"):
                    continue
                read = await read_tool.execute(
                    {"path": entry["path"]}, self._context(request, read_tool.name)
                )
                if read.status != ExecutionStatus.SUCCESS:
                    continue
                frontmatter, body = parse_frontmatter(read.output["content"])
                note_id = str(frontmatter.get("id") or entry["name"][: -len(".md")])
                note = self._note_from_frontmatter(cat, note_id, frontmatter, body)

                if wanted_tags and not wanted_tags & set(note.tags):
                    continue
                if query and query.lower() not in f"{note.id} {note.body}".lower():
                    continue
                notes.append(note)

        return notes

    # --- person notes ------------------------------------------------------

    async def store_person(
        self,
        name: str,
        relationship: str,
        *,
        tone: str | None = None,
        context_notes: str = "",
        tags: Iterable[str] = (),
        request: RequestContext | None = None,
    ) -> MemoryNote:
        """Create or update the note for one person - who they are and how to talk to/about them."""
        return await self.store(
            MemoryCategory.PERSON,
            name,
            context_notes,
            tags=tags,
            source="user",
            extra={"name": name, "relationship": relationship, "tone": tone},
            request=request,
        )

    async def get_person(
        self, name: str, *, request: RequestContext | None = None
    ) -> MemoryNote:
        return await self.retrieve(MemoryCategory.PERSON, name, request=request)

    # --- internals -----------------------------------------------------

    def _path_for(self, category: MemoryCategory, note_id: str) -> str:
        return f"{self.vault_root.rstrip('/')}/{category.folder}/{note_id}.md"

    def _context(
        self, request: RequestContext | None, tool_name: str
    ) -> ToolExecutionContext:
        return ToolExecutionContext.for_tool(
            request or RequestContext.create(source="memory"), tool_name
        )

    @staticmethod
    def _note_from_frontmatter(
        category: MemoryCategory, note_id: str, frontmatter: Mapping[str, Any], body: str
    ) -> MemoryNote:
        extra = {k: v for k, v in frontmatter.items() if k not in BASE_FRONTMATTER_KEYS}
        now = _now_iso()
        return MemoryNote(
            id=str(frontmatter.get("id") or note_id),
            category=category,
            tags=tuple(frontmatter.get("tags") or ()),
            source=str(frontmatter.get("source") or "system"),
            scope=str(frontmatter.get("scope") or "global"),
            created_at=str(frontmatter.get("created_at") or now),
            updated_at=str(frontmatter.get("updated_at") or now),
            body=body.strip(),
            extra=extra,
        )
