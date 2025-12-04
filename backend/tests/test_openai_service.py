"""
Unit tests for OpenAIService.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.openai_service import OpenAIService


class TestOpenAIService:
    """Tests for OpenAIService class."""

    def test_is_configured_true(self):
        """Test is_configured returns True when API key is set."""
        with patch("app.services.openai_service.settings") as mock_settings:
            mock_settings.openai_api_key = "test-key"
            service = OpenAIService()

            assert service.is_configured() is True

    def test_is_configured_false(self):
        """Test is_configured returns False when API key is not set."""
        with patch("app.services.openai_service.settings") as mock_settings:
            mock_settings.openai_api_key = ""
            service = OpenAIService()

            assert service.is_configured() is False

    def test_ensure_client_raises_without_key(self):
        """Test _ensure_client raises error without API key."""
        with patch("app.services.openai_service.settings") as mock_settings:
            mock_settings.openai_api_key = ""
            service = OpenAIService()

            with pytest.raises(ValueError, match="OpenAI API key not configured"):
                service._ensure_client()

    def test_count_tokens(self):
        """Test token counting."""
        service = OpenAIService()

        # Mock the encoder to avoid network call
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = [1, 2, 3, 4]  # 4 tokens
        service._encoder = mock_encoder

        count = service.count_tokens("Hello, world!")

        assert isinstance(count, int)
        assert count == 4
        mock_encoder.encode.assert_called_once_with("Hello, world!")

    def test_count_tokens_empty_string(self):
        """Test token counting for empty string."""
        service = OpenAIService()

        # Mock the encoder
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = []  # 0 tokens
        service._encoder = mock_encoder

        count = service.count_tokens("")

        assert count == 0

    def test_encoder_lazy_initialization(self):
        """Test that encoder is lazily initialized."""
        service = OpenAIService()

        # Before accessing, _encoder should be None
        assert service._encoder is None

        # Mock tiktoken to avoid network call
        with patch("app.services.openai_service.tiktoken") as mock_tiktoken:
            mock_encoder = MagicMock()
            mock_tiktoken.get_encoding.return_value = mock_encoder

            # After accessing, encoder should be initialized
            _ = service.encoder
            assert service._encoder is mock_encoder
            mock_tiktoken.get_encoding.assert_called_once_with("cl100k_base")

    @pytest.mark.asyncio
    async def test_send_message_basic(self, mock_openai_client):
        """Test basic message sending."""
        service = OpenAIService()
        service.client = mock_openai_client

        messages = [{"role": "user", "content": "Hello!"}]

        with patch("app.services.openai_service.settings") as mock_settings:
            mock_settings.default_openai_model = "gpt-4o"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 4096

            response = await service.send_message(messages)

        assert response["content"] == "This is a test response from GPT."
        assert response["model"] == "gpt-4o"
        assert "usage" in response
        assert response["usage"]["input_tokens"] == 100
        assert response["usage"]["output_tokens"] == 50

    @pytest.mark.asyncio
    async def test_send_message_with_system_prompt(self, mock_openai_client):
        """Test message sending with system prompt."""
        service = OpenAIService()
        service.client = mock_openai_client

        messages = [{"role": "user", "content": "Hello!"}]

        with patch("app.services.openai_service.settings") as mock_settings:
            mock_settings.default_openai_model = "gpt-4o"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 4096

            await service.send_message(
                messages,
                system_prompt="You are a helpful assistant.",
            )

        # Verify API call includes system message
        call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
        api_messages = call_kwargs["messages"]
        assert api_messages[0]["role"] == "system"
        assert api_messages[0]["content"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_send_message_custom_parameters(self, mock_openai_client):
        """Test message sending with custom parameters."""
        service = OpenAIService()
        service.client = mock_openai_client

        messages = [{"role": "user", "content": "Hello!"}]

        with patch("app.services.openai_service.settings") as mock_settings:
            mock_settings.default_openai_model = "gpt-4o"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 4096

            await service.send_message(
                messages,
                model="gpt-4-turbo",
                temperature=0.5,
                max_tokens=2000,
            )

        call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4-turbo"
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["max_tokens"] == 2000

    def test_build_messages_with_memories_no_memories(self):
        """Test building messages without memories."""
        service = OpenAIService()

        memories = []
        context = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        current = "How are you?"

        messages = service.build_messages_with_memories(memories, context, current)

        # Should have context + current message
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hi"
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "How are you?"

    def test_build_messages_with_memories(self, sample_memories):
        """Test building messages with memories."""
        service = OpenAIService()

        context = []
        current = "What do you remember?"

        messages = service.build_messages_with_memories(sample_memories, context, current)

        # Should have: memory block, acknowledgment, current message
        assert len(messages) == 3

        # First message should contain memory block
        assert messages[0]["role"] == "user"
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in messages[0]["content"]

        # Second message should be acknowledgment
        assert messages[1]["role"] == "assistant"

        # Third message should be current message
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "What do you remember?"

    def test_build_messages_format_same_as_anthropic(self, sample_memories, sample_conversation_context):
        """Test that OpenAI uses same format as Anthropic."""
        from app.services.anthropic_service import AnthropicService

        openai_service = OpenAIService()
        anthropic_service = AnthropicService()

        current = "Test message"

        openai_messages = openai_service.build_messages_with_memories(
            sample_memories, sample_conversation_context, current
        )
        anthropic_messages = anthropic_service.build_messages_with_memories(
            sample_memories, sample_conversation_context, current
        )

        # Both should produce the same structure
        assert len(openai_messages) == len(anthropic_messages)

        for i, (openai_msg, anthropic_msg) in enumerate(zip(openai_messages, anthropic_messages)):
            assert openai_msg["role"] == anthropic_msg["role"]
            assert openai_msg["content"] == anthropic_msg["content"]
