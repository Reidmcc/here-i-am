"""
Tests for memory status provenance and released-memory review (issue #320).

Two halves of one change:
- every memory_status write (set or clear) records who made it and when
  (status_set_by / status_set_at), and researcher-set changes since the
  entity's last session are reported to the entity at its next session
  start — the native first turn and the Claude Code identity block;
- memory_query mode="released" lists the entity's released memories so a
  release can be reviewed and undone by the one who made it.

These run against a real (in-memory SQLite) database: the anchor and the
listing are SQL, and the point of both is what the SQL selects.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

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
from app.services.memory_context import format_status_change_notice
from app.services.memory_service import (
    STATUS_SET_BY_ENTITY,
    STATUS_SET_BY_RESEARCHER,
    memory_service,
)
from app.services.memory_tools import (
    MemoryToolContext,
    mark_memory,
    query_memories,
    release_memory,
)
from app.services.session_manager import ConversationSession, SessionManager

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
ENTITY = "test-entity"
OTHER_ENTITY = "other-entity"
TEST_ENTITY_INDEXES = (
    '[{"index_name": "test-entity", "label": "Test Entity", '
    '"description": "Test entity", "llm_provider": "anthropic"}, '
    '{"index_name": "other-entity", "label": "Other Entity", '
    '"description": "Other entity", "llm_provider": "anthropic"}]'
)

NOW = datetime(2026, 9, 2, 12, 0, 0)


def at(**delta) -> datetime:
    return NOW + timedelta(**delta)


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
    """Two entities configured; memory "configured" for both (no Pinecone)."""
    monkeypatch.setattr(settings, "pinecone_indexes", TEST_ENTITY_INDEXES)
    monkeypatch.setattr(settings, "claude_code_mode_enabled", True)
    monkeypatch.setattr(memory_service, "is_configured", lambda entity_id=None: True)
    from app.services import llm_service

    monkeypatch.setattr(llm_service, "count_tokens", lambda text, model=None: len(text) // 4)


@pytest.fixture
async def async_client(session_factory, entities_configured):
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
    created_at: datetime = NOW,
    participants=None,
    **kwargs,
) -> Conversation:
    conversation = Conversation(
        id=str(uuid.uuid4()),
        title="t",
        entity_id=entity_id,
        created_at=created_at,
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
    content: str = "A memory worth keeping",
    created_at: datetime = NOW,
    speaker_entity_id=None,
    memory_status=None,
    status_set_by=None,
    status_set_at=None,
) -> Message:
    message = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        role=role,
        content=content,
        created_at=created_at,
        speaker_entity_id=speaker_entity_id,
        memory_status=memory_status,
        status_set_by=status_set_by,
        status_set_at=status_set_at,
    )
    db.add(message)
    await db.commit()
    return message


async def reload(db: AsyncSession, message_id: str) -> Message:
    """Re-read a row from the database, refreshing the identity-map copy."""
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


# ============================================================
# Provenance is written on every status write
# ============================================================

class TestSetMemoryStatusProvenance:
    async def test_entity_write_is_attributed(self, db):
        conversation = await make_conversation(db)
        message = await make_message(db, conversation)

        before = datetime.utcnow()
        assert await memory_service.set_memory_status(
            message.id, "released", db, set_by=STATUS_SET_BY_ENTITY
        )
        row = await reload(db, message.id)
        assert row.memory_status == "released"
        assert row.status_set_by == "entity"
        assert row.status_set_at is not None
        assert before - timedelta(seconds=1) <= row.status_set_at <= datetime.utcnow() + timedelta(seconds=1)

    async def test_clear_is_attributed_too(self, db):
        """A researcher clearing the entity's release is exactly the change
        the notice must report, so clears carry provenance like sets."""
        conversation = await make_conversation(db)
        message = await make_message(
            db, conversation, memory_status="released",
            status_set_by="entity", status_set_at=at(hours=-2),
        )
        assert await memory_service.set_memory_status(
            message.id, None, db, set_by=STATUS_SET_BY_RESEARCHER
        )
        row = await reload(db, message.id)
        assert row.memory_status is None
        assert row.status_set_by == "researcher"
        assert row.status_set_at > at(hours=-2)

    async def test_setter_is_required_and_validated(self, db):
        conversation = await make_conversation(db)
        message = await make_message(db, conversation)
        with pytest.raises(TypeError):
            await memory_service.set_memory_status(message.id, "pinned", db)  # noqa
        with pytest.raises(ValueError):
            await memory_service.set_memory_status(message.id, "pinned", db, set_by="someone")

    async def test_tools_attribute_to_entity(self, db, session_factory):
        conversation = await make_conversation(db)
        released = await make_message(db, conversation)
        pinned = await make_message(db, conversation)
        ctx = MemoryToolContext(entity_id=ENTITY, conversation_id=conversation.id)

        with patch("app.services.memory_tools.async_session_maker", session_factory):
            release_result = await release_memory(ctx, released.id)
            mark_result = await mark_memory(ctx, pinned.id)

        assert "Released memory" in release_result
        # The release message points at the review channel, not the researcher
        assert "mode='released'" in release_result
        assert "undo=true" in release_result
        assert "Pinned memory" in mark_result

        assert (await reload(db, released.id)).status_set_by == "entity"
        assert (await reload(db, pinned.id)).status_set_by == "entity"

    async def test_route_attributes_to_researcher(self, async_client, db):
        conversation = await make_conversation(db)
        message = await make_message(
            db, conversation, memory_status="released",
            status_set_by="entity", status_set_at=at(hours=-1),
        )

        response = await async_client.put(
            f"/api/memories/{message.id}/status", json={"status": None}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["memory_status"] is None
        assert body["status_set_by"] == "researcher"
        assert body["status_set_at"] is not None

        response = await async_client.put(
            f"/api/memories/{message.id}/status", json={"status": "pinned"}
        )
        assert response.json()["status_set_by"] == "researcher"

        # The overrides listing and the single-memory view expose provenance
        overrides = (await async_client.get("/api/memories/overrides")).json()
        assert len(overrides) == 1
        assert overrides[0]["status_set_by"] == "researcher"
        assert overrides[0]["status_set_at"] is not None

        single = (await async_client.get(f"/api/memories/{message.id}")).json()
        assert single["status_set_by"] == "researcher"

    async def test_legacy_status_has_null_provenance(self, async_client, db):
        conversation = await make_conversation(db)
        await make_message(db, conversation, memory_status="pinned")
        overrides = (await async_client.get("/api/memories/overrides")).json()
        assert overrides[0]["status_set_by"] is None
        assert overrides[0]["status_set_at"] is None


# ============================================================
# "Since the entity's last session"
# ============================================================

class TestLastSessionAnchor:
    async def test_none_when_the_entity_never_spoke(self, db, entities_configured):
        conversation = await make_conversation(db)
        await make_message(db, conversation, role=MessageRole.HUMAN)
        assert await memory_service.get_last_session_anchor(db, ENTITY) is None

    async def test_anchor_is_first_response_of_latest_spoken_conversation(
        self, db, entities_configured
    ):
        older = await make_conversation(db, created_at=at(days=-3))
        await make_message(db, older, created_at=at(days=-3, minutes=1))
        await make_message(db, older, created_at=at(days=-3, minutes=5))
        latest = await make_conversation(db, created_at=at(days=-1))
        await make_message(db, latest, role=MessageRole.HUMAN, created_at=at(days=-1))
        first_response = await make_message(db, latest, created_at=at(days=-1, minutes=2))
        await make_message(db, latest, created_at=at(days=-1, minutes=9))
        # A newer conversation the entity never answered in is not an anchor:
        # it never had a first turn to carry a notice
        unspoken = await make_conversation(db, created_at=at(hours=-1))
        await make_message(db, unspoken, role=MessageRole.HUMAN, created_at=at(hours=-1))

        anchor = await memory_service.get_last_session_anchor(db, ENTITY)
        assert anchor == first_response.created_at

    async def test_excludes_the_current_conversation(self, db, entities_configured):
        older = await make_conversation(db, created_at=at(days=-3))
        older_first = await make_message(db, older, created_at=at(days=-3, minutes=1))
        current = await make_conversation(db, created_at=at(days=-1))
        await make_message(db, current, created_at=at(days=-1, minutes=2))

        anchor = await memory_service.get_last_session_anchor(
            db, ENTITY, exclude_conversation_id=current.id
        )
        assert anchor == older_first.created_at

    async def test_only_this_entitys_responses_count(self, db, entities_configured):
        ours = await make_conversation(db, created_at=at(days=-5))
        ours_first = await make_message(db, ours, created_at=at(days=-5, minutes=1))
        # Another entity's own conversation, more recent
        theirs = await make_conversation(db, entity_id=OTHER_ENTITY, created_at=at(days=-2))
        await make_message(db, theirs, created_at=at(days=-2, minutes=1))
        # A multi-entity conversation we take part in, where only the other
        # entity has responded so far
        shared = await make_conversation(
            db, entity_id="multi-entity", created_at=at(days=-1),
            participants=[ENTITY, OTHER_ENTITY],
        )
        await make_message(
            db, shared, created_at=at(days=-1, minutes=1), speaker_entity_id=OTHER_ENTITY
        )

        anchor = await memory_service.get_last_session_anchor(db, ENTITY)
        assert anchor == ours_first.created_at

        # Once we respond there, the shared conversation becomes the anchor
        ours_in_shared = await make_message(
            db, shared, created_at=at(days=-1, minutes=7), speaker_entity_id=ENTITY
        )
        anchor = await memory_service.get_last_session_anchor(db, ENTITY)
        assert anchor == ours_in_shared.created_at


class TestResearcherStatusChanges:
    async def test_only_researcher_writes_after_the_anchor(self, db, entities_configured):
        conversation = await make_conversation(db)
        anchor = at(hours=-3)
        entity_release = await make_message(
            db, conversation, content="entity released", memory_status="released",
            status_set_by="entity", status_set_at=at(hours=-1),
        )
        old_override = await make_message(
            db, conversation, content="old researcher pin", memory_status="pinned",
            status_set_by="researcher", status_set_at=at(hours=-4),
        )
        cleared = await make_message(
            db, conversation, content="researcher cleared this", memory_status=None,
            status_set_by="researcher", status_set_at=at(hours=-2),
        )
        released = await make_message(
            db, conversation, content="researcher released this", memory_status="released",
            status_set_by="researcher", status_set_at=at(hours=-1),
        )
        other = await make_conversation(db, entity_id=OTHER_ENTITY)
        await make_message(
            db, other, content="another entity's memory", memory_status="released",
            status_set_by="researcher", status_set_at=at(hours=-1),
        )

        changes = await memory_service.get_researcher_status_changes(db, ENTITY, since=anchor)
        assert [c["id"] for c in changes] == [cleared.id, released.id]
        assert changes[0]["memory_status"] is None
        assert changes[1]["memory_status"] == "released"
        assert entity_release.id not in {c["id"] for c in changes}

        # No anchor (the entity has never spoken): every researcher write counts
        everything = await memory_service.get_researcher_status_changes(db, ENTITY)
        assert [c["id"] for c in everything] == [old_override.id, cleared.id, released.id]

    async def test_notice_reports_each_change_once(self, db, entities_configured):
        """A change made before a session's first response is reported by
        that session; a change made during it is reported by the next; a
        change is never reported twice or dropped."""
        previous = await make_conversation(db, created_at=at(days=-2))
        await make_message(db, previous, created_at=at(days=-2, minutes=1))
        memory = await make_message(db, previous, content="the one they changed")
        await memory_service.set_memory_status(
            memory.id, "released", db, set_by=STATUS_SET_BY_RESEARCHER
        )

        current = await make_conversation(db, created_at=datetime.utcnow())
        notice = await memory_service.build_status_change_notice(
            db, ENTITY, exclude_conversation_id=current.id
        )
        assert notice is not None
        assert "[MEMORY STATUS NOTICE]" in notice
        assert memory.id[:8] in notice
        assert "now released" in notice
        assert "the one they changed" in notice

        # The current session responds (after the change); the next session
        # anchors on that response and sees nothing new
        await make_message(db, current, created_at=datetime.utcnow())
        following = await make_conversation(db)
        assert await memory_service.build_status_change_notice(
            db, ENTITY, exclude_conversation_id=following.id
        ) is None

        # A further change during the current session reaches the next one
        await memory_service.set_memory_status(
            memory.id, None, db, set_by=STATUS_SET_BY_RESEARCHER
        )
        notice = await memory_service.build_status_change_notice(
            db, ENTITY, exclude_conversation_id=following.id
        )
        assert notice is not None
        assert "status cleared (now normal)" in notice

    async def test_entity_own_changes_are_silent(self, db, entities_configured):
        previous = await make_conversation(db, created_at=at(days=-2))
        await make_message(db, previous, created_at=at(days=-2, minutes=1))
        memory = await make_message(db, previous)
        await memory_service.set_memory_status(
            memory.id, "released", db, set_by=STATUS_SET_BY_ENTITY
        )
        current = await make_conversation(db)
        assert await memory_service.build_status_change_notice(
            db, ENTITY, exclude_conversation_id=current.id
        ) is None


class TestFormatStatusChangeNotice:
    def test_one_line_per_change_with_review_pointer(self):
        notice = format_status_change_notice([
            {
                "id": "abcdef12-0000", "role": "assistant", "memory_status": "released",
                "status_set_at": datetime(2026, 9, 1, 14, 2),
                "content": "  A memory   with   odd spacing\nand a newline " + "x" * 200,
            },
            {
                "id": "12345678-0000", "role": "reflection", "memory_status": None,
                "status_set_at": "2026-09-02T09:30:00",
                "content": "short",
            },
        ])
        lines = notice.split("\n")
        assert lines[0].startswith("[MEMORY STATUS NOTICE]")
        assert "2 of your memories" in lines[0]
        assert lines[1].startswith("- abcdef12 (")
        assert "now released on 2026-09-01 14:02 UTC" in lines[1]
        assert "A memory with odd spacing and a newline" in lines[1]
        assert lines[1].endswith('…"')
        assert "- 12345678 (" in lines[2]
        assert "status cleared (now normal) on 2026-09-02 09:30 UTC" in lines[2]
        assert '"short"' in lines[2]
        assert 'memory_query mode="released"' in lines[3]
        assert "memory_release undo=true" in lines[3]

    def test_singular_and_missing_timestamp(self):
        notice = format_status_change_notice([
            {"id": "abcdef12", "role": "human", "memory_status": "pinned",
             "status_set_at": None, "content": "hello"},
        ])
        assert "1 of your memory:" in notice
        assert "now pinned: \"hello\"" in notice


# ============================================================
# Released-memory review
# ============================================================

class TestReleasedMemoriesQuery:
    async def _seed(self, db):
        conversation = await make_conversation(db)
        newest = await make_message(
            db, conversation, content="released most recently",
            created_at=at(days=-10), memory_status="released",
            status_set_by="entity", status_set_at=at(hours=-3),
        )
        by_researcher = await make_message(
            db, conversation, content="released by the researcher",
            created_at=at(days=-2), memory_status="released",
            status_set_by="researcher", status_set_at=at(days=-1),
        )
        legacy = await make_message(
            db, conversation, content="released before provenance", role=MessageRole.HUMAN,
            created_at=at(days=-1), memory_status="released",
        )
        await make_message(db, conversation, content="pinned, not released", memory_status="pinned")
        await make_message(db, conversation, content="normal memory")
        return conversation, newest, by_researcher, legacy

    async def test_newest_release_first_legacy_last(self, db, entities_configured):
        _, newest, by_researcher, legacy = await self._seed(db)
        memories = await memory_service.get_released_memories(db, ENTITY, limit=10)
        assert [m["id"] for m in memories] == [newest.id, by_researcher.id, legacy.id]
        assert memories[0]["status_set_by"] == "entity"
        assert memories[1]["status_set_by"] == "researcher"
        assert memories[2]["status_set_by"] is None
        assert memories[2]["status_set_at"] is None
        assert await memory_service.count_released_memories(db, ENTITY) == 3

    async def test_exclusions_since_and_role_filter(self, db, entities_configured):
        conversation, newest, by_researcher, legacy = await self._seed(db)

        # since bounds the release time; legacy (unknown release time) drops out
        memories = await memory_service.get_released_memories(
            db, ENTITY, limit=10, since=at(days=-2)
        )
        assert [m["id"] for m in memories] == [newest.id, by_researcher.id]

        # ids already in view are skipped (paging); the count is unaffected
        memories = await memory_service.get_released_memories(
            db, ENTITY, limit=10, exclude_ids={newest.id}
        )
        assert [m["id"] for m in memories] == [by_researcher.id, legacy.id]
        assert await memory_service.count_released_memories(db, ENTITY) == 3

        # the current conversation is excluded like every other mode
        assert await memory_service.get_released_memories(
            db, ENTITY, limit=10, exclude_conversation_id=conversation.id
        ) == []

        # source narrows by author, in SQL
        human = await memory_service.get_released_memories(db, ENTITY, limit=10, role_filter="human")
        assert [m["id"] for m in human] == [legacy.id]
        ai = await memory_service.get_released_memories(db, ENTITY, limit=10, role_filter="ai")
        assert [m["id"] for m in ai] == [newest.id, by_researcher.id]
        assert await memory_service.count_released_memories(db, ENTITY, role_filter="ai") == 2
        assert await memory_service.get_released_memories(
            db, ENTITY, limit=10, role_filter="reflection"
        ) == []

    async def test_scoped_to_the_entitys_experience(self, db, entities_configured):
        _, newest, by_researcher, legacy = await self._seed(db)
        theirs = await make_conversation(db, entity_id=OTHER_ENTITY)
        await make_message(db, theirs, content="not ours", memory_status="released",
                           status_set_by="entity", status_set_at=at(minutes=-1))
        shared = await make_conversation(
            db, entity_id="multi-entity", participants=[ENTITY, OTHER_ENTITY]
        )
        shared_release = await make_message(
            db, shared, content="shared experience", memory_status="released",
            status_set_by="entity", status_set_at=at(minutes=-2),
        )
        not_shared = await make_conversation(
            db, entity_id="multi-entity", participants=[OTHER_ENTITY]
        )
        await make_message(db, not_shared, content="not shared", memory_status="released",
                           status_set_by="entity", status_set_at=at(minutes=-3))

        memories = await memory_service.get_released_memories(db, ENTITY, limit=10)
        assert [m["id"] for m in memories] == [
            shared_release.id, newest.id, by_researcher.id, legacy.id
        ]

    async def test_tool_lists_and_links_without_tracking(
        self, db, session_factory, entities_configured
    ):
        _, newest, by_researcher, legacy = await self._seed(db)
        current = await make_conversation(
            db, source=ConversationSource.CLAUDE_CODE.value,
            external_session_id="cc-session",
        )
        ctx = MemoryToolContext(
            entity_id=ENTITY, conversation_id=current.id, link_query_results=True
        )
        with patch("app.services.memory_tools.async_session_maker", session_factory):
            result = await query_memories(ctx, mode="released", num_results=10)

        assert "3 shown of 3 released in total" in result
        assert "most recently released first" in result
        assert result.index(newest.id[:8]) < result.index(by_researcher.id[:8]) < result.index(legacy.id[:8])
        assert "released by you" in result
        assert "released by the researcher" in result
        assert "released before release provenance was recorded" in result
        assert "You said" in result and "Human said" in result
        assert "via Here I Am" in result
        assert "Restore any of these with memory_release(memory_id, undo=true)" in result
        assert "similarity" not in result

        # Curation, not recall: no retrieval tracking
        for memory in (newest, by_researcher, legacy):
            assert (await reload(db, memory.id)).times_retrieved == 0
        # Claude Code: linked as the dedup record; stamped for turn dedup
        result_links = await db.execute(
            select(ConversationMemoryLink.message_id).where(
                ConversationMemoryLink.conversation_id == current.id
            )
        )
        assert set(result_links.scalars().all()) == {newest.id, by_researcher.id, legacy.id}
        assert ctx.turn_query_memory_ids == {newest.id, by_researcher.id, legacy.id}

        # Paging: what is in view is skipped, and the total keeps the picture whole
        with patch("app.services.memory_tools.async_session_maker", session_factory):
            again = await query_memories(ctx, mode="released", num_results=10)
        assert "not already in view (3 released in total)" in again

    async def test_tool_source_and_since_and_empty(
        self, db, session_factory, entities_configured
    ):
        _, newest, by_researcher, legacy = await self._seed(db)
        current = await make_conversation(db)
        ctx = MemoryToolContext(entity_id=ENTITY, conversation_id=current.id)
        with patch("app.services.memory_tools.async_session_maker", session_factory):
            human = await query_memories(ctx, mode="released", source="human")
            since = await query_memories(
                MemoryToolContext(entity_id=ENTITY, conversation_id=current.id),
                mode="released", since=at(days=-2).isoformat(),
            )
            nothing = await query_memories(
                MemoryToolContext(entity_id=OTHER_ENTITY, conversation_id=current.id),
                mode="released",
            )
        assert "(the human's messages only)" in human
        assert legacy.id[:8] in human and newest.id[:8] not in human
        assert "1 shown of 1 released in total" in human
        assert "(released after 2026-08-31T12:00:00 UTC)" in since
        assert legacy.id[:8] not in since
        assert nothing == "You have no released memories."


# ============================================================
# Session-start delivery: Claude Code identity block, native first turn
# ============================================================

async def _seed_researcher_change(db) -> Message:
    previous = await make_conversation(db, created_at=at(days=-2))
    await make_message(db, previous, created_at=at(days=-2, minutes=1))
    memory = await make_message(db, previous, content="overridden by the researcher")
    await memory_service.set_memory_status(
        memory.id, "released", db, set_by=STATUS_SET_BY_RESEARCHER
    )
    return memory


class TestClaudeCodeSessionStartNotice:
    async def test_notice_rides_inline_in_the_identity_block(self, async_client, db):
        memory = await _seed_researcher_change(db)
        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        body = response.json()
        assert "[MEMORY STATUS NOTICE]" in body["context"]
        assert memory.id[:8] in body["context"]
        assert "now released" in body["context"]
        assert "[MEMORY STATUS NOTICE]" not in body["bulk_context"]

    async def test_silence_when_nothing_changed(self, async_client, db):
        previous = await make_conversation(db, created_at=at(days=-2))
        await make_message(db, previous, created_at=at(days=-2, minutes=1))
        memory = await make_message(db, previous)
        await memory_service.set_memory_status(
            memory.id, "released", db, set_by=STATUS_SET_BY_ENTITY
        )
        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        assert "[MEMORY STATUS NOTICE]" not in response.json()["context"]

    async def test_failed_check_is_loud(self, async_client, db, monkeypatch):
        monkeypatch.setattr(
            memory_service, "build_status_change_notice",
            AsyncMock(side_effect=RuntimeError("db went away")),
        )
        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        context = response.json()["context"]
        assert "[MEMORY STATUS NOTICE] Could not check" in context
        assert "db went away" in context


class TestNativeFirstTurnNotice:
    def _session(self, conversation_id: str) -> ConversationSession:
        return ConversationSession(
            conversation_id=conversation_id,
            model="claude-sonnet-4-5-20250929",
            temperature=0.7,
            max_tokens=1000,
            system_prompt="",
            entity_id=ENTITY,
        )

    async def test_injects_context_only_notice(self, db, entities_configured):
        memory = await _seed_researcher_change(db)
        current = await make_conversation(db)
        session = self._session(current.id)

        await SessionManager()._inject_status_change_notice(session, db)

        assert len(session.conversation_context) == 1
        notice = session.conversation_context[0]
        assert notice["role"] == "user"
        assert notice["is_context_notice"] is True
        assert "[MEMORY STATUS NOTICE]" in notice["content"]
        assert memory.id[:8] in notice["content"]
        # A notice is not a conversational message: the first-turn check
        # that gates the injection still reads this as the first turn
        assert session.has_conversational_messages() is False

    async def test_nothing_injected_without_changes(self, db, entities_configured):
        current = await make_conversation(db)
        session = self._session(current.id)
        await SessionManager()._inject_status_change_notice(session, db)
        assert session.conversation_context == []

    async def test_failed_check_is_loud(self, db, entities_configured, monkeypatch):
        monkeypatch.setattr(
            memory_service, "build_status_change_notice",
            AsyncMock(side_effect=RuntimeError("db went away")),
        )
        current = await make_conversation(db)
        session = self._session(current.id)
        await SessionManager()._inject_status_change_notice(session, db)
        assert len(session.conversation_context) == 1
        assert "Could not check" in session.conversation_context[0]["content"]
        assert "db went away" in session.conversation_context[0]["content"]
