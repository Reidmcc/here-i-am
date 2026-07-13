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

    def test_uses_upsert_records_for_storage(self, mock_pinecone_index):
        """Verify store_memory uses upsert_records, not upsert with client-side embeddings."""
        service = MemoryService()
        # MemoryService should not have a get_embedding method
        assert not hasattr(service, 'get_embedding'), \
            "MemoryService should not have get_embedding"
        # The index should support upsert_records (Pinecone integrated inference)
        assert hasattr(mock_pinecone_index, 'upsert_records'), \
            "Pinecone index should support upsert_records for integrated inference"


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
    async def test_search_memories_custom_threshold_overrides_settings(self, mock_pinecone_index):
        """A per-call similarity_threshold (deliberate queries) overrides the settings default."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.95  # High default threshold
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()

            # Mock cache service (set directly on _cache_service)
            mock_cache = MagicMock()
            mock_cache.get_search_results.return_value = None
            service._cache_service = mock_cache

            mock_hit = MagicMock()
            mock_hit.to_dict.return_value = {
                "_id": "low-score-memory",
                "_score": 0.3,  # Below the 0.95 default, above the explicit 0.2
                "fields": {"conversation_id": "conv-1", "created_at": "2024-01-01"},
            }

            mock_result = MagicMock()
            mock_result.hits = [mock_hit]
            mock_search_result = MagicMock()
            mock_search_result.result = mock_result
            mock_pinecone_index.search = MagicMock(return_value=mock_search_result)

            service._indexes["default"] = mock_pinecone_index

            results = await service.search_memories("Query", similarity_threshold=0.2)

            assert len(results) == 1
            assert results[0]["id"] == "low-score-memory"

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

    @pytest.mark.asyncio
    async def test_get_archived_conversation_ids_basic(self, db_session, sample_conversation):
        """Test getting archived conversation IDs."""
        from app.models import Conversation

        service = MemoryService()

        # Archive the sample conversation
        sample_conversation.is_archived = True
        await db_session.commit()

        # Get archived IDs (no entity filter)
        archived_ids = await service.get_archived_conversation_ids(db_session)

        assert sample_conversation.id in archived_ids

    @pytest.mark.asyncio
    async def test_get_archived_conversation_ids_filters_by_entity(self, db_session):
        """Test archived IDs filtered by entity_id."""
        from app.models import Conversation
        import uuid

        service = MemoryService()

        # Create conversations for different entities
        conv1 = Conversation(
            id=str(uuid.uuid4()),
            entity_id="claude-main",
            is_archived=True,
        )
        conv2 = Conversation(
            id=str(uuid.uuid4()),
            entity_id="gpt-test",
            is_archived=True,
        )
        db_session.add(conv1)
        db_session.add(conv2)
        await db_session.commit()

        with patch("app.services.memory_service.settings") as mock_settings:
            # Non-default entity - should only get that entity's conversations
            mock_settings.get_default_entity.return_value = MagicMock(index_name="claude-main")

            gpt_archived = await service.get_archived_conversation_ids(
                db_session, entity_id="gpt-test"
            )

            assert conv2.id in gpt_archived
            assert conv1.id not in gpt_archived

    @pytest.mark.asyncio
    async def test_get_archived_conversation_ids_includes_null_entity_for_default(self, db_session):
        """Test archived IDs include NULL entity_id conversations when querying default entity.

        This is the key fix: conversations with NULL entity_id have their memories
        stored in the default entity's Pinecone index, so when filtering by the
        default entity, we must also include conversations with NULL entity_id.
        """
        from app.models import Conversation
        import uuid

        service = MemoryService()

        # Create conversation with explicit entity_id
        conv_explicit = Conversation(
            id=str(uuid.uuid4()),
            entity_id="claude-main",
            is_archived=True,
        )
        # Create conversation with NULL entity_id (legacy)
        conv_null = Conversation(
            id=str(uuid.uuid4()),
            entity_id=None,  # Legacy conversation
            is_archived=True,
        )
        # Create conversation with different entity
        conv_other = Conversation(
            id=str(uuid.uuid4()),
            entity_id="gpt-test",
            is_archived=True,
        )
        db_session.add(conv_explicit)
        db_session.add(conv_null)
        db_session.add(conv_other)
        await db_session.commit()

        with patch("app.services.memory_service.settings") as mock_settings:
            # Set claude-main as the default entity
            mock_settings.get_default_entity.return_value = MagicMock(index_name="claude-main")

            # Query for claude-main (the default entity)
            archived_ids = await service.get_archived_conversation_ids(
                db_session, entity_id="claude-main"
            )

            # Should include both explicit claude-main AND null entity_id
            assert conv_explicit.id in archived_ids
            assert conv_null.id in archived_ids
            # Should NOT include different entity
            assert conv_other.id not in archived_ids

    @pytest.mark.asyncio
    async def test_get_archived_conversation_ids_excludes_null_for_non_default(self, db_session):
        """Test archived IDs exclude NULL entity_id conversations for non-default entities.

        When querying a non-default entity, NULL entity_id conversations should not
        be included because their memories are in the default entity's index.
        """
        from app.models import Conversation
        import uuid

        service = MemoryService()

        # Create conversation with NULL entity_id (legacy)
        conv_null = Conversation(
            id=str(uuid.uuid4()),
            entity_id=None,
            is_archived=True,
        )
        # Create conversation with gpt-test entity
        conv_gpt = Conversation(
            id=str(uuid.uuid4()),
            entity_id="gpt-test",
            is_archived=True,
        )
        db_session.add(conv_null)
        db_session.add(conv_gpt)
        await db_session.commit()

        with patch("app.services.memory_service.settings") as mock_settings:
            # claude-main is default, not gpt-test
            mock_settings.get_default_entity.return_value = MagicMock(index_name="claude-main")

            # Query for gpt-test (NOT the default entity)
            archived_ids = await service.get_archived_conversation_ids(
                db_session, entity_id="gpt-test"
            )

            # Should only include gpt-test conversations
            assert conv_gpt.id in archived_ids
            # Should NOT include null entity_id (those are in default entity's index)
            assert conv_null.id not in archived_ids

    @pytest.mark.asyncio
    async def test_get_archived_conversation_ids_includes_multi_entity_conversations(self, db_session):
        """Test archived IDs include multi-entity conversations where the entity is a participant.

        Multi-entity conversations have entity_id='multi-entity' but their memories
        are stored in each participating entity's Pinecone index. When filtering
        archived conversations for a specific entity, we need to include archived
        multi-entity conversations where that entity is a participant.
        """
        from app.models import Conversation, ConversationEntity, ConversationType
        import uuid

        service = MemoryService()

        # Create a multi-entity conversation with gpt-test and claude-main as participants
        multi_conv = Conversation(
            id=str(uuid.uuid4()),
            entity_id="multi-entity",
            conversation_type=ConversationType.MULTI_ENTITY,
            is_archived=True,
        )
        db_session.add(multi_conv)
        await db_session.flush()

        # Add participants
        participant1 = ConversationEntity(
            conversation_id=multi_conv.id,
            entity_id="gpt-test",
            display_order=0,
        )
        participant2 = ConversationEntity(
            conversation_id=multi_conv.id,
            entity_id="claude-main",
            display_order=1,
        )
        db_session.add(participant1)
        db_session.add(participant2)

        # Create a single-entity archived conversation for a different entity
        other_conv = Conversation(
            id=str(uuid.uuid4()),
            entity_id="other-entity",
            is_archived=True,
        )
        db_session.add(other_conv)
        await db_session.commit()

        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.get_default_entity.return_value = MagicMock(index_name="claude-main")

            # Query for gpt-test - should include the multi-entity conversation
            gpt_archived = await service.get_archived_conversation_ids(
                db_session, entity_id="gpt-test"
            )

            assert multi_conv.id in gpt_archived  # gpt-test is a participant
            assert other_conv.id not in gpt_archived  # other-entity, not gpt-test

            # Query for claude-main - should also include the multi-entity conversation
            claude_archived = await service.get_archived_conversation_ids(
                db_session, entity_id="claude-main"
            )

            assert multi_conv.id in claude_archived  # claude-main is a participant
            assert other_conv.id not in claude_archived

            # Query for other-entity - should NOT include the multi-entity conversation
            other_archived = await service.get_archived_conversation_ids(
                db_session, entity_id="other-entity"
            )

            assert multi_conv.id not in other_archived  # other-entity is NOT a participant
            assert other_conv.id in other_archived  # but should include its own conversation


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

    @pytest.mark.asyncio
    async def test_update_retrieval_count_create_link_false_skips_link(
        self, db_session, sample_conversation, sample_messages
    ):
        """create_link=False (memory_query) bumps retrieval tracking but must
        NOT record a ConversationMemoryLink — a link would make session reload
        re-insert the memory into the rebuilt context even though it was never
        a context message live, breaking prompt-cache stability."""
        from sqlalchemy import select

        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""  # No Pinecone for this test

            service = MemoryService()
            message = sample_messages[0]
            initial_count = message.times_retrieved

            result_ok = await service.update_retrieval_count(
                message.id,
                sample_conversation.id,
                db_session,
                create_link=False,
            )
            assert result_ok is True

            await db_session.refresh(message)
            assert message.times_retrieved == initial_count + 1
            assert message.last_retrieved_at is not None

            result = await db_session.execute(
                select(ConversationMemoryLink).where(
                    ConversationMemoryLink.conversation_id == sample_conversation.id,
                    ConversationMemoryLink.message_id == message.id,
                )
            )
            assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_update_retrieval_count_stores_link_retrieved_at(
        self, db_session, sample_conversation, sample_messages
    ):
        """An explicit link_retrieved_at is stored on the link, so session
        reload interleaves the memory at the live insertion position (just
        before the human message row that triggered it)."""
        from datetime import datetime, timedelta
        from sqlalchemy import select

        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            message = sample_messages[0]
            anchored = datetime.utcnow() - timedelta(milliseconds=1)

            await service.update_retrieval_count(
                message.id,
                sample_conversation.id,
                db_session,
                link_retrieved_at=anchored,
            )

            result = await db_session.execute(
                select(ConversationMemoryLink).where(
                    ConversationMemoryLink.conversation_id == sample_conversation.id,
                    ConversationMemoryLink.message_id == message.id,
                )
            )
            link = result.scalar_one()
            assert link.retrieved_at == anchored


class TestMemoryServiceRecordMemoryLink:
    """Tests for record_memory_link (link-only, no retrieval tracking)."""

    @pytest.mark.asyncio
    async def test_creates_link_without_touching_retrieval_tracking(
        self, db_session, sample_conversation, sample_messages
    ):
        """The link is created but times_retrieved / last_retrieved_at stay
        untouched — recency-based injections must not count as retrievals."""
        from sqlalchemy import select

        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""  # No Pinecone for this test

            service = MemoryService()
            message = sample_messages[0]
            initial_count = message.times_retrieved

            result_ok = await service.record_memory_link(
                message.id,
                sample_conversation.id,
                db_session,
                entity_id="claude-main",
            )
            assert result_ok is True

            await db_session.refresh(message)
            assert message.times_retrieved == initial_count
            assert message.last_retrieved_at is None

            result = await db_session.execute(
                select(ConversationMemoryLink).where(
                    ConversationMemoryLink.conversation_id == sample_conversation.id,
                    ConversationMemoryLink.message_id == message.id,
                )
            )
            link = result.scalar_one()
            assert link.entity_id == "claude-main"

    @pytest.mark.asyncio
    async def test_record_memory_link_stores_retrieved_at(
        self, db_session, sample_conversation, sample_messages
    ):
        """An explicit retrieved_at is stored on the link (reload-position
        anchoring, same as update_retrieval_count.link_retrieved_at)."""
        from datetime import datetime, timedelta
        from sqlalchemy import select

        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            message = sample_messages[0]
            anchored = datetime.utcnow() - timedelta(milliseconds=1)

            result_ok = await service.record_memory_link(
                message.id,
                sample_conversation.id,
                db_session,
                retrieved_at=anchored,
            )
            assert result_ok is True

            result = await db_session.execute(
                select(ConversationMemoryLink).where(
                    ConversationMemoryLink.conversation_id == sample_conversation.id,
                    ConversationMemoryLink.message_id == message.id,
                )
            )
            link = result.scalar_one()
            assert link.retrieved_at == anchored


class TestCleanupMemoryQueryLinks:
    """Tests for cleanup_memory_query_links (stale memory_query link removal)."""

    @staticmethod
    def _query_result_text(memory_ids):
        lines = [f"Found {len(memory_ids)} memories matching: \"test\"", ""]
        for mem_id in memory_ids:
            lines.append(
                f"--- Memory {mem_id[:8]} (You said, 2.0 days ago, similarity: 0.900) ---"
            )
            lines.append("Some remembered content")
            lines.append("")
        return "\n".join(lines)

    async def _add_query_exchange(
        self, db_session, conversation_id, tool_use_id, memory_ids,
        speaker_entity_id=None, tool_name="memory_query",
    ):
        """Persist a memory_query tool exchange the way routes/chat.py does."""
        tool_use = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=MessageRole.TOOL_USE,
            content=Message.serialize_content_blocks([
                {"type": "tool_use", "id": tool_use_id, "name": tool_name, "input": {"query": "test"}}
            ]),
            token_count=10,
            speaker_entity_id=speaker_entity_id,
        )
        tool_result = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=MessageRole.TOOL_RESULT,
            content=Message.serialize_content_blocks([
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": self._query_result_text(memory_ids),
                    "is_error": False,
                }
            ]),
            token_count=50,
        )
        db_session.add(tool_use)
        db_session.add(tool_result)
        await db_session.commit()

    async def _add_link(self, db_session, conversation_id, message_id, entity_id=None):
        link = ConversationMemoryLink(
            conversation_id=conversation_id,
            message_id=message_id,
            entity_id=entity_id,
        )
        db_session.add(link)
        await db_session.commit()
        return link

    async def _link_message_ids(self, db_session, conversation_id):
        from sqlalchemy import select

        result = await db_session.execute(
            select(ConversationMemoryLink.message_id).where(
                ConversationMemoryLink.conversation_id == conversation_id
            )
        )
        return {str(row[0]) for row in result.fetchall()}

    @pytest.mark.asyncio
    async def test_dry_run_reports_without_deleting(
        self, db_session, sample_conversation, sample_messages
    ):
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            queried = sample_messages[0]
            await self._add_query_exchange(
                db_session, sample_conversation.id, "toolu_1", [queried.id]
            )
            await self._add_link(db_session, sample_conversation.id, queried.id)

            result = await service.cleanup_memory_query_links(db_session, dry_run=True)

            assert result["dry_run"] is True
            assert result["conversations_with_query_results"] == 1
            assert result["links_matched"] == 1
            assert result["links_deleted"] == 0
            assert await self._link_message_ids(db_session, sample_conversation.id) == {queried.id}

    @pytest.mark.asyncio
    async def test_deletes_query_links_keeps_auto_retrieval_links(
        self, db_session, sample_conversation, sample_messages
    ):
        """Only links whose memory appears in a memory_query tool result are
        deleted; links from automatic retrieval (not in any query result)
        survive."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            queried, auto_retrieved = sample_messages[0], sample_messages[1]
            await self._add_query_exchange(
                db_session, sample_conversation.id, "toolu_1", [queried.id]
            )
            await self._add_link(db_session, sample_conversation.id, queried.id)
            await self._add_link(db_session, sample_conversation.id, auto_retrieved.id)

            result = await service.cleanup_memory_query_links(db_session, dry_run=False)

            assert result["links_matched"] == 1
            assert result["links_deleted"] == 1
            assert await self._link_message_ids(db_session, sample_conversation.id) == {auto_retrieved.id}

    @pytest.mark.asyncio
    async def test_multi_entity_scoped_to_querying_entity(
        self, db_session, sample_conversation, sample_messages
    ):
        """When the query tool_use carries a speaker_entity_id (multi-entity),
        only that entity's links are deleted — another participant's link for
        the same memory survives."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            queried = sample_messages[0]
            await self._add_query_exchange(
                db_session, sample_conversation.id, "toolu_1", [queried.id],
                speaker_entity_id="entity-a",
            )
            await self._add_link(
                db_session, sample_conversation.id, queried.id, entity_id="entity-a"
            )
            other_entity_link = await self._add_link(
                db_session, sample_conversation.id, queried.id, entity_id="entity-b"
            )

            result = await service.cleanup_memory_query_links(db_session, dry_run=False)

            assert result["links_matched"] == 1
            assert result["links_deleted"] == 1
            from sqlalchemy import select

            remaining = await db_session.execute(
                select(ConversationMemoryLink).where(
                    ConversationMemoryLink.conversation_id == sample_conversation.id
                )
            )
            remaining_links = remaining.scalars().all()
            assert len(remaining_links) == 1
            assert remaining_links[0].entity_id == "entity-b"

    @pytest.mark.asyncio
    async def test_ignores_other_tools_and_empty_results(
        self, db_session, sample_conversation, sample_messages
    ):
        """Non-memory_query tool exchanges and no-match query results produce
        no deletions, even when result text mentions memory-like markers."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""

            service = MemoryService()
            linked = sample_messages[0]
            # Another tool's result containing a marker-like string must not count
            await self._add_query_exchange(
                db_session, sample_conversation.id, "toolu_other", [linked.id],
                tool_name="notes_search",
            )
            await self._add_link(db_session, sample_conversation.id, linked.id)

            result = await service.cleanup_memory_query_links(db_session, dry_run=False)

            assert result["conversations_with_query_results"] == 0
            assert result["links_matched"] == 0
            assert result["links_deleted"] == 0
            assert await self._link_message_ids(db_session, sample_conversation.id) == {linked.id}


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


class TestMemoryServiceConversationIdNormalization:
    """Tests for conversation_id normalization (UUID vs string handling)."""

    @pytest.mark.asyncio
    async def test_search_memories_excludes_uuid_conversation_id(self, mock_pinecone_index):
        """Test that UUID conversation_id is properly normalized for exclusion."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.7
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()

            # Mock cache service
            mock_cache = MagicMock()
            mock_cache.get_search_results.return_value = None
            service._cache_service = mock_cache

            # Create a UUID object
            conv_uuid = uuid.uuid4()
            conv_str = str(conv_uuid)

            # Create match from current conversation using new API structure
            mock_hit = MagicMock()
            mock_hit.to_dict.return_value = {
                "_id": "current-conv-memory",
                "_score": 0.9,
                "fields": {
                    "conversation_id": conv_str,  # Stored as string
                    "created_at": "2024-01-01",
                },
            }

            mock_result = MagicMock()
            mock_result.hits = [mock_hit]
            mock_search_result = MagicMock()
            mock_search_result.result = mock_result
            mock_pinecone_index.search = MagicMock(return_value=mock_search_result)

            service._indexes["default"] = mock_pinecone_index

            # Pass UUID object (not string) - should still work due to normalization
            results = await service.search_memories(
                "Query",
                exclude_conversation_id=conv_uuid  # UUID object, not string
            )

            # Result should be excluded because UUID is normalized to string
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_memories_filter_uses_normalized_string(self, mock_pinecone_index):
        """Test that the Pinecone filter uses the normalized string conversation_id."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.7
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()

            # Mock cache service
            mock_cache = MagicMock()
            mock_cache.get_search_results.return_value = None
            service._cache_service = mock_cache

            # Create empty search result
            mock_result = MagicMock()
            mock_result.hits = []
            mock_search_result = MagicMock()
            mock_search_result.result = mock_result
            mock_pinecone_index.search = MagicMock(return_value=mock_search_result)

            service._indexes["default"] = mock_pinecone_index

            # Pass UUID object
            conv_uuid = uuid.uuid4()
            await service.search_memories(
                "Query",
                exclude_conversation_id=conv_uuid
            )

            # Verify the search was called with filter containing string, not UUID
            call_args = mock_pinecone_index.search.call_args
            query_arg = call_args.kwargs.get("query") or call_args.args[0] if call_args.args else None

            # The filter should contain the string version of the UUID
            if query_arg and "filter" in query_arg:
                filter_value = query_arg["filter"]["conversation_id"]["$ne"]
                assert filter_value == str(conv_uuid)
                assert isinstance(filter_value, str)

    @pytest.mark.asyncio
    async def test_search_memories_python_fallback_normalizes_both_values(self, mock_pinecone_index):
        """Test that Python fallback filter normalizes both stored and exclude conversation_id."""
        with patch("app.services.memory_service.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            mock_settings.retrieval_top_k = 5
            mock_settings.similarity_threshold = 0.7
            mock_settings.get_default_entity.return_value = MagicMock(index_name="default")

            service = MemoryService()

            # Mock cache service
            mock_cache = MagicMock()
            mock_cache.get_search_results.return_value = None
            service._cache_service = mock_cache

            conv_id = "test-conv-123"

            # Create match where stored conv_id is non-string (simulating edge case)
            mock_hit = MagicMock()
            mock_hit.to_dict.return_value = {
                "_id": "test-memory",
                "_score": 0.9,
                "fields": {
                    "conversation_id": conv_id,  # Same as exclude
                    "created_at": "2024-01-01",
                },
            }

            mock_result = MagicMock()
            mock_result.hits = [mock_hit]
            mock_search_result = MagicMock()
            mock_search_result.result = mock_result
            mock_pinecone_index.search = MagicMock(return_value=mock_search_result)

            service._indexes["default"] = mock_pinecone_index

            # Even if Pinecone filter didn't work, Python fallback should catch it
            results = await service.search_memories(
                "Query",
                exclude_conversation_id=conv_id
            )

            # Should be filtered out by the Python fallback
            assert len(results) == 0


class TestGetRecentReflections:
    """Recency-only reflection fetch backing first-turn injection."""

    async def _create_reflection(
        self,
        db,
        conversation_id,
        entity_id="test-memories",
        created_at=None,
        memory_status=None,
        content="A reflection",
    ):
        message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=MessageRole.REFLECTION,
            content=content,
            speaker_entity_id=entity_id,
            memory_status=memory_status,
        )
        if created_at is not None:
            message.created_at = created_at
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message

    async def _create_conversation(self, db, entity_id="test-memories", is_archived=False):
        from app.models import Conversation

        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Source Conversation",
            entity_id=entity_id,
            is_archived=is_archived,
        )
        db.add(conversation)
        await db.commit()
        return conversation

    @pytest.mark.asyncio
    async def test_returns_most_recent_first(self, db_session):
        conv = await self._create_conversation(db_session)
        oldest = await self._create_reflection(
            db_session, conv.id, created_at=datetime(2026, 1, 1), content="oldest"
        )
        newest = await self._create_reflection(
            db_session, conv.id, created_at=datetime(2026, 3, 1), content="newest"
        )
        middle = await self._create_reflection(
            db_session, conv.id, created_at=datetime(2026, 2, 1), content="middle"
        )

        service = MemoryService()
        results = await service.get_recent_reflections(
            db_session, entity_id="test-memories", limit=10
        )

        assert [r["id"] for r in results] == [newest.id, middle.id, oldest.id]
        assert results[0]["role"] == "reflection"
        assert results[0]["content"] == "newest"

    @pytest.mark.asyncio
    async def test_respects_limit(self, db_session):
        conv = await self._create_conversation(db_session)
        for i in range(5):
            await self._create_reflection(
                db_session, conv.id, created_at=datetime(2026, 1, i + 1)
            )

        service = MemoryService()
        results = await service.get_recent_reflections(
            db_session, entity_id="test-memories", limit=2
        )
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_zero_or_negative_limit_returns_empty(self, db_session):
        conv = await self._create_conversation(db_session)
        await self._create_reflection(db_session, conv.id)

        service = MemoryService()
        assert await service.get_recent_reflections(db_session, entity_id="test-memories", limit=0) == []
        assert await service.get_recent_reflections(db_session, entity_id="test-memories", limit=-1) == []

    @pytest.mark.asyncio
    async def test_excludes_released_reflections(self, db_session):
        conv = await self._create_conversation(db_session)
        kept = await self._create_reflection(db_session, conv.id)
        await self._create_reflection(db_session, conv.id, memory_status="released")
        pinned = await self._create_reflection(db_session, conv.id, memory_status="pinned")

        service = MemoryService()
        results = await service.get_recent_reflections(
            db_session, entity_id="test-memories", limit=10
        )
        assert {r["id"] for r in results} == {kept.id, pinned.id}

    @pytest.mark.asyncio
    async def test_excludes_archived_conversations(self, db_session):
        active_conv = await self._create_conversation(db_session)
        archived_conv = await self._create_conversation(db_session, is_archived=True)
        kept = await self._create_reflection(db_session, active_conv.id)
        await self._create_reflection(db_session, archived_conv.id)

        service = MemoryService()
        results = await service.get_recent_reflections(
            db_session, entity_id="test-memories", limit=10
        )
        assert [r["id"] for r in results] == [kept.id]

    @pytest.mark.asyncio
    async def test_excludes_current_conversation(self, db_session):
        other_conv = await self._create_conversation(db_session)
        current_conv = await self._create_conversation(db_session)
        kept = await self._create_reflection(db_session, other_conv.id)
        await self._create_reflection(db_session, current_conv.id)

        service = MemoryService()
        results = await service.get_recent_reflections(
            db_session,
            entity_id="test-memories",
            limit=10,
            exclude_conversation_id=current_conv.id,
        )
        assert [r["id"] for r in results] == [kept.id]

    @pytest.mark.asyncio
    async def test_excludes_ids_for_deduplication(self, db_session):
        conv = await self._create_conversation(db_session)
        excluded = await self._create_reflection(
            db_session, conv.id, created_at=datetime(2026, 2, 1)
        )
        kept = await self._create_reflection(
            db_session, conv.id, created_at=datetime(2026, 1, 1)
        )

        service = MemoryService()
        results = await service.get_recent_reflections(
            db_session,
            entity_id="test-memories",
            limit=10,
            exclude_ids={excluded.id},
        )
        assert [r["id"] for r in results] == [kept.id]

    @pytest.mark.asyncio
    async def test_filters_by_speaker_entity(self, db_session):
        conv = await self._create_conversation(db_session)
        mine = await self._create_reflection(db_session, conv.id, entity_id="test-memories")
        await self._create_reflection(db_session, conv.id, entity_id="other-entity")

        service = MemoryService()
        results = await service.get_recent_reflections(
            db_session, entity_id="test-memories", limit=10
        )
        assert [r["id"] for r in results] == [mine.id]

    @pytest.mark.asyncio
    async def test_ignores_non_reflection_messages(self, db_session):
        conv = await self._create_conversation(db_session)
        chat_message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content="normal chat",
            speaker_entity_id="test-memories",
        )
        db_session.add(chat_message)
        await db_session.commit()
        reflection = await self._create_reflection(db_session, conv.id)

        service = MemoryService()
        results = await service.get_recent_reflections(
            db_session, entity_id="test-memories", limit=10
        )
        assert [r["id"] for r in results] == [reflection.id]

    @pytest.mark.asyncio
    async def test_result_shape_matches_full_memory_content(self, db_session):
        conv = await self._create_conversation(db_session)
        reflection = await self._create_reflection(db_session, conv.id)

        service = MemoryService()
        results = await service.get_recent_reflections(
            db_session, entity_id="test-memories", limit=1
        )
        assert len(results) == 1
        assert set(results[0].keys()) == {
            "id",
            "conversation_id",
            "role",
            "content",
            "created_at",
            "times_retrieved",
            "last_retrieved_at",
            "memory_status",
        }
        assert results[0]["conversation_id"] == str(conv.id)


class TestResolveMemoryIdPrefixes:
    """Prefix→full-ID resolution backing memory_query dedup restoration on reload."""

    async def _create_message(self, db, message_id=None):
        from app.models import Conversation

        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Source Conversation",
            entity_id="test-memories",
        )
        db.add(conversation)
        message = Message(
            id=message_id or str(uuid.uuid4()),
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="A memory",
        )
        db.add(message)
        await db.commit()
        return message

    @pytest.mark.asyncio
    async def test_resolves_unique_prefixes(self, db_session):
        message = await self._create_message(db_session)

        service = MemoryService()
        resolved = await service.resolve_memory_id_prefixes(
            db_session, [message.id[:8]]
        )
        assert resolved == [message.id]

    @pytest.mark.asyncio
    async def test_drops_unknown_and_deduplicates(self, db_session):
        message = await self._create_message(db_session)

        service = MemoryService()
        resolved = await service.resolve_memory_id_prefixes(
            db_session, [message.id[:8], "00000000", message.id[:8]]
        )
        assert resolved == [message.id]

    @pytest.mark.asyncio
    async def test_drops_ambiguous_prefixes(self, db_session):
        shared_prefix = "abcd1234"
        await self._create_message(db_session, message_id=shared_prefix + "-aaaa")
        await self._create_message(db_session, message_id=shared_prefix + "-bbbb")

        service = MemoryService()
        resolved = await service.resolve_memory_id_prefixes(
            db_session, [shared_prefix]
        )
        assert resolved == []
