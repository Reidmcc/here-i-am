"""
Unit tests for SessionManager.
"""
import re
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Conversation, ConversationType, Message, MessageRole
from app.services.session_manager import (
    ConversationSession,
    MemoryEntry,
    SessionManager,
    _add_cache_control_to_tool_result,
)


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

    def test_insert_memory_into_context(self):
        """Test inserting a new memory into the conversation context."""
        session = ConversationSession(conversation_id="conv-123")
        memory = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Test",
            created_at="2024-01-01",
            times_retrieved=1,
        )

        added, is_new_retrieval = session.insert_memory_into_context(memory)

        assert added is True
        assert is_new_retrieval is True
        assert "mem-1" in session.retrieved_ids
        assert "mem-1" in session.get_in_context_memory_ids()
        assert "mem-1" in session.session_memories
        assert session.session_memories["mem-1"] == memory
        assert session.conversation_context[-1]["is_memory"] is True

    def test_insert_memory_duplicate(self):
        """Test inserting a memory already in context is rejected."""
        session = ConversationSession(conversation_id="conv-123")
        memory = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Test",
            created_at="2024-01-01",
            times_retrieved=1,
        )

        session.insert_memory_into_context(memory)
        added, is_new_retrieval = session.insert_memory_into_context(memory)

        assert added is False
        assert is_new_retrieval is False
        assert len(session.session_memories) == 1

    def test_insert_memory_reinsert_rolled_out(self):
        """Test re-inserting a rolled-out memory restores it without updating count."""
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

        # Insert memory initially
        session.insert_memory_into_context(memory)
        assert "mem-1" in session.get_in_context_memory_ids()
        assert "mem-1" in session.retrieved_ids

        # Simulate rollout via context trimming
        session.conversation_context.pop(0)
        session.memory_tracker.handle_context_rollout(
            num_messages_removed=1,
            conversation_context=session.conversation_context,
        )
        assert "mem-1" not in session.get_in_context_memory_ids()
        assert "mem-1" in session.retrieved_ids  # Still in retrieved_ids

        # Re-insert the same memory
        added, is_new_retrieval = session.insert_memory_into_context(memory)

        # Should be added back to context but not trigger new retrieval count
        assert added is True
        assert is_new_retrieval is False
        assert "mem-1" in session.get_in_context_memory_ids()
        assert "mem-1" in session.retrieved_ids

    def test_add_exchange(self):
        """Test adding a conversation exchange."""
        session = ConversationSession(conversation_id="conv-123")

        session.add_exchange("Hello!", "Hi there!")

        assert len(session.conversation_context) == 2
        assert session.conversation_context[0] == {"role": "user", "content": "Hello!"}
        assert session.conversation_context[1] == {"role": "assistant", "content": "Hi there!"}

    def test_trim_context_rolls_out_memories(self):
        """Memories trimmed out with the context are tracked as rolled out."""
        session = ConversationSession(conversation_id="conv-123")

        # Memory inserted first, then enough exchanges to trim it out
        memory = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Memory content",
            created_at="2024-01-01",
            times_retrieved=1,
        )
        session.insert_memory_into_context(memory)
        session.add_exchange("Hello", "Hi there")
        session.add_exchange("How are you?", "I'm well!")

        # Over limit until the first three messages are gone
        def count_tokens(x):
            return 50000 if len(session.conversation_context) > 2 else 100

        removed = session.trim_context_to_limit(max_tokens=40000, count_tokens_fn=count_tokens)

        assert removed > 0
        assert "mem-1" not in session.get_in_context_memory_ids()
        # Still tracked as retrieved (prevents re-incrementing retrieval count)
        assert "mem-1" in session.retrieved_ids
        assert "mem-1" in session.session_memories

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
            mock_settings.default_max_tokens = 64000

            session = manager.create_session("conv-123")

        assert session.conversation_id == "conv-123"
        assert "conv-123" in manager._sessions

    def test_create_session_with_custom_params(self):
        """Test creating a session with custom parameters."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000

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
            mock_settings.default_max_tokens = 64000

            session = manager.create_session(
                conversation_id="conv-123",
                entity_id="gpt-entity",
            )

        assert session.model == "gpt-4o"

    def test_create_session_normalizes_uuid_to_string(self):
        """Test that UUID conversation_id is normalized to string."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000

            # Pass a UUID object instead of string
            conv_uuid = uuid.uuid4()
            session = manager.create_session(conv_uuid)

        # session.conversation_id should be a string
        assert isinstance(session.conversation_id, str)
        assert session.conversation_id == str(conv_uuid)
        # Session should be stored with string key
        assert str(conv_uuid) in manager._sessions

    def test_create_session_with_string_conversation_id(self):
        """Test that string conversation_id remains unchanged."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000

            conv_str = "test-conversation-123"
            session = manager.create_session(conv_str)

        assert session.conversation_id == conv_str
        assert isinstance(session.conversation_id, str)

    def test_get_session_exists(self):
        """Test getting an existing session."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000

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
            mock_settings.default_max_tokens = 64000

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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(
                return_value=[
                    {"message_id": retrieved_id, "retrieved_at": datetime.utcnow()}
                ]
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
            mock_settings.default_max_tokens = 64000
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
            mock_llm.build_messages.return_value = [
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
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000

            session = manager.create_session(sample_conversation.id)
            result = await manager.process_message(session, "Hello", db_session)

        assert result["content"] == "Hi there!"
        assert len(session.conversation_context) == 2  # User + assistant
        # Human messages carry a context-only timestamp prefix
        assert re.fullmatch(
            r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} [^\]]+\] Hello",
            session.conversation_context[0]["content"],
        )
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
            mock_memory.record_memory_link = AsyncMock()

            mock_llm.build_messages.return_value = [
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
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.significance_half_life_days = 60
            mock_settings.recency_boost_strength = 1.0
            mock_settings.significance_floor = 0.01
            mock_settings.retrieval_candidate_multiplier = 2
            mock_settings.initial_retrieval_top_k = 5
            mock_settings.retrieval_top_k = 5
            mock_settings.recent_reflections_enabled = False

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
    async def test_memory_link_anchored_before_user_message_timestamp(
        self, db_session, sample_conversation
    ):
        """Memory links must be timestamped just before the turn's send
        timestamp (= the human row's created_at), so a session reload
        re-inserts the memories before the human message — the position the
        live context (and therefore the prompt cache) was built with."""
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
            mock_memory.record_memory_link = AsyncMock()

            mock_llm.build_messages.return_value = []
            mock_llm.send_message = AsyncMock(return_value={
                "content": "Response",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            })
            mock_llm.count_tokens = MagicMock(return_value=50)

            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.significance_half_life_days = 60
            mock_settings.recency_boost_strength = 1.0
            mock_settings.significance_floor = 0.01
            mock_settings.retrieval_candidate_multiplier = 2
            mock_settings.initial_retrieval_top_k = 5
            mock_settings.retrieval_top_k = 5
            mock_settings.recent_reflections_enabled = False

            session = manager.create_session(sample_conversation.id)
            sent_at = datetime.utcnow()
            await manager.process_message(
                session, "Hello", db_session, user_message_timestamp=sent_at
            )

            call_kwargs = mock_memory.update_retrieval_count.call_args.kwargs
            link_retrieved_at = call_kwargs["link_retrieved_at"]
            assert link_retrieved_at is not None
            assert link_retrieved_at < sent_at
            # Anchored 1ms back, not some arbitrary wall-clock time
            assert (sent_at - link_retrieved_at) <= timedelta(milliseconds=1)

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
            mock_memory.record_memory_link = AsyncMock()

            mock_llm.build_messages.return_value = []
            mock_llm.send_message = AsyncMock(return_value={
                "content": "Response",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            })
            mock_llm.count_tokens = MagicMock(return_value=50)  # Mock token counting

            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.significance_half_life_days = 60
            mock_settings.recency_boost_strength = 1.0
            mock_settings.significance_floor = 0.01
            mock_settings.retrieval_candidate_multiplier = 2
            mock_settings.initial_retrieval_top_k = 5
            mock_settings.retrieval_top_k = 5
            mock_settings.recent_reflections_enabled = False

            session = manager.create_session(sample_conversation.id)

            # First message should retrieve memory
            await manager.process_message(session, "First", db_session)
            assert mock_memory.update_retrieval_count.call_count == 1

            # Second message - memory should be excluded
            mock_memory.search_memories.reset_mock()
            mock_memory.update_retrieval_count.reset_mock()

            await manager.process_message(session, "Second", db_session)

            # Search is called WITHOUT exclude_ids (deduplication now happens
            # at session.add_memory level, not at the search level)
            call_kwargs = mock_memory.search_memories.call_args.kwargs
            assert "exclude_ids" not in call_kwargs or call_kwargs.get("exclude_ids") is None

            # Update count should NOT be called again for same memory
            # (session.add_memory returns added=False for duplicates)
            mock_memory.update_retrieval_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_message_restores_rolled_out_memory_without_count_update(
        self, db_session, sample_conversation
    ):
        """Test that rolled-out memories can be restored without updating retrieval count."""
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
        added, is_new_retrieval = session.insert_memory_into_context(memory)
        assert added is True
        assert is_new_retrieval is True
        assert "mem-1" in session.get_in_context_memory_ids()
        assert "mem-1" in session.retrieved_ids

        # Simulate the memory rolling out via context trimming
        session.conversation_context.pop(0)
        session.memory_tracker.handle_context_rollout(
            num_messages_removed=1,
            conversation_context=session.conversation_context,
        )
        assert "mem-1" not in session.get_in_context_memory_ids()
        assert "mem-1" in session.retrieved_ids  # Still tracked

        # Re-insert the same memory (as if search returned it again)
        memory2 = MemoryEntry(
            id="mem-1",
            conversation_id="old-conv",
            role="assistant",
            content="Memory content",
            created_at="2024-01-01",
            times_retrieved=1,
            score=0.95,  # Maybe different score this time
        )
        added, is_new_retrieval = session.insert_memory_into_context(memory2)

        # Should be added back to context
        assert added is True
        # But should NOT be treated as a new retrieval (no count update needed)
        assert is_new_retrieval is False
        assert "mem-1" in session.get_in_context_memory_ids()
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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

        # Verify get_retrieved_memories_with_timestamps was called with entity_id
        mock_memory.get_retrieved_memories_with_timestamps.assert_called_once()
        call_kwargs = mock_memory.get_retrieved_memories_with_timestamps.call_args.kwargs
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(
                sample_conversation.id,
                db_session
            )

        # Verify get_retrieved_memories_with_timestamps was called without entity_id
        mock_memory.get_retrieved_memories_with_timestamps.assert_called_once()
        call_kwargs = mock_memory.get_retrieved_memories_with_timestamps.call_args.kwargs
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
        # Human messages are labeled [Human], matching session reload rendering
        # (live and reloaded context must be identical for cache stability)
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

        # Add some memories (inserted into the conversation context)
        for i in range(3):
            memory = MemoryEntry(
                id=f"mem-{i}",
                conversation_id="old-conv",
                role="assistant",
                content=f"Memory {i}",
                created_at="2024-01-01",
                times_retrieved=1,
            )
            session.insert_memory_into_context(memory)

        # Add conversation context
        session.add_exchange("Hello", "Hi")
        session.add_exchange("How are you?", "I'm well!")

        # Cache state is empty (nothing cached yet)
        assert session.last_cached_context_length == 0

        content = session.get_cache_aware_content()

        # All context is new (3 memory messages + 4 exchange messages)
        assert len(content["cached_context"]) == 0
        assert len(content["new_context"]) == 7

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

    def test_cache_breakpoint_advances_every_turn(self):
        """The breakpoint covers the full history after every exchange, so each
        turn's new messages are cached incrementally (longest-prefix matching
        makes this a write of the tail, not a miss)."""
        session = ConversationSession(conversation_id="conv-123")

        session.add_exchange("First", "Response 1")
        session.update_cache_state(len(session.conversation_context))
        assert session.last_cached_context_length == 2

        session.add_exchange("Second", "Response 2")
        session.update_cache_state(len(session.conversation_context))
        assert session.last_cached_context_length == 4

        # Everything is in the cached prefix; nothing rides uncached
        content = session.get_cache_aware_content()
        assert len(content["cached_context"]) == 4
        assert len(content["new_context"]) == 0

    def test_cache_breakpoint_advances_over_tool_exchanges(self):
        """Tool exchanges added by add_exchange are included when the
        breakpoint advances — no messages are left dangling outside the cache."""
        session = ConversationSession(conversation_id="conv-123")

        tool_exchanges = [
            {
                "assistant": {"content": [{"type": "tool_use", "name": "search", "id": "t1", "input": {}}]},
                "user": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "results"}]},
            },
        ]
        session.add_exchange("Search this", "Found it.", tool_exchanges=tool_exchanges)
        session.update_cache_state(len(session.conversation_context))

        # user + tool_use + tool_result + assistant = 4 messages, all cached
        assert session.last_cached_context_length == 4
        content = session.get_cache_aware_content()
        assert len(content["new_context"]) == 0

    def test_messages_added_after_breakpoint_ride_uncached_one_turn(self):
        """Messages appended after the breakpoint (e.g. memory-in-context
        insertions) are new_context until the next advance absorbs them."""
        session = ConversationSession(conversation_id="conv-123")

        session.add_exchange("First", "Response 1")
        session.update_cache_state(len(session.conversation_context))

        # Simulate a memory-in-context insertion after the breakpoint
        session.conversation_context.append(
            {"role": "user", "content": "[MEMORY] something relevant", "is_memory": True}
        )

        content = session.get_cache_aware_content()
        assert len(content["cached_context"]) == 2
        assert len(content["new_context"]) == 1

        # Next turn's advance absorbs it into the cached prefix
        session.add_exchange("Second", "Response 2")
        session.update_cache_state(len(session.conversation_context))
        content = session.get_cache_aware_content()
        assert len(content["cached_context"]) == 5
        assert len(content["new_context"]) == 0

    @pytest.mark.asyncio
    async def test_load_session_preserves_context_cache_length(
        self, db_session, sample_conversation, sample_messages
    ):
        """Test that load_session_from_db can preserve context cache length."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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

    def test_memory_insertions_keep_positions_for_cache_stability(self):
        """Memory messages stay at their insertion positions in the context."""
        session = ConversationSession(conversation_id="conv-123")

        # Insert memories in arrival order
        for id_suffix in ["z", "a", "m", "b"]:
            memory = MemoryEntry(
                id=f"mem-{id_suffix}",
                conversation_id="old-conv",
                role="assistant",
                content=f"Content {id_suffix}",
                created_at="2024-01-01",
                times_retrieved=1,
            )
            session.insert_memory_into_context(memory)

        # Context preserves insertion order (positions never reshuffle,
        # which is what keeps the cached prefix stable)
        memory_ids = [
            m["memory_id"] for m in session.conversation_context if m.get("is_memory")
        ]
        assert memory_ids == ["mem-z", "mem-a", "mem-m", "mem-b"]

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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        # Should use fallback since entity not in dict
        assert session.system_prompt == "Fallback system prompt"

    @pytest.mark.asyncio
    async def test_multi_entity_uses_responding_entity_prompt(self, db_session):
        """Test that multi-entity conversations use the responding entity's system prompt."""
        from app.models import Conversation, ConversationEntity, ConversationType

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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
        from app.models import Conversation, ConversationEntity, ConversationType

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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000

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
        from app.models import Conversation, ConversationEntity, ConversationType

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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
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
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(conversation.id, db_session)

        # Empty dict should use fallback
        assert session.system_prompt == "Fallback prompt"


class TestAgenticToolLoopMessages:
    """Tests for message building in the agentic tool loop.

    Memories are embedded in the conversation context, so the base message
    set is built once and reused across tool iterations, with accumulated
    tool exchanges appended.
    """

    @pytest.mark.asyncio
    async def test_first_iteration_includes_memories(self, db_session, sample_conversation):
        """A simple response (no tool use) builds once with memories in the context."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            # Track what messages are built
            build_calls = []

            def track_build_messages(**kwargs):
                build_calls.append({"kwargs": kwargs})
                return [{"role": "user", "content": "test"}]

            mock_llm.build_messages.side_effect = track_build_messages
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
            # Insert a mock memory into the session context
            memory = MemoryEntry(
                id="mem-1",
                conversation_id="old-conv",
                role="assistant",
                content="Test memory",
                created_at="2024-01-01",
                times_retrieved=1,
            )
            session.insert_memory_into_context(memory)

            # Process message
            events = []
            async for event in manager.process_message_stream(
                session, "Hello", db_session, tool_schemas=[]
            ):
                events.append(event)

        # Without tool use, messages are built only once
        assert len(build_calls) == 1

        # The memory rides inside the conversation context passed to the builder
        context_arg = build_calls[0]["kwargs"]["conversation_context"]
        memory_msgs = [m for m in context_arg if m.get("is_memory")]
        assert [m["memory_id"] for m in memory_msgs] == ["mem-1"]

    @pytest.mark.asyncio
    async def test_subsequent_iterations_reuse_base_messages(self, db_session, sample_conversation):
        """Subsequent tool loop iterations reuse the base messages plus tool exchanges."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            # Track messages sent to LLM
            sent_messages = []
            build_call_count = [0]

            def build_messages(**kwargs):
                build_call_count[0] += 1
                return [{"role": "user", "content": "base"}]

            mock_llm.build_messages.side_effect = build_messages
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
            # Insert a mock memory into the session context
            memory = MemoryEntry(
                id="mem-1",
                conversation_id="old-conv",
                role="assistant",
                content="Test memory",
                created_at="2024-01-01",
                times_retrieved=1,
            )
            session.insert_memory_into_context(memory)

            # Process message
            events = []
            async for event in manager.process_message_stream(
                session, "Search for something", db_session, tool_schemas=[{"name": "web_search"}]
            ):
                events.append(event)

        # Should have two LLM calls but only one message build (base is reused)
        assert len(sent_messages) == 2
        assert build_call_count[0] == 1

        # First iteration uses the base messages
        assert sent_messages[0][0]["content"] == "base"

        # Second iteration reuses the base messages plus the tool exchange
        assert sent_messages[1][0]["content"] == "base"
        assert len(sent_messages[1]) == 3  # base message + assistant tool_use + user tool_result
        assert sent_messages[1][1]["role"] == "assistant"
        assert sent_messages[1][2]["role"] == "user"

    @pytest.mark.asyncio
    async def test_attachments_persist_across_tool_iterations(self, db_session, sample_conversation):
        """Attachments must stay on the current message after a tool call.

        Regression test: previously the base message set rebuilt for subsequent
        tool-loop iterations was built without the attachments, so any tool call
        dropped the attached file/image from the final answer's context and the
        model responded as if nothing was attached.
        """
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            # Capture the kwargs of every build call so we can inspect attachments
            build_calls = []

            def build_messages(**kwargs):
                build_calls.append({"kwargs": kwargs})
                return [{"role": "user", "content": "msg"}]

            mock_llm.build_messages.side_effect = build_messages
            mock_llm.count_tokens = MagicMock(return_value=100)

            call_count = [0]

            async def mock_stream(messages, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
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

            mock_tool_result = MagicMock()
            mock_tool_result.tool_use_id = "tool-1"
            mock_tool_result.content = "Search results"
            mock_tool_result.is_error = False
            mock_tool.execute_tool = AsyncMock(return_value=mock_tool_result)

            attachments = {
                "images": [],
                "files": [{
                    "filename": "notes.txt",
                    "content": "the attached file body",
                    "content_type": "text",
                    "media_type": "text/plain",
                }],
            }

            session = manager.create_session(sample_conversation.id)

            events = []
            async for event in manager.process_message_stream(
                session,
                "Summarize the attached file",
                db_session,
                tool_schemas=[{"name": "web_search"}],
                attachments=attachments,
            ):
                events.append(event)

        # One build call whose messages are reused across both iterations.
        # The extracted file text is folded into the current message (matching
        # the DB-persisted rendering) so it survives both iterations; the files
        # themselves are stripped from the attachments passed to message
        # building, leaving only (ephemeral) images.
        assert len(build_calls) == 1
        for call in build_calls:
            assert call["kwargs"].get("attachments") == {"images": [], "files": []}
            current = call["kwargs"].get("current_message")
            assert "[ATTACHED FILE: notes.txt" in current
            assert "the attached file body" in current
            assert "Summarize the attached file" in current

    @pytest.mark.asyncio
    async def test_file_attachment_context_matches_db_rendering(self, db_session, sample_conversation):
        """Live context for a text-file attachment message must equal the
        stamped rendering of the DB-persisted content.

        Regression test: previously the live context stored only the stamped
        user text while the DB row (and thus a reloaded session) carried the
        [ATTACHED FILE] blocks, so every reload of such a conversation busted
        the prompt cache from that message onward.
        """
        from app.services.attachment_service import build_persistable_content
        from app.services.session_helpers import stamp_human_message

        manager = SessionManager()
        sent_at = datetime(2026, 1, 15, 12, 30, 0)
        user_message = "Summarize the attached file"
        attachments = {
            "images": [],
            "files": [{
                "filename": "notes.txt",
                "content": "the attached file body",
                "content_type": "text",
                "media_type": "text/plain",
            }],
        }

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            mock_llm.build_messages = MagicMock(
                return_value=[{"role": "user", "content": "msg"}]
            )
            mock_llm.count_tokens = MagicMock(return_value=100)

            async def mock_stream(messages, **kwargs):
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

            async for _ in manager.process_message_stream(
                session,
                user_message,
                db_session,
                attachments=attachments,
                user_message_timestamp=sent_at,
            ):
                pass

        # This is exactly what load_session_from_db renders for the persisted
        # row (routes/chat.py stores build_persistable_content(...) with
        # created_at == sent_at, and the load stamps it).
        expected = stamp_human_message(
            build_persistable_content(user_message, attachments), sent_at
        )
        user_msgs = [
            m for m in session.conversation_context
            if m["role"] == "user" and isinstance(m["content"], str)
        ]
        assert user_msgs[-1]["content"] == expected

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
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            sent_messages = []

            def build_messages(**kwargs):
                return [{"role": "user", "content": "base"}]

            mock_llm.build_messages.side_effect = build_messages
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
        """Test that without tool use, only one iteration occurs."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            sent_messages = []

            def build_messages(**kwargs):
                return [{"role": "user", "content": "base"}]

            mock_llm.build_messages.side_effect = build_messages
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
            session.insert_memory_into_context(memory)

            events = []
            async for event in manager.process_message_stream(
                session, "Hello", db_session, tool_schemas=[]
            ):
                events.append(event)

        # Should have only one LLM call, using the built base messages
        assert len(sent_messages) == 1
        assert sent_messages[0][0]["content"] == "base"

    @pytest.mark.asyncio
    async def test_base_messages_built_once_for_tool_loop(self, db_session, sample_conversation):
        """The base message set is built once and reused when tools are used."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            build_calls = []

            def track_build(**kwargs):
                build_calls.append({"kwargs": kwargs})
                return [{"role": "user", "content": "test"}]

            mock_llm.build_messages.side_effect = track_build
            mock_llm.count_tokens = MagicMock(return_value=100)

            call_count = [0]

            async def mock_stream(messages, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call: tool use to trigger lazy build of base messages
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {
                        "type": "done",
                        "content": "",
                        "stop_reason": "tool_use",
                        "content_blocks": [
                            {"type": "tool_use", "id": "tool-1", "name": "test_tool", "input": {}}
                        ],
                        "tool_use": [{"id": "tool-1", "name": "test_tool", "input": {}}],
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {},
                    }
                else:
                    # Second call: final response
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

            # Mock tool execution
            mock_tool_result = MagicMock()
            mock_tool_result.tool_use_id = "tool-1"
            mock_tool_result.content = "Tool result"
            mock_tool_result.is_error = False
            mock_tool.execute_tool = AsyncMock(return_value=mock_tool_result)

            session = manager.create_session(sample_conversation.id)
            # Insert multiple memories into the session context
            for i in range(3):
                memory = MemoryEntry(
                    id=f"mem-{i}",
                    conversation_id="old-conv",
                    role="assistant",
                    content=f"Memory {i}",
                    created_at="2024-01-01",
                    times_retrieved=1,
                )
                session.insert_memory_into_context(memory)

            events = []
            async for event in manager.process_message_stream(
                session, "Test", db_session, tool_schemas=[{"name": "test_tool"}]
            ):
                events.append(event)

        # A single build call serves both iterations; the memories ride in
        # the conversation context it was given
        assert len(build_calls) == 1
        context_arg = build_calls[0]["kwargs"]["conversation_context"]
        memory_msgs = [m for m in context_arg if m.get("is_memory")]
        assert len(memory_msgs) == 3


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
    """Tests for the tool-loop cache breakpoint sitting on the latest tool_result."""

    @pytest.mark.asyncio
    async def test_cache_control_added_to_tool_result(
        self, db_session, sample_conversation
    ):
        """Test that cache_control is placed on the tool_result of the latest exchange."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            sent_messages = []

            def build_messages(**kwargs):
                return [{"role": "user", "content": "base"}]

            mock_llm.build_messages.side_effect = build_messages
            mock_llm.count_tokens = MagicMock(return_value=3000)

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

        # Second iteration should have cache_control on the latest tool_result
        tool_result_msg = sent_messages[1][2]  # base + assistant + user (tool_result)
        assert tool_result_msg["role"] == "user"
        assert isinstance(tool_result_msg["content"], list)
        # The last content block should have cache_control
        last_block = tool_result_msg["content"][-1]
        assert last_block.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}

    @pytest.mark.asyncio
    async def test_cache_control_added_even_for_small_exchanges(
        self, db_session, sample_conversation
    ):
        """Even a small tool exchange gets cache_control — there is no token
        threshold; the breakpoint always sits on the latest tool_result."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            sent_messages = []

            def build_messages(**kwargs):
                return [{"role": "user", "content": "base"}]

            mock_llm.build_messages.side_effect = build_messages
            # Small token counts don't matter: caching is unconditional
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

        # Second iteration should have cache_control despite the small exchange
        tool_result_msg = sent_messages[1][2]  # base + assistant + user (tool_result)
        assert tool_result_msg["role"] == "user"
        assert isinstance(tool_result_msg["content"], list)
        last_block = tool_result_msg["content"][-1]
        assert last_block.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}

    @pytest.mark.asyncio
    async def test_multiple_tool_iterations_cache_control_moves_to_latest(
        self, db_session, sample_conversation
    ):
        """Test that the cache breakpoint moves to the newest exchange every iteration."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure mocks
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            sent_messages = []

            def build_messages(**kwargs):
                return [{"role": "user", "content": "base"}]

            mock_llm.build_messages.side_effect = build_messages
            mock_llm.count_tokens = MagicMock(return_value=3000)

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

        # Second iteration: cache_control on the (only) tool result
        # Messages: base, assistant (tool_use), user (tool_result with cache_control)
        second_call_tool_result = sent_messages[1][2]
        assert second_call_tool_result["role"] == "user"
        last_block = second_call_tool_result["content"][-1]
        assert last_block.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}

        # Third iteration: the breakpoint moves to the NEWEST tool exchange.
        # First tool result should NOT have cache_control (breakpoint moved past it)
        third_call_first_tool_result = sent_messages[2][2]
        assert third_call_first_tool_result["role"] == "user"
        first_block = third_call_first_tool_result["content"][-1]
        assert first_block.get("cache_control") is None  # Breakpoint moved

        # Second tool_result should have cache_control (current breakpoint)
        third_call_second_tool_result = sent_messages[2][4]
        assert third_call_second_tool_result["role"] == "user"
        last_block = third_call_second_tool_result["content"][-1]
        assert last_block.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}


class TestRecentReflectionsInjection:
    """First-turn injection of the most recent memory_save reflections."""

    @staticmethod
    def _reflection_dict(mem_id, created_at="2026-01-02T00:00:00", content=None):
        return {
            "id": mem_id,
            "conversation_id": "reflection-source-conv",
            "role": "reflection",
            "content": content or f"Reflection {mem_id}",
            "created_at": created_at,
            "times_retrieved": 0,
            "last_retrieved_at": None,
            "memory_status": None,
        }

    @staticmethod
    def _configure_settings(mock_settings, enabled=True, count=3):
        mock_settings.default_model = "claude-sonnet-4-5-20250929"
        mock_settings.default_temperature = 1.0
        mock_settings.default_max_tokens = 64000
        mock_settings.context_token_limit = 150000
        mock_settings.significance_half_life_days = 60
        mock_settings.recency_boost_strength = 1.0
        mock_settings.significance_floor = 0.01
        mock_settings.retrieval_candidate_multiplier = 2
        mock_settings.initial_retrieval_top_k = 5
        mock_settings.retrieval_top_k = 5
        mock_settings.memory_role_balance_enabled = False
        mock_settings.tool_use_max_iterations = 10
        mock_settings.recent_reflections_enabled = enabled
        mock_settings.recent_reflections_count = count

    @staticmethod
    def _configure_llm(mock_llm):
        mock_llm.build_messages.return_value = [
            {"role": "user", "content": "Hello"}
        ]
        mock_llm.send_message = AsyncMock(return_value={
            "content": "Hi!",
            "model": "claude-sonnet-4-5-20250929",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        })
        mock_llm.count_tokens = MagicMock(return_value=10)

    @pytest.mark.asyncio
    async def test_first_turn_injects_recent_reflections(self, db_session, sample_conversation):
        """On the first turn, recent reflections are injected alongside semantic results."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[])
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()
            # Newest first, as the real get_recent_reflections returns them
            mock_memory.get_recent_reflections = AsyncMock(return_value=[
                self._reflection_dict("refl-new", created_at="2026-01-02T00:00:00"),
                self._reflection_dict("refl-old", created_at="2026-01-01T00:00:00"),
            ])
            self._configure_llm(mock_llm)
            self._configure_settings(mock_settings, enabled=True, count=2)

            session = manager.create_session(sample_conversation.id)
            # Regression: load_session_from_db prepends the entity's notes as
            # a context message before any conversation exists. That seed must
            # not defeat first-turn detection.
            session.conversation_context.append({
                "role": "user",
                "content": "[ENTITY NOTES]\nSome notes\n[/ENTITY NOTES]",
                "is_notes": True,
            })
            session.last_cached_context_length = 1
            result = await manager.process_message(session, "Hello", db_session)

            # Both reflections were injected and reported as retrieved
            retrieved_ids = [m["id"] for m in result["new_memories_retrieved"]]
            assert set(retrieved_ids) == {"refl-new", "refl-old"}
            assert result["total_memories_in_context"] == 2

            # Selected purely by recency, so tagged with the dedicated source
            assert session.session_memories["refl-new"].source == "recent_reflection"
            assert session.session_memories["refl-new"].score == 0.0

            # Link-only tracking: the ConversationMemoryLink is recorded (feeds
            # reload re-insertion/dedup) but times_retrieved is NOT incremented —
            # that counter is reserved for semantic recall
            assert mock_memory.record_memory_link.call_count == 2
            mock_memory.update_retrieval_count.assert_not_called()

            # The fetch excluded the current conversation and was recency-limited
            call_kwargs = mock_memory.get_recent_reflections.call_args.kwargs
            assert call_kwargs["limit"] == 2
            assert call_kwargs["exclude_conversation_id"] == str(sample_conversation.id)

    @pytest.mark.asyncio
    async def test_turns_after_first_are_unaffected(self, db_session, sample_conversation):
        """Recent reflections are only pulled on the first turn."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[])
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()
            mock_memory.get_recent_reflections = AsyncMock(return_value=[
                self._reflection_dict("refl-1"),
            ])
            self._configure_llm(mock_llm)
            self._configure_settings(mock_settings, enabled=True, count=1)

            session = manager.create_session(sample_conversation.id)

            await manager.process_message(session, "First", db_session)
            assert mock_memory.get_recent_reflections.call_count == 1

            await manager.process_message(session, "Second", db_session)
            # Not called again after the first turn
            assert mock_memory.get_recent_reflections.call_count == 1

    @pytest.mark.asyncio
    async def test_injected_reflections_deduplicated_from_later_retrieval(
        self, db_session, sample_conversation
    ):
        """Once injected, recent reflections are tracked exactly like
        automatically retrieved memories: later semantic retrieval skips them
        (no re-insert, no count update) and they sit in the in-context set
        that memory_query excludes."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[])
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()
            mock_memory.get_recent_reflections = AsyncMock(return_value=[
                self._reflection_dict("refl-1"),
            ])
            self._configure_llm(mock_llm)
            self._configure_settings(mock_settings, enabled=True, count=1)

            session = manager.create_session(sample_conversation.id)

            await manager.process_message(session, "First", db_session)

            # The injected reflection is tracked like an automatic retrieval:
            # in the memory tracker (what memory_query's exclusion reads) and
            # in the session's retrieved set
            assert "refl-1" in session.get_in_context_memory_ids()
            assert "refl-1" in session.retrieved_ids

            # Second turn: semantic search now surfaces the same reflection
            mock_memory.search_memories = AsyncMock(return_value=[
                {"id": "refl-1", "score": 0.95, "conversation_id": "reflection-source-conv", "created_at": "2026-01-02T00:00:00", "last_retrieved_at": None}
            ])
            mock_memory.get_full_memory_content = AsyncMock(
                return_value=self._reflection_dict("refl-1")
            )

            result = await manager.process_message(session, "Second", db_session)

            # Skipped as already in context: not re-inserted, not re-reported,
            # and no retrieval-count update
            assert result["new_memories_retrieved"] == []
            assert result["total_memories_in_context"] == 1
            memory_messages = [m for m in session.conversation_context if m.get("is_memory")]
            assert len(memory_messages) == 1
            mock_memory.update_retrieval_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_by_default_setting(self, db_session, sample_conversation):
        """With recent_reflections_enabled=False, no reflection fetch happens."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[])
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()
            mock_memory.get_recent_reflections = AsyncMock(return_value=[])
            self._configure_llm(mock_llm)
            self._configure_settings(mock_settings, enabled=False)

            session = manager.create_session(sample_conversation.id)
            result = await manager.process_message(session, "Hello", db_session)

            mock_memory.get_recent_reflections.assert_not_called()
            assert result["new_memories_retrieved"] == []

    @pytest.mark.asyncio
    async def test_deduplicates_against_semantic_retrieval(self, db_session, sample_conversation):
        """A reflection that also surfaced semantically is excluded, not injected twice."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            # Semantic retrieval returns the reflection itself
            mock_memory.search_memories = AsyncMock(return_value=[
                {"id": "refl-dup", "score": 0.9, "conversation_id": "reflection-source-conv",
                 "created_at": "2026-01-01", "role": "reflection", "last_retrieved_at": None}
            ])
            mock_memory.get_full_memory_content = AsyncMock(
                return_value=self._reflection_dict("refl-dup", created_at="2026-01-01T00:00:00")
            )
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()
            # Simulate the fetch returning the duplicate anyway (belt-and-braces:
            # the SQL-level exclude_ids filter is tested in test_memory_service)
            mock_memory.get_recent_reflections = AsyncMock(return_value=[
                self._reflection_dict("refl-dup", created_at="2026-01-01T00:00:00"),
            ])
            self._configure_llm(mock_llm)
            self._configure_settings(mock_settings, enabled=True, count=1)

            session = manager.create_session(sample_conversation.id)
            result = await manager.process_message(session, "Hello", db_session)

            # The semantically retrieved duplicate is passed as an exclusion
            call_kwargs = mock_memory.get_recent_reflections.call_args.kwargs
            assert "refl-dup" in call_kwargs["exclude_ids"]

            # Injected exactly once via the semantic path, which is the only
            # one that counts as a retrieval
            retrieved_ids = [m["id"] for m in result["new_memories_retrieved"]]
            assert retrieved_ids == ["refl-dup"]
            assert result["total_memories_in_context"] == 1
            assert mock_memory.update_retrieval_count.call_count == 1
            mock_memory.record_memory_link.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_first_turn_inserts_reflections_into_context(
        self, db_session, sample_conversation
    ):
        """Streaming path with memory-in-context: reflections become context messages."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[])
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()
            mock_memory.get_recent_reflections = AsyncMock(return_value=[
                self._reflection_dict("refl-new", created_at="2026-01-02T00:00:00"),
                self._reflection_dict("refl-old", created_at="2026-01-01T00:00:00"),
            ])
            self._configure_settings(mock_settings, enabled=True, count=2)
            mock_llm.build_messages.return_value = [
                {"role": "user", "content": "Hello"}
            ]
            mock_llm.count_tokens = MagicMock(return_value=10)

            async def mock_stream(*args, **kwargs):
                yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                yield {
                    "type": "done",
                    "content": "Hi!",
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "stop_reason": "end_turn",
                    "content_blocks": [{"type": "text", "text": "Hi!"}],
                }

            mock_llm.send_message_stream = mock_stream

            session = manager.create_session(sample_conversation.id)
            events = []
            async for event in manager.process_message_stream(session, "Hello", db_session):
                events.append(event)

            # The memories event reports both injected reflections
            memories_event = next(e for e in events if e["type"] == "memories")
            assert {m["id"] for m in memories_event["new_memories"]} == {"refl-new", "refl-old"}

            # Reflections were inserted as memory context messages,
            # oldest first so the newest sits closest to the current message
            memory_messages = [
                m for m in session.conversation_context if m.get("is_memory")
            ]
            assert len(memory_messages) == 2
            assert "Reflection refl-old" in memory_messages[0]["content"]
            assert "Reflection refl-new" in memory_messages[1]["content"]

    @pytest.mark.asyncio
    async def test_deduplicated_slots_are_backfilled(self, db_session, sample_conversation):
        """Slots freed by dedup are backfilled: the AI always gets N recent reflections."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            # Semantic retrieval surfaces the most recent reflection itself
            mock_memory.search_memories = AsyncMock(return_value=[
                {"id": "refl-newest", "score": 0.9, "conversation_id": "reflection-source-conv",
                 "created_at": "2026-01-03", "role": "reflection", "last_retrieved_at": None}
            ])
            mock_memory.get_full_memory_content = AsyncMock(
                return_value=self._reflection_dict("refl-newest", created_at="2026-01-03T00:00:00")
            )
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()

            # Emulate the real SQL behavior: exclusion applies before LIMIT,
            # so excluded reflections are backfilled by the next-most-recent
            all_reflections = [
                self._reflection_dict("refl-newest", created_at="2026-01-03T00:00:00"),
                self._reflection_dict("refl-middle", created_at="2026-01-02T00:00:00"),
                self._reflection_dict("refl-oldest", created_at="2026-01-01T00:00:00"),
            ]

            async def fake_get_recent(db, entity_id=None, limit=None,
                                      exclude_conversation_id=None, exclude_ids=None):
                exclude_ids = exclude_ids or set()
                eligible = [r for r in all_reflections if r["id"] not in exclude_ids]
                return eligible[:limit]

            mock_memory.get_recent_reflections = AsyncMock(side_effect=fake_get_recent)
            self._configure_llm(mock_llm)
            self._configure_settings(mock_settings, enabled=True, count=2)

            session = manager.create_session(sample_conversation.id)
            result = await manager.process_message(session, "Hello", db_session)

            # refl-newest came in semantically; its recent-reflection slot was
            # backfilled so two OTHER recent reflections were still injected
            retrieved_ids = {m["id"] for m in result["new_memories_retrieved"]}
            assert retrieved_ids == {"refl-newest", "refl-middle", "refl-oldest"}
            assert result["total_memories_in_context"] == 3
            assert session.session_memories["refl-middle"].source == "recent_reflection"
            assert session.session_memories["refl-oldest"].source == "recent_reflection"
            # Only the semantic retrieval counts toward times_retrieved; the
            # two recency-injected reflections get link-only tracking
            assert mock_memory.update_retrieval_count.call_count == 1
            assert mock_memory.record_memory_link.call_count == 2

    @staticmethod
    async def _create_multi_entity_conversation(db, spoken_entity_ids):
        """Multi-entity conversation with one human turn and one assistant
        response per entity in spoken_entity_ids (in order)."""
        conv = Conversation(
            id=str(uuid.uuid4()),
            title="Multi-entity test",
            conversation_type=ConversationType.MULTI_ENTITY,
            entity_id="multi-entity",
            llm_model_used="claude-sonnet-4-5-20250929",
        )
        db.add(conv)
        db.add(Message(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            role=MessageRole.HUMAN,
            content="Hello everyone",
        ))
        for eid in spoken_entity_ids:
            db.add(Message(
                id=str(uuid.uuid4()),
                conversation_id=conv.id,
                role=MessageRole.ASSISTANT,
                content=f"Response from {eid}",
                speaker_entity_id=eid,
            ))
        await db.commit()
        return conv

    @staticmethod
    def _make_multi_entity_session(manager, conv, responding_entity_id, label):
        """Session state as chat.py sets it up for a responding entity,
        with the prior exchange already in context (as a reload would build)."""
        session = manager.create_session(conv.id, entity_id=responding_entity_id)
        session.is_multi_entity = True
        session.entity_labels = {"entity-a": "Alpha", "entity-b": "Beta"}
        session.responding_entity_label = label
        session.conversation_context.append(
            {"role": "user", "content": "[Human]: Hello everyone"}
        )
        session.conversation_context.append(
            {"role": "assistant", "content": "[Alpha]: Response from entity-a"}
        )
        session.last_cached_context_length = 2
        return session

    @pytest.mark.asyncio
    async def test_multi_entity_first_response_gets_own_reflections(self, db_session):
        """In a multi-entity conversation, an entity responding for the first
        time gets its recent reflections even though the conversation already
        has turns from other participants — and the fetch is scoped to it."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[])
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()
            mock_memory.get_recent_reflections = AsyncMock(return_value=[
                self._reflection_dict("refl-b"),
            ])
            self._configure_llm(mock_llm)
            self._configure_settings(mock_settings, enabled=True, count=1)

            # Entity A has already spoken; entity B responds for the first time
            conv = await self._create_multi_entity_conversation(
                db_session, spoken_entity_ids=["entity-a"]
            )
            session = self._make_multi_entity_session(manager, conv, "entity-b", "Beta")
            result = await manager.process_message(session, "Over to you, Beta", db_session)

            # B's reflections were injected despite the existing conversation turns
            retrieved_ids = [m["id"] for m in result["new_memories_retrieved"]]
            assert retrieved_ids == ["refl-b"]
            assert session.session_memories["refl-b"].source == "recent_reflection"

            # The fetch was scoped to the responding entity only — B never
            # sees another participant's reflections
            call_kwargs = mock_memory.get_recent_reflections.call_args.kwargs
            assert call_kwargs["entity_id"] == "entity-b"
            mock_memory.record_memory_link.assert_called_once()
            assert mock_memory.record_memory_link.call_args.kwargs["entity_id"] == "entity-b"
            mock_memory.update_retrieval_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_entity_entity_that_already_spoke_gets_none(self, db_session):
        """An entity that already responded in the conversation doesn't get
        recent reflections again, regardless of other participants' turns."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[])
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()
            mock_memory.get_recent_reflections = AsyncMock(return_value=[
                self._reflection_dict("refl-b"),
            ])
            self._configure_llm(mock_llm)
            self._configure_settings(mock_settings, enabled=True, count=1)

            # Both entities have already spoken; entity B responds again
            conv = await self._create_multi_entity_conversation(
                db_session, spoken_entity_ids=["entity-a", "entity-b"]
            )
            session = self._make_multi_entity_session(manager, conv, "entity-b", "Beta")
            result = await manager.process_message(session, "Anything else?", db_session)

            mock_memory.get_recent_reflections.assert_not_called()
            assert result["new_memories_retrieved"] == []

    def test_first_turn_detection_ignores_context_seeds(self):
        """Notes, memory, and notice messages don't count as conversation."""
        session = ConversationSession(conversation_id="conv-1")
        assert session.has_conversational_messages() is False

        session.conversation_context.append(
            {"role": "user", "content": "[ENTITY NOTES]...", "is_notes": True}
        )
        session.conversation_context.append(
            {"role": "user", "content": "[MEMORY abc123 ...]", "is_memory": True}
        )
        session.conversation_context.append(
            {"role": "user", "content": "[CONTEXT NOTICE] ...", "is_context_notice": True}
        )
        assert session.has_conversational_messages() is False

        session.conversation_context.append({"role": "user", "content": "Hello"})
        assert session.has_conversational_messages() is True


class TestMemoryQueryResultDedup:
    """Tests for dedup of memories surfaced via memory_query tool results."""

    def test_add_exchange_stamps_memory_query_ids_on_tool_result(self):
        """memory_query_ids on an exchange land on the tool_result context message."""
        session = ConversationSession(conversation_id="conv-1")

        session.add_exchange(
            "Query your memory please",
            "Done.",
            tool_exchanges=[
                {
                    "assistant": {"content": [{"type": "tool_use", "id": "t1", "name": "memory_query", "input": {"query": "x"}}]},
                    "user": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "--- Memory aaaa1111 (...)"}]},
                    "memory_query_ids": ["mem-full-id-1", "mem-full-id-2"],
                },
                {
                    "assistant": {"content": [{"type": "tool_use", "id": "t2", "name": "web_search", "input": {"query": "y"}}]},
                    "user": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "results"}]},
                },
            ],
        )

        tool_results = [m for m in session.conversation_context if m.get("is_tool_result")]
        assert tool_results[0]["memory_query_ids"] == ["mem-full-id-1", "mem-full-id-2"]
        assert "memory_query_ids" not in tool_results[1]

        assert session.get_query_surfaced_memory_ids() == {"mem-full-id-1", "mem-full-id-2"}

    def test_query_surfaced_ids_shrink_when_tool_result_trims_out(self):
        """Query-surfaced IDs leave the dedup set when trimming removes the tool result."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}],
            "is_tool_result": True,
            "memory_query_ids": ["mem-1"],
        })
        session.conversation_context.append({"role": "user", "content": "hello"})

        assert session.get_query_surfaced_memory_ids() == {"mem-1"}

        session.conversation_context.pop(0)
        assert session.get_query_surfaced_memory_ids() == set()

    @pytest.mark.asyncio
    async def test_automatic_retrieval_skips_query_surfaced_memories(
        self, db_session, sample_conversation
    ):
        """Automatic retrieval must not re-insert a memory the entity already
        saw in a memory_query tool result (no [MEMORY] duplicate, no count update)."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = True
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.search_memories = AsyncMock(return_value=[
                {"id": "mem-query-1", "score": 0.9, "conversation_id": "old-conv", "created_at": "2024-01-01", "last_retrieved_at": None}
            ])
            mock_memory.get_full_memory_content = AsyncMock(return_value={
                "id": "mem-query-1",
                "conversation_id": "old-conv",
                "role": "assistant",
                "content": "Memory the entity already saw via memory_query",
                "created_at": "2024-01-01",
                "times_retrieved": 1,
                "last_retrieved_at": None,
            })
            mock_memory.update_retrieval_count = AsyncMock()
            mock_memory.record_memory_link = AsyncMock()

            mock_llm.build_messages.return_value = []
            mock_llm.send_message = AsyncMock(return_value={
                "content": "Response",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            })
            mock_llm.count_tokens = MagicMock(return_value=50)

            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.significance_half_life_days = 60
            mock_settings.recency_boost_strength = 1.0
            mock_settings.significance_floor = 0.01
            mock_settings.retrieval_candidate_multiplier = 2
            mock_settings.initial_retrieval_top_k = 5
            mock_settings.retrieval_top_k = 5
            mock_settings.recent_reflections_enabled = False

            session = manager.create_session(sample_conversation.id)
            # A previous turn's memory_query surfaced this memory
            session.conversation_context.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}],
                "is_tool_result": True,
                "memory_query_ids": ["mem-query-1"],
            })

            result = await manager.process_message(session, "Hello", db_session)

            assert result["new_memories_retrieved"] == []
            assert result["total_memories_in_context"] == 0
            assert not any(m.get("is_memory") for m in session.conversation_context)
            mock_memory.update_retrieval_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_session_restores_memory_query_ids(
        self, db_session, sample_conversation, sample_messages
    ):
        """Reload re-stamps memory_query tool_result messages with the full IDs
        resolved from the persisted result's 8-char prefixes."""
        import json

        surfaced_id = str(uuid.uuid4())
        prefix = surfaced_id[:8]
        result_text = (
            f'Found 1 memories matching: "test"\n\n'
            f"--- Memory {prefix} (You said, 3.0 days ago, similarity: 0.812) ---\n"
            f"Some remembered content\n"
        )

        tool_use_msg = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_USE,
            content=json.dumps([
                {"type": "tool_use", "id": "toolu_1", "name": "memory_query", "input": {"query": "test"}}
            ]),
        )
        tool_result_msg = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": result_text, "is_error": False}
            ]),
        )
        db_session.add_all([tool_use_msg, tool_result_msg])
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_memory.resolve_memory_id_prefixes = AsyncMock(return_value=[surfaced_id])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.notes_enabled = False
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(
                sample_conversation.id, db_session
            )

        mock_memory.resolve_memory_id_prefixes.assert_awaited_once()
        assert mock_memory.resolve_memory_id_prefixes.call_args.args[1] == [prefix]

        tool_results = [m for m in session.conversation_context if m.get("is_tool_result")]
        assert len(tool_results) == 1
        assert tool_results[0]["memory_query_ids"] == [surfaced_id]
        assert session.get_query_surfaced_memory_ids() == {surfaced_id}

    @pytest.mark.asyncio
    async def test_load_session_ignores_non_memory_query_tool_results(
        self, db_session, sample_conversation, sample_messages
    ):
        """Results of other tools are not parsed for memory ID prefixes even if
        their text happens to contain a matching line."""
        import json

        tool_use_msg = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_USE,
            content=json.dumps([
                {"type": "tool_use", "id": "toolu_2", "name": "web_search", "input": {"query": "x"}}
            ]),
        )
        tool_result_msg = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "toolu_2", "content": "--- Memory deadbeef (You said, similarity: 0.9) ---", "is_error": False}
            ]),
        )
        db_session.add_all([tool_use_msg, tool_result_msg])
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_memory.resolve_memory_id_prefixes = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.notes_enabled = False
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(
                sample_conversation.id, db_session
            )

        mock_memory.resolve_memory_id_prefixes.assert_not_awaited()
        tool_results = [m for m in session.conversation_context if m.get("is_tool_result")]
        assert all("memory_query_ids" not in m for m in tool_results)


class TestNoteStampTracking:
    """Tests for note-content stamps (notes_read dedup state)."""

    def test_add_exchange_stamps_note_stamps_on_tool_result(self):
        """note_stamps on an exchange land on the tool_result context message."""
        from app.services.notes_tools import note_content_hash

        session = ConversationSession(conversation_id="conv-1")
        stamp = {
            "owner": "TestEntity",
            "filename": "plan.md",
            "hash": note_content_hash("v1"),
            "source": "write",
        }

        session.add_exchange(
            "Write a note please",
            "Done.",
            tool_exchanges=[
                {
                    "assistant": {"content": [{"type": "tool_use", "id": "t1", "name": "notes_write", "input": {"filename": "plan.md", "content": "v1"}}]},
                    "user": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Created 'plan.md' in your notes"}]},
                    "note_stamps": [stamp],
                },
                {
                    "assistant": {"content": [{"type": "tool_use", "id": "t2", "name": "web_search", "input": {"query": "y"}}]},
                    "user": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "results"}]},
                },
            ],
        )

        tool_results = [m for m in session.conversation_context if m.get("is_tool_result")]
        assert tool_results[0]["note_stamps"] == [stamp]
        assert "note_stamps" not in tool_results[1]

        assert session.get_in_context_note_stamps() == {("TestEntity", "plan.md"): [stamp]}

    def test_note_stamps_shrink_when_message_trims_out(self):
        """Stamps leave the state when trimming removes their message."""
        session = ConversationSession(conversation_id="conv-1")
        stamp = {"owner": "E", "filename": "a.md", "hash": "abc", "source": "read"}
        session.conversation_context.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}],
            "is_tool_result": True,
            "note_stamps": [stamp],
        })
        session.conversation_context.append({"role": "user", "content": "hello"})

        assert session.get_in_context_note_stamps() == {("E", "a.md"): [stamp]}

        session.conversation_context.pop(0)
        assert session.get_in_context_note_stamps() == {}

    def test_stamps_grouped_by_owner_and_filename_in_order(self):
        session = ConversationSession(conversation_id="conv-1")
        s1 = {"owner": "E", "filename": "a.md", "hash": "h1", "source": "read"}
        s2 = {"owner": "shared", "filename": "a.md", "hash": "h2", "source": "read"}
        s3 = {"owner": "E", "filename": "a.md", "hash": "h3", "source": "edit"}
        session.conversation_context.append(
            {"role": "user", "content": "x", "is_tool_result": True, "note_stamps": [s1, s2]}
        )
        session.conversation_context.append(
            {"role": "user", "content": "y", "is_tool_result": True, "note_stamps": [s3]}
        )

        stamps = session.get_in_context_note_stamps()
        assert stamps[("E", "a.md")] == [s1, s3]
        assert stamps[("shared", "a.md")] == [s2]

    def test_build_notes_context_message_stamps_seed(self):
        """The notes seed message carries seed stamps for the index files."""
        from app.services.notes_tools import note_content_hash

        manager = SessionManager()
        message = manager._build_notes_context_message(
            "TestEntity", "# my index", "# shared index"
        )

        assert message["is_notes"] is True
        assert message["note_stamps"] == [
            {
                "owner": "TestEntity",
                "filename": "index.md",
                "hash": note_content_hash("# my index"),
                "source": "seed",
            },
            {
                "owner": "shared",
                "filename": "index.md",
                "hash": note_content_hash("# shared index"),
                "source": "seed",
            },
        ]

    @pytest.mark.asyncio
    async def test_load_session_captures_and_freezes_notes_seed(
        self, db_session, sample_conversation
    ):
        """
        The first single-entity load snapshots the current notes into
        conversation.notes_seed; later loads rebuild the seed message from that
        frozen snapshot, so editing the notes on disk mid-conversation does not
        change the position-0 seed (which would bust the prompt cache).
        """
        from app.services.notes_service import notes_service

        manager = SessionManager()

        entity = MagicMock()
        entity.label = "TestEntity"
        entity.llm_provider = "anthropic"
        entity.default_model = None

        async def load_with_notes(entity_notes, shared_notes):
            with patch("app.services.session_manager.memory_service") as mock_memory, \
                 patch("app.services.session_manager.settings") as mock_settings, \
                 patch.object(notes_service, "get_index_content",
                              return_value=entity_notes), \
                 patch.object(notes_service, "get_shared_index_content",
                              return_value=shared_notes):
                mock_memory.is_configured.return_value = False
                mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
                mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
                mock_settings.default_model = "claude-sonnet-4-5-20250929"
                mock_settings.default_temperature = 1.0
                mock_settings.default_max_tokens = 64000
                mock_settings.notes_enabled = True
                mock_settings.get_entity_by_index.return_value = entity
                return await manager.load_session_from_db(
                    sample_conversation.id, db_session
                )

        # First load captures the current disk content as the frozen snapshot.
        session = await load_with_notes("# index v1", "# shared v1")
        notes_msgs = [m for m in session.conversation_context if m.get("is_notes")]
        assert len(notes_msgs) == 1
        assert "# index v1" in notes_msgs[0]["content"]
        assert "# shared v1" in notes_msgs[0]["content"]

        await db_session.refresh(sample_conversation)
        assert sample_conversation.notes_seed == {
            "entity": "# index v1",
            "shared": "# shared v1",
        }

        # A later load, after the notes changed on disk, rebuilds the identical
        # seed from the snapshot rather than the new disk content.
        session2 = await load_with_notes("# index v2", "# shared v2")
        notes_msgs2 = [m for m in session2.conversation_context if m.get("is_notes")]
        assert len(notes_msgs2) == 1
        assert notes_msgs2[0]["content"] == notes_msgs[0]["content"]
        assert "# index v2" not in notes_msgs2[0]["content"]

        await db_session.refresh(sample_conversation)
        assert sample_conversation.notes_seed == {
            "entity": "# index v1",
            "shared": "# shared v1",
        }

    @pytest.mark.asyncio
    async def test_stream_empty_response_soft_errors_without_mutating_session(
        self, db_session, sample_conversation
    ):
        """
        An empty final response (no text, no tool use) yields an
        empty_response soft error and leaves the session untouched: no exchange
        is appended and the cache breakpoint does not advance, so the warm
        session can be retried in place instead of via a reload.
        """
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            mock_llm.build_messages.return_value = [{"role": "user", "content": "x"}]
            mock_llm.count_tokens = MagicMock(return_value=10)

            async def mock_stream(*args, **kwargs):
                yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                yield {
                    "type": "done",
                    "content": "",
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                    "stop_reason": "end_turn",
                    "content_blocks": [],
                }

            mock_llm.send_message_stream = mock_stream

            session = manager.create_session(sample_conversation.id)
            before_len = len(session.conversation_context)
            before_cache = session.last_cached_context_length

            events = []
            async for event in manager.process_message_stream(
                session, "Hello", db_session, tool_schemas=[]
            ):
                events.append(event)

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert error_events[0].get("error_type") == "empty_response"
        assert not any(e.get("type") == "done" for e in events)
        # Session untouched: no exchange appended, cache breakpoint not advanced.
        assert len(session.conversation_context) == before_len
        assert session.last_cached_context_length == before_cache

    @pytest.mark.asyncio
    async def test_stream_whitespace_only_response_soft_errors(
        self, db_session, sample_conversation
    ):
        """A whitespace-only final response is treated as empty."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            mock_llm.build_messages.return_value = [{"role": "user", "content": "x"}]
            mock_llm.count_tokens = MagicMock(return_value=10)

            async def mock_stream(*args, **kwargs):
                yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                yield {"type": "token", "content": "   \n"}
                yield {
                    "type": "done",
                    "content": "   \n",
                    "model": "claude-sonnet-4-5-20250929",
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                    "stop_reason": "end_turn",
                    "content_blocks": [{"type": "text", "text": "   \n"}],
                }

            mock_llm.send_message_stream = mock_stream

            session = manager.create_session(sample_conversation.id)
            before_len = len(session.conversation_context)

            events = []
            async for event in manager.process_message_stream(
                session, "Hello", db_session, tool_schemas=[]
            ):
                events.append(event)

        assert any(
            e.get("type") == "error" and e.get("error_type") == "empty_response"
            for e in events
        )
        assert len(session.conversation_context) == before_len

    @pytest.mark.asyncio
    async def test_load_session_restores_note_stamps(
        self, db_session, sample_conversation, sample_messages
    ):
        """Reload re-stamps notes tool_result messages, replaying notes_edit
        records against the content reconstructed from the history walk."""
        import json

        from app.services.notes_tools import note_content_hash

        base_time = datetime.utcnow()
        write_use = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_USE,
            content=json.dumps([
                {"type": "tool_use", "id": "toolu_w", "name": "notes_write",
                 "input": {"filename": "plan.md", "content": "Status: draft"}}
            ]),
            created_at=base_time + timedelta(seconds=1),
        )
        write_result = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "toolu_w",
                 "content": "Created 'plan.md' in your notes", "is_error": False}
            ]),
            created_at=base_time + timedelta(seconds=2),
        )
        edit_use = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_USE,
            content=json.dumps([
                {"type": "tool_use", "id": "toolu_e", "name": "notes_edit",
                 "input": {"filename": "plan.md", "old_string": "draft", "new_string": "final"}}
            ]),
            created_at=base_time + timedelta(seconds=3),
        )
        edit_result = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "toolu_e",
                 "content": "Edited 'plan.md' in your notes (1 replacement)", "is_error": False}
            ]),
            created_at=base_time + timedelta(seconds=4),
        )
        db_session.add_all([write_use, write_result, edit_use, edit_result])
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.notes_enabled = False
            mock_settings.get_entity_by_index.return_value = MagicMock(
                label="TestEntity", llm_provider="anthropic", default_model=None
            )

            session = await manager.load_session_from_db(
                sample_conversation.id, db_session
            )

        tool_results = [m for m in session.conversation_context if m.get("is_tool_result")]
        assert len(tool_results) == 2
        assert tool_results[0]["note_stamps"] == [{
            "owner": "TestEntity",
            "filename": "plan.md",
            "hash": note_content_hash("Status: draft"),
            "source": "write",
        }]
        assert tool_results[1]["note_stamps"] == [{
            "owner": "TestEntity",
            "filename": "plan.md",
            "hash": note_content_hash("Status: final"),
            "source": "edit",
        }]

    @pytest.mark.asyncio
    async def test_load_session_edit_without_base_gets_no_stamp(
        self, db_session, sample_conversation, sample_messages
    ):
        """An edit whose base content never appeared in history is not stamped."""
        import json

        edit_use = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_USE,
            content=json.dumps([
                {"type": "tool_use", "id": "toolu_e", "name": "notes_edit",
                 "input": {"filename": "plan.md", "old_string": "a", "new_string": "b"}}
            ]),
        )
        edit_result = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "toolu_e",
                 "content": "Edited 'plan.md' in your notes (1 replacement)", "is_error": False}
            ]),
        )
        db_session.add_all([edit_use, edit_result])
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.notes_enabled = False
            mock_settings.get_entity_by_index.return_value = MagicMock(
                label="TestEntity", llm_provider="anthropic", default_model=None
            )

            session = await manager.load_session_from_db(
                sample_conversation.id, db_session
            )

        tool_results = [m for m in session.conversation_context if m.get("is_tool_result")]
        assert all("note_stamps" not in m for m in tool_results)

    @pytest.mark.asyncio
    async def test_load_session_read_result_restores_stamp_and_feeds_edit_replay(
        self, db_session, sample_conversation, sample_messages
    ):
        """A notes_read result provides the full content: it is stamped and
        becomes the base for replaying a later notes_edit."""
        import json

        from app.services.notes_tools import note_content_hash

        base_time = datetime.utcnow()
        read_use = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_USE,
            content=json.dumps([
                {"type": "tool_use", "id": "toolu_r", "name": "notes_read",
                 "input": {"filename": "log.md", "shared": True}}
            ]),
            created_at=base_time + timedelta(seconds=1),
        )
        read_result = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "toolu_r",
                 "content": "entry one", "is_error": False}
            ]),
            created_at=base_time + timedelta(seconds=2),
        )
        edit_use = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_USE,
            content=json.dumps([
                {"type": "tool_use", "id": "toolu_e", "name": "notes_edit",
                 "input": {"filename": "log.md", "old_string": "one", "new_string": "two", "shared": True}}
            ]),
            created_at=base_time + timedelta(seconds=3),
        )
        edit_result = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "toolu_e",
                 "content": "Edited 'log.md' in shared notes (1 replacement)", "is_error": False}
            ]),
            created_at=base_time + timedelta(seconds=4),
        )
        db_session.add_all([read_use, read_result, edit_use, edit_result])
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.notes_enabled = False
            mock_settings.get_entity_by_index.return_value = MagicMock(
                label="TestEntity", llm_provider="anthropic", default_model=None
            )

            session = await manager.load_session_from_db(
                sample_conversation.id, db_session
            )

        tool_results = [m for m in session.conversation_context if m.get("is_tool_result")]
        assert tool_results[0]["note_stamps"] == [{
            "owner": "shared",
            "filename": "log.md",
            "hash": note_content_hash("entry one"),
            "source": "read",
        }]
        assert tool_results[1]["note_stamps"] == [{
            "owner": "shared",
            "filename": "log.md",
            "hash": note_content_hash("entry two"),
            "source": "edit",
        }]

    @pytest.mark.asyncio
    async def test_load_session_skips_pointer_and_error_results(
        self, db_session, sample_conversation, sample_messages
    ):
        """[NOTE IN CONTEXT] pointers and Error results add no stamps."""
        import json

        from app.services.notes_tools import NOTE_IN_CONTEXT_MARKER

        base_time = datetime.utcnow()
        pointer_use = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_USE,
            content=json.dumps([
                {"type": "tool_use", "id": "toolu_p", "name": "notes_read",
                 "input": {"filename": "a.md"}}
            ]),
            created_at=base_time + timedelta(seconds=1),
        )
        pointer_result = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "toolu_p",
                 "content": f"{NOTE_IN_CONTEXT_MARKER} The current content of 'a.md' ...", "is_error": False}
            ]),
            created_at=base_time + timedelta(seconds=2),
        )
        error_use = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_USE,
            content=json.dumps([
                {"type": "tool_use", "id": "toolu_x", "name": "notes_read",
                 "input": {"filename": "b.md"}}
            ]),
            created_at=base_time + timedelta(seconds=3),
        )
        error_result = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "toolu_x",
                 "content": "Error: File not found: b.md", "is_error": False}
            ]),
            created_at=base_time + timedelta(seconds=4),
        )
        db_session.add_all([pointer_use, pointer_result, error_use, error_result])
        await db_session.commit()

        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.notes_enabled = False
            mock_settings.get_entity_by_index.return_value = MagicMock(
                label="TestEntity", llm_provider="anthropic", default_model=None
            )

            session = await manager.load_session_from_db(
                sample_conversation.id, db_session
            )

        tool_results = [m for m in session.conversation_context if m.get("is_tool_result")]
        assert all("note_stamps" not in m for m in tool_results)
