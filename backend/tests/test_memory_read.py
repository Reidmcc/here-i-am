"""
Tests for the archive readers (issue #343): memory_read (a span of the
record, in order, paginated by tokens) and memory_neighbors (one memory
with the messages around it).

Everything the entity has of its archive otherwise arrives by similarity;
these read it by position. Rules under test: span boundaries in a non-UTC
timezone; pagination by token budget including the oversized-single-message
case; reflections interleaved where they were saved; sibling letters and
other entities labeled; released skipped by default and included on
request; rows already in live context rendered as pointers, never
duplicated (the current conversation's own rows, post-compaction only in
Claude Code mode); no retrieval-tracking writes; Claude Code links recorded once;
neighbors at the start and end of a conversation; prefix resolution and the
ambiguity error; archived conversations hidden; the reload-side re-stamping of what a page showed; the
isolated scope (issue #345: a reader whose context is not the
conversation's gets every row in full and leaves no trace in the
conversation's dedup); direction="backward" (issue #351: the newest rows
first across pages, each page still in order, the cursor walking toward
the start — the post-compaction call); and the MCP exposure.

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
from app.services.claude_code_mode import POST_COMPACT_PAGE_TOKENS
from app.services.memory_service import memory_service
from app.services.memory_tools import (
    HARNESS_CHARS_PER_TOKEN,
    HARNESS_PERSIST_BYTES,
    HARNESS_RESULT_CAP_TOKENS,
    IN_CONTEXT_POINTER,
    ISOLATED_SCOPE_NOTE,
    MEMORY_READ_SCHEMA,
    MEMORY_RESULT_STAMPING_TOOLS,
    PAGE_FRAME_TOKENS,
    READ_PAGE_MAX_BYTES,
    READ_PAGE_TOKENS_MAX,
    READ_PAGE_TOKENS_MIN,
    RENDERED_CHARS_PER_TOKEN,
    MemoryToolContext,
    is_isolated_read,
    neighbor_memories,
    read_memories,
    rendered_tokens,
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


def prose(tokens: int, prefix: str = "") -> str:
    """Content that weighs about `tokens` on a page by the rendered-text
    measure (issue #353); the row's header line adds ~40 more. The stored
    token_count plays no part in paging, so the tests size the text."""
    text = "x" * int(tokens * RENDERED_CHARS_PER_TOKEN)
    return f"{prefix} {text}" if prefix else text


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

    async def test_rows_already_in_context_are_pointers_not_duplicates(self, db, tools_db):
        """The page stays whole and in order, but nothing the reader can
        already see is repeated: the current conversation's own rows (all of
        them, natively) and memories retrieved into context from elsewhere
        render as header-only pointers and still carry their ids."""
        here = await make_conversation(db, title="Here")
        own = await make_message(db, here, content="said in this very conversation", created_at=at())
        elsewhere = await make_conversation(db, title="Elsewhere")
        retrieved = await make_message(db, elsewhere, content="pulled in earlier", created_at=at(minutes=1))
        fresh = await make_message(db, elsewhere, content="never seen", created_at=at(minutes=2))
        ctx = native_ctx(conversation_id=here.id)
        ctx.extra_exclude_ids = {retrieved.id}

        result = await read_memories(ctx, from_="2026-09-01")

        assert ids_in_order(result) == [own.id[:8], retrieved.id[:8], fresh.id[:8]]
        assert "said in this very conversation" not in result
        assert "pulled in earlier" not in result
        assert "never seen" in result
        assert result.count(IN_CONTEXT_POINTER) == 2
        assert "2 of them are already in your context and are listed without their content." in result
        # Pointers still count as shown for dedup stamping
        assert set(ctx.last_query_memory_ids) == {own.id, retrieved.id, fresh.id}

    async def test_compacted_claude_code_conversation_reads_its_own_past_in_full(self, db, tools_db):
        """After a compaction only post-boundary rows are in live context;
        the pre-compaction stretch survives as summary alone, so it renders
        verbatim — the use the post-compaction block points at."""
        room = await make_conversation(db, title="Room", source=ConversationSource.CLAUDE_CODE.value)
        before = await make_message(db, room, content="before the compaction", created_at=at())
        after = await make_message(db, room, content="after the compaction", created_at=at(minutes=10))
        ctx = claude_code_ctx(room.id)
        ctx.exclude_conversation_after = at(minutes=5)

        result = await read_memories(ctx, from_="2026-09-01", in_conversation=room.id[:8])

        assert ids_in_order(result) == [before.id[:8], after.id[:8]]
        assert "before the compaction" in result
        assert "after the compaction" not in result
        assert result.count(IN_CONTEXT_POINTER) == 1

        # Never compacted: everything in it is in live context
        ctx.exclude_conversation_after = None
        result = await read_memories(ctx, from_="2026-09-01", in_conversation=room.id[:8])
        assert result.count(IN_CONTEXT_POINTER) == 2
        assert "before the compaction" not in result

    async def test_pointer_rows_weigh_little_on_the_page(self, db, tools_db):
        """A page of pointers is not charged the content it doesn't carry."""
        here = await make_conversation(db, title="Here")
        for i in range(5):
            await make_message(db, here, content=prose(5000, f"m{i}"), created_at=at(minutes=i))
        ctx = native_ctx(conversation_id=here.id)
        result = await read_memories(ctx, from_="2026-09-01", page_tokens=1400)
        assert len(ids_in_order(result)) == 5
        assert result.rstrip().endswith("End of span.")


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
        # Six messages of ~340 tokens each as rendered; a 1400-token page
        # (250 of it the frame) holds three
        messages = [
            await make_message(
                db, conversation, content=prose(300, f"message {i}"), created_at=at(minutes=i)
            )
            for i in range(6)
        ]

        page1 = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1400)
        assert ids_in_order(page1) == [m.id[:8] for m in messages[:3]]
        assert "6 messages in the span; this page shows 1–3" in page1
        assert "Next page: pass cursor=" in page1
        assert "(3 messages remain)" in page1
        cursor = page1.split('cursor="', 1)[1].split('"', 1)[0]

        page2 = await read_memories(
            native_ctx(), from_="2026-09-01", page_tokens=1400, cursor=cursor
        )
        assert ids_in_order(page2) == [m.id[:8] for m in messages[3:]]
        assert "this page shows 4–6" in page2
        assert page2.rstrip().endswith("End of span.")

        # A cursor past the last row is an explicit end, not an empty page
        last = messages[-1]
        past = memory_service.encode_read_cursor(last.created_at, last.id)
        end = await read_memories(
            native_ctx(), from_="2026-09-01", page_tokens=1400, cursor=past
        )
        assert end.startswith("End of span: no messages after that cursor")

    async def test_oversized_message_is_returned_alone_and_whole(self, db, tools_db):
        conversation = await make_conversation(db)
        small = await make_message(db, conversation, content="small", created_at=at(), token_count=100)
        huge_text = "word " * 5000  # ~8,900 tokens as rendered
        huge = await make_message(db, conversation, content=huge_text, created_at=at(minutes=1))
        after = await make_message(db, conversation, content="after", created_at=at(minutes=2), token_count=100)

        page1 = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1400)
        assert ids_in_order(page1) == [small.id[:8]]
        cursor = page1.split('cursor="', 1)[1].split('"', 1)[0]

        page2 = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1400, cursor=cursor)
        assert ids_in_order(page2) == [huge.id[:8]]
        assert huge_text.rstrip() in page2  # never truncated
        cursor = page2.split('cursor="', 1)[1].split('"', 1)[0]

        page3 = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1400, cursor=cursor)
        assert ids_in_order(page3) == [after.id[:8]]

    async def test_page_tokens_is_clamped(self, db, tools_db):
        conversation = await make_conversation(db)
        for i in range(3):
            await make_message(db, conversation, content=prose(400, f"m{i}"), created_at=at(minutes=i))
        # 1 clamps up to the 500 minimum (250 for rows): one ~440-token row per page
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

    async def test_in_context_target_is_a_pointer_with_full_neighbors(self, db, tools_db):
        """The usual entry point is a memory marker already in context: the
        target renders as a pointer and the messages around it in full."""
        _, messages = await self.make_thread(db, count=3)
        ctx = native_ctx()
        ctx.extra_exclude_ids = {messages[1].id}
        result = await neighbor_memories(ctx, messages[1].id, before=1, after=1)
        assert ids_in_order(result) == [m.id[:8] for m in messages]
        assert f"--- >> Memory {messages[1].id[:8]}" in result
        assert "turn 1" not in result
        assert "turn 0" in result and "turn 2" in result
        assert result.count(IN_CONTEXT_POINTER) == 1
        assert "1 of these are already in your context" in result

    async def test_neighbors_do_not_track_retrieval(self, db, tools_db):
        _, messages = await self.make_thread(db, count=3)
        ctx = native_ctx()
        with patch.object(memory_service, "update_retrieval_count") as tracker:
            await neighbor_memories(ctx, messages[1].id)
            tracker.assert_not_called()
        assert set(ctx.last_query_memory_ids) == {m.id for m in messages}


# ============================================================
# scope="isolated" (issue #345)
# ============================================================

class TestIsolatedScope:
    """A subagent shares its parent's conversation_id, so under the default
    scope it inherits the parent's in-view set as pointers and its own reads
    land in the parent's dedup. scope="isolated" belongs to no in-view set:
    every row in full, nothing recorded."""

    async def test_isolated_read_returns_full_content_and_records_nothing(self, db, tools_db):
        here = await make_conversation(db, title="Here")
        own = await make_message(db, here, content="said in this very conversation", created_at=at())
        elsewhere = await make_conversation(db, title="Elsewhere")
        retrieved = await make_message(db, elsewhere, content="pulled in earlier", created_at=at(minutes=1))
        fresh = await make_message(db, elsewhere, content="never seen", created_at=at(minutes=2))
        ctx = native_ctx(conversation_id=here.id)
        ctx.extra_exclude_ids = {retrieved.id}

        result = await read_memories(ctx, from_="2026-09-01", scope="isolated")

        assert ids_in_order(result) == [own.id[:8], retrieved.id[:8], fresh.id[:8]]
        assert "said in this very conversation" in result
        assert "pulled in earlier" in result
        assert "never seen" in result
        assert IN_CONTEXT_POINTER not in result
        assert "of them are already in your context" not in result
        assert ISOLATED_SCOPE_NOTE.strip() in result
        # Nothing recorded: no stamping for the tool loop, no turn accumulator
        assert ctx.last_query_memory_ids == []
        assert ctx.turn_query_memory_ids == set()

        # The default scope on the same context is unchanged
        result = await read_memories(ctx, from_="2026-09-01")
        assert result.count(IN_CONTEXT_POINTER) == 2
        assert ISOLATED_SCOPE_NOTE.strip() not in result

    async def test_isolated_read_leaves_no_trace_in_conversation_dedup(self, db, tools_db):
        """Ordering proof: an isolated read followed by a conversation-scope
        read of the same span returns exactly what the conversation-scope
        read returns on an identical room where the isolated read never
        happened; and the isolated read writes no links."""
        async def build_room(title, day):
            # Each room on its own day, so the two spans don't overlap
            room = await make_conversation(
                db, title=title, source=ConversationSource.CLAUDE_CODE.value,
                last_compacted_at=at(days=day, minutes=5),
            )
            await make_message(db, room, content="before the boundary", created_at=at(days=day))
            await make_message(db, room, content="after the boundary", created_at=at(days=day, minutes=10))
            elsewhere = await make_conversation(db, title=f"{title} elsewhere")
            linked = await make_message(
                db, elsewhere, content="linked earlier", created_at=at(days=day, minutes=1)
            )
            await make_message(db, elsewhere, content="unlinked", created_at=at(days=day, minutes=2))
            db.add(ConversationMemoryLink(
                conversation_id=room.id, message_id=linked.id, entity_id=ENTITY,
                retrieved_at=at(days=day, minutes=6),
            ))
            await db.commit()
            return room

        async def fresh_ctx(room):
            # As the MCP endpoint builds it: the post-boundary link set is
            # the exclusion set
            links = await memory_service.get_retrieved_ids_for_conversation(
                room.id, db, entity_id=ENTITY, linked_after=room.last_compacted_at
            )
            ctx = claude_code_ctx(room.id)
            ctx.extra_exclude_ids = links
            ctx.exclude_conversation_after = room.last_compacted_at
            return ctx

        async def link_contents(room):
            rows = (await db.execute(
                select(Message.content)
                .join(ConversationMemoryLink, ConversationMemoryLink.message_id == Message.id)
                .where(ConversationMemoryLink.conversation_id == room.id)
            )).scalars().all()
            return sorted(rows)

        def shape(text):
            # Which rows were pointers, by content order, independent of ids
            lines = text.split("\n")
            return [
                lines[i + 1] == IN_CONTEXT_POINTER
                for i, line in enumerate(lines) if line.startswith("--- Memory ")
            ]

        control_room = await build_room("Control", day=0)
        control = await read_memories(await fresh_ctx(control_room), from_="2026-09-01")
        control_links = await link_contents(control_room)

        room = await build_room("Treatment", day=1)
        links_before = await link_contents(room)
        isolated = await read_memories(await fresh_ctx(room), from_="2026-09-02", scope="isolated")
        assert IN_CONTEXT_POINTER not in isolated
        assert "after the boundary" in isolated and "linked earlier" in isolated
        assert await link_contents(room) == links_before  # nothing written

        after = await read_memories(await fresh_ctx(room), from_="2026-09-02")
        assert shape(after) == shape(control)
        assert after.count(IN_CONTEXT_POINTER) == control.count(IN_CONTEXT_POINTER) == 2
        assert await link_contents(room) == control_links

    async def test_isolated_read_returns_post_compaction_rows_in_full(self, db, tools_db):
        """The conversation's own post-boundary rows are pointers under the
        default scope (they are in the parent's live context) and full text
        under isolated (they were never in the subagent's)."""
        room = await make_conversation(db, title="Room", source=ConversationSource.CLAUDE_CODE.value)
        before = await make_message(db, room, content="before the compaction", created_at=at())
        after = await make_message(db, room, content="after the compaction", created_at=at(minutes=10))
        ctx = claude_code_ctx(room.id)
        ctx.exclude_conversation_after = at(minutes=5)

        result = await read_memories(ctx, from_="2026-09-01", in_conversation=room.id[:8], scope="isolated")
        assert ids_in_order(result) == [before.id[:8], after.id[:8]]
        assert "before the compaction" in result and "after the compaction" in result
        assert IN_CONTEXT_POINTER not in result

        result = await read_memories(ctx, from_="2026-09-01", in_conversation=room.id[:8])
        assert "after the compaction" not in result
        assert result.count(IN_CONTEXT_POINTER) == 1

        # Never compacted: still everything in full under isolated
        ctx.exclude_conversation_after = None
        result = await read_memories(ctx, from_="2026-09-01", in_conversation=room.id[:8], scope="isolated")
        assert IN_CONTEXT_POINTER not in result
        assert "before the compaction" in result and "after the compaction" in result

    async def test_isolated_neighbors_target_in_full_and_unrecorded(self, db, tools_db):
        room = await make_conversation(db, title="Room", source=ConversationSource.CLAUDE_CODE.value)
        messages = [
            await make_message(db, room, content=f"turn {i}", created_at=at(minutes=i)) for i in range(3)
        ]
        ctx = claude_code_ctx(room.id)
        ctx.extra_exclude_ids = {messages[1].id}

        result = await neighbor_memories(ctx, messages[1].id, before=1, after=1, scope="isolated")
        assert ids_in_order(result) == [m.id[:8] for m in messages]
        assert f"--- >> Memory {messages[1].id[:8]}" in result
        assert "turn 0" in result and "turn 1" in result and "turn 2" in result
        assert IN_CONTEXT_POINTER not in result
        assert ISOLATED_SCOPE_NOTE.strip() in result
        assert ctx.last_query_memory_ids == []
        assert ctx.turn_query_memory_ids == set()
        links = (await db.execute(
            select(ConversationMemoryLink).where(ConversationMemoryLink.conversation_id == room.id)
        )).scalars().all()
        assert links == []

        # Default scope: the target is a pointer and the window is linked
        result = await neighbor_memories(ctx, messages[1].id, before=1, after=1)
        assert result.count(IN_CONTEXT_POINTER) == 3  # own rows, never compacted
        links = (await db.execute(
            select(ConversationMemoryLink.message_id)
            .where(ConversationMemoryLink.conversation_id == room.id)
        )).scalars().all()
        assert sorted(links) == sorted(m.id for m in messages)

    async def test_visibility_rules_hold_under_isolated(self, db, tools_db):
        """Isolation is context bookkeeping, not visibility: released rows
        stay hidden unless asked, archived conversations stay hidden,
        and no retrieval tracking happens."""
        room = await make_conversation(db, title="Room")
        shown = await make_message(db, room, content="shown", created_at=at())
        await make_message(
            db, room, content="let go", created_at=at(minutes=1),
            memory_status="released", status_set_by="entity", status_set_at=at(days=1),
        )
        archived = await make_conversation(db, title="Archived", is_archived=True)
        await make_message(db, archived, content="withdrawn", created_at=at(minutes=2))
        ctx = native_ctx(conversation_id=room.id)

        with patch.object(memory_service, "update_retrieval_count") as tracker:
            result = await read_memories(ctx, from_="2026-09-01", scope="isolated")
            tracker.assert_not_called()
        assert ids_in_order(result) == [shown.id[:8]]
        assert "let go" not in result
        assert "withdrawn" not in result
        result = await read_memories(ctx, from_="2026-09-01", scope="isolated", include_released=True)
        assert len(ids_in_order(result)) == 2

    async def test_unknown_scope_is_an_error(self, db, tools_db):
        room = await make_conversation(db, title="Room")
        message = await make_message(db, room, content="x", created_at=at())
        ctx = native_ctx(conversation_id=room.id)
        assert "Unknown scope 'fresh'" in await read_memories(ctx, from_="2026-09-01", scope="fresh")
        assert "Unknown scope 'fresh'" in await neighbor_memories(ctx, message.id, scope="fresh")
        # Case and whitespace are forgiven; the default is the conversation's
        assert IN_CONTEXT_POINTER not in await read_memories(ctx, from_="2026-09-01", scope=" Isolated ")
        assert IN_CONTEXT_POINTER in await read_memories(ctx, from_="2026-09-01", scope="conversation")

    def test_is_isolated_read_reads_tool_input(self):
        assert is_isolated_read({"from": "2026-09-01", "scope": "isolated"})
        assert is_isolated_read({"memory_id": "abcdef", "scope": " ISOLATED "})
        assert not is_isolated_read({"from": "2026-09-01"})
        assert not is_isolated_read({"from": "2026-09-01", "scope": "conversation"})
        assert not is_isolated_read(None)
        assert not is_isolated_read("scope=isolated")


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
        assert set(MEMORY_RESULT_STAMPING_TOOLS) == {
            "memory_query", "memory_read", "memory_neighbors", "memory_find",
        }

    async def test_mcp_read_and_neighbors(self, db, async_client):
        room = await make_conversation(
            db, title="Room", source=ConversationSource.CLAUDE_CODE.value,
            external_session_id="sess-1",
            # Compacted after both rows: they survive only as summary, so the
            # readers show them in full rather than as pointers
            last_compacted_at=at(minutes=5),
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
        assert "hello" in text and IN_CONTEXT_POINTER not in text

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

    async def test_mcp_isolated_scope(self, db, async_client):
        """Over MCP, a never-compacted room's own rows are pointers by default
        and full text under scope="isolated", which also links nothing."""
        room = await make_conversation(
            db, title="Room", source=ConversationSource.CLAUDE_CODE.value,
            external_session_id="sess-2",
        )
        a = await make_message(db, room, role=MessageRole.HUMAN, content="hello there", created_at=at())
        b = await make_message(db, room, content="hi yourself", created_at=at(minutes=1))

        def rpc(name, arguments):
            return {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }

        async def links():
            return sorted((await db.execute(
                select(ConversationMemoryLink.message_id)
                .where(ConversationMemoryLink.conversation_id == room.id)
            )).scalars().all())

        response = await async_client.post("/mcp", json=rpc("memory_read", {
            "from": "2026-09-01", "conversation_id": room.id, "scope": "isolated",
        }))
        text = response.json()["result"]["content"][0]["text"]
        assert response.json()["result"]["isError"] is False
        assert ids_in_order(text) == [a.id[:8], b.id[:8]]
        assert "hello there" in text and "hi yourself" in text
        assert IN_CONTEXT_POINTER not in text
        assert await links() == []

        response = await async_client.post("/mcp", json=rpc("memory_neighbors", {
            "memory_id": b.id[:8], "conversation_id": room.id, "scope": "isolated",
        }))
        text = response.json()["result"]["content"][0]["text"]
        assert f"--- >> Memory {b.id[:8]}" in text
        assert "hi yourself" in text and IN_CONTEXT_POINTER not in text
        assert await links() == []

        response = await async_client.post("/mcp", json=rpc("memory_read", {
            "from": "2026-09-01", "conversation_id": room.id,
        }))
        text = response.json()["result"]["content"][0]["text"]
        assert text.count(IN_CONTEXT_POINTER) == 2
        assert await links() == sorted([a.id, b.id])

        # The schema advertises the parameter on both readers
        response = await async_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
        for name in ("memory_read", "memory_neighbors"):
            scope = tools[name]["inputSchema"]["properties"]["scope"]
            assert scope["enum"] == ["conversation", "isolated"]
            assert scope["default"] == "conversation"
        assert "scope" not in tools["memory_query"]["inputSchema"]["properties"]


# ============================================================
# memory_read: direction="backward" (issue #351)
# ============================================================

class TestBackward:
    """A backward page starts at `to` (default now), takes the newest rows
    not yet shown, renders them oldest-first so the page still reads like
    the archive, and its cursor walks further back toward `from` (default
    the start of the archive) — the page a freshly compacted session wants,
    whatever its dates."""

    async def test_backward_page_selects_newest_and_renders_oldest_first(self, db, tools_db):
        conversation = await make_conversation(db)
        messages = [
            await make_message(
                db, conversation, content=prose(300, f"message {i}"), created_at=at(minutes=i)
            )
            for i in range(6)
        ]

        page1 = await read_memories(
            native_ctx(), from_="2026-09-01", direction="backward", page_tokens=1400
        )
        # The newest three, in archive order within the page
        assert ids_in_order(page1) == [m.id[:8] for m in messages[3:]]
        assert "6 messages in the span; read backward from its end, this page shows 4–6, in order." in page1
        assert "Next page (earlier): pass cursor=" in page1
        assert "(3 earlier messages remain)" in page1
        cursor = page1.split('cursor="', 1)[1].split('"', 1)[0]
        # The cursor is the page's older edge, tagged with its direction
        # and the page it came from
        assert cursor == memory_service.encode_read_cursor(
            messages[3].created_at, messages[3].id, backward=True, page=1
        )
        assert cursor.endswith("|backward|page=1")

        page2 = await read_memories(
            native_ctx(), from_="2026-09-01", direction="backward", page_tokens=1400, cursor=cursor
        )
        assert ids_in_order(page2) == [m.id[:8] for m in messages[:3]]
        assert "this page shows 1–3, in order" in page2
        assert page2.rstrip().endswith("Start of span: nothing earlier.")

        # A cursor at the very first row is an explicit start, not an empty page
        past = memory_service.encode_read_cursor(
            messages[0].created_at, messages[0].id, backward=True
        )
        start = await read_memories(
            native_ctx(), from_="2026-09-01", direction="backward", cursor=past
        )
        assert start.startswith("Start of span: no messages before that cursor between")
        assert "(6 in the span)" in start

    async def test_backward_needs_no_from_and_ends_at_now(self, db, tools_db):
        conversation = await make_conversation(db)
        old = await make_message(db, conversation, content="long ago", created_at=at(days=-400))
        recent = await make_message(db, conversation, content="lately", created_at=at())

        result = await read_memories(native_ctx(), direction="backward")
        assert ids_in_order(result) == [old.id[:8], recent.id[:8]]
        assert "Your archive, the start of your archive to now:" in result
        assert result.rstrip().endswith("Start of your archive: nothing earlier.")

        # Confined to one conversation with no 'from', the stop is the
        # conversation's own beginning, and the last page says so
        result = await read_memories(
            native_ctx(), direction="backward", in_conversation=conversation.id[:8]
        )
        assert result.rstrip().endswith("Start of the conversation: nothing earlier.")
        # ...and so does an empty page past it, or past the archive's start
        past = memory_service.encode_read_cursor(old.created_at, old.id, backward=True)
        result = await read_memories(
            native_ctx(), direction="backward", in_conversation=conversation.id[:8], cursor=past
        )
        assert result.startswith("Start of the conversation: no messages before that cursor")
        result = await read_memories(native_ctx(), direction="backward", cursor=past)
        assert result.startswith("Start of your archive: no messages before that cursor")

        # 'to' alone bounds the start of the read; 'from' stays the stop
        # 7 AM Eastern is 11:00 UTC, an hour before `recent`
        result = await read_memories(
            native_ctx(), direction="backward", to="2026-09-01T07:00", tz="America/New_York"
        )
        assert ids_in_order(result) == [old.id[:8]]
        assert "[America/New_York; UTC the start to 2026-09-01 11:00:00]" in result

        # Forward still requires 'from', and the error points at the alternative
        error = await read_memories(native_ctx())
        assert error.startswith("Error: 'from' is required")
        assert 'direction="backward"' in error

    async def test_backward_from_a_boundary_reads_the_stretch_just_before_it(self, db, tools_db):
        """The compaction use: 'to' is the boundary, the first page is the
        talk right before it, and older pages follow on request."""
        conversation = await make_conversation(db)
        messages = [
            await make_message(
                db, conversation, content=prose(300, f"message {i}"), created_at=at(minutes=i)
            )
            for i in range(6)
        ]
        boundary = at(minutes=3, seconds=30)

        page1 = await read_memories(
            native_ctx(), direction="backward", to=boundary.isoformat(), page_tokens=1400
        )
        # Rows after the boundary are out; the three just before it are in
        assert ids_in_order(page1) == [m.id[:8] for m in messages[1:4]]
        assert "4 messages in the span; read backward from its end, this page shows 2–4" in page1
        assert "the start of your archive to 2026-09-01 12:03:30 UTC" in page1
        cursor = page1.split('cursor="', 1)[1].split('"', 1)[0]
        page2 = await read_memories(
            native_ctx(), direction="backward", to=boundary.isoformat(), page_tokens=1400, cursor=cursor
        )
        assert ids_in_order(page2) == [messages[0].id[:8]]
        assert page2.rstrip().endswith("Start of your archive: nothing earlier.")

    async def test_same_span_forward_and_backward_yield_the_same_rows(self, db, tools_db):
        porch = await make_conversation(db, title="Porch")
        workshop = await make_conversation(db, title="Workshop")
        expected = []
        for i in range(7):
            room = porch if i % 2 else workshop
            expected.append(await make_message(
                db, room, content=prose(300, f"m{i}"), created_at=at(minutes=i)
            ))

        async def collect(direction):
            pages, cursor = [], None
            while True:
                page = await read_memories(
                    native_ctx(), from_="2026-09-01", direction=direction,
                    page_tokens=1070, cursor=cursor,
                )
                pages.append(ids_in_order(page))
                if 'cursor="' not in page:
                    return pages
                cursor = page.split('cursor="', 1)[1].split('"', 1)[0]

        forward = await collect("forward")
        backward = await collect("backward")
        assert [i for page in forward for i in page] == [m.id[:8] for m in expected]
        # Backward walks the same rows from the other end; each page is
        # itself in archive order, so reversing the page order restores it
        assert [i for page in reversed(backward) for i in page] == [m.id[:8] for m in expected]
        # Seven rows, two per page
        assert len(backward) == len(forward) == 4

    async def test_oversized_message_backward_comes_alone_and_whole(self, db, tools_db):
        conversation = await make_conversation(db)
        small = await make_message(db, conversation, content="small", created_at=at(), token_count=100)
        huge_text = "word " * 5000
        huge = await make_message(db, conversation, content=huge_text, created_at=at(minutes=1))
        after = await make_message(db, conversation, content="after", created_at=at(minutes=2), token_count=100)

        page1 = await read_memories(native_ctx(), from_="2026-09-01", direction="backward", page_tokens=1400)
        assert ids_in_order(page1) == [after.id[:8]]
        cursor = page1.split('cursor="', 1)[1].split('"', 1)[0]
        page2 = await read_memories(
            native_ctx(), from_="2026-09-01", direction="backward", page_tokens=1400, cursor=cursor
        )
        assert ids_in_order(page2) == [huge.id[:8]]
        assert huge_text.rstrip() in page2
        cursor = page2.split('cursor="', 1)[1].split('"', 1)[0]
        page3 = await read_memories(
            native_ctx(), from_="2026-09-01", direction="backward", page_tokens=1400, cursor=cursor
        )
        assert ids_in_order(page3) == [small.id[:8]]
        assert page3.rstrip().endswith("Start of span: nothing earlier.")

    async def test_pointers_and_isolated_scope_hold_in_both_directions(self, db, tools_db):
        here = await make_conversation(db, title="Here")
        own = await make_message(db, here, content="said in this very conversation", created_at=at())
        elsewhere = await make_conversation(db, title="Elsewhere")
        retrieved = await make_message(db, elsewhere, content="pulled in earlier", created_at=at(minutes=1))
        fresh = await make_message(db, elsewhere, content="never seen", created_at=at(minutes=2))

        for direction in ("forward", "backward"):
            ctx = native_ctx(conversation_id=here.id)
            ctx.extra_exclude_ids = {retrieved.id}
            with patch.object(memory_service, "update_retrieval_count") as tracker:
                result = await read_memories(ctx, from_="2026-09-01", direction=direction)
                tracker.assert_not_called()
            assert ids_in_order(result) == [own.id[:8], retrieved.id[:8], fresh.id[:8]]
            assert "said in this very conversation" not in result
            assert "pulled in earlier" not in result
            assert "never seen" in result
            assert result.count(IN_CONTEXT_POINTER) == 2
            assert set(ctx.last_query_memory_ids) == {own.id, retrieved.id, fresh.id}

            ctx = native_ctx(conversation_id=here.id)
            ctx.extra_exclude_ids = {retrieved.id}
            result = await read_memories(ctx, from_="2026-09-01", direction=direction, scope="isolated")
            assert ids_in_order(result) == [own.id[:8], retrieved.id[:8], fresh.id[:8]]
            assert IN_CONTEXT_POINTER not in result
            assert "pulled in earlier" in result
            assert ISOLATED_SCOPE_NOTE.strip() in result
            assert ctx.last_query_memory_ids == []
            assert ctx.turn_query_memory_ids == set()

    async def test_cursor_is_only_resumed_in_its_own_direction(self, db, tools_db):
        conversation = await make_conversation(db)
        for i in range(4):
            await make_message(db, conversation, content=prose(300, f"m{i}"), created_at=at(minutes=i))

        forward = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1070)
        forward_cursor = forward.split('cursor="', 1)[1].split('"', 1)[0]
        backward = await read_memories(
            native_ctx(), from_="2026-09-01", direction="backward", page_tokens=1070
        )
        backward_cursor = backward.split('cursor="', 1)[1].split('"', 1)[0]

        wrong = await read_memories(
            native_ctx(), from_="2026-09-01", direction="backward", cursor=forward_cursor
        )
        assert wrong.startswith("Error: That cursor came from a memory_read page read direction=\"forward\"")
        wrong = await read_memories(native_ctx(), from_="2026-09-01", cursor=backward_cursor)
        assert wrong.startswith("Error: That cursor came from a memory_read page read direction=\"backward\"")
        assert memory_service.decode_read_cursor("2026-09-01T12:00:00|abc|sideways") is None
        # A hand-built cursor with no page tag is page 1
        assert memory_service.decode_read_cursor("2026-09-01T12:00:00|abc") == (
            datetime(2026, 9, 1, 12, 0), "abc", False, 1
        )
        decoded = memory_service.decode_read_cursor("2026-09-01T12:00:00|abc|backward|page=4")
        assert decoded == (datetime(2026, 9, 1, 12, 0), "abc", True, 4)
        assert (decoded.backward, decoded.page) == (True, 4)
        assert "Unknown direction 'sideways'" in await read_memories(
            native_ctx(), from_="2026-09-01", direction="sideways"
        )
        assert "direction" in MEMORY_READ_SCHEMA["properties"]
        assert "max_pages" in MEMORY_READ_SCHEMA["properties"]
        assert "from" not in MEMORY_READ_SCHEMA.get("required", [])

    async def test_max_pages_caps_the_walk_but_never_refuses_a_page(self, db, tools_db):
        """The page that reaches the cap still gives its cursor, under a cap
        note instead of the next-page line; passing it back with a higher
        cap continues; a lower cap never refuses the page asked for."""
        conversation = await make_conversation(db)
        messages = [
            await make_message(
                db, conversation, content=prose(300, f"message {i}"), created_at=at(minutes=i)
            )
            for i in range(6)
        ]
        kwargs = dict(from_="2026-09-01", direction="backward", page_tokens=1070)

        page1 = await read_memories(native_ctx(), max_pages=2, **kwargs)
        assert ids_in_order(page1) == [m.id[:8] for m in messages[4:]]
        assert "Next page (earlier): pass cursor=" in page1
        cursor = page1.split('cursor="', 1)[1].split('"', 1)[0]
        assert cursor.endswith("|backward|page=1")

        page2 = await read_memories(native_ctx(), max_pages=2, cursor=cursor, **kwargs)
        assert ids_in_order(page2) == [m.id[:8] for m in messages[2:4]]
        assert "Next page" not in page2
        assert "Page cap reached (max_pages=2; this was page 2): 2 earlier messages remain unread." in page2
        assert "with the same span, direction, and filters and a higher max_pages" in page2
        cursor = page2.split('cursor="', 1)[1].split('"', 1)[0]
        assert cursor.endswith("|backward|page=2")

        # A higher cap continues from the same cursor; the walk then ends
        page3 = await read_memories(native_ctx(), max_pages=3, cursor=cursor, **kwargs)
        assert ids_in_order(page3) == [m.id[:8] for m in messages[:2]]
        assert page3.rstrip().endswith("Start of span: nothing earlier.")
        # The same cursor with the old cap is still served (the cap is a
        # note on the walk, never a refusal)
        assert ids_in_order(await read_memories(native_ctx(), max_pages=2, cursor=cursor, **kwargs)) == [
            m.id[:8] for m in messages[:2]
        ]

        # Forward too, and the cap counts from page 1
        forward = await read_memories(native_ctx(), from_="2026-09-01", page_tokens=1070, max_pages=1)
        assert "Page cap reached (max_pages=1; this was page 1): 4 messages remain unread." in forward
        assert "earlier" not in forward.split("Page cap reached", 1)[1]

        assert "must be an integer" in await read_memories(native_ctx(), from_="2026-09-01", max_pages="many")
        assert "at least 1" in await read_memories(native_ctx(), from_="2026-09-01", max_pages=0)

    async def test_mcp_backward_from_the_compaction_boundary(self, db, async_client):
        """The post-compaction block's call, end to end over MCP: read
        backward from last_compacted_at within this conversation. The rows
        after the boundary are outside the span; the ones before it survive
        only as summary, so they come back in full; the last page says it
        reached the conversation's start; and the read is linked once."""
        room = await make_conversation(
            db, title="Room", source=ConversationSource.CLAUDE_CODE.value,
            external_session_id="sess-back", last_compacted_at=at(minutes=5),
        )
        first = await make_message(db, room, role=MessageRole.HUMAN, content="the first thing", created_at=at())
        last_before = await make_message(db, room, content="just before the boundary", created_at=at(minutes=4))
        await make_message(db, room, role=MessageRole.HUMAN, content="after the boundary", created_at=at(minutes=6))

        response = await async_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "memory_read", "arguments": {
                "direction": "backward",
                "to": room.last_compacted_at.strftime("%Y-%m-%dT%H:%M:%S"),
                "in_conversation": room.id,
                "conversation_id": room.id,
            }},
        })
        body = response.json()["result"]
        assert body["isError"] is False
        text = body["content"][0]["text"]
        assert ids_in_order(text) == [first.id[:8], last_before.id[:8]]
        assert "just before the boundary" in text
        assert "after the boundary" not in text
        assert IN_CONTEXT_POINTER not in text
        assert "read backward from its end" in text
        assert text.rstrip().endswith("Start of the conversation: nothing earlier.")
        links = (await db.execute(
            select(ConversationMemoryLink.message_id)
            .where(ConversationMemoryLink.conversation_id == room.id)
        )).scalars().all()
        assert sorted(links) == sorted([first.id, last_before.id])

    async def test_mcp_in_conversation_without_conversation_id_names_the_cause(
        self, db, async_client
    ):
        """in_conversation chooses what to read; conversation_id says who is
        reading. Without the latter an MCP call runs as the default entity,
        so an in_conversation of another entity's session does not resolve —
        and the error says why, instead of a bare "no conversation of yours"."""
        room = await make_conversation(
            db, entity_id=OTHER_ENTITY, title="Other's room",
            source=ConversationSource.CLAUDE_CODE.value, external_session_id="sess-other",
        )
        response = await async_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "memory_read", "arguments": {
                "direction": "backward", "in_conversation": room.id,
            }},
        })
        text = response.json()["result"]["content"][0]["text"]
        assert text.startswith(f"Error: No conversation of yours found with ID '{room.id}'.")
        assert "This call carried no conversation_id" in text
        assert "default entity (Test Entity)" in text
        assert "in_conversation only chooses what to read" in text

        # With conversation_id the same filter resolves, and the hint is
        # absent when a filter fails for its own reasons
        response = await async_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "memory_read", "arguments": {
                "direction": "backward", "in_conversation": room.id,
                "conversation_id": room.id,
            }},
        })
        text = response.json()["result"]["content"][0]["text"]
        assert not text.startswith("Error:")
        response = await async_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "memory_read", "arguments": {
                "direction": "backward", "in_conversation": "ffffff",
                "conversation_id": room.id,
            }},
        })
        text = response.json()["result"]["content"][0]["text"]
        assert text.startswith("Error: No conversation of yours found with ID 'ffffff'.")
        assert "conversation_id" not in text


# ============================================================
# memory_read / memory_find: the page budget (issue #353)
# ============================================================

class TestPageBudget:
    """Issue #353: the budget is measured on the page as rendered, in the
    harness's units, so a page asked for at the maximum lands in Claude
    Code's context whole instead of spilling to a file that costs two or
    three Read calls to get back."""

    def test_rendered_measure(self):
        assert rendered_tokens("") == 1
        assert rendered_tokens("x" * 28) == 10
        assert rendered_tokens("x" * 29) == 11
        # Bytes, not characters: the persist line is in bytes (an em dash is three)
        assert rendered_tokens("\u2014" * 28) == rendered_tokens("x" * 84)
        assert PAGE_FRAME_TOKENS < READ_PAGE_TOKENS_MIN

    def test_the_maximum_page_and_the_block_page_clear_both_harness_limits(self):
        """The two limits are measured constants (memory_tools, 2026-09-16);
        a re-measurement is a constant edit this test checks. The maximum
        page must clear the 50 KB persist line by a tenth and the 25k-token
        cap by a third at the harness's own ratio; the post-compaction
        block's page, walked 25 times after a compaction, by more."""
        assert RENDERED_CHARS_PER_TOKEN == HARNESS_CHARS_PER_TOKEN
        assert READ_PAGE_MAX_BYTES == int(READ_PAGE_TOKENS_MAX * RENDERED_CHARS_PER_TOKEN)
        assert READ_PAGE_MAX_BYTES <= HARNESS_PERSIST_BYTES * 0.9
        assert READ_PAGE_MAX_BYTES / HARNESS_CHARS_PER_TOKEN <= HARNESS_RESULT_CAP_TOKENS * 2 / 3
        block_bytes = POST_COMPACT_PAGE_TOKENS * RENDERED_CHARS_PER_TOKEN
        assert block_bytes <= HARNESS_PERSIST_BYTES * 0.7
        assert POST_COMPACT_PAGE_TOKENS <= READ_PAGE_TOKENS_MAX

    async def test_a_page_at_the_maximum_stays_within_the_byte_ceiling(self, db, tools_db):
        """Rows with long content and a light stored token_count (the old
        measure, which would have kept filling): every page — headers,
        pointers, and footer included — renders within READ_PAGE_MAX_BYTES,
        under the harness's persist line, and the pages fill rather than
        shrink."""
        here = await make_conversation(db, title="Here")
        elsewhere = await make_conversation(db, title="Elsewhere")
        by_prefix = {}
        for i in range(60):
            content = f"turn {i} " + "prose " * (300 + (i % 7) * 250)  # 1.8k–10.8k characters
            room = here if i % 5 == 0 else elsewhere
            message = await make_message(
                db, room, content=content, created_at=at(minutes=i), token_count=len(content) // 5
            )
            by_prefix[message.id[:8]] = message
        ctx = native_ctx(conversation_id=here.id)  # Here's own rows render as pointers
        pages, cursor = [], None
        while True:
            page = await read_memories(
                ctx, from_="2026-09-01", page_tokens=READ_PAGE_TOKENS_MAX, cursor=cursor
            )
            pages.append(page)
            if 'cursor="' not in page:
                break
            cursor = page.split('cursor="', 1)[1].split('"', 1)[0]
        assert len(pages) > 1
        sizes = [len(page.encode("utf-8")) for page in pages]
        assert all(size <= READ_PAGE_MAX_BYTES for size in sizes)
        assert all(size < HARNESS_PERSIST_BYTES for size in sizes)
        assert max(sizes) > READ_PAGE_MAX_BYTES * 0.7
        assert sum(len(ids_in_order(page)) for page in pages) == 60
        assert sum(page.count(IN_CONTEXT_POINTER) for page in pages) == 12
        # By the stored counts the first page was still under budget, so the
        # content-only measure would have kept filling it past the ceiling
        stored = sum(by_prefix[prefix].token_count for prefix in ids_in_order(pages[0]))
        assert stored < READ_PAGE_TOKENS_MAX

    async def test_headers_and_pointers_are_budgeted(self, db, tools_db):
        """Forty in-context rows of one character each are charged their
        header and pointer lines: they don't all fit the smallest page, and
        the page renders within its budget."""
        here = await make_conversation(db, title="Here")
        for i in range(40):
            await make_message(db, here, content="x", created_at=at(minutes=i))
        ctx = native_ctx(conversation_id=here.id)
        page = await read_memories(ctx, from_="2026-09-01", page_tokens=READ_PAGE_TOKENS_MIN)
        shown = len(ids_in_order(page))
        assert 0 < shown < 40
        assert page.count(IN_CONTEXT_POINTER) == shown
        assert len(page.encode("utf-8")) <= READ_PAGE_TOKENS_MIN * RENDERED_CHARS_PER_TOKEN
        assert "Next page: pass cursor=" in page
