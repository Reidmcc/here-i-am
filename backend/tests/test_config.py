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
            llm_provider="anthropic",
            default_model="claude-sonnet-4-5-20250929",
        )

        assert entity.index_name == "test-index"
        assert entity.label == "Test Entity"
        assert entity.description == "A test entity"
        assert entity.llm_provider == "anthropic"
        assert entity.default_model == "claude-sonnet-4-5-20250929"

    def test_entity_config_defaults(self):
        """Test EntityConfig default values."""
        entity = EntityConfig(
            index_name="test-index",
            label="Test Entity",
        )

        assert entity.description == ""
        assert entity.llm_provider == "anthropic"
        assert entity.default_model is None

    def test_entity_config_openai_provider(self):
        """Test EntityConfig with OpenAI provider."""
        entity = EntityConfig(
            index_name="gpt-index",
            label="GPT Entity",
            llm_provider="openai",
            default_model="gpt-4o",
        )

        assert entity.llm_provider == "openai"
        assert entity.default_model == "gpt-4o"

    def test_entity_config_to_dict(self):
        """Test EntityConfig to_dict method."""
        entity = EntityConfig(
            index_name="test-index",
            label="Test Entity",
            description="A test entity",
            llm_provider="anthropic",
            default_model="claude-sonnet-4-5-20250929",
        )

        result = entity.to_dict()

        assert result == {
            "index_name": "test-index",
            "label": "Test Entity",
            "description": "A test entity",
            "llm_provider": "anthropic",
            "default_model": "claude-sonnet-4-5-20250929",
            "host": None,
        }


class TestSettings:
    """Tests for Settings class."""

    def test_settings_default_values(self):
        """Test Settings default values."""
        settings = Settings(
            anthropic_api_key="test-key",
            _env_file=None,  # Disable .env loading for test
        )

        assert settings.default_model == "claude-sonnet-4-5-20250929"
        assert settings.default_openai_model == "gpt-5.1"
        assert settings.default_temperature == 1.0
        assert settings.default_max_tokens == 64000
        assert settings.retrieval_top_k == 3  # Updated to match current default
        assert settings.similarity_threshold == 0.4  # Updated: tuned for llama-text-embed-v2

    def test_settings_custom_values(self, test_settings):
        """Test Settings with custom values."""
        assert test_settings.anthropic_api_key == "test-anthropic-key"
        assert test_settings.openai_api_key == "test-openai-key"
        assert test_settings.pinecone_api_key == "test-pinecone-key"
        assert test_settings.retrieval_top_k == 5

    def test_get_entities_no_config(self):
        """Test get_entities returns empty list when PINECONE_INDEXES not set."""
        settings = Settings(
            anthropic_api_key="test-key",
            pinecone_indexes="",  # Empty means no entities
            _env_file=None,
        )

        entities = settings.get_entities()

        assert len(entities) == 0

    def test_get_entities_multiple_from_json(self, test_settings_multi_entity):
        """Test get_entities with multiple entities from JSON."""
        entities = test_settings_multi_entity.get_entities()

        assert len(entities) == 2

        # Check first entity (Claude)
        assert entities[0].index_name == "claude-test"
        assert entities[0].label == "Claude Test"
        assert entities[0].llm_provider == "anthropic"

        # Check second entity (GPT)
        assert entities[1].index_name == "gpt-test"
        assert entities[1].label == "GPT Test"
        assert entities[1].llm_provider == "openai"
        assert entities[1].default_model == "gpt-4o"

    def test_get_entities_invalid_json_raises_error(self):
        """Test get_entities raises ValueError with invalid JSON."""
        settings = Settings(
            anthropic_api_key="test-key",
            pinecone_indexes="invalid json [[[",
            _env_file=None,
        )

        with pytest.raises(ValueError) as exc_info:
            settings.get_entities()

        assert "Invalid JSON in PINECONE_INDEXES" in str(exc_info.value)

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

    def test_get_default_entity_no_config(self):
        """Test get_default_entity returns None when no entities configured."""
        settings = Settings(
            anthropic_api_key="test-key",
            pinecone_indexes="",  # No entities configured
            _env_file=None,
        )

        entity = settings.get_default_entity()

        assert entity is None

    def test_get_default_model_for_provider_anthropic(self):
        """Test get_default_model_for_provider for Anthropic."""
        settings = Settings(
            anthropic_api_key="test-key",
            default_model="claude-opus-4-20250514",
            _env_file=None,
        )

        model = settings.get_default_model_for_provider("anthropic")

        assert model == "claude-opus-4-20250514"

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

        assert settings.recency_boost_strength == 1.2
        assert settings.significance_floor == 0.25

    def test_settings_reflection_defaults(self):
        """Test reflection-related default settings."""
        settings = Settings(
            anthropic_api_key="test-key",
            _env_file=None,
        )

        assert settings.reflection_seed_count == 7
        assert settings.reflection_exclude_recent_conversations == 0
