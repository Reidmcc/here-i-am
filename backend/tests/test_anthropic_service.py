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

        # Conversation-first structure:
        # 1. user: [CONVERSATION HISTORY] + Hi
        # 2. assistant: Hello!
        # 3. user: [/CONVERSATION HISTORY] + [CURRENT USER MESSAGE] + date + current
        assert len(messages) == 3

        # First two messages: conversation history
        assert messages[0]["role"] == "user"
        assert "[CONVERSATION HISTORY]" in messages[0]["content"]
        assert "Hi" in messages[0]["content"]
        assert messages[1]["role"] == "assistant"

        # Third message: final combined message
        assert messages[2]["role"] == "user"
        assert "[/CONVERSATION HISTORY]" in messages[2]["content"]
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

        # Conversation-first structure (same as with caching):
        # 1. user: [CONVERSATION HISTORY] + Hi
        # 2. assistant: Hello!
        # 3. user: [/CONVERSATION HISTORY] + [CURRENT USER MESSAGE] + date + current
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert "[CONVERSATION HISTORY]" in messages[0]["content"]
        assert "Hi" in messages[0]["content"]
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"
        assert "How are you?" in messages[2]["content"]

    def test_build_messages_with_memories(self, sample_memories, mock_encoder):
        """Test building messages with memories."""
        service = AnthropicService()
        service._encoder = mock_encoder

        context = []
        current = "What do you remember?"

        messages = service.build_messages_with_memories(sample_memories, context, current)

        # Conversation-first structure with no context (memories only):
        # Single user message with: [CONVERSATION HISTORY] + [/CONVERSATION HISTORY] + [MEMORIES] + date + current
        assert len(messages) == 1

        # Single message should contain memories in the final block
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        if isinstance(content, list):
            content = content[0]["text"]
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in content
        assert "I remember you mentioned enjoying programming" in content
        assert "[/MEMORIES]" in content
        assert "[DATE CONTEXT]" in content
        assert "What do you remember?" in content

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

        # Conversation-first structure with memories and context:
        # 1. user: [CONVERSATION HISTORY] + Hello!
        # 2. assistant: Hi there!
        # 3. user: [/CONVERSATION HISTORY] + [MEMORIES] + memories + [/MEMORIES] + date + current
        assert len(messages) == 3

        # First message: conversation history start
        assert messages[0]["role"] == "user"
        assert "[CONVERSATION HISTORY]" in messages[0]["content"]
        assert "Hello!" in messages[0]["content"]

        # Second message: assistant response from history
        assert messages[1]["role"] == "assistant"
        assert "Hi there!" in messages[1]["content"]

        # Third message: combined final message
        assert messages[2]["role"] == "user"
        final_content = messages[2]["content"]
        assert "[/CONVERSATION HISTORY]" in final_content
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in final_content
        assert "I remember you mentioned enjoying programming" in final_content
        assert "[DATE CONTEXT]" in final_content
        assert "Tell me more." in final_content

    def test_build_messages_memory_format(self, sample_memories, mock_encoder):
        """Test that memories are formatted correctly."""
        service = AnthropicService()
        service._encoder = mock_encoder

        messages = service.build_messages_with_memories(sample_memories, [], "Test")

        # With no context, single message contains everything
        memory_content = messages[0]["content"]
        if isinstance(memory_content, list):
            memory_content = memory_content[0]["text"]

        # Check formatting - memories now include role, times_retrieved was removed for cache stability
        assert "Memory from assistant (from 2024-01-01):" in memory_content
        assert '"I remember you mentioned enjoying programming."' in memory_content
        assert "Memory from user (from 2024-01-02):" in memory_content

    def test_build_messages_caching_structure(self, sample_memories):
        """Test that cache_control markers are added correctly on conversation history."""
        service = AnthropicService()

        # Mock both the encoder AND the cache to ensure we get >1024 tokens
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        # Also mock the cache service to not interfere
        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None  # Force cache miss
        service._cache_service = mock_cache

        # With conversation-first caching, we need conversation history to get cache_control
        cached_context = [
            {"role": "user", "content": "Hello " * 100},
            {"role": "assistant", "content": "Hi " * 100},
        ]

        messages = service.build_messages_with_memories(
            sample_memories, [], "Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Last cached context message should have cache_control when >= 1024 tokens
        # Structure: context[0], context[1] with cache_control, final message
        last_cached_msg = messages[1]
        assert last_cached_msg["role"] == "assistant"
        assert isinstance(last_cached_msg["content"], list)
        assert last_cached_msg["content"][0]["type"] == "text"
        assert "cache_control" in last_cached_msg["content"][0]
        assert last_cached_msg["content"][0]["cache_control"]["type"] == "ephemeral"

    def test_build_messages_all_memories_consolidated(self):
        """Test that all memories (old and new) are consolidated into one block."""
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

        # All memories should be in the final message (conversation-first structure)
        final_content = messages[-1]["content"]
        if isinstance(final_content, list):
            final_content = final_content[0]["text"]
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in final_content
        assert "Old memory" in final_content
        assert "New memory" in final_content
        assert "[/MEMORIES]" in final_content
        assert "[DATE CONTEXT]" in final_content
        assert "Test" in final_content


class TestCacheBreakpointPlacement:
    """Tests for cache_control marker placement in message building."""

    def test_cache_control_on_conversation_history(self):
        """Test that cache_control is placed on last cached conversation history message."""
        service = AnthropicService()

        # Mock encoder to return > 1024 tokens for history
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Hello " * 100},
            {"role": "assistant", "content": "Hi " * 100},
        ]

        messages = service.build_messages_with_memories(
            [], [], "Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Last cached context message should have cache_control
        last_cached_msg = messages[1]
        assert isinstance(last_cached_msg["content"], list)
        assert last_cached_msg["content"][0]["cache_control"]["type"] == "ephemeral"

    def test_no_cache_control_when_history_too_small(self):
        """Test that cache_control is NOT placed when history < 1024 tokens."""
        service = AnthropicService()

        # Mock encoder to return < 1024 tokens
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Short message"},
            {"role": "assistant", "content": "Short reply"},
        ]

        messages = service.build_messages_with_memories(
            [], [], "Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Last cached context message should be plain string, not array with cache_control
        last_cached_msg = messages[1]
        assert isinstance(last_cached_msg["content"], str)

    def test_cache_control_on_last_cached_history_message(self):
        """Test that cache_control is placed on last cached history message."""
        service = AnthropicService()

        # Mock encoder to return > 1024 tokens for history
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        # Provide cached_context with multiple messages
        cached_context = [
            {"role": "user", "content": "First user message " * 50},
            {"role": "assistant", "content": "First assistant response " * 50},
            {"role": "user", "content": "Second user message " * 50},
            {"role": "assistant", "content": "Second assistant response " * 50},
        ]

        messages = service.build_messages_with_memories(
            memories=[],
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Find the last cached context message (4th message, index 3 in cached_context)
        # Messages: [CURRENT CONVERSATION] + cached_context[0], cached_context[1], cached_context[2], cached_context[3], final
        # The last cached context message should have cache_control
        cached_last_msg = messages[3]  # 4th message (0-indexed = 3)
        assert isinstance(cached_last_msg["content"], list)
        assert cached_last_msg["content"][0]["cache_control"]["type"] == "ephemeral"

    def test_no_cache_control_on_new_context(self):
        """Test that new_context messages don't have cache_control."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Cached message " * 50},
            {"role": "assistant", "content": "Cached response " * 50},
        ]
        new_context = [
            {"role": "user", "content": "New message"},
            {"role": "assistant", "content": "New response"},
        ]

        messages = service.build_messages_with_memories(
            memories=[],
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=new_context,
        )

        # New context messages should be plain strings
        # Structure: cached_context[0], cached_context[1] (with cache_control), new_context[0], new_context[1], final
        new_user_msg = messages[2]  # First new context message
        new_asst_msg = messages[3]  # Second new context message

        assert isinstance(new_user_msg["content"], str)
        assert isinstance(new_asst_msg["content"], str)

    def test_message_structure_identical_across_calls(self):
        """Test that message structure is identical when inputs are identical."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        memories = [
            {"id": "mem-1", "content": "Memory 1 " * 50, "created_at": "2024-01-01"},
            {"id": "mem-2", "content": "Memory 2 " * 50, "created_at": "2024-01-02"},
        ]
        cached_context = [
            {"role": "user", "content": "Hello " * 50},
            {"role": "assistant", "content": "Hi " * 50},
        ]

        from datetime import datetime
        with patch.object(service, 'build_messages_with_memories') as original:
            original.side_effect = lambda *args, **kwargs: AnthropicService.build_messages_with_memories(service, *args, **kwargs)

        # Build messages twice with identical inputs
        messages1 = service.build_messages_with_memories(
            memories=memories,
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        messages2 = service.build_messages_with_memories(
            memories=memories,
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Structure should be identical (ignoring date which changes)
        assert len(messages1) == len(messages2)
        for i, (m1, m2) in enumerate(zip(messages1, messages2)):
            assert m1["role"] == m2["role"], f"Role mismatch at index {i}"
            # Compare content structure
            c1 = m1["content"]
            c2 = m2["content"]
            if isinstance(c1, list) and isinstance(c2, list):
                assert len(c1) == len(c2), f"Content array length mismatch at index {i}"
                for j, (block1, block2) in enumerate(zip(c1, c2)):
                    assert block1.get("type") == block2.get("type"), f"Block type mismatch at {i}.{j}"
                    assert block1.get("cache_control") == block2.get("cache_control"), f"Cache control mismatch at {i}.{j}"

    def test_multi_entity_header_consistency(self):
        """Test that multi-entity header is consistent across calls."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(100))  # Below cache threshold
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        entity_labels = {"entity-a": "Claude", "entity-b": "GPT"}
        cached_context = [
            {"role": "user", "content": "[Human]: Hello"},
            {"role": "assistant", "content": "[Claude]: Hi"},
        ]

        # Build messages twice
        messages1 = service.build_messages_with_memories(
            memories=[],
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
            is_multi_entity=True,
            entity_labels=entity_labels,
            responding_entity_label="Claude",
        )

        messages2 = service.build_messages_with_memories(
            memories=[],
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
            is_multi_entity=True,
            entity_labels=entity_labels,
            responding_entity_label="Claude",
        )

        # First cached context message should have multi-entity header
        first_context_content1 = messages1[0]["content"]
        first_context_content2 = messages2[0]["content"]

        assert "[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN. DO NOT WRITE FOR OTHER PARTICIPANTS. DO NOT LABEL YOUR MESSAGES WITH YOUR NAME.]" in first_context_content1
        assert first_context_content1 == first_context_content2

    def test_multi_entity_header_consistent_across_entities(self):
        """Test that multi-entity header is consistent regardless of responding entity."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(100))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        entity_labels = {"entity-a": "Claude", "entity-b": "GPT"}
        cached_context = [
            {"role": "user", "content": "[Human]: Hello"},
        ]

        # Build for Claude
        messages_claude = service.build_messages_with_memories(
            memories=[],
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
            is_multi_entity=True,
            entity_labels=entity_labels,
            responding_entity_label="Claude",
        )

        # Build for GPT
        messages_gpt = service.build_messages_with_memories(
            memories=[],
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
            is_multi_entity=True,
            entity_labels=entity_labels,
            responding_entity_label="GPT",
        )

        # Both should contain the multi-entity header
        first_content_claude = messages_claude[0]["content"]
        first_content_gpt = messages_gpt[0]["content"]

        assert "[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN. DO NOT WRITE FOR OTHER PARTICIPANTS. DO NOT LABEL YOUR MESSAGES WITH YOUR NAME.]" in first_content_claude
        assert "[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN. DO NOT WRITE FOR OTHER PARTICIPANTS. DO NOT LABEL YOUR MESSAGES WITH YOUR NAME.]" in first_content_gpt
        # The header is now the same regardless of responding entity (simplified for cache stability)
        assert first_content_claude == first_content_gpt


class TestTwoBreakpointCachingStrategy:
    """Tests for the conversation-first caching strategy."""

    def test_conversation_history_cached_not_memories(self):
        """Test that conversation history is cached, not memories."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(2000))  # > 1024
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        memories = [
            {"id": "mem-1", "content": "Memory " * 200, "created_at": "2024-01-01"},
        ]
        cached_context = [
            {"role": "user", "content": "Question " * 100},
            {"role": "assistant", "content": "Answer " * 100},
        ]

        messages = service.build_messages_with_memories(
            memories=memories,
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Last cached context message should have cache_control (not memory block)
        # Structure: context[0], context[1] with cache_control, final with memories
        last_cached = messages[1]
        assert last_cached["role"] == "assistant"
        assert isinstance(last_cached["content"], list)
        assert last_cached["content"][0]["cache_control"]["type"] == "ephemeral"

        # Final message should contain memories (not cached)
        final_msg = messages[2]
        assert final_msg["role"] == "user"
        assert isinstance(final_msg["content"], str)
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in final_msg["content"]

    def test_breakpoint_2_last_cached_context_has_cache_control(self):
        """Test breakpoint 2: last cached context message has cache_control."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(2000))  # > 1024
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Question " * 100},
            {"role": "assistant", "content": "Answer " * 100},
        ]

        messages = service.build_messages_with_memories(
            memories=[],
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Last cached context message should have cache_control
        # Structure: cached[0] with marker, cached[1] with cache_control, final
        last_cached = messages[1]  # Second message (last of cached context)
        assert last_cached["role"] == "assistant"
        assert isinstance(last_cached["content"], list)
        assert last_cached["content"][0]["cache_control"]["type"] == "ephemeral"

    def test_conversation_first_structure_with_memories_and_context(self):
        """Test conversation-first structure with single cache breakpoint."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(2000))  # > 1024
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        memories = [
            {"id": "mem-1", "content": "Memory " * 200, "created_at": "2024-01-01"},
        ]
        cached_context = [
            {"role": "user", "content": "Question " * 100},
            {"role": "assistant", "content": "Answer " * 100},
        ]

        messages = service.build_messages_with_memories(
            memories=memories,
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Structure should be (3 messages for proper alternation):
        # 0: cached context[0] with [CONVERSATION HISTORY] marker
        # 1: cached context[1] with cache_control (cache breakpoint)
        # 2: combined: [/CONVERSATION HISTORY] + memories + [CURRENT USER MESSAGE] + date + current message

        assert len(messages) == 3

        # First cached context (with CONVERSATION HISTORY marker)
        assert messages[0]["role"] == "user"
        assert "[CONVERSATION HISTORY]" in messages[0]["content"]
        assert "Question " in messages[0]["content"]

        # Cache breakpoint: last cached context
        assert messages[1]["role"] == "assistant"
        assert isinstance(messages[1]["content"], list)
        assert messages[1]["content"][0]["cache_control"]["type"] == "ephemeral"

        # Combined final message (maintains proper user/assistant alternation)
        assert messages[2]["role"] == "user"
        assert "[/CONVERSATION HISTORY]" in messages[2]["content"]
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in messages[2]["content"]
        assert "Memory " in messages[2]["content"]
        assert "[CURRENT USER MESSAGE]" in messages[2]["content"]
        assert "[DATE CONTEXT]" in messages[2]["content"]
        assert "Test" in messages[2]["content"]

    def test_new_context_after_cached_context(self):
        """Test that new context appears after cached context without cache_control."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(2000))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Old question " * 100},
            {"role": "assistant", "content": "Old answer " * 100},
        ]
        new_context = [
            {"role": "user", "content": "New question"},
            {"role": "assistant", "content": "New answer"},
        ]

        messages = service.build_messages_with_memories(
            memories=[],
            conversation_context=[],
            current_message="Current",
            cached_context=cached_context,
            new_context=new_context,
        )

        # Structure:
        # 0: cached[0] with marker
        # 1: cached[1] with cache_control (breakpoint 2)
        # 2: new[0] - NO cache_control
        # 3: new[1] - NO cache_control
        # 4: final message

        # New context messages should be plain strings
        assert messages[2]["role"] == "user"
        assert isinstance(messages[2]["content"], str)
        assert "New question" in messages[2]["content"]

        assert messages[3]["role"] == "assistant"
        assert isinstance(messages[3]["content"], str)
        assert "New answer" in messages[3]["content"]
