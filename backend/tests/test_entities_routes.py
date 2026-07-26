"""
Tests for entities.py routes - Entity management endpoints.

Tests cover:
- GET /api/entities/ - List all configured entities
- GET /api/entities/{entity_id} - Get specific entity
- GET /api/entities/{entity_id}/status - Get entity Pinecone status
- PUT /api/entities/{entity_id}/system-prompt - Persist entity system prompt
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import EntityConfig
from app.database import Base, get_db
from app.models import EntitySetting


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def entities_client():
    """
    Async test client for the entities router backed by an in-memory SQLite DB
    with all tables created. The list/get endpoints read persisted system
    prompts from the entity_settings table, so a real DB is required.

    Yields (client, session_factory); the factory lets tests seed/inspect rows.
    Everything runs in a single event loop, avoiding cross-loop async-engine
    issues that a sync TestClient would otherwise hit with in-memory SQLite.
    """
    from app.routes.entities import router

    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory

    await engine.dispose()


# ============================================================
# Helper: Create mock entity configs
# ============================================================

def _make_entity(index_name="claude-test", label="Claude Test",
                 description="Test entity", llm_provider="anthropic",
                 default_model="claude-sonnet-4-5-20250929"):
    return EntityConfig(
        index_name=index_name,
        label=label,
        description=description,
        llm_provider=llm_provider,
        default_model=default_model,
    )


# ============================================================
# Tests for GET /api/entities/
# ============================================================

class TestListEntities:
    """Tests for listing all configured entities."""

    @patch("app.routes.entities.settings")
    async def test_list_entities_success(self, mock_settings, entities_client):
        """Should return list of configured entities."""
        client, _ = entities_client

        entities = [
            _make_entity("claude-test", "Claude", "Primary", "anthropic"),
            _make_entity("gpt-test", "GPT", "OpenAI", "openai", "gpt-4o"),
        ]
        mock_settings.get_entities.return_value = entities
        mock_settings.get_default_entity.return_value = entities[0]

        response = await client.get("/api/entities/")
        assert response.status_code == 200
        data = response.json()

        assert len(data["entities"]) == 2
        assert data["default_entity"] == "claude-test"
        assert data["entities"][0]["is_default"] is True
        assert data["entities"][1]["is_default"] is False

    @patch("app.routes.entities.settings")
    async def test_list_entities_empty(self, mock_settings, entities_client):
        """Should return empty list when no entities configured."""
        client, _ = entities_client

        mock_settings.get_entities.return_value = []
        mock_settings.get_default_entity.return_value = None

        response = await client.get("/api/entities/")
        assert response.status_code == 200
        data = response.json()

        assert data["entities"] == []
        assert data["default_entity"] is None

    @patch("app.routes.entities.settings")
    async def test_list_entities_with_llm_providers(self, mock_settings, entities_client):
        """Should include LLM provider information."""
        client, _ = entities_client

        entities = [
            _make_entity("claude-test", "Claude", "Test", "anthropic", "claude-sonnet-4-5-20250929"),
            _make_entity("gpt-test", "GPT", "Test", "openai", "gpt-4o"),
        ]
        mock_settings.get_entities.return_value = entities
        mock_settings.get_default_entity.return_value = entities[0]

        response = await client.get("/api/entities/")
        data = response.json()

        assert data["entities"][0]["llm_provider"] == "anthropic"
        assert data["entities"][0]["default_model"] == "claude-sonnet-4-5-20250929"
        assert data["entities"][1]["llm_provider"] == "openai"
        assert data["entities"][1]["default_model"] == "gpt-4o"

    @patch("app.routes.entities.settings")
    async def test_list_entities_includes_persisted_system_prompt(self, mock_settings, entities_client):
        """Should include each entity's persisted system prompt (None if unset)."""
        client, session_factory = entities_client

        entities = [
            _make_entity("claude-test", "Claude", "Primary", "anthropic"),
            _make_entity("gpt-test", "GPT", "OpenAI", "openai", "gpt-4o"),
        ]
        mock_settings.get_entities.return_value = entities
        mock_settings.get_default_entity.return_value = entities[0]

        # Seed a stored prompt for one entity only.
        async with session_factory() as session:
            session.add(EntitySetting(entity_id="claude-test", system_prompt="Be Claude"))
            await session.commit()

        response = await client.get("/api/entities/")
        data = response.json()

        prompts = {e["index_name"]: e["system_prompt"] for e in data["entities"]}
        assert prompts["claude-test"] == "Be Claude"
        assert prompts["gpt-test"] is None


# ============================================================
# Tests for GET /api/entities/{entity_id}
# ============================================================

class TestGetEntity:
    """Tests for getting a specific entity."""

    @patch("app.routes.entities.settings")
    async def test_get_entity_success(self, mock_settings, entities_client):
        """Should return entity details."""
        client, _ = entities_client

        entity = _make_entity("claude-test", "Claude Test", "Primary", "anthropic")
        mock_settings.get_entity_by_index.return_value = entity
        mock_settings.get_default_entity.return_value = entity

        response = await client.get("/api/entities/claude-test")
        assert response.status_code == 200
        data = response.json()

        assert data["index_name"] == "claude-test"
        assert data["label"] == "Claude Test"
        assert data["is_default"] is True
        assert data["system_prompt"] is None

    @patch("app.routes.entities.settings")
    async def test_get_entity_returns_persisted_system_prompt(self, mock_settings, entities_client):
        """Should return the stored system prompt for the entity."""
        client, session_factory = entities_client

        entity = _make_entity("claude-test", "Claude Test", "Primary", "anthropic")
        mock_settings.get_entity_by_index.return_value = entity
        mock_settings.get_default_entity.return_value = entity

        async with session_factory() as session:
            session.add(EntitySetting(entity_id="claude-test", system_prompt="Persisted prompt"))
            await session.commit()

        response = await client.get("/api/entities/claude-test")
        data = response.json()
        assert data["system_prompt"] == "Persisted prompt"

    @patch("app.routes.entities.settings")
    async def test_get_entity_not_found(self, mock_settings, entities_client):
        """Should return 404 for unknown entity."""
        client, _ = entities_client

        mock_settings.get_entity_by_index.return_value = None

        response = await client.get("/api/entities/nonexistent")
        assert response.status_code == 404

    @patch("app.routes.entities.settings")
    async def test_get_entity_not_default(self, mock_settings, entities_client):
        """Should show is_default=False for non-default entity."""
        client, _ = entities_client

        entity = _make_entity("gpt-test", "GPT", "Secondary", "openai")
        default = _make_entity("claude-test", "Claude", "Primary", "anthropic")
        mock_settings.get_entity_by_index.return_value = entity
        mock_settings.get_default_entity.return_value = default

        response = await client.get("/api/entities/gpt-test")
        data = response.json()
        assert data["is_default"] is False


# ============================================================
# Tests for PUT /api/entities/{entity_id}/system-prompt
# ============================================================

class TestUpdateEntitySystemPrompt:
    """Tests for persisting an entity's default system prompt."""

    @patch("app.routes.entities.settings")
    async def test_create_system_prompt(self, mock_settings, entities_client):
        """Should persist a new system prompt for the entity."""
        client, session_factory = entities_client
        mock_settings.get_entity_by_index.return_value = _make_entity("claude-test")

        response = await client.put(
            "/api/entities/claude-test/system-prompt",
            json={"system_prompt": "You are Claude."},
        )
        assert response.status_code == 200
        assert response.json() == {"entity_id": "claude-test", "system_prompt": "You are Claude."}

        # Verify it was persisted.
        async with session_factory() as session:
            setting = await session.get(EntitySetting, "claude-test")
            assert setting is not None
            assert setting.system_prompt == "You are Claude."

    @patch("app.routes.entities.settings")
    async def test_update_existing_system_prompt(self, mock_settings, entities_client):
        """Should overwrite an existing system prompt."""
        client, session_factory = entities_client
        mock_settings.get_entity_by_index.return_value = _make_entity("claude-test")

        async with session_factory() as session:
            session.add(EntitySetting(entity_id="claude-test", system_prompt="Old"))
            await session.commit()

        response = await client.put(
            "/api/entities/claude-test/system-prompt",
            json={"system_prompt": "New"},
        )
        assert response.status_code == 200
        assert response.json()["system_prompt"] == "New"

        async with session_factory() as session:
            setting = await session.get(EntitySetting, "claude-test")
            assert setting.system_prompt == "New"

    @patch("app.routes.entities.settings")
    async def test_clear_system_prompt_with_null(self, mock_settings, entities_client):
        """Should clear the prompt when null is sent."""
        client, session_factory = entities_client
        mock_settings.get_entity_by_index.return_value = _make_entity("claude-test")

        async with session_factory() as session:
            session.add(EntitySetting(entity_id="claude-test", system_prompt="Existing"))
            await session.commit()

        response = await client.put(
            "/api/entities/claude-test/system-prompt",
            json={"system_prompt": None},
        )
        assert response.status_code == 200
        assert response.json()["system_prompt"] is None

        async with session_factory() as session:
            setting = await session.get(EntitySetting, "claude-test")
            assert setting.system_prompt is None

    @patch("app.routes.entities.settings")
    async def test_blank_string_normalized_to_null(self, mock_settings, entities_client):
        """Should normalize a whitespace-only prompt to NULL."""
        client, _ = entities_client
        mock_settings.get_entity_by_index.return_value = _make_entity("claude-test")

        response = await client.put(
            "/api/entities/claude-test/system-prompt",
            json={"system_prompt": "   "},
        )
        assert response.status_code == 200
        assert response.json()["system_prompt"] is None

    @patch("app.routes.entities.settings")
    async def test_update_unknown_entity_returns_404(self, mock_settings, entities_client):
        """Should 404 when the entity is not configured."""
        client, _ = entities_client
        mock_settings.get_entity_by_index.return_value = None

        response = await client.put(
            "/api/entities/nonexistent/system-prompt",
            json={"system_prompt": "x"},
        )
        assert response.status_code == 404


# ============================================================
# Tests for GET /api/entities/{entity_id}/status
# ============================================================

class TestGetEntityStatus:
    """Tests for getting entity Pinecone status."""

    @patch("app.routes.entities.memory_service")
    @patch("app.routes.entities.settings")
    def test_status_pinecone_not_configured(self, mock_settings, mock_memory):
        """Should return not-configured status when Pinecone is not set up."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        entity = _make_entity("claude-test")
        mock_settings.get_entity_by_index.return_value = entity
        mock_memory.is_configured.return_value = False

        response = client.get("/api/entities/claude-test/status")
        assert response.status_code == 200
        data = response.json()

        assert data["pinecone_configured"] is False
        assert data["index_connected"] is False

    @patch("app.routes.entities.memory_service")
    @patch("app.routes.entities.settings")
    def test_status_index_not_connected(self, mock_settings, mock_memory):
        """Should report index not connected when get_index returns None."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        entity = _make_entity("claude-test")
        mock_settings.get_entity_by_index.return_value = entity
        mock_memory.is_configured.return_value = True
        mock_memory.get_index.return_value = None

        response = client.get("/api/entities/claude-test/status")
        data = response.json()

        assert data["pinecone_configured"] is True
        assert data["index_connected"] is False

    @patch("app.routes.entities.memory_service")
    @patch("app.routes.entities.settings")
    def test_status_connected_with_stats(self, mock_settings, mock_memory):
        """Should return stats when index is connected."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        entity = _make_entity("claude-test")
        mock_settings.get_entity_by_index.return_value = entity
        mock_memory.is_configured.return_value = True

        mock_index = MagicMock()
        mock_stats = MagicMock()
        mock_stats.total_vector_count = 1500
        mock_stats.dimension = 1024
        mock_index.describe_index_stats.return_value = mock_stats
        mock_memory.get_index.return_value = mock_index

        response = client.get("/api/entities/claude-test/status")
        data = response.json()

        assert data["pinecone_configured"] is True
        assert data["index_connected"] is True
        assert data["stats"]["total_vector_count"] == 1500
        assert data["stats"]["dimension"] == 1024

    @patch("app.routes.entities.memory_service")
    @patch("app.routes.entities.settings")
    def test_status_stats_error(self, mock_settings, mock_memory):
        """Should handle stats error gracefully."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        entity = _make_entity("claude-test")
        mock_settings.get_entity_by_index.return_value = entity
        mock_memory.is_configured.return_value = True

        mock_index = MagicMock()
        mock_index.describe_index_stats.side_effect = Exception("Connection timeout")
        mock_memory.get_index.return_value = mock_index

        response = client.get("/api/entities/claude-test/status")
        data = response.json()

        assert data["index_connected"] is True
        assert data["stats"] is None
        assert "Connection timeout" in data["message"]

    @patch("app.routes.entities.memory_service")
    @patch("app.routes.entities.settings")
    def test_status_entity_not_found(self, mock_settings, mock_memory):
        """Should return 404 for unknown entity."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_settings.get_entity_by_index.return_value = None

        response = client.get("/api/entities/nonexistent/status")
        assert response.status_code == 404


# ============================================================
# Tests for PUT /api/entities/{entity_id}/thinking-effort
# ============================================================

class TestEntityThinkingEffort:
    """Tests for per-entity thinking effort."""

    @patch("app.routes.entities.settings")
    async def test_list_includes_thinking_effort_and_default(self, mock_settings, entities_client):
        """Listing should carry each entity's effort plus the global fallback."""
        client, session_factory = entities_client

        entities = [
            _make_entity("claude-test", "Claude", "Primary", "anthropic"),
            _make_entity("gpt-test", "GPT", "OpenAI", "openai", "gpt-4o"),
        ]
        mock_settings.get_entities.return_value = entities
        mock_settings.get_default_entity.return_value = entities[0]

        async with session_factory() as session:
            session.add(EntitySetting(entity_id="claude-test", thinking_effort="max"))
            await session.commit()

        response = await client.get("/api/entities/")
        data = response.json()

        efforts = {e["index_name"]: e["thinking_effort"] for e in data["entities"]}
        assert efforts["claude-test"] == "max"
        # Unset entities report None and fall back to the global default
        assert efforts["gpt-test"] is None
        assert data["default_thinking_effort"] == "high"
        assert data["thinking_effort_levels"] == ["low", "medium", "high", "xhigh", "max"]

    @patch("app.routes.entities.settings")
    async def test_set_thinking_effort(self, mock_settings, entities_client):
        """Should persist a new effort level for the entity."""
        client, session_factory = entities_client
        mock_settings.get_entity_by_index.return_value = _make_entity("claude-test")

        response = await client.put(
            "/api/entities/claude-test/thinking-effort",
            json={"thinking_effort": "xhigh"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "entity_id": "claude-test",
            "thinking_effort": "xhigh",
            "effective_thinking_effort": "xhigh",
        }

        async with session_factory() as session:
            setting = await session.get(EntitySetting, "claude-test")
            assert setting.thinking_effort == "xhigh"

    @patch("app.routes.entities.settings")
    async def test_set_thinking_effort_is_case_insensitive(self, mock_settings, entities_client):
        """Should normalize case before persisting."""
        client, session_factory = entities_client
        mock_settings.get_entity_by_index.return_value = _make_entity("claude-test")

        response = await client.put(
            "/api/entities/claude-test/thinking-effort",
            json={"thinking_effort": "  High "},
        )
        assert response.status_code == 200
        assert response.json()["thinking_effort"] == "high"

        async with session_factory() as session:
            setting = await session.get(EntitySetting, "claude-test")
            assert setting.thinking_effort == "high"

    @patch("app.routes.entities.settings")
    async def test_clear_thinking_effort_falls_back_to_default(self, mock_settings, entities_client):
        """Clearing should NULL the override and report the global default."""
        client, session_factory = entities_client
        mock_settings.get_entity_by_index.return_value = _make_entity("claude-test")

        async with session_factory() as session:
            session.add(EntitySetting(entity_id="claude-test", thinking_effort="low"))
            await session.commit()

        response = await client.put(
            "/api/entities/claude-test/thinking-effort",
            json={"thinking_effort": None},
        )
        assert response.status_code == 200
        assert response.json()["thinking_effort"] is None
        assert response.json()["effective_thinking_effort"] == "high"

        async with session_factory() as session:
            setting = await session.get(EntitySetting, "claude-test")
            assert setting.thinking_effort is None

    @patch("app.routes.entities.settings")
    async def test_invalid_thinking_effort_rejected(self, mock_settings, entities_client):
        """An unrecognized level is a client error, not a silent fallback."""
        client, session_factory = entities_client
        mock_settings.get_entity_by_index.return_value = _make_entity("claude-test")

        response = await client.put(
            "/api/entities/claude-test/thinking-effort",
            json={"thinking_effort": "ludicrous"},
        )
        assert response.status_code == 422

        async with session_factory() as session:
            assert await session.get(EntitySetting, "claude-test") is None

    @patch("app.routes.entities.settings")
    async def test_thinking_effort_preserves_system_prompt(self, mock_settings, entities_client):
        """Setting the effort must not disturb the entity's stored prompt."""
        client, session_factory = entities_client
        mock_settings.get_entity_by_index.return_value = _make_entity("claude-test")

        async with session_factory() as session:
            session.add(EntitySetting(entity_id="claude-test", system_prompt="Be Claude"))
            await session.commit()

        response = await client.put(
            "/api/entities/claude-test/thinking-effort",
            json={"thinking_effort": "low"},
        )
        assert response.status_code == 200

        async with session_factory() as session:
            setting = await session.get(EntitySetting, "claude-test")
            assert setting.system_prompt == "Be Claude"
            assert setting.thinking_effort == "low"

    @patch("app.routes.entities.settings")
    async def test_thinking_effort_unknown_entity_returns_404(self, mock_settings, entities_client):
        """Should 404 for an entity that isn't configured."""
        client, _ = entities_client
        mock_settings.get_entity_by_index.return_value = None

        response = await client.put(
            "/api/entities/nonexistent/thinking-effort",
            json={"thinking_effort": "high"},
        )
        assert response.status_code == 404
