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
        # - First message gets [CURRENT CONVERSATION] marker
        # - Final message with date + current
        assert len(messages) == 3

        # First two messages: conversation history as regular messages
        # First message gets [CURRENT CONVERSATION] marker
        assert messages[0]["role"] == "user"
        assert "[CURRENT CONVERSATION]" in messages[0]["content"]
        assert "Hi" in messages[0]["content"]
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
        # First message gets [CURRENT CONVERSATION] marker
        # + final message with date
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert "[CURRENT CONVERSATION]" in messages[0]["content"]
        assert "Hi" in messages[0]["content"]
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
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS" in first_content
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
        # 3. user: [CURRENT CONVERSATION] + "Hello!" (regular message with marker)
        # 4. assistant: "Hi there!" (regular message)
        # 5. final message (date + current)
        assert len(messages) == 5

        # Verify memory block is first
        first_content = messages[0]["content"]
        if isinstance(first_content, list):
            first_content = first_content[0]["text"]
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS" in first_content

        # Verify memory acknowledgment
        assert messages[1]["role"] == "assistant"
        assert "acknowledge" in messages[1]["content"].lower()

        # Verify history - first message has [CURRENT CONVERSATION] marker
        assert messages[2]["role"] == "user"
        assert "[CURRENT CONVERSATION]" in messages[2]["content"]
        assert "Hello!" in messages[2]["content"]
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

        # All memories (old and new) should be in the consolidated memory block
        first_content = messages[0]["content"]
        if isinstance(first_content, list):
            first_content = first_content[0]["text"]
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS. THESE ARE NOT PART OF THE CURRENT CONVERSATION]" in first_content
        assert "Old memory" in first_content
        assert "New memory" in first_content
        assert "[END MEMORIES]" in first_content

        # Final message should NOT have a separate new memories block
        final_content = messages[-1]["content"]
        assert "[NEW MEMORIES RETRIEVED THIS TURN]" not in final_content
        assert "[DATE CONTEXT]" in final_content
        assert "Test" in final_content


class TestCacheBreakpointPlacement:
    """Tests for cache_control marker placement in message building."""

    def test_cache_control_on_memory_block(self):
        """Test that cache_control is placed on memory block when >= 1024 tokens."""
        service = AnthropicService()

        # Mock encoder to return > 1024 tokens for memory block
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        memories = [
            {"id": "mem-1", "content": "Memory content " * 100, "created_at": "2024-01-01"},
        ]

        messages = service.build_messages_with_memories(memories, [], "Test")

        # First message (memory block) should have cache_control
        first_msg = messages[0]
        assert isinstance(first_msg["content"], list)
        assert first_msg["content"][0]["cache_control"]["type"] == "ephemeral"

    def test_no_cache_control_when_memory_block_too_small(self):
        """Test that cache_control is NOT placed when memory block < 1024 tokens."""
        service = AnthropicService()

        # Mock encoder to return < 1024 tokens
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        memories = [
            {"id": "mem-1", "content": "Short memory", "created_at": "2024-01-01"},
        ]

        messages = service.build_messages_with_memories(memories, [], "Test")

        # First message (memory block) should be plain string, not array with cache_control
        first_msg = messages[0]
        assert isinstance(first_msg["content"], str)

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

        assert "[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN]" in first_context_content1
        assert first_context_content1 == first_context_content2

    def test_multi_entity_header_changes_with_responding_entity(self):
        """Test that multi-entity header changes when responding entity changes."""
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

        # Headers should be different (different responding entity label)
        first_content_claude = messages_claude[0]["content"]
        first_content_gpt = messages_gpt[0]["content"]

        assert 'MESSAGES LABELED AS FROM "Claude" ARE YOURS' in first_content_claude
        assert 'MESSAGES LABELED AS FROM "GPT" ARE YOURS' in first_content_gpt
        assert first_content_claude != first_content_gpt


class TestTwoBreakpointCachingStrategy:
    """Tests for the two-breakpoint caching strategy."""

    def test_breakpoint_1_memory_block_with_cache_control(self):
        """Test breakpoint 1: memory block has cache_control when large enough."""
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

        messages = service.build_messages_with_memories(
            memories=memories,
            conversation_context=[],
            current_message="Test",
        )

        # Memory block (first message) should have cache_control
        assert messages[0]["role"] == "user"
        assert isinstance(messages[0]["content"], list)
        assert messages[0]["content"][0]["cache_control"]["type"] == "ephemeral"

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
