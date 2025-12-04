"""
Unit tests for LLMService.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.llm_service import LLMService, ModelProvider, MODEL_PROVIDER_MAP, AVAILABLE_MODELS


class TestModelProviderMapping:
    """Tests for model to provider mapping."""

    def test_claude_models_map_to_anthropic(self):
        """Test that Claude models map to Anthropic provider."""
        claude_models = [
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-5-20251101",
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
        ]

        for model in claude_models:
            assert MODEL_PROVIDER_MAP.get(model) == ModelProvider.ANTHROPIC

    def test_gpt_models_map_to_openai(self):
        """Test that GPT models map to OpenAI provider."""
        gpt_models = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]

        for model in gpt_models:
            assert MODEL_PROVIDER_MAP.get(model) == ModelProvider.OPENAI

    def test_o1_models_map_to_openai(self):
        """Test that o1 models map to OpenAI provider."""
        o1_models = ["o1", "o1-mini", "o1-preview"]

        for model in o1_models:
            assert MODEL_PROVIDER_MAP.get(model) == ModelProvider.OPENAI


class TestLLMService:
    """Tests for LLMService class."""

    def test_get_provider_for_known_model(self):
        """Test get_provider_for_model with known models."""
        service = LLMService()

        assert service.get_provider_for_model("claude-sonnet-4-5-20250929") == ModelProvider.ANTHROPIC
        assert service.get_provider_for_model("gpt-4o") == ModelProvider.OPENAI

    def test_get_provider_for_unknown_model(self):
        """Test get_provider_for_model with unknown model."""
        service = LLMService()

        assert service.get_provider_for_model("unknown-model") is None

    def test_is_provider_configured_anthropic(self):
        """Test is_provider_configured for Anthropic."""
        service = LLMService()

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            assert service.is_provider_configured(ModelProvider.ANTHROPIC) is True

            mock_settings.anthropic_api_key = ""
            assert service.is_provider_configured(ModelProvider.ANTHROPIC) is False

    def test_is_provider_configured_openai(self):
        """Test is_provider_configured for OpenAI."""
        service = LLMService()

        with patch("app.services.llm_service.openai_service") as mock_openai:
            mock_openai.is_configured.return_value = True
            assert service.is_provider_configured(ModelProvider.OPENAI) is True

            mock_openai.is_configured.return_value = False
            assert service.is_provider_configured(ModelProvider.OPENAI) is False

    def test_get_available_providers_all_configured(self):
        """Test get_available_providers when all providers configured."""
        service = LLMService()

        with patch("app.services.llm_service.settings") as mock_settings, \
             patch("app.services.llm_service.openai_service") as mock_openai:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_openai_model = "gpt-4o"
            mock_openai.is_configured.return_value = True

            providers = service.get_available_providers()

            assert len(providers) == 2

            anthropic_provider = next(p for p in providers if p["id"] == "anthropic")
            assert anthropic_provider["name"] == "Anthropic"
            assert len(anthropic_provider["models"]) > 0

            openai_provider = next(p for p in providers if p["id"] == "openai")
            assert openai_provider["name"] == "OpenAI"

    def test_get_available_providers_only_anthropic(self):
        """Test get_available_providers with only Anthropic configured."""
        service = LLMService()

        with patch("app.services.llm_service.settings") as mock_settings, \
             patch("app.services.llm_service.openai_service") as mock_openai:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_openai.is_configured.return_value = False

            providers = service.get_available_providers()

            assert len(providers) == 1
            assert providers[0]["id"] == "anthropic"

    def test_get_all_available_models(self):
        """Test get_all_available_models returns flat list."""
        service = LLMService()

        with patch("app.services.llm_service.settings") as mock_settings, \
             patch("app.services.llm_service.openai_service") as mock_openai:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_openai_model = "gpt-4o"
            mock_openai.is_configured.return_value = True

            models = service.get_all_available_models()

            # Should have models from both providers
            assert len(models) > 0

            # Each model should have provider info
            for model in models:
                assert "id" in model
                assert "name" in model
                assert "provider" in model
                assert "provider_name" in model

    def test_count_tokens(self):
        """Test count_tokens delegates to anthropic_service."""
        service = LLMService()

        with patch("app.services.llm_service.anthropic_service") as mock_anthropic:
            mock_anthropic.count_tokens.return_value = 42

            result = service.count_tokens("Hello, world!")

            mock_anthropic.count_tokens.assert_called_once_with("Hello, world!")
            assert result == 42

    @pytest.mark.asyncio
    async def test_send_message_routes_to_anthropic(self):
        """Test send_message routes Claude models to Anthropic."""
        service = LLMService()

        with patch("app.services.llm_service.settings") as mock_settings, \
             patch("app.services.llm_service.anthropic_service") as mock_anthropic:
            mock_settings.anthropic_api_key = "test-key"
            mock_anthropic.send_message = AsyncMock(return_value={
                "content": "Response",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "stop_reason": "end_turn",
            })

            messages = [{"role": "user", "content": "Hello"}]
            result = await service.send_message(messages, model="claude-sonnet-4-5-20250929")

            mock_anthropic.send_message.assert_called_once()
            assert result["content"] == "Response"

    @pytest.mark.asyncio
    async def test_send_message_routes_to_openai(self):
        """Test send_message routes GPT models to OpenAI."""
        service = LLMService()

        with patch("app.services.llm_service.openai_service") as mock_openai:
            mock_openai.is_configured.return_value = True
            mock_openai.send_message = AsyncMock(return_value={
                "content": "Response",
                "model": "gpt-4o",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "stop_reason": "stop",
            })

            messages = [{"role": "user", "content": "Hello"}]
            result = await service.send_message(messages, model="gpt-4o")

            mock_openai.send_message.assert_called_once()
            assert result["content"] == "Response"

    @pytest.mark.asyncio
    async def test_send_message_infers_anthropic_from_name(self):
        """Test send_message infers Anthropic from model name prefix."""
        service = LLMService()

        with patch("app.services.llm_service.settings") as mock_settings, \
             patch("app.services.llm_service.anthropic_service") as mock_anthropic:
            mock_settings.anthropic_api_key = "test-key"
            mock_anthropic.send_message = AsyncMock(return_value={
                "content": "Response",
                "model": "claude-unknown-version",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "stop_reason": "end_turn",
            })

            messages = [{"role": "user", "content": "Hello"}]
            # Use unknown Claude model that's not in the map
            result = await service.send_message(messages, model="claude-unknown-version")

            mock_anthropic.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_infers_openai_from_name(self):
        """Test send_message infers OpenAI from model name prefix."""
        service = LLMService()

        with patch("app.services.llm_service.openai_service") as mock_openai:
            mock_openai.is_configured.return_value = True
            mock_openai.send_message = AsyncMock(return_value={
                "content": "Response",
                "model": "gpt-5-preview",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "stop_reason": "stop",
            })

            messages = [{"role": "user", "content": "Hello"}]
            result = await service.send_message(messages, model="gpt-5-preview")

            mock_openai.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_unknown_model_raises(self):
        """Test send_message raises for unknown model."""
        service = LLMService()

        messages = [{"role": "user", "content": "Hello"}]

        with pytest.raises(ValueError, match="Unknown model"):
            await service.send_message(messages, model="unknown-model-xyz")

    @pytest.mark.asyncio
    async def test_send_message_unconfigured_provider_raises(self):
        """Test send_message raises for unconfigured provider."""
        service = LLMService()

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""

            messages = [{"role": "user", "content": "Hello"}]

            with pytest.raises(ValueError, match="not configured"):
                await service.send_message(messages, model="claude-sonnet-4-5-20250929")

    def test_build_messages_with_memories(self, sample_memories, sample_conversation_context):
        """Test build_messages_with_memories delegates to anthropic_service."""
        service = LLMService()

        with patch("app.services.llm_service.anthropic_service") as mock_anthropic:
            mock_anthropic.build_messages_with_memories.return_value = [
                {"role": "user", "content": "Memory block"},
            ]

            result = service.build_messages_with_memories(
                sample_memories,
                sample_conversation_context,
                "Current message",
            )

            mock_anthropic.build_messages_with_memories.assert_called_once_with(
                memories=sample_memories,
                conversation_context=sample_conversation_context,
                current_message="Current message",
            )


class TestAvailableModels:
    """Tests for available models configuration."""

    def test_anthropic_models_defined(self):
        """Test that Anthropic models are defined."""
        models = AVAILABLE_MODELS[ModelProvider.ANTHROPIC]

        assert len(models) > 0

        model_ids = [m["id"] for m in models]
        assert "claude-sonnet-4-5-20250929" in model_ids

    def test_openai_models_defined(self):
        """Test that OpenAI models are defined."""
        models = AVAILABLE_MODELS[ModelProvider.OPENAI]

        assert len(models) > 0

        model_ids = [m["id"] for m in models]
        assert "gpt-4o" in model_ids

    def test_all_models_have_required_fields(self):
        """Test that all models have required id and name fields."""
        for provider, models in AVAILABLE_MODELS.items():
            for model in models:
                assert "id" in model
                assert "name" in model
                assert isinstance(model["id"], str)
                assert isinstance(model["name"], str)
