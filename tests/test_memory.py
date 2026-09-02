"""Tests for PART 8: the memory service and its Obsidian-vault note format."""

import asyncio

import pytest

from app.core.context import RequestContext
from app.fs.policy import FilesystemPolicy
from app.memory.conversation import ConversationContext
from app.memory.errors import MemoryNotFoundError
from app.memory.frontmatter import parse as parse_frontmatter
from app.memory.frontmatter import render as render_frontmatter
from app.memory.models import MemoryCategory
from app.memory.service import MemoryService


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def policy(vault):
    return FilesystemPolicy([vault])


@pytest.fixture
def memory(vault, policy):
    return MemoryService(vault_root=str(vault), policy=policy)


# ===========================================================================
# frontmatter format
# ===========================================================================


def test_frontmatter_round_trips_through_render_and_parse():
    fm = {"id": "a", "category": "memory", "tags": ["x", "y"]}
    text = render_frontmatter(fm, "hello world")
    parsed_fm, body = parse_frontmatter(text)
    assert parsed_fm == fm
    assert body.strip() == "hello world"


def test_frontmatter_parse_handles_body_with_no_frontmatter():
    fm, body = parse_frontmatter("just a plain note")
    assert fm == {}
    assert body == "just a plain note"


# ===========================================================================
# store / retrieve / update / delete
# ===========================================================================


def test_store_then_retrieve_round_trips_a_note(memory):
    stored = run(
        memory.store(
            MemoryCategory.PREFERENCE, "Favorite Color", "blue", tags=("ui",), source="user"
        )
    )
    assert stored.id == "favorite-color"

    fetched = run(memory.retrieve(MemoryCategory.PREFERENCE, "Favorite Color"))
    assert fetched.body == "blue"
    assert fetched.tags == ("ui",)
    assert fetched.source == "user"
    assert fetched.created_at == stored.created_at


def test_store_writes_a_human_readable_markdown_file(memory, vault):
    run(memory.store(MemoryCategory.TASK, "water plants", "every Tuesday"))
    path = vault / "tasks" / "water-plants.md"
    assert path.is_file()
    text = path.read_text()
    assert text.startswith("---\n")
    assert "category: task" in text
    assert "every Tuesday" in text


def test_retrieve_missing_note_raises(memory):
    with pytest.raises(MemoryNotFoundError):
        run(memory.retrieve(MemoryCategory.TASK, "nope"))


def test_store_overwrite_preserves_created_at(memory):
    first = run(memory.store(MemoryCategory.MEMORY, "note-1", "v1"))
    second = run(memory.store(MemoryCategory.MEMORY, "note-1", "v2"))
    assert second.created_at == first.created_at
    assert second.body == "v2"


def test_update_merges_onto_an_existing_note(memory):
    run(memory.store(MemoryCategory.DEVICE, "pi", "the Pi", tags=("home",)))
    updated = run(memory.update(MemoryCategory.DEVICE, "pi", tags=("home", "server")))
    assert updated.body == "the Pi"
    assert set(updated.tags) == {"home", "server"}


def test_update_missing_note_raises(memory):
    with pytest.raises(MemoryNotFoundError):
        run(memory.update(MemoryCategory.DEVICE, "nope", body="x"))


def test_delete_removes_the_note(memory, vault):
    run(memory.store(MemoryCategory.MEMORY, "temp", "delete me"))
    run(memory.delete(MemoryCategory.MEMORY, "temp"))
    assert not (vault / "memories" / "temp.md").exists()
    with pytest.raises(MemoryNotFoundError):
        run(memory.retrieve(MemoryCategory.MEMORY, "temp"))


def test_delete_missing_note_raises(memory):
    with pytest.raises(MemoryNotFoundError):
        run(memory.delete(MemoryCategory.MEMORY, "nope"))


# ===========================================================================
# search
# ===========================================================================


def test_search_filters_by_category(memory):
    run(memory.store(MemoryCategory.TASK, "task-a", "a"))
    run(memory.store(MemoryCategory.MEMORY, "mem-a", "a"))

    results = run(memory.search(category=MemoryCategory.TASK))
    assert [n.id for n in results] == ["task-a"]


def test_search_filters_by_tag(memory):
    run(memory.store(MemoryCategory.MEMORY, "tagged", "x", tags=("urgent",)))
    run(memory.store(MemoryCategory.MEMORY, "untagged", "x"))

    results = run(memory.search(tags=("urgent",)))
    assert [n.id for n in results] == ["tagged"]


def test_search_filters_by_query_text(memory):
    run(memory.store(MemoryCategory.MEMORY, "cats", "loves cats"))
    run(memory.store(MemoryCategory.MEMORY, "dogs", "loves dogs"))

    results = run(memory.search(query="cats"))
    assert [n.id for n in results] == ["cats"]


def test_search_over_an_empty_vault_returns_nothing(memory):
    assert run(memory.search()) == []


# ===========================================================================
# person notes
# ===========================================================================


def test_store_person_and_get_person(memory):
    run(
        memory.store_person(
            "Ada Lovelace",
            "colleague",
            tone="formal",
            context_notes="Works on the compiler team.",
            tags=("work",),
        )
    )
    person = run(memory.get_person("Ada Lovelace"))
    assert person.extra["relationship"] == "colleague"
    assert person.extra["tone"] == "formal"
    assert person.body == "Works on the compiler team."
    assert person.tags == ("work",)


def test_person_notes_are_findable_via_search(memory):
    run(memory.store_person("Sam", "friend"))
    results = run(memory.search(category=MemoryCategory.PERSON))
    assert [n.id for n in results] == ["sam"]
    assert results[0].extra["relationship"] == "friend"


# ===========================================================================
# audit trail reuse
# ===========================================================================


def test_store_and_delete_go_through_a_supplied_request_context(memory, vault):
    request = RequestContext.create(request_id="corr-memory")
    run(memory.store(MemoryCategory.MEMORY, "audited", "x", request=request))
    run(memory.delete(MemoryCategory.MEMORY, "audited", request=request))
    assert not (vault / "memories" / "audited.md").exists()


# ===========================================================================
# conversation context - in-memory only
# ===========================================================================


def test_conversation_context_holds_messages_in_process_only():
    convo = ConversationContext(session_id="s1")
    convo.add_message("user", "hello")
    convo.add_message("assistant", "hi there")

    assert convo.messages == (
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    )


def test_conversation_context_clear_empties_it():
    convo = ConversationContext(session_id="s1")
    convo.add_message("user", "hello")
    convo.clear()
    assert convo.messages == ()


if __name__ == "__main__":
    # ponytail: smallest runnable check for the store/retrieve/update/delete
    # round trip, independent of pytest.
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp()
    try:
        v = FilesystemPolicy([tmp])
        svc = MemoryService(vault_root=tmp, policy=v)
        run(svc.store(MemoryCategory.MEMORY, "demo", "hello"))
        assert run(svc.retrieve(MemoryCategory.MEMORY, "demo")).body == "hello"
        run(svc.update(MemoryCategory.MEMORY, "demo", body="updated"))
        assert run(svc.retrieve(MemoryCategory.MEMORY, "demo")).body == "updated"
        run(svc.delete(MemoryCategory.MEMORY, "demo"))
        try:
            run(svc.retrieve(MemoryCategory.MEMORY, "demo"))
        except MemoryNotFoundError:
            pass
        else:
            raise AssertionError("expected MemoryNotFoundError after delete")
        print("memory service self-check OK")
    finally:
        shutil.rmtree(tmp)
