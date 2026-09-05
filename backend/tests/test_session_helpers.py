"""
Tests for session_helpers.py - Session helper functions.

Tests cover:
- build_memory_queries: Building memory similarity search queries
- calculate_significance: Memory significance calculation
- search_candidate_pools / select_top_by_pool: role-balanced candidate pools (issue #335)
- get_message_content_text: Content text extraction from messages
- add_cache_control_to_tool_result: Cache control insertion
"""

import os
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.services.memory_service import role_matches_filter
from app.services.session_helpers import (
    POOL_AI,
    POOL_ALL,
    POOL_HUMAN,
    add_cache_control_to_tool_result,
    build_memory_queries,
    calculate_significance,
    drop_in_context_reflections,
    estimate_prompt_tokens,
    get_message_content_text,
    make_link_timestamper,
    retrieval_top_k_by_pool,
    search_candidate_pools,
    select_top_by_pool,
    stamp_human_message,
    total_prompt_tokens_from_usage,
)

# ============================================================
# Tests for stamp_human_message
# ============================================================

class TestStampHumanMessage:
    """Tests for timestamping human messages in LLM context."""

    @pytest.fixture
    def chicago_tz(self):
        """Force a known local timezone so the UTC->local conversion is deterministic."""
        if not hasattr(time, "tzset"):
            pytest.skip("time.tzset not available on this platform")
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/Chicago"
        time.tzset()
        yield
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

    def test_naive_utc_converted_to_local_time(self, chicago_tz):
        # Naive datetimes are treated as UTC (matching Message.created_at);
        # July 8 14:32 UTC is 09:32 CDT
        ts = datetime(2026, 7, 8, 14, 32, 45)
        assert stamp_human_message("Hello", ts) == "[2026-07-08 09:32 CDT] Hello"

    def test_timestamp_is_prefix_so_suffix_matching_survives(self):
        # Regenerate matches context entries via endswith(original content)
        ts = datetime(2026, 7, 8, 14, 32)
        assert stamp_human_message("Hello", ts).endswith("Hello")

    def test_none_timestamp_returns_content_unchanged(self):
        assert stamp_human_message("Hello", None) == "Hello"


# ============================================================
# Tests for build_memory_queries
# ============================================================

class TestBuildMemoryQueries:
    """Tests for building memory similarity search queries."""

    def test_with_current_message_and_assistant_response(self):
        """Should return both user query and assistant query."""
        context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        user_q, assistant_q = build_memory_queries(context, "Tell me about AI")
        assert user_q == "Tell me about AI"
        assert assistant_q == "Hi there!"

    def test_with_current_message_no_assistant(self):
        """Should return user query with no assistant query."""
        context = [
            {"role": "user", "content": "First message"},
        ]
        user_q, assistant_q = build_memory_queries(context, "Second message")
        assert user_q == "Second message"
        assert assistant_q is None

    def test_continuation_with_assistant(self):
        """Continuation (no current message) should return last assistant message."""
        context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "I was talking about AI ethics."},
        ]
        user_q, assistant_q = build_memory_queries(context, None)
        assert user_q is None
        assert assistant_q == "I was talking about AI ethics."

    def test_continuation_without_assistant(self):
        """Continuation without assistant should fall back to last user message."""
        context = [
            {"role": "user", "content": "What about ethics?"},
        ]
        user_q, assistant_q = build_memory_queries(context, None)
        assert user_q == "What about ethics?"
        assert assistant_q is None

    def test_continuation_empty_context(self):
        """Continuation with empty context should return (None, None)."""
        user_q, assistant_q = build_memory_queries([], None)
        assert user_q is None
        assert assistant_q is None

    def test_empty_current_message_treated_as_continuation(self):
        """Empty string current message should be treated as continuation."""
        context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        user_q, assistant_q = build_memory_queries(context, "")
        assert user_q is None
        assert assistant_q == "Hi!"

    def test_multiple_assistant_messages_returns_last(self):
        """Should return the most recent assistant message."""
        context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "Follow up"},
            {"role": "assistant", "content": "Second response"},
        ]
        user_q, assistant_q = build_memory_queries(context, "Third message")
        assert user_q == "Third message"
        assert assistant_q == "Second response"


# ============================================================
# Tests for calculate_significance
# ============================================================

class TestCalculateSignificance:
    """Tests for memory significance calculation."""

    def test_never_retrieved_memory(self):
        """Memory with zero retrievals should have base significance."""
        now = datetime.utcnow()
        sig = calculate_significance(
            times_retrieved=0,
            created_at=now,
            last_retrieved_at=None,
        )
        # (1 + 0.1 * 0) * 1.0 * 1.0 = 1.0
        assert sig == pytest.approx(1.0, abs=0.01)

    def test_retrieved_memory_boosts_significance(self):
        """Memory with retrievals should have higher base significance."""
        now = datetime.utcnow()
        sig = calculate_significance(
            times_retrieved=10,
            created_at=now,
            last_retrieved_at=None,
        )
        # (1 + 0.1 * 10) * 1.0 * 1.0 = 2.0
        assert sig == pytest.approx(2.0, abs=0.01)

    def test_recently_retrieved_gets_recency_boost(self):
        """Recently retrieved memory should get recency boost."""
        now = datetime.utcnow()
        sig = calculate_significance(
            times_retrieved=0,
            created_at=now,
            last_retrieved_at=now,
        )
        # (1 + 0) * (1.0 + recency_boost_strength) * 1.0
        # recency_boost_strength default is 1.2
        assert sig > 1.0

    def test_old_memory_decays(self):
        """Old memory should have reduced significance due to half-life."""
        now = datetime.utcnow()
        old_date = now - timedelta(days=60)  # One half-life
        sig = calculate_significance(
            times_retrieved=0,
            created_at=old_date,
            last_retrieved_at=None,
        )
        # (1 + 0) * 1.0 * 0.5^(60/60) = 0.5
        assert sig == pytest.approx(0.5, abs=0.05)

    def test_string_dates_parsed(self):
        """Should handle string date formats."""
        now = datetime.utcnow()
        sig = calculate_significance(
            times_retrieved=1,
            created_at=now.isoformat(),
            last_retrieved_at=now.isoformat(),
        )
        assert sig > 0

    def test_none_created_at(self):
        """Should handle None created_at gracefully."""
        sig = calculate_significance(
            times_retrieved=1,
            created_at=None,
            last_retrieved_at=None,
        )
        # half_life_modifier stays at 1.0
        assert sig == pytest.approx(1.1, abs=0.01)

    def test_retrieval_days_ago_reduces_recency(self):
        """Retrieval from days ago should have reduced recency boost."""
        now = datetime.utcnow()
        sig_recent = calculate_significance(
            times_retrieved=0,
            created_at=now,
            last_retrieved_at=now,
        )
        sig_older = calculate_significance(
            times_retrieved=0,
            created_at=now,
            last_retrieved_at=now - timedelta(days=7),
        )
        assert sig_recent > sig_older

    def test_reflection_role_gets_significance_multiplier(self):
        """Memories saved via memory_save (role='reflection') are boosted."""
        from app.config import settings
        now = datetime.utcnow()
        sig_normal = calculate_significance(
            times_retrieved=0,
            created_at=now,
            last_retrieved_at=None,
        )
        sig_reflection = calculate_significance(
            times_retrieved=0,
            created_at=now,
            last_retrieved_at=None,
            role="reflection",
        )
        assert sig_reflection == pytest.approx(
            sig_normal * settings.reflection_significance_multiplier, abs=0.01
        )

    def test_non_reflection_role_not_boosted(self):
        """Human/assistant memories are unaffected by the reflection multiplier."""
        now = datetime.utcnow()
        sig_default = calculate_significance(
            times_retrieved=2,
            created_at=now,
            last_retrieved_at=None,
        )
        sig_human = calculate_significance(
            times_retrieved=2,
            created_at=now,
            last_retrieved_at=None,
            role="human",
        )
        assert sig_human == pytest.approx(sig_default, abs=0.001)


# ============================================================
# Tests for the role-balanced candidate pools (issue #335)
# ============================================================

class TestRetrievalTopKByPool:
    def test_split_gives_each_role_pool_the_per_role_n(self):
        assert retrieval_top_k_by_pool(True, merged_top_k=5, per_role_top_k=3) == {
            POOL_HUMAN: 3, POOL_AI: 3,
        }

    def test_merged_gives_the_single_pool_top_k(self):
        assert retrieval_top_k_by_pool(False, merged_top_k=5, per_role_top_k=3) == {
            POOL_ALL: 5,
        }


class TestSearchCandidatePools:
    """search_candidate_pools runs the prompt query and the entity's
    last-message query against each pool and merges per pool."""

    HITS = [
        {"id": "h1", "score": 0.9, "role": "human"},
        {"id": "a1", "score": 0.8, "role": "assistant"},
        {"id": "r1", "score": 0.7, "role": "reflection"},
        {"id": "s1", "score": 0.6, "role": "sibling"},
        {"id": "u1", "score": 0.5},  # no role metadata: pre-role record
    ]

    @staticmethod
    def _search(hits_by_query=None, honor_filter=True):
        """A search_memories stand-in. With honor_filter it applies the
        role filter the way Pinecone would; without it, it ignores the
        filter (the shape of a naive test mock)."""
        async def search(query, top_k, role_filter=None, **kwargs):
            hits = (hits_by_query or {}).get(query, TestSearchCandidatePools.HITS)
            if honor_filter:
                hits = [h for h in hits if role_matches_filter(h.get("role"), role_filter)]
            return [dict(h) for h in hits]
        return AsyncMock(side_effect=search)

    async def test_split_runs_both_queries_against_both_pools(self):
        search = self._search()
        pools = await search_candidate_pools(
            search, "the prompt", "my last message", fetch_k=10, split_by_role=True,
            entity_id="idx",
        )
        calls = {(c.kwargs["role_filter"], c.kwargs["query"]) for c in search.await_args_list}
        assert calls == {
            ("human", "the prompt"), ("human", "my last message"),
            ("ai", "the prompt"), ("ai", "my last message"),
        }
        assert all(c.kwargs["entity_id"] == "idx" and c.kwargs["top_k"] == 10
                   for c in search.await_args_list)
        assert set(pools) == {POOL_HUMAN, POOL_AI}
        assert [h["id"] for h in pools[POOL_HUMAN]] == ["h1"]
        # "ai" is everything that isn't the human: messages, reflections,
        # sibling letters, and records written before the role field
        assert {h["id"] for h in pools[POOL_AI]} == {"a1", "r1", "s1", "u1"}
        assert all(h["_pool"] == POOL_AI for h in pools[POOL_AI])
        assert all(h["_source"] == "both" for h in pools[POOL_AI])

    async def test_merged_runs_both_queries_once_with_no_filter(self):
        search = self._search()
        pools = await search_candidate_pools(
            search, "the prompt", "my last message", fetch_k=10, split_by_role=False,
        )
        assert [(c.kwargs["role_filter"], c.kwargs["query"]) for c in search.await_args_list] == [
            (None, "the prompt"), (None, "my last message"),
        ]
        assert set(pools) == {POOL_ALL}
        assert {h["id"] for h in pools[POOL_ALL]} == {"h1", "a1", "r1", "s1", "u1"}
        assert all(h["_pool"] == POOL_ALL for h in pools[POOL_ALL])

    async def test_missing_assistant_query_halves_the_plan(self):
        search = self._search()
        await search_candidate_pools(
            search, "the prompt", None, fetch_k=10, split_by_role=True,
        )
        assert [(c.kwargs["role_filter"], c.kwargs["query"]) for c in search.await_args_list] == [
            ("human", "the prompt"), ("ai", "the prompt"),
        ]

    async def test_no_queries_runs_no_search(self):
        search = self._search()
        pools = await search_candidate_pools(
            search, None, None, fetch_k=10, split_by_role=True,
        )
        search.assert_not_awaited()
        assert pools == {POOL_HUMAN: [], POOL_AI: []}

    async def test_duplicate_within_a_pool_keeps_the_higher_score_and_both_sources(self):
        search = self._search(hits_by_query={
            "the prompt": [{"id": "h1", "score": 0.5, "role": "human"},
                           {"id": "h2", "score": 0.4, "role": "human"}],
            "my last message": [{"id": "h1", "score": 0.9, "role": "human"}],
        })
        pools = await search_candidate_pools(
            search, "the prompt", "my last message", fetch_k=10, split_by_role=True,
        )
        by_id = {h["id"]: h for h in pools[POOL_HUMAN]}
        assert by_id["h1"]["score"] == 0.9
        assert by_id["h1"]["_source"] == "both"
        assert by_id["h2"]["_source"] == "user"

    async def test_a_hit_that_contradicts_its_pool_filter_is_not_admitted(self):
        # The Pinecone filter guarantees this in production; the backstop
        # keeps a hit from landing in two pools if the filter ever leaks
        search = self._search(honor_filter=False)
        pools = await search_candidate_pools(
            search, "the prompt", None, fetch_k=10, split_by_role=True,
        )
        assert [h["id"] for h in pools[POOL_HUMAN]] == ["h1"]
        assert {h["id"] for h in pools[POOL_AI]} == {"a1", "r1", "s1", "u1"}

    async def test_tags_do_not_leak_into_the_search_results(self):
        # search_memories may hand back its cache's own dicts
        shared = [{"id": "h1", "score": 0.9, "role": "human"}]
        search = AsyncMock(return_value=shared)
        await search_candidate_pools(search, "the prompt", None, fetch_k=10, split_by_role=True)
        assert "_pool" not in shared[0] and "_source" not in shared[0]


class TestSelectTopByPool:
    @staticmethod
    def _item(mem_id, role, score, pool):
        return {
            "mem_data": {"id": mem_id, "role": role},
            "combined_score": score,
            "pool": pool,
        }

    def test_each_pool_is_cut_at_its_own_n_and_merged_by_score(self):
        enriched = [
            self._item("a1", "assistant", 0.9, POOL_AI),
            self._item("a2", "assistant", 0.8, POOL_AI),
            self._item("a3", "assistant", 0.7, POOL_AI),
            self._item("a4", "assistant", 0.6, POOL_AI),
            self._item("h1", "human", 0.5, POOL_HUMAN),
            self._item("h2", "human", 0.4, POOL_HUMAN),
            self._item("h3", "human", 0.3, POOL_HUMAN),
            self._item("h4", "human", 0.2, POOL_HUMAN),
        ]
        selection = select_top_by_pool(enriched, set(), {POOL_HUMAN: 3, POOL_AI: 3})
        assert [i["mem_data"]["id"] for i in selection.selected] == [
            "a1", "a2", "a3", "h1", "h2", "h3",
        ]
        assert [i["mem_data"]["id"] for i in selection.unselected] == ["a4", "h4"]
        assert selection.pool_sizes == {POOL_HUMAN: 4, POOL_AI: 4}
        assert selection.pool_selected == {POOL_HUMAN: 3, POOL_AI: 3}
        assert selection.describe({POOL_HUMAN: 3, POOL_AI: 3}) == "human 3/4 (top 3), ai 3/4 (top 3)"

    def test_a_short_pool_returns_fewer_not_filler_from_the_other(self):
        enriched = [
            self._item("a1", "assistant", 0.9, POOL_AI),
            self._item("a2", "assistant", 0.8, POOL_AI),
            self._item("a3", "assistant", 0.7, POOL_AI),
            self._item("a4", "assistant", 0.6, POOL_AI),
            self._item("h1", "human", 0.5, POOL_HUMAN),
        ]
        selection = select_top_by_pool(enriched, set(), {POOL_HUMAN: 3, POOL_AI: 3})
        assert [i["mem_data"]["id"] for i in selection.selected] == ["a1", "a2", "a3", "h1"]
        assert selection.pool_selected == {POOL_HUMAN: 1, POOL_AI: 3}

    def test_in_context_reflection_leaves_its_pool_before_the_cut(self):
        enriched = [
            self._item("r1", "reflection", 0.9, POOL_AI),
            self._item("a1", "assistant", 0.8, POOL_AI),
            self._item("a2", "assistant", 0.7, POOL_AI),
            self._item("a3", "assistant", 0.6, POOL_AI),
        ]
        selection = select_top_by_pool(enriched, {"r1"}, {POOL_HUMAN: 3, POOL_AI: 3})
        assert [i["mem_data"]["id"] for i in selection.selected] == ["a1", "a2", "a3"]
        assert [i["mem_data"]["id"] for i in selection.skipped_reflections] == ["r1"]
        assert selection.pool_sizes[POOL_AI] == 3

    def test_in_context_verbatim_stays_in_the_cut(self):
        enriched = [
            self._item("a0", "assistant", 0.9, POOL_AI),
            self._item("a1", "assistant", 0.8, POOL_AI),
            self._item("a2", "assistant", 0.7, POOL_AI),
            self._item("a3", "assistant", 0.6, POOL_AI),
        ]
        selection = select_top_by_pool(enriched, {"a0"}, {POOL_AI: 3})
        # The caller skips a0 without backfill; a3 stays below the cut
        assert [i["mem_data"]["id"] for i in selection.selected] == ["a0", "a1", "a2"]
        assert [i["mem_data"]["id"] for i in selection.unselected] == ["a3"]

    def test_merged_pool_is_cut_at_top_k_in_score_order(self):
        enriched = [
            self._item("a1", "assistant", 0.5, POOL_ALL),
            self._item("h1", "human", 0.9, POOL_ALL),
            self._item("a2", "assistant", 0.7, POOL_ALL),
        ]
        selection = select_top_by_pool(enriched, set(), {POOL_ALL: 2})
        assert [i["mem_data"]["id"] for i in selection.selected] == ["h1", "a2"]
        assert [i["mem_data"]["id"] for i in selection.unselected] == ["a1"]

    def test_untagged_items_belong_to_the_merged_pool(self):
        enriched = [{"mem_data": {"id": "x", "role": "human"}, "combined_score": 0.5}]
        selection = select_top_by_pool(enriched, set(), {POOL_ALL: 1})
        assert [i["mem_data"]["id"] for i in selection.selected] == ["x"]

    def test_empty(self):
        selection = select_top_by_pool([], set(), {POOL_HUMAN: 3, POOL_AI: 3})
        assert selection.selected == [] and selection.unselected == []
        assert selection.pool_selected == {POOL_HUMAN: 0, POOL_AI: 0}


# ============================================================
# Tests for get_message_content_text
# ============================================================

class TestGetMessageContentText:
    """Tests for extracting text from message content."""

    def test_string_content(self):
        """Should return string directly."""
        assert get_message_content_text("Hello world") == "Hello world"

    def test_empty_string(self):
        """Should return empty string for empty input."""
        assert get_message_content_text("") == ""

    def test_non_string_non_list(self):
        """Should convert non-string non-list to string."""
        assert get_message_content_text(42) == "42"
        assert get_message_content_text(None) == "None"

    def test_text_content_blocks(self):
        """Should extract text from text content blocks."""
        blocks = [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "World"},
        ]
        result = get_message_content_text(blocks)
        assert "Hello" in result
        assert "World" in result

    def test_tool_use_content_blocks(self):
        """Should summarize tool use blocks."""
        blocks = [
            {"type": "tool_use", "name": "web_search", "input": {"query": "test"}},
        ]
        result = get_message_content_text(blocks)
        assert "web_search" in result
        assert "test" in result

    def test_tool_result_content_blocks_string(self):
        """Should extract tool result content as string."""
        blocks = [
            {"type": "tool_result", "content": "Search results here"},
        ]
        result = get_message_content_text(blocks)
        assert "Search results here" in result

    def test_tool_result_content_blocks_list(self):
        """Should extract tool result content from list."""
        blocks = [
            {"type": "tool_result", "content": [{"type": "text", "text": "data"}]},
        ]
        result = get_message_content_text(blocks)
        assert "data" in result

    def test_mixed_content_blocks(self):
        """Should handle mixed content block types."""
        blocks = [
            {"type": "text", "text": "Let me search."},
            {"type": "tool_use", "name": "search", "input": {}},
        ]
        result = get_message_content_text(blocks)
        assert "Let me search." in result
        assert "search" in result

    def test_non_dict_blocks(self):
        """Should handle non-dict items in content list."""
        blocks = ["plain string", 42]
        result = get_message_content_text(blocks)
        assert "plain string" in result
        assert "42" in result


# ============================================================
# Tests for add_cache_control_to_tool_result
# ============================================================

class TestAddCacheControlToToolResult:
    """Tests for adding cache control to tool result messages."""

    def test_adds_cache_control_to_last_block(self):
        """Should add cache_control to the last content block."""
        user_msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "content": "Result 1", "tool_use_id": "t1"},
                {"type": "tool_result", "content": "Result 2", "tool_use_id": "t2"},
            ],
        }
        result = add_cache_control_to_tool_result(user_msg)

        # Original should be unchanged
        assert "cache_control" not in user_msg["content"][1]

        # Result should have cache_control on last block
        assert "cache_control" in result["content"][1]
        assert result["content"][1]["cache_control"]["type"] == "ephemeral"

        # First block should NOT have cache_control
        assert "cache_control" not in result["content"][0]

    def test_single_block(self):
        """Should add cache_control to single content block."""
        user_msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "content": "Only result", "tool_use_id": "t1"},
            ],
        }
        result = add_cache_control_to_tool_result(user_msg)
        assert "cache_control" in result["content"][0]

    def test_string_content_unchanged(self):
        """Should handle string content without changes."""
        user_msg = {"role": "user", "content": "Plain text"}
        result = add_cache_control_to_tool_result(user_msg)
        assert result["content"] == "Plain text"

    def test_empty_content_list(self):
        """Should handle empty content list."""
        user_msg = {"role": "user", "content": []}
        result = add_cache_control_to_tool_result(user_msg)
        assert result["content"] == []

    def test_does_not_mutate_original(self):
        """Should not mutate the original message."""
        original_block = {"type": "tool_result", "content": "Result", "tool_use_id": "t1"}
        user_msg = {"role": "user", "content": [original_block]}
        result = add_cache_control_to_tool_result(user_msg)

        assert "cache_control" not in original_block
        assert "cache_control" in result["content"][0]


# ============================================================
# Tests for provider-usage calibration helpers
# ============================================================

class TestTotalPromptTokensFromUsage:
    """Tests for summing prompt-side tokens from a provider usage dict."""

    def test_sums_all_prompt_side_fields(self):
        usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 850,
            "output_tokens": 400,  # output side must be excluded
        }
        assert total_prompt_tokens_from_usage(usage) == 1000

    def test_handles_missing_cache_fields(self):
        assert total_prompt_tokens_from_usage({"input_tokens": 42}) == 42

    def test_handles_none_values(self):
        """Some Anthropic-compatible APIs return None instead of omitting."""
        usage = {"input_tokens": 10, "cache_read_input_tokens": None}
        assert total_prompt_tokens_from_usage(usage) == 10

    def test_handles_missing_usage(self):
        assert total_prompt_tokens_from_usage(None) == 0
        assert total_prompt_tokens_from_usage({}) == 0


class TestEstimatePromptTokens:
    """Tests for locally estimating a full API prompt's token size."""

    def test_counts_messages_and_system_prompt(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        captured = []

        def fake_count(text):
            captured.append(text)
            return len(text)

        result = estimate_prompt_tokens(messages, fake_count, system_prompt="Be kind")
        assert result > 0
        assert len(captured) == 1
        assert "Be kind" in captured[0]
        assert "user: Hello" in captured[0]
        assert "assistant: Hi there" in captured[0]

    def test_extracts_text_from_content_blocks(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Block text"}]},
        ]
        captured = []

        def fake_count(text):
            captured.append(text)
            return 5

        assert estimate_prompt_tokens(messages, fake_count) == 5
        assert "Block text" in captured[0]

    def test_empty_messages(self):
        assert estimate_prompt_tokens([], lambda t: len(t)) == 0


class TestMakeLinkTimestamper:
    """Tests for make_link_timestamper (memory-link reload-position anchoring)."""

    def test_anchors_strictly_before_send_timestamp(self):
        """All produced timestamps must sort before the human message row's
        created_at, so session reload re-inserts the memories before it
        (matching the live insertion position)."""
        sent_at = datetime(2026, 7, 10, 20, 30, 0)
        next_link_time = make_link_timestamper(sent_at)

        first = next_link_time()
        assert first == sent_at - timedelta(milliseconds=1)
        assert first < sent_at

    def test_strictly_increasing_preserves_insertion_order(self):
        """Reload sorts links by retrieved_at, so per-turn timestamps must be
        strictly increasing to reproduce the live insertion order."""
        sent_at = datetime(2026, 7, 10, 20, 30, 0)
        next_link_time = make_link_timestamper(sent_at)

        stamps = [next_link_time() for _ in range(5)]
        assert stamps == sorted(stamps)
        assert len(set(stamps)) == 5
        # Even a long turn's worth of links stays before the send timestamp
        assert all(s < sent_at for s in stamps)

    def test_none_timestamp_yields_none(self):
        """Without a send timestamp, callers fall back to wall-clock defaults."""
        next_link_time = make_link_timestamper(None)
        assert next_link_time() is None
        assert next_link_time() is None


# ============================================================
# Tests for drop_in_context_reflections (issue #328)
# ============================================================

class TestDropInContextReflections:
    """In-context reflections leave the ranked pool before the top-k cut;
    in-context verbatim memories stay (and hold their slot downstream)."""

    def _make_candidate(self, role, score, mem_id):
        return {
            "mem_data": {"role": role, "id": mem_id},
            "combined_score": score,
        }

    def test_in_context_reflections_are_dropped_in_rank_order(self):
        pool = [
            self._make_candidate("reflection", 0.95, "r1"),
            self._make_candidate("human", 0.9, "h1"),
            self._make_candidate("reflection", 0.85, "r2"),
            self._make_candidate("assistant", 0.8, "a1"),
        ]
        remaining, dropped = drop_in_context_reflections(pool, {"r1", "r2"})
        assert [c["mem_data"]["id"] for c in remaining] == ["h1", "a1"]
        assert [c["mem_data"]["id"] for c in dropped] == ["r1", "r2"]

    def test_in_context_verbatim_stays_in_the_pool(self):
        pool = [
            self._make_candidate("human", 0.9, "h1"),
            self._make_candidate("assistant", 0.8, "a1"),
        ]
        remaining, dropped = drop_in_context_reflections(pool, {"h1", "a1"})
        assert [c["mem_data"]["id"] for c in remaining] == ["h1", "a1"]
        assert dropped == []

    def test_reflections_not_in_context_stay_retrievable(self):
        pool = [
            self._make_candidate("reflection", 0.95, "r1"),
            self._make_candidate("human", 0.9, "h1"),
        ]
        remaining, dropped = drop_in_context_reflections(pool, {"h1"})
        assert [c["mem_data"]["id"] for c in remaining] == ["r1", "h1"]
        assert dropped == []

    def test_empty_inputs(self):
        assert drop_in_context_reflections([], {"r1"}) == ([], [])
        pool = [self._make_candidate("reflection", 0.9, "r1")]
        remaining, dropped = drop_in_context_reflections(pool, set())
        assert len(remaining) == 1 and dropped == []
