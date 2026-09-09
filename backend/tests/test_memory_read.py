"""
Tests for the archive readers (issue #343): memory_read (a span of the
record, in order, paginated by tokens) and memory_neighbors (one memory
with the messages around it).

Everything the entity has of its archive otherwise arrives by similarity;
these read it by position. Rules under test: span boundaries in a non-UTC
timezone; pagination by token budget including the oversized-single-message
case; reflections interleaved where they were saved; sibling letters and
other entities labeled; released skipped by default and included on
request; no retrieval-tracking writes; Claude Code links recorded once;
neighbors at the start and end of a conversation; prefix resolution and the
ambiguity error; archived conversations hidden; the reload-side re-stamping of what a page showed; and the
MCP exposure.

These run against a real (in-memory SQLite) database: both readers are
SQL, and the point of them is what the SQL selects and in what order.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import (
    Conversation,
    ConversationEntity,
    ConversationMemoryLink,
    ConversationSource,
    Message,
    MessageRole,
)
from app.services.memory_service import memory_service
from app.services.memory_tools import (
    MEMORY_RESULT_STAMPING_TOOLS,
    MemoryToolContext,
    neighbor_memories,
    read_memories,
)
from app.services.session_manager import _MEMORY_QUERY_RESULT_ID_RE

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
ENTITY = "test-entity"
OTHER_ENTITY = "other-entity"
TEST_ENTITY_INDEXES = (
    '[{"index_name": "test-entity", "label": "Test Entity", '
    '"description": "Test entity", "llm_provider": "anthropic"}, '
    '{"index_name": "other-entity", "label": "Other Entity", '
    '"description": "Other entity", "llm_provider": "anthropic"}]'
)

# A Tuesday. Eastern is UTC-4 on this date (EDT).
DAY = datetime(2026, 9, 1, 12, 0, 0)


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
def tools_db(session_factory, entities_configured):
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
    db: AsyncSession,
    entity_id: str = ENTITY,
    title: str = "A day on the porch",
    participants=None,
    **kwargs,
) -> Conversation:
    conversation = Conversation(
        id=str(uuid.uuid4()),
        title=title,
        entity_id=entity_id,
        created_at=DAY,
        **kwargs,
    )
    db.add(conversation)
    for order, participant in enumerate(participants or []):
        db.add(ConversationEntity(
            conversation_id=conversation.id, entity_id=participant, display_order=order
        ))
    await db.commit()
    return conversation


async def make_message(
    db: AsyncSession,
    conversation: Conversation,
    role: MessageRole = MessageRole.ASSISTANT,
    content: str = "Something said",
    created_at: datetime = DAY,
    message_id: str = None,
    **kwargs,
) -> Message:
    message = Message(
        id=message_id or str(uuid.uuid4()),
        conversation_id=conversation.id,
        role=role,
        content=content,
        created_at=created_at,
        **kwargs,
    )
    db.add(message)
    await db.commit()
    return message


def ids_in_order(text: str):
    return _MEMORY_QUERY_RESULT_ID_RE.findall(text)


def native_ctx(conversation_id="current-conversation"):
    return MemoryToolContext(entity_id=ENTITY, conversation_id=conversation_id)


def claude_code_ctx(conversation_id):
    return MemoryToolContext(
        entity_id=ENTITY, conversation_id=conversation_id, link_query_results=True
    )


# ============================================================
# memory_read: spans, timezones, ordering, labels
# ============================================================

class TestReadSpan:
    async def test_bare_date_in_a_non_utc_timezone(self, db, tools_db):
        """"September 1st, Eastern" is 04:00 UTC on the 1st to 03:59:59 UTC
        on the 2nd: a message at 01:00 UTC on the 1st is August 31st Eastern
        and stays out; one at 02:00 UTC on the 2nd is still the 1st and is in."""
        conversation = await make_conversation(db)
        aug31_eastern = await make_message(
            db, conversation, content="late on the 31st", created_at=datetime(2026, 9, 1, 1, 0)
        )
        midday = await make_message(
            db, conversation, content="midday on the 1st", created_at=datetime(2026, 9, 1, 16, 0)
        )
        late = await make_message(
            db, conversation, content="ten pm on the 1st", created_at=datetime(2026, 9, 2, 2, 0)
        )
        await make_message(
            db, conversation, content="the 2nd", created_at=datetime(2026, 9, 2, 12, 0)
        )

        result = await read_memories(native_ctx(), from_="2026-09-01", tz="America/New_York")

        assert ids_in_order(result) == [midday.id[:8], late.id[:8]]
        assert aug31_eastern.id[:8] not in result
        assert "2 messages in the span; this page shows 1–2" in result
        # UTC stamp with the local time alongside
        assert "2026-09-02 02:00:00 UTC (2026-09-01 22:00:00 EDT)" in result
        assert "[America/New_York; UTC 2026-09-01 04:00:00 to 2026-09-02 03:59:59]" in result
        assert result.rstrip().endswith("End of span.")

    async def test_utc_default_and_explicit_to(self, db, tools_db):
        conversation = await make_conversation(db)
        first = await make_message(db, conversation, content="a", created_at=at(hours=-2))
        second = await make_message(db, conversation, content="b", created_at=at(hours=1))
        await make_message(db, conversation, content="c", created_at=at(days=3))

        result = await read_memories(
            native_ctx(), from_="2026-09-01T09:00", to="2026-09-02"
        )
        assert ids_in_order(result) == [first.id[:8], second.id[:8]]
        assert "2026-09-01 09:00:00 to 2026-09-02 23:59:59 UTC" in result
        # UTC only: no bracketed local time
        assert "(2026-09-01" not in result.split("\n")[0]

    async def test_empty_span_says_so_plainly(self, db, tools_db):
        await make_conversation(db)
        result = await read_memories(native_ctx(), from_="2020-01-01")
        assert result.startswith("No messages between 2020-01-01 00:00:00 to 2020-01-01 23:59:59 UTC")
        assert "Released memories are not shown" in result

    async def test_argument_errors(self, db, tools_db):
        assert (await read_memories(native_ctx())).startswith("Error: 'from' is required")
        assert "Could not parse" in await read_memories(native_ctx(), from_="last tuesday")
        assert "Unknown timezone" in await read_memories(native_ctx(), from_="2026-09-01", tz="Mars/Olympus")
        assert "is before 'from'" in await read_memories(
            native_ctx(), from_="2026-09-02", to="2026-09-01"
        )
        assert "Unknown source" in await read_memories(
            native_ctx(), from_="2026-09-01", source="cats"
        )
        assert "Unrecognized cursor" in await read_memories(
            native_ctx(), from_="2026-09-01", cursor="nope"
        )
        assert "No entity context" in await read_memories(
            MemoryToolContext(entity_id=None), from_="2026-09-01"
        )

    async def test_order_interleaves_reflections_and_labels_every_voice(self, db, tools_db):
        """Reflections sit where they were saved; the human, you, a sibling
        letter, and (in a multi-entity room) the other entity are each
        labeled in the house's words; a span across rooms names each room."""
        porch = await make_conversation(db, title="Porch", source=ConversationSource.CLAUDE_CODE.value)
        human = await make_message(
            db, porch, role=MessageRole.HUMAN, content="Good morning.", created_at=at(minutes=0)
        )
        mine = await make_message(
            db, porch, content="Morning.", created_at=at(minutes=1), speaker_entity_id=ENTITY
        )
        reflection = await make_message(
            db, porch, role=MessageRole.REFLECTION, content="Saved: the morning.",
            created_at=at(minutes=2), speaker_entity_id=ENTITY,
        )
        letter = await make_message(
            db, porch, content="Letter from the workshop.", created_at=at(minutes=3),
            sibling_session="Workshop",
        )
        salon = await make_conversation(
            db, entity_id="multi-entity", title="Salon", participants=[ENTITY, OTHER_ENTITY]
        )
        theirs = await make_message(
            db, salon, content="Other entity speaking.", created_at=at(minutes=4),
            speaker_entity_id=OTHER_ENTITY,
        )
        their_reflection = await make_message(
            db, salon, role=MessageRole.REFLECTION, content="Their note.", created_at=at(minutes=5),
            speaker_entity_id=OTHER_ENTITY,
        )
        # Tool exchange rows are not memories and never appear
        await make_message(
            db, porch, role=MessageRole.TOOL_USE, content="[]", created_at=at(minutes=2, seconds=30)
        )

        result = await read_memories(native_ctx(), from_="2026-09-01")

        assert ids_in_order(result) == [
            m.id[:8] for m in (human, mine, reflection, letter, theirs, their_reflection)
        ]
        assert f"Memory {human.id[:8]} (Human said," in result
        assert f"Memory {mine.id[:8]} (You said," in result
        assert f"Memory {reflection.id[:8]} (You reflected," in result
        assert f'Memory {letter.id[:8]} (You said (inter-session message from "Workshop"),' in result
        assert f"Memory {theirs.id[:8]} (Other Entity said," in result
        assert f"Memory {their_reflection.id[:8]} (Other Entity reflected," in result
        assert 'via Claude Code, in "Porch")' in result
        assert 'via Here I Am, in "Salon")' in result
        assert "Good morning.\n" in result and "Their note.\n" in result

    async def test_source_filter_and_conversation_prefix(self, db, tools_db):
        porch = await make_conversation(db, title="Porch")
        other = await make_conversation(db, title="Elsewhere")
        human = await make_message(db, porch, role=MessageRole.HUMAN, content="h", created_at=at())
        await make_message(db, porch, content="a", created_at=at(minutes=1))
        await make_message(db, other, role=MessageRole.HUMAN, content="h2", created_at=at(minutes=2))

        result = await read_memories(
            native_ctx(), from_="2026-09-01", source="human", in_conversation=porch.id[:8]
        )
        assert ids_in_order(result) == [human.id[:8]]
        assert "(the human's messages only)" in result
        assert ', in "Porch"' in result.split("\n")[0]

        # Another entity's conversation is not the reader's to open
        foreign = await make_conversation(db, entity_id=OTHER_ENTITY, title="Not mine")
        result = await read_memories(native_ctx(), from_="2026-09-01", in_conversation=foreign.id)
        assert result.startswith("Error: No conversation of yours found")

        # Ambiguous prefix: two conversations sharing six characters
        twin_a = await make_conversation(db, title="A")
        twin_b = await make_conversation(db, title="B")
        twin_b.id = twin_a.id[:6] + "-twin"
        db.add(twin_b)
        await db.commit()
        result = await read_memories(native_ctx(), from_="2026-09-01", in_conversation=twin_a.id[:6])
        assert "is ambiguous" in result

    async def test_archived_conversations_are_hidden(self, db, tools_db):
        """Archiving removes a conversation where something went wrong from
        every memory surface; the readers are no exception: not in a span,
        not by conversation prefix, not as a neighbor window."""
        archived = await make_conversation(db, title="Old room", is_archived=True)
        message = await make_message(db, archived, content="from the archive", created_at=at())
        live = await make_conversation(db, title="Live")
        kept = await make_message(db, live, content="kept", created_at=at(minutes=1))

        result = await read_memories(native_ctx(), from_="2026-09-01")
        assert ids_in_order(result) == [kept.id[:8]]
        assert "1 messages in the span" in result

        result = await read_memories(native_ctx(), from_="2026-09-01", in_conversation=archived.id)
        assert result.startswith("Error: No conversation of yours found")

        result = await neighbor_memories(native_ctx(), message.id)
        assert "belongs to an archived conversation" in result

    async def test_current_conversation_and_in_context_memories_are_not_excluded(
        self, db, tools_db
    ):
        conversation = await make_conversation(db)
        here = await make_message(db, conversation, content="said here", created_at=at())
        ctx = native_ctx(conversation_id=conversation.id)
        ctx.extra_exclude_ids = {here.id}
        ctx.turn_query_memory_ids = {here.id}
        result = await read_memories(ctx, from_="2026-09-01")
        assert ids_in_order(result) == [here.id[:8]]


# ============================================================
# memory_read: released, model, pagination
# ============================================================

class TestReadReleasedAndModel:
    async def test_released_skipped_by_default_and_included_on_request(self, db, tools_db):
        conversation = await make_conversation(db)
        kept = await make_message(db, conversation, content="kept", created_at=at())
        released = await make_message(
            db, conversation, content="let go", created_at=at(minutes=1),
            memory_status="released", status_set_by="entity", status_set_at=at(days=1),
        )
        pinned = await make_message(
            db, conversation, content="held", created_at=at(minutes=2), memory_status="pinned"
        )

        result = await read_memories(native_ctx(), from_="2026-09-01")
        assert ids_in_order(result) == [kept.id[:8], pinned.id[:8]]
        assert "2 messages in the span" in result
        assert "; pinned)" in result

        result = await read_memories(native_ctx(), from_="2026-09-01", include_released=True)
        assert ids_in_order(result) == [kept.id[:8], released.id[:8], pinned.id[:8]]
        assert "3 messages in the span" in result
        assert "; released by you" in result
        assert "Released memories are not shown" not in result

    async def test_model_is_opt_in(self, db, tools_db):
        conversation = await make_conversation(db)
        await make_message(db, conversation, content="x", created_at=at(), model="claude-fable-5-1")
        await make_message(db, conversation, content="y", created_at=at(minutes=1))

        plain = await read_memories(native_ctx(), from_="2026-09-01")
        assert "model:" not in plain
        labeled = await read_memories(native_ctx(), from_="2026-09-01", include_model=True)
        assert ", model: claude-fable-5-1)" in labeled
        assert ", model: unrecorded)" in labeled


class TestReadPagination:
    async def test_pages_are_bounded_by_tokens_and_resume_from_cursor(self, db, tools_db):
        conversation = await make_conversation(db)
        # Six messages of 300 tokens each; a 1000-token page holds three
        messages = [
            await make_message(
                db, conversation, content=f"message {i}", created_at=at(minutes=i), token_count=300
            )
            for i in range(6)
        ]

        page1 = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1000)
        assert ids_in_order(page1) == [m.id[:8] for m in messages[:3]]
        assert "6 messages in the span; this page shows 1–3" in page1
        assert "Next page: pass cursor=" in page1
        assert "(3 messages remain)" in page1
        cursor = page1.split('cursor="', 1)[1].split('"', 1)[0]

        page2 = await read_memories(
            native_ctx(), from_="2026-09-01", page_tokens=1000, cursor=cursor
        )
        assert ids_in_order(page2) == [m.id[:8] for m in messages[3:]]
        assert "this page shows 4–6" in page2
        assert page2.rstrip().endswith("End of span.")

        # A cursor past the last row is an explicit end, not an empty page
        last = messages[-1]
        past = memory_service.encode_read_cursor(last.created_at, last.id)
        end = await read_memories(
            native_ctx(), from_="2026-09-01", page_tokens=1000, cursor=past
        )
        assert end.startswith("End of span: no messages after that cursor")

    async def test_oversized_message_is_returned_alone_and_whole(self, db, tools_db):
        conversation = await make_conversation(db)
        small = await make_message(db, conversation, content="small", created_at=at(), token_count=100)
        huge_text = "word " * 5000  # ~6250 tokens by length estimate, token_count left NULL
        huge = await make_message(db, conversation, content=huge_text, created_at=at(minutes=1))
        after = await make_message(db, conversation, content="after", created_at=at(minutes=2), token_count=100)

        page1 = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1000)
        assert ids_in_order(page1) == [small.id[:8]]
        cursor = page1.split('cursor="', 1)[1].split('"', 1)[0]

        page2 = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1000, cursor=cursor)
        assert ids_in_order(page2) == [huge.id[:8]]
        assert huge_text.rstrip() in page2  # never truncated
        cursor = page2.split('cursor="', 1)[1].split('"', 1)[0]

        page3 = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1000, cursor=cursor)
        assert ids_in_order(page3) == [after.id[:8]]

    async def test_page_tokens_is_clamped(self, db, tools_db):
        conversation = await make_conversation(db)
        for i in range(3):
            await make_message(db, conversation, content=f"m{i}", created_at=at(minutes=i), token_count=400)
        # 1 clamps up to the 500 minimum: one 400-token row per page
        result = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1)
        assert len(ids_in_order(result)) == 1
        assert "must be an integer" in await read_memories(
            native_ctx(), from_="2026-09-01", page_tokens="lots"
        )

    async def test_reading_stamps_but_never_tracks_retrieval(self, db, tools_db):
        conversation = await make_conversation(db)
        message = await make_message(db, conversation, content="x", created_at=at())
        ctx = native_ctx()
        with patch.object(memory_service, "update_retrieval_count") as tracker, \
             patch.object(memory_service, "record_memory_link") as linker:
            await read_memories(ctx, from_="2026-09-01")
            tracker.assert_not_called()
            linker.assert_not_called()  # native: no links
        # Native dedup: the tool loop stamps these onto the tool result
        assert ctx.last_query_memory_ids == [message.id]
        assert ctx.turn_query_memory_ids == {message.id}
        row = (await db.execute(
            select(Message).where(Message.id == message.id).execution_options(populate_existing=True)
        )).scalar_one()
        assert row.times_retrieved == 0
        assert row.last_retrieved_at is None


# ============================================================
# Claude Code mode: links recorded once
# ============================================================

class TestClaudeCodeLinks:
    async def test_links_recorded_once_across_repeated_reads(self, db, tools_db):
        room = await make_conversation(db, title="Room", source=ConversationSource.CLAUDE_CODE.value)
        a = await make_message(db, room, content="a", created_at=at())
        b = await make_message(db, room, content="b", created_at=at(minutes=1))
        # b was already linked (retrieved) before the compaction boundary
        db.add(ConversationMemoryLink(
            conversation_id=room.id, message_id=b.id, entity_id=ENTITY, retrieved_at=at(days=-2)
        ))
        await db.commit()

        ctx = claude_code_ctx(room.id)
        await read_memories(ctx, from_="2026-09-01")
        await read_memories(ctx, from_="2026-09-01")
        await neighbor_memories(ctx, a.id)

        links = (await db.execute(
            select(ConversationMemoryLink).where(ConversationMemoryLink.conversation_id == room.id)
        )).scalars().all()
        assert sorted(link.message_id for link in links) == sorted([a.id, b.id])
        # The pre-existing link was bumped past the old boundary, so the
        # just-shown row counts as in view again
        b_link = next(link for link in links if link.message_id == b.id)
        assert b_link.retrieved_at > at(days=-1)
        for link in links:
            assert link.entity_id == ENTITY


# ============================================================
# memory_neighbors
# ============================================================

class TestNeighbors:
    async def make_thread(self, db, count=6, **conversation_kwargs):
        conversation = await make_conversation(db, **conversation_kwargs)
        messages = []
        for i in range(count):
            role = MessageRole.HUMAN if i % 2 == 0 else MessageRole.ASSISTANT
            messages.append(await make_message(
                db, conversation, role=role, content=f"turn {i}", created_at=at(minutes=i)
            ))
        return conversation, messages

    async def test_window_around_a_memory_marks_it(self, db, tools_db):
        conversation, messages = await self.make_thread(db, count=8)
        target = messages[3]
        result = await neighbor_memories(native_ctx(), target.id[:8])

        assert ids_in_order(result) == [m.id[:8] for m in messages[1:6]]
        assert f"--- >> Memory {target.id[:8]} (You said," in result
        assert result.count(">> Memory") == 1
        assert f'Memory {target.id[:8]} in "A day on the porch" (via Here I Am), with 2 messages before and 2 after' in result
        assert "reached the" not in result
        assert f'in_conversation="{conversation.id[:8]}"' in result

    async def test_window_reports_start_and_end(self, db, tools_db):
        _, messages = await self.make_thread(db, count=4)
        first = await neighbor_memories(native_ctx(), messages[0].id, before=3, after=1)
        assert ids_in_order(first) == [m.id[:8] for m in messages[:2]]
        assert "with 0 messages before and 1 after" in first
        assert "reached the start of the conversation" in first
        assert "start and end" not in first

        last = await neighbor_memories(native_ctx(), messages[3].id, before=1, after=5)
        assert ids_in_order(last) == [m.id[:8] for m in messages[2:]]
        assert "reached the end of the conversation" in last

        whole = await neighbor_memories(native_ctx(), messages[1].id, before=10, after=10)
        assert "reached the start and end of the conversation" in whole
        assert "with 1 message before and 2 after" in whole

    async def test_reflection_neighbors_are_the_exchange_it_was_saved_about(self, db, tools_db):
        conversation, messages = await self.make_thread(db, count=2)
        reflection = await make_message(
            db, conversation, role=MessageRole.REFLECTION, content="what mattered",
            created_at=at(minutes=1, seconds=30), speaker_entity_id=ENTITY,
        )
        result = await neighbor_memories(native_ctx(), reflection.id)
        assert ids_in_order(result) == [messages[0].id[:8], messages[1].id[:8], reflection.id[:8]]
        assert f"--- >> Memory {reflection.id[:8]} (You reflected," in result

    async def test_released_neighbors_skipped_unless_asked(self, db, tools_db):
        conversation, messages = await self.make_thread(db, count=3)
        messages[1].memory_status = "released"
        db.add(messages[1])
        await db.commit()

        result = await neighbor_memories(native_ctx(), messages[2].id, before=1, after=0)
        assert ids_in_order(result) == [messages[0].id[:8], messages[2].id[:8]]
        result = await neighbor_memories(
            native_ctx(), messages[2].id, before=1, after=0, include_released=True
        )
        assert ids_in_order(result) == [messages[1].id[:8], messages[2].id[:8]]
        assert "; released before release provenance was recorded)" in result

        # The requested memory itself is shown even when released
        result = await neighbor_memories(native_ctx(), messages[1].id, before=0, after=0)
        assert ids_in_order(result) == [messages[1].id[:8]]

    async def test_id_resolution_errors(self, db, tools_db):
        conversation, messages = await self.make_thread(db, count=2)
        assert "at least 6 characters" in await neighbor_memories(native_ctx(), "abc")
        assert "No memory found" in await neighbor_memories(native_ctx(), "zzzzzz-not-here")

        foreign = await make_conversation(db, entity_id=OTHER_ENTITY)
        theirs = await make_message(db, foreign, content="not yours")
        assert "belongs to another entity" in await neighbor_memories(native_ctx(), theirs.id)

        tool_row = await make_message(db, conversation, role=MessageRole.TOOL_RESULT, content="[]")
        assert "not a memory" in await neighbor_memories(native_ctx(), tool_row.id)

        twin = await make_message(db, conversation, content="twin", message_id=messages[0].id[:6] + "-twin")
        assert "is ambiguous" in await neighbor_memories(native_ctx(), twin.id[:6])

        assert "must be an integer" in await neighbor_memories(native_ctx(), messages[0].id, before="two")

    async def test_neighbors_do_not_track_retrieval(self, db, tools_db):
        _, messages = await self.make_thread(db, count=3)
        ctx = native_ctx()
        with patch.object(memory_service, "update_retrieval_count") as tracker:
            await neighbor_memories(ctx, messages[1].id)
            tracker.assert_not_called()
        assert set(ctx.last_query_memory_ids) == {m.id for m in messages}


# ============================================================
# Reload re-stamping and MCP exposure
# ============================================================

class TestReloadAndMcp:
    def test_reload_regex_reads_both_header_shapes(self):
        text = (
            "--- Memory 0123abcd (You said, 2026-09-01 12:00:00 UTC, via Here I Am, in \"x\") ---\n"
            "hello\n\n"
            "--- >> Memory 89abcdef (Human said, 2026-09-01 12:01:00 UTC, via Here I Am, in \"x\") ---\n"
            "there\n"
        )
        assert _MEMORY_QUERY_RESULT_ID_RE.findall(text) == ["0123abcd", "89abcdef"]
        assert set(MEMORY_RESULT_STAMPING_TOOLS) == {"memory_query", "memory_read", "memory_neighbors"}

    async def test_mcp_read_and_neighbors(self, db, async_client):
        room = await make_conversation(
            db, title="Room", source=ConversationSource.CLAUDE_CODE.value,
            external_session_id="sess-1",
        )
        a = await make_message(db, room, role=MessageRole.HUMAN, content="hello", created_at=at())
        b = await make_message(db, room, content="hi", created_at=at(minutes=1))

        def rpc(name, arguments):
            return {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }

        response = await async_client.post("/mcp", json=rpc("memory_read", {
            "from": "2026-09-01", "tz": "America/New_York", "conversation_id": room.id,
        }))
        text = response.json()["result"]["content"][0]["text"]
        assert response.json()["result"]["isError"] is False
        assert ids_in_order(text) == [a.id[:8], b.id[:8]]

        response = await async_client.post("/mcp", json=rpc("memory_neighbors", {
            "memory_id": b.id[:8], "conversation_id": room.id,
        }))
        text = response.json()["result"]["content"][0]["text"]
        assert f"--- >> Memory {b.id[:8]}" in text

        # Both reads linked the rows exactly once
        links = (await db.execute(
            select(ConversationMemoryLink.message_id)
            .where(ConversationMemoryLink.conversation_id == room.id)
        )).scalars().all()
        assert sorted(links) == sorted([a.id, b.id])

        response = await async_client.post("/mcp", json=rpc("memory_read", {"conversation_id": room.id}))
        assert response.json()["result"]["isError"] is True
        assert "'from' is required" in response.json()["result"]["content"][0]["text"]
