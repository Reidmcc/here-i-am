"""
Integration tests for memories routes.

Tests memory listing, search, statistics, and deletion.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import EntityConfig
from app.database import Base, get_db
from app.main import app
from app.models import Conversation, Message, MessageRole
from app.routes.memories import calculate_significance

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def test_engine():
    """Create a test database engine."""
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
def mock_settings():
    """Mock settings for significance calculation."""
    with patch("app.routes.memories.settings") as mock:
        mock.significance_half_life_days = 60
        mock.recency_boost_strength = 1.2
        mock.significance_floor = 0.25
        mock.reflection_significance_multiplier = 1.5
        mock.retrieval_candidate_multiplier = 2
        mock.query_similarity_threshold = 0.2
        yield mock


@pytest.fixture
def mock_memory_service():
    """Mock memory service for tests."""
    with patch("app.routes.memories.memory_service") as mock:
        mock.is_configured.return_value = True
        mock.search_memories = AsyncMock(return_value=[])
        mock.get_full_memory_content = AsyncMock(return_value=None)
        mock.get_archived_conversation_ids = AsyncMock(return_value=set())
        mock.delete_memory = AsyncMock(return_value=True)
        mock.find_orphaned_records = AsyncMock(return_value=[])
        mock.cleanup_orphaned_records = AsyncMock(return_value={
            "entity_id": None,
            "dry_run": True,
            "orphans_found": 0,
            "orphans_deleted": 0,
            "errors": [],
            "orphan_ids": [],
        })
        mock.cleanup_memory_query_links = AsyncMock(return_value={
            "dry_run": True,
            "conversations_with_query_results": 0,
            "links_matched": 0,
            "links_deleted": 0,
        })
        yield mock


@pytest.fixture
async def async_client(test_engine, mock_settings):
    """Create an async test client with database override."""
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
async def create_test_data(test_engine):
    """Create test conversations and messages."""
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    created_data = {"conversations": [], "messages": []}

    async with async_session() as session:
        # Create a conversation
        conv_id = str(uuid.uuid4())
        conversation = Conversation(
            id=conv_id,
            title="Test Conversation",
            entity_id="test-entity",
        )
        session.add(conversation)
        await session.flush()
        created_data["conversations"].append(conv_id)

        # Create messages with varying retrieval stats
        now = datetime.utcnow()
        for i in range(5):
            msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=MessageRole.HUMAN if i % 2 == 0 else MessageRole.ASSISTANT,
                content=f"Test message {i}",
                times_retrieved=i * 2,
                last_retrieved_at=now - timedelta(days=i) if i > 0 else None,
            )
            session.add(msg)
            created_data["messages"].append(msg.id)

        # A reflection - vectorized as a memory, should appear in the browser
        reflection = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv_id,
            role=MessageRole.REFLECTION,
            content="A self-authored reflection",
        )
        session.add(reflection)
        created_data["reflection_id"] = reflection.id

        # Tool exchange + system messages - stored in SQL for conversation
        # replay but never vectorized, so they must NOT appear as memories
        for role, content in [
            (MessageRole.TOOL_USE, '[{"type": "tool_use", "name": "web_search"}]'),
            (MessageRole.TOOL_RESULT, '[{"type": "tool_result", "content": "results"}]'),
            (MessageRole.SYSTEM, "System message"),
        ]:
            msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=role,
                content=content,
            )
            session.add(msg)
            created_data.setdefault("non_memory_ids", []).append(msg.id)

        await session.commit()

    return created_data


class TestSignificanceCalculation:
    """Tests for the significance calculation function."""

    def test_significance_basic(self, mock_settings):
        """Test basic significance calculation."""
        now = datetime.utcnow()
        created_at = now - timedelta(days=30)
        last_retrieved_at = now - timedelta(days=5)

        sig = calculate_significance(
            times_retrieved=10,
            created_at=created_at,
            last_retrieved_at=last_retrieved_at,
        )

        assert sig > 0
        assert isinstance(sig, float)

    def test_significance_never_retrieved(self, mock_settings):
        """Test significance for never-retrieved memory."""
        now = datetime.utcnow()
        created_at = now - timedelta(days=10)

        sig = calculate_significance(
            times_retrieved=0,
            created_at=created_at,
            last_retrieved_at=None,
        )

        # With times_retrieved=0, created_at=10 days ago, last_retrieved_at=None:
        # significance = (1 + 0.1*0) * 1.0 * 0.5^(10/60) ≈ 0.891
        # Not at the floor - the half-life modifier for 10 days is still high
        assert sig > 0.8
        assert sig < 1.0

    def test_significance_recently_retrieved(self, mock_settings):
        """Test that recently retrieved memories get a boost."""
        now = datetime.utcnow()
        created_at = now - timedelta(days=30)

        # Retrieved 1 day ago (recent)
        sig_recent = calculate_significance(
            times_retrieved=5,
            created_at=created_at,
            last_retrieved_at=now - timedelta(days=1),
        )

        # Retrieved 30 days ago (old)
        sig_old = calculate_significance(
            times_retrieved=5,
            created_at=created_at,
            last_retrieved_at=now - timedelta(days=30),
        )

        # Recent should have higher significance
        assert sig_recent > sig_old

    def test_significance_half_life_decay(self, mock_settings):
        """Test that older memories decay in significance."""
        now = datetime.utcnow()
        last_retrieved_at = now - timedelta(days=1)

        # New memory
        sig_new = calculate_significance(
            times_retrieved=10,
            created_at=now - timedelta(days=10),
            last_retrieved_at=last_retrieved_at,
        )

        # Old memory (120 days = 2 half-lives with 60-day half-life)
        sig_old = calculate_significance(
            times_retrieved=10,
            created_at=now - timedelta(days=120),
            last_retrieved_at=last_retrieved_at,
        )

        # New memory should have higher significance
        assert sig_new > sig_old

    def test_significance_reflection_multiplier(self, mock_settings):
        """Reflections (memory_save) get their significance multiplied."""
        now = datetime.utcnow()
        created_at = now - timedelta(days=10)

        sig_normal = calculate_significance(
            times_retrieved=5,
            created_at=created_at,
            last_retrieved_at=now - timedelta(days=2),
        )
        sig_reflection = calculate_significance(
            times_retrieved=5,
            created_at=created_at,
            last_retrieved_at=now - timedelta(days=2),
            role="reflection",
        )

        assert sig_reflection == pytest.approx(sig_normal * 1.5, abs=0.001)

    def test_significance_reflection_multiplier_respects_floor(self, mock_settings):
        """The floor still applies after the reflection multiplier."""
        now = datetime.utcnow()
        # Very old memory whose boosted significance is still below the floor
        created_at = now - timedelta(days=600)

        sig = calculate_significance(
            times_retrieved=0,
            created_at=created_at,
            last_retrieved_at=None,
            role="reflection",
        )

        assert sig == pytest.approx(0.25, abs=0.001)


class TestListMemories:
    """Tests for listing memories."""

    @pytest.mark.asyncio
    async def test_list_memories_empty(self, async_client):
        """Test listing when no memories exist."""
        response = await async_client.get("/api/memories/")

        assert response.status_code == 200
        data = response.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_list_memories_with_data(self, async_client, create_test_data):
        """Test listing memories with data."""
        response = await async_client.get("/api/memories/")

        assert response.status_code == 200
        data = response.json()
        # 5 human/assistant + 1 reflection; tool_use/tool_result/system excluded
        assert len(data) == 6
        # Should have significance calculated
        for memory in data:
            assert "significance" in memory
            assert memory["significance"] > 0

    @pytest.mark.asyncio
    async def test_list_memories_filter_by_role(self, async_client, create_test_data):
        """Test filtering memories by role."""
        response = await async_client.get("/api/memories/", params={"role": "human"})

        assert response.status_code == 200
        data = response.json()
        # 3 human messages (indices 0, 2, 4)
        assert len(data) == 3
        for memory in data:
            assert memory["role"] == "human"

    @pytest.mark.asyncio
    async def test_list_memories_excludes_tool_and_system_messages(
        self, async_client, create_test_data
    ):
        """Tool exchanges and system messages are not memories and must not
        appear in the memory browser."""
        response = await async_client.get("/api/memories/", params={"limit": 200})

        assert response.status_code == 200
        data = response.json()
        listed_ids = {m["id"] for m in data}
        for non_memory_id in create_test_data["non_memory_ids"]:
            assert non_memory_id not in listed_ids
        for memory in data:
            assert memory["role"] in ("human", "assistant", "reflection")

    @pytest.mark.asyncio
    async def test_list_memories_filter_by_reflection_role(
        self, async_client, create_test_data
    ):
        """Reflections are vectorized memories and filterable by role."""
        response = await async_client.get("/api/memories/", params={"role": "reflection"})

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == create_test_data["reflection_id"]

    @pytest.mark.asyncio
    async def test_list_memories_rejects_non_memory_role_filter(
        self, async_client, create_test_data
    ):
        """Filtering by a non-memory role (e.g. tool_use) is rejected."""
        for bad_role in ("tool_use", "tool_result", "system", "bogus"):
            response = await async_client.get("/api/memories/", params={"role": bad_role})
            assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_list_memories_sort_by_significance(self, async_client, create_test_data):
        """Test sorting memories by significance."""
        response = await async_client.get(
            "/api/memories/",
            params={"sort_by": "significance"}
        )

        assert response.status_code == 200
        data = response.json()
        # Should be sorted by significance descending
        significances = [m["significance"] for m in data]
        assert significances == sorted(significances, reverse=True)

    @pytest.mark.asyncio
    async def test_list_memories_sort_by_created_at(self, async_client, create_test_data):
        """Test sorting memories by created_at."""
        response = await async_client.get(
            "/api/memories/",
            params={"sort_by": "created_at"}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_list_memories_pagination(self, async_client, create_test_data):
        """Test pagination of memories."""
        response = await async_client.get(
            "/api/memories/",
            params={"limit": 2, "offset": 0}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

        # Get second page
        response2 = await async_client.get(
            "/api/memories/",
            params={"limit": 2, "offset": 2}
        )

        data2 = response2.json()
        assert len(data2) == 2
        # Different memories
        assert data[0]["id"] != data2[0]["id"]


class TestSearchMemories:
    """Tests for semantic memory search."""

    @pytest.mark.asyncio
    async def test_search_not_configured(self, async_client, mock_memory_service):
        """Test search when memory system is not configured."""
        mock_memory_service.is_configured.return_value = False

        response = await async_client.post(
            "/api/memories/search",
            json={"query": "test query"}
        )

        assert response.status_code == 503
        assert "not configured" in response.json()["detail"].lower()

    @staticmethod
    def _full_content(memory_id: str, **overrides):
        """Build a get_full_memory_content-style payload for a memory ID."""
        data = {
            "id": memory_id,
            "conversation_id": f"conv-{memory_id}",
            "role": "human",
            "content": f"Content of {memory_id}",
            "created_at": datetime.utcnow().isoformat(),
            "times_retrieved": 1,
            "last_retrieved_at": None,
            "memory_status": None,
        }
        data.update(overrides)
        return data

    @pytest.mark.asyncio
    async def test_search_basic(self, async_client, mock_memory_service):
        """Test basic memory search returns similarity-ordered results."""
        mock_memory_service.search_memories.return_value = [
            {"id": "mem-1", "score": 0.95, "conversation_id": "conv-mem-1"},
            {"id": "mem-2", "score": 0.85, "conversation_id": "conv-mem-2"},
        ]
        mock_memory_service.get_full_memory_content = AsyncMock(
            side_effect=lambda memory_id, db: TestSearchMemories._full_content(memory_id)
        )

        response = await async_client.post(
            "/api/memories/search",
            json={"query": "test query", "top_k": 10}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == "mem-1"
        assert data[0]["score"] == 0.95
        assert data[0]["content"] == "Content of mem-1"
        assert "significance" in data[0]
        mock_memory_service.search_memories.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_without_content(self, async_client, mock_memory_service):
        """include_content=False omits full content but keeps the preview."""
        mock_memory_service.search_memories.return_value = [
            {"id": "mem-1", "score": 0.95, "conversation_id": "conv-mem-1"},
        ]
        mock_memory_service.get_full_memory_content = AsyncMock(
            side_effect=lambda memory_id, db: TestSearchMemories._full_content(memory_id)
        )

        response = await async_client.post(
            "/api/memories/search",
            json={"query": "test query", "top_k": 10, "include_content": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert "content" not in data[0]
        assert data[0]["content_preview"] == "Content of mem-1"

    @pytest.mark.asyncio
    async def test_search_with_entity_filter(self, async_client, mock_memory_service):
        """Test memory search with entity filter over-fetches candidates."""
        mock_memory_service.search_memories.return_value = []

        response = await async_client.post(
            "/api/memories/search",
            json={
                "query": "test query",
                "entity_id": "specific-entity",
            }
        )

        assert response.status_code == 200
        assert response.json() == []
        # top_k is over-fetched by the candidate multiplier (10 * 2), and
        # deliberate queries use the lower query similarity threshold
        mock_memory_service.search_memories.assert_called_with(
            query="test query",
            top_k=20,
            entity_id="specific-entity",
            similarity_threshold=0.2,
        )

    @pytest.mark.asyncio
    async def test_search_multi_entity_rejected(self, async_client, mock_memory_service):
        """The multi-entity sentinel is not a searchable index."""
        response = await async_client.post(
            "/api/memories/search",
            json={"query": "test query", "entity_id": "multi-entity"}
        )

        assert response.status_code == 400
        assert "specific entity" in response.json()["detail"]
        mock_memory_service.search_memories.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_filters_archived_released_and_orphans(
        self, async_client, mock_memory_service
    ):
        """Archived-conversation, released, and orphaned memories are excluded."""
        mock_memory_service.search_memories.return_value = [
            {"id": "mem-archived", "score": 0.99, "conversation_id": "conv-archived"},
            {"id": "mem-released", "score": 0.98, "conversation_id": "conv-mem-released"},
            {"id": "mem-orphan", "score": 0.97, "conversation_id": "conv-mem-orphan"},
            {"id": "mem-ok", "score": 0.90, "conversation_id": "conv-mem-ok"},
        ]
        mock_memory_service.get_archived_conversation_ids = AsyncMock(
            return_value={"conv-archived"}
        )

        def full_content(memory_id, db):
            if memory_id == "mem-orphan":
                return None
            if memory_id == "mem-released":
                return TestSearchMemories._full_content(memory_id, memory_status="released")
            return TestSearchMemories._full_content(memory_id)

        mock_memory_service.get_full_memory_content = AsyncMock(side_effect=full_content)

        response = await async_client.post(
            "/api/memories/search",
            json={"query": "test query", "top_k": 10}
        )

        assert response.status_code == 200
        data = response.json()
        assert [m["id"] for m in data] == ["mem-ok"]

    @pytest.mark.asyncio
    async def test_search_times_out_when_database_hangs(
        self, async_client, mock_memory_service
    ):
        """A stuck database turns into a 504 instead of hanging the request."""
        import asyncio as aio

        mock_memory_service.search_memories.return_value = [
            {"id": "mem-1", "score": 0.95, "conversation_id": "conv-mem-1"},
        ]

        async def never_returns(db, entity_id=None):
            await aio.sleep(3600)

        mock_memory_service.get_archived_conversation_ids = AsyncMock(
            side_effect=never_returns
        )

        with patch("app.routes.memories.SEARCH_ENRICHMENT_TIMEOUT_SECONDS", 0.05):
            response = await async_client.post(
                "/api/memories/search",
                json={"query": "test query", "top_k": 10}
            )

        assert response.status_code == 504
        assert "timed out" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_search_caps_results_at_top_k(self, async_client, mock_memory_service):
        """Over-fetched candidates are trimmed back to top_k."""
        mock_memory_service.search_memories.return_value = [
            {"id": f"mem-{i}", "score": 0.9 - i * 0.01, "conversation_id": f"conv-mem-{i}"}
            for i in range(6)
        ]
        mock_memory_service.get_full_memory_content = AsyncMock(
            side_effect=lambda memory_id, db: TestSearchMemories._full_content(memory_id)
        )

        response = await async_client.post(
            "/api/memories/search",
            json={"query": "test query", "top_k": 3}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        assert [m["id"] for m in data] == ["mem-0", "mem-1", "mem-2"]


class TestMemoryStats:
    """Tests for memory statistics."""

    @pytest.mark.asyncio
    async def test_stats_empty(self, async_client):
        """Test stats when no memories exist."""
        response = await async_client.get("/api/memories/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 0
        assert data["human_count"] == 0
        assert data["assistant_count"] == 0

    @pytest.mark.asyncio
    async def test_stats_with_data(self, async_client, create_test_data):
        """Test stats with existing data."""
        response = await async_client.get("/api/memories/stats")

        assert response.status_code == 200
        data = response.json()
        # 5 human/assistant + 1 reflection; tool/system messages excluded
        assert data["total_count"] == 6
        assert data["human_count"] == 3  # indices 0, 2, 4
        assert data["assistant_count"] == 2  # indices 1, 3
        assert "avg_times_retrieved" in data
        assert "max_times_retrieved" in data
        assert "most_significant" in data
        assert "retrieval_distribution" in data

    @pytest.mark.asyncio
    async def test_stats_retrieval_distribution(self, async_client, create_test_data):
        """Test retrieval distribution in stats."""
        response = await async_client.get("/api/memories/stats")

        assert response.status_code == 200
        data = response.json()
        dist = data["retrieval_distribution"]

        # Check all expected buckets exist
        assert "0" in dist
        assert "1-5" in dist
        assert "6-10" in dist
        assert "11-20" in dist
        assert "21+" in dist


class TestMemoryHealth:
    """Tests for memory system health check."""

    @pytest.mark.asyncio
    async def test_health_configured(self, async_client, mock_memory_service):
        """Test health check when configured."""
        with patch("app.routes.memories.settings") as mock_settings:
            mock_settings.get_entities.return_value = [
                EntityConfig(
                    index_name="test-index",
                    label="Test",
                    llm_provider="anthropic",
                )
            ]
            mock_settings.get_default_entity.return_value = EntityConfig(
                index_name="test-index",
                label="Test",
                llm_provider="anthropic",
            )
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.7
            mock_settings.recency_boost_strength = 1.2
            mock_settings.significance_half_life_days = 60
            mock_settings.significance_floor = 0.25

            response = await async_client.get("/api/memories/status/health")

        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is True
        assert "entities" in data
        assert "retrieval_top_k" in data

    @pytest.mark.asyncio
    async def test_health_not_configured(self, async_client, mock_memory_service):
        """Test health check when not configured."""
        mock_memory_service.is_configured.return_value = False

        with patch("app.routes.memories.settings") as mock_settings:
            mock_settings.get_entities.return_value = []
            mock_settings.get_default_entity.return_value = None
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.7
            mock_settings.recency_boost_strength = 1.2
            mock_settings.significance_half_life_days = 60
            mock_settings.significance_floor = 0.25

            response = await async_client.get("/api/memories/status/health")

        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is False


class TestGetMemory:
    """Tests for getting a specific memory."""

    @pytest.mark.asyncio
    async def test_get_existing_memory(self, async_client, create_test_data):
        """Test getting an existing memory."""
        memory_id = create_test_data["messages"][0]
        response = await async_client.get(f"/api/memories/{memory_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == memory_id
        assert "content" in data
        assert "significance" in data

    @pytest.mark.asyncio
    async def test_get_nonexistent_memory(self, async_client):
        """Test getting a non-existent memory."""
        fake_id = str(uuid.uuid4())
        response = await async_client.get(f"/api/memories/{fake_id}")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestDeleteMemory:
    """Tests for deleting memories."""

    @pytest.mark.asyncio
    async def test_delete_memory(self, async_client, create_test_data, mock_memory_service):
        """Test deleting a memory."""
        memory_id = create_test_data["messages"][0]
        response = await async_client.delete(f"/api/memories/{memory_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["id"] == memory_id

    @pytest.mark.asyncio
    async def test_delete_nonexistent_memory(self, async_client):
        """Test deleting a non-existent memory."""
        fake_id = str(uuid.uuid4())
        response = await async_client.delete(f"/api/memories/{fake_id}")

        assert response.status_code == 404


class TestOrphanedRecords:
    """Tests for orphaned record management."""

    @pytest.mark.asyncio
    async def test_list_orphans_not_configured(self, async_client, mock_memory_service):
        """Test listing orphans when memory not configured."""
        mock_memory_service.is_configured.return_value = False

        response = await async_client.get("/api/memories/orphans")

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_list_orphans_empty(self, async_client, mock_memory_service):
        """Test listing orphans when none exist."""
        response = await async_client.get("/api/memories/orphans")

        assert response.status_code == 200
        data = response.json()
        assert data["orphans_found"] == 0
        assert data["orphans"] == []

    @pytest.mark.asyncio
    async def test_list_orphans_with_data(self, async_client, mock_memory_service):
        """Test listing orphans when some exist."""
        mock_memory_service.find_orphaned_records.return_value = [
            {"id": "orphan-1", "metadata": {"content": "test"}},
            {"id": "orphan-2", "metadata": {"content": "test2"}},
        ]

        response = await async_client.get("/api/memories/orphans")

        assert response.status_code == 200
        data = response.json()
        assert data["orphans_found"] == 2
        assert len(data["orphans"]) == 2

    @pytest.mark.asyncio
    async def test_cleanup_orphans_dry_run(self, async_client, mock_memory_service):
        """Test orphan cleanup in dry run mode."""
        response = await async_client.post(
            "/api/memories/orphans/cleanup",
            json={"dry_run": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True

    @pytest.mark.asyncio
    async def test_cleanup_orphans_actual(self, async_client, mock_memory_service):
        """Test actual orphan cleanup."""
        mock_memory_service.cleanup_orphaned_records.return_value = {
            "entity_id": None,
            "dry_run": False,
            "orphans_found": 2,
            "orphans_deleted": 2,
            "errors": [],
            "orphan_ids": ["orphan-1", "orphan-2"],
        }

        response = await async_client.post(
            "/api/memories/orphans/cleanup",
            json={"dry_run": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is False
        assert data["orphans_deleted"] == 2


class TestQueryLinkCleanup:
    """Tests for the stale memory_query link cleanup endpoint."""

    @pytest.mark.asyncio
    async def test_defaults_to_dry_run(self, async_client, mock_memory_service):
        """An empty body must default to dry_run=True."""
        response = await async_client.post(
            "/api/memories/query-links/cleanup",
            json={}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        assert data["links_deleted"] == 0
        call_kwargs = mock_memory_service.cleanup_memory_query_links.call_args.kwargs
        assert call_kwargs["dry_run"] is True

    @pytest.mark.asyncio
    async def test_no_body_defaults_to_dry_run(self, async_client, mock_memory_service):
        """A bare POST with no body at all (curl -X POST ...) must work and
        default to dry_run=True."""
        response = await async_client.post("/api/memories/query-links/cleanup")

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        call_kwargs = mock_memory_service.cleanup_memory_query_links.call_args.kwargs
        assert call_kwargs["dry_run"] is True

    @pytest.mark.asyncio
    async def test_actual_cleanup(self, async_client, mock_memory_service):
        """dry_run=false is passed through and results are surfaced."""
        mock_memory_service.cleanup_memory_query_links.return_value = {
            "dry_run": False,
            "conversations_with_query_results": 2,
            "links_matched": 7,
            "links_deleted": 7,
        }

        response = await async_client.post(
            "/api/memories/query-links/cleanup",
            json={"dry_run": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is False
        assert data["conversations_with_query_results"] == 2
        assert data["links_matched"] == 7
        assert data["links_deleted"] == 7
        call_kwargs = mock_memory_service.cleanup_memory_query_links.call_args.kwargs
        assert call_kwargs["dry_run"] is False

    @pytest.mark.asyncio
    async def test_works_without_pinecone(self, async_client, mock_memory_service):
        """The cleanup is SQL-only — no 503 when memory is not configured."""
        mock_memory_service.is_configured.return_value = False

        response = await async_client.post(
            "/api/memories/query-links/cleanup",
            json={}
        )

        assert response.status_code == 200
