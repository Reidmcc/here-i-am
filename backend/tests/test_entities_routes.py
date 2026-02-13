"""
Tests for entities.py routes - Entity management endpoints.

Tests cover:
- GET /api/entities/ - List all configured entities
- GET /api/entities/{entity_id} - Get specific entity
- GET /api/entities/{entity_id}/status - Get entity Pinecone status
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from app.config import EntityConfig


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
    def test_list_entities_success(self, mock_settings):
        """Should return list of configured entities."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        entities = [
            _make_entity("claude-test", "Claude", "Primary", "anthropic"),
            _make_entity("gpt-test", "GPT", "OpenAI", "openai", "gpt-4o"),
        ]
        mock_settings.get_entities.return_value = entities
        mock_settings.get_default_entity.return_value = entities[0]

        response = client.get("/api/entities/")
        assert response.status_code == 200
        data = response.json()

        assert len(data["entities"]) == 2
        assert data["default_entity"] == "claude-test"
        assert data["entities"][0]["is_default"] is True
        assert data["entities"][1]["is_default"] is False

    @patch("app.routes.entities.settings")
    def test_list_entities_empty(self, mock_settings):
        """Should return empty list when no entities configured."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_settings.get_entities.return_value = []
        mock_settings.get_default_entity.return_value = None

        response = client.get("/api/entities/")
        assert response.status_code == 200
        data = response.json()

        assert data["entities"] == []
        assert data["default_entity"] is None

    @patch("app.routes.entities.settings")
    def test_list_entities_with_llm_providers(self, mock_settings):
        """Should include LLM provider information."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        entities = [
            _make_entity("claude-test", "Claude", "Test", "anthropic", "claude-sonnet-4-5-20250929"),
            _make_entity("gpt-test", "GPT", "Test", "openai", "gpt-4o"),
        ]
        mock_settings.get_entities.return_value = entities
        mock_settings.get_default_entity.return_value = entities[0]

        response = client.get("/api/entities/")
        data = response.json()

        assert data["entities"][0]["llm_provider"] == "anthropic"
        assert data["entities"][0]["default_model"] == "claude-sonnet-4-5-20250929"
        assert data["entities"][1]["llm_provider"] == "openai"
        assert data["entities"][1]["default_model"] == "gpt-4o"


# ============================================================
# Tests for GET /api/entities/{entity_id}
# ============================================================

class TestGetEntity:
    """Tests for getting a specific entity."""

    @patch("app.routes.entities.settings")
    def test_get_entity_success(self, mock_settings):
        """Should return entity details."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        entity = _make_entity("claude-test", "Claude Test", "Primary", "anthropic")
        mock_settings.get_entity_by_index.return_value = entity
        mock_settings.get_default_entity.return_value = entity

        response = client.get("/api/entities/claude-test")
        assert response.status_code == 200
        data = response.json()

        assert data["index_name"] == "claude-test"
        assert data["label"] == "Claude Test"
        assert data["is_default"] is True

    @patch("app.routes.entities.settings")
    def test_get_entity_not_found(self, mock_settings):
        """Should return 404 for unknown entity."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_settings.get_entity_by_index.return_value = None

        response = client.get("/api/entities/nonexistent")
        assert response.status_code == 404

    @patch("app.routes.entities.settings")
    def test_get_entity_not_default(self, mock_settings):
        """Should show is_default=False for non-default entity."""
        from fastapi.testclient import TestClient
        from app.routes.entities import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        entity = _make_entity("gpt-test", "GPT", "Secondary", "openai")
        default = _make_entity("claude-test", "Claude", "Primary", "anthropic")
        mock_settings.get_entity_by_index.return_value = entity
        mock_settings.get_default_entity.return_value = default

        response = client.get("/api/entities/gpt-test")
        data = response.json()
        assert data["is_default"] is False


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
