"""
Tests for Claude Code mode (routes/claude_code.py + services/claude_code_mode.py).

Memory (Pinecone) is unconfigured in the test environment, so vectorization
and semantic retrieval no-op — these tests cover the conversation/message
recording, identity/reflection context, idempotency, entity resolution, the
feature gate, and the native-chat guard.
"""
import uuid

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
    async def test_creates_conversation_with_source_and_session_id(
        self, async_client, db_session
    ):
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
            select(Conversation).where(Conversation.id == body["conversation_id"])
        )
        conversation = result.scalar_one()
        assert conversation.source == ConversationSource.CLAUDE_CODE.value
        assert conversation.external_session_id == session_id
        assert conversation.entity_id == "test-entity"
        assert conversation.title == "Claude Code: my-project"

    async def test_resume_returns_same_conversation_without_context(
        self, async_client
    ):
        session_id = str(uuid.uuid4())
        first = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}
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

        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        body = response.json()
        assert "I keep returning to the idea of continuity." in body["context"]

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

    async def test_empty_cc_conversation_survives_list_cleanup(
        self, async_client, db_session
    ):
        # The list endpoint deletes message-less conversations, but a
        # registered Claude Code session legitimately has none until its
        # first prompt — it must not be swept
        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        conversation_id = started.json()["conversation_id"]

        await async_client.get("/api/conversations/")

        result = await db_session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        assert result.scalar_one_or_none() is not None
