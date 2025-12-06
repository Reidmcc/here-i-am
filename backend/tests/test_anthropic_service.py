"""
Unit tests for AnthropicService.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.anthropic_service import AnthropicService


@pytest.fixture
def mock_encoder():
    """Create a mock encoder that returns predictable token counts."""
    mock = MagicMock()
    # Return a list with length based on word count approximation
    mock.encode.side_effect = lambda text: list(range(len(text.split())))
    return mock


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
        """Test message sending with system prompt - now includes cache_control."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(
            messages,
            system_prompt="You are a helpful assistant.",
        )

        # Verify system prompt was passed with cache_control format
        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert "system" in call_kwargs
        # With caching enabled, system prompt is wrapped in content array format
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert system[0]["type"] == "text"
        assert system[0]["text"] == "You are a helpful assistant."
        assert "cache_control" in system[0]

    @pytest.mark.asyncio
    async def test_send_message_with_system_prompt_no_caching(self, mock_anthropic_client):
        """Test message sending with system prompt when caching disabled."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(
            messages,
            system_prompt="You are a helpful assistant.",
            enable_caching=False,
        )

        # Verify system prompt was passed as plain string
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

    def test_build_messages_with_memories_no_memories(self, mock_encoder):
        """Test building messages without memories but with conversation context."""
        service = AnthropicService()
        service._encoder = mock_encoder

        memories = []
        context = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        current = "How are you?"

        messages = service.build_messages_with_memories(memories, context, current)

        # With caching enabled but no memories:
        # - History is regular alternating messages (not cached)
        # - Final message with date + current
        assert len(messages) == 3

        # First two messages: conversation history as regular messages
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hi"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hello!"

        # Third message: final message with date context + current
        assert messages[2]["role"] == "user"
        assert "[DATE CONTEXT]" in messages[2]["content"]
        assert "How are you?" in messages[2]["content"]

    def test_build_messages_with_memories_no_memories_no_caching(self, mock_encoder):
        """Test building messages without memories and caching disabled."""
        service = AnthropicService()
        service._encoder = mock_encoder

        memories = []
        context = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        current = "How are you?"

        messages = service.build_messages_with_memories(
            memories, context, current, enable_caching=False
        )

        # With caching disabled, context is passed through individually
        # + final message with date
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hi"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hello!"
        assert messages[2]["role"] == "user"
        assert "How are you?" in messages[2]["content"]

    def test_build_messages_with_memories(self, sample_memories, mock_encoder):
        """Test building messages with memories."""
        service = AnthropicService()
        service._encoder = mock_encoder

        context = []
        current = "What do you remember?"

        messages = service.build_messages_with_memories(sample_memories, context, current)

        # Should have: memory block, acknowledgment, final message (with date)
        assert len(messages) == 3

        # First message should contain memory block
        assert messages[0]["role"] == "user"
        first_content = messages[0]["content"]
        if isinstance(first_content, list):
            first_content = first_content[0]["text"]
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in first_content
        assert "I remember you mentioned enjoying programming" in first_content
        assert "[END MEMORIES]" in first_content

        # Second message should be acknowledgment
        assert messages[1]["role"] == "assistant"
        assert "acknowledge" in messages[1]["content"].lower()

        # Third message should be final message with date + current
        assert messages[2]["role"] == "user"
        assert "[DATE CONTEXT]" in messages[2]["content"]
        assert "What do you remember?" in messages[2]["content"]

    def test_build_messages_with_memories_and_context(
        self, sample_memories, sample_conversation_context, mock_encoder
    ):
        """Test building messages with both memories and context."""
        service = AnthropicService()
        service._encoder = mock_encoder

        current = "Tell me more."

        messages = service.build_messages_with_memories(
            sample_memories,
            sample_conversation_context,
            current
        )

        # New structure:
        # 1. memory block (cached)
        # 2. memory acknowledgment
        # 3. user: "Hello!" (regular message)
        # 4. assistant: "Hi there!" (regular message)
        # 5. final message (date + current)
        assert len(messages) == 5

        # Verify memory block is first
        first_content = messages[0]["content"]
        if isinstance(first_content, list):
            first_content = first_content[0]["text"]
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in first_content

        # Verify memory acknowledgment
        assert messages[1]["role"] == "assistant"
        assert "acknowledge" in messages[1]["content"].lower()

        # Verify history is regular alternating messages (not cached block)
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "Hello!"
        assert messages[3]["role"] == "assistant"
        assert messages[3]["content"] == "Hi there!"

        # Verify final message
        assert "Tell me more." in messages[4]["content"]

    def test_build_messages_memory_format(self, sample_memories, mock_encoder):
        """Test that memories are formatted correctly."""
        service = AnthropicService()
        service._encoder = mock_encoder

        messages = service.build_messages_with_memories(sample_memories, [], "Test")

        memory_content = messages[0]["content"]
        if isinstance(memory_content, list):
            memory_content = memory_content[0]["text"]

        # Check formatting - note: times_retrieved was removed for cache stability
        assert "Memory (from 2024-01-01):" in memory_content
        assert '"I remember you mentioned enjoying programming."' in memory_content
        assert "Memory (from 2024-01-02):" in memory_content

    def test_build_messages_caching_structure(self, sample_memories):
        """Test that cache_control markers are added correctly."""
        service = AnthropicService()

        # Mock both the encoder AND the cache to ensure we get >1024 tokens
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        # Also mock the cache service to not interfere
        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None  # Force cache miss
        service._cache_service = mock_cache

        messages = service.build_messages_with_memories(sample_memories, [], "Test")

        # First message (memory block) should have cache_control when >= 1024 tokens
        first_msg = messages[0]
        assert first_msg["role"] == "user"
        assert isinstance(first_msg["content"], list)
        assert first_msg["content"][0]["type"] == "text"
        assert "cache_control" in first_msg["content"][0]
        assert first_msg["content"][0]["cache_control"]["type"] == "ephemeral"

    def test_build_messages_new_memories_not_cached(self):
        """Test that new memories go to the uncached final message."""
        service = AnthropicService()

        # Mock encoder and cache
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        memories = [
            {"id": "old-1", "content": "Old memory", "created_at": "2024-01-01"},
            {"id": "new-1", "content": "New memory", "created_at": "2024-01-02"},
        ]
        new_memory_ids = {"new-1"}

        messages = service.build_messages_with_memories(
            memories, [], "Test", new_memory_ids=new_memory_ids
        )

        # Old memory should be in the cached block
        first_content = messages[0]["content"]
        if isinstance(first_content, list):
            first_content = first_content[0]["text"]
        assert "Old memory" in first_content
        assert "New memory" not in first_content

        # New memory should be in the final uncached message
        final_content = messages[-1]["content"]
        assert "[NEW MEMORIES RETRIEVED THIS TURN]" in final_content
        assert "New memory" in final_content
