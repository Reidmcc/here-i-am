"""
Unit tests for SessionManager.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
import uuid

from app.services.session_manager import SessionManager, ConversationSession, MemoryEntry
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

        result = session.add_memory(memory)

        assert result is True
        assert "mem-1" in session.retrieved_ids
        assert "mem-1" in session.session_memories
        assert session.session_memories["mem-1"] == memory

    def test_add_memory_duplicate(self):
        """Test adding a duplicate memory is rejected."""
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
        result = session.add_memory(memory)

        assert result is False
        assert len(session.session_memories) == 1

    def test_get_memories_for_injection_sorted(self):
        """Test memories are sorted by score for injection."""
        session = ConversationSession(conversation_id="conv-123")

        # Add memories with different scores
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

        # Should be sorted by score descending
        assert len(memories) == 3
        assert memories[0]["id"] == "mem-1"  # score 0.9
        assert memories[1]["id"] == "mem-2"  # score 0.7
        assert memories[2]["id"] == "mem-0"  # score 0.5

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


class TestSessionManager:
    """Tests for SessionManager class."""

    def test_create_session(self):
        """Test creating a new session."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 4096

            session = manager.create_session("conv-123")

        assert session.conversation_id == "conv-123"
        assert "conv-123" in manager._sessions

    def test_create_session_with_custom_params(self):
        """Test creating a session with custom parameters."""
        manager = SessionManager()

        with patch("app.services.session_manager.settings") as mock_settings:
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 4096

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
            mock_entity.model_provider = "openai"
            mock_settings.get_entity_by_index.return_value = mock_entity
            mock_settings.get_default_model_for_provider.return_value = "gpt-4o"
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 4096

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
            mock_settings.default_max_tokens = 4096

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
            mock_settings.default_max_tokens = 4096

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
            mock_settings.default_max_tokens = 4096
            mock_settings.get_entity_by_index.return_value = None

            session = await manager.load_session_from_db(
                sample_conversation.id,
                db_session
            )

        assert session is not None
        assert session.conversation_id == sample_conversation.id
        assert session.model == sample_conversation.model_used
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
            mock_settings.default_max_tokens = 4096
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
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 4096

            session = manager.create_session(sample_conversation.id)
            result = await manager.process_message(session, "Hello", db_session)

        assert result["content"] == "Hi there!"
        assert len(session.conversation_context) == 2  # User + assistant
        assert session.conversation_context[0]["content"] == "Hello"
        assert session.conversation_context[1]["content"] == "Hi there!"

    @pytest.mark.asyncio
    async def test_process_message_with_memory_retrieval(self, db_session, sample_conversation):
        """Test message processing with memory retrieval."""
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.settings") as mock_settings:
            # Configure memory retrieval
            mock_memory.is_configured.return_value = True
            mock_memory.search_memories = AsyncMock(return_value=[
                {
                    "id": "mem-1",
                    "score": 0.9,
                    "conversation_id": "old-conv",
                    "created_at": "2024-01-01",
                    "role": "assistant",
                }
            ])
            mock_memory.get_full_memory_content = AsyncMock(return_value={
                "id": "mem-1",
                "conversation_id": "old-conv",
                "role": "assistant",
                "content": "Previous memory content",
                "created_at": "2024-01-01",
                "times_retrieved": 2,
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

            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 4096

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
            mock_memory.search_memories = AsyncMock(return_value=[
                {"id": "mem-1", "score": 0.9, "conversation_id": "old-conv"}
            ])
            mock_memory.get_full_memory_content = AsyncMock(return_value={
                "id": "mem-1",
                "conversation_id": "old-conv",
                "role": "assistant",
                "content": "Memory",
                "created_at": "2024-01-01",
                "times_retrieved": 1,
            })
            mock_memory.update_retrieval_count = AsyncMock()

            mock_llm.build_messages_with_memories.return_value = []
            mock_llm.send_message = AsyncMock(return_value={
                "content": "Response",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            })

            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 4096

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
