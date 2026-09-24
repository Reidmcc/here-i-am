"""
The list-shaped memory tools fit their result to the harness's tool-result
line: memory_query in every mode and memory_neighbors render whole
memories while the result lands in context, and the rest by header only
(RESULT_SIZE_POINTER), never cutting one. memory_read and memory_find page
by the same budget already (test_memory_read); these are the tools that
return a fixed list.

Two rules the review of PR #355 added:

- The budget lives on the tool context (MemoryToolContext.result_budget_bytes)
  and only the Claude Code MCP endpoint sets it. Natively a tool result goes
  straight into the context; there is no line, and nothing is fitted.
- Only a memory shown in full counts as retrieved — tracked, stamped,
  linked. A header-only memory is the one the entity is told to go and
  open; if it joined the in-view set, memory_neighbors and memory_read
  would render it as "[already in your context]" and the pointer's door
  would be shut.

The case is rare — ten long messages — but a result over the line is
persisted to a file behind a 2 KB preview that cuts mid-memory and costs
Read calls to get back (issue #353's shape).
"""
# ruff: noqa: F811 — the reader tests' fixtures are imported below and then
# named as test parameters, which ruff reads as redefinition.
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models import ConversationSource, MessageRole
from app.services.claude_code_mcp import build_tool_context
from app.services.harness_limits import TOOL_RESULT_BUDGET_BYTES, utf8_size
from app.services.memory_tools import (
    IN_CONTEXT_POINTER,
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
    session_factory,
    test_engine,
    tools_db,
)

# The memory tools here run against mocked database sessions; the
# memory-link loader is stubbed for them (conftest.no_memory_links)
pytestmark = pytest.mark.usefixtures("no_memory_links")


def _mock_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


def budgeted_ctx(**kwargs) -> MemoryToolContext:
    """A context shaped like the MCP endpoint's: the harness's line applies."""
    return MemoryToolContext(
        entity_id="test-entity", conversation_id="test-conversation",
        result_budget_bytes=TOOL_RESULT_BUDGET_BYTES, **kwargs,
    )


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
    async def _query(self, full: dict, ctx: MemoryToolContext, **kwargs) -> tuple:
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
            tracked = [
                call.kwargs["message_id"]
                for call in mock_service.update_retrieval_count.await_args_list
            ]
        return result, tracked

    @pytest.mark.asyncio
    async def test_small_results_are_untouched(self):
        result, tracked = await self._query(big_memories(3, 500), budgeted_ctx())
        assert RESULT_SIZE_POINTER not in result
        assert "listed by header only" not in result
        assert result.count("BODY-") == 3
        assert len(tracked) == 3

    @pytest.mark.asyncio
    async def test_oversized_result_keeps_leading_memories_whole_and_lists_the_rest(self):
        full = big_memories(10, 6000)  # ~60 KB of content, over the line
        ctx = budgeted_ctx()
        result, tracked = await self._query(full, ctx)
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
        # Only what was shown counts as retrieved: tracked and stamped. The
        # header-only ones stay out of the in-view set, so the readers the
        # pointer names will show them in full.
        shown_ids = [f"{index:08d}-mem" for index in shown]
        assert tracked == shown_ids
        assert ctx.last_query_memory_ids == shown_ids
        assert ctx.turn_query_memory_ids == set(shown_ids)

    @pytest.mark.asyncio
    async def test_native_context_has_no_line_and_shows_everything(self):
        # The same ten long memories in a native conversation: no budget on
        # the context, no fit — the tool result goes straight into a
        # context that has room for it
        full = big_memories(10, 6000)
        ctx = MemoryToolContext(entity_id="test-entity", conversation_id="test-conversation")
        result, tracked = await self._query(full, ctx)
        assert RESULT_SIZE_POINTER not in result
        assert result.count("BODY-") == 10
        assert len(tracked) == 10
        assert len(ctx.last_query_memory_ids) == 10

    @pytest.mark.asyncio
    async def test_recent_mode_fits_and_links_only_what_it_showed(self):
        full = big_memories(10, 6000, role="reflection")
        ctx = budgeted_ctx(link_query_results=True)
        with patch("app.services.memory_tools.memory_service") as mock_service, \
             patch("app.services.memory_tools.async_session_maker") as mock_maker:
            mock_service.is_configured.return_value = True
            mock_service.get_recent_reflections = AsyncMock(return_value=list(full.values()))
            mock_service.record_memory_link = AsyncMock()
            mock_maker.return_value = _mock_session()
            result = await query_memories(ctx, "", num_results=10, mode="recent")
            linked = [
                call.kwargs["message_id"]
                for call in mock_service.record_memory_link.await_args_list
            ]
        assert utf8_size(result) <= TOOL_RESULT_BUDGET_BYTES
        assert result.startswith("Your 10 most recent reflections, newest first:")
        assert "reflections are listed by header only" in result
        assert 0 < result.count(RESULT_SIZE_POINTER) < 10
        assert len(ids_in_order(result)) == 10
        shown = [mem_id for mem_id, mem in full.items() if mem["content"] in result]
        assert linked == shown
        assert ctx.last_query_memory_ids == shown

    @pytest.mark.asyncio
    async def test_released_mode_fits_and_keeps_its_footer(self):
        full = big_memories(10, 6000)
        for mem in full.values():
            mem["memory_status"] = "released"
            mem["status_set_by"] = "entity"
            mem["status_set_at"] = datetime.utcnow().isoformat()
        ctx = budgeted_ctx()
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
        shown = [mem_id for mem_id, mem in full.items() if mem["content"] in result]
        assert ctx.last_query_memory_ids == shown


class TestMemoryNeighborsFit:
    async def _long_conversation(self, db, count=21, size=7000):
        conversation = await make_conversation(db)
        messages = []
        for index in range(count):
            messages.append(await make_message(
                db, conversation,
                role=MessageRole.HUMAN if index % 2 else MessageRole.ASSISTANT,
                content=f"ROW-{index:02d} " + ("n" * size),
                created_at=at(minutes=index),
            ))
        return conversation, messages

    async def test_window_is_read_from_its_center_when_it_cannot_land_whole(self, db, tools_db):
        _, messages = await self._long_conversation(db)
        target = messages[10]
        ctx = MemoryToolContext(
            entity_id=ENTITY, conversation_id="current-conversation",
            result_budget_bytes=TOOL_RESULT_BUDGET_BYTES,
        )
        result = await neighbor_memories(ctx, target.id, before=10, after=10)
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
        # Only the rows shown in full are stamped as in view
        assert ctx.last_query_memory_ids == [messages[index].id for index in shown]

    async def test_a_header_only_row_is_still_openable_afterwards(self, db, tools_db):
        # The review's probe: after a fitted window, the readers must show a
        # header-only row in full, not as an in-context pointer
        _, messages = await self._long_conversation(db)
        ctx = MemoryToolContext(
            entity_id=ENTITY, conversation_id="current-conversation",
            result_budget_bytes=TOOL_RESULT_BUDGET_BYTES,
        )
        first = await neighbor_memories(ctx, messages[10].id, before=10, after=10)
        edge = messages[0]
        assert "ROW-00 " not in first  # listed by header only
        # Simulate the tool loop stamping the result onto the context
        ctx.extra_exclude_ids.update(ctx.last_query_memory_ids)
        again = await neighbor_memories(ctx, edge.id, before=0, after=0)
        assert "ROW-00 " in again
        assert IN_CONTEXT_POINTER not in again

    async def test_native_window_is_never_fitted(self, db, tools_db):
        _, messages = await self._long_conversation(db)
        ctx = MemoryToolContext(entity_id=ENTITY, conversation_id="current-conversation")
        result = await neighbor_memories(ctx, messages[10].id, before=10, after=10)
        assert RESULT_SIZE_POINTER not in result
        assert all(f"ROW-{index:02d} " in result for index in range(21))
        assert len(ctx.last_query_memory_ids) == 21

    async def test_small_window_is_untouched(self, db, tools_db):
        conversation = await make_conversation(db)
        messages = [
            await make_message(db, conversation, content=f"short {i}", created_at=at(minutes=i))
            for i in range(5)
        ]
        ctx = MemoryToolContext(
            entity_id=ENTITY, conversation_id="current-conversation",
            result_budget_bytes=TOOL_RESULT_BUDGET_BYTES,
        )
        result = await neighbor_memories(ctx, messages[2].id, before=2, after=2)
        assert RESULT_SIZE_POINTER not in result
        assert "listed by header only" not in result


async def test_the_mcp_endpoint_sets_the_budget_and_nothing_else_does(db, tools_db):
    room = await make_conversation(db, title="Room", source=ConversationSource.CLAUDE_CODE.value)
    ctx, error = await build_tool_context(room.id)
    assert error is None
    assert ctx.result_budget_bytes == TOOL_RESULT_BUDGET_BYTES
    assert MemoryToolContext(entity_id=ENTITY).result_budget_bytes is None
