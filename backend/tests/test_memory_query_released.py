"""
Tests for memory_query's released mode at the tool layer (service mocked).

Released mode is the entity's review channel for what it withdrew from
retrieval: a release is reversible in principle (memory_release undo=true)
but was irreversible in practice while the entity could no longer see what
it released. Rules under test here: argument handling (query text not
needed, `since` and `source` accepted), what reaches the service, the
recent-mode dedup/linking discipline, and that nothing here touches
retrieval tracking. The SQL selection itself is covered against a real
database in test_memory_status_provenance.py.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

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
def sample_released():
    now = datetime.utcnow()
    return [
        {
            "id": "rel-entity-1234",
            "conversation_id": "conv-a",
            "role": "assistant",
            "content": "Something I chose to let go of.",
            "created_at": (now - timedelta(days=30)).isoformat(),
            "times_retrieved": 4,
            "last_retrieved_at": None,
            "memory_status": "released",
            "status_set_by": "entity",
            "status_set_at": (now - timedelta(days=2)).isoformat(),
            "source": "claude_code",
        },
        {
            "id": "rel-research-56",
            "conversation_id": "conv-b",
            "role": "human",
            "content": "Something the researcher withdrew.",
            "created_at": (now - timedelta(days=5)).isoformat(),
            "times_retrieved": 0,
            "last_retrieved_at": None,
            "memory_status": "released",
            "status_set_by": "researcher",
            "status_set_at": (now - timedelta(hours=1)).isoformat(),
            "source": "native",
        },
        {
            "id": "rel-legacy-7890",
            "conversation_id": "conv-c",
            "role": "reflection",
            "content": "Released before anyone wrote down who did it.",
            "created_at": (now - timedelta(days=90)).isoformat(),
            "times_retrieved": 1,
            "last_retrieved_at": None,
            "memory_status": "released",
            "status_set_by": None,
            "status_set_at": None,
            "source": "native",
        },
    ]


class TestReleasedModeArguments:
    @pytest.mark.asyncio
    async def test_needs_no_query_text_and_accepts_since_and_source(
        self, mock_db_session, sample_released
    ):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_released_memories = AsyncMock(return_value=sample_released)
            mock_service.count_released_memories = AsyncMock(return_value=7)
            mock_session_maker.return_value = mock_db_session

            result = await _memory_query(
                mode="released", num_results=3, source="ai", since="2026-08-24T18:00:00Z"
            )

            kwargs = mock_service.get_released_memories.call_args[1]
            assert kwargs["entity_id"] == "test-entity"
            assert kwargs["limit"] == 3
            assert kwargs["exclude_conversation_id"] == "test-conversation"
            assert kwargs["role_filter"] == "ai"
            assert kwargs["since"] == datetime(2026, 8, 24, 18, 0, 0)
            assert kwargs["exclude_conversation_after"] is None
            count_kwargs = mock_service.count_released_memories.call_args[1]
            assert count_kwargs["role_filter"] == "ai"
            # Curation, not recall
            mock_service.update_retrieval_count.assert_not_called()
            mock_service.search_memories.assert_not_called()
            mock_service.record_memory_link.assert_not_called()  # native: no links

        assert "(AI-authored memories only)" in result
        assert "(released after 2026-08-24T18:00:00 UTC)" in result
        assert "3 shown of 7 released in total" in result

    @pytest.mark.asyncio
    async def test_unparseable_since_rejected(self):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            result = await _memory_query(mode="released", since="last tuesday")
        assert result.startswith("Error")
        assert "since" in result

    @pytest.mark.asyncio
    async def test_semantic_mode_error_names_both_query_free_modes(self):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service:
            mock_service.is_configured.return_value = True
            result = await _memory_query(query="")
        assert "mode 'recent' or 'released'" in result


class TestReleasedModeOutput:
    @pytest.mark.asyncio
    async def test_says_who_released_each_and_when(self, mock_db_session, sample_released):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_released_memories = AsyncMock(return_value=sample_released)
            mock_service.count_released_memories = AsyncMock(return_value=3)
            mock_session_maker.return_value = mock_db_session

            result = await _memory_query(mode="released")

        assert "--- Memory rel-enti (You said, 30.0 days ago, via Claude Code; released by you 2.0 days ago) ---" in result
        assert "(Human said, 5.0 days ago, via Here I Am; released by the researcher today)" in result
        assert "(You reflected, 90.0 days ago, via Here I Am; released before release provenance was recorded)" in result
        assert "Something I chose to let go of." in result
        assert result.rstrip().endswith("Restore any of these with memory_release(memory_id, undo=true).")
        assert "similarity" not in result

    @pytest.mark.asyncio
    async def test_empty_results_distinguish_none_from_all_in_view(self, mock_db_session):
        set_memory_tool_context("test-entity", "test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_released_memories = AsyncMock(return_value=[])
            mock_session_maker.return_value = mock_db_session

            mock_service.count_released_memories = AsyncMock(return_value=0)
            none = await _memory_query(mode="released")
            mock_service.count_released_memories = AsyncMock(return_value=4)
            all_in_view = await _memory_query(mode="released", source="reflection")

        assert none == "You have no released memories."
        assert all_in_view == (
            "No released memories found (your saved reflections only) that are not "
            "already in view (4 released in total)."
        )


class TestReleasedModeDedup:
    @pytest.mark.asyncio
    async def test_excludes_in_context_and_stamps_results(self, mock_db_session, sample_released):
        ctx = MemoryToolContext(
            entity_id="test-entity",
            conversation_id="test-conversation",
            extra_exclude_ids={"already-linked"},
            turn_query_memory_ids={"earlier-this-turn"},
        )
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_released_memories = AsyncMock(return_value=sample_released)
            mock_service.count_released_memories = AsyncMock(return_value=3)
            mock_session_maker.return_value = mock_db_session

            await query_memories(ctx, mode="released")

            assert mock_service.get_released_memories.call_args[1]["exclude_ids"] == {
                "already-linked", "earlier-this-turn"
            }

        assert ctx.last_query_memory_ids == ["rel-entity-1234", "rel-research-56", "rel-legacy-7890"]
        assert ctx.turn_query_memory_ids == {
            "earlier-this-turn", "rel-entity-1234", "rel-research-56", "rel-legacy-7890"
        }

    @pytest.mark.asyncio
    async def test_links_results_in_claude_code_context(self, mock_db_session, sample_released):
        """In Claude Code conversations the results are linked as the dedup
        record (also what keeps a memory restored after review from
        re-surfacing there) — still without retrieval tracking."""
        ctx = MemoryToolContext(
            entity_id="test-entity",
            conversation_id="cc-conversation",
            link_query_results=True,
            exclude_conversation_after=datetime(2026, 9, 1),
        )
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_session_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_released_memories = AsyncMock(return_value=sample_released)
            mock_service.count_released_memories = AsyncMock(return_value=3)
            mock_service.record_memory_link = AsyncMock(return_value=True)
            mock_session_maker.return_value = mock_db_session

            await query_memories(ctx, mode="released")

            assert mock_service.get_released_memories.call_args[1][
                "exclude_conversation_after"
            ] == datetime(2026, 9, 1)
            linked = [c.kwargs["message_id"] for c in mock_service.record_memory_link.call_args_list]
            assert linked == ["rel-entity-1234", "rel-research-56", "rel-legacy-7890"]
            mock_service.update_retrieval_count.assert_not_called()
