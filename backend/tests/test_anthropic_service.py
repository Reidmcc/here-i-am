"""
Unit tests for AnthropicService.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.anthropic_service import AnthropicService


class TestAnthropicService:
    """Tests for AnthropicService class."""

    def test_count_tokens(self):
        """Test token counting."""
        service = AnthropicService()

        # Mock the encoder to avoid network call
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = [1, 2, 3, 4]  # 4 tokens
        service._encoder = mock_encoder

        # Count tokens for a simple string
        count = service.count_tokens("Hello, world!")

        assert isinstance(count, int)
        assert count == 4
        mock_encoder.encode.assert_called_once_with("Hello, world!")

    def test_count_tokens_empty_string(self):
        """Test token counting for empty string."""
        service = AnthropicService()

        # Mock the encoder
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = []  # 0 tokens
        service._encoder = mock_encoder

        count = service.count_tokens("")

        assert count == 0

    def test_count_tokens_long_text(self):
        """Test token counting for longer text."""
        service = AnthropicService()

        # Mock the encoder
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(150))  # 150 tokens
        service._encoder = mock_encoder

        long_text = "This is a test. " * 100
        count = service.count_tokens(long_text)

        assert count == 150

    def test_encoder_lazy_initialization(self):
        """Test that encoder is lazily initialized."""
        service = AnthropicService()

        # Before accessing, _encoder should be None
        assert service._encoder is None

        # Mock tiktoken to avoid network call
        with patch("app.services.anthropic_service.tiktoken") as mock_tiktoken:
            mock_encoder = MagicMock()
            mock_tiktoken.get_encoding.return_value = mock_encoder

            # After accessing, encoder should be initialized
            _ = service.encoder
            assert service._encoder is mock_encoder
            mock_tiktoken.get_encoding.assert_called_once_with("cl100k_base")

    @pytest.mark.asyncio
    async def test_send_message_basic(self, mock_anthropic_client):
        """Test basic message sending."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        response = await service.send_message(messages)

        assert response["content"] == "This is a test response from Claude."
        assert response["model"] == "claude-sonnet-4-5-20250929"
        assert "usage" in response
        assert response["usage"]["input_tokens"] == 100
        assert response["usage"]["output_tokens"] == 50

    @pytest.mark.asyncio
    async def test_send_message_with_system_prompt(self, mock_anthropic_client):
        """Test message sending with system prompt."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(
            messages,
            system_prompt="You are a helpful assistant.",
        )

        # Verify system prompt was passed to API
        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_send_message_no_system_prompt(self, mock_anthropic_client):
        """Test message sending without system prompt."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(messages, system_prompt=None)

        # Verify system prompt was NOT passed to API
        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert "system" not in call_kwargs

    @pytest.mark.asyncio
    async def test_send_message_custom_parameters(self, mock_anthropic_client):
        """Test message sending with custom parameters."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(
            messages,
            model="claude-opus-4-20250514",
            temperature=0.5,
            max_tokens=2000,
        )

        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-20250514"
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["max_tokens"] == 2000

    def test_build_messages_with_memories_no_memories(self):
        """Test building messages without memories."""
        service = AnthropicService()

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
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hello!"
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "How are you?"

    def test_build_messages_with_memories(self, sample_memories):
        """Test building messages with memories."""
        service = AnthropicService()

        context = []
        current = "What do you remember?"

        messages = service.build_messages_with_memories(sample_memories, context, current)

        # Should have: memory block, acknowledgment, current message
        assert len(messages) == 3

        # First message should contain memory block
        assert messages[0]["role"] == "user"
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in messages[0]["content"]
        assert "I remember you mentioned enjoying programming" in messages[0]["content"]
        assert "[END MEMORIES]" in messages[0]["content"]

        # Second message should be acknowledgment
        assert messages[1]["role"] == "assistant"
        assert "acknowledge" in messages[1]["content"].lower()

        # Third message should be current message
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "What do you remember?"

    def test_build_messages_with_memories_and_context(self, sample_memories, sample_conversation_context):
        """Test building messages with both memories and context."""
        service = AnthropicService()

        current = "Tell me more."

        messages = service.build_messages_with_memories(
            sample_memories,
            sample_conversation_context,
            current
        )

        # Should have: memory block, acknowledgment, context (2), current message
        assert len(messages) == 5

        # Verify memory block is first
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in messages[0]["content"]

        # Verify context is preserved after acknowledgment
        assert messages[2]["content"] == "Hello!"
        assert messages[3]["content"] == "Hi there!"

        # Verify current message is last
        assert messages[4]["content"] == "Tell me more."

    def test_build_messages_memory_format(self, sample_memories):
        """Test that memories are formatted correctly."""
        service = AnthropicService()

        messages = service.build_messages_with_memories(sample_memories, [], "Test")

        memory_block = messages[0]["content"]

        # Check formatting
        assert "Memory (from 2024-01-01, retrieved 3 times):" in memory_block
        assert '"I remember you mentioned enjoying programming."' in memory_block
        assert "Memory (from 2024-01-02, retrieved 1 time" in memory_block  # times/time varies
