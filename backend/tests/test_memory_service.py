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
            # Mock entity with host (required for serverless indexes)
            mock_entity = MagicMock()
            mock_entity.host = "https://test-index.svc.pinecone.io"
            mock_settings.get_entity_by_index.return_value = mock_entity

            service = MemoryService()
            mock_pc = MagicMock()
            mock_pc.Index.return_value = mock_pinecone_index
            service._pc = mock_pc

            # First call should create index
            index1 = service.get_index("test-index")
            # Second call should return cached
            index2 = service.get_index("test-index")

            assert index1 is index2
            mock_pc.Index.assert_called_once_with("test-index", host="https://test-index.svc.pinecone.io")


class TestMemoryServiceIntegratedInference:
    """Tests for Pinecone integrated inference (no client-side embeddings)."""

    def test_no_embedding_method(self):
        """Verify that MemoryService doesn't have a get_embedding method.

        Embeddings are now handled by Pinecone's integrated inference.
        """
        service = MemoryService()
        assert not hasattr(service, 'get_embedding'), \
            "MemoryService should not have get_embedding - Pinecone handles embeddings"

    def test_uses_upsert_records_for_storage(self):
        """Verify store_memory uses upsert_records (Pinecone integrated inference)."""
        # This is verified in the store_memory tests below
        pass


class TestMemoryServiceStorage:
    """Tests for memory storage using Pinecone integrated inference."""

    @pytest.mark.asyncio
    async def test_store_memory_success(self, mock_pinecone_index):
        """Test successful memory storage using upsert_records (integrated inference)."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()
            service._indexes["default"] = mock_pinecone_index

            # Add upsert_records mock (Pinecone handles embedding internally)
            mock_pinecone_index.upsert_records = MagicMock()

            result = await service.store_memory(
                message_id="msg-123",
                conversation_id="conv-456",
                role="assistant",
                content="This is a test memory.",
                created_at=datetime.utcnow(),
            )

            assert result is True
            mock_pinecone_index.upsert_records.assert_called_once()

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
    async def test_store_memory_pinecone_failure(self, mock_pinecone_index):
        """Test store_memory handles Pinecone API failure."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()
            service._indexes["default"] = mock_pinecone_index

            # Mock upsert_records failure
            mock_pinecone_index.upsert_records = MagicMock(side_effect=Exception("API error"))

            result = await service.store_memory(
                message_id="msg-123",
                conversation_id="conv-456",
                role="assistant",
                content="This is a test memory.",
                created_at=datetime.utcnow(),
            )

            assert result is False


class TestMemoryServiceSearch:
    """Tests for memory search using Pinecone integrated inference."""

    @pytest.mark.asyncio
    async def test_search_memories_success(self, mock_pinecone_index):
        """Test successful memory search using index.search (integrated inference)."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.7
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()
            service._indexes["default"] = mock_pinecone_index

            # Mock cache service to avoid cache hits (set directly on _cache_service)
            mock_cache = MagicMock()
            mock_cache.get_search_results.return_value = None
            service._cache_service = mock_cache

            # Mock the new search response structure (integrated inference)
            mock_hit = MagicMock()
            mock_hit.to_dict.return_value = {
                "_id": "test-memory-id",
                "_score": 0.9,
                "fields": {
                    "conversation_id": "old-conversation-id",
                    "created_at": "2024-01-01T12:00:00",
                    "role": "assistant",
                    "content_preview": "This is a test memory...",
                    "times_retrieved": 5,
                },
            }

            mock_result = MagicMock()
            mock_result.hits = [mock_hit]
            mock_search_result = MagicMock()
            mock_search_result.result = mock_result
            mock_pinecone_index.search = MagicMock(return_value=mock_search_result)

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

            # Mock cache service (set directly on _cache_service)
            mock_cache = MagicMock()
            mock_cache.get_search_results.return_value = None
            service._cache_service = mock_cache

            # Create match with score below threshold using new search API structure
            mock_hit = MagicMock()
            mock_hit.to_dict.return_value = {
                "_id": "low-score-memory",
                "_score": 0.8,  # Below 0.95 threshold
                "fields": {"conversation_id": "conv-1", "created_at": "2024-01-01"},
            }

            mock_result = MagicMock()
            mock_result.hits = [mock_hit]
            mock_search_result = MagicMock()
            mock_search_result.result = mock_result
            mock_pinecone_index.search = MagicMock(return_value=mock_search_result)

            service._indexes["default"] = mock_pinecone_index

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

            # Mock cache service (set directly on _cache_service)
            mock_cache = MagicMock()
            mock_cache.get_search_results.return_value = None
            service._cache_service = mock_cache

            # Create match from current conversation using new API structure
            mock_hit = MagicMock()
            mock_hit.to_dict.return_value = {
                "_id": "current-conv-memory",
                "_score": 0.9,
                "fields": {
                    "conversation_id": "current-conv-id",
                    "created_at": "2024-01-01",
                },
            }

            mock_result = MagicMock()
            mock_result.hits = [mock_hit]
            mock_search_result = MagicMock()
            mock_search_result.result = mock_result
            mock_pinecone_index.search = MagicMock(return_value=mock_search_result)

            service._indexes["default"] = mock_pinecone_index

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

            # Mock cache service (set directly on _cache_service)
            mock_cache = MagicMock()
            mock_cache.get_search_results.return_value = None
            service._cache_service = mock_cache

            # Create match using new API structure
            mock_hit = MagicMock()
            mock_hit.to_dict.return_value = {
                "_id": "test-memory-id",
                "_score": 0.9,
                "fields": {
                    "conversation_id": "conv-1",
                    "created_at": "2024-01-01",
                },
            }

            mock_result = MagicMock()
            mock_result.hits = [mock_hit]
            mock_search_result = MagicMock()
            mock_search_result.result = mock_result
            mock_pinecone_index.search = MagicMock(return_value=mock_search_result)

            service._indexes["default"] = mock_pinecone_index

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

    @pytest.mark.asyncio
    async def test_get_retrieved_ids_filters_by_entity(self, db_session, sample_conversation, sample_messages):
        """Test getting retrieved IDs filtered by entity_id for multi-entity isolation."""
        service = MemoryService()

        # Create memory links for different entities
        link1 = ConversationMemoryLink(
            conversation_id=sample_conversation.id,
            message_id=sample_messages[0].id,
            entity_id="claude-main",
        )
        link2 = ConversationMemoryLink(
            conversation_id=sample_conversation.id,
            message_id=sample_messages[1].id,
            entity_id="gpt-test",
        )
        db_session.add(link1)
        db_session.add(link2)
        await db_session.commit()

        # Get IDs for claude-main only
        claude_ids = await service.get_retrieved_ids_for_conversation(
            sample_conversation.id,
            db_session,
            entity_id="claude-main"
        )

        # Get IDs for gpt-test only
        gpt_ids = await service.get_retrieved_ids_for_conversation(
            sample_conversation.id,
            db_session,
            entity_id="gpt-test"
        )

        # Each entity should only see its own retrieved memories
        assert len(claude_ids) == 1
        assert sample_messages[0].id in claude_ids
        assert sample_messages[1].id not in claude_ids

        assert len(gpt_ids) == 1
        assert sample_messages[1].id in gpt_ids
        assert sample_messages[0].id not in gpt_ids

    @pytest.mark.asyncio
    async def test_get_retrieved_ids_without_entity_returns_all(self, db_session, sample_conversation, sample_messages):
        """Test getting retrieved IDs without entity_id returns all (backward compatible)."""
        service = MemoryService()

        # Create memory links for different entities
        link1 = ConversationMemoryLink(
            conversation_id=sample_conversation.id,
            message_id=sample_messages[0].id,
            entity_id="claude-main",
        )
        link2 = ConversationMemoryLink(
            conversation_id=sample_conversation.id,
            message_id=sample_messages[1].id,
            entity_id="gpt-test",
        )
        db_session.add(link1)
        db_session.add(link2)
        await db_session.commit()

        # Get all IDs (no entity filter)
        all_ids = await service.get_retrieved_ids_for_conversation(
            sample_conversation.id,
            db_session
            # entity_id not provided
        )

        # Should return both
        assert len(all_ids) == 2
        assert sample_messages[0].id in all_ids
        assert sample_messages[1].id in all_ids

    @pytest.mark.asyncio
    async def test_get_retrieved_ids_entity_filter_with_null_entity_ids(
        self, db_session, sample_conversation, sample_messages
    ):
        """Test entity filtering excludes links with null entity_id when filtering."""
        service = MemoryService()

        # Create a link without entity_id (legacy data)
        link1 = ConversationMemoryLink(
            conversation_id=sample_conversation.id,
            message_id=sample_messages[0].id,
            # entity_id not set - simulates old data
        )
        # Create a link with entity_id
        link2 = ConversationMemoryLink(
            conversation_id=sample_conversation.id,
            message_id=sample_messages[1].id,
            entity_id="claude-main",
        )
        db_session.add(link1)
        db_session.add(link2)
        await db_session.commit()

        # Filter by claude-main - should only get the link with matching entity
        claude_ids = await service.get_retrieved_ids_for_conversation(
            sample_conversation.id,
            db_session,
            entity_id="claude-main"
        )

        assert len(claude_ids) == 1
        assert sample_messages[1].id in claude_ids
        # Link without entity_id should not be included
        assert sample_messages[0].id not in claude_ids


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

    @pytest.mark.asyncio
    async def test_update_retrieval_count_stores_entity_id(self, db_session, sample_conversation, sample_messages):
        """Test that update_retrieval_count stores entity_id in the link."""
        from sqlalchemy import select

        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""  # No Pinecone for this test

            service = MemoryService()
            message = sample_messages[0]

            await service.update_retrieval_count(
                message.id,
                sample_conversation.id,
                db_session,
                entity_id="claude-main",
            )

            # Check the link has entity_id set
            result = await db_session.execute(
                select(ConversationMemoryLink).where(
                    ConversationMemoryLink.conversation_id == sample_conversation.id,
                    ConversationMemoryLink.message_id == message.id,
                )
            )
            link = result.scalar_one()
            assert link.entity_id == "claude-main"

    @pytest.mark.asyncio
    async def test_update_retrieval_count_entity_id_none_for_single_entity(
        self, db_session, sample_conversation, sample_messages
    ):
        """Test that entity_id is None when not provided (single-entity conversations)."""
        from sqlalchemy import select

        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            message = sample_messages[0]

            await service.update_retrieval_count(
                message.id,
                sample_conversation.id,
                db_session,
                # entity_id not provided
            )

            result = await db_session.execute(
                select(ConversationMemoryLink).where(
                    ConversationMemoryLink.conversation_id == sample_conversation.id,
                    ConversationMemoryLink.message_id == message.id,
                )
            )
            link = result.scalar_one()
            assert link.entity_id is None


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
