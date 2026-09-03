"""
Tests for memory_query's recent mode (reflections by creation time), the
`since` bound, and the source='reflection' semantic filter.

Recent mode is the catch-up channel for long-running / concurrent sessions:
reflections saved by sibling sessions after this one began aren't in its
context, so the entity pulls them by recency instead of guessing search
text. Design rules under test:
- recency pulls never touch retrieval tracking (significance feedback is
  reserved for semantic recall, matching the recency-injection rule);
- results are still stamped/linked for dedup so nothing re-surfaces;
- validation errors are reported rather than silently widened.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.memory_tools import (
    MemoryToolContext,
    _memory_query,
    query_memories,
    set_memory_tool_context,
)


@pytest.fixture
def mock_db_session():
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    return mock_session


@pytest.fixture
def sample_reflections():
    now = datetime.utcnow()
    return [
        {
            "id": "refl-new-1234",
            "conversation_id": "conv-sibling",
            "role": "reflection",
            "content": "Concurrency ruling refined tonight.",
            "created_at": (now - timedelta(hours=2)).isoformat(),
            "times_retrieved": 0,
            "last_retrieved_at": None,
            "memory_status": None,
            "source": "claude_code",
        },
        {
            "id": "refl-old-5678",
            "conversation_id": "conv-older",
            "role": "reflection",
            "content": "The porch register held.",
            "created_at": (now - timedelta(days=2)).isoformat(),
            "times_retrieved": 3,
            "last_retrieved_at": None,
            "memory_status": "pinned",
            "source": "native",
        },
    ]


class TestRecentModeValidation:
    @pytest.mark.asyncio
    async def test_unknown_mode_rejected(self):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            result = await _memory_query("q", mode="bogus")
        assert "Error" in result
        assert "Unknown mode" in result

    @pytest.mark.asyncio
    async def test_semantic_mode_requires_query_text(self):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            result = await _memory_query("")
        assert "Error" in result
        assert "required for semantic" in result

    @pytest.mark.asyncio
    async def test_since_rejected_in_semantic_mode(self):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            result = await _memory_query("q", since="2026-08-24")
        assert "Error" in result
        assert "modes 'recent' and 'released' only" in result

    @pytest.mark.asyncio
    async def test_unparseable_since_rejected(self):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            result = await _memory_query(mode="recent", since="not a date")
        assert "Error" in result
        assert "Could not parse" in result

    @pytest.mark.asyncio
    async def test_recent_mode_rejects_non_reflection_source(self):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            result = await _memory_query(mode="recent", source="human")
        assert "Error" in result
        assert "reflections only" in result


class TestRecentModeBehavior:
    @pytest.mark.asyncio
    async def test_returns_reflections_without_tracking(
        self, mock_db_session, sample_reflections
    ):
        """Recent mode fetches by recency, formats without similarity scores,
        and never touches retrieval tracking (recency is not relevance)."""
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_recent_reflections = AsyncMock(return_value=sample_reflections)
            mock_session_maker.return_value = mock_db_session

            result = await _memory_query(mode="recent", num_results=5)

            call_kwargs = mock_service.get_recent_reflections.call_args[1]
            assert call_kwargs["entity_id"] == "test-entity"
            assert call_kwargs["limit"] == 5
            assert call_kwargs["exclude_conversation_id"] == "test-conversation"
            assert call_kwargs["since"] is None
            mock_service.update_retrieval_count.assert_not_called()
            mock_service.record_memory_link.assert_not_called()  # native: no links

        assert "Concurrency ruling refined tonight." in result
        assert "The porch register held." in result
        assert "Memory refl-new" in result
        assert "You reflected" in result
        assert "via Claude Code" in result
        assert "pinned" in result
        assert "similarity" not in result

    @pytest.mark.asyncio
    async def test_accepts_reflection_source_and_since(
        self, mock_db_session, sample_reflections
    ):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_recent_reflections = AsyncMock(
                return_value=sample_reflections[:1]
            )
            mock_session_maker.return_value = mock_db_session

            result = await _memory_query(
                mode="recent", source="reflection", since="2026-08-24T18:00:00"
            )

            call_kwargs = mock_service.get_recent_reflections.call_args[1]
            assert call_kwargs["since"] == datetime(2026, 8, 24, 18, 0, 0)
        assert "created after 2026-08-24T18:00:00 UTC" in result

    @pytest.mark.asyncio
    async def test_aware_since_converted_to_naive_utc(self, mock_db_session):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_recent_reflections = AsyncMock(return_value=[])
            mock_session_maker.return_value = mock_db_session

            await _memory_query(mode="recent", since="2026-08-24T18:00:00-04:00")

            call_kwargs = mock_service.get_recent_reflections.call_args[1]
            assert call_kwargs["since"] == datetime(2026, 8, 24, 22, 0, 0)

    @pytest.mark.asyncio
    async def test_excludes_in_context_memories(self, mock_db_session):
        session = MagicMock()
        session.get_in_context_memory_ids.return_value = {"mem-in-ctx"}
        session.get_query_surfaced_memory_ids.return_value = {"mem-queried"}
        set_memory_tool_context("test-entity", "test-conversation", session=session)
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_recent_reflections = AsyncMock(return_value=[])
            mock_session_maker.return_value = mock_db_session

            result = await _memory_query(mode="recent")

            call_kwargs = mock_service.get_recent_reflections.call_args[1]
            assert call_kwargs["exclude_ids"] == {"mem-in-ctx", "mem-queried"}
        assert "No reflections found" in result
        set_memory_tool_context("test-entity", "test-conversation")

    @pytest.mark.asyncio
    async def test_links_results_in_claude_code_context(
        self, mock_db_session, sample_reflections
    ):
        """With link_query_results (Claude Code), recent results are linked as
        the dedup record — still without touching retrieval tracking."""
        ctx = MemoryToolContext(
            entity_id="test-entity",
            conversation_id="cc-conversation",
            link_query_results=True,
        )
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_recent_reflections = AsyncMock(return_value=sample_reflections)
            mock_service.record_memory_link = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session

            await query_memories(ctx, mode="recent")

            linked_ids = [
                call.kwargs["message_id"]
                for call in mock_service.record_memory_link.call_args_list
            ]
            assert linked_ids == ["refl-new-1234", "refl-old-5678"]
            mock_service.update_retrieval_count.assert_not_called()

        # Results are stamped for turn-level and context-level dedup
        assert ctx.last_query_memory_ids == ["refl-new-1234", "refl-old-5678"]
        assert ctx.turn_query_memory_ids == {"refl-new-1234", "refl-old-5678"}


class TestReflectionSourceFilter:
    """source='reflection' narrows the semantic search to saved reflections."""

    @pytest.mark.asyncio
    async def test_reflection_source_passes_role_filter(self):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])

            result = await _memory_query("what did I conclude", source="reflection")

            assert mock_service.search_memories.call_args[1]["role_filter"] == "reflection"
        assert "your saved reflections only" in result


class TestCompactionBoundaryInQueries:
    """
    A compacted Claude Code conversation's MemoryToolContext carries
    exclude_conversation_after (its last_compacted_at); both query modes must
    thread it through so the conversation's own pre-compaction memories are
    eligible again.
    """

    BOUNDARY = datetime(2026, 8, 25, 12, 0, 0)

    def _cc_ctx(self):
        return MemoryToolContext(
            entity_id="test-entity",
            conversation_id="cc-conversation",
            link_query_results=True,
            exclude_conversation_after=self.BOUNDARY,
        )

    @pytest.mark.asyncio
    async def test_semantic_mode_passes_the_boundary_to_search(self):
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])

            await query_memories(self._cc_ctx(), "the pre-compaction work")

            call_kwargs = mock_service.search_memories.call_args[1]
            assert call_kwargs["exclude_conversation_id"] == "cc-conversation"
            assert call_kwargs["exclude_conversation_after"] == self.BOUNDARY

    @pytest.mark.asyncio
    async def test_recent_mode_passes_the_boundary(self, mock_db_session):
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_recent_reflections = AsyncMock(return_value=[])
            mock_session_maker.return_value = mock_db_session

            result = await query_memories(self._cc_ctx(), mode="recent")

            call_kwargs = mock_service.get_recent_reflections.call_args[1]
            assert call_kwargs["exclude_conversation_id"] == "cc-conversation"
            assert call_kwargs["exclude_conversation_after"] == self.BOUNDARY
        # The empty-result phrasing reflects that only the post-compaction
        # slice of this conversation is off limits
        assert "since its last compaction" in result

    @pytest.mark.asyncio
    async def test_native_context_leaves_the_boundary_unset(self):
        """Native conversations never compact: the module-level context built
        by set_memory_tool_context must carry no boundary."""
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=[])

            await _memory_query("anything")

            assert (
                mock_service.search_memories.call_args[1]["exclude_conversation_after"]
                is None
            )
