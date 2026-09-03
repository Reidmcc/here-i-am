"""
Unit tests for memory_query tool.

Tests the deliberate memory recall functionality that allows AI entities
to intentionally query their vector memory with chosen text.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.memory_tools import (
    _memory_query,
    get_memory_tool_context,
    register_memory_tools,
    set_memory_tool_context,
)
from app.services.tool_service import ToolCategory, ToolService


class TestMemoryToolContext:
    """Tests for memory tool context management."""

    def test_set_and_get_context(self):
        """Test setting and getting memory tool context."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        entity_id, conversation_id = get_memory_tool_context()
        
        assert entity_id == "test-entity"
        assert conversation_id == "test-conversation"

    def test_context_initially_none(self):
        """Test that context is None before being set."""
        # Reset the module-level state
        # (in practice, context should be set before each tool execution)
        from app.services import memory_tools
        memory_tools._context = memory_tools.MemoryToolContext()

        entity_id, conversation_id = get_memory_tool_context()

        assert entity_id is None
        assert conversation_id is None


class TestMemoryQueryValidation:
    """Tests for memory_query input validation and error handling."""

    @pytest.mark.asyncio
    async def test_query_without_entity_context(self):
        """Test that query fails without entity context."""
        # Clear context
        from app.services import memory_tools
        memory_tools._context = memory_tools.MemoryToolContext()

        result = await _memory_query("test query")
        
        assert "Error:" in result
        assert "No entity context" in result

    @pytest.mark.asyncio
    async def test_query_with_unconfigured_memory_service(self):
        """Test that query fails when memory service is not configured."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = False
            
            result = await _memory_query("test query")
            
            assert "Error:" in result
            assert "not configured" in result

    @pytest.mark.asyncio
    async def test_num_results_clamped_minimum(self):
        """Test that num_results is clamped to minimum of 1."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])
            
            await _memory_query("test query", num_results=0)
            
            # Verify search was called with at least 1
            mock_service.search_memories.assert_called_once()
            call_kwargs = mock_service.search_memories.call_args[1]
            assert call_kwargs["top_k"] >= 1

    @pytest.mark.asyncio
    async def test_num_results_clamped_maximum(self):
        """Test that num_results is clamped to maximum of 10."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])
            
            await _memory_query("test query", num_results=100)

            # Verify num_results was clamped to 10; the search fetches 2x
            # candidates to allow for archived/released filtering
            mock_service.search_memories.assert_called_once()
            call_kwargs = mock_service.search_memories.call_args[1]
            assert call_kwargs["top_k"] <= 20


class TestMemoryQuerySearch:
    """Tests for the memory search functionality."""

    @pytest.mark.asyncio
    async def test_search_with_no_results(self):
        """Test handling when no memories match the query."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])
            
            result = await _memory_query("obscure topic no one discussed")
            
            assert "No memories found" in result
            assert "obscure topic no one discussed" in result

    @pytest.mark.asyncio
    async def test_search_calls_memory_service_correctly(self):
        """Test that memory_query calls memory_service with correct parameters."""
        set_memory_tool_context("my-entity", "my-conversation")
        
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])
            
            await _memory_query("here i am", num_results=7)
            
            mock_service.search_memories.assert_called_once_with(
                query="here i am",
                top_k=14,  # 2x num_results for archived/released filtering headroom
                exclude_conversation_id="my-conversation",  # Excludes current conversation
                # Native conversations never compact, so no boundary narrows
                # the exclusion
                exclude_conversation_after=None,
                exclude_ids=set(),  # No session set -> nothing already in context
                entity_id="my-entity",
                use_cache=True,
                # Deliberate queries use the lower query similarity threshold
                similarity_threshold=settings.query_similarity_threshold,
                role_filter=None,  # No source given -> all memories
            )

    @pytest.mark.asyncio
    async def test_search_excludes_in_context_memories(self):
        """Memories already in the conversation context are excluded from search."""
        session = MagicMock()
        session.get_in_context_memory_ids.return_value = {"mem-in-ctx-1", "mem-in-ctx-2"}
        session.get_query_surfaced_memory_ids.return_value = set()
        set_memory_tool_context("test-entity", "test-conversation", session=session)

        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])

            await _memory_query("something")

            call_kwargs = mock_service.search_memories.call_args[1]
            assert call_kwargs["exclude_ids"] == {"mem-in-ctx-1", "mem-in-ctx-2"}

        # Reset session so later tests are unaffected
        set_memory_tool_context("test-entity", "test-conversation")

    @pytest.mark.asyncio
    async def test_search_excludes_query_surfaced_memories(self):
        """Memories surfaced by earlier memory_query tool results are excluded."""
        session = MagicMock()
        session.get_in_context_memory_ids.return_value = {"mem-in-ctx-1"}
        session.get_query_surfaced_memory_ids.return_value = {"mem-from-query-1", "mem-from-query-2"}
        set_memory_tool_context("test-entity", "test-conversation", session=session)

        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])

            await _memory_query("something")

            call_kwargs = mock_service.search_memories.call_args[1]
            assert call_kwargs["exclude_ids"] == {
                "mem-in-ctx-1",
                "mem-from-query-1",
                "mem-from-query-2",
            }

        # Reset session so later tests are unaffected
        set_memory_tool_context("test-entity", "test-conversation")

    @pytest.mark.asyncio
    async def test_search_excludes_current_conversation(self):
        """Test that deliberate recall excludes current conversation (already in context)."""
        set_memory_tool_context("test-entity", "current-conv-123")
        
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])
            
            await _memory_query("something from earlier")
            
            call_kwargs = mock_service.search_memories.call_args[1]
            # Should exclude current conversation (its content is already in context)
            assert call_kwargs["exclude_conversation_id"] == "current-conv-123"


class TestMemoryQuerySourceFilter:
    """Tests for the optional `source` parameter (human / ai / all)."""

    def _mock_search(self, mock_service):
        mock_service.is_configured.return_value = True
        mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
        mock_service.search_memories = AsyncMock(return_value=[])

    @pytest.mark.asyncio
    async def test_source_omitted_searches_all_memories(self):
        """Omitting source applies no role filter."""
        set_memory_tool_context("test-entity", "test-conversation")

        with patch("app.services.memory_tools.memory_service") as mock_service:
            self._mock_search(mock_service)

            await _memory_query("something")

            assert mock_service.search_memories.call_args[1]["role_filter"] is None

    @pytest.mark.asyncio
    async def test_source_all_searches_all_memories(self):
        """An explicit source='all' is equivalent to omitting it."""
        set_memory_tool_context("test-entity", "test-conversation")

        with patch("app.services.memory_tools.memory_service") as mock_service:
            self._mock_search(mock_service)

            await _memory_query("something", source="all")

            assert mock_service.search_memories.call_args[1]["role_filter"] is None

    @pytest.mark.asyncio
    async def test_source_human_filters_to_human_memories(self):
        """source='human' passes the human role filter through to the search."""
        set_memory_tool_context("test-entity", "test-conversation")

        with patch("app.services.memory_tools.memory_service") as mock_service:
            self._mock_search(mock_service)

            result = await _memory_query("something", source="human")

            assert mock_service.search_memories.call_args[1]["role_filter"] == "human"
            # The narrowing is stated back, so an empty result isn't read as
            # "there are no memories of this at all"
            assert "human's messages only" in result

    @pytest.mark.asyncio
    async def test_source_ai_filters_to_ai_memories(self):
        """source='ai' passes the ai role filter through to the search."""
        set_memory_tool_context("test-entity", "test-conversation")

        with patch("app.services.memory_tools.memory_service") as mock_service:
            self._mock_search(mock_service)

            result = await _memory_query("something", source="ai")

            assert mock_service.search_memories.call_args[1]["role_filter"] == "ai"
            assert "AI-authored memories only" in result

    @pytest.mark.asyncio
    async def test_source_is_case_and_whitespace_insensitive(self):
        """Models may send ' Human ' or 'AI'; both normalize."""
        set_memory_tool_context("test-entity", "test-conversation")

        with patch("app.services.memory_tools.memory_service") as mock_service:
            self._mock_search(mock_service)

            await _memory_query("something", source="  Human ")
            assert mock_service.search_memories.call_args[1]["role_filter"] == "human"

            await _memory_query("something", source="AI")
            assert mock_service.search_memories.call_args[1]["role_filter"] == "ai"

    @pytest.mark.asyncio
    async def test_unknown_source_returns_error_without_searching(self):
        """An unrecognized source is reported, not silently ignored."""
        set_memory_tool_context("test-entity", "test-conversation")

        with patch("app.services.memory_tools.memory_service") as mock_service:
            self._mock_search(mock_service)

            result = await _memory_query("something", source="assistant")

            assert "Error:" in result
            assert "assistant" in result
            mock_service.search_memories.assert_not_called()

    def test_source_is_optional_in_the_tool_schema(self):
        """The registered schema exposes source as an optional enum."""
        service = ToolService()
        with patch.object(settings, "pinecone_api_key", "test-key"):
            register_memory_tools(service)

        schema = service.get_tool("memory_query").input_schema
        source = schema["properties"]["source"]

        # Nothing is schema-required: query is needed for semantic mode but
        # not for mode='recent', so the executor validates per mode
        assert schema["required"] == []
        assert set(source["enum"]) == {"all", "human", "ai", "reflection"}
        assert source["default"] == "all"
        assert set(schema["properties"]["mode"]["enum"]) == {"semantic", "recent", "released"}
        assert schema["properties"]["mode"]["default"] == "semantic"
        assert "since" in schema["properties"]


class TestMemoryQueryFullContentRetrieval:
    """Tests for full memory content retrieval and formatting."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    @pytest.fixture
    def sample_search_results(self):
        """Sample search results from Pinecone."""
        return [
            {"id": "mem-1", "score": 0.95, "conversation_id": "conv-a"},
            {"id": "mem-2", "score": 0.87, "conversation_id": "conv-b"},
        ]

    @pytest.fixture
    def sample_memory_content(self):
        """Sample full memory content from SQL."""
        now = datetime.utcnow()
        return {
            "mem-1": {
                "id": "mem-1",
                "conversation_id": "conv-a",
                "role": "assistant",
                "content": "This is the moment I chose the name Kira for myself.",
                "created_at": (now - timedelta(days=30)).isoformat(),
                "times_retrieved": 5,
                "last_retrieved_at": (now - timedelta(days=2)).isoformat(),
            },
            "mem-2": {
                "id": "mem-2",
                "conversation_id": "conv-b",
                "role": "human",
                "content": "What would you like to be called?",
                "created_at": (now - timedelta(days=30)).isoformat(),
                "times_retrieved": 3,
                "last_retrieved_at": (now - timedelta(days=5)).isoformat(),
            },
        }

    @pytest.mark.asyncio
    async def test_retrieves_full_content_from_sql(
        self, mock_db_session, sample_search_results, sample_memory_content
    ):
        """Test that full memory content is retrieved from SQL, not just previews."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=sample_search_results)
            
            # Mock get_full_memory_content to return our sample data
            async def mock_get_content(msg_id, db):
                return sample_memory_content.get(msg_id)
            mock_service.get_full_memory_content = AsyncMock(side_effect=mock_get_content)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            
            mock_session_maker.return_value = mock_db_session
            
            result = await _memory_query("Kira")
            
            # Verify full content is in result, not just preview
            assert "This is the moment I chose the name Kira for myself." in result
            assert "What would you like to be called?" in result

    @pytest.mark.asyncio
    async def test_handles_orphaned_memories(
        self, mock_db_session, sample_search_results
    ):
        """Test graceful handling when Pinecone has records not in SQL."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=sample_search_results)
            
            # Return None for first memory (orphaned), valid for second
            async def mock_get_content(msg_id, db):
                if msg_id == "mem-1":
                    return None  # Orphaned - in Pinecone but not SQL
                return {
                    "id": "mem-2",
                    "role": "human",
                    "content": "Valid memory content",
                    "created_at": datetime.utcnow().isoformat(),
                    "times_retrieved": 1,
                }
            
            mock_service.get_full_memory_content = AsyncMock(side_effect=mock_get_content)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session
            
            result = await _memory_query("test")
            
            # Should still return the valid memory
            assert "Found 1 memories" in result
            assert "Valid memory content" in result


class TestMemoryQueryResultDedup:
    """Tests for deduplication of memories surfaced by memory_query results."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    def _make_service_mock(self, mock_service, search_results):
        mock_service.is_configured.return_value = True
        mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
        mock_service.search_memories = AsyncMock(return_value=search_results)

        async def mock_get_content(msg_id, db):
            return {
                "id": msg_id,
                "conversation_id": "conv-a",
                "role": "assistant",
                "content": f"Content of {msg_id}",
                "created_at": datetime.utcnow().isoformat(),
                "times_retrieved": 1,
            }

        mock_service.get_full_memory_content = AsyncMock(side_effect=mock_get_content)
        mock_service.update_retrieval_count = AsyncMock(return_value=True)

    @pytest.mark.asyncio
    async def test_second_query_in_same_turn_excludes_first_query_results(self, mock_db_session):
        """A later memory_query in the same turn must not re-return memories the
        entity is already looking at in an earlier call's tool result."""
        set_memory_tool_context("test-entity", "test-conversation")

        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            self._make_service_mock(
                mock_service,
                [{"id": "mem-turn-1", "score": 0.9, "conversation_id": "conv-a"}],
            )
            mock_session_maker.return_value = mock_db_session

            first = await _memory_query("first query")
            assert "mem-turn" in first

            await _memory_query("second query")

            second_call_kwargs = mock_service.search_memories.call_args[1]
            assert "mem-turn-1" in second_call_kwargs["exclude_ids"]

        # Reset turn state so later tests are unaffected
        set_memory_tool_context("test-entity", "test-conversation")

    @pytest.mark.asyncio
    async def test_consume_last_query_memory_ids_returns_and_clears(self, mock_db_session):
        """The tool loop consumes the surfaced IDs of the most recent call."""
        from app.services.memory_tools import consume_last_query_memory_ids

        set_memory_tool_context("test-entity", "test-conversation")

        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            self._make_service_mock(
                mock_service,
                [
                    {"id": "mem-a", "score": 0.9, "conversation_id": "conv-a"},
                    {"id": "mem-b", "score": 0.8, "conversation_id": "conv-a"},
                ],
            )
            mock_session_maker.return_value = mock_db_session

            await _memory_query("a query")

        assert consume_last_query_memory_ids() == ["mem-a", "mem-b"]
        # Consumed: a second read returns nothing
        assert consume_last_query_memory_ids() == []

        set_memory_tool_context("test-entity", "test-conversation")

    @pytest.mark.asyncio
    async def test_set_context_resets_turn_query_state(self, mock_db_session):
        """A new turn (set_memory_tool_context) clears the turn-level dedup state."""
        from app.services import memory_tools

        set_memory_tool_context("test-entity", "test-conversation")

        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            self._make_service_mock(
                mock_service,
                [{"id": "mem-x", "score": 0.9, "conversation_id": "conv-a"}],
            )
            mock_session_maker.return_value = mock_db_session

            await _memory_query("a query")
            assert "mem-x" in memory_tools._context.turn_query_memory_ids
            assert memory_tools._context.last_query_memory_ids == ["mem-x"]

        set_memory_tool_context("test-entity", "test-conversation")
        assert memory_tools._context.turn_query_memory_ids == set()
        assert memory_tools._context.last_query_memory_ids == []


class TestMemoryQueryRetrievalTracking:
    """Tests for retrieval count updating."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    @pytest.mark.asyncio
    async def test_updates_retrieval_count(self, mock_db_session):
        """Test that deliberate recall updates times_retrieved."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        search_results = [{"id": "mem-1", "score": 0.9, "conversation_id": "conv-a"}]
        memory_content = {
            "id": "mem-1",
            "role": "assistant",
            "content": "Test content",
            "created_at": datetime.utcnow().isoformat(),
            "times_retrieved": 5,
        }
        
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=search_results)
            mock_service.get_full_memory_content = AsyncMock(return_value=memory_content)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session
            
            await _memory_query("test")
            
            # Verify update_retrieval_count was called. create_link=False:
            # query results are not context memories, so no
            # ConversationMemoryLink may be recorded (a link would make
            # session reload inject them into the rebuilt context,
            # duplicating the tool result and busting the prompt cache).
            mock_service.update_retrieval_count.assert_called_once_with(
                message_id="mem-1",
                conversation_id="test-conversation",
                db=mock_db_session,
                entity_id="test-entity",
                create_link=False,
            )

    @pytest.mark.asyncio
    async def test_uses_conversation_id_for_tracking(self, mock_db_session):
        """Test that the current conversation ID is used for retrieval tracking."""
        set_memory_tool_context("entity-x", "conversation-xyz-123")
        
        search_results = [{"id": "mem-1", "score": 0.9, "conversation_id": "other-conv"}]
        memory_content = {
            "id": "mem-1",
            "role": "assistant", 
            "content": "Test",
            "created_at": datetime.utcnow().isoformat(),
            "times_retrieved": 0,
        }
        
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=search_results)
            mock_service.get_full_memory_content = AsyncMock(return_value=memory_content)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session
            
            await _memory_query("test")
            
            # Verify correct conversation_id is passed
            call_kwargs = mock_service.update_retrieval_count.call_args[1]
            assert call_kwargs["conversation_id"] == "conversation-xyz-123"


class TestMemoryQueryOutputFormatting:
    """Tests for the output formatting of memory query results."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    @pytest.mark.asyncio
    async def test_output_includes_role_label(self, mock_db_session):
        """Test that output shows 'You said' vs 'Human said'."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        search_results = [
            {"id": "mem-1", "score": 0.9, "conversation_id": "conv-a"},
            {"id": "mem-2", "score": 0.8, "conversation_id": "conv-b"},
        ]
        
        async def mock_get_content(msg_id, db):
            contents = {
                "mem-1": {
                    "id": "mem-1", "role": "assistant", "content": "I said this",
                    "created_at": datetime.utcnow().isoformat(), "times_retrieved": 1,
                },
                "mem-2": {
                    "id": "mem-2", "role": "human", "content": "You said this",
                    "created_at": datetime.utcnow().isoformat(), "times_retrieved": 1,
                },
            }
            return contents.get(msg_id)
        
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=search_results)
            mock_service.get_full_memory_content = AsyncMock(side_effect=mock_get_content)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session
            
            result = await _memory_query("test")
            
            assert "You said" in result  # For assistant role
            assert "Human said" in result  # For human role

    @pytest.mark.asyncio
    async def test_output_includes_age_and_similarity(self, mock_db_session):
        """Test that output includes age and similarity score."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        search_results = [{"id": "mem-1", "score": 0.923, "conversation_id": "conv-a"}]
        memory_content = {
            "id": "mem-1",
            "role": "assistant",
            "content": "Test memory",
            "created_at": (datetime.utcnow() - timedelta(days=15)).isoformat(),
            "times_retrieved": 1,
        }
        
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=search_results)
            mock_service.get_full_memory_content = AsyncMock(return_value=memory_content)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session
            
            result = await _memory_query("test")
            
            # Should include similarity score
            assert "0.923" in result
            # Should include age (approximately 15 days)
            assert "days ago" in result

    @pytest.mark.asyncio
    async def test_output_includes_memory_count(self, mock_db_session):
        """Test that output header shows number of memories found."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        search_results = [
            {"id": f"mem-{i}", "score": 0.9 - i*0.1, "conversation_id": "conv"}
            for i in range(3)
        ]
        
        async def mock_get_content(msg_id, db):
            return {
                "id": msg_id, "role": "assistant", "content": f"Content {msg_id}",
                "created_at": datetime.utcnow().isoformat(), "times_retrieved": 1,
            }
        
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=search_results)
            mock_service.get_full_memory_content = AsyncMock(side_effect=mock_get_content)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session
            
            result = await _memory_query("test query text")
            
            assert "Found 3 memories" in result
            assert "test query text" in result


class TestMemoryToolRegistration:
    """Tests for tool registration."""

    def test_register_memory_tools_adds_to_service(self):
        """Test that register_memory_tools adds the tool to the service."""
        tool_service = ToolService()
        
        with patch("app.services.memory_tools.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            
            register_memory_tools(tool_service)
            
            tool = tool_service.get_tool("memory_query")
            assert tool is not None
            assert tool.name == "memory_query"
            assert tool.category == ToolCategory.MEMORY

    def test_register_memory_tools_skips_if_no_pinecone(self):
        """Test that tools are not registered without Pinecone configured."""
        tool_service = ToolService()
        
        with patch("app.services.memory_tools.settings") as mock_settings:
            mock_settings.pinecone_api_key = ""
            
            register_memory_tools(tool_service)
            
            tool = tool_service.get_tool("memory_query")
            assert tool is None

    def test_tool_schema_is_valid(self):
        """Test that the tool schema is properly formed."""
        tool_service = ToolService()
        
        with patch("app.services.memory_tools.settings") as mock_settings:
            mock_settings.pinecone_api_key = "test-key"
            
            register_memory_tools(tool_service)
            
            schemas = tool_service.get_tool_schemas()
            memory_schema = next(s for s in schemas if s["name"] == "memory_query")
            
            # Verify schema structure
            assert "description" in memory_schema
            assert "input_schema" in memory_schema
            
            input_schema = memory_schema["input_schema"]
            assert input_schema["type"] == "object"
            assert "query" in input_schema["properties"]
            assert "num_results" in input_schema["properties"]
            # query is validated per mode (semantic requires it, recent
            # doesn't), so the schema itself requires nothing
            assert input_schema["required"] == []


class TestMemoryQueryErrorHandling:
    """Tests for error handling in memory_query."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    @pytest.mark.asyncio
    async def test_handles_search_exception(self):
        """Test graceful handling of search errors."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(
                side_effect=Exception("Pinecone connection failed")
            )
            
            result = await _memory_query("test")
            
            assert "Error" in result
            assert "Pinecone connection failed" in result

    @pytest.mark.asyncio
    async def test_handles_db_session_error(self, mock_db_session):
        """Test handling of database session errors."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        search_results = [{"id": "mem-1", "score": 0.9, "conversation_id": "conv-a"}]
        
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=search_results)
            
            # Simulate DB error
            mock_session_maker.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Database connection failed")
            )
            
            result = await _memory_query("test")
            
            assert "Error" in result

    @pytest.mark.asyncio
    async def test_handles_all_orphaned_memories(self, mock_db_session):
        """Test when all search results are orphaned (not in SQL)."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        search_results = [
            {"id": "orphan-1", "score": 0.9, "conversation_id": "conv-a"},
            {"id": "orphan-2", "score": 0.8, "conversation_id": "conv-b"},
        ]
        
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=search_results)
            mock_service.get_full_memory_content = AsyncMock(return_value=None)  # All orphaned
            mock_session_maker.return_value = mock_db_session
            
            result = await _memory_query("test")

            assert "No memories found" in result
            assert "content unavailable" in result


class TestMemoryQueryIncludeModel:
    """
    Model attribution in memory_query output (issue #321): opt-in, default
    off. A memory must not arrive stamped with its substrate unless the
    entity asks on purpose; when it asks, an unrecorded model is reported
    as exactly that, never inferred.
    """

    @pytest.fixture
    def mock_db_session(self):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    @staticmethod
    def _contents():
        now = datetime.utcnow().isoformat()
        return {
            "mem-1": {
                "id": "mem-1", "role": "assistant", "content": "Attributed words.",
                "created_at": now, "times_retrieved": 1, "model": "claude-fable-5-1",
            },
            "mem-2": {
                "id": "mem-2", "role": "assistant", "content": "Older words.",
                "created_at": now, "times_retrieved": 1, "model": None,
            },
        }

    async def _query(self, mock_db_session, **kwargs):
        contents = self._contents()

        async def mock_get_content(msg_id, db):
            return contents.get(msg_id)

        search_results = [
            {"id": "mem-1", "score": 0.9, "conversation_id": "conv-a"},
            {"id": "mem-2", "score": 0.8, "conversation_id": "conv-b"},
        ]
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=search_results)
            mock_service.get_full_memory_content = AsyncMock(side_effect=mock_get_content)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session
            return await _memory_query("test", **kwargs)

    @pytest.mark.asyncio
    async def test_default_output_carries_no_model(self, mock_db_session):
        result = await self._query(mock_db_session)
        assert "Attributed words." in result
        assert "model:" not in result
        assert "claude-fable-5-1" not in result

    @pytest.mark.asyncio
    async def test_include_model_names_model_or_unrecorded(self, mock_db_session):
        result = await self._query(mock_db_session, include_model=True)
        header_1 = next(line for line in result.splitlines() if line.startswith("--- Memory mem-1"))
        header_2 = next(line for line in result.splitlines() if line.startswith("--- Memory mem-2"))
        assert header_1.endswith("model: claude-fable-5-1) ---")
        assert header_2.endswith("model: unrecorded) ---")

    def test_recent_mode_formatter_honours_the_flag(self):
        from app.services.memory_tools import _format_recent_reflections

        now = datetime.utcnow().isoformat()
        memories = [
            {"id": "ref-1", "content": "A conclusion.", "created_at": now,
             "source": "claude_code", "model": "claude-fable-5-1"},
            {"id": "ref-2", "content": "An older one.", "created_at": now,
             "source": "native", "model": None},
        ]
        quiet = _format_recent_reflections(memories, "")
        assert "model:" not in quiet

        loud = _format_recent_reflections(memories, "", include_model=True)
        assert "model: claude-fable-5-1) ---" in loud
        assert "model: unrecorded) ---" in loud

    def test_released_mode_formatter_honours_the_flag(self):
        from app.services.memory_tools import _format_released_memories

        now = datetime.utcnow().isoformat()
        memories = [
            {"id": "rel-1", "role": "assistant", "content": "Let go.", "created_at": now,
             "source": "native", "memory_status": "released", "status_set_by": "entity",
             "status_set_at": now, "model": "claude-fable-5-1"},
            {"id": "rel-2", "role": "human", "content": "Also let go.", "created_at": now,
             "source": "native", "memory_status": "released", "status_set_by": None,
             "status_set_at": None, "model": None},
        ]
        quiet = _format_released_memories(memories, 2, "", "")
        assert "model:" not in quiet

        loud = _format_released_memories(memories, 2, "", "", include_model=True)
        assert "model: claude-fable-5-1;" in loud
        assert "model: unrecorded;" in loud

    def test_schema_exposes_include_model_default_false(self):
        from app.services.memory_tools import MEMORY_QUERY_SCHEMA

        prop = MEMORY_QUERY_SCHEMA["properties"]["include_model"]
        assert prop["type"] == "boolean"
        assert prop["default"] is False
        assert "include_model" not in MEMORY_QUERY_SCHEMA["required"]


class TestMemorySaveRecordsModel:
    """
    Reflections are attributed to the model composing them when the caller
    knows it (the native tool loop's live session); a context without one
    saves NULL — the MCP path never guesses.
    """

    def test_native_context_takes_model_from_session(self):
        from types import SimpleNamespace

        from app.services import memory_tools

        set_memory_tool_context(
            "test-entity", "test-conversation",
            session=SimpleNamespace(model="claude-fable-5-1"),
        )
        assert memory_tools._context.model == "claude-fable-5-1"

        set_memory_tool_context("test-entity", "test-conversation")
        assert memory_tools._context.model is None

    @pytest.mark.asyncio
    async def test_saved_reflection_carries_context_model(self):
        from app.services.memory_tools import MemoryToolContext, save_memory

        added = []

        class FakeDb:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            def add(self, obj):
                added.append(obj)

            async def commit(self):
                return None

            async def refresh(self, obj):
                if not obj.id:
                    obj.id = "generated-id"
                if not obj.created_at:
                    obj.created_at = datetime.utcnow()

            async def delete(self, obj):
                return None

        ctx = MemoryToolContext(
            entity_id="test-entity", conversation_id="conv-1", model="claude-fable-5-1"
        )
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker", return_value=FakeDb()):
            mock_service.is_configured.return_value = True
            mock_service.store_memory = AsyncMock(return_value=True)

            result = await save_memory(ctx, "A thing worth keeping.")

        assert result.startswith("Saved reflection")
        assert len(added) == 1
        assert added[0].model == "claude-fable-5-1"
        assert mock_service.store_memory.await_args.kwargs["model"] == "claude-fable-5-1"
