"""
Unit tests for configuration and EntityConfig.
"""
import pytest
from unittest.mock import patch
from app.config import Settings, EntityConfig


class TestEntityConfig:
    """Tests for EntityConfig class."""

    def test_entity_config_creation(self):
        """Test basic EntityConfig creation."""
        entity = EntityConfig(
            index_name="test-index",
            label="Test Entity",
            description="A test entity",
            model_provider="anthropic",
            default_model="claude-sonnet-4-5-latest",
        )

        assert entity.index_name == "test-index"
        assert entity.label == "Test Entity"
        assert entity.description == "A test entity"
        assert entity.model_provider == "anthropic"
        assert entity.default_model == "claude-sonnet-4-5-latest"

    def test_entity_config_defaults(self):
        """Test EntityConfig default values."""
        entity = EntityConfig(
            index_name="test-index",
            label="Test Entity",
        )

        assert entity.description == ""
        assert entity.model_provider == "anthropic"
        assert entity.default_model is None

    def test_entity_config_openai_provider(self):
        """Test EntityConfig with OpenAI provider."""
        entity = EntityConfig(
            index_name="gpt-index",
            label="GPT Entity",
            model_provider="openai",
            default_model="gpt-4o",
        )

        assert entity.model_provider == "openai"
        assert entity.default_model == "gpt-4o"

    def test_entity_config_to_dict(self):
        """Test EntityConfig to_dict method."""
        entity = EntityConfig(
            index_name="test-index",
            label="Test Entity",
            description="A test entity",
            model_provider="anthropic",
            default_model="claude-sonnet-4-5-latest",
        )

        result = entity.to_dict()

        assert result == {
            "index_name": "test-index",
            "label": "Test Entity",
            "description": "A test entity",
            "model_provider": "anthropic",
            "default_model": "claude-sonnet-4-5-latest",
        }


class TestSettings:
    """Tests for Settings class."""

    def test_settings_default_values(self):
        """Test Settings default values."""
        settings = Settings(
            anthropic_api_key="test-key",
            _env_file=None,  # Disable .env loading for test
        )

        assert settings.default_model == "claude-sonnet-4-5-latest"
        assert settings.default_openai_model == "gpt-4o"
        assert settings.default_temperature == 1.0
        assert settings.default_max_tokens == 4096
        assert settings.retrieval_top_k == 10
        assert settings.similarity_threshold == 0.7

    def test_settings_custom_values(self, test_settings):
        """Test Settings with custom values."""
        assert test_settings.anthropic_api_key == "test-anthropic-key"
        assert test_settings.openai_api_key == "test-openai-key"
        assert test_settings.pinecone_api_key == "test-pinecone-key"
        assert test_settings.retrieval_top_k == 5

    def test_get_entities_single_fallback(self):
        """Test get_entities with single fallback index."""
        settings = Settings(
            anthropic_api_key="test-key",
            pinecone_index_name="my-memories",
            pinecone_indexes="",  # Empty means use fallback
            _env_file=None,
        )

        entities = settings.get_entities()

        assert len(entities) == 1
        assert entities[0].index_name == "my-memories"
        assert entities[0].label == "Default"
        assert entities[0].model_provider == "anthropic"

    def test_get_entities_multiple_from_json(self, test_settings_multi_entity):
        """Test get_entities with multiple entities from JSON."""
        entities = test_settings_multi_entity.get_entities()

        assert len(entities) == 2

        # Check first entity (Claude)
        assert entities[0].index_name == "claude-test"
        assert entities[0].label == "Claude Test"
        assert entities[0].model_provider == "anthropic"

        # Check second entity (GPT)
        assert entities[1].index_name == "gpt-test"
        assert entities[1].label == "GPT Test"
        assert entities[1].model_provider == "openai"
        assert entities[1].default_model == "gpt-4o"

    def test_get_entities_invalid_json_fallback(self):
        """Test get_entities falls back with invalid JSON."""
        settings = Settings(
            anthropic_api_key="test-key",
            pinecone_index_name="fallback-index",
            pinecone_indexes="invalid json [[[",
            _env_file=None,
        )

        entities = settings.get_entities()

        # Should fall back to single index
        assert len(entities) == 1
        assert entities[0].index_name == "fallback-index"

    def test_get_entity_by_index_found(self, test_settings_multi_entity):
        """Test get_entity_by_index when entity exists."""
        entity = test_settings_multi_entity.get_entity_by_index("claude-test")

        assert entity is not None
        assert entity.index_name == "claude-test"
        assert entity.label == "Claude Test"

    def test_get_entity_by_index_not_found(self, test_settings_multi_entity):
        """Test get_entity_by_index when entity doesn't exist."""
        entity = test_settings_multi_entity.get_entity_by_index("nonexistent")

        assert entity is None

    def test_get_default_entity(self, test_settings_multi_entity):
        """Test get_default_entity returns first entity."""
        entity = test_settings_multi_entity.get_default_entity()

        assert entity is not None
        assert entity.index_name == "claude-test"

    def test_get_default_model_for_provider_anthropic(self):
        """Test get_default_model_for_provider for Anthropic."""
        settings = Settings(
            anthropic_api_key="test-key",
            default_model="claude-opus-4-latest",
            _env_file=None,
        )

        model = settings.get_default_model_for_provider("anthropic")

        assert model == "claude-opus-4-latest"

    def test_get_default_model_for_provider_openai(self):
        """Test get_default_model_for_provider for OpenAI."""
        settings = Settings(
            anthropic_api_key="test-key",
            default_openai_model="gpt-4-turbo",
            _env_file=None,
        )

        model = settings.get_default_model_for_provider("openai")

        assert model == "gpt-4-turbo"

    def test_settings_database_url_alias(self):
        """Test that database URL can be set via environment variable alias."""
        import os
        # Test using the HERE_I_AM_DATABASE_URL environment variable (the alias)
        with patch.dict(os.environ, {"HERE_I_AM_DATABASE_URL": "postgresql+asyncpg://localhost/test"}):
            settings = Settings(
                anthropic_api_key="test-key",
                _env_file=None,
            )
            assert settings.here_i_am_database_url == "postgresql+asyncpg://localhost/test"

    def test_settings_memory_defaults(self):
        """Test memory-related default settings."""
        settings = Settings(
            anthropic_api_key="test-key",
            _env_file=None,
        )

        assert settings.recency_boost_strength == 1.0
        assert settings.age_decay_rate == 0.01
        assert settings.significance_floor == 0.0

    def test_settings_reflection_defaults(self):
        """Test reflection-related default settings."""
        settings = Settings(
            anthropic_api_key="test-key",
            _env_file=None,
        )

        assert settings.reflection_seed_count == 7
        assert settings.reflection_exclude_recent_conversations == 0
