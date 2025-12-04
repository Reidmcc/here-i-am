"""
Unit tests for MemoryService.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
import uuid

from app.services.memory_service import MemoryService
from app.models import Message, MessageRole, ConversationMemoryLink


class TestMemoryServiceConfiguration:
    """Tests for MemoryService configuration."""

    def test_is_configured_without_pinecone_key(self):
        """Test is_configured returns False without API key."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            assert service.is_configured() is False

    def test_is_configured_with_pinecone_key(self):
        """Test is_configured returns True with API key."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.get_entity_by_index.return_value = MagicMock()

            service = MemoryService()
            # is_configured() without entity_id just checks API key
            assert service.is_configured() is True

    def test_is_configured_with_entity_id(self):
        """Test is_configured checks entity existence."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.get_entity_by_index.return_value = MagicMock()

            service = MemoryService()
            assert service.is_configured("valid-entity") is True

            mock_settings.get_entity_by_index.return_value = None
            assert service.is_configured("invalid-entity") is False

    def test_pc_lazy_initialization(self):
        """Test Pinecone client is lazily initialized."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            assert service._pc is None
            assert service.pc is None  # Still None without key

    def test_get_index_caching(self, mock_pinecone_index):
        """Test that indexes are cached."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()
            mock_pc = MagicMock()
            mock_pc.Index.return_value = mock_pinecone_index
            service._pc = mock_pc

            # First call should create index
            index1 = service.get_index("test-index")
            # Second call should return cached
            index2 = service.get_index("test-index")

            assert index1 is index2
            mock_pc.Index.assert_called_once_with("test-index")


class TestMemoryServiceEmbeddings:
    """Tests for embedding generation."""

    @pytest.mark.asyncio
    async def test_get_embedding_success(self):
        """Test successful embedding generation."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"

            service = MemoryService()

            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1024)]

            mock_anthropic = MagicMock()
            mock_anthropic.embeddings.create = AsyncMock(return_value=mock_response)
            service._anthropic = mock_anthropic

            embedding = await service.get_embedding("Test text")

            assert embedding is not None
            assert len(embedding) == 1024
            mock_anthropic.embeddings.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_embedding_truncates_long_text(self):
        """Test that long text is truncated."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"

            service = MemoryService()

            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1024)]

            mock_anthropic = MagicMock()
            mock_anthropic.embeddings.create = AsyncMock(return_value=mock_response)
            service._anthropic = mock_anthropic

            long_text = "x" * 10000
            await service.get_embedding(long_text)

            # Verify truncated text was passed
            call_args = mock_anthropic.embeddings.create.call_args.kwargs
            assert len(call_args["input"][0]) == 8000

    @pytest.mark.asyncio
    async def test_get_embedding_failure_returns_none(self):
        """Test that embedding failure returns None gracefully."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"

            service = MemoryService()

            mock_anthropic = MagicMock()
            mock_anthropic.embeddings.create = AsyncMock(side_effect=Exception("API error"))
            service._anthropic = mock_anthropic

            embedding = await service.get_embedding("Test text")

            assert embedding is None


class TestMemoryServiceStorage:
    """Tests for memory storage."""

    @pytest.mark.asyncio
    async def test_store_memory_success(self, mock_pinecone_index):
        """Test successful memory storage."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()
            service._indexes["default"] = mock_pinecone_index

            # Mock embedding generation
            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
            mock_anthropic = MagicMock()
            mock_anthropic.embeddings.create = AsyncMock(return_value=mock_response)
            service._anthropic = mock_anthropic

            result = await service.store_memory(
                message_id="msg-123",
                conversation_id="conv-456",
                role="assistant",
                content="This is a test memory.",
                created_at=datetime.utcnow(),
            )

            assert result is True
            mock_pinecone_index.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_memory_not_configured(self):
        """Test store_memory returns False when not configured."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()

            result = await service.store_memory(
                message_id="msg-123",
                conversation_id="conv-456",
                role="assistant",
                content="This is a test memory.",
                created_at=datetime.utcnow(),
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_store_memory_embedding_failure(self, mock_pinecone_index):
        """Test store_memory handles embedding failure."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()
            service._indexes["default"] = mock_pinecone_index

            # Mock embedding failure
            mock_anthropic = MagicMock()
            mock_anthropic.embeddings.create = AsyncMock(side_effect=Exception("API error"))
            service._anthropic = mock_anthropic

            result = await service.store_memory(
                message_id="msg-123",
                conversation_id="conv-456",
                role="assistant",
                content="This is a test memory.",
                created_at=datetime.utcnow(),
            )

            assert result is False


class TestMemoryServiceSearch:
    """Tests for memory search."""

    @pytest.mark.asyncio
    async def test_search_memories_success(self, mock_pinecone_index):
        """Test successful memory search."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.7
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()
            service._indexes["default"] = mock_pinecone_index

            # Mock embedding generation
            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
            mock_anthropic = MagicMock()
            mock_anthropic.embeddings.create = AsyncMock(return_value=mock_response)
            service._anthropic = mock_anthropic

            results = await service.search_memories("What did we discuss?")

            assert len(results) == 1
            assert results[0]["id"] == "test-memory-id"
            assert results[0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_search_memories_filters_low_score(self, mock_pinecone_index):
        """Test that low score results are filtered."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.95  # High threshold
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()

            # Create match with score below threshold
            mock_match = MagicMock()
            mock_match.id = "low-score-memory"
            mock_match.score = 0.8  # Below 0.95 threshold
            mock_match.metadata = {"conversation_id": "conv-1", "created_at": "2024-01-01"}

            mock_query_result = MagicMock()
            mock_query_result.matches = [mock_match]
            mock_pinecone_index.query = MagicMock(return_value=mock_query_result)

            service._indexes["default"] = mock_pinecone_index

            # Mock embedding
            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
            mock_anthropic = MagicMock()
            mock_anthropic.embeddings.create = AsyncMock(return_value=mock_response)
            service._anthropic = mock_anthropic

            results = await service.search_memories("Query")

            # Result should be filtered out
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_memories_excludes_current_conversation(self, mock_pinecone_index):
        """Test that current conversation is excluded from results."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.7
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()

            # Create match from current conversation
            mock_match = MagicMock()
            mock_match.id = "current-conv-memory"
            mock_match.score = 0.9
            mock_match.metadata = {
                "conversation_id": "current-conv-id",
                "created_at": "2024-01-01",
            }

            mock_query_result = MagicMock()
            mock_query_result.matches = [mock_match]
            mock_pinecone_index.query = MagicMock(return_value=mock_query_result)

            service._indexes["default"] = mock_pinecone_index

            # Mock embedding
            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
            mock_anthropic = MagicMock()
            mock_anthropic.embeddings.create = AsyncMock(return_value=mock_response)
            service._anthropic = mock_anthropic

            results = await service.search_memories(
                "Query",
                exclude_conversation_id="current-conv-id"
            )

            # Result should be excluded
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_memories_excludes_ids(self, mock_pinecone_index):
        """Test that specified IDs are excluded from results."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.7
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()
            service._indexes["default"] = mock_pinecone_index

            # Mock embedding
            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
            mock_anthropic = MagicMock()
            mock_anthropic.embeddings.create = AsyncMock(return_value=mock_response)
            service._anthropic = mock_anthropic

            results = await service.search_memories(
                "Query",
                exclude_ids={"test-memory-id"}  # Exclude the mock match
            )

            # Result should be excluded
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_memories_not_configured(self):
        """Test search returns empty when not configured."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            results = await service.search_memories("Query")

            assert results == []


class TestMemoryServiceDatabase:
    """Tests for database operations."""

    @pytest.mark.asyncio
    async def test_get_full_memory_content(self, db_session, sample_conversation, sample_messages):
        """Test getting full memory content from database."""
        service = MemoryService()
        message = sample_messages[0]

        result = await service.get_full_memory_content(message.id, db_session)

        assert result is not None
        assert result["id"] == message.id
        assert result["content"] == message.content
        assert result["role"] == message.role.value

    @pytest.mark.asyncio
    async def test_get_full_memory_content_not_found(self, db_session):
        """Test getting non-existent memory returns None."""
        service = MemoryService()

        result = await service.get_full_memory_content("nonexistent-id", db_session)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_retrieved_ids_for_conversation(self, db_session, sample_conversation, sample_messages):
        """Test getting retrieved memory IDs for conversation."""
        service = MemoryService()

        # Create some memory links
        for msg in sample_messages:
            link = ConversationMemoryLink(
                conversation_id=sample_conversation.id,
                message_id=msg.id,
            )
            db_session.add(link)
        await db_session.commit()

        result = await service.get_retrieved_ids_for_conversation(
            sample_conversation.id,
            db_session
        )

        assert len(result) == 2
        assert all(msg.id in result for msg in sample_messages)

    @pytest.mark.asyncio
    async def test_get_retrieved_ids_empty(self, db_session, sample_conversation):
        """Test getting retrieved IDs when none exist."""
        service = MemoryService()

        result = await service.get_retrieved_ids_for_conversation(
            sample_conversation.id,
            db_session
        )

        assert result == set()


class TestMemoryServiceRetrievalCount:
    """Tests for retrieval count updates."""

    @pytest.mark.asyncio
    async def test_update_retrieval_count_sql(self, db_session, sample_conversation, sample_messages):
        """Test updating retrieval count in SQL."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""  # No Pinecone for this test

            service = MemoryService()
            message = sample_messages[0]
            initial_count = message.times_retrieved

            await service.update_retrieval_count(
                message.id,
                sample_conversation.id,
                db_session,
            )

            await db_session.refresh(message)
            assert message.times_retrieved == initial_count + 1
            assert message.last_retrieved_at is not None


class TestMemoryServiceDelete:
    """Tests for memory deletion."""

    @pytest.mark.asyncio
    async def test_delete_memory_success(self, mock_pinecone_index):
        """Test successful memory deletion."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()
            service._indexes["default"] = mock_pinecone_index

            result = await service.delete_memory("msg-123")

            assert result is True
            mock_pinecone_index.delete.assert_called_once_with(ids=["msg-123"])

    @pytest.mark.asyncio
    async def test_delete_memory_not_configured(self):
        """Test delete returns False when not configured."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()

            result = await service.delete_memory("msg-123")

            assert result is False
