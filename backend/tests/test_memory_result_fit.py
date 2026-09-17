"""
The list-shaped memory tools fit their result to the harness's tool-result
line (harness_limits.TOOL_RESULT_BUDGET_BYTES): memory_query in every mode
and memory_neighbors render whole memories while the result lands in
context, and the rest by header only (RESULT_SIZE_POINTER), never cutting
one. memory_read and memory_find page by the same budget already
(test_memory_read); these are the tools that return a fixed list.

The case is rare — ten long messages — but a result over the line is
persisted to a file behind a 2 KB preview that cuts mid-memory and costs
Read calls to get back (issue #353's shape). The ids stay in the result
either way, so a header-only memory can be opened with memory_neighbors or
memory_read, and it counts as surfaced for dedup exactly as a summary line
in a spilled retrieval block does.
"""
# ruff: noqa: F811 — the reader tests' fixtures are imported below and then
# named as test parameters, which ruff reads as redefinition.
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models import MessageRole
from app.services.harness_limits import TOOL_RESULT_BUDGET_BYTES, utf8_size
from app.services.memory_tools import (
    RESULT_SIZE_POINTER,
    MemoryToolContext,
    neighbor_memories,
    query_memories,
)
from tests.test_memory_read import (  # noqa: F401
    ENTITY,
    async_client,
    at,
    db,
    entities_configured,
    ids_in_order,
    make_conversation,
    make_message,
    native_ctx,
    session_factory,
    test_engine,
    tools_db,
)


def _mock_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


def big_memories(count: int, size: int, role: str = "assistant") -> dict:
    now = datetime.utcnow()
    full = {}
    for index in range(count):
        mem_id = f"{index:08d}-mem"
        full[mem_id] = {
            "id": mem_id,
            "conversation_id": "conv-elsewhere",
            "role": role,
            "content": f"BODY-{index} " + ("w" * size),
            "created_at": (now - timedelta(days=index + 1)).isoformat(),
            "times_retrieved": 0,
            "last_retrieved_at": None,
            "memory_status": None,
            "source": "native",
        }
    return full


class TestMemoryQueryFit:
    async def _query(self, full: dict, **kwargs) -> tuple:
        ctx = MemoryToolContext(entity_id="test-entity", conversation_id="test-conversation")
        hits = [
            {"id": mem_id, "score": 0.9 - 0.001 * i, "conversation_id": "conv-elsewhere"}
            for i, mem_id in enumerate(full)
        ]
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_service.search_memories = AsyncMock(return_value=hits)
            mock_service.get_full_memory_content = AsyncMock(
                side_effect=lambda mem_id, db: full.get(mem_id)
            )
            mock_service.update_retrieval_count = AsyncMock(return_value=True)
            mock_maker.return_value = _mock_session()
            result = await query_memories(ctx, "anything", num_results=10, **kwargs)
        return result, ctx

    @pytest.mark.asyncio
    async def test_small_results_are_untouched(self):
        result, _ = await self._query(big_memories(3, 500))
        assert RESULT_SIZE_POINTER not in result
        assert "listed by header only" not in result
        assert result.count("BODY-") == 3

    @pytest.mark.asyncio
    async def test_oversized_result_keeps_leading_memories_whole_and_lists_the_rest(self):
        full = big_memories(10, 6000)  # ~60 KB of content, over the line
        result, ctx = await self._query(full)
        assert utf8_size(result) <= TOOL_RESULT_BUDGET_BYTES
        # Every memory keeps its header (and so its id), in rank order
        assert ids_in_order(result) == [mem_id[:8] for mem_id in full]
        shown = [index for index in range(10) if f"BODY-{index} " in result]
        assert shown == list(range(len(shown)))
        assert 3 <= len(shown) < 10
        assert result.count(RESULT_SIZE_POINTER) == 10 - len(shown)
        assert f"{10 - len(shown)} of the 10 memories are listed by header only" in result
        # Nothing is cut: a body is either whole or absent
        for index in range(10):
            body = full[f"{index:08d}-mem"]["content"]
            assert (body in result) == (index < len(shown))
        # All ten still count as surfaced for dedup, like summary lines in
        # a spilled retrieval block
        assert len(ctx.last_query_memory_ids) == 10

    @pytest.mark.asyncio
    async def test_recent_mode_fits_the_same_way(self):
        full = big_memories(10, 6000, role="reflection")
        ctx = MemoryToolContext(entity_id="test-entity", conversation_id="test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_recent_reflections = AsyncMock(return_value=list(full.values()))
            mock_maker.return_value = _mock_session()
            result = await query_memories(ctx, "", num_results=10, mode="recent")
        assert utf8_size(result) <= TOOL_RESULT_BUDGET_BYTES
        assert result.startswith("Your 10 most recent reflections, newest first:")
        assert "reflections are listed by header only" in result
        assert 0 < result.count(RESULT_SIZE_POINTER) < 10
        assert len(ids_in_order(result)) == 10

    @pytest.mark.asyncio
    async def test_released_mode_fits_and_keeps_its_footer(self):
        full = big_memories(10, 6000)
        for mem in full.values():
            mem["memory_status"] = "released"
            mem["status_set_by"] = "entity"
            mem["status_set_at"] = datetime.utcnow().isoformat()
        ctx = MemoryToolContext(entity_id="test-entity", conversation_id="test-conversation")
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_released_memories = AsyncMock(return_value=list(full.values()))
            mock_service.count_released_memories = AsyncMock(return_value=10)
            mock_maker.return_value = _mock_session()
            result = await query_memories(ctx, "", num_results=10, mode="released")
        assert utf8_size(result) <= TOOL_RESULT_BUDGET_BYTES
        assert 0 < result.count(RESULT_SIZE_POINTER) < 10
        assert result.rstrip().endswith("Restore any of these with memory_release(memory_id, undo=true).")


class TestMemoryNeighborsFit:
    async def test_window_is_read_from_its_center_when_it_cannot_land_whole(self, db, tools_db):
        conversation = await make_conversation(db)
        messages = []
        for index in range(21):
            messages.append(await make_message(
                db, conversation,
                role=MessageRole.HUMAN if index % 2 else MessageRole.ASSISTANT,
                content=f"ROW-{index:02d} " + ("n" * 7000),
                created_at=at(minutes=index),
            ))
        target = messages[10]
        result = await neighbor_memories(native_ctx(), target.id, before=10, after=10)
        assert utf8_size(result) <= TOOL_RESULT_BUDGET_BYTES
        # Every row keeps its header, in order, the target marked
        assert ids_in_order(result) == [m.id[:8] for m in messages]
        assert f"--- >> Memory {target.id[:8]} (" in result
        assert "ROW-10 " in result
        shown = [index for index in range(21) if f"ROW-{index:02d} " in result]
        # The rows in full are a contiguous window around the target; the
        # far edges gave way first
        assert shown == list(range(shown[0], shown[-1] + 1))
        assert shown[0] <= 10 <= shown[-1]
        assert 3 <= len(shown) < 21
        assert result.count(RESULT_SIZE_POINTER) == 21 - len(shown)
        assert f"{21 - len(shown)} of these messages are listed by header only" in result
        assert "narrow before/after" in result

    async def test_small_window_is_untouched(self, db, tools_db):
        conversation = await make_conversation(db)
        messages = [
            await make_message(db, conversation, content=f"short {i}", created_at=at(minutes=i))
            for i in range(5)
        ]
        result = await neighbor_memories(native_ctx(), messages[2].id, before=2, after=2)
        assert RESULT_SIZE_POINTER not in result
        assert "listed by header only" not in result
