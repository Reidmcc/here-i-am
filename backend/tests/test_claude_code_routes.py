"""
Tests for Claude Code mode (routes/claude_code.py + services/claude_code_mode.py).

Memory (Pinecone) is unconfigured in the test environment, so vectorization
and semantic retrieval no-op — these tests cover the conversation/message
recording, identity/reflection context, idempotency, entity resolution, the
feature gate, and the native-chat guard.
"""
import asyncio
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
    ConversationMemoryLink,
    ConversationSource,
    EntitySetting,
    Message,
    MessageRole,
)

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

TEST_ENTITY_INDEXES = (
    '[{"index_name": "test-entity", "label": "Test Entity", '
    '"description": "Test entity", "llm_provider": "anthropic"}]'
)


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
async def db_session(test_engine) -> AsyncSession:
    async_session = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def cc_mode_enabled(monkeypatch):
    """Enable Claude Code mode and configure a test entity."""
    monkeypatch.setattr(settings, "claude_code_mode_enabled", True)
    monkeypatch.setattr(settings, "pinecone_indexes", TEST_ENTITY_INDEXES)
    # tiktoken fetches its encoding over the network on first use; token
    # counts are incidental to these tests, so stub the counter
    from app.services import llm_service

    monkeypatch.setattr(llm_service, "count_tokens", lambda text, model=None: len(text) // 4)


@pytest.fixture
async def async_client(test_engine, cc_mode_enabled):
    async_session = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


class TestFeatureGate:
    async def test_endpoints_404_when_disabled(self, test_engine, monkeypatch):
        monkeypatch.setattr(settings, "claude_code_mode_enabled", False)
        async_session = async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )

        async def override_get_db():
            async with async_session() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for path, payload in [
                ("/api/claude-code/session-start", {"session_id": "s1"}),
                ("/api/claude-code/retrieve", {"session_id": "s1", "prompt": "hi"}),
                ("/api/claude-code/log-assistant", {"session_id": "s1", "content": "hi"}),
            ]:
                response = await client.post(path, json=payload)
                assert response.status_code == 404
        app.dependency_overrides.clear()


class TestSessionStart:
    async def test_returns_context_without_creating_conversation(
        self, async_client, db_session
    ):
        """Registration is lazy: Claude Desktop fires SessionStart for
        background/utility sessions that never speak, so no row may be
        created until something is recorded."""
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "cwd": "/home/user/my-project"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["created"] is True
        assert body["entity_id"] == "test-entity"
        assert body["entity_label"] == "Test Entity"
        assert "Test Entity" in body["context"]

        result = await db_session.execute(
            select(Conversation).where(
                Conversation.external_session_id == session_id
            )
        )
        assert result.scalar_one_or_none() is None

        # The announced conversation id is deterministic, so a repeat firing
        # (still unrecorded) hands out the same id and the same full context
        again = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "cwd": "/home/user/my-project"},
        )
        assert again.json()["conversation_id"] == body["conversation_id"]
        assert again.json()["created"] is True

    async def test_first_prompt_registers_under_announced_id(
        self, async_client, db_session
    ):
        session_id = str(uuid.uuid4())
        started = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "cwd": "/home/user/my-project"},
        )
        announced_id = started.json()["conversation_id"]

        retrieved = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": session_id,
                "prompt": "hello",
                "cwd": "/home/user/my-project",
            },
        )
        assert retrieved.json()["conversation_id"] == announced_id

        result = await db_session.execute(
            select(Conversation).where(Conversation.id == announced_id)
        )
        conversation = result.scalar_one()
        assert conversation.source == ConversationSource.CLAUDE_CODE.value
        assert conversation.external_session_id == session_id
        assert conversation.entity_id == "test-entity"
        assert conversation.title == "Claude Code: my-project"

    async def test_windows_cwd_yields_project_title(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": session_id,
                "prompt": "hello",
                "cwd": "C:\\Users\\someone\\my-project",
            },
        )
        result = await db_session.execute(
            select(Conversation).where(
                Conversation.id == response.json()["conversation_id"]
            )
        )
        assert result.scalar_one().title == "Claude Code: my-project"

    async def test_resume_returns_same_conversation_without_context(
        self, async_client
    ):
        session_id = str(uuid.uuid4())
        await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        first = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )
        second = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "resume"},
        )
        assert second.status_code == 200
        body = second.json()
        assert body["created"] is False
        assert body["conversation_id"] == first.json()["conversation_id"]
        assert body["context"] == ""
        assert body["bulk_context"] == ""

    async def test_includes_entity_system_prompt(self, async_client, db_session):
        db_session.add(
            EntitySetting(entity_id="test-entity", system_prompt="You enjoy gardens.")
        )
        await db_session.commit()

        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        assert "You enjoy gardens." in response.json()["context"]

    async def test_injects_recent_reflections_with_links(
        self, async_client, db_session
    ):
        other_conversation = Conversation(entity_id="test-entity")
        db_session.add(other_conversation)
        await db_session.commit()
        reflection = Message(
            conversation_id=other_conversation.id,
            role=MessageRole.REFLECTION,
            content="I keep returning to the idea of continuity.",
            speaker_entity_id="test-entity",
        )
        db_session.add(reflection)
        await db_session.commit()

        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        body = response.json()
        # Reflections ride in the bulk block (spilled to a file when large),
        # never the always-inline identity block
        assert "I keep returning to the idea of continuity." in body["bulk_context"]
        assert "I keep returning to the idea of continuity." not in body["context"]

        # No row yet, so no links yet — they are stashed until the first
        # recorded prompt lazily creates the conversation
        result = await db_session.execute(
            select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == body["conversation_id"]
            )
        )
        assert result.scalars().all() == []

        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )
        result = await db_session.execute(
            select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == body["conversation_id"]
            )
        )
        links = result.scalars().all()
        assert [link.message_id for link in links] == [reflection.id]
        # Recency injection must not touch retrieval tracking
        await db_session.refresh(reflection)
        assert reflection.times_retrieved == 0

    async def test_session_reflection_count_follows_recent_reflections_count(
        self, async_client, db_session, monkeypatch
    ):
        """
        With no Claude Code override, session start injects
        RECENT_REFLECTIONS_COUNT reflections, not a hardcoded 3.
        """
        monkeypatch.setattr(settings, "claude_code_session_reflections_count", None)
        monkeypatch.setattr(settings, "recent_reflections_count", 1)
        other_conversation = Conversation(entity_id="test-entity")
        db_session.add(other_conversation)
        await db_session.commit()
        for i in range(3):
            db_session.add(Message(
                conversation_id=other_conversation.id,
                role=MessageRole.REFLECTION,
                content=f"Recency reflection {i}.",
                speaker_entity_id="test-entity",
                created_at=datetime(2026, 2, 1 + i),
            ))
        await db_session.commit()

        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        bulk = response.json()["bulk_context"]
        assert "Recency reflection 2." in bulk  # newest
        assert "Recency reflection 1." not in bulk
        assert "Recency reflection 0." not in bulk

    async def test_session_reflection_count_override_wins(
        self, async_client, db_session, monkeypatch
    ):
        """CLAUDE_CODE_SESSION_REFLECTIONS_COUNT still overrides the native knob."""
        monkeypatch.setattr(settings, "claude_code_session_reflections_count", 2)
        monkeypatch.setattr(settings, "recent_reflections_count", 1)
        other_conversation = Conversation(entity_id="test-entity")
        db_session.add(other_conversation)
        await db_session.commit()
        for i in range(3):
            db_session.add(Message(
                conversation_id=other_conversation.id,
                role=MessageRole.REFLECTION,
                content=f"Override reflection {i}.",
                speaker_entity_id="test-entity",
                created_at=datetime(2026, 3, 1 + i),
            ))
        await db_session.commit()

        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        bulk = response.json()["bulk_context"]
        assert "Override reflection 2." in bulk
        assert "Override reflection 1." in bulk
        assert "Override reflection 0." not in bulk

    async def test_unknown_entity_rejected(self, async_client):
        response = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": str(uuid.uuid4()), "entity": "nonexistent"},
        )
        assert response.status_code == 400

    async def test_entity_resolved_by_label_case_insensitive(self, async_client):
        response = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": str(uuid.uuid4()), "entity": "test entity"},
        )
        assert response.status_code == 200
        assert response.json()["entity_id"] == "test-entity"


class TestRetrieve:
    async def test_persists_human_message(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "What did we discuss about gardens?"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["human_message_id"] is not None
        assert body["memories_retrieved"] == 0  # Pinecone unconfigured

        result = await db_session.execute(
            select(Message).where(Message.id == body["human_message_id"])
        )
        message = result.scalar_one()
        assert message.role == MessageRole.HUMAN
        assert message.content == "What did we discuss about gardens?"
        assert message.conversation_id == body["conversation_id"]

    async def test_creates_conversation_if_session_unseen(
        self, async_client, db_session
    ):
        # No prior session-start (e.g. backend restarted mid-session)
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )
        assert response.status_code == 200
        result = await db_session.execute(
            select(Conversation).where(
                Conversation.external_session_id == session_id
            )
        )
        assert result.scalar_one().source == ConversationSource.CLAUDE_CODE.value

    async def test_bare_slash_command_ignored(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "/compact"},
        )
        body = response.json()
        assert body["human_message_id"] is None
        assert body["context"] == ""

        result = await db_session.execute(
            select(Message).join(
                Conversation, Conversation.id == Message.conversation_id
            ).where(Conversation.external_session_id == session_id)
        )
        assert result.scalars().all() == []

    async def test_record_nothing_tick_still_counts_sibling_reflections(
        self, async_client, db_session
    ):
        """A wakeup tick the hook dropped (issue #318) arrives as an empty
        prompt. Nothing may be recorded, but the mailbox count still runs —
        a loop session can go hours on ticks alone and must still learn of
        sibling reflections."""
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "opening prompt"},
        )
        conversation_id = response.json()["conversation_id"]
        result = await db_session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one()

        sibling = Conversation(entity_id="test-entity")
        db_session.add(sibling)
        await db_session.commit()
        db_session.add(Message(
            conversation_id=sibling.id,
            role=MessageRole.REFLECTION,
            content="Sibling conclusion.",
            speaker_entity_id="test-entity",
            created_at=conversation.created_at + timedelta(seconds=5),
        ))
        await db_session.commit()

        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": ""},
        )
        body = response.json()
        assert body["human_message_id"] is None
        assert body["context"] == ""
        assert body["new_sibling_reflections"] == 1

        result = await db_session.execute(
            select(Message).where(Message.conversation_id == conversation_id)
        )
        contents = [m.content for m in result.scalars().all()]
        assert contents == ["opening prompt"]


class TestPeerMessages:
    """
    Inter-session messages (issue #312 phase 2): SendMessage deliveries the
    UserPromptSubmit hook extracts from the prompt channel arrive as
    peer_messages and are recorded under the entity's own name — an
    ASSISTANT row with sibling_session marking the sender — never as the
    human's words.
    """

    async def test_pure_delivery_records_sibling_row_not_human(
        self, async_client, db_session
    ):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": session_id,
                "prompt": "",
                "peer_messages": [
                    {"content": "Hello, Workshop. The porch specced it.",
                     "sender": "Porch chat"},
                ],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["human_message_id"] is None
        assert len(body["peer_message_ids"]) == 1

        result = await db_session.execute(
            select(Message).where(Message.id == body["peer_message_ids"][0])
        )
        message = result.scalar_one()
        assert message.role == MessageRole.ASSISTANT
        assert message.sibling_session == "Porch chat"
        assert message.content == "Hello, Workshop. The porch specced it."
        assert message.conversation_id == body["conversation_id"]

    async def test_mixed_prompt_records_human_and_sibling_separately(
        self, async_client, db_session
    ):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": session_id,
                "prompt": "Here's what arrived — thoughts?",
                "peer_messages": [
                    {"content": "peer words", "sender": "Porch chat"},
                ],
            },
        )
        body = response.json()
        assert body["human_message_id"] is not None
        assert len(body["peer_message_ids"]) == 1

        human = (await db_session.execute(
            select(Message).where(Message.id == body["human_message_id"])
        )).scalar_one()
        assert human.role == MessageRole.HUMAN
        assert human.sibling_session is None
        assert human.content == "Here's what arrived — thoughts?"

        peer = (await db_session.execute(
            select(Message).where(Message.id == body["peer_message_ids"][0])
        )).scalar_one()
        assert peer.role == MessageRole.ASSISTANT
        assert peer.sibling_session == "Porch chat"

    async def test_peer_message_vectorized_with_sibling_role(
        self, async_client
    ):
        session_id = str(uuid.uuid4())
        with patch("app.services.claude_code_mode.memory_service") as mock_memory:
            mock_memory.is_configured.return_value = True
            mock_memory.store_memory = AsyncMock(return_value=True)
            # retrieve_for_prompt runs against the same (mocked) service
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[])
            response = await async_client.post(
                "/api/claude-code/retrieve",
                json={
                    "session_id": session_id,
                    "prompt": "",
                    "peer_messages": [
                        {"content": "letter text", "sender": "Porch chat"},
                    ],
                },
            )
        assert response.status_code == 200
        # The human-corpus filter keys on role metadata, so the vectorized
        # copy must never carry role="human" — or "assistant", which would
        # make the letter indistinguishable from this session's own voice
        store_call = mock_memory.store_memory.await_args_list[0]
        assert store_call.kwargs["role"] == "sibling"
        assert store_call.kwargs["sibling_session"] == "Porch chat"

    async def test_unnamed_sender_still_marked_as_sibling(
        self, async_client, db_session
    ):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": session_id,
                "prompt": "",
                "peer_messages": [{"content": "anonymous knock"}],
            },
        )
        body = response.json()
        message = (await db_session.execute(
            select(Message).where(Message.id == body["peer_message_ids"][0])
        )).scalar_one()
        # NULL means "not an inter-session message", so a missing sender
        # must still produce a non-NULL marker
        assert message.sibling_session == "unknown session"

    async def test_blank_peer_content_alone_records_nothing(
        self, async_client, db_session
    ):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": session_id,
                "prompt": "",
                "peer_messages": [{"content": "   ", "sender": "Porch chat"}],
            },
        )
        body = response.json()
        assert body["human_message_id"] is None
        assert body["peer_message_ids"] == []

        result = await db_session.execute(
            select(Message).join(
                Conversation, Conversation.id == Message.conversation_id
            ).where(Conversation.external_session_id == session_id)
        )
        assert result.scalars().all() == []

    async def test_bare_slash_prompt_with_letter_records_only_the_letter(
        self, async_client, db_session
    ):
        # The slash-command skip is about harness input from the human's
        # channel; a sibling's letter riding alongside is still conversation
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": session_id,
                "prompt": "/compact",
                "peer_messages": [
                    {"content": "letter text", "sender": "Porch chat"},
                ],
            },
        )
        body = response.json()
        assert body["human_message_id"] is None
        assert len(body["peer_message_ids"]) == 1

    async def test_last_assistant_content_skips_sibling_rows(
        self, async_client, db_session
    ):
        from app.services import claude_code_mode as cc

        session_id = str(uuid.uuid4())
        first = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "opening prompt"},
        )
        conversation_id = first.json()["conversation_id"]
        await async_client.post(
            "/api/claude-code/log-assistant",
            json={"session_id": session_id, "content": "my own last reply"},
        )
        # A letter arrives after the entity's reply
        await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": session_id,
                "prompt": "",
                "peer_messages": [
                    {"content": "sibling letter", "sender": "Porch chat"},
                ],
            },
        )

        # The assistant-side retrieval query must be this session's own
        # voice, not the letter that just arrived
        content = await cc._last_assistant_content(db_session, conversation_id)
        assert content == "my own last reply"


class TestSiblingReflectionsFlag:
    """/retrieve's new_sibling_reflections mailbox counter."""

    async def _start_conversation(self, async_client, db_session):
        """Register a conversation via a first recorded prompt."""
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "opening prompt"},
        )
        body = response.json()
        assert body["new_sibling_reflections"] == 0
        result = await db_session.execute(
            select(Conversation).where(Conversation.id == body["conversation_id"])
        )
        return session_id, result.scalar_one()

    async def _add_reflection(self, db_session, conversation_id, content, **kwargs):
        message = Message(
            conversation_id=conversation_id,
            role=MessageRole.REFLECTION,
            content=content,
            speaker_entity_id="test-entity",
            **kwargs,
        )
        db_session.add(message)
        await db_session.commit()
        return message

    async def test_counts_only_new_unlinked_sibling_reflections(
        self, async_client, db_session
    ):
        session_id, conversation = await self._start_conversation(
            async_client, db_session
        )
        sibling = Conversation(entity_id="test-entity")
        db_session.add(sibling)
        await db_session.commit()

        after = conversation.created_at + timedelta(seconds=5)
        before = conversation.created_at - timedelta(days=1)
        counted = await self._add_reflection(
            db_session, sibling.id, "Sibling conclusion.", created_at=after
        )
        # Predates this conversation: not new mail
        await self._add_reflection(
            db_session, sibling.id, "Old conclusion.", created_at=before
        )
        # Released: withdrawn from retrieval, so not counted either
        await self._add_reflection(
            db_session, sibling.id, "Released conclusion.",
            created_at=after, memory_status="released",
        )

        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "second prompt"},
        )
        assert response.json()["new_sibling_reflections"] == 1

        # Linking the reflection into this conversation (what session-start
        # injection and recent-mode memory_query do) clears the flag
        db_session.add(ConversationMemoryLink(
            conversation_id=conversation.id,
            message_id=counted.id,
            entity_id="test-entity",
        ))
        await db_session.commit()
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "third prompt"},
        )
        assert response.json()["new_sibling_reflections"] == 0

    async def test_own_conversation_reflections_not_counted(
        self, async_client, db_session
    ):
        session_id, conversation = await self._start_conversation(
            async_client, db_session
        )
        await self._add_reflection(
            db_session, conversation.id, "Saved right here.",
            created_at=conversation.created_at + timedelta(seconds=5),
        )
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "next prompt"},
        )
        assert response.json()["new_sibling_reflections"] == 0

    async def test_pre_compaction_links_count_as_unread_again(
        self, async_client, db_session
    ):
        """A sibling reflection pulled in before a compaction survives only
        in the summary afterwards, so it flags as unread mail again."""
        session_id, conversation = await self._start_conversation(
            async_client, db_session
        )
        sibling = Conversation(entity_id="test-entity")
        db_session.add(sibling)
        await db_session.commit()
        pulled = await self._add_reflection(
            db_session, sibling.id, "Pulled before compaction.",
            created_at=conversation.created_at + timedelta(seconds=5),
        )
        db_session.add(ConversationMemoryLink(
            conversation_id=conversation.id,
            message_id=pulled.id,
            entity_id="test-entity",
            retrieved_at=conversation.created_at + timedelta(seconds=10),
        ))
        # The compaction postdates the link
        conversation.last_compacted_at = (
            conversation.created_at + timedelta(seconds=20)
        )
        await db_session.commit()

        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "post-compaction prompt"},
        )
        assert response.json()["new_sibling_reflections"] == 1


class TestLogAssistant:
    async def test_persists_assistant_message(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/log-assistant",
            json={"session_id": session_id, "content": "The tests pass now."},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["deduplicated"] is False

        result = await db_session.execute(
            select(Message).where(Message.id == body["message_id"])
        )
        message = result.scalar_one()
        assert message.role == MessageRole.ASSISTANT
        assert message.content == "The tests pass now."

    async def test_idempotent_on_message_uuid(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        message_uuid = str(uuid.uuid4())
        payload = {
            "session_id": session_id,
            "content": "Once only.",
            "message_uuid": message_uuid,
        }
        first = await async_client.post("/api/claude-code/log-assistant", json=payload)
        second = await async_client.post("/api/claude-code/log-assistant", json=payload)
        assert first.json()["deduplicated"] is False
        assert second.json()["deduplicated"] is True
        assert second.json()["message_id"] == message_uuid

        result = await db_session.execute(
            select(Message).where(Message.id == message_uuid)
        )
        assert len(result.scalars().all()) == 1

    async def test_empty_content_not_persisted(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/log-assistant",
            json={"session_id": session_id, "content": "   "},
        )
        assert response.json()["message_id"] is None

        result = await db_session.execute(
            select(Message).join(
                Conversation, Conversation.id == Message.conversation_id
            ).where(Conversation.external_session_id == session_id)
        )
        assert result.scalars().all() == []


class TestNativeChatGuard:
    async def test_chat_send_rejects_claude_code_conversation(
        self, async_client, db_session
    ):
        conversation = Conversation(
            entity_id="test-entity",
            source=ConversationSource.CLAUDE_CODE.value,
            external_session_id=str(uuid.uuid4()),
        )
        db_session.add(conversation)
        await db_session.commit()

        response = await async_client.post(
            "/api/chat/send",
            json={"conversation_id": conversation.id, "message": "hello"},
        )
        assert response.status_code == 409
        assert "Claude Code" in response.json()["detail"]


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    """Point the notes service at a temp tree with entity + shared indexes."""
    from app.services.notes_service import notes_service

    monkeypatch.setattr(notes_service, "_base_dir", tmp_path)
    entity_dir = tmp_path / "Test Entity"
    entity_dir.mkdir(parents=True)
    (entity_dir / "index.md").write_text("My index: current projects.", encoding="utf-8")
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    (shared_dir / "index.md").write_text("Shared house rules.", encoding="utf-8")
    return tmp_path


class TestSessionStartNotes:
    async def test_fresh_session_includes_notes_paths_and_indexes(
        self, async_client, notes_dir
    ):
        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        body = response.json()
        # Paths belong to the always-inline block; index contents to the bulk
        # block (spilled to a file when oversized)
        assert str((notes_dir / "Test Entity").resolve()) in body["context"]
        assert str((notes_dir / "shared").resolve()) in body["context"]
        assert "My index: current projects." in body["bulk_context"]
        assert "Shared house rules." in body["bulk_context"]
        assert "My index: current projects." not in body["context"]

    async def test_notes_disabled_omits_block(self, async_client, notes_dir, monkeypatch):
        monkeypatch.setattr(settings, "notes_enabled", False)
        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        body = response.json()
        assert "[YOUR NOTES]" not in body["context"]
        assert "[NOTES INDEX" not in body["bulk_context"]


class TestPostCompact:
    async def test_compact_reinjects_notes_and_reflections(
        self, async_client, db_session, notes_dir
    ):
        # A reflection from an earlier conversation (linked at session start)
        other_conversation = Conversation(entity_id="test-entity")
        db_session.add(other_conversation)
        await db_session.commit()
        earlier_reflection = Message(
            conversation_id=other_conversation.id,
            role=MessageRole.REFLECTION,
            content="An earlier conclusion about gardens.",
            speaker_entity_id="test-entity",
        )
        db_session.add(earlier_reflection)
        await db_session.commit()

        session_id = str(uuid.uuid4())
        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        conversation_id = started.json()["conversation_id"]
        # First prompt registers the conversation (lazy registration)
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )

        # A reflection saved DURING this session (pre-compaction save)
        session_reflection = Message(
            conversation_id=conversation_id,
            role=MessageRole.REFLECTION,
            content="Saved just before compaction.",
            speaker_entity_id="test-entity",
        )
        db_session.add(session_reflection)
        await db_session.commit()

        compacted = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "compact"},
        )
        body = compacted.json()
        assert body["created"] is False
        bulk = body["bulk_context"]
        # Notes index reloaded
        assert "My index: current projects." in bulk
        # Both reflections restored — including the one saved in this session
        assert "Saved just before compaction." in bulk
        assert "An earlier conclusion about gardens." in bulk
        # The entity is re-told its conversation_id, in the inline block
        assert conversation_id in body["context"]

        # No duplicate links: one per reflection across start + compact
        result = await db_session.execute(
            select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == conversation_id
            )
        )
        linked_ids = sorted(link.message_id for link in result.scalars().all())
        assert linked_ids == sorted([earlier_reflection.id, session_reflection.id])

    async def test_plain_resume_still_gets_empty_context(self, async_client, notes_dir):
        session_id = str(uuid.uuid4())
        await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )
        resumed = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "resume"},
        )
        assert resumed.json()["context"] == ""
        assert resumed.json()["bulk_context"] == ""

    async def test_resume_of_unrecorded_session_reserves_full_context(
        self, async_client
    ):
        """A resume for a session with no row (it never spoke, or it ran
        while the backend was down) re-serves the full identity block —
        arriving twice beats never arriving."""
        session_id = str(uuid.uuid4())
        resumed = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "resume"},
        )
        assert resumed.json()["created"] is True
        assert "Test Entity" in resumed.json()["context"]

    async def test_compact_on_unregistered_session_registers_it(
        self, async_client, db_session, notes_dir
    ):
        """source "compact" implies a session with recorded history; if the
        row is missing anyway, the compact registers it so the post-compact
        block's reflection links have a home."""
        session_id = str(uuid.uuid4())
        compacted = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "compact"},
        )
        body = compacted.json()
        assert body["created"] is False
        assert body["conversation_id"] in body["context"]
        assert "My index: current projects." in body["bulk_context"]

        result = await db_session.execute(
            select(Conversation).where(
                Conversation.external_session_id == session_id
            )
        )
        assert result.scalar_one().id == body["conversation_id"]

    async def test_compact_stamps_last_compacted_at(
        self, async_client, db_session, notes_dir
    ):
        """The compact session-start stamps the retrieval eligibility
        boundary, and a later compact advances it."""
        session_id = str(uuid.uuid4())
        await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )

        await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "compact"},
        )
        db_session.expire_all()
        result = await db_session.execute(
            select(Conversation).where(
                Conversation.external_session_id == session_id
            )
        )
        conversation = result.scalar_one()
        first_boundary = conversation.last_compacted_at
        assert first_boundary is not None

        await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "compact"},
        )
        db_session.expire_all()
        result = await db_session.execute(
            select(Conversation).where(
                Conversation.external_session_id == session_id
            )
        )
        assert result.scalar_one().last_compacted_at > first_boundary

    async def test_reinjection_refreshes_pre_compaction_links(
        self, async_client, db_session, notes_dir
    ):
        """A re-shown reflection whose link predates the compaction gets its
        link timestamp bumped past the new boundary — otherwise dedup would
        treat the just-injected reflection as out of view (and eligible for
        immediate re-retrieval). Still exactly one link per reflection."""
        other_conversation = Conversation(entity_id="test-entity")
        db_session.add(other_conversation)
        await db_session.commit()
        reflection = Message(
            conversation_id=other_conversation.id,
            role=MessageRole.REFLECTION,
            content="An earlier conclusion.",
            speaker_entity_id="test-entity",
        )
        db_session.add(reflection)
        await db_session.commit()
        reflection_id = reflection.id

        session_id = str(uuid.uuid4())
        await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )
        from app.services import claude_code_mode as cc_mode
        conversation_id = cc_mode.conversation_id_for_session(session_id)

        # Backdate the session-start injection's link (as if the session ran
        # for a long time before compacting)
        result = await db_session.execute(
            select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == conversation_id
            )
        )
        link = result.scalar_one()
        link.retrieved_at = datetime(2026, 1, 1)
        await db_session.commit()

        await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "compact"},
        )
        db_session.expire_all()
        result = await db_session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        boundary = result.scalar_one().last_compacted_at
        result = await db_session.execute(
            select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == conversation_id
            )
        )
        links = result.scalars().all()
        assert len(links) == 1
        assert links[0].message_id == reflection_id
        assert links[0].retrieved_at > boundary

    async def test_post_compact_reflection_count_knob(
        self, async_client, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "claude_code_post_compact_reflections_count", 1)
        other_conversation = Conversation(entity_id="test-entity")
        db_session.add(other_conversation)
        await db_session.commit()
        for i in range(3):
            db_session.add(Message(
                conversation_id=other_conversation.id,
                role=MessageRole.REFLECTION,
                content=f"Reflection number {i}.",
                speaker_entity_id="test-entity",
                created_at=datetime(2026, 1, 1 + i),
            ))
        await db_session.commit()

        session_id = str(uuid.uuid4())
        await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        compacted = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "compact"},
        )
        bulk = compacted.json()["bulk_context"]
        assert "Reflection number 2." in bulk  # newest
        assert "Reflection number 0." not in bulk


class TestRetrievalStatus:
    """/retrieve reports whether automatic retrieval happened (issue #326),
    so the hook can stamp an empty result instead of staying silent: "ran"
    with nothing found, "skipped" (nothing to query), "unconfigured", or
    "failed" — the last without losing the rows already recorded."""

    @staticmethod
    def _configured_service(mock_memory, search=None, linked=None, full=None):
        mock_memory.is_configured.return_value = True
        mock_memory.store_memory = AsyncMock(return_value=True)
        mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
        mock_memory.get_retrieved_ids_for_conversation = AsyncMock(
            return_value=linked or set()
        )
        mock_memory.search_memories = AsyncMock(return_value=search or [])
        mock_memory.get_full_memory_content = AsyncMock(return_value=full)
        mock_memory.update_retrieval_count = AsyncMock()

    async def test_unconfigured_memory_is_reported_as_such(self, async_client):
        # Pinecone is unconfigured in the test environment: no search ran
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": str(uuid.uuid4()), "prompt": "hello"},
        )
        body = response.json()
        assert body["retrieval_status"] == "unconfigured"
        assert body["memories_retrieved"] == 0
        assert body["human_message_id"] is not None  # still recorded

    async def test_search_that_finds_nothing_is_ran_not_skipped(self, async_client):
        with patch("app.services.claude_code_mode.memory_service") as mock_memory:
            self._configured_service(mock_memory, search=[])
            response = await async_client.post(
                "/api/claude-code/retrieve",
                json={"session_id": str(uuid.uuid4()), "prompt": "hello"},
            )
        body = response.json()
        assert body["retrieval_status"] == "ran"
        assert body["memories_retrieved"] == 0
        assert body["already_in_context"] == 0
        assert body["context"] == ""

    async def test_matches_already_in_context_are_counted(self, async_client):
        # One candidate matches, but it is already linked into this
        # conversation: suppressed without backfill, and the response says
        # so — "0 new" is a different fact from "nothing matched"
        candidate = {"id": "mem-1", "score": 0.9, "conversation_id": "elsewhere"}
        full = {
            "id": "mem-1",
            "content": "a remembered thing",
            "created_at": datetime.utcnow() - timedelta(days=1),
            "last_retrieved_at": None,
            "times_retrieved": 0,
            "role": "human",
            "memory_status": None,
            "source": "native",
        }
        with patch("app.services.claude_code_mode.memory_service") as mock_memory:
            self._configured_service(
                mock_memory, search=[candidate], linked={"mem-1"}, full=full
            )
            response = await async_client.post(
                "/api/claude-code/retrieve",
                json={"session_id": str(uuid.uuid4()), "prompt": "hello"},
            )
        body = response.json()
        assert body["retrieval_status"] == "ran"
        assert body["memories_retrieved"] == 0
        assert body["already_in_context"] == 1
        assert body["context"] == ""
        mock_memory.update_retrieval_count.assert_not_awaited()

    async def test_record_nothing_path_is_skipped(self, async_client):
        for prompt in ("", "/compact"):
            response = await async_client.post(
                "/api/claude-code/retrieve",
                json={"session_id": str(uuid.uuid4()), "prompt": prompt},
            )
            body = response.json()
            assert body["retrieval_status"] == "skipped", prompt
            assert body["human_message_id"] is None

    async def test_letter_only_turn_runs_retrieval(self, async_client):
        # A sibling's letter is a query in its own right, not a skip
        with patch("app.services.claude_code_mode.memory_service") as mock_memory:
            self._configured_service(mock_memory, search=[])
            response = await async_client.post(
                "/api/claude-code/retrieve",
                json={
                    "session_id": str(uuid.uuid4()),
                    "prompt": "",
                    "peer_messages": [{"content": "letter text", "sender": "Porch"}],
                },
            )
        assert response.json()["retrieval_status"] == "ran"
        assert mock_memory.search_memories.await_count >= 1

    async def test_retrieval_failure_is_reported_not_raised(
        self, async_client, db_session
    ):
        # The prompt is committed before the search runs; a 500 here would
        # make the hook's unreachable-backend notice claim it was never
        # recorded. The route reports the failure in the response instead.
        session_id = str(uuid.uuid4())
        with patch("app.services.claude_code_mode.memory_service") as mock_memory:
            self._configured_service(mock_memory)
            mock_memory.search_memories = AsyncMock(
                side_effect=RuntimeError("pinecone down")
            )
            response = await async_client.post(
                "/api/claude-code/retrieve",
                json={"session_id": session_id, "prompt": "hello"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["retrieval_status"] == "failed"
        assert "RuntimeError: pinecone down" in body["retrieval_error"]
        assert body["memories_retrieved"] == 0
        assert body["context"] == ""
        assert body["human_message_id"] is not None
        row = (await db_session.execute(
            select(Message).where(Message.id == body["human_message_id"])
        )).scalar_one()
        assert row.content == "hello"


class TestRecordingVerification:
    """Hook-chosen row ids (issue #326): /retrieve honors a well-formed
    message_id, reuses an existing row under it (a retried call never
    records twice), and /recorded says which ids exist for the session —
    the hook's way of telling "recorded, then failed" from "not recorded"
    without inferring it from an error."""

    async def test_hook_chosen_message_id_is_honored(self, async_client):
        wanted = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": str(uuid.uuid4()), "prompt": "hello", "message_id": wanted},
        )
        assert response.json()["human_message_id"] == wanted

    async def test_retry_with_same_id_records_once(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        wanted = str(uuid.uuid4())
        payload = {"session_id": session_id, "prompt": "hello", "message_id": wanted}
        first = await async_client.post("/api/claude-code/retrieve", json=payload)
        second = await async_client.post("/api/claude-code/retrieve", json=payload)
        assert first.json()["human_message_id"] == wanted
        assert second.json()["human_message_id"] == wanted
        assert second.json()["retrieval_status"] in ("ran", "unconfigured")
        rows = (await db_session.execute(
            select(Message).where(Message.role == MessageRole.HUMAN, Message.content == "hello")
        )).scalars().all()
        assert len(rows) == 1

    async def test_malformed_message_id_gets_a_generated_one(self, async_client):
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": str(uuid.uuid4()), "prompt": "hello", "message_id": "not-a-uuid"},
        )
        got = response.json()["human_message_id"]
        assert got != "not-a-uuid"
        assert uuid.UUID(got)

    async def test_peer_message_id_honored_and_idempotent(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        wanted = str(uuid.uuid4())
        payload = {
            "session_id": session_id,
            "prompt": "",
            "peer_messages": [
                {"content": "letter", "sender": "Porch", "message_id": wanted}
            ],
        }
        first = await async_client.post("/api/claude-code/retrieve", json=payload)
        second = await async_client.post("/api/claude-code/retrieve", json=payload)
        assert first.json()["peer_message_ids"] == [wanted]
        assert second.json()["peer_message_ids"] == [wanted]
        rows = (await db_session.execute(
            select(Message).where(Message.sibling_session == "Porch")
        )).scalars().all()
        assert len(rows) == 1

    async def test_recorded_reports_ids_scoped_to_the_session(self, async_client):
        session_id = str(uuid.uuid4())
        landed = str(uuid.uuid4())
        never = str(uuid.uuid4())
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello", "message_id": landed},
        )
        response = await async_client.post(
            "/api/claude-code/recorded",
            json={"session_id": session_id, "message_ids": [landed, never]},
        )
        assert response.status_code == 200
        assert response.json() == {"recorded": [landed], "missing": [never]}

        # The same id asked about under another session reads as missing
        other = await async_client.post(
            "/api/claude-code/recorded",
            json={"session_id": str(uuid.uuid4()), "message_ids": [landed]},
        )
        assert other.json() == {"recorded": [], "missing": [landed]}

    async def test_recorded_tolerates_garbage_ids(self, async_client):
        response = await async_client.post(
            "/api/claude-code/recorded",
            json={"session_id": str(uuid.uuid4()), "message_ids": ["nope", ""]},
        )
        assert response.json() == {"recorded": [], "missing": ["nope", ""]}

    async def test_recorded_creates_no_conversation(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        await async_client.post(
            "/api/claude-code/recorded",
            json={"session_id": session_id, "message_ids": [str(uuid.uuid4())]},
        )
        rows = (await db_session.execute(
            select(Conversation).where(Conversation.external_session_id == session_id)
        )).scalars().all()
        assert rows == []


class TestRetrievalCompactionBoundary:
    """retrieve_for_prompt threads last_compacted_at into search and dedup,
    so pre-compaction state stops counting as in-context."""

    async def test_retrieve_for_prompt_passes_the_boundary(
        self, cc_mode_enabled, db_session
    ):
        from app.services import claude_code_mode as cc_mode

        boundary = datetime(2026, 8, 25, 12, 0, 0)
        conversation = Conversation(
            entity_id="test-entity",
            source=ConversationSource.CLAUDE_CODE.value,
            external_session_id=str(uuid.uuid4()),
            last_compacted_at=boundary,
        )
        db_session.add(conversation)
        await db_session.commit()

        entity = cc_mode.resolve_entity(None)
        with patch("app.services.claude_code_mode.memory_service") as mock_ms:
            mock_ms.is_configured.return_value = True
            mock_ms.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_ms.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_ms.search_memories = AsyncMock(return_value=[])

            retrieval = await cc_mode.retrieve_for_prompt(
                db_session, conversation, entity, "a prompt"
            )

        assert (retrieval.context, retrieval.count, retrieval.summary) == ("", 0, "")
        assert retrieval.status == cc_mode.RETRIEVAL_RAN
        assert (
            mock_ms.get_retrieved_ids_for_conversation.call_args.kwargs["linked_after"]
            == boundary
        )
        search_calls = mock_ms.search_memories.call_args_list
        assert search_calls
        for call in search_calls:
            assert call.kwargs["exclude_conversation_id"] == conversation.id
            assert call.kwargs["exclude_conversation_after"] == boundary

    async def test_uncompacted_conversation_passes_no_boundary(
        self, cc_mode_enabled, db_session
    ):
        from app.services import claude_code_mode as cc_mode

        conversation = Conversation(
            entity_id="test-entity",
            source=ConversationSource.CLAUDE_CODE.value,
            external_session_id=str(uuid.uuid4()),
        )
        db_session.add(conversation)
        await db_session.commit()

        entity = cc_mode.resolve_entity(None)
        with patch("app.services.claude_code_mode.memory_service") as mock_ms:
            mock_ms.is_configured.return_value = True
            mock_ms.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_ms.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_ms.search_memories = AsyncMock(return_value=[])

            await cc_mode.retrieve_for_prompt(
                db_session, conversation, entity, "a prompt"
            )

        assert (
            mock_ms.get_retrieved_ids_for_conversation.call_args.kwargs["linked_after"]
            is None
        )
        for call in mock_ms.search_memories.call_args_list:
            assert call.kwargs["exclude_conversation_after"] is None


class TestNotesSync:
    @pytest.fixture
    def sync_spy(self, monkeypatch):
        """Configure memory + record background sync_entity_notes calls."""
        from app.routes import claude_code as cc_routes

        calls = []

        async def fake_sync(label):
            calls.append(label)
            return {"indexed": 0, "removed": 0, "unchanged": 0, "errors": []}

        monkeypatch.setattr(
            cc_routes.memory_service, "is_configured", lambda entity_id=None: True
        )
        monkeypatch.setattr(
            cc_routes.notes_vector_service, "sync_entity_notes", fake_sync
        )
        return calls

    async def _drain_background(self):
        for _ in range(3):
            await asyncio.sleep(0)

    async def test_session_end_runs_final_sync(self, async_client, sync_spy):
        session_id = str(uuid.uuid4())
        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )
        await self._drain_background()
        sync_spy.clear()

        response = await async_client.post(
            "/api/claude-code/session-end", json={"session_id": session_id}
        )
        body = response.json()
        assert body["notes_sync_started"] is True
        assert body["conversation_id"] == started.json()["conversation_id"]

        await self._drain_background()
        assert sync_spy == ["Test Entity"]

    async def test_every_recorded_prompt_triggers_sync(self, async_client, sync_spy):
        """Sessions may never formally end, so freshness rides on prompts."""
        session_id = str(uuid.uuid4())
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "first"},
        )
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "second"},
        )
        await self._drain_background()
        assert sync_spy == ["Test Entity", "Test Entity"]

    async def test_session_start_triggers_sync(self, async_client, sync_spy):
        await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        await self._drain_background()
        assert sync_spy == ["Test Entity"]

    async def test_no_sync_when_memory_unconfigured(self, async_client):
        response = await async_client.post(
            "/api/claude-code/session-end", json={"session_id": str(uuid.uuid4())}
        )
        body = response.json()
        assert body["notes_sync_started"] is False
        # Unseen session: no conversation is created for a session that never spoke
        assert body["conversation_id"] is None


def _rpc(method, request_id=1, params=None):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


class TestMcpEndpoint:
    async def test_disabled_returns_404(self, test_engine, monkeypatch):
        monkeypatch.setattr(settings, "claude_code_mode_enabled", False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/mcp", json=_rpc("initialize"))
            assert response.status_code == 404

    async def test_initialize_negotiates_protocol(self, async_client):
        response = await async_client.post(
            "/mcp",
            json=_rpc("initialize", params={
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            }),
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["protocolVersion"] == "2025-03-26"
        assert result["serverInfo"]["name"] == "here-i-am"
        assert "tools" in result["capabilities"]

    async def test_initialize_unknown_version_offers_latest(self, async_client):
        response = await async_client.post(
            "/mcp",
            json=_rpc("initialize", params={"protocolVersion": "1999-01-01"}),
        )
        assert response.json()["result"]["protocolVersion"] == "2025-06-18"

    async def test_notification_gets_202_no_body(self, async_client):
        response = await async_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert response.status_code == 202
        assert response.content == b""

    async def test_tools_list(self, async_client):
        response = await async_client.post("/mcp", json=_rpc("tools/list"))
        tools = {t["name"]: t for t in response.json()["result"]["tools"]}
        assert set(tools) == {
            "memory_query", "memory_save", "memory_mark", "memory_release",
            "declare_room", "retire_room",
        }
        # Every tool takes the MCP-only conversation_id parameter
        for tool in tools.values():
            assert "conversation_id" in tool["inputSchema"]["properties"]
        # memory_save requires it (reflections need a home conversation)
        assert "conversation_id" in tools["memory_save"]["inputSchema"]["required"]

    async def test_unknown_method_and_tool_errors(self, async_client):
        response = await async_client.post("/mcp", json=_rpc("resources/list"))
        assert response.json()["error"]["code"] == -32601

        response = await async_client.post(
            "/mcp", json=_rpc("tools/call", params={"name": "nonexistent", "arguments": {}})
        )
        assert response.json()["error"]["code"] == -32602

    async def test_parse_error(self, async_client):
        response = await async_client.post(
            "/mcp", content=b"{not json", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32700

    async def test_get_and_delete_are_405(self, async_client):
        assert (await async_client.get("/mcp")).status_code == 405
        assert (await async_client.delete("/mcp")).status_code == 405

    async def test_tool_call_without_memory_configured(self, async_client):
        """Pinecone unconfigured: the tool responds with an error result, not
        a protocol error."""
        response = await async_client.post(
            "/mcp",
            json=_rpc("tools/call", params={
                "name": "memory_query", "arguments": {"query": "gardens"},
            }),
        )
        result = response.json()["result"]
        assert result["isError"] is True
        assert "not configured" in result["content"][0]["text"]

    async def test_tool_call_rejects_native_conversation(
        self, async_client, db_session, test_engine
    ):
        native = Conversation(entity_id="test-entity")
        db_session.add(native)
        await db_session.commit()

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with patch("app.services.claude_code_mcp.async_session_maker", maker):
            response = await async_client.post(
                "/mcp",
                json=_rpc("tools/call", params={
                    "name": "memory_save",
                    "arguments": {"content": "a thought", "conversation_id": native.id},
                }),
            )
        result = response.json()["result"]
        assert result["isError"] is True
        assert "native" in result["content"][0]["text"]

    async def test_memory_save_creates_reflection(
        self, async_client, db_session, test_engine
    ):
        session_id = str(uuid.uuid4())
        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        conversation_id = started.json()["conversation_id"]
        # The row the tools act on is created by the first recorded prompt
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with patch("app.services.claude_code_mcp.async_session_maker", maker), \
             patch("app.services.memory_tools.async_session_maker", maker), \
             patch("app.services.memory_tools.memory_service") as mock_memory:
            mock_memory.is_configured.return_value = True
            mock_memory.store_memory = AsyncMock(return_value=True)
            response = await async_client.post(
                "/mcp",
                json=_rpc("tools/call", params={
                    "name": "memory_save",
                    "arguments": {
                        "content": "Continuity holds across modes.",
                        "conversation_id": conversation_id,
                    },
                }),
            )

        result = response.json()["result"]
        assert result["isError"] is False
        assert "Saved reflection" in result["content"][0]["text"]

        rows = await db_session.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.REFLECTION,
            )
        )
        reflection = rows.scalar_one()
        assert reflection.content == "Continuity holds across modes."
        assert reflection.speaker_entity_id == "test-entity"


class TestMcpToolContext:
    async def test_claude_code_conversation_builds_linking_context(
        self, async_client, db_session, test_engine
    ):
        from app.services import claude_code_mcp

        session_id = str(uuid.uuid4())
        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        conversation_id = started.json()["conversation_id"]
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with patch("app.services.claude_code_mcp.async_session_maker", maker):
            ctx, error = await claude_code_mcp.build_tool_context(conversation_id)

        assert error is None
        assert ctx.entity_id == "test-entity"
        assert ctx.conversation_id == conversation_id
        assert ctx.link_query_results is True

    async def test_compacted_conversation_carries_the_boundary(
        self, async_client, db_session, test_engine
    ):
        """After a compaction the tool context excludes only post-boundary
        links, and carries the boundary for the same-conversation search
        exclusion."""
        from app.services import claude_code_mcp

        boundary = datetime(2026, 8, 25, 12, 0, 0)
        conversation = Conversation(
            entity_id="test-entity",
            source=ConversationSource.CLAUDE_CODE.value,
            external_session_id=str(uuid.uuid4()),
            last_compacted_at=boundary,
        )
        db_session.add(conversation)
        await db_session.commit()
        memory = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="A memory.",
        )
        stale_memory = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="Another memory.",
        )
        db_session.add_all([memory, stale_memory])
        await db_session.commit()
        db_session.add(ConversationMemoryLink(
            conversation_id=conversation.id,
            message_id=stale_memory.id,
            entity_id="test-entity",
            retrieved_at=boundary - timedelta(days=1),
        ))
        db_session.add(ConversationMemoryLink(
            conversation_id=conversation.id,
            message_id=memory.id,
            entity_id="test-entity",
            retrieved_at=boundary + timedelta(days=1),
        ))
        await db_session.commit()

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with patch("app.services.claude_code_mcp.async_session_maker", maker):
            ctx, error = await claude_code_mcp.build_tool_context(conversation.id)

        assert error is None
        assert ctx.exclude_conversation_after == boundary
        # Only the post-compaction link still counts as in-context
        assert ctx.extra_exclude_ids == {memory.id}

    async def test_no_conversation_falls_back_to_default_entity(self, cc_mode_enabled):
        from app.services import claude_code_mcp

        ctx, error = await claude_code_mcp.build_tool_context(None)
        assert error is None
        assert ctx.entity_id == "test-entity"
        assert ctx.conversation_id is None
        assert ctx.link_query_results is False

    async def test_unknown_conversation_errors(
        self, async_client, test_engine
    ):
        from app.services import claude_code_mcp

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with patch("app.services.claude_code_mcp.async_session_maker", maker):
            ctx, error = await claude_code_mcp.build_tool_context("does-not-exist")
        assert ctx is None
        assert "No conversation found" in error


class TestMemoryProvenance:
    async def test_full_memory_content_carries_conversation_source(
        self, async_client, db_session, test_engine
    ):
        from app.services.memory_service import MemoryService

        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "a prompt to remember"},
        )
        message_id = response.json()["human_message_id"]

        service = MemoryService()
        mem_data = await service.get_full_memory_content(
            message_id, db_session, use_cache=False
        )
        assert mem_data["source"] == "claude_code"

    async def test_session_start_context_names_conversation_id(
        self, async_client, monkeypatch
    ):
        """With memory configured, the identity block tells the entity which
        conversation_id to pass to the MCP memory tools."""
        from app.services.claude_code_mode import memory_service as cc_memory_service

        monkeypatch.setattr(cc_memory_service, "is_configured", lambda entity_id=None: True)
        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        body = started.json()
        assert body["conversation_id"] in body["context"]
        assert "memory_query" in body["context"]


class TestRetrievalSummary:
    """render_retrieval_summary is the inline stand-in printed when the hook
    spills an oversized retrieval block to a file — one line per memory in
    the marker vocabulary."""

    def test_one_line_per_memory_with_marker_vocabulary(self):
        from app.services.claude_code_mode import render_retrieval_summary

        summary = render_retrieval_summary([
            {
                "id": "abcdef1234567890",
                "content": "First line of the memory.\nSecond line.",
                "created_at": "2026-07-04T19:17:42.717088",
                "role": "assistant",
                "source": "native",
            },
            {
                "id": "1234567890abcdef",
                "content": "y" * 150,
                "created_at": "2026-08-24T16:31:15.788419",
                "role": "reflection",
                "source": "claude_code",
            },
        ])
        lines = summary.splitlines()
        assert "2 memories" in lines[0]
        assert lines[1] == (
            "- abcdef12 (2026-07-04 - originally from you - via Here I Am): "
            "First line of the memory."
        )
        assert lines[2].startswith(
            "- 12345678 (2026-08-24 - a reflection you saved - via Claude Code): "
        )
        # Long first lines are snipped
        assert lines[2].endswith("…")
        assert "y" * 101 not in lines[2]

    def test_single_memory_singular(self):
        from app.services.claude_code_mode import render_retrieval_summary

        summary = render_retrieval_summary([
            {
                "id": "abcd1234efgh5678",
                "content": "A human said this.",
                "created_at": "2026-01-01T00:00:00",
                "role": "human",
                "source": "native",
            },
        ])
        assert "1 memory" in summary
        assert "originally from human" in summary


class TestConversationResponses:
    async def test_list_and_get_expose_source(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        conversation_id = started.json()["conversation_id"]
        await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )

        detail = await async_client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        assert detail.json()["source"] == "claude_code"

        listing = await async_client.get("/api/conversations/")
        assert listing.status_code == 200
        listed = {c["id"]: c for c in listing.json()}
        assert listed[conversation_id]["source"] == "claude_code"

    async def test_fresh_empty_cc_conversation_survives_list_cleanup(
        self, async_client, db_session
    ):
        # The list endpoint deletes message-less conversations, but a
        # freshly registered Claude Code session can legitimately have none
        # (e.g. its only input so far was a bare slash command) — it must
        # not be swept inside the retention window
        session_id = str(uuid.uuid4())
        registered = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "/compact"},
        )
        conversation_id = registered.json()["conversation_id"]

        await async_client.get("/api/conversations/")

        result = await db_session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        assert result.scalar_one_or_none() is not None

    async def test_stale_empty_cc_conversation_is_swept(
        self, async_client, db_session
    ):
        # An empty claude_code row idle past the retention window is an
        # abandoned registration (e.g. from before lazy registration) and
        # gets cleaned up like any other empty conversation
        stale = Conversation(
            entity_id="test-entity",
            source=ConversationSource.CLAUDE_CODE.value,
            external_session_id=str(uuid.uuid4()),
            created_at=datetime.utcnow() - timedelta(days=2),
        )
        db_session.add(stale)
        await db_session.commit()

        await async_client.get("/api/conversations/")

        result = await db_session.execute(
            select(Conversation).where(Conversation.id == stale.id)
        )
        assert result.scalar_one_or_none() is None


class TestLazyRegistration:
    def test_conversation_id_is_deterministic_per_session(self):
        from app.services import claude_code_mode as cc_mode

        session_id = str(uuid.uuid4())
        first = cc_mode.conversation_id_for_session(session_id)
        assert cc_mode.conversation_id_for_session(session_id) == first
        assert cc_mode.conversation_id_for_session(str(uuid.uuid4())) != first
        # Must be a valid uuid string (Conversation.id is String(36))
        assert uuid.UUID(first)

    def test_pending_reflection_links_registry_is_bounded(self, monkeypatch):
        """Background sessions stash and never consume, so the registry must
        not grow without bound over backend uptime."""
        from app.services import claude_code_mode as cc_mode

        monkeypatch.setattr(cc_mode, "_pending_reflection_links", {})
        monkeypatch.setattr(cc_mode, "_PENDING_REFLECTION_LINKS_MAX", 3)
        for i in range(5):
            cc_mode._stash_pending_reflection_links(
                f"conversation-{i}", "test-entity", [f"reflection-{i}"]
            )
        assert len(cc_mode._pending_reflection_links) == 3
        # Oldest entries are the ones evicted
        assert set(cc_mode._pending_reflection_links) == {
            "conversation-2", "conversation-3", "conversation-4",
        }

    async def test_registration_without_stash_records_no_links(
        self, async_client, db_session
    ):
        """A backend restart between session start and first prompt loses
        the stash; registration still succeeds, just without the reflection
        dedup links (duplicated injection at worst, never hidden content)."""
        from app.services import claude_code_mode as cc_mode

        other_conversation = Conversation(entity_id="test-entity")
        db_session.add(other_conversation)
        await db_session.commit()
        db_session.add(Message(
            conversation_id=other_conversation.id,
            role=MessageRole.REFLECTION,
            content="A reflection that predates the restart.",
            speaker_entity_id="test-entity",
        ))
        await db_session.commit()

        session_id = str(uuid.uuid4())
        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
        )
        conversation_id = started.json()["conversation_id"]
        cc_mode._pending_reflection_links.clear()  # simulate restart

        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": session_id, "prompt": "hello"},
        )
        assert response.status_code == 200
        assert response.json()["conversation_id"] == conversation_id

        result = await db_session.execute(
            select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == conversation_id
            )
        )
        assert result.scalars().all() == []


class TestSafeTokenCount:
    """safe_token_count must reach the LLMService singleton, not the submodule.

    `from app.services import llm_service` resolves against the package's
    attributes, which hold the submodule until __init__.py binds the
    instance (this is what broke GET /api/chat/config). Here the failure
    would be silent rather than loud: the function swallows exceptions, so a
    shadowed name turns every recorded message's token_count into NULL with
    only a log line to show for it.
    """

    def test_reaches_the_singleton(self, monkeypatch):
        from app.services import claude_code_mode as cc_mode
        from app.services.llm_service import llm_service as singleton

        monkeypatch.setattr(singleton, "count_tokens", lambda text, model=None: 4242)

        assert cc_mode.safe_token_count("some prompt text") == 4242

    def test_counting_failure_degrades_to_none(self, monkeypatch):
        from app.services import claude_code_mode as cc_mode
        from app.services.llm_service import llm_service as singleton

        def boom(text, model=None):
            raise RuntimeError("tiktoken encoding unavailable")

        monkeypatch.setattr(singleton, "count_tokens", boom)

        assert cc_mode.safe_token_count("some prompt text") is None

    def test_survives_package_attribute_shadowing(self, monkeypatch):
        """The discriminating case: with `app.services.llm_service` still
        bound to the submodule (as it is mid-`__init__.py`), the old
        `from app.services import llm_service` form silently yields None."""
        import importlib

        import app.services as services_pkg
        from app.services import claude_code_mode as cc_mode
        from app.services.llm_service import llm_service as singleton

        # `import app.services.llm_service as m` would itself resolve through
        # the package attribute (i.e. to the singleton); import_module is the
        # one way to get the actual module object.
        llm_module = importlib.import_module("app.services.llm_service")
        assert llm_module is not singleton
        monkeypatch.setattr(services_pkg, "llm_service", llm_module, raising=False)
        monkeypatch.setattr(singleton, "count_tokens", lambda text, model=None: 7)

        assert cc_mode.safe_token_count("some prompt text") == 7


class TestLogAssistantModel:
    """
    The Stop hook carries the transcript entry's model to /log-assistant
    (issue #321); the row records it verbatim, and its absence is NULL.
    """

    async def test_model_recorded_on_the_row(self, async_client, db_session):
        response = await async_client.post(
            "/api/claude-code/log-assistant",
            json={
                "session_id": str(uuid.uuid4()),
                "content": "Attributed.",
                "model": "claude-fable-5-1",
            },
        )
        assert response.status_code == 200
        row = (await db_session.execute(
            select(Message).where(Message.id == response.json()["message_id"])
        )).scalar_one()
        assert row.model == "claude-fable-5-1"

    async def test_missing_or_blank_model_stays_null(self, async_client, db_session):
        for payload in (
            {"session_id": str(uuid.uuid4()), "content": "Older hook."},
            {"session_id": str(uuid.uuid4()), "content": "Blank.", "model": "   "},
        ):
            response = await async_client.post("/api/claude-code/log-assistant", json=payload)
            row = (await db_session.execute(
                select(Message).where(Message.id == response.json()["message_id"])
            )).scalar_one()
            assert row.model is None

    async def test_human_prompt_and_letters_are_never_attributed(self, async_client, db_session):
        session_id = str(uuid.uuid4())
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": session_id,
                "prompt": "A typed prompt.",
                "peer_messages": [{"content": "A letter.", "sender": "Porch"}],
            },
        )
        assert response.status_code == 200
        rows = (await db_session.execute(
            select(Message).join(
                Conversation, Conversation.id == Message.conversation_id
            ).where(Conversation.external_session_id == session_id)
        )).scalars().all()
        assert len(rows) == 2
        assert all(row.model is None for row in rows)

    async def test_mcp_tool_listing_exposes_include_model(self, async_client):
        response = await async_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        tools = {t["name"]: t for t in response.json()["result"]["tools"]}
        prop = tools["memory_query"]["inputSchema"]["properties"]["include_model"]
        assert prop["type"] == "boolean"
        assert prop["default"] is False


class TestInContextReflectionDedup:
    """Issue #328 (merged pool): an already-linked reflection the pull ranks
    highly leaves the candidate pool before the top-k cut, so it holds no
    slot and the next-ranked verbatim memory arrives; an already-linked
    verbatim memory still holds its slot (no backfill), as before. The
    split-pool variant is TestSplitRolePools."""

    @staticmethod
    def _memory(mem_id, role):
        return {
            "id": mem_id,
            "content": f"content of {mem_id}",
            "created_at": datetime.utcnow() - timedelta(days=1),
            "last_retrieved_at": None,
            "times_retrieved": 0,
            "role": role,
            "memory_status": None,
            "source": "native",
        }

    @classmethod
    def _pool(cls, ranked):
        """ranked: [(id, role), ...] best first -> (search hits, full rows)."""
        hits, full = [], {}
        for i, (mem_id, role) in enumerate(ranked):
            hits.append({
                "id": mem_id,
                "score": 0.95 - 0.01 * i,
                "conversation_id": "elsewhere",
                "role": role,
            })
            full[mem_id] = cls._memory(mem_id, role)
        return hits, full

    @staticmethod
    def _configure(mock_memory, hits, full, linked):
        mock_memory.is_configured.return_value = True
        mock_memory.store_memory = AsyncMock(return_value=True)
        mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
        mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set(linked))
        mock_memory.search_memories = AsyncMock(return_value=hits)
        mock_memory.get_full_memory_content = AsyncMock(
            side_effect=lambda mem_id, db: full.get(mem_id)
        )
        mock_memory.update_retrieval_count = AsyncMock()

    @staticmethod
    def _retrieved_ids(mock_memory):
        return [call.args[0] for call in mock_memory.update_retrieval_count.await_args_list]

    async def _retrieve(self, async_client, monkeypatch, ranked, linked):
        monkeypatch.setattr(settings, "retrieval_top_k", 5)
        monkeypatch.setattr(settings, "initial_retrieval_top_k", 5)
        monkeypatch.setattr(settings, "memory_role_balance_enabled", False)
        hits, full = self._pool(ranked)
        with patch("app.services.claude_code_mode.memory_service") as mock_memory:
            self._configure(mock_memory, hits, full, linked)
            response = await async_client.post(
                "/api/claude-code/retrieve",
                json={"session_id": str(uuid.uuid4()), "prompt": "hello"},
            )
            retrieved = self._retrieved_ids(mock_memory)
        assert response.status_code == 200
        return response.json(), retrieved

    async def test_in_context_reflections_do_not_consume_slots(self, async_client, monkeypatch):
        # Top five holds two in-context reflections: five verbatim arrive, not three
        ranked = [
            ("refl-1", "reflection"), ("refl-2", "reflection"),
            ("verb-1", "human"), ("verb-2", "assistant"), ("verb-3", "human"),
            ("verb-4", "assistant"), ("verb-5", "human"),
        ]
        body, retrieved = await self._retrieve(
            async_client, monkeypatch, ranked, linked={"refl-1", "refl-2"}
        )
        assert body["retrieval_status"] == "ran"
        assert body["memories_retrieved"] == 5
        assert retrieved == ["verb-1", "verb-2", "verb-3", "verb-4", "verb-5"]
        assert body["in_context_reflections_skipped"] == 2
        assert body["already_in_context"] == 0
        assert "refl-1" not in body["context"]

    async def test_in_context_verbatim_still_holds_slots(self, async_client, monkeypatch):
        # Top five holds two in-context verbatim memories: three arrive (unchanged)
        ranked = [
            ("verb-1", "human"), ("verb-2", "assistant"), ("verb-3", "human"),
            ("verb-4", "assistant"), ("verb-5", "human"),
            ("verb-6", "assistant"), ("verb-7", "human"),
        ]
        body, retrieved = await self._retrieve(
            async_client, monkeypatch, ranked, linked={"verb-1", "verb-2"}
        )
        assert body["memories_retrieved"] == 3
        assert retrieved == ["verb-3", "verb-4", "verb-5"]
        assert body["already_in_context"] == 2
        assert body["in_context_reflections_skipped"] == 0

    async def test_mixed_case_reports_both_counts(self, async_client, monkeypatch):
        ranked = [
            ("refl-1", "reflection"), ("verb-1", "human"), ("verb-2", "assistant"),
            ("verb-3", "human"), ("verb-4", "assistant"), ("verb-5", "human"),
            ("verb-6", "assistant"),
        ]
        body, retrieved = await self._retrieve(
            async_client, monkeypatch, ranked, linked={"refl-1", "verb-1"}
        )
        # Pool after the reflection leaves: verb-1..verb-6; top five is
        # verb-1..verb-5; verb-1 holds its slot
        assert body["memories_retrieved"] == 4
        assert retrieved == ["verb-2", "verb-3", "verb-4", "verb-5"]
        assert body["already_in_context"] == 1
        assert body["in_context_reflections_skipped"] == 1

    async def test_reflection_not_yet_in_context_is_still_retrieved(self, async_client, monkeypatch):
        # Only *in-context* reflections leave the pool; a fresh one competes as before
        ranked = [
            ("refl-1", "reflection"), ("verb-1", "human"), ("verb-2", "assistant"),
            ("verb-3", "human"), ("verb-4", "assistant"), ("verb-5", "human"),
        ]
        body, retrieved = await self._retrieve(
            async_client, monkeypatch, ranked, linked=set()
        )
        assert body["memories_retrieved"] == 5
        assert retrieved[0] == "refl-1"
        assert body["in_context_reflections_skipped"] == 0

    async def test_all_matches_in_context_reflections_reports_zero_new(self, async_client, monkeypatch):
        ranked = [("refl-1", "reflection"), ("refl-2", "reflection")]
        body, retrieved = await self._retrieve(
            async_client, monkeypatch, ranked, linked={"refl-1", "refl-2"}
        )
        assert body["memories_retrieved"] == 0
        assert body["context"] == ""
        assert retrieved == []
        assert body["in_context_reflections_skipped"] == 2
        assert body["already_in_context"] == 0


class TestSplitRolePools:
    """Issue #335, Claude Code pipeline: with role balance on, the human's
    words and the entity's are searched and ranked as separate pools, both
    queries feeding both pools, and each pool contributes its own top N."""

    @staticmethod
    def _memory(mem_id, role):
        return {
            "id": mem_id,
            "content": f"content of {mem_id}",
            "created_at": datetime.utcnow() - timedelta(days=1),
            "last_retrieved_at": None,
            "times_retrieved": 0,
            "role": role,
            "memory_status": None,
            "source": "native",
        }

    @classmethod
    def _configure(cls, mock_memory, ranked, linked=()):
        from app.services.memory_service import role_matches_filter

        hits = [
            {
                "id": mem_id,
                "score": 0.95 - 0.01 * i,
                "conversation_id": "elsewhere",
                "role": role,
            }
            for i, (mem_id, role) in enumerate(ranked)
        ]
        full = {mem_id: cls._memory(mem_id, role) for mem_id, role in ranked}

        async def search(query, top_k, role_filter=None, **kwargs):
            return [dict(h) for h in hits if role_matches_filter(h["role"], role_filter)]

        mock_memory.is_configured.return_value = True
        mock_memory.store_memory = AsyncMock(return_value=True)
        mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
        mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set(linked))
        mock_memory.search_memories = AsyncMock(side_effect=search)
        mock_memory.get_full_memory_content = AsyncMock(
            side_effect=lambda mem_id, db: full.get(mem_id)
        )
        mock_memory.update_retrieval_count = AsyncMock()

    @staticmethod
    def _settings(monkeypatch, *, balance=True, per_role=3, initial_per_role=None,
                  merged_top_k=5):
        monkeypatch.setattr(settings, "retrieval_top_k", merged_top_k)
        monkeypatch.setattr(settings, "initial_retrieval_top_k", merged_top_k)
        monkeypatch.setattr(settings, "memory_role_balance_enabled", balance)
        monkeypatch.setattr(settings, "retrieval_top_k_per_role", per_role)
        monkeypatch.setattr(
            settings, "initial_retrieval_top_k_per_role",
            per_role if initial_per_role is None else initial_per_role,
        )

    async def _retrieve(self, async_client, ranked, linked=()):
        with patch("app.services.claude_code_mode.memory_service") as mock_memory:
            self._configure(mock_memory, ranked, linked)
            response = await async_client.post(
                "/api/claude-code/retrieve",
                json={"session_id": str(uuid.uuid4()), "prompt": "hello"},
            )
            retrieved = [
                call.args[0] for call in mock_memory.update_retrieval_count.await_args_list
            ]
        assert response.status_code == 200
        return response.json(), retrieved, mock_memory.search_memories

    ENTITY_HEAVY = [
        ("a-1", "assistant"), ("a-2", "assistant"), ("a-3", "assistant"),
        ("a-4", "assistant"), ("a-5", "assistant"),
        ("h-1", "human"), ("h-2", "human"), ("h-3", "human"), ("h-4", "human"),
    ]

    async def test_each_pool_contributes_its_top_n(self, async_client, monkeypatch):
        # Five entity memories outrank every human one: the merged pool
        # would have been all entity (and the old swap could only rescue
        # one); each pool now gives its top three
        self._settings(monkeypatch)
        body, retrieved, _ = await self._retrieve(async_client, self.ENTITY_HEAVY)
        assert body["retrieval_status"] == "ran"
        assert body["memories_retrieved"] == 6
        assert retrieved == ["a-1", "a-2", "a-3", "h-1", "h-2", "h-3"]
        assert "content of h-3" in body["context"]
        assert "content of a-4" not in body["context"]

    async def test_first_turn_uses_the_initial_per_role_knob(self, async_client, monkeypatch):
        self._settings(monkeypatch, per_role=3, initial_per_role=1)
        body, retrieved, _ = await self._retrieve(async_client, self.ENTITY_HEAVY)
        assert retrieved == ["a-1", "h-1"]
        assert body["memories_retrieved"] == 2

    async def test_empty_human_pool_returns_fewer_not_filler(self, async_client, monkeypatch):
        self._settings(monkeypatch)
        entity_only = [(f"a-{i}", "assistant") for i in range(1, 6)]
        body, retrieved, _ = await self._retrieve(async_client, entity_only)
        assert retrieved == ["a-1", "a-2", "a-3"]
        assert body["memories_retrieved"] == 3

    async def test_in_context_reflection_frees_its_pool_slot(self, async_client, monkeypatch):
        self._settings(monkeypatch)
        ranked = [
            ("refl-1", "reflection"), ("a-1", "assistant"), ("a-2", "assistant"),
            ("a-3", "assistant"), ("h-1", "human"), ("h-2", "human"), ("h-3", "human"),
        ]
        body, retrieved, _ = await self._retrieve(async_client, ranked, linked={"refl-1"})
        assert retrieved == ["a-1", "a-2", "a-3", "h-1", "h-2", "h-3"]
        assert body["in_context_reflections_skipped"] == 1
        assert body["already_in_context"] == 0

    async def test_in_context_verbatim_holds_its_pool_slot(self, async_client, monkeypatch):
        self._settings(monkeypatch)
        ranked = [
            ("a-0", "assistant"), ("a-1", "assistant"), ("a-2", "assistant"),
            ("a-3", "assistant"), ("h-1", "human"), ("h-2", "human"), ("h-3", "human"),
        ]
        body, retrieved, _ = await self._retrieve(async_client, ranked, linked={"a-0"})
        # a-0 holds one of the entity pool's three slots (no backfill from
        # a-3); the human pool is unaffected
        assert retrieved == ["a-1", "a-2", "h-1", "h-2", "h-3"]
        assert body["already_in_context"] == 1
        assert body["memories_retrieved"] == 5

    async def test_prompt_only_turn_queries_each_pool_once(self, async_client, monkeypatch):
        self._settings(monkeypatch)
        _, _, search = await self._retrieve(async_client, self.ENTITY_HEAVY)
        calls = sorted((c.kwargs["role_filter"], c.kwargs["query"]) for c in search.await_args_list)
        assert calls == [("ai", "hello"), ("human", "hello")]

    async def test_both_queries_feed_both_pools(self, cc_mode_enabled, db_session, monkeypatch):
        from app.services import claude_code_mode as cc_mode

        self._settings(monkeypatch)
        conversation = Conversation(
            entity_id="test-entity",
            source=ConversationSource.CLAUDE_CODE.value,
            external_session_id=str(uuid.uuid4()),
        )
        db_session.add(conversation)
        await db_session.commit()
        db_session.add(Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="what I said last",
            speaker_entity_id="test-entity",
        ))
        await db_session.commit()

        entity = cc_mode.resolve_entity(None)
        with patch("app.services.claude_code_mode.memory_service") as mock_memory:
            self._configure(mock_memory, self.ENTITY_HEAVY)
            retrieval = await cc_mode.retrieve_for_prompt(
                db_session, conversation, entity, "a new subject"
            )
            calls = sorted(
                (c.kwargs["role_filter"], c.kwargs["query"])
                for c in mock_memory.search_memories.await_args_list
            )
        assert retrieval.count == 6
        assert calls == [
            ("ai", "a new subject"), ("ai", "what I said last"),
            ("human", "a new subject"), ("human", "what I said last"),
        ]

    async def test_role_balance_off_is_a_merged_pool_without_the_swap(
        self, async_client, monkeypatch
    ):
        self._settings(monkeypatch, balance=False)
        body, retrieved, search = await self._retrieve(async_client, self.ENTITY_HEAVY)
        assert retrieved == ["a-1", "a-2", "a-3", "a-4", "a-5"]
        assert body["memories_retrieved"] == 5
        assert [c.kwargs["role_filter"] for c in search.await_args_list] == [None]
