"""
Tests for entity memory agency features:
- pinned memories exempt from age decay (significance)
- released memories excluded from retrieval
- memory ID resolution for memory_mark/memory_release
- self-authored memories (reflections) formatting
- notes chunking for vectorization
- researcher endpoints for viewing/clearing memory status overrides
"""
import pytest
from unittest.mock import patch, AsyncMock
import uuid
from datetime import datetime, timedelta

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db
from app.models import Conversation, Message, MessageRole
from app.routes.memories import calculate_significance as route_significance
from app.services.session_helpers import calculate_significance as helper_significance
from app.services.memory_context import format_memory_as_context_message
from app.services.memory_tools import _resolve_memory_id, set_memory_tool_context, _memory_query
from app.services.notes_vector_service import chunk_note_content


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


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
async def db_session(test_engine):
    async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest.fixture
async def async_client(test_engine):
    async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def _create_conversation_with_message(
    db: AsyncSession,
    entity_id: str = "test-entity",
    memory_status: str = None,
    role: MessageRole = MessageRole.ASSISTANT,
):
    conversation = Conversation(
        id=str(uuid.uuid4()),
        title="Test Conversation",
        entity_id=entity_id,
    )
    db.add(conversation)
    message = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        role=role,
        content="A memory worth keeping",
        memory_status=memory_status,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return conversation, message


class TestPinnedSignificance:
    """Pinned memories are exempt from age-based decay in both formula copies."""

    def test_helper_pinned_skips_age_decay(self):
        old_date = datetime.utcnow() - timedelta(days=120)
        normal = helper_significance(0, old_date, None)
        pinned = helper_significance(0, old_date, None, memory_status="pinned")
        assert pinned > normal
        # With no retrievals and no recency boost, pinned base is exactly 1.0
        assert pinned == pytest.approx(1.0)

    def test_route_pinned_skips_age_decay(self):
        old_date = datetime.utcnow() - timedelta(days=120)
        normal = route_significance(0, old_date, None)
        pinned = route_significance(0, old_date, None, memory_status="pinned")
        assert pinned > normal

    def test_fresh_memory_unaffected_by_pin(self):
        now = datetime.utcnow()
        assert helper_significance(0, now, None) == pytest.approx(
            helper_significance(0, now, None, memory_status="pinned")
        )

    def test_released_status_does_not_change_significance(self):
        # "released" affects retrieval filtering, not the significance formula
        old_date = datetime.utcnow() - timedelta(days=120)
        assert helper_significance(0, old_date, None) == pytest.approx(
            helper_significance(0, old_date, None, memory_status="released")
        )


class TestMemoryIdResolution:
    """ID/prefix resolution for memory_mark and memory_release."""

    @pytest.mark.asyncio
    async def test_resolve_exact_id(self, db_session):
        _, message = await _create_conversation_with_message(db_session)
        resolved, error = await _resolve_memory_id(message.id, db_session, "test-entity")
        assert error is None
        assert resolved.id == message.id

    @pytest.mark.asyncio
    async def test_resolve_prefix(self, db_session):
        _, message = await _create_conversation_with_message(db_session)
        resolved, error = await _resolve_memory_id(message.id[:8], db_session, "test-entity")
        assert error is None
        assert resolved.id == message.id

    @pytest.mark.asyncio
    async def test_resolve_too_short_prefix(self, db_session):
        await _create_conversation_with_message(db_session)
        resolved, error = await _resolve_memory_id("abc", db_session, "test-entity")
        assert resolved is None
        assert "at least 6" in error

    @pytest.mark.asyncio
    async def test_resolve_unknown_id(self, db_session):
        resolved, error = await _resolve_memory_id("ffffff-not-real", db_session, "test-entity")
        assert resolved is None
        assert "No memory found" in error

    @pytest.mark.asyncio
    async def test_resolve_rejects_other_entity(self, db_session):
        _, message = await _create_conversation_with_message(db_session, entity_id="other-entity")
        resolved, error = await _resolve_memory_id(message.id, db_session, "test-entity")
        assert resolved is None
        assert "another entity" in error

    @pytest.mark.asyncio
    async def test_resolve_allows_multi_entity_conversation(self, db_session):
        _, message = await _create_conversation_with_message(db_session, entity_id="multi-entity")
        resolved, error = await _resolve_memory_id(message.id, db_session, "test-entity")
        assert error is None
        assert resolved.id == message.id


class TestReleasedFiltering:
    """Released memories are excluded from memory_query results."""

    @pytest.mark.asyncio
    async def test_memory_query_skips_released(self):
        set_memory_tool_context("test-entity", "conv-1")

        released_id = str(uuid.uuid4())
        normal_id = str(uuid.uuid4())

        def content_for(mem_id, db):
            status = "released" if mem_id == released_id else None
            return {
                "id": mem_id,
                "conversation_id": "conv-2",
                "role": "assistant",
                "content": "memory content",
                "created_at": datetime.utcnow().isoformat(),
                "times_retrieved": 0,
                "last_retrieved_at": None,
                "memory_status": status,
            }

        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.search_memories = AsyncMock(return_value=[
                {"id": released_id, "score": 0.9, "conversation_id": "conv-2"},
                {"id": normal_id, "score": 0.8, "conversation_id": "conv-2"},
            ])
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.get_full_memory_content = AsyncMock(side_effect=content_for)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)

            result = await _memory_query("anything", num_results=5)

        assert normal_id[:8] in result
        assert released_id[:8] not in result
        # Retrieval count updated only for the non-released memory
        updated_ids = [
            call.kwargs["message_id"]
            for call in mock_service.update_retrieval_count.call_args_list
        ]
        assert updated_ids == [normal_id]


class TestReflectionFormatting:
    """Reflections and memory IDs in context-message rendering."""

    def test_memory_marker_includes_short_id(self):
        mem_id = str(uuid.uuid4())
        msg = format_memory_as_context_message(mem_id, "content", "2026-01-01", "assistant")
        assert f"[MEMORY {mem_id[:8]} from 2026-01-01" in msg["content"]
        assert "originally from you" in msg["content"]

    def test_reflection_role_label(self):
        mem_id = str(uuid.uuid4())
        msg = format_memory_as_context_message(mem_id, "content", "2026-01-01", "reflection")
        assert "a reflection you saved" in msg["content"]

    def test_human_role_label(self):
        mem_id = str(uuid.uuid4())
        msg = format_memory_as_context_message(mem_id, "content", "2026-01-01", "human")
        assert "originally from human" in msg["content"]


class TestNotesChunking:
    """Note content chunking for vectorization."""

    def test_empty_content(self):
        assert chunk_note_content("") == []
        assert chunk_note_content("   \n  ") == []

    def test_short_content_single_chunk(self):
        assert chunk_note_content("hello world") == ["hello world"]

    def test_splits_on_paragraphs(self):
        para = "x" * 1500
        content = f"{para}\n\n{para}"
        chunks = chunk_note_content(content, max_chars=2000)
        assert len(chunks) == 2
        assert all(len(c) <= 2000 for c in chunks)

    def test_hard_splits_oversized_lines(self):
        content = "y" * 5000
        chunks = chunk_note_content(content, max_chars=2000)
        assert all(len(c) <= 2000 for c in chunks)
        assert "".join(chunks) == content

    def test_preserves_all_content(self):
        paragraphs = [f"Paragraph {i}: " + "word " * 100 for i in range(10)]
        content = "\n\n".join(paragraphs)
        chunks = chunk_note_content(content, max_chars=1000)
        rejoined = " ".join(chunks)
        for i in range(10):
            assert f"Paragraph {i}:" in rejoined


class TestMemoryStatusRoutes:
    """Researcher endpoints for viewing and clearing memory status overrides."""

    @pytest.mark.asyncio
    async def test_list_overrides_empty(self, async_client):
        response = await async_client.get("/api/memories/overrides")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_overrides_returns_pinned_and_released(self, async_client, test_engine):
        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            await _create_conversation_with_message(session, memory_status="pinned")
            await _create_conversation_with_message(session, memory_status="released")
            await _create_conversation_with_message(session, memory_status=None)

        response = await async_client.get("/api/memories/overrides")
        assert response.status_code == 200
        overrides = response.json()
        assert len(overrides) == 2
        statuses = {m["memory_status"] for m in overrides}
        assert statuses == {"pinned", "released"}

    @pytest.mark.asyncio
    async def test_set_and_clear_status(self, async_client, test_engine):
        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            _, message = await _create_conversation_with_message(session)

        # Pin it
        response = await async_client.put(
            f"/api/memories/{message.id}/status", json={"status": "pinned"}
        )
        assert response.status_code == 200
        assert response.json()["memory_status"] == "pinned"

        response = await async_client.get("/api/memories/overrides")
        assert len(response.json()) == 1

        # Clear it (researcher override)
        response = await async_client.put(
            f"/api/memories/{message.id}/status", json={"status": None}
        )
        assert response.status_code == 200
        assert response.json()["memory_status"] is None

        response = await async_client.get("/api/memories/overrides")
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_set_status_invalid_value(self, async_client, test_engine):
        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            _, message = await _create_conversation_with_message(session)

        response = await async_client.put(
            f"/api/memories/{message.id}/status", json={"status": "frozen"}
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_set_status_unknown_memory(self, async_client):
        response = await async_client.put(
            f"/api/memories/{uuid.uuid4()}/status", json={"status": "pinned"}
        )
        assert response.status_code == 404
