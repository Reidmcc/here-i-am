"""
Unit tests for SessionManager.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
import uuid

from app.services.session_manager import SessionManager, ConversationSession, MemoryEntry, _add_cache_control_to_tool_result
from app.models import Conversation, Message, MessageRole, ConversationType


class TestMemoryEntry:
    """Tests for MemoryEntry dataclass."""

    def test_memory_entry_creation(self):
        """Test creating a MemoryEntry."""
        entry = MemoryEntry(
            id="mem-123",
            conversation_id="conv-456",
            role="assistant",
            content="Test content",
            created_at="2024-01-01T12:00:00",
            times_retrieved=5,
            score=0.95,
        )

        assert entry.id == "mem-123"
        assert entry.conversation_id == "conv-456"
        assert entry.role == "assistant"
        assert entry.content == "Test content"
        assert entry.times_retrieved == 5
        assert entry.score == 0.95

    def test_memory_entry_default_score(self):
        """Test MemoryEntry default score."""
        entry = MemoryEntry(
            id="mem-123",
            conversation_id="conv-456",
            role="assistant",
            content="Test content",
            created_at="2024-01-01",
            times_retrieved=0,
        )

        assert entry.score == 0.0


class TestConversationSession:
    """Tests for ConversationSession dataclass."""

    def test_session_creation(self):
        """Test creating a ConversationSession."""
        session = ConversationSession(
            conversation_id="conv-123",
            model="claude-sonnet-4-5-20250929",
            temperature=0.8,
            max_tokens=2000,
            system_prompt="You are helpful.",
            entity_id="test-entity",
        )

        assert session.conversation_id == "conv-123"
        assert session.model == "claude-sonnet-4-5-20250929"
        assert session.temperature == 0.8
        assert session.max_tokens == 2000
        assert session.system_prompt == "You are helpful."
        assert session.entity_id == "test-entity"
        assert session.conversation_context == []
        assert session.session_memories == {}
        assert session.retrieved_ids == set()
        assert session.in_context_ids == set()

    def test_add_memory_new(self):
        """Test adding a new memory."""
        session = ConversationSession(conversation_id="conv-123")
        memory = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Test",
            created_at="2024-01-01",
            times_retrieved=1,
        )

        added, is_new_retrieval = session.add_memory(memory)

        assert added is True
        assert is_new_retrieval is True
        assert "mem-1" in session.retrieved_ids
        assert "mem-1" in session.in_context_ids
        assert "mem-1" in session.session_memories
        assert session.session_memories["mem-1"] == memory

    def test_add_memory_duplicate(self):
        """Test adding a memory already in context is rejected."""
        session = ConversationSession(conversation_id="conv-123")
        memory = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Test",
            created_at="2024-01-01",
            times_retrieved=1,
        )

        session.add_memory(memory)
        added, is_new_retrieval = session.add_memory(memory)

        assert added is False
        assert is_new_retrieval is False
        assert len(session.session_memories) == 1

    def test_add_memory_restore_trimmed(self):
        """Test re-adding a previously trimmed memory restores it without updating count."""
        session = ConversationSession(conversation_id="conv-123")
        memory = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Test",
            created_at="2024-01-01",
            times_retrieved=1,
            score=0.8,
        )

        # Add memory initially
        session.add_memory(memory)
        assert "mem-1" in session.in_context_ids
        assert "mem-1" in session.retrieved_ids

        # Simulate trimming by removing from in_context_ids only
        session.in_context_ids.discard("mem-1")
        assert "mem-1" not in session.in_context_ids
        assert "mem-1" in session.retrieved_ids  # Still in retrieved_ids

        # Re-add the same memory (with potentially different score)
        memory2 = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Test",
            created_at="2024-01-01",
            times_retrieved=1,
            score=0.95,  # Higher score this time
        )
        added, is_new_retrieval = session.add_memory(memory2)

        # Should be added back to context but not trigger new retrieval count
        assert added is True
        assert is_new_retrieval is False
        assert "mem-1" in session.in_context_ids
        assert "mem-1" in session.retrieved_ids
        # Score should be updated
        assert session.session_memories["mem-1"].score == 0.95

    def test_get_memories_for_injection_sorted(self):
        """Test memories are sorted by ID for stable caching."""
        session = ConversationSession(conversation_id="conv-123")

        # Add memories with different scores but they should be sorted by ID
        for i, score in enumerate([0.5, 0.9, 0.7]):
            memory = MemoryEntry(
                id=f"mem-{i}",
                conversation_id="old-conv",
                role="assistant",
                content=f"Content {i}",
                created_at="2024-01-01",
                times_retrieved=1,
                score=score,
            )
            session.add_memory(memory)

        memories = session.get_memories_for_injection()

        # Should be sorted by ID (for cache stability), not score
        assert len(memories) == 3
        assert memories[0]["id"] == "mem-0"
        assert memories[1]["id"] == "mem-1"
        assert memories[2]["id"] == "mem-2"

    def test_get_memories_for_injection_format(self):
        """Test memories are formatted correctly for injection."""
        session = ConversationSession(conversation_id="conv-123")

        memory = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Test content",
            created_at="2024-01-01",
            times_retrieved=5,
            score=0.9,
        )
        session.add_memory(memory)

        memories = session.get_memories_for_injection()

        assert len(memories) == 1
        assert memories[0]["id"] == "mem-1"
        assert memories[0]["content"] == "Test content"
        assert memories[0]["created_at"] == "2024-01-01"
        assert memories[0]["times_retrieved"] == 5
        assert memories[0]["role"] == "assistant"

    def test_add_exchange(self):
        """Test adding a conversation exchange."""
        session = ConversationSession(conversation_id="conv-123")

        session.add_exchange("Hello!", "Hi there!")

        assert len(session.conversation_context) == 2
        assert session.conversation_context[0] == {"role": "user", "content": "Hello!"}
        assert session.conversation_context[1] == {"role": "assistant", "content": "Hi there!"}

    def test_trim_memories_to_limit_no_trimming_needed(self):
        """Test that memories are not trimmed when under limit."""
        session = ConversationSession(conversation_id="conv-123")

        # Add a memory
        memory = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Test content",
            created_at="2024-01-01",
            times_retrieved=1,
            score=0.9,
        )
        session.add_memory(memory)

        # Mock token counter that returns small count
        count_tokens = lambda x: 100

        removed = session.trim_memories_to_limit(max_tokens=40000, count_tokens_fn=count_tokens)

        assert removed == []
        assert "mem-1" in session.session_memories
        assert "mem-1" in session.retrieved_ids
        assert "mem-1" in session.in_context_ids

    def test_trim_memories_to_limit_removes_oldest_first(self):
        """Test that oldest-retrieved memories are removed from context first (FIFO)."""
        session = ConversationSession(conversation_id="conv-123")

        # Add memories in order - first added = first to be removed from context
        for i in range(3):
            memory = MemoryEntry(
                id=f"mem-{i}",
                conversation_id="old-conv",
                role="assistant",
                content=f"Memory content {i}",
                created_at="2024-01-01",
                times_retrieved=1,
                score=0.5 + i * 0.1,  # Different scores
            )
            session.add_memory(memory)

        # Token counter that forces trimming (returns decreasing values)
        call_count = [0]
        def count_tokens(x):
            call_count[0] += 1
            # Return high value first, then lower to simulate trimming effect
            if call_count[0] <= 2:
                return 50000  # Over limit
            return 100  # Under limit after removing 2

        removed = session.trim_memories_to_limit(max_tokens=40000, count_tokens_fn=count_tokens)

        # Should have removed mem-0 and mem-1 from context (oldest retrieved first)
        assert "mem-0" in removed
        assert "mem-1" in removed
        assert "mem-2" not in removed

        # mem-0 and mem-1 should be removed from in_context_ids only
        assert "mem-0" not in session.in_context_ids
        assert "mem-1" not in session.in_context_ids

        # But they should still be in retrieved_ids and session_memories
        # (to prevent re-incrementing retrieval count and to allow restoration)
        assert "mem-0" in session.retrieved_ids
        assert "mem-0" in session.session_memories
        assert "mem-1" in session.retrieved_ids
        assert "mem-1" in session.session_memories

        # mem-2 should still be in context
        assert "mem-2" in session.in_context_ids
        assert "mem-2" in session.retrieved_ids
        assert "mem-2" in session.session_memories

    def test_trim_context_to_limit_no_trimming_needed(self):
        """Test that context is not trimmed when under limit."""
        session = ConversationSession(conversation_id="conv-123")
        session.add_exchange("Hello", "Hi there")
        session.add_exchange("How are you?", "I'm well!")

        count_tokens = lambda x: 100

        removed = session.trim_context_to_limit(
            max_tokens=150000,
            count_tokens_fn=count_tokens,
            current_message="New message"
        )

        assert removed == 0
        assert len(session.conversation_context) == 4

    def test_trim_context_to_limit_removes_oldest_first(self):
        """Test that oldest messages are removed first (FIFO)."""
        session = ConversationSession(conversation_id="conv-123")

        # Add several exchanges
        session.add_exchange("First question", "First answer")
        session.add_exchange("Second question", "Second answer")
        session.add_exchange("Third question", "Third answer")

        # Token counter that forces trimming
        call_count = [0]
        def count_tokens(x):
            call_count[0] += 1
            if call_count[0] <= 2:
                return 200000  # Over limit
            return 100  # Under limit after removing some

        removed = session.trim_context_to_limit(
            max_tokens=150000,
            count_tokens_fn=count_tokens,
            current_message="New message"
        )

        # Should have removed 4 messages (2 exchanges worth)
        assert removed == 4

        # Only the third exchange should remain
        assert len(session.conversation_context) == 2
        assert session.conversation_context[0]["content"] == "Third question"
        assert session.conversation_context[1]["content"] == "Third answer"


class TestSessionManager:
    """Tests for SessionManager class."""

    def test_create_session(self):
        """Test creating a new session."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000

            session = manager.create_session("conv-123")

        assert session.conversation_id == "conv-123"
        assert "conv-123" in manager._sessions

    def test_create_session_with_custom_params(self):
        """Test creating a session with custom parameters."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000

            session = manager.create_session(
                conversation_id="conv-123",
                model="claude-opus-4-20250514",
                temperature=0.5,
                max_tokens=2000,
                system_prompt="Be helpful",
                entity_id="custom-entity",
            )

        assert session.model == "claude-opus-4-20250514"
        assert session.temperature == 0.5
        assert session.max_tokens == 2000
        assert session.system_prompt == "Be helpful"
        assert session.entity_id == "custom-entity"

    def test_create_session_uses_entity_default_model(self):
        """Test session uses entity's default model."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_entity = MagicMock()
            mock_entity.default_model = "gpt-4o"
            mock_entity.llm_provider = "openai"
            mock_settings.get_entity_by_index.return_value = mock_entity
            mock_settings.get_default_model_for_provider.return_value = "gpt-4o"
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000

            session = manager.create_session(
                conversation_id="conv-123",
                entity_id="gpt-entity",
            )

        assert session.model == "gpt-4o"

    def test_get_session_exists(self):
        """Test getting an existing session."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000

            created = manager.create_session("conv-123")
            retrieved = manager.get_session("conv-123")

        assert retrieved is created

    def test_get_session_not_exists(self):
        """Test getting a non-existent session."""
        manager = SessionManager()

        result = manager.get_session("nonexistent")

        assert result is None

    def test_close_session(self):
        """Test closing a session."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000

            manager.create_session("conv-123")
            assert "conv-123" in manager._sessions

            manager.close_session("conv-123")
            assert "conv-123" not in manager._sessions

    def test_close_session_not_exists(self):
        """Test closing a non-existent session doesn't error."""
        manager = SessionManager()

        # Should not raise
        manager.close_session("nonexistent")

    @pytest.mark.asyncio
    async def test_load_session_from_db(self, db_session, sample_conversation, sample_messages):
        """Test loading a session from the database."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(
                sample_conversation.id,
                db_session
            )

        assert session is not None
        assert session.conversation_id == sample_conversation.id
        assert session.model == sample_conversation.llm_model_used
        assert len(session.conversation_context) == 2  # Two sample messages

    @pytest.mark.asyncio
    async def test_load_session_from_db_not_found(self, db_session):
        """Test loading a non-existent conversation returns None."""
        manager = SessionManager()

        session = await manager.load_session_from_db("nonexistent-id", db_session)

        assert session is None

    @pytest.mark.asyncio
    async def test_load_session_from_db_with_retrieved_memories(
        self, db_session, sample_conversation, sample_messages
    ):
        """Test loading session includes previously retrieved memories."""
        manager = SessionManager()

        retrieved_id = sample_messages[0].id

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(
                return_value={retrieved_id}
            )
            mock_memory.get_full_memory_content = AsyncMock(return_value={
                "id": retrieved_id,
                "conversation_id": "other-conv",
                "role": "assistant",
                "content": "Retrieved memory content",
                "created_at": "2024-01-01T12:00:00",
                "times_retrieved": 3,
            })
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(
                sample_conversation.id,
                db_session
            )

        assert retrieved_id in session.retrieved_ids
        assert retrieved_id in session.session_memories

    @pytest.mark.asyncio
    async def test_process_message_basic(self, db_session, sample_conversation):
        """Test basic message processing."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False  # No memory retrieval
            mock_llm.build_messages_with_memories.return_value = [
                {"role": "user", "content": "Hello"}
            ]
            mock_llm.send_message = AsyncMock(return_value={
                "content": "Hi there!",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            })
            mock_llm.count_tokens = MagicMock(return_value=10)  # Mock token counting
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000

            session = manager.create_session(sample_conversation.id)
            result = await manager.process_message(session, "Hello", db_session)

        assert result["content"] == "Hi there!"
        assert len(session.conversation_context) == 2  # User + assistant
        assert session.conversation_context[0]["content"] == "Hello"
        assert session.conversation_context[1]["content"] == "Hi there!"
        assert result["trimmed_memory_ids"] == []
        assert result["trimmed_context_messages"] == 0

    @pytest.mark.asyncio
    async def test_process_message_with_memory_retrieval(self, db_session, sample_conversation):
        """Test message processing with memory retrieval."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure memory retrieval
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[
                {
                    "id": "mem-1",
                    "score": 0.9,
                    "conversation_id": "old-conv",
                    "created_at": "2024-01-01",
                    "role": "assistant",
                    "last_retrieved_at": None,
                }
            ])
            mock_memory.get_full_memory_content = AsyncMock(return_value={
                "id": "mem-1",
                "conversation_id": "old-conv",
                "role": "assistant",
                "content": "Previous memory content",
                "created_at": "2024-01-01",
                "times_retrieved": 2,
                "last_retrieved_at": None,
            })
            mock_memory.update_retrieval_count = AsyncMock()

            mock_llm.build_messages_with_memories.return_value = [
                {"role": "user", "content": "With memory context"}
            ]
            mock_llm.send_message = AsyncMock(return_value={
                "content": "Response with memory",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 50, "output_tokens": 20},
                "stop_reason": "end_turn",
            })
            mock_llm.count_tokens = MagicMock(return_value=100)  # Mock token counting

            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000
            mock_settings.significance_half_life_days = 60
            mock_settings.recency_boost_strength = 1.0
            mock_settings.significance_floor = 0.01
            mock_settings.retrieval_candidate_multiplier = 2

            session = manager.create_session(sample_conversation.id)
            result = await manager.process_message(session, "Hello", db_session)

        # Should have retrieved memories
        assert len(result["new_memories_retrieved"]) == 1
        assert result["new_memories_retrieved"][0]["id"] == "mem-1"
        assert result["total_memories_in_context"] == 1

        # Memory should be in session
        assert "mem-1" in session.session_memories

        # Update count should have been called
        mock_memory.update_retrieval_count.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_message_deduplicates_memories(self, db_session, sample_conversation):
        """Test that already-retrieved memories are not retrieved again."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[
                {"id": "mem-1", "score": 0.9, "conversation_id": "old-conv", "created_at": "2024-01-01", "last_retrieved_at": None}
            ])
            mock_memory.get_full_memory_content = AsyncMock(return_value={
                "id": "mem-1",
                "conversation_id": "old-conv",
                "role": "assistant",
                "content": "Memory",
                "created_at": "2024-01-01",
                "times_retrieved": 1,
                "last_retrieved_at": None,
            })
            mock_memory.update_retrieval_count = AsyncMock()

            mock_llm.build_messages_with_memories.return_value = []
            mock_llm.send_message = AsyncMock(return_value={
                "content": "Response",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            })
            mock_llm.count_tokens = MagicMock(return_value=50)  # Mock token counting

            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000
            mock_settings.significance_half_life_days = 60
            mock_settings.recency_boost_strength = 1.0
            mock_settings.significance_floor = 0.01
            mock_settings.retrieval_candidate_multiplier = 2

            session = manager.create_session(sample_conversation.id)

            # First message should retrieve memory
            await manager.process_message(session, "First", db_session)
            assert mock_memory.update_retrieval_count.call_count == 1

            # Second message - memory should be excluded
            mock_memory.search_memories.reset_mock()
            mock_memory.update_retrieval_count.reset_mock()

            await manager.process_message(session, "Second", db_session)

            # Search should have been called with exclude_ids
            call_kwargs = mock_memory.search_memories.call_args.kwargs
            assert "mem-1" in call_kwargs["exclude_ids"]

            # Update count should NOT be called again for same memory
            mock_memory.update_retrieval_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_message_restores_trimmed_memory_without_count_update(
        self, db_session, sample_conversation
    ):
        """Test that trimmed memories can be restored without updating retrieval count."""
        # This tests the ConversationSession behavior for restored memories
        session = ConversationSession(conversation_id="conv-123")

        memory = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Memory content",
            created_at="2024-01-01",
            times_retrieved=1,
            score=0.9,
        )

        # First retrieval - should be marked as new
        added, is_new_retrieval = session.add_memory(memory)
        assert added is True
        assert is_new_retrieval is True
        assert "mem-1" in session.in_context_ids
        assert "mem-1" in session.retrieved_ids

        # Simulate trimming by removing from in_context_ids only
        session.in_context_ids.discard("mem-1")
        assert "mem-1" not in session.in_context_ids
        assert "mem-1" in session.retrieved_ids  # Still tracked

        # Re-add the same memory (as if search returned it again)
        memory2 = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Memory content",
            created_at="2024-01-01",
            times_retrieved=1,
            score=0.95,  # Maybe different score this time
        )
        added, is_new_retrieval = session.add_memory(memory2)

        # Should be added back to context
        assert added is True
        # But should NOT be treated as a new retrieval (no count update needed)
        assert is_new_retrieval is False
        assert "mem-1" in session.in_context_ids
        assert "mem-1" in session.retrieved_ids


class TestMultiEntityMemoryIsolation:
    """Tests for multi-entity conversation memory isolation."""

    @pytest.mark.asyncio
    async def test_load_session_passes_entity_id_for_multi_entity(
        self, db_session, sample_conversation
    ):
        """Test that load_session_from_db passes entity_id for multi-entity conversations."""
        from app.models import ConversationEntity

        # Set conversation as multi-entity
        sample_conversation.conversation_type = ConversationType.MULTI_ENTITY
        sample_conversation.entity_id = "multi-entity"
        await db_session.commit()

        # Add participating entities
        entity1 = ConversationEntity(
            conversation_id=sample_conversation.id,
            entity_id="claude-main",
            display_order=0,
        )
        entity2 = ConversationEntity(
            conversation_id=sample_conversation.id,
            entity_id="gpt-test",
            display_order=1,
        )
        db_session.add(entity1)
        db_session.add(entity2)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = MagicMock(
                label="Claude",
                default_model="claude-sonnet-4-5-20250929",
                llm_provider="anthropic"
            )
            mock_settings.get_default_model_for_provider.return_value = "claude-sonnet-4-5-20250929"

            session = await manager.load_session_from_db(
                sample_conversation.id,
                db_session,
                responding_entity_id="claude-main"  # Specify responding entity
            )

        # Verify get_retrieved_ids_for_conversation was called with entity_id
        mock_memory.get_retrieved_ids_for_conversation.assert_called_once()
        call_kwargs = mock_memory.get_retrieved_ids_for_conversation.call_args.kwargs
        assert call_kwargs.get("entity_id") == "claude-main"

        # Verify session has correct entity_id
        assert session.entity_id == "claude-main"
        assert session.is_multi_entity is True

    @pytest.mark.asyncio
    async def test_load_session_no_entity_filter_for_single_entity(
        self, db_session, sample_conversation
    ):
        """Test that load_session_from_db doesn't filter by entity for single-entity conversations."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(
                sample_conversation.id,
                db_session
            )

        # Verify get_retrieved_ids_for_conversation was called without entity_id
        mock_memory.get_retrieved_ids_for_conversation.assert_called_once()
        call_kwargs = mock_memory.get_retrieved_ids_for_conversation.call_args.kwargs
        assert call_kwargs.get("entity_id") is None

        assert session.is_multi_entity is False

    def test_session_entity_id_change_detection(self):
        """Test that session entity_id change can be detected for reload logic."""
        session = ConversationSession(
            conversation_id="conv-123",
            entity_id="claude-main",
            is_multi_entity=True,
        )

        # Entity ID matches
        assert session.entity_id == "claude-main"

        # Simulating what chat.py does - check if entity changed
        new_entity_id = "gpt-test"
        entity_changed = session.entity_id != new_entity_id
        assert entity_changed is True

        # Same entity - no change
        same_entity_id = "claude-main"
        entity_changed = session.entity_id != same_entity_id
        assert entity_changed is False

    def test_multi_entity_session_fields(self):
        """Test multi-entity specific session fields."""
        session = ConversationSession(
            conversation_id="conv-123",
            entity_id="claude-main",
            is_multi_entity=True,
            entity_labels={"claude-main": "Claude", "gpt-test": "GPT"},
            responding_entity_label="Claude",
        )

        assert session.is_multi_entity is True
        assert session.entity_labels == {"claude-main": "Claude", "gpt-test": "GPT"}
        assert session.responding_entity_label == "Claude"

    def test_multi_entity_add_exchange_labels_messages(self):
        """Test that add_exchange labels messages in multi-entity conversations."""
        session = ConversationSession(
            conversation_id="conv-123",
            is_multi_entity=True,
            responding_entity_label="Claude",
        )

        session.add_exchange("Hello!", "Hi there!")

        assert len(session.conversation_context) == 2
        # Human messages should be labeled
        assert session.conversation_context[0]["content"] == "[Human]: Hello!"
        # Assistant messages should be labeled with responding entity
        assert session.conversation_context[1]["content"] == "[Claude]: Hi there!"

    def test_single_entity_add_exchange_no_labels(self):
        """Test that add_exchange doesn't label messages in single-entity conversations."""
        session = ConversationSession(
            conversation_id="conv-123",
            is_multi_entity=False,
        )

        session.add_exchange("Hello!", "Hi there!")

        assert len(session.conversation_context) == 2
        # Messages should not be labeled
        assert session.conversation_context[0]["content"] == "Hello!"
        assert session.conversation_context[1]["content"] == "Hi there!"


class TestCacheStateManagement:
    """Tests for cache state management and conversation-first caching."""

    def test_get_cache_aware_content_empty_session(self):
        """Test cache-aware content for empty session."""
        session = ConversationSession(conversation_id="conv-123")

        content = session.get_cache_aware_content()

        # With conversation-first caching, only context is tracked in cache state
        assert content["cached_context"] == []
        assert content["new_context"] == []

    def test_get_cache_aware_content_all_new(self):
        """Test cache-aware content when nothing is cached yet."""
        session = ConversationSession(conversation_id="conv-123")

        # Add some memories
        for i in range(3):
            memory = MemoryEntry(
                id=f"mem-{i}",
                conversation_id="old-conv",
                role="assistant",
                content=f"Memory {i}",
                created_at="2024-01-01",
                times_retrieved=1,
            )
            session.add_memory(memory)

        # Add conversation context
        session.add_exchange("Hello", "Hi")
        session.add_exchange("How are you?", "I'm well!")

        # Cache state is empty (nothing cached yet)
        assert session.last_cached_context_length == 0

        content = session.get_cache_aware_content()

        # All context is new (memories are no longer tracked in cache state)
        assert len(content["cached_context"]) == 0
        assert len(content["new_context"]) == 4

    def test_get_cache_aware_content_with_cached_state(self):
        """Test cache-aware content with existing cached state."""
        session = ConversationSession(conversation_id="conv-123")

        # Add conversation context
        session.add_exchange("First", "Response 1")
        session.add_exchange("Second", "Response 2")

        # Set cache state: first 2 messages are cached
        session.last_cached_context_length = 2

        content = session.get_cache_aware_content()

        # 2 cached context messages, 2 new context messages
        assert len(content["cached_context"]) == 2
        assert len(content["new_context"]) == 2
        assert content["cached_context"][0]["content"] == "First"
        assert content["new_context"][0]["content"] == "Second"

    def test_update_cache_state(self):
        """Test updating cache state."""
        session = ConversationSession(conversation_id="conv-123")

        # Initial state
        assert session.last_cached_context_length == 0

        # Update cache state (only context length now)
        session.update_cache_state(cached_context_length=4)

        assert session.last_cached_context_length == 4

    def test_should_consolidate_cache_context_threshold(self):
        """Test consolidation triggers at 2048 token context threshold."""
        session = ConversationSession(conversation_id="conv-123")

        # Add conversation context - some cached, some new
        for i in range(10):
            session.add_exchange(f"Question {i} " * 50, f"Answer {i} " * 50)

        # First 4 messages are cached
        session.last_cached_context_length = 4

        # Token counter
        call_count = [0]
        def count_tokens(text):
            call_count[0] += 1
            if call_count[0] == 1:
                # Cached context check - return high value (so we don't hit "too small" branch)
                return 2000
            else:
                # New context check - return value above 2048 threshold
                return 2500

        result = session.should_consolidate_cache(count_tokens)

        # Should consolidate because new context >= 2048 tokens
        assert result is True

    def test_should_consolidate_cache_context_below_threshold(self):
        """Test consolidation doesn't trigger below context threshold."""
        session = ConversationSession(conversation_id="conv-123")

        # Add a small amount of context
        session.add_exchange("Hello", "Hi")
        session.add_exchange("Question", "Answer")

        # First 2 messages are cached
        session.last_cached_context_length = 2

        # Token counter - cached is large enough, new is below threshold
        call_count = [0]
        def count_tokens(text):
            call_count[0] += 1
            if call_count[0] == 1:
                return 2000  # Cached context
            else:
                return 500  # New context - below 2048 threshold

        result = session.should_consolidate_cache(count_tokens)

        # Should not consolidate because new context < 2048 tokens
        assert result is False

    def test_should_consolidate_cache_small_cached_context(self):
        """Test consolidation triggers when cached context is too small."""
        session = ConversationSession(conversation_id="conv-123")

        # Add some context
        session.add_exchange("Hi", "Hello")
        session.add_exchange("Question", "Answer")

        # First 2 messages are "cached" but small
        session.last_cached_context_length = 2

        # Token counter - cached context is below 1024 minimum
        def count_tokens(text):
            return 500  # Below 1024 minimum

        result = session.should_consolidate_cache(count_tokens)

        # Should consolidate to grow the cache
        assert result is True

    def test_cache_state_preserved_across_exchanges(self):
        """Test that cache state remains stable as new exchanges are added."""
        session = ConversationSession(conversation_id="conv-123")

        # Initial exchanges
        session.add_exchange("First", "Response 1")
        session.add_exchange("Second", "Response 2")

        # Set cache state (only context length now - memories are after cache breakpoint)
        session.update_cache_state(cached_context_length=4)  # All 4 messages cached

        # Add more exchanges
        session.add_exchange("Third", "Response 3")

        # Cache state should be unchanged
        assert session.last_cached_context_length == 4

        # Get cache-aware content
        content = session.get_cache_aware_content()

        # First 4 messages should be cached, last 2 should be new
        assert len(content["cached_context"]) == 4
        assert len(content["new_context"]) == 2

    @pytest.mark.asyncio
    async def test_load_session_preserves_context_cache_length(
        self, db_session, sample_conversation, sample_messages
    ):
        """Test that load_session_from_db can preserve context cache length."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            # Load without preserving - should use full context length
            session1 = await manager.load_session_from_db(
                sample_conversation.id,
                db_session
            )
            manager.close_session(sample_conversation.id)

            # Should have bootstrapped to full context length
            assert session1.last_cached_context_length == len(session1.conversation_context)

            # Load with preserved value
            session2 = await manager.load_session_from_db(
                sample_conversation.id,
                db_session,
                preserve_context_cache_length=1  # Preserve at 1
            )

            # Should use preserved value
            assert session2.last_cached_context_length == 1

    @pytest.mark.asyncio
    async def test_load_session_caps_preserved_length_at_context_size(
        self, db_session, sample_conversation, sample_messages
    ):
        """Test that preserved cache length is capped at actual context size."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            # Load with preserved value larger than actual context
            session = await manager.load_session_from_db(
                sample_conversation.id,
                db_session,
                preserve_context_cache_length=100  # Way more than actual context
            )

            # Should be capped at actual context length (2 messages from fixture)
            assert session.last_cached_context_length == 2


class TestCacheBreakpointPlacement:
    """Tests for cache breakpoint placement in message building."""

    def test_memory_ids_sorted_for_cache_stability(self):
        """Test that memories are sorted by ID for cache stability."""
        session = ConversationSession(conversation_id="conv-123")

        # Add memories in random order
        for id_suffix in ["z", "a", "m", "b"]:
            memory = MemoryEntry(
                id=f"mem-{id_suffix}",
                conversation_id="old-conv",
                role="assistant",
                content=f"Content {id_suffix}",
                created_at="2024-01-01",
                times_retrieved=1,
            )
            session.add_memory(memory)

        memories = session.get_memories_for_injection()

        # Should be sorted by ID
        ids = [m["id"] for m in memories]
        assert ids == ["mem-a", "mem-b", "mem-m", "mem-z"]

    def test_context_split_preserves_order(self):
        """Test that context split preserves message order."""
        session = ConversationSession(conversation_id="conv-123")

        # Add exchanges
        session.add_exchange("First", "Response 1")
        session.add_exchange("Second", "Response 2")
        session.add_exchange("Third", "Response 3")

        # Cache first 4 messages (2 exchanges)
        session.last_cached_context_length = 4

        content = session.get_cache_aware_content()

        # Verify order is preserved
        assert content["cached_context"][0]["content"] == "First"
        assert content["cached_context"][1]["content"] == "Response 1"
        assert content["cached_context"][2]["content"] == "Second"
        assert content["cached_context"][3]["content"] == "Response 2"
        assert content["new_context"][0]["content"] == "Third"
        assert content["new_context"][1]["content"] == "Response 3"

    def test_identical_cache_aware_content_across_calls(self):
        """Test that cache-aware content is identical across multiple calls."""
        session = ConversationSession(conversation_id="conv-123")

        # Add context
        session.add_exchange("Hello", "Hi")
        session.add_exchange("Question", "Answer")

        # Set cache state
        session.last_cached_context_length = 2

        # Get content multiple times
        content1 = session.get_cache_aware_content()
        content2 = session.get_cache_aware_content()
        content3 = session.get_cache_aware_content()

        # All should be identical
        assert content1 == content2
        assert content2 == content3

        # Verify specific structure (memories no longer tracked in cache state)
        assert len(content1["cached_context"]) == 2
        assert len(content1["new_context"]) == 2


class TestSystemPromptSelection:
    """Tests for entity-specific system prompt selection."""

    @pytest.mark.asyncio
    async def test_single_entity_uses_entity_system_prompt(self, db_session):
        """Test that single-entity conversations use entity_system_prompts when available."""
        from app.models import Conversation, ConversationType

        # Create conversation with entity_system_prompts
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test Single Entity",
            conversation_type=ConversationType.NORMAL,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="claude-main",
            system_prompt_used="Fallback system prompt",
            entity_system_prompts={"claude-main": "Entity-specific prompt for Claude"},
        )
        db_session.add(conversation)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        # Should use entity-specific prompt, not fallback
        assert session.system_prompt == "Entity-specific prompt for Claude"

    @pytest.mark.asyncio
    async def test_single_entity_falls_back_to_system_prompt_used(self, db_session):
        """Test that single-entity conversations fall back to system_prompt_used when no entity prompt."""
        from app.models import Conversation, ConversationType

        # Create conversation without entity_system_prompts
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test Fallback",
            conversation_type=ConversationType.NORMAL,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="claude-main",
            system_prompt_used="Fallback system prompt",
            entity_system_prompts=None,
        )
        db_session.add(conversation)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        # Should use fallback system_prompt_used
        assert session.system_prompt == "Fallback system prompt"

    @pytest.mark.asyncio
    async def test_single_entity_falls_back_when_entity_not_in_dict(self, db_session):
        """Test fallback when entity_id is not in entity_system_prompts dict."""
        from app.models import Conversation, ConversationType

        # Create conversation with entity_system_prompts that doesn't include this entity
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test Entity Not In Dict",
            conversation_type=ConversationType.NORMAL,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="claude-main",
            system_prompt_used="Fallback system prompt",
            entity_system_prompts={"other-entity": "Some other prompt"},
        )
        db_session.add(conversation)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        # Should use fallback since entity not in dict
        assert session.system_prompt == "Fallback system prompt"

    @pytest.mark.asyncio
    async def test_multi_entity_uses_responding_entity_prompt(self, db_session):
        """Test that multi-entity conversations use the responding entity's system prompt."""
        from app.models import Conversation, ConversationType, ConversationEntity

        # Create multi-entity conversation with different prompts per entity
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test Multi Entity",
            conversation_type=ConversationType.MULTI_ENTITY,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="multi-entity",
            system_prompt_used="Fallback system prompt",
            entity_system_prompts={
                "claude-main": "You are Claude, a helpful AI.",
                "gpt-test": "You are GPT, an OpenAI model.",
            },
        )
        db_session.add(conversation)
        await db_session.flush()

        # Add participating entities
        entity1 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="claude-main",
            display_order=0,
        )
        entity2 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="gpt-test",
            display_order=1,
        )
        db_session.add(entity1)
        db_session.add(entity2)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_entity = MagicMock()
            mock_entity.label = "Claude"
            mock_entity.default_model = "claude-sonnet-4-5-20250929"
            mock_entity.llm_provider = "anthropic"
            mock_settings.get_entity_by_index.return_value = mock_entity
            mock_settings.get_default_model_for_provider.return_value = "claude-sonnet-4-5-20250929"

            # Load session with Claude as responding entity
            session = await manager.load_session_from_db(
                conversation.id,
                db_session,
                responding_entity_id="claude-main"
            )

        # Should use Claude's specific prompt
        assert session.system_prompt == "You are Claude, a helpful AI."

    @pytest.mark.asyncio
    async def test_multi_entity_different_prompts_for_different_entities(self, db_session):
        """Test that different responding entities get different system prompts."""
        from app.models import Conversation, ConversationType, ConversationEntity

        # Create multi-entity conversation
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test Multi Entity Different Prompts",
            conversation_type=ConversationType.MULTI_ENTITY,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="multi-entity",
            system_prompt_used="Fallback system prompt",
            entity_system_prompts={
                "claude-main": "You are Claude.",
                "gpt-test": "You are GPT.",
            },
        )
        db_session.add(conversation)
        await db_session.flush()

        # Add participating entities
        entity1 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="claude-main",
            display_order=0,
        )
        entity2 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="gpt-test",
            display_order=1,
        )
        db_session.add(entity1)
        db_session.add(entity2)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000

            # Mock entity configs
            def get_entity(eid):
                if eid == "claude-main":
                    mock = MagicMock()
                    mock.label = "Claude"
                    mock.default_model = "claude-sonnet-4-5-20250929"
                    mock.llm_provider = "anthropic"
                    return mock
                elif eid == "gpt-test":
                    mock = MagicMock()
                    mock.label = "GPT"
                    mock.default_model = "gpt-4o"
                    mock.llm_provider = "openai"
                    return mock
                return None

            mock_settings.get_entity_by_index.side_effect = get_entity
            mock_settings.get_default_model_for_provider.return_value = "claude-sonnet-4-5-20250929"

            # Load session with Claude
            session_claude = await manager.load_session_from_db(
                conversation.id,
                db_session,
                responding_entity_id="claude-main"
            )
            manager.close_session(conversation.id)

            # Load session with GPT
            session_gpt = await manager.load_session_from_db(
                conversation.id,
                db_session,
                responding_entity_id="gpt-test"
            )

        # Each should have their own system prompt
        assert session_claude.system_prompt == "You are Claude."
        assert session_gpt.system_prompt == "You are GPT."

    @pytest.mark.asyncio
    async def test_multi_entity_falls_back_when_entity_not_in_dict(self, db_session):
        """Test multi-entity fallback when responding entity not in entity_system_prompts."""
        from app.models import Conversation, ConversationType, ConversationEntity

        # Create multi-entity conversation with only one entity's prompt
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test Multi Entity Partial Prompts",
            conversation_type=ConversationType.MULTI_ENTITY,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="multi-entity",
            system_prompt_used="Fallback system prompt",
            entity_system_prompts={
                "claude-main": "You are Claude.",
                # gpt-test is NOT in this dict
            },
        )
        db_session.add(conversation)
        await db_session.flush()

        # Add participating entities
        entity1 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="claude-main",
            display_order=0,
        )
        entity2 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="gpt-test",
            display_order=1,
        )
        db_session.add(entity1)
        db_session.add(entity2)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_entity = MagicMock()
            mock_entity.label = "GPT"
            mock_entity.default_model = "gpt-4o"
            mock_entity.llm_provider = "openai"
            mock_settings.get_entity_by_index.return_value = mock_entity
            mock_settings.get_default_model_for_provider.return_value = "gpt-4o"

            # Load session with GPT (which is not in entity_system_prompts)
            session = await manager.load_session_from_db(
                conversation.id,
                db_session,
                responding_entity_id="gpt-test"
            )

        # Should use fallback since gpt-test not in entity_system_prompts
        assert session.system_prompt == "Fallback system prompt"

    @pytest.mark.asyncio
    async def test_empty_string_system_prompt_is_used(self, db_session):
        """Test that empty string system prompt in entity_system_prompts is used (not fallback)."""
        from app.models import Conversation, ConversationType

        # Create conversation with empty string prompt for entity
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test Empty String Prompt",
            conversation_type=ConversationType.NORMAL,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="claude-main",
            system_prompt_used="Fallback system prompt",
            entity_system_prompts={"claude-main": ""},  # Empty string, not None
        )
        db_session.add(conversation)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        # Should use empty string (entity explicitly has no system prompt)
        assert session.system_prompt == ""

    @pytest.mark.asyncio
    async def test_null_entity_system_prompts_uses_fallback(self, db_session):
        """Test that null entity_system_prompts uses system_prompt_used fallback."""
        from app.models import Conversation, ConversationType

        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test Null Entity Prompts",
            conversation_type=ConversationType.NORMAL,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="claude-main",
            system_prompt_used="This is the fallback",
            entity_system_prompts=None,
        )
        db_session.add(conversation)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        assert session.system_prompt == "This is the fallback"

    @pytest.mark.asyncio
    async def test_no_system_prompt_at_all(self, db_session):
        """Test conversation with no system prompt (both null)."""
        from app.models import Conversation, ConversationType

        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test No System Prompt",
            conversation_type=ConversationType.NORMAL,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="claude-main",
            system_prompt_used=None,
            entity_system_prompts=None,
        )
        db_session.add(conversation)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        # System prompt should be None
        assert session.system_prompt is None

    @pytest.mark.asyncio
    async def test_conversation_without_entity_id_uses_fallback(self, db_session):
        """Test conversation with no entity_id uses system_prompt_used."""
        from app.models import Conversation, ConversationType

        # Conversation with no entity_id (legacy or default)
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test No Entity ID",
            conversation_type=ConversationType.NORMAL,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id=None,  # No entity
            system_prompt_used="Fallback prompt",
            entity_system_prompts={"some-entity": "Some prompt"},  # Has prompts but no entity_id
        )
        db_session.add(conversation)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        # Should use fallback since entity_id is None
        assert session.system_prompt == "Fallback prompt"

    @pytest.mark.asyncio
    async def test_empty_entity_system_prompts_dict_uses_fallback(self, db_session):
        """Test that empty entity_system_prompts dict uses fallback."""
        from app.models import Conversation, ConversationType

        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test Empty Dict",
            conversation_type=ConversationType.NORMAL,
            llm_model_used="claude-sonnet-4-5-20250929",
            entity_id="claude-main",
            system_prompt_used="Fallback prompt",
            entity_system_prompts={},  # Empty dict
        )
        db_session.add(conversation)
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        # Empty dict should use fallback
        assert session.system_prompt == "Fallback prompt"


class TestAgenticToolLoopMemoryOptimization:
    """Tests for memory optimization in the agentic tool loop.

    When tools are used, the first iteration should include memories,
    but subsequent iterations should exclude the memory block to reduce
    context size and token costs.
    """

    @pytest.mark.asyncio
    async def test_first_iteration_includes_memories(self, db_session, sample_conversation):
        """Test that the first tool loop iteration includes memories."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            # Track what messages are built
            build_calls = []

            def track_build_messages(memories, **kwargs):
                build_calls.append({"memories": memories, "kwargs": kwargs})
                return [{"role": "user", "content": "test"}]

            mock_llm.build_messages_with_memories.side_effect = track_build_messages
            mock_llm.count_tokens = MagicMock(return_value=100)

            # Simple response without tool use
            async def mock_stream(*args, **kwargs):
                yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                yield {"type": "token", "content": "Hello"}
                yield {
                    "type": "done",
                    "content": "Hello",
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "stop_reason": "end_turn",
                    "content_blocks": [{"type": "text", "text": "Hello"}],
                }

            mock_llm.send_message_stream = mock_stream

            session = manager.create_session(sample_conversation.id)
            # Add a mock memory to the session
            memory = MemoryEntry(
                id="mem-1",
                conversation_id="old-conv",
                role="assistant",
                content="Test memory",
                created_at="2024-01-01",
                times_retrieved=1,
            )
            session.add_memory(memory)

            # Process message
            events = []
            async for event in manager.process_message_stream(
                session, "Hello", db_session, tool_schemas=[]
            ):
                events.append(event)

        # Should have built messages twice: once with memories, once without
        assert len(build_calls) == 2

        # First call (with memories) should have the memory
        assert len(build_calls[0]["memories"]) == 1
        assert build_calls[0]["memories"][0]["id"] == "mem-1"

        # Second call (base without memories) should have empty memories
        assert len(build_calls[1]["memories"]) == 0

    @pytest.mark.asyncio
    async def test_subsequent_iterations_exclude_memories(self, db_session, sample_conversation):
        """Test that subsequent tool loop iterations exclude memory block."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            # Track messages sent to LLM
            sent_messages = []

            def build_messages(memories, **kwargs):
                if memories:
                    return [{"role": "user", "content": "with_memories"}]
                else:
                    return [{"role": "user", "content": "without_memories"}]

            mock_llm.build_messages_with_memories.side_effect = build_messages
            mock_llm.count_tokens = MagicMock(return_value=100)

            call_count = [0]

            async def mock_stream(messages, **kwargs):
                sent_messages.append(list(messages))  # Copy the messages
                call_count[0] += 1

                if call_count[0] == 1:
                    # First call: return tool use
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {
                        "type": "done",
                        "content": "",
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                        "stop_reason": "tool_use",
                        "content_blocks": [
                            {"type": "tool_use", "id": "tool-1", "name": "web_search", "input": {"query": "test"}}
                        ],
                        "tool_use": [{"id": "tool-1", "name": "web_search", "input": {"query": "test"}}],
                    }
                else:
                    # Second call: return final response
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {"type": "token", "content": "Done"}
                    yield {
                        "type": "done",
                        "content": "Done",
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {"input_tokens": 20, "output_tokens": 10},
                        "stop_reason": "end_turn",
                        "content_blocks": [{"type": "text", "text": "Done"}],
                    }

            mock_llm.send_message_stream = mock_stream

            # Mock tool execution
            mock_tool_result = MagicMock()
            mock_tool_result.tool_use_id = "tool-1"
            mock_tool_result.content = "Search results"
            mock_tool_result.is_error = False
            mock_tool.execute_tool = AsyncMock(return_value=mock_tool_result)

            session = manager.create_session(sample_conversation.id)
            # Add a mock memory
            memory = MemoryEntry(
                id="mem-1",
                conversation_id="old-conv",
                role="assistant",
                content="Test memory",
                created_at="2024-01-01",
                times_retrieved=1,
            )
            session.add_memory(memory)

            # Process message
            events = []
            async for event in manager.process_message_stream(
                session, "Search for something", db_session, tool_schemas=[{"name": "web_search"}]
            ):
                events.append(event)

        # Should have two LLM calls
        assert len(sent_messages) == 2

        # First iteration should use messages with memories
        assert sent_messages[0][0]["content"] == "with_memories"

        # Second iteration should use messages without memories (plus tool exchanges)
        assert sent_messages[1][0]["content"] == "without_memories"
        # Should also have tool exchange messages appended
        assert len(sent_messages[1]) == 3  # base message + assistant tool_use + user tool_result
        assert sent_messages[1][1]["role"] == "assistant"
        assert sent_messages[1][2]["role"] == "user"

    @pytest.mark.asyncio
    async def test_tool_exchanges_accumulated_correctly(self, db_session, sample_conversation):
        """Test that tool exchanges are properly accumulated across iterations."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            sent_messages = []

            def build_messages(memories, **kwargs):
                return [{"role": "user", "content": "base"}]

            mock_llm.build_messages_with_memories.side_effect = build_messages
            mock_llm.count_tokens = MagicMock(return_value=100)

            call_count = [0]

            async def mock_stream(messages, **kwargs):
                sent_messages.append(list(messages))
                call_count[0] += 1

                if call_count[0] == 1:
                    # First tool use
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {
                        "type": "done",
                        "stop_reason": "tool_use",
                        "content_blocks": [
                            {"type": "tool_use", "id": "tool-1", "name": "web_search", "input": {"query": "first"}}
                        ],
                        "tool_use": [{"id": "tool-1", "name": "web_search", "input": {"query": "first"}}],
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {},
                    }
                elif call_count[0] == 2:
                    # Second tool use
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {
                        "type": "done",
                        "stop_reason": "tool_use",
                        "content_blocks": [
                            {"type": "tool_use", "id": "tool-2", "name": "web_fetch", "input": {"url": "http://example.com"}}
                        ],
                        "tool_use": [{"id": "tool-2", "name": "web_fetch", "input": {"url": "http://example.com"}}],
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {},
                    }
                else:
                    # Final response
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {"type": "token", "content": "Final"}
                    yield {
                        "type": "done",
                        "content": "Final",
                        "stop_reason": "end_turn",
                        "content_blocks": [{"type": "text", "text": "Final"}],
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {},
                    }

            mock_llm.send_message_stream = mock_stream

            # Mock tool execution
            tool_call_count = [0]

            async def mock_execute(tool_use_id, tool_name, tool_input):
                tool_call_count[0] += 1
                result = MagicMock()
                result.tool_use_id = tool_use_id
                result.content = f"Result {tool_call_count[0]}"
                result.is_error = False
                return result

            mock_tool.execute_tool = mock_execute

            session = manager.create_session(sample_conversation.id)

            # Process message
            events = []
            async for event in manager.process_message_stream(
                session, "Multi-tool query", db_session, tool_schemas=[{"name": "web_search"}, {"name": "web_fetch"}]
            ):
                events.append(event)

        # Should have three LLM calls
        assert len(sent_messages) == 3

        # First iteration: just base message
        assert len(sent_messages[0]) == 1

        # Second iteration: base + 1 tool exchange (2 messages)
        assert len(sent_messages[1]) == 3
        assert sent_messages[1][1]["role"] == "assistant"
        assert sent_messages[1][2]["role"] == "user"

        # Third iteration: base + 2 tool exchanges (4 messages)
        assert len(sent_messages[2]) == 5
        # Verify all tool exchanges are present
        assert sent_messages[2][1]["role"] == "assistant"  # First tool use
        assert sent_messages[2][2]["role"] == "user"       # First tool result
        assert sent_messages[2][3]["role"] == "assistant"  # Second tool use
        assert sent_messages[2][4]["role"] == "user"       # Second tool result

    @pytest.mark.asyncio
    async def test_no_tool_use_single_iteration(self, db_session, sample_conversation):
        """Test that without tool use, only one iteration occurs with memories."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            sent_messages = []

            def build_messages(memories, **kwargs):
                if memories:
                    return [{"role": "user", "content": "with_memories"}]
                else:
                    return [{"role": "user", "content": "without_memories"}]

            mock_llm.build_messages_with_memories.side_effect = build_messages
            mock_llm.count_tokens = MagicMock(return_value=100)

            async def mock_stream(messages, **kwargs):
                sent_messages.append(list(messages))
                yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                yield {"type": "token", "content": "Response"}
                yield {
                    "type": "done",
                    "content": "Response",
                    "stop_reason": "end_turn",
                    "content_blocks": [{"type": "text", "text": "Response"}],
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": {},
                }

            mock_llm.send_message_stream = mock_stream

            session = manager.create_session(sample_conversation.id)
            memory = MemoryEntry(
                id="mem-1",
                conversation_id="old-conv",
                role="assistant",
                content="Test memory",
                created_at="2024-01-01",
                times_retrieved=1,
            )
            session.add_memory(memory)

            events = []
            async for event in manager.process_message_stream(
                session, "Hello", db_session, tool_schemas=[]
            ):
                events.append(event)

        # Should have only one LLM call
        assert len(sent_messages) == 1
        # That call should use messages with memories
        assert sent_messages[0][0]["content"] == "with_memories"

    @pytest.mark.asyncio
    async def test_base_messages_built_without_memories(self, db_session, sample_conversation):
        """Test that base_messages_no_memories is built with empty memories list."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            build_calls = []

            def track_build(memories, **kwargs):
                build_calls.append({
                    "memories_count": len(memories),
                    "memories": list(memories),
                })
                return [{"role": "user", "content": "test"}]

            mock_llm.build_messages_with_memories.side_effect = track_build
            mock_llm.count_tokens = MagicMock(return_value=100)

            async def mock_stream(messages, **kwargs):
                yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                yield {"type": "token", "content": "Done"}
                yield {
                    "type": "done",
                    "content": "Done",
                    "stop_reason": "end_turn",
                    "content_blocks": [],
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": {},
                }

            mock_llm.send_message_stream = mock_stream

            session = manager.create_session(sample_conversation.id)
            # Add multiple memories
            for i in range(3):
                memory = MemoryEntry(
                    id=f"mem-{i}",
                    conversation_id="old-conv",
                    role="assistant",
                    content=f"Memory {i}",
                    created_at="2024-01-01",
                    times_retrieved=1,
                )
                session.add_memory(memory)

            events = []
            async for event in manager.process_message_stream(
                session, "Test", db_session, tool_schemas=[]
            ):
                events.append(event)

        # Should have two build calls
        assert len(build_calls) == 2

        # First: with all 3 memories
        assert build_calls[0]["memories_count"] == 3

        # Second: with no memories (base for subsequent iterations)
        assert build_calls[1]["memories_count"] == 0
        assert build_calls[1]["memories"] == []


class TestAddCacheControlToToolResult:
    """Tests for _add_cache_control_to_tool_result helper function."""

    def test_adds_cache_control_to_single_tool_result(self):
        """Test adding cache_control to a single tool_result block."""
        user_msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "Search results here",
                    "is_error": False,
                }
            ],
        }

        result = _add_cache_control_to_tool_result(user_msg)

        # Should have cache_control on the tool_result block
        assert result["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        # Original fields should be preserved
        assert result["content"][0]["type"] == "tool_result"
        assert result["content"][0]["tool_use_id"] == "tool-1"
        assert result["content"][0]["content"] == "Search results here"
        assert result["content"][0]["is_error"] is False

    def test_adds_cache_control_to_last_block_only(self):
        """Test that cache_control is only added to the last tool_result block."""
        user_msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "First result",
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-2",
                    "content": "Second result",
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-3",
                    "content": "Third result",
                    "is_error": False,
                },
            ],
        }

        result = _add_cache_control_to_tool_result(user_msg)

        # First two blocks should NOT have cache_control
        assert "cache_control" not in result["content"][0]
        assert "cache_control" not in result["content"][1]

        # Last block SHOULD have cache_control
        assert result["content"][2]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    def test_does_not_mutate_original_message(self):
        """Test that the original message is not mutated."""
        user_msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "Result",
                    "is_error": False,
                }
            ],
        }

        result = _add_cache_control_to_tool_result(user_msg)

        # Original should not have cache_control
        assert "cache_control" not in user_msg["content"][0]

        # Result should have cache_control
        assert result["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

        # They should be different objects
        assert result is not user_msg
        assert result["content"] is not user_msg["content"]
        assert result["content"][0] is not user_msg["content"][0]

    def test_handles_empty_content(self):
        """Test handling of message with empty content list."""
        user_msg = {
            "role": "user",
            "content": [],
        }

        result = _add_cache_control_to_tool_result(user_msg)

        # Should return message unchanged (no crash)
        assert result["role"] == "user"
        assert result["content"] == []

    def test_handles_non_list_content(self):
        """Test handling of message with non-list content."""
        user_msg = {
            "role": "user",
            "content": "Plain text content",
        }

        result = _add_cache_control_to_tool_result(user_msg)

        # Should return message unchanged (no crash)
        assert result["role"] == "user"
        assert result["content"] == "Plain text content"

    def test_handles_missing_content(self):
        """Test handling of message with missing content key."""
        user_msg = {
            "role": "user",
        }

        result = _add_cache_control_to_tool_result(user_msg)

        # Should return message unchanged (no crash)
        assert result["role"] == "user"
        assert "content" not in result

    def test_preserves_role_and_other_fields(self):
        """Test that role and other message fields are preserved."""
        user_msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "Result",
                    "is_error": False,
                }
            ],
            "extra_field": "some_value",
        }

        result = _add_cache_control_to_tool_result(user_msg)

        assert result["role"] == "user"
        assert result["extra_field"] == "some_value"


class TestToolIterationCaching:
    """Tests for cache_control being added to tool iterations."""

    @pytest.mark.asyncio
    async def test_cache_control_added_to_tool_result_in_subsequent_iterations(
        self, db_session, sample_conversation
    ):
        """Test that cache_control is added to tool result messages in subsequent iterations."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            sent_messages = []

            def build_messages(memories, **kwargs):
                return [{"role": "user", "content": "base"}]

            mock_llm.build_messages_with_memories.side_effect = build_messages
            mock_llm.count_tokens = MagicMock(return_value=100)

            call_count = [0]

            async def mock_stream(messages, **kwargs):
                sent_messages.append(list(messages))
                call_count[0] += 1

                if call_count[0] == 1:
                    # First call: return tool use
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {
                        "type": "done",
                        "content": "",
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                        "stop_reason": "tool_use",
                        "content_blocks": [
                            {"type": "tool_use", "id": "tool-1", "name": "web_search", "input": {"query": "test"}}
                        ],
                        "tool_use": [{"id": "tool-1", "name": "web_search", "input": {"query": "test"}}],
                    }
                else:
                    # Second call: return final response
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {"type": "token", "content": "Done"}
                    yield {
                        "type": "done",
                        "content": "Done",
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {"input_tokens": 20, "output_tokens": 10},
                        "stop_reason": "end_turn",
                        "content_blocks": [{"type": "text", "text": "Done"}],
                    }

            mock_llm.send_message_stream = mock_stream

            # Mock tool execution
            mock_tool_result = MagicMock()
            mock_tool_result.tool_use_id = "tool-1"
            mock_tool_result.content = "Search results"
            mock_tool_result.is_error = False
            mock_tool.execute_tool = AsyncMock(return_value=mock_tool_result)

            session = manager.create_session(sample_conversation.id)

            # Process message
            events = []
            async for event in manager.process_message_stream(
                session, "Search for something", db_session, tool_schemas=[{"name": "web_search"}]
            ):
                events.append(event)

        # Should have two LLM calls
        assert len(sent_messages) == 2

        # Second iteration should have cache_control on the tool result message
        tool_result_msg = sent_messages[1][2]  # base + assistant + user (tool_result)
        assert tool_result_msg["role"] == "user"
        assert isinstance(tool_result_msg["content"], list)
        # The last content block should have cache_control
        last_block = tool_result_msg["content"][-1]
        assert last_block.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}

    @pytest.mark.asyncio
    async def test_multiple_tool_iterations_each_gets_cache_control(
        self, db_session, sample_conversation
    ):
        """Test that each tool iteration has cache_control on the last accumulated tool result."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 20000
            mock_settings.memory_token_limit = 40000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            sent_messages = []

            def build_messages(memories, **kwargs):
                return [{"role": "user", "content": "base"}]

            mock_llm.build_messages_with_memories.side_effect = build_messages
            mock_llm.count_tokens = MagicMock(return_value=100)

            call_count = [0]

            async def mock_stream(messages, **kwargs):
                sent_messages.append(list(messages))
                call_count[0] += 1

                if call_count[0] == 1:
                    # First tool use
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {
                        "type": "done",
                        "stop_reason": "tool_use",
                        "content_blocks": [
                            {"type": "tool_use", "id": "tool-1", "name": "web_search", "input": {"query": "first"}}
                        ],
                        "tool_use": [{"id": "tool-1", "name": "web_search", "input": {"query": "first"}}],
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {},
                    }
                elif call_count[0] == 2:
                    # Second tool use
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {
                        "type": "done",
                        "stop_reason": "tool_use",
                        "content_blocks": [
                            {"type": "tool_use", "id": "tool-2", "name": "web_fetch", "input": {"url": "http://example.com"}}
                        ],
                        "tool_use": [{"id": "tool-2", "name": "web_fetch", "input": {"url": "http://example.com"}}],
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {},
                    }
                else:
                    # Final response
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {"type": "token", "content": "Final"}
                    yield {
                        "type": "done",
                        "content": "Final",
                        "stop_reason": "end_turn",
                        "content_blocks": [{"type": "text", "text": "Final"}],
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {},
                    }

            mock_llm.send_message_stream = mock_stream

            # Mock tool execution
            tool_call_count = [0]

            async def mock_execute(tool_use_id, tool_name, tool_input):
                tool_call_count[0] += 1
                result = MagicMock()
                result.tool_use_id = tool_use_id
                result.content = f"Result {tool_call_count[0]}"
                result.is_error = False
                return result

            mock_tool.execute_tool = mock_execute

            session = manager.create_session(sample_conversation.id)

            # Process message
            events = []
            async for event in manager.process_message_stream(
                session, "Multi-tool query", db_session, tool_schemas=[{"name": "web_search"}, {"name": "web_fetch"}]
            ):
                events.append(event)

        # Should have three LLM calls
        assert len(sent_messages) == 3

        # Second iteration: should have cache_control on first tool result
        # Messages: base, assistant (tool_use), user (tool_result with cache_control)
        second_call_tool_result = sent_messages[1][2]
        assert second_call_tool_result["role"] == "user"
        last_block = second_call_tool_result["content"][-1]
        assert last_block.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}

        # Third iteration: should have cache_control on ALL tool results
        # This ensures prefix consistency for cache hits:
        # - Iteration 2 sent: [base_cache, asst_1, user_1_cache]
        # - Iteration 3 sends: [base_cache, asst_1, user_1_cache, asst_2, user_2_cache]
        # The prefix matches, enabling cache hit on the first tool exchange
        third_call_first_tool_result = sent_messages[2][2]
        assert third_call_first_tool_result["role"] == "user"
        first_block = third_call_first_tool_result["content"][-1]
        assert first_block.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}  # All have cache_control

        # The second tool_result also has cache_control
        third_call_second_tool_result = sent_messages[2][4]
        assert third_call_second_tool_result["role"] == "user"
        last_block = third_call_second_tool_result["content"][-1]
        assert last_block.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}
