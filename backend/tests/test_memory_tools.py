"""
Unit tests for memory_query tool.

Tests the deliberate memory recall functionality that allows AI entities
to intentionally query their vector memory with chosen text.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
import uuid

from app.services.memory_tools import (
    register_memory_tools,
    set_memory_tool_context,
    get_memory_tool_context,
    _memory_query,
)
from app.services.tool_service import ToolService, ToolCategory


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
        # Reset the module-level state by importing fresh
        # (in practice, context should be set before each tool execution)
        from app.services import memory_tools
        memory_tools._current_entity_id = None
        memory_tools._current_conversation_id = None
        
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
        memory_tools._current_entity_id = None
        memory_tools._current_conversation_id = None
        
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
            mock_service.search_memories = AsyncMock(return_value=[])
            
            await _memory_query("test query", num_results=100)
            
            # Verify search was called with at most 10
            mock_service.search_memories.assert_called_once()
            call_kwargs = mock_service.search_memories.call_args[1]
            assert call_kwargs["top_k"] <= 10


class TestMemoryQuerySearch:
    """Tests for the memory search functionality."""

    @pytest.mark.asyncio
    async def test_search_with_no_results(self):
        """Test handling when no memories match the query."""
        set_memory_tool_context("test-entity", "test-conversation")
        
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
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
            mock_service.search_memories = AsyncMock(return_value=[])
            
            await _memory_query("here i am", num_results=7)
            
            mock_service.search_memories.assert_called_once_with(
                query="here i am",
                top_k=7,
                exclude_conversation_id=None,  # Deliberate recall includes all
                exclude_ids=None,  # Deliberate recall includes all
                entity_id="my-entity",
                use_cache=True,
            )

    @pytest.mark.asyncio
    async def test_search_does_not_exclude_current_conversation(self):
        """Test that deliberate recall can surface memories from current conversation."""
        set_memory_tool_context("test-entity", "current-conv-123")
        
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.search_memories = AsyncMock(return_value=[])
            
            await _memory_query("something from earlier")
            
            call_kwargs = mock_service.search_memories.call_args[1]
            # Should NOT exclude current conversation
            assert call_kwargs["exclude_conversation_id"] is None


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
            mock_service.search_memories = AsyncMock(return_value=search_results)
            mock_service.get_full_memory_content = AsyncMock(return_value=memory_content)
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session
            
            await _memory_query("test")
            
            # Verify update_retrieval_count was called
            mock_service.update_retrieval_count.assert_called_once_with(
                message_id="mem-1",
                conversation_id="test-conversation",
                db=mock_db_session,
                entity_id="test-entity",
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
            assert "query" in input_schema["required"]


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
            mock_service.search_memories = AsyncMock(return_value=search_results)
            mock_service.get_full_memory_content = AsyncMock(return_value=None)  # All orphaned
            mock_session_maker.return_value = mock_db_session
            
            result = await _memory_query("test")
            
            assert "No memories found" in result
            assert "content unavailable" in result
