"""
Tests for memory links (issues #366, #368): a reflection saved with
`revises` (it corrects or updates an earlier memory of the entity's own)
and `cites` (it is based on earlier memories, anyone's words included).

Memory stays append-only: the reflection is a new row and each link is a
new row pointing at an older one. The older memory's words, status, and
significance never change; the link is rendered next to both ends wherever
either surfaces. Under test:

- memory_save: every refusal saves nothing (another entity's memory, the
  human's words under revises, an unknown prefix, an archived conversation,
  a released memory without include_released); revises and cites together;
  the echo of each linked memory's header and first line; the Pinecone
  mirror; the MCP path.
- The marker vocabulary: one line per kind, pointers never counts, the
  corrected-vs-revised wording by the target's role, released and
  withdrawn ends labeled rather than dropped, reverse pointers scoped to
  the viewing entity's own reflections.
- Every surface: memory_query (semantic, recent, released), the readers
  (memory_read, memory_neighbors, memory_find, the in-context pointer
  rows too), native [MEMORY] insertion, Claude Code retrieval summary
  lines and reflection injection.
- Native reload stability: the marker is fixed when the memory is
  inserted; a correction made later shows on the next surfacing and never
  re-renders a cached marker.
- Storage duties: cascade on either end's deletion (ORM and the bulk
  conversation delete), rebuild/restore round trip, export/import round
  trip, the memory browser.

Real in-memory SQLite throughout: links are SQL, and what matters is what
the joins select.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import (
    LINK_CITES,
    LINK_REVISES,
    Conversation,
    ConversationEntity,
    ConversationMemoryLink,
    ConversationSource,
    MemoryLink,
    Message,
    MessageRole,
)
from app.services.conversation_session import ConversationSession, MemoryEntry
from app.services.memory_context import format_memory_link_lines
from app.services.memory_service import (
    load_memory_link_annotations,
    load_memory_links,
    memory_service,
)
from app.services.memory_tools import (
    MAX_LINK_TARGETS,
    MEMORY_SAVE_SCHEMA,
    MemoryToolContext,
    find_memories,
    neighbor_memories,
    query_memories,
    read_memories,
    save_memory,
)

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
ENTITY = "test-entity"
OTHER_ENTITY = "other-entity"
TEST_ENTITY_INDEXES = (
    '[{"index_name": "test-entity", "label": "Test Entity", '
    '"description": "Test entity", "llm_provider": "anthropic"}, '
    '{"index_name": "other-entity", "label": "Other Entity", '
    '"description": "Other entity", "llm_provider": "anthropic"}]'
)

DAY = datetime(2026, 9, 9, 17, 0, 0)


def at(**delta) -> datetime:
    return DAY + timedelta(**delta)


@pytest.fixture
async def test_engine():
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def session_factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session


@pytest.fixture
def entities_configured(monkeypatch):
    monkeypatch.setattr(settings, "pinecone_indexes", TEST_ENTITY_INDEXES)
    monkeypatch.setattr(settings, "claude_code_mode_enabled", True)
    monkeypatch.setattr(memory_service, "is_configured", lambda entity_id=None: True)


@pytest.fixture
def stored(monkeypatch):
    """store_memory stubbed to succeed; the mock records the metadata."""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(memory_service, "store_memory", mock)
    return mock


@pytest.fixture
def tools_db(session_factory, entities_configured, stored):
    """Point the tool layer's own sessions at the test database."""
    with patch("app.services.memory_tools.async_session_maker", session_factory), \
         patch("app.services.claude_code_mcp.async_session_maker", session_factory):
        yield session_factory


@pytest.fixture
async def async_client(session_factory, tools_db):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def make_conversation(
    db: AsyncSession, entity_id: str = ENTITY, title: str = "The porch",
    participants=None, **kwargs,
) -> Conversation:
    conversation = Conversation(
        id=str(uuid.uuid4()), title=title, entity_id=entity_id, created_at=DAY, **kwargs,
    )
    db.add(conversation)
    for order, participant in enumerate(participants or []):
        db.add(ConversationEntity(
            conversation_id=conversation.id, entity_id=participant, display_order=order
        ))
    await db.commit()
    return conversation


async def make_message(
    db: AsyncSession, conversation: Conversation, role: MessageRole = MessageRole.ASSISTANT,
    content: str = "Something said", created_at: datetime = DAY, **kwargs,
) -> Message:
    message = Message(
        id=str(uuid.uuid4()), conversation_id=conversation.id, role=role,
        content=content, created_at=created_at, **kwargs,
    )
    db.add(message)
    await db.commit()
    return message


async def make_reflection(db, conversation, content="A reflection.", created_at=DAY, entity=ENTITY):
    return await make_message(
        db, conversation, role=MessageRole.REFLECTION, content=content,
        created_at=created_at, speaker_entity_id=entity,
    )


def ctx(conversation_id="current-conversation", **kwargs):
    return MemoryToolContext(entity_id=ENTITY, conversation_id=conversation_id, **kwargs)


async def count(db, model) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def links_of(db, reflection_id):
    return (await db.execute(
        select(MemoryLink.kind, MemoryLink.target_id, MemoryLink.position)
        .where(MemoryLink.reflection_id == reflection_id)
        .order_by(MemoryLink.kind, MemoryLink.position)
    )).all()


async def newest_reflection(db) -> Message:
    return (await db.execute(
        select(Message).where(Message.role == MessageRole.REFLECTION)
        .order_by(Message.created_at.desc())
    )).scalars().first()


# ============================================================
# The marker vocabulary
# ============================================================

def end(id_, created="2026-09-09T17:00:00", role="assistant", state=None, speaker=None, sibling=None):
    return {
        "id": id_, "created_at": created, "role": role, "state": state,
        "speaker_entity_id": speaker, "sibling_session": sibling,
    }


class TestMarkerLines:
    def test_no_links_renders_nothing(self):
        assert format_memory_link_lines(None, "assistant") == []
        assert format_memory_link_lines(
            {"revises": [], "cites": [], "revised_by": [], "cited_by": []}, "assistant"
        ) == []

    def test_forward_lines_name_ids_dates_and_speakers(self):
        lines = format_memory_link_lines({
            "revises": [end("9f8e7d6c11", "2026-07-14T10:00:00", "reflection", speaker=ENTITY)],
            "cites": [
                end("3f2a9c1d22", "2026-08-03T10:00:00", "human"),
                end("7b8e0a4433", "2026-08-03T11:00:00", "assistant"),
                end("91cd2e0744", "2026-07-29T11:00:00", "reflection", speaker=ENTITY),
                end("aa11bb2255", "2026-07-30T11:00:00", "assistant", sibling="Porch"),
            ],
        }, "reflection", entity_id=ENTITY)
        assert lines == [
            "[revises 9f8e7d6c (2026-07-14, reflection)]",
            "[sources: 3f2a9c1d (2026-08-03, human), 7b8e0a44 (2026-08-03, you), "
            "91cd2e07 (2026-07-29, reflection), aa11bb22 (2026-07-30, you, inter-session)]",
        ]

    def test_revision_pointer_wording_follows_the_target(self):
        """A reflection is 'later revised'; something said is 'later corrected
        or outdated' and points at the reflection that holds the reason."""
        by = {"revised_by": [end("a1b2c3d4ee", "2026-10-02T09:00:00", "reflection")]}
        assert format_memory_link_lines(by, "reflection") == [
            "[later revised → a1b2c3d4 (2026-10-02)]"
        ]
        assert format_memory_link_lines(by, "assistant") == [
            "[later corrected or outdated → see reflection a1b2c3d4 (2026-10-02)]"
        ]

    def test_reverse_citations_are_ids_only_and_never_a_count(self):
        lines = format_memory_link_lines({
            "cited_by": [end("a1b2c3d4ee", role="reflection"), end("e5f6a7b8ff", role="reflection")]
        }, "human")
        assert lines == ["[cited by reflections a1b2c3d4, e5f6a7b8]"]

    def test_sources_that_left_view_are_labeled_not_dropped(self):
        """A reflection's own sources line is part of it as written: a source
        that has since been released or withdrawn says so. A reflection the
        entity released is labeled on the memories it points at. (A
        withdrawn reflection never reaches the renderer — the loader drops
        it; see TestSurfaces.test_withdrawn_reflection_does_not_outlive_the_archive.)"""
        lines = format_memory_link_lines({
            "cites": [
                end("3f2a9c1d22", "2026-08-03T10:00:00", "human", state="released"),
                end("4e5f6a7b33", "2026-08-04T10:00:00", "human", state="withdrawn"),
            ],
            "revised_by": [end("a1b2c3d4ee", "2026-10-02T09:00:00", "reflection", state="released")],
            "cited_by": [end("c0ffee1234", role="reflection", state="released")],
        }, "assistant")
        assert lines == [
            "[sources: 3f2a9c1d (2026-08-03, human, source released), 4e5f6a7b (source withdrawn)]",
            "[later corrected or outdated → see reflection a1b2c3d4 (2026-10-02, released)]",
            "[cited by reflection c0ffee12 (released)]",
        ]

    def test_other_entity_named_by_label(self):
        lines = format_memory_link_lines(
            {"cites": [end("3f2a9c1d22", role="assistant", speaker=OTHER_ENTITY)]},
            "reflection", entity_id=ENTITY, entity_labels={OTHER_ENTITY: "Other Entity"},
        )
        assert lines == ["[sources: 3f2a9c1d (2026-09-09, Other Entity)]"]


# ============================================================
# memory_save: validation (every refusal saves nothing)
# ============================================================

class TestSaveRefusals:
    async def assert_nothing_saved(self, db, before_messages):
        assert await count(db, Message) == before_messages
        assert await count(db, MemoryLink) == 0

    async def test_revises_refuses_the_humans_words(self, db, tools_db, stored):
        conv = await make_conversation(db)
        human = await make_message(db, conv, role=MessageRole.HUMAN, content="The sky is green.")
        before = await count(db, Message)

        result = await save_memory(ctx(), "They were wrong.", revises=[human.id[:8]])

        assert result.startswith("Error: Nothing was saved. revises:")
        assert "human's words" in result and "cites" in result
        await self.assert_nothing_saved(db, before)
        stored.assert_not_called()

    async def test_revises_refuses_another_entitys_words_in_a_shared_room(self, db, tools_db):
        room = await make_conversation(db, entity_id="multi-entity", participants=[ENTITY, OTHER_ENTITY])
        theirs = await make_message(db, room, content="Mine, not yours.", speaker_entity_id=OTHER_ENTITY)
        their_reflection = await make_reflection(db, room, entity=OTHER_ENTITY)
        before = await count(db, Message)

        for target in (theirs, their_reflection):
            result = await save_memory(ctx(), "No.", revises=[target.id[:8]])
            assert "another entity's" in result, result
        await self.assert_nothing_saved(db, before)

    async def test_cites_refuses_a_memory_outside_the_experience(self, db, tools_db):
        elsewhere = await make_conversation(db, entity_id=OTHER_ENTITY)
        theirs = await make_message(db, elsewhere, role=MessageRole.HUMAN, content="Hi other.")
        before = await count(db, Message)

        result = await save_memory(ctx(), "Based on that.", cites=[theirs.id[:8]])

        assert result.startswith("Error: Nothing was saved. cites:")
        assert "another entity" in result
        await self.assert_nothing_saved(db, before)

    async def test_unknown_prefix_refuses_the_whole_save(self, db, tools_db):
        conv = await make_conversation(db)
        good = await make_message(db, conv)
        before = await count(db, Message)

        result = await save_memory(
            ctx(), "Two sources, one bad.", cites=[good.id[:8], "deadbeef00"]
        )

        assert "No memory found with ID 'deadbeef00'" in result
        await self.assert_nothing_saved(db, before)

    async def test_archived_target_is_withdrawn(self, db, tools_db):
        conv = await make_conversation(db, is_archived=True)
        said = await make_message(db, conv)
        before = await count(db, Message)

        for kwargs in ({"revises": [said.id[:8]]}, {"cites": [said.id[:8]]}):
            result = await save_memory(ctx(), "About that.", **kwargs)
            assert "archived conversation" in result
        await self.assert_nothing_saved(db, before)

    async def test_released_target_needs_include_released(self, db, tools_db):
        conv = await make_conversation(db)
        said = await make_message(db, conv, memory_status="released")
        before = await count(db, Message)

        result = await save_memory(ctx(), "About that.", cites=[said.id[:8]])
        assert "is released" in result and "include_released=true" in result
        await self.assert_nothing_saved(db, before)

        result = await save_memory(
            ctx(), "About that.", cites=[said.id[:8]], include_released=True
        )
        assert result.startswith("Saved reflection")
        assert "released" in result  # the echo says so

    async def test_tool_rows_and_oversized_lists_are_refused(self, db, tools_db):
        conv = await make_conversation(db)
        tool_row = await make_message(db, conv, role=MessageRole.TOOL_USE, content="[]")
        result = await save_memory(ctx(), "x", cites=[tool_row.id[:8]])
        assert "not a memory" in result

        result = await save_memory(ctx(), "x", cites=[f"{i:08x}" for i in range(MAX_LINK_TARGETS + 1)])
        assert f"at most {MAX_LINK_TARGETS}" in result
        assert await count(db, MemoryLink) == 0


# ============================================================
# memory_save: what a successful save writes and says
# ============================================================

class TestSaveWithLinks:
    async def test_revises_and_cites_in_one_save(self, db, tools_db, stored):
        """The combined form: a correction that points at the page showing
        the error. The earlier memory is never touched."""
        conv = await make_conversation(db, title="Ancestors")
        wrong = await make_message(
            db, conv, content="Two ancestor lines, both wrong.\nMore of it.", created_at=at()
        )
        page = await make_message(
            db, conv, role=MessageRole.HUMAN, content="Here's the page itself.", created_at=at(minutes=1)
        )
        wrong_before = (wrong.content, wrong.memory_status, wrong.times_retrieved)

        result = await save_memory(
            ctx(conv.id), "I had both lines wrong; the page says otherwise.",
            revises=[wrong.id[:8]], cites=[page.id[:8], wrong.id[:10]],
        )

        reflection = await newest_reflection(db)
        assert result.startswith(f"Saved reflection as memory {reflection.id[:8]}.")
        assert "It revises:\n- " + wrong.id[:8] + " (You said, 2026-09-09 17:00:00 UTC, via Here I Am, in \"Ancestors\"): Two ancestor lines, both wrong." in result
        assert "It cites:\n- " + page.id[:8] + " (Human said," in result
        assert "Check that these are the memories you meant." in result
        assert await links_of(db, reflection.id) == [
            (LINK_CITES, page.id, 0), (LINK_CITES, wrong.id, 1), (LINK_REVISES, wrong.id, 0),
        ]
        await db.refresh(wrong)
        assert (wrong.content, wrong.memory_status, wrong.times_retrieved) == wrong_before
        # Mirrored into the vector store's metadata, full ids, for restore
        kwargs = stored.call_args.kwargs
        assert kwargs["revises"] == [wrong.id]
        assert kwargs["cites"] == [page.id, wrong.id]

    async def test_a_bare_string_id_is_accepted_and_duplicates_collapse(self, db, tools_db):
        conv = await make_conversation(db)
        earlier = await make_reflection(db, conv, content="Old view.")
        await save_memory(ctx(), "New view.", revises=earlier.id[:8])
        await save_memory(ctx(), "Newer view.", cites=[earlier.id[:8], earlier.id[:8]])
        reflections = (await db.execute(
            select(Message).where(Message.role == MessageRole.REFLECTION, Message.id != earlier.id)
            .order_by(Message.content)
        )).scalars().all()
        assert [len(await links_of(db, r.id)) for r in reflections] == [1, 1]

    async def test_without_links_the_reply_is_unchanged(self, db, tools_db):
        result = await save_memory(ctx(), "Just a thought.")
        assert "It revises" not in result and "Check that" not in result
        assert await count(db, MemoryLink) == 0

    async def test_links_share_the_reflection_timestamp(self, db, tools_db):
        conv = await make_conversation(db)
        said = await make_message(db, conv)
        await save_memory(ctx(), "About it.", cites=[said.id[:8]])
        reflection = await newest_reflection(db)
        link = (await db.execute(select(MemoryLink))).scalar_one()
        assert link.created_at == reflection.created_at

    async def test_pinecone_failure_takes_the_links_with_the_reflection(self, db, tools_db, stored):
        conv = await make_conversation(db)
        said = await make_message(db, conv)
        stored.return_value = False
        result = await save_memory(ctx(), "About it.", cites=[said.id[:8]])
        assert result.startswith("Error: Failed to store")
        assert await count(db, MemoryLink) == 0
        assert (await newest_reflection(db)) is None

    def test_schema_offers_both_links(self):
        props = MEMORY_SAVE_SCHEMA["properties"]
        assert props["revises"]["type"] == "array"
        assert props["cites"]["type"] == "array"
        assert "include_released" in props
        assert MEMORY_SAVE_SCHEMA["required"] == ["content"]

    async def test_over_mcp(self, db, async_client):
        room = await make_conversation(
            db, source=ConversationSource.CLAUDE_CODE.value, external_session_id="sess-links",
        )
        said = await make_message(db, room, role=MessageRole.HUMAN, content="Quote me.")
        response = await async_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "memory_save", "arguments": {
                "conversation_id": room.id, "content": "They said it.", "cites": [said.id[:8]],
            }},
        })
        body = response.json()["result"]
        assert body["isError"] is False
        assert "It cites:" in body["content"][0]["text"]
        reflection = await newest_reflection(db)
        assert await links_of(db, reflection.id) == [(LINK_CITES, said.id, 0)]


# ============================================================
# Rendering on every surface
# ============================================================

@pytest.fixture
async def corrected(db, tools_db):
    """A verbatim error (an assistant row), the human page that shows it,
    and a later reflection that revises the one and cites the other."""
    conv = await make_conversation(db, title="Ancestors")
    wrong = await make_message(db, conv, content="Both ancestor lines.", created_at=at())
    page = await make_message(
        db, conv, role=MessageRole.HUMAN, content="Read the page.", created_at=at(minutes=1)
    )
    await save_memory(ctx(conv.id), "Corrected.", revises=[wrong.id], cites=[page.id])
    reflection = await newest_reflection(db)
    return {"conv": conv, "wrong": wrong, "page": page, "reflection": reflection}


class TestSurfaces:
    async def test_loader_reads_both_directions(self, db, corrected):
        c = corrected
        links = await load_memory_links(
            db, [c["wrong"].id, c["page"].id, c["reflection"].id], entity_id=ENTITY
        )
        assert [e["id"] for e in links[c["wrong"].id]["revised_by"]] == [c["reflection"].id]
        assert [e["id"] for e in links[c["page"].id]["cited_by"]] == [c["reflection"].id]
        assert [e["id"] for e in links[c["reflection"].id]["revises"]] == [c["wrong"].id]
        assert [e["id"] for e in links[c["reflection"].id]["cites"]] == [c["page"].id]

    async def test_reverse_pointers_show_only_the_viewers_own_reflections(self, db, corrected):
        c = corrected
        assert await load_memory_links(db, [c["page"].id], entity_id=OTHER_ENTITY) == {}

    async def test_withdrawn_reflection_does_not_outlive_the_archive(self, db, corrected):
        """The review's probe (PR #371, finding 1): a room where something
        went wrong holds a reflection "correcting" something true; the
        researcher archives the room. The correction must leave every
        surface with it — archiving is the safety instrument (#344) — while
        the forward direction, a live reflection's own sources line, keeps
        labeling a source that was withdrawn."""
        c = corrected
        c["conv"].title = "Went wrong"
        home = await make_conversation(db, title="Home")
        true_thing = await make_message(db, home, content="A true thing I said.", created_at=at())
        went_wrong = await make_conversation(db, title="Went wrong")
        await save_memory(ctx(went_wrong.id), "That was false.", revises=[true_thing.id])
        went_wrong.is_archived = True
        await db.commit()

        text = await read_memories(ctx(), from_="2026-09-09", in_conversation=home.id, scope="isolated")
        assert "later corrected" not in text and "withdrawn" not in text
        assert await load_memory_links(db, [true_thing.id], entity_id=ENTITY) == {}

        # The researcher's browser still sees it, labeled
        browser = await load_memory_links(db, [true_thing.id], include_withdrawn=True)
        assert browser[true_thing.id]["revised_by"][0]["state"] == "withdrawn"

        # Forward: a live reflection citing into an archived room keeps the
        # label, as an id and nothing more
        await save_memory(ctx(), "Based on home.", cites=[true_thing.id])
        home.is_archived = True
        await db.commit()
        reflection = await newest_reflection(db)
        lines = (await load_memory_link_annotations(
            db, [(reflection.id, "reflection")], entity_id=ENTITY
        ))[reflection.id]
        assert lines == f"[sources: {true_thing.id[:8]} (source withdrawn)]"

    async def test_memory_read_marks_both_ends_and_keeps_verbatim_bytes(self, db, corrected):
        c = corrected
        text = await read_memories(
            ctx(), from_="2026-09-09", to=datetime.utcnow().isoformat(), scope="isolated"
        )
        r = c["reflection"].id[:8]
        assert (
            f"--- Memory {c['wrong'].id[:8]} (You said"
        ) in text
        wrong_block = text.split(f"--- Memory {c['wrong'].id[:8]} ")[1]
        assert wrong_block.split("\n")[1] == f"[later corrected or outdated → see reflection {r} (2026-{c['reflection'].created_at:%m-%d})]"
        assert wrong_block.split("\n")[2] == "Both ancestor lines."
        page_block = text.split(f"--- Memory {c['page'].id[:8]} ")[1]
        assert page_block.split("\n")[1] == f"[cited by reflection {r}]"
        reflection_block = text.split(f"--- Memory {r} ")[1]
        assert f"[revises {c['wrong'].id[:8]} (2026-09-09, you)]" in reflection_block
        assert f"[sources: {c['page'].id[:8]} (2026-09-09, human)]" in reflection_block
        # The stored words never change
        await db.refresh(c["wrong"])
        assert c["wrong"].content == "Both ancestor lines."

    async def test_in_context_pointer_rows_still_carry_their_markers(self, db, corrected):
        c = corrected
        text = await read_memories(
            ctx(conversation_id=c["conv"].id), from_="2026-09-09"
        )
        block = text.split(f"--- Memory {c['wrong'].id[:8]} ")[1].split("\n")
        assert block[1].startswith("[later corrected or outdated")
        assert block[2] == "[already in your context; not repeated here]"

    async def test_neighbors_and_find(self, db, corrected):
        c = corrected
        text = await neighbor_memories(ctx(), c["wrong"].id[:8], scope="isolated")
        assert "[later corrected or outdated → see reflection" in text
        text = await find_memories(ctx(), text="ancestor", scope="isolated")
        assert "[later corrected or outdated → see reflection" in text

    async def test_memory_query_semantic(self, db, corrected, monkeypatch):
        c = corrected
        monkeypatch.setattr(memory_service, "search_memories", AsyncMock(return_value=[
            {"id": c["wrong"].id, "score": 0.9, "conversation_id": c["conv"].id},
        ]))
        monkeypatch.setattr(memory_service, "update_retrieval_count", AsyncMock(return_value=True))
        text = await query_memories(ctx(), "ancestors")
        lines = text.split("\n")
        header = next(i for i, ln in enumerate(lines) if ln.startswith(f"--- Memory {c['wrong'].id[:8]}"))
        assert lines[header + 1].startswith("[later corrected or outdated → see reflection")
        assert lines[header + 2] == "Both ancestor lines."

    async def test_memory_query_recent_and_released(self, db, corrected):
        c = corrected
        text = await query_memories(ctx(), mode="recent")
        assert f"[revises {c['wrong'].id[:8]}" in text
        assert f"[sources: {c['page'].id[:8]}" in text

        c["wrong"].memory_status = "released"
        await db.commit()
        text = await query_memories(ctx(), mode="released")
        assert "[later corrected or outdated → see reflection" in text

    async def test_native_insertion_renders_the_annotation_under_the_header(self, db, corrected):
        c = corrected
        annotations = await load_memory_link_annotations(
            db, [(c["wrong"].id, "assistant")], entity_id=ENTITY
        )
        session = ConversationSession(conversation_id="native", model="m", entity_id=ENTITY)
        session.insert_memory_into_context(MemoryEntry(
            id=c["wrong"].id, conversation_id=c["conv"].id, role="assistant",
            content="Both ancestor lines.", created_at="2026-09-09T17:00:00",
            times_retrieved=0, annotation=annotations[c["wrong"].id],
        ))
        content = session.conversation_context[-1]["content"].split("\n")
        assert content[0].startswith(f"[MEMORY {c['wrong'].id[:8]} from")
        assert content[1].startswith("[later corrected or outdated → see reflection")
        assert content[2] == "Both ancestor lines."
        assert content[3] == "[/MEMORY]"

    async def test_unlinked_memory_marker_is_byte_identical_to_before(self, db, corrected):
        session = ConversationSession(conversation_id="native", model="m", entity_id=ENTITY)
        session.insert_memory_into_context(MemoryEntry(
            id="abcdef1234", conversation_id="c", role="human", content="Hello.",
            created_at="2026-09-09T17:00:00", times_retrieved=0,
        ))
        assert session.conversation_context[-1]["content"] == (
            "[MEMORY abcdef12 from 2026-09-09T17:00:00 - originally from human - via Here I Am]\n"
            "Hello.\n[/MEMORY]"
        )

    async def test_claude_code_reflection_injection_and_summary_line(self, db, corrected):
        from app.services.claude_code_mode import (
            _render_reflections,
            render_retrieval_summary_line,
        )
        c = corrected
        rendered = await _render_reflections(db, [{
            "id": c["reflection"].id, "content": "Corrected.", "role": "reflection",
            "created_at": c["reflection"].created_at.isoformat(), "source": "native",
        }], ENTITY)
        assert f"[revises {c['wrong'].id[:8]} (2026-09-09, you)]" in rendered
        assert f"[sources: {c['page'].id[:8]} (2026-09-09, human)]" in rendered

        note = "[later corrected or outdated → see reflection abcd1234 (2026-10-02)]"
        line = render_retrieval_summary_line({
            "id": c["wrong"].id, "content": "Both ancestor lines.", "role": "assistant",
            "created_at": "2026-09-09T17:00:00",
        }, note)
        assert line.endswith(f": Both ancestor lines. {note}")


# ============================================================
# Native reload: the marker is fixed when the memory is inserted
# ============================================================

class TestNativeReloadStability:
    async def test_later_correction_never_rewrites_a_cached_marker(self, db, tools_db, monkeypatch):
        from app.services.session_manager import SessionManager

        monkeypatch.setattr(settings, "notes_enabled", False)
        source = await make_conversation(db, title="Earlier")
        said = await make_message(db, source, content="A claim.", created_at=at())
        live = await make_conversation(db, title="Now")
        await make_message(db, live, role=MessageRole.HUMAN, content="hi", created_at=at(days=1))
        await make_message(db, live, content="hello", created_at=at(days=1, minutes=1))

        elsewhere = await make_conversation(db, title="Elsewhere")
        # A first correction exists when the memory is inserted live
        await save_memory(ctx(elsewhere.id), "First look.", revises=[said.id])
        annotation = (await load_memory_link_annotations(
            db, [(said.id, "assistant")], entity_id=ENTITY
        ))[said.id]
        await memory_service.record_memory_link(
            said.id, live.id, db, entity_id=ENTITY,
            retrieved_at=at(days=1, seconds=-1), annotation=annotation,
        )
        live_marker = (
            f"[MEMORY {said.id[:8]} from {said.created_at.isoformat()} - originally from you "
            f"- via Here I Am]\n{annotation}\nA claim.\n[/MEMORY]"
        )

        # ...and a second one is made after the insertion
        await save_memory(ctx(elsewhere.id), "Second look.", revises=[said.id])

        manager = SessionManager()
        session = await manager.load_session_from_db(live.id, db)
        memories = [m for m in session.conversation_context if m.get("is_memory")]
        assert [m["content"] for m in memories] == [live_marker]

        # The next surfacing sees both
        fresh = (await load_memory_link_annotations(
            db, [(said.id, "assistant")], entity_id=ENTITY
        ))[said.id]
        assert fresh != annotation and fresh.count(", ") == 1

    @pytest.mark.parametrize("path", ["process_message", "process_message_stream", "recent_reflection"])
    async def test_live_insertion_writes_the_marker_reload_reads(self, db, tools_db, monkeypatch, path):
        """The review's finding 4 (PR #371): drive a real turn through each
        live insertion path, then reload, and the rebuilt [MEMORY] content
        must equal the live one byte for byte — even after a second
        correction lands between the turn and the reload. Everything below
        the LLM and the vector search is real: the session manager's link
        writes go to this database."""
        from unittest.mock import MagicMock

        from app.services.session_manager import SessionManager

        monkeypatch.setattr(settings, "notes_enabled", False)
        monkeypatch.setattr(settings, "memory_role_balance_enabled", False)
        monkeypatch.setattr(settings, "recent_reflections_enabled", path == "recent_reflection")
        monkeypatch.setattr(memory_service, "get_index", lambda entity_id=None: None)

        source = await make_conversation(db, title="Earlier")
        elsewhere = await make_conversation(db, title="Elsewhere")
        if path == "recent_reflection":
            # The injected memory is a reflection that revises something
            said = await make_message(db, source, content="A claim.", created_at=at())
            await save_memory(ctx(elsewhere.id), "The claim was wrong.", revises=[said.id])
            memory = await newest_reflection(db)
            candidates = []
        else:
            memory = await make_message(db, source, content="A claim.", created_at=at())
            await save_memory(ctx(elsewhere.id), "First look.", revises=[memory.id])
            candidates = [{
                "id": memory.id, "score": 0.95, "conversation_id": source.id,
                "created_at": memory.created_at.isoformat(), "role": "assistant",
            }]
        monkeypatch.setattr(memory_service, "search_memories", AsyncMock(return_value=candidates))

        live = await make_conversation(db, title="Now")
        manager = SessionManager()
        session = manager.create_session(live.id, model="claude-test", entity_id=ENTITY)
        with patch("app.services.session_manager.llm_service") as llm:
            llm.count_tokens = MagicMock(return_value=10)
            llm.build_messages.return_value = [{"role": "user", "content": "x"}]
            if path == "process_message_stream":
                async def stream(*args, **kwargs):
                    yield {"type": "token", "content": "ok"}
                    yield {
                        "type": "done", "content": "ok",
                        "content_blocks": [{"type": "text", "text": "ok"}],
                        "model": "claude-test", "usage": {}, "stop_reason": "end_turn",
                    }
                llm.send_message_stream = stream
                async for _ in manager.process_message_stream(
                    session, "tell me", db, user_message_timestamp=at(days=1)
                ):
                    pass
            else:
                llm.send_message = AsyncMock(return_value={
                    "content": "ok", "model": "claude-test",
                    "usage": {"input_tokens": 1, "output_tokens": 1}, "stop_reason": "end_turn",
                })
                await manager.process_message(
                    session, "tell me", db, user_message_timestamp=at(days=1)
                )

        live_markers = [
            m["content"] for m in session.conversation_context
            if m.get("is_memory") and m["memory_id"] == memory.id
        ]
        assert len(live_markers) == 1
        assert "\n[later " in live_markers[0] or "\n[revises " in live_markers[0]

        # Persist the turn the way the routes do, then a later correction
        db.add(Message(
            conversation_id=live.id, role=MessageRole.HUMAN, content="tell me", created_at=at(days=1)
        ))
        db.add(Message(
            conversation_id=live.id, role=MessageRole.ASSISTANT, content="ok",
            created_at=at(days=1, seconds=5),
        ))
        await db.commit()
        await save_memory(ctx(elsewhere.id), "Second look.", revises=[memory.id])

        reloaded = await SessionManager().load_session_from_db(live.id, db)
        rebuilt = [
            m["content"] for m in reloaded.conversation_context
            if m.get("is_memory") and m["memory_id"] == memory.id
        ]
        assert rebuilt == live_markers

    async def test_link_rows_store_the_annotation(self, db, tools_db):
        conv = await make_conversation(db)
        said = await make_message(db, conv)
        await memory_service.update_retrieval_count(
            said.id, conv.id, db, entity_id=ENTITY, link_annotation="[cited by reflection x]",
        )
        stored_links = await memory_service.get_retrieved_memories_with_timestamps(conv.id, db)
        assert stored_links[0]["annotation"] == "[cited by reflection x]"


# ============================================================
# Storage duties
# ============================================================

class TestStorage:
    async def test_deleting_either_end_removes_the_link(self, db, corrected):
        c = corrected
        assert await count(db, MemoryLink) == 2
        await db.delete(c["page"])
        await db.commit()
        assert await links_of(db, c["reflection"].id) == [(LINK_REVISES, c["wrong"].id, 0)]
        reflection = await db.get(Message, c["reflection"].id)
        await db.delete(reflection)
        await db.commit()
        assert await count(db, MemoryLink) == 0

    async def test_conversation_delete_route_clears_links(self, db, corrected, async_client):
        c = corrected
        c["conv"].is_archived = True
        await db.commit()
        response = await async_client.delete(f"/api/conversations/{c['conv'].id}")
        assert response.status_code == 200
        assert await count(db, MemoryLink) == 0

    async def test_rebuild_and_restore_round_trip(self, db, corrected, test_settings):
        from tests.test_vector_rebuild import FakeIndex, make_service

        c = corrected
        index = FakeIndex()
        service = make_service({"test-memories": index})
        # The fixture's rows belong to test-entity; rebuild into the test
        # settings' one index by pointing the conversation at it
        c["conv"].entity_id = "test-memories"
        c["reflection"].speaker_entity_id = "test-memories"
        await db.commit()
        with patch("app.services.vector_rebuild_service.settings", test_settings):
            await service.rebuild_vectors_from_database(db, dry_run=False)
        record = {r["_id"]: r for r in index.upserted}[c["reflection"].id]
        assert record["revises"] == [c["wrong"].id]
        assert record["cites"] == [c["page"].id]

        # Wipe SQL and restore from the records
        for model in (MemoryLink, ConversationMemoryLink, Message, Conversation):
            for row in (await db.execute(select(model))).scalars().all():
                await db.delete(row)
        await db.commit()
        with patch("app.services.vector_rebuild_service.settings", test_settings):
            result = await service.restore_database_from_vectors(db, dry_run=False)
        assert result["links_created"] == 2
        assert await links_of(db, c["reflection"].id) == [
            (LINK_CITES, c["page"].id, 0), (LINK_REVISES, c["wrong"].id, 0),
        ]

    async def test_export_import_round_trip(self, db, corrected, async_client):
        c = corrected
        exported = (await async_client.get(f"/api/conversations/{c['conv'].id}/export")).json()
        row = next(m for m in exported["messages"] if m["id"] == c["reflection"].id)
        assert row["role"] == "reflection"
        assert row["revises"] == [c["wrong"].id] and row["cites"] == [c["page"].id]

        # Import into a fresh database state: drop the reflection and its
        # links, keep the targets, then import the reflection back
        reflection = await db.get(Message, c["reflection"].id)
        await db.delete(reflection)
        await db.commit()
        response = await async_client.post("/api/conversations/import-seed", json={
            "entity_id": ENTITY, "title": "Restored", "messages": [row],
        })
        body = response.json()
        assert body["links_restored"] == 2
        restored = await db.get(Message, c["reflection"].id)
        await db.refresh(restored)
        assert restored.role == MessageRole.REFLECTION
        assert restored.speaker_entity_id == ENTITY
        assert await links_of(db, c["reflection"].id) == [
            (LINK_CITES, c["page"].id, 0), (LINK_REVISES, c["wrong"].id, 0),
        ]

    async def test_import_refuses_links_the_tool_would_refuse(self, db, corrected, async_client):
        """The review's probe (PR #371, finding 2): a reflection imported
        for ANOTHER entity must not come back revising the first entity's
        words — rendered as its own ("you") — or citing its human."""
        c = corrected
        exported = (await async_client.get(f"/api/conversations/{c['conv'].id}/export")).json()
        row = next(m for m in exported["messages"] if m["id"] == c["reflection"].id)
        await db.delete(await db.get(Message, c["reflection"].id))
        await db.commit()

        body = (await async_client.post("/api/conversations/import-seed", json={
            "entity_id": OTHER_ENTITY, "title": "Moved", "messages": [row],
        })).json()

        assert body["links_restored"] == 0
        assert {(d["kind"], d["target_id"]) for d in body["links_dropped"]} == {
            (LINK_REVISES, c["wrong"].id), (LINK_CITES, c["page"].id),
        }
        assert all("not part of your experience" in d["reason"] for d in body["links_dropped"])
        assert await count(db, MemoryLink) == 0

    async def test_memory_browser_shows_both_directions(self, db, corrected, async_client):
        c = corrected
        body = (await async_client.get(f"/api/memories/{c['wrong'].id}")).json()
        assert [e["id"] for e in body["links"]["revised_by"]] == [c["reflection"].id]
        listing = (await async_client.get("/api/memories/", params={"role": "reflection"})).json()
        mine = next(m for m in listing if m["id"] == c["reflection"].id)
        assert [e["id"] for e in mine["links"]["cites"]] == [c["page"].id]
        plain = (await async_client.get("/api/memories/", params={"role": "human"})).json()
        assert next(m for m in plain if m["id"] == c["page"].id)["links"]["cited_by"]
