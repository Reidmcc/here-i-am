"""
Tests for session_helpers.py - Session helper functions.

Tests cover:
- build_memory_queries: Building memory similarity search queries
- calculate_significance: Memory significance calculation
- ensure_role_balance: Memory role balance in retrieval
- get_message_content_text: Content text extraction from messages
- build_memory_block_text: Memory block text formatting
- add_cache_control_to_tool_result: Cache control insertion
- estimate_tool_exchange_tokens: Token estimation for tool exchanges
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from app.services.session_helpers import (
    build_memory_queries,
    calculate_significance,
    ensure_role_balance,
    get_message_content_text,
    build_memory_block_text,
    add_cache_control_to_tool_result,
    estimate_tool_exchange_tokens,
)


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


# ============================================================
# Tests for ensure_role_balance
# ============================================================

class TestEnsureRoleBalance:
    """Tests for memory role balance in retrieval results."""

    def _make_candidate(self, role, score, mem_id):
        return {
            "mem_data": {"role": role, "id": mem_id},
            "combined_score": score,
        }

    def test_already_balanced(self):
        """Should return candidates unchanged when already balanced."""
        candidates = [
            self._make_candidate("human", 0.9, "h1"),
            self._make_candidate("assistant", 0.8, "a1"),
            self._make_candidate("human", 0.7, "h2"),
        ]
        result = ensure_role_balance(candidates, 3)
        assert len(result) == 3

    def test_all_human_replaces_last(self):
        """Should replace lowest-scored human with best assistant when all human."""
        candidates = [
            self._make_candidate("human", 0.9, "h1"),
            self._make_candidate("human", 0.8, "h2"),
            self._make_candidate("human", 0.7, "h3"),
            self._make_candidate("assistant", 0.6, "a1"),  # Outside top_k but in pool
        ]
        result = ensure_role_balance(candidates, 3)
        assert len(result) == 3
        roles = [r["mem_data"]["role"] for r in result]
        assert "assistant" in roles

    def test_all_assistant_replaces_last(self):
        """Should replace lowest-scored assistant with best human when all assistant."""
        candidates = [
            self._make_candidate("assistant", 0.9, "a1"),
            self._make_candidate("assistant", 0.8, "a2"),
            self._make_candidate("assistant", 0.7, "a3"),
            self._make_candidate("human", 0.6, "h1"),
        ]
        result = ensure_role_balance(candidates, 3)
        assert len(result) == 3
        roles = [r["mem_data"]["role"] for r in result]
        assert "human" in roles

    def test_empty_candidates(self):
        """Should return empty list for empty candidates."""
        result = ensure_role_balance([], 5)
        assert result == []

    def test_zero_top_k(self):
        """Should return empty list for zero top_k."""
        candidates = [self._make_candidate("human", 0.9, "h1")]
        result = ensure_role_balance(candidates, 0)
        assert result == []

    def test_single_candidate(self):
        """Should return single candidate unchanged."""
        candidates = [self._make_candidate("human", 0.9, "h1")]
        result = ensure_role_balance(candidates, 1)
        assert len(result) == 1

    def test_no_replacement_available(self):
        """Should return unchanged when no opposite-role candidates exist."""
        candidates = [
            self._make_candidate("human", 0.9, "h1"),
            self._make_candidate("human", 0.8, "h2"),
        ]
        result = ensure_role_balance(candidates, 2)
        assert len(result) == 2
        # Both still human since no assistant exists
        assert all(r["mem_data"]["role"] == "human" for r in result)


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
# Tests for build_memory_block_text
# ============================================================

class TestBuildMemoryBlockText:
    """Tests for building memory block text."""

    def test_empty_memories(self):
        """Should return empty string for no memories."""
        assert build_memory_block_text([]) == ""

    def test_single_memory(self):
        """Should format single memory correctly."""
        memories = [
            {"content": "I like Python", "created_at": "2024-01-01", "role": "human"},
        ]
        result = build_memory_block_text(memories)
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" in result
        assert "I like Python" in result
        assert "2024-01-01" in result
        assert "[/MEMORIES]" in result

    def test_multiple_memories(self):
        """Should format multiple memories."""
        memories = [
            {"content": "Memory 1", "created_at": "2024-01-01", "role": "human"},
            {"content": "Memory 2", "created_at": "2024-01-02", "role": "assistant"},
        ]
        result = build_memory_block_text(memories)
        assert "Memory 1" in result
        assert "Memory 2" in result


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
# Tests for estimate_tool_exchange_tokens
# ============================================================

class TestEstimateToolExchangeTokens:
    """Tests for estimating token counts in tool exchanges."""

    def _mock_count_tokens(self, text):
        """Simple mock: 1 token per word."""
        return len(text.split())

    def test_basic_tool_exchange(self):
        """Should estimate tokens for a basic tool exchange."""
        exchange = {
            "assistant": {
                "content": [
                    {"type": "tool_use", "name": "web_search", "input": {"query": "test"}},
                ],
            },
            "user": {
                "content": [
                    {"type": "tool_result", "content": "Search results here"},
                ],
            },
        }
        tokens = estimate_tool_exchange_tokens(exchange, self._mock_count_tokens)
        assert tokens > 0

    def test_text_block_in_assistant(self):
        """Should count text blocks in assistant content."""
        exchange = {
            "assistant": {
                "content": [
                    {"type": "text", "text": "Let me search for that."},
                    {"type": "tool_use", "name": "search", "input": {}},
                ],
            },
            "user": {
                "content": [
                    {"type": "tool_result", "content": "Done"},
                ],
            },
        }
        tokens = estimate_tool_exchange_tokens(exchange, self._mock_count_tokens)
        assert tokens > 0

    def test_tool_result_with_content_blocks(self):
        """Should handle tool result with content block list."""
        exchange = {
            "assistant": {
                "content": [
                    {"type": "tool_use", "name": "search", "input": {}},
                ],
            },
            "user": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": [
                            {"type": "text", "text": "Result text here"},
                        ],
                    },
                ],
            },
        }
        tokens = estimate_tool_exchange_tokens(exchange, self._mock_count_tokens)
        assert tokens > 0

    def test_empty_exchange(self):
        """Should return 0 for empty exchange."""
        exchange = {"assistant": {}, "user": {}}
        tokens = estimate_tool_exchange_tokens(exchange, self._mock_count_tokens)
        assert tokens == 0

    def test_string_content_not_counted_as_list(self):
        """Should handle string content in assistant (not list)."""
        exchange = {
            "assistant": {"content": "Plain text"},
            "user": {"content": "Also plain text"},
        }
        tokens = estimate_tool_exchange_tokens(exchange, self._mock_count_tokens)
        assert tokens == 0  # Only list content is processed
