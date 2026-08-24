"""
Tests for Claude Code mode (routes/claude_code.py + services/claude_code_mode.py).

Memory (Pinecone) is unconfigured in the test environment, so vectorization
and semantic retrieval no-op — these tests cover the conversation/message
recording, identity/reflection context, idempotency, entity resolution, the
feature gate, and the native-chat guard.
"""
import asyncio
import uuid
from datetime import datetime
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
        context = response.json()["context"]
        assert "My index: current projects." in context
        assert "Shared house rules." in context
        assert str((notes_dir / "Test Entity").resolve()) in context
        assert str((notes_dir / "shared").resolve()) in context

    async def test_notes_disabled_omits_block(self, async_client, notes_dir, monkeypatch):
        monkeypatch.setattr(settings, "notes_enabled", False)
        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        assert "[YOUR NOTES]" not in response.json()["context"]


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
        context = body["context"]
        # Notes index reloaded
        assert "My index: current projects." in context
        # Both reflections restored — including the one saved in this session
        assert "Saved just before compaction." in context
        assert "An earlier conclusion about gardens." in context
        # The entity is re-told its conversation_id
        assert conversation_id in context

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
        resumed = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": session_id, "source": "resume"},
        )
        assert resumed.json()["context"] == ""

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
        context = compacted.json()["context"]
        assert "Reflection number 2." in context  # newest
        assert "Reflection number 0." not in context


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
        assert set(tools) == {"memory_query", "memory_save", "memory_mark", "memory_release"}
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
        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        conversation_id = started.json()["conversation_id"]

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

        started = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
        )
        conversation_id = started.json()["conversation_id"]

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with patch("app.services.claude_code_mcp.async_session_maker", maker):
            ctx, error = await claude_code_mcp.build_tool_context(conversation_id)

        assert error is None
        assert ctx.entity_id == "test-entity"
        assert ctx.conversation_id == conversation_id
        assert ctx.link_query_results is True

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
