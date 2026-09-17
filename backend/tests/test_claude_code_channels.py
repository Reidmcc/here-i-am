"""
The Claude Code endpoints hand the hooks what they need to fit their
stdout to the harness's line: session-start returns the bulk as named
parts (one file each) and the hook budget; retrieve returns the block's
header and one entry per memory (rendered marker + summary line) in rank
order, so the hook can print whole memories while they fit and summary
lines after. The joined bulk_context and the whole context_summary stay
for hooks that predate the split.

Reuses the route tests' fixtures.
"""
# ruff: noqa: F811 — the route tests' fixtures are imported and then named
# as test parameters, which ruff reads as redefinition.
import uuid
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services.claude_code_mode import RETRIEVAL_BLOCK_HEADER
from app.services.harness_limits import HOOK_INLINE_BUDGET_CHARS
from tests.test_claude_code_routes import (  # noqa: F401
    async_client,
    cc_mode_enabled,
    db_session,
    notes_dir,
    test_engine,
)


async def test_session_start_returns_named_bulk_parts_and_the_budget(async_client, notes_dir):
    response = await async_client.post(
        "/api/claude-code/session-start", json={"session_id": str(uuid.uuid4())}
    )
    body = response.json()
    assert body["inline_budget"] == HOOK_INLINE_BUDGET_CHARS
    names = [part["name"] for part in body["bulk_parts"]]
    assert names == ["notes-index"]
    assert "My index: current projects." in body["bulk_parts"][0]["text"]
    # The joined block is the parts, for a hook that predates them
    assert body["bulk_context"] == "\n\n".join(part["text"] for part in body["bulk_parts"])


def _memory(mem_id: str, role: str, content: str) -> dict:
    return {
        "id": mem_id,
        "conversation_id": "elsewhere",
        "role": role,
        "content": content,
        "created_at": "2026-09-01T12:00:00",
        "times_retrieved": 0,
        "last_retrieved_at": None,
        "memory_status": None,
        "source": "native",
    }


async def test_retrieve_returns_header_and_one_item_per_memory_in_rank_order(
    async_client, monkeypatch
):
    monkeypatch.setattr(settings, "memory_role_balance_enabled", False)
    monkeypatch.setattr(settings, "initial_retrieval_top_k", 5)
    ranked = [
        ("mem-1", "human", "First line of the first memory.\nMore of it."),
        ("mem-2", "assistant", "The second memory, whole."),
    ]
    hits = [
        {"id": mem_id, "score": 0.9 - 0.01 * i, "conversation_id": "elsewhere", "role": role}
        for i, (mem_id, role, _) in enumerate(ranked)
    ]
    full = {mem_id: _memory(mem_id, role, content) for mem_id, role, content in ranked}
    with patch("app.services.claude_code_mode.memory_service") as mock_memory:
        mock_memory.is_configured.return_value = True
        mock_memory.store_memory = AsyncMock(return_value=True)
        mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
        mock_memory.get_retrieved_ids_for_conversation = AsyncMock(return_value=set())
        mock_memory.search_memories = AsyncMock(return_value=hits)
        mock_memory.get_full_memory_content = AsyncMock(side_effect=lambda mem_id, db: full.get(mem_id))
        mock_memory.update_retrieval_count = AsyncMock()
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": str(uuid.uuid4()), "prompt": "hello"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["memories_retrieved"] == 2
    assert body["inline_budget"] == HOOK_INLINE_BUDGET_CHARS
    assert body["context_header"] == RETRIEVAL_BLOCK_HEADER
    items = body["context_items"]
    assert [item["id"] for item in items] == ["mem-1", "mem-2"]
    # Each item is the rendered marker the block is made of, plus its summary line
    assert body["context"] == RETRIEVAL_BLOCK_HEADER + "\n\n" + "\n\n".join(item["text"] for item in items)
    assert items[0]["text"].startswith("[MEMORY mem-1 from 2026-09-01")
    assert "First line of the first memory." in items[0]["text"]
    assert items[0]["summary"] == (
        "- mem-1 (2026-09-01 - originally from human - via Here I Am): "
        "First line of the first memory."
    )
    # The whole summary block is the same lines under a header
    assert body["context_summary"].splitlines()[1:] == [item["summary"] for item in items]


async def test_empty_retrieval_has_no_items(async_client):
    response = await async_client.post(
        "/api/claude-code/retrieve",
        json={"session_id": str(uuid.uuid4()), "prompt": "hello"},
    )
    body = response.json()
    assert body["context"] == ""
    assert body["context_items"] == []
    assert body["context_header"] == ""
