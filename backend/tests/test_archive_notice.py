"""
Tests for the archive notice (issue #367).

Archiving withdraws a whole conversation from every memory surface, and
from inside a withdrawn conversation looks exactly like one that never
happened. So each archive/unarchive is stamped (archive_changed_at, plus
an optional researcher note), and the changes since the entity's last
session are reported once at its next session start — the native first
turn and the Claude Code identity block — through the same anchor and
delivery path as researcher-set status changes (test_memory_status_provenance).

The notice says a gap exists — span, size, source, when, the note — and
never what was in it: no title, no content.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

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
    ConversationSource,
    Message,
    MessageRole,
)
from app.services.memory_context import format_archive_change_notice
from app.services.memory_service import STATUS_SET_BY_RESEARCHER, memory_service
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

SECRET_TITLE = "The conversation about the thing"
SECRET_CONTENT = "Something only the conversation itself should say"


def ago(**delta) -> datetime:
    return datetime.utcnow() - timedelta(**delta)


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
    created_at: datetime = None,
    participants=None,
    **kwargs,
) -> Conversation:
    conversation = Conversation(
        id=str(uuid.uuid4()),
        title=SECRET_TITLE,
        entity_id=entity_id,
        created_at=created_at or datetime.utcnow(),
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
    created_at: datetime = None,
    content: str = SECRET_CONTENT,
    speaker_entity_id=None,
) -> Message:
    message = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        role=role,
        content=content,
        created_at=created_at or datetime.utcnow(),
        speaker_entity_id=speaker_entity_id,
    )
    db.add(message)
    await db.commit()
    return message


async def reload(db: AsyncSession, conversation_id: str) -> Conversation:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def change_archive(client, db: AsyncSession, conversation_id: str, action: str, reason=None):
    """Archive or unarchive through the route, then empty db's identity map:
    the route commits through its own session, and db would otherwise keep
    handing back the pre-change row."""
    kwargs = {"json": {"reason": reason}} if reason is not None else {}
    response = await client.post(f"/api/conversations/{conversation_id}/{action}", **kwargs)
    db.expunge_all()
    return response


async def seed_withdrawable(db: AsyncSession) -> Conversation:
    """A past conversation (Claude Code, two days of talk, three messages)
    that the entity spoke in, so it is also the last-session anchor."""
    conversation = await make_conversation(
        db, created_at=ago(days=5), source=ConversationSource.CLAUDE_CODE.value,
        external_session_id=str(uuid.uuid4()),
    )
    await make_message(db, conversation, role=MessageRole.HUMAN, created_at=ago(days=5))
    await make_message(db, conversation, created_at=ago(days=5, minutes=-1))
    await make_message(db, conversation, created_at=ago(days=3))
    # A tool row is not a message the entity remembers; it doesn't count
    await make_message(db, conversation, role=MessageRole.TOOL_USE, created_at=ago(days=3))
    return conversation


# ============================================================
# The routes stamp every change
# ============================================================

class TestArchiveRoutesStamp:
    async def test_archive_and_unarchive_are_stamped(self, async_client, db):
        conversation = await make_conversation(db)
        before = datetime.utcnow() - timedelta(seconds=1)

        response = await async_client.post(
            f"/api/conversations/{conversation.id}/archive",
            json={"reason": "  Something went wrong here.  "},
        )
        assert response.status_code == 200
        row = await reload(db, conversation.id)
        assert row.is_archived is True
        assert row.archive_changed_at >= before
        assert row.archive_note == "Something went wrong here."
        archived_at = row.archive_changed_at

        # No body is fine; the unarchive replaces the note with its own (none)
        response = await async_client.post(f"/api/conversations/{conversation.id}/unarchive")
        assert response.status_code == 200
        row = await reload(db, conversation.id)
        assert row.is_archived is False
        assert row.archive_changed_at >= archived_at
        assert row.archive_note is None

    async def test_blank_note_is_no_note_and_long_note_is_refused(self, async_client, db):
        conversation = await make_conversation(db)
        response = await async_client.post(
            f"/api/conversations/{conversation.id}/archive", json={"reason": "x" * 501}
        )
        assert response.status_code == 422
        assert (await reload(db, conversation.id)).is_archived is False

        response = await async_client.post(
            f"/api/conversations/{conversation.id}/archive", json={"reason": "   "}
        )
        assert response.status_code == 200
        assert (await reload(db, conversation.id)).archive_note is None

    async def test_archived_listing_shows_what_the_entity_was_told(self, async_client, db):
        conversation = await make_conversation(db)
        await async_client.post(
            f"/api/conversations/{conversation.id}/archive", json={"reason": "why"}
        )
        listing = (await async_client.get("/api/conversations/archived")).json()
        assert listing[0]["archive_note"] == "why"
        assert listing[0]["archive_changed_at"] is not None

    async def test_legacy_archive_is_never_reported(self, async_client, db):
        """Archived before stamping existed: NULL, not backfilled, silent."""
        conversation = await seed_withdrawable(db)
        conversation.is_archived = True
        await db.commit()
        current = await make_conversation(db)
        assert await memory_service.build_archive_change_notice(
            db, ENTITY, exclude_conversation_id=current.id
        ) is None


# ============================================================
# Which changes, and once
# ============================================================

class TestArchiveChanges:
    async def test_scoped_to_the_entitys_experience(self, db, entities_configured):
        ours = await make_conversation(db, is_archived=True, archive_changed_at=ago(hours=3))
        theirs = await make_conversation(
            db, entity_id=OTHER_ENTITY, is_archived=True, archive_changed_at=ago(hours=2)
        )
        shared = await make_conversation(
            db, entity_id="multi-entity", participants=[ENTITY, OTHER_ENTITY],
            is_archived=True, archive_changed_at=ago(hours=1),
        )

        ours_changes = await memory_service.get_researcher_archive_changes(db, ENTITY)
        assert [c["id"] for c in ours_changes] == [ours.id, shared.id]
        # Every participant of a multi-entity conversation is told
        theirs_changes = await memory_service.get_researcher_archive_changes(db, OTHER_ENTITY)
        assert [c["id"] for c in theirs_changes] == [theirs.id, shared.id]

    async def test_span_and_size_count_memory_messages_only(self, db, entities_configured):
        conversation = await seed_withdrawable(db)
        conversation.is_archived = True
        conversation.archive_changed_at = datetime.utcnow()
        await db.commit()

        [change] = await memory_service.get_researcher_archive_changes(db, ENTITY)
        assert change["message_count"] == 3
        assert change["first_message_at"].date() == ago(days=5).date()
        assert change["last_message_at"].date() == ago(days=3).date()
        assert change["source"] == "claude_code"
        assert change["is_archived"] is True
        assert "title" not in change and "content" not in change

    async def test_archive_after_the_anchor_is_reported_once(
        self, async_client, db
    ):
        withdrawn = await seed_withdrawable(db)
        await change_archive(async_client, db, withdrawn.id, "archive")

        current = await make_conversation(db)
        notice = await memory_service.build_archive_change_notice(
            db, ENTITY, exclude_conversation_id=current.id
        )
        assert notice is not None
        assert notice.count("was withdrawn from your memory") == 1

        # The current session responds; the next one anchors on that
        # response and hears nothing more
        await make_message(db, current)
        following = await make_conversation(db)
        assert await memory_service.build_archive_change_notice(
            db, ENTITY, exclude_conversation_id=following.id
        ) is None

        # Restoring it during that session reaches the one after
        await change_archive(async_client, db, withdrawn.id, "unarchive")
        notice = await memory_service.build_archive_change_notice(
            db, ENTITY, exclude_conversation_id=following.id
        )
        assert "was restored to your memory" in notice
        assert "withdrawn from your memory" not in notice.split("\n")[1]

    async def test_a_standing_room_that_keeps_talking_does_not_repeat_it(
        self, db, entities_configured
    ):
        """The PR #369 review's probe. Since fork adoption a standing room
        keeps one conversation for weeks and speaks on nearly every tick, so
        it is usually the latest speaker; anchoring on *its* first response
        anchored on its birth, and every new session heard every change
        since. The anchor is the latest session start that spoke."""
        porch = await make_conversation(db, created_at=ago(days=10))
        await make_message(db, porch, role=MessageRole.HUMAN, created_at=ago(days=10))
        await make_message(db, porch, created_at=ago(days=10, minutes=-1))
        await make_conversation(
            db, created_at=ago(days=6), is_archived=True, archive_changed_at=ago(days=5)
        )
        w1 = await make_conversation(db, created_at=ago(hours=2))
        await make_message(db, w1, role=MessageRole.HUMAN, created_at=ago(hours=2))
        await make_message(db, w1, created_at=ago(hours=2, minutes=-5))
        # The porch is still talking, and so is the latest speaker
        await make_message(db, porch, role=MessageRole.HUMAN, created_at=ago(minutes=2))
        await make_message(db, porch, created_at=ago(minutes=1))

        w2 = await make_conversation(db)
        assert await memory_service.build_archive_change_notice(
            db, ENTITY, exclude_conversation_id=w2.id
        ) is None

    async def test_a_change_during_a_first_turn_is_not_lost(self, db, entities_configured):
        """The review's second probe: a session checks for changes when its
        first turn begins (the native send, or Claude Code's SessionStart
        just before the first prompt) but first *responds* at the end of
        that turn — minutes later for an agentic one. A change landing in
        between was after the check and before the anchor, so nobody was
        told. The anchor is where the turn that carried the notice began."""
        w1 = await make_conversation(db, created_at=ago(hours=1))
        await make_message(db, w1, role=MessageRole.HUMAN, created_at=ago(hours=1))
        # W1's check ran as that prompt arrived; then, mid-turn, an archive
        await make_conversation(
            db, created_at=ago(days=6), is_archived=True, archive_changed_at=ago(minutes=40)
        )
        # A reflection saved during the turn doesn't start a turn
        await make_message(
            db, w1, role=MessageRole.REFLECTION, created_at=ago(minutes=35)
        )
        await make_message(db, w1, created_at=ago(minutes=30))

        w2 = await make_conversation(db)
        notice = await memory_service.build_archive_change_notice(
            db, ENTITY, exclude_conversation_id=w2.id
        )
        assert notice is not None and "was withdrawn from your memory" in notice

    async def test_archive_before_the_anchor_is_not_reported(self, db, entities_configured):
        await make_conversation(db, is_archived=True, archive_changed_at=ago(days=2))
        spoken = await make_conversation(db, created_at=ago(days=1))
        await make_message(db, spoken, created_at=ago(days=1))
        current = await make_conversation(db)
        assert await memory_service.build_archive_change_notice(
            db, ENTITY, exclude_conversation_id=current.id
        ) is None


# ============================================================
# What the notice says, and doesn't
# ============================================================

class TestArchiveNoticeText:
    async def test_facts_and_note_but_no_title_or_content(self, async_client, db):
        withdrawn = await seed_withdrawable(db)
        await change_archive(
            async_client, db, withdrawn.id, "archive",
            reason="It went somewhere it shouldn't have.",
        )
        current = await make_conversation(db)
        notice = await memory_service.build_archive_change_notice(
            db, ENTITY, exclude_conversation_id=current.id
        )
        first, last = ago(days=5).strftime("%Y-%m-%d"), ago(days=3).strftime("%Y-%m-%d")
        assert notice.startswith("[MEMORY ARCHIVE NOTICE]")
        assert f"A conversation from {first} to {last} (3 messages, via Claude Code)" in notice
        assert "was withdrawn from your memory by the researcher on " in notice
        assert 'Their note: "It went somewhere it shouldn\'t have."' in notice
        assert SECRET_TITLE not in notice
        assert SECRET_CONTENT not in notice
        assert withdrawn.id not in notice and withdrawn.id[:8] not in notice

    def test_same_day_empty_and_overflow(self):
        def change(**kw):
            base = {
                "is_archived": True, "source": "native", "message_count": 1,
                "archive_changed_at": datetime(2026, 9, 24, 13, 5),
                "first_message_at": datetime(2026, 8, 3, 9), "last_message_at": datetime(2026, 8, 3, 17),
            }
            return {**base, **kw}

        notice = format_archive_change_notice([
            change(),
            change(first_message_at=None, last_message_at=None, message_count=0,
                   created_at="2026-08-04T10:00:00", is_archived=False, archive_changed_at=None),
        ])
        lines = notice.split("\n")
        assert "2 whole conversations" in lines[0]
        assert lines[1] == (
            "- A conversation on 2026-08-03 (1 message, via Here I Am) was withdrawn "
            "from your memory by the researcher on 2026-09-24 13:05 UTC."
        )
        assert lines[2] == (
            "- An empty conversation started 2026-08-04 (0 messages, via Here I Am) "
            "was restored to your memory by the researcher."
        )
        assert "only sign of it" in lines[3]

        many = format_archive_change_notice(
            [change(message_count=2)] * 12 + [change(is_archived=False, message_count=5)],
            max_lines=10,
        )
        lines = many.split("\n")
        assert "13 whole conversations" in lines[0]
        assert len([line for line in lines if line.startswith("- A conversation")]) == 10
        assert "- And 3 more: 2 withdrawn, 1 restored, 9 messages in all." in lines

    def test_long_notes_are_bounded_by_characters_not_just_lines(self):
        """Ten lines of 500-char notes would run ~6.5 KB (PR #369 review):
        the listing stops at the character budget and counts the rest,
        saying how many notes went unshown. One line is always listed."""
        noted = {
            "is_archived": True, "source": "native", "message_count": 4,
            "archive_changed_at": datetime(2026, 9, 24, 13, 5),
            "first_message_at": datetime(2026, 8, 3), "last_message_at": datetime(2026, 8, 4),
            "archive_note": "n" * 500,
        }
        notice = format_archive_change_notice([noted] * 10, max_chars=3000)
        lines = notice.split("\n")
        listed = [line for line in lines if line.startswith("- A conversation")]
        assert 1 <= len(listed) < 10
        assert sum(len(line) for line in listed) <= 3000
        assert (
            f"- And {10 - len(listed)} more: {10 - len(listed)} withdrawn, 0 restored, "
            f"{4 * (10 - len(listed))} messages in all; {10 - len(listed)} of them "
            "with a note from the researcher, not shown here."
        ) in lines

        only = format_archive_change_notice([noted], max_chars=10)
        assert "n" * 500 in only and "more:" not in only


# ============================================================
# Delivery: Claude Code identity block, native first turn
# ============================================================

class TestClaudeCodeDelivery:
    async def test_rides_inline_in_the_identity_block(self, async_client, db):
        withdrawn = await seed_withdrawable(db)
        await change_archive(async_client, db, withdrawn.id, "archive")
        body = (await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )).json()
        assert "[MEMORY ARCHIVE NOTICE]" in body["context"]
        assert "was withdrawn from your memory" in body["context"]
        assert "[MEMORY ARCHIVE NOTICE]" not in body["bulk_context"]

    async def test_failed_check_is_loud_and_does_not_hide_the_status_notice(
        self, async_client, db, monkeypatch
    ):
        previous = await make_conversation(db, created_at=ago(days=2))
        await make_message(db, previous, created_at=ago(days=2))
        memory = await make_message(db, previous, content="overridden")
        await memory_service.set_memory_status(
            memory.id, "released", db, set_by=STATUS_SET_BY_RESEARCHER
        )
        monkeypatch.setattr(
            memory_service, "build_archive_change_notice",
            AsyncMock(side_effect=RuntimeError("db went away")),
        )
        context = (await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )).json()["context"]
        assert "[MEMORY ARCHIVE NOTICE] Could not check" in context
        assert "db went away" in context
        assert "[MEMORY STATUS NOTICE] Since your last session" in context


    async def test_a_broken_wrapper_still_speaks(self, async_client, monkeypatch):
        monkeypatch.setattr(
            memory_service, "build_researcher_change_notices",
            AsyncMock(side_effect=RuntimeError("promise broken")),
        )
        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        assert response.status_code == 200
        context = response.json()["context"]
        assert "Could not check for changes the researcher made" in context
        assert "promise broken" in context


    async def test_nothing_changed_is_said_for_both(self, async_client, db):
        context = (await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )).json()["context"]
        assert (
            "[MEMORY STATUS NOTICE] Checked: since your last session, the researcher "
            "changed the status of none of your memories."
        ) in context
        assert (
            "[MEMORY ARCHIVE NOTICE] Checked: since your last session, the researcher "
            "withdrew or restored none of your conversations."
        ) in context


class TestPostCompactionDelivery:
    """A long-running room never starts fresh, so without the notice in the
    post-compaction block it would never hear of an archive (issue #367
    follow-up). Its window is its own: since it was last told."""

    async def _porch(self, db) -> Conversation:
        porch = await make_conversation(
            db, created_at=ago(days=10), source=ConversationSource.CLAUDE_CODE.value,
            external_session_id="porch-session",
        )
        await make_message(db, porch, role=MessageRole.HUMAN, created_at=ago(days=10))
        await make_message(db, porch, created_at=ago(days=10, minutes=-1))
        return porch

    async def _compact(self, client, db) -> str:
        response = await client.post(
            "/api/claude-code/session-start",
            json={"session_id": "porch-session", "source": "compact"},
        )
        db.expunge_all()
        assert response.status_code == 200
        return response.json()["context"]

    async def test_a_room_hears_what_it_missed_then_only_what_is_new(
        self, async_client, db
    ):
        await self._porch(db)
        withdrawn = await seed_withdrawable(db)
        await change_archive(async_client, db, withdrawn.id, "archive")
        # A workshop started after the archive and was told of it; the house
        # anchor has moved past it, but the porch was never told
        workshop = await make_conversation(db)
        await make_message(db, workshop, role=MessageRole.HUMAN)
        await make_message(db, workshop)

        context = await self._compact(async_client, db)
        assert (
            "[MEMORY ARCHIVE NOTICE] Since this session was last told (at its start "
            "or its last compaction) the researcher withdrew or restored 1 whole "
            "conversation of yours:"
        ) in context
        assert "was withdrawn from your memory" in context
        assert "[MEMORY STATUS NOTICE] Checked: since this session was last told" in context

        # The next compaction is told nothing twice
        context = await self._compact(async_client, db)
        assert "was withdrawn from your memory" not in context
        assert (
            "[MEMORY ARCHIVE NOTICE] Checked: since this session was last told (at its "
            "start or its last compaction), the researcher withdrew or restored none "
            "of your conversations."
        ) in context

        # ...and a change after it reaches the one after
        await change_archive(async_client, db, withdrawn.id, "unarchive")
        context = await self._compact(async_client, db)
        assert "was restored to your memory" in context

    async def test_a_room_with_no_turn_yet_falls_back_to_the_house_anchor(
        self, async_client, db
    ):
        """A compact that registers its own row has nothing recorded, so no
        moment it was told: the window is the house's."""
        spoken = await make_conversation(db, created_at=ago(days=1))
        await make_message(db, spoken, role=MessageRole.HUMAN, created_at=ago(days=1))
        await make_message(db, spoken, created_at=ago(days=1, minutes=-1))
        await make_conversation(
            db, is_archived=True, archive_changed_at=ago(days=2)  # before the anchor
        )
        await make_conversation(
            db, is_archived=True, archive_changed_at=ago(hours=1)  # after it
        )
        context = (await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": "brand-new-session", "source": "compact"},
        )).json()["context"]
        assert "Since your last session the researcher withdrew or restored 1 whole" in context

    async def test_failure_is_loud(self, async_client, db, monkeypatch):
        await self._porch(db)
        monkeypatch.setattr(
            memory_service, "get_last_session_anchor",
            AsyncMock(side_effect=RuntimeError("db went away")),
        )
        context = await self._compact(async_client, db)
        assert "Could not check for changes the researcher made" in context
        assert "db went away" in context


class TestNativeDelivery:
    def _session(self, conversation_id: str) -> ConversationSession:
        return ConversationSession(
            conversation_id=conversation_id,
            model="claude-sonnet-4-5-20250929",
            temperature=0.7,
            max_tokens=1000,
            system_prompt="",
            entity_id=ENTITY,
        )

    async def test_injects_one_context_only_notice(self, async_client, db):
        withdrawn = await seed_withdrawable(db)
        await change_archive(async_client, db, withdrawn.id, "archive")
        current = await make_conversation(db)
        session = self._session(current.id)

        await SessionManager()._inject_status_change_notice(session, db)

        assert len(session.conversation_context) == 1
        notice = session.conversation_context[0]
        assert notice["is_context_notice"] is True
        assert notice["content"].startswith("[MEMORY ARCHIVE NOTICE]")
        assert session.has_conversational_messages() is False

    async def test_status_and_archive_notices_share_one_message(self, async_client, db):
        withdrawn = await seed_withdrawable(db)
        memory = await make_message(db, withdrawn, content="overridden", created_at=ago(days=4))
        await memory_service.set_memory_status(
            memory.id, "pinned", db, set_by=STATUS_SET_BY_RESEARCHER
        )
        await change_archive(async_client, db, withdrawn.id, "archive")
        current = await make_conversation(db)
        session = self._session(current.id)

        await SessionManager()._inject_status_change_notice(session, db)

        [notice] = session.conversation_context
        assert notice["content"].index("[MEMORY STATUS NOTICE]") < notice["content"].index(
            "[MEMORY ARCHIVE NOTICE]"
        )

    async def test_failed_check_is_loud(self, db, entities_configured, monkeypatch):
        monkeypatch.setattr(
            memory_service, "build_archive_change_notice",
            AsyncMock(side_effect=RuntimeError("db went away")),
        )
        current = await make_conversation(db)
        session = self._session(current.id)
        await SessionManager()._inject_status_change_notice(session, db)
        [notice] = session.conversation_context
        assert "[MEMORY ARCHIVE NOTICE] Could not check" in notice["content"]
        assert "db went away" in notice["content"]

    async def test_one_anchor_for_both_and_its_failure_is_said_twice(
        self, db, entities_configured, monkeypatch
    ):
        anchor = AsyncMock(return_value=None)
        monkeypatch.setattr(memory_service, "get_last_session_anchor", anchor)
        current = await make_conversation(db)
        assert await memory_service.build_researcher_change_notices(
            db, ENTITY, exclude_conversation_id=current.id
        ) == []
        assert anchor.await_count == 1

        anchor.side_effect = RuntimeError("no boundary")
        notices = await memory_service.build_researcher_change_notices(
            db, ENTITY, exclude_conversation_id=current.id
        )
        assert len(notices) == 2
        assert notices[0].startswith("[MEMORY STATUS NOTICE] Could not check")
        assert notices[1].startswith("[MEMORY ARCHIVE NOTICE] Could not check")
        assert all("no boundary" in notice for notice in notices)

    async def test_a_broken_wrapper_still_speaks(self, db, entities_configured, monkeypatch):
        """The wrapper promises never to raise; if it breaks that promise
        the first turn still runs, and says the check failed."""
        monkeypatch.setattr(
            memory_service, "build_researcher_change_notices",
            AsyncMock(side_effect=RuntimeError("promise broken")),
        )
        current = await make_conversation(db)
        session = self._session(current.id)
        await SessionManager()._inject_status_change_notice(session, db)
        [notice] = session.conversation_context
        assert "Could not check for changes the researcher made" in notice["content"]
        assert "promise broken" in notice["content"]
