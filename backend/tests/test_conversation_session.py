"""
Tests for conversation_session.py - ConversationSession and MemoryEntry dataclasses.

Tests cover:
- MemoryEntry: Dataclass initialization
- ConversationSession: Memory-in-context system (insert_memory_into_context, get_in_context_memory_count)
- ConversationSession: Context methods (add_exchange, get_cache_aware_content,
  update_cache_state, trim_context_to_limit)
"""

import pytest
from datetime import datetime
from unittest.mock import patch

from app.services.conversation_session import MemoryEntry, ConversationSession


# ============================================================
# Tests for MemoryEntry
# ============================================================

class TestMemoryEntry:
    """Tests for the MemoryEntry dataclass."""

    def test_basic_creation(self):
        """Should create MemoryEntry with required fields."""
        entry = MemoryEntry(
            id="mem-1",
            conversation_id="conv-1",
            role="assistant",
            content="Some memory content",
            created_at="2024-01-01",
            times_retrieved=3,
        )
        assert entry.id == "mem-1"
        assert entry.role == "assistant"
        assert entry.times_retrieved == 3

    def test_default_values(self):
        """Should have correct default values."""
        entry = MemoryEntry(
            id="mem-1",
            conversation_id="conv-1",
            role="human",
            content="Content",
            created_at="2024-01-01",
            times_retrieved=0,
        )
        assert entry.score == 0.0
        assert entry.significance == 0.0
        assert entry.combined_score == 0.0
        assert entry.days_since_creation == 0.0
        assert entry.days_since_retrieval == 0.0
        assert entry.source == "unknown"


# ============================================================
# Tests for ConversationSession - Memory-in-Context System
# ============================================================

class TestConversationSessionMemoryInContext:
    """Tests for the memory-in-context system."""

    def _make_memory(self, mem_id="mem-1", role="assistant", content="Test"):
        return MemoryEntry(
            id=mem_id,
            conversation_id="conv-1",
            role=role,
            content=content,
            created_at="2024-01-01",
            times_retrieved=1,
            score=0.9,
        )

    def test_insert_new_memory(self):
        """Should insert a new memory into context."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        memory = self._make_memory()

        inserted, is_new = session.insert_memory_into_context(memory)
        assert inserted is True
        assert is_new is True
        assert len(session.conversation_context) == 3  # Memory added
        assert session.conversation_context[2]["is_memory"] is True
        assert "mem-1" in session.retrieved_ids

    def test_insert_duplicate_memory_skipped(self):
        """Should not insert a memory already in context."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "Hello"},
        ]
        memory = self._make_memory()

        session.insert_memory_into_context(memory)
        inserted, is_new = session.insert_memory_into_context(memory)
        assert inserted is False
        assert is_new is False

    def test_get_in_context_memory_count(self):
        """Should count memories via the position tracker."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "[MEMORY]...[/MEMORY]", "is_memory": True, "memory_id": "mem-1"},
        ]
        session.memory_tracker.record_memory_insertion("mem-1", position=1, is_new_retrieval=True)
        assert session.get_in_context_memory_count() == 1


# ============================================================
# Tests for ConversationSession - Shared Methods
# ============================================================

class TestConversationSessionSharedMethods:
    """Tests for the session's context management methods."""

    def test_add_exchange_basic(self):
        """Should add human and assistant messages to context."""
        session = ConversationSession(conversation_id="conv-1")
        session.add_exchange("Hello!", "Hi there!")

        assert len(session.conversation_context) == 2
        assert session.conversation_context[0] == {"role": "user", "content": "Hello!"}
        assert session.conversation_context[1] == {"role": "assistant", "content": "Hi there!"}

    def test_add_exchange_continuation(self):
        """Should only add assistant response for continuation (no human message)."""
        session = ConversationSession(conversation_id="conv-1")
        session.add_exchange(None, "Continuing my thought...")

        assert len(session.conversation_context) == 1
        assert session.conversation_context[0]["role"] == "assistant"

    def test_add_exchange_multi_entity(self):
        """Should label assistant message in multi-entity mode."""
        session = ConversationSession(
            conversation_id="conv-1",
            is_multi_entity=True,
            responding_entity_label="Claude",
        )
        session.add_exchange("Hello", "Response here")

        assert "[Claude]:" in session.conversation_context[1]["content"]

    def test_add_exchange_with_tool_exchanges(self):
        """Should insert tool exchanges between user and assistant messages."""
        session = ConversationSession(conversation_id="conv-1")
        tool_exchanges = [
            {
                "assistant": {"content": [{"type": "tool_use", "name": "search"}]},
                "user": {"content": [{"type": "tool_result", "content": "results"}]},
            },
        ]
        session.add_exchange("Search for AI", "Here are the results.", tool_exchanges=tool_exchanges)

        assert len(session.conversation_context) == 4
        assert session.conversation_context[0]["role"] == "user"
        assert session.conversation_context[1].get("is_tool_use") is True
        assert session.conversation_context[2].get("is_tool_result") is True
        assert session.conversation_context[3]["role"] == "assistant"

    def test_get_cache_aware_content(self):
        """Should split context into cached and new portions."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "3"},
            {"role": "assistant", "content": "4"},
        ]
        session.last_cached_context_length = 2

        result = session.get_cache_aware_content()
        assert len(result["cached_context"]) == 2
        assert len(result["new_context"]) == 2

    def test_get_cache_aware_content_no_cache(self):
        """Should return all context as new when nothing cached."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "1"},
        ]

        result = session.get_cache_aware_content()
        assert len(result["cached_context"]) == 0
        assert len(result["new_context"]) == 1

    def test_update_cache_state(self):
        """Should update cached context length."""
        session = ConversationSession(conversation_id="conv-1")
        session.update_cache_state(5)
        assert session.last_cached_context_length == 5

    def test_trim_context_to_limit(self):
        """Should trim oldest messages from context."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "Message 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Message 2"},
            {"role": "assistant", "content": "Response 2"},
        ]

        # First call returns over limit, subsequent calls under limit
        call_count = [0]
        def mock_count(text):
            call_count[0] += 1
            if len(session.conversation_context) > 2:
                return 1000  # Over limit
            return 10  # Under limit

        removed = session.trim_context_to_limit(
            max_tokens=50,
            count_tokens_fn=mock_count,
        )
        assert removed > 0
        assert len(session.conversation_context) <= 4

    def test_trim_context_removes_pairs(self):
        """Should remove user/assistant pairs together."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "M1"},
            {"role": "assistant", "content": "R1"},
            {"role": "user", "content": "M2"},
            {"role": "assistant", "content": "R2"},
        ]

        # Always over limit until only 2 messages remain
        def mock_count(text):
            if len(session.conversation_context) > 2:
                return 1000
            return 10

        removed = session.trim_context_to_limit(
            max_tokens=50,
            count_tokens_fn=mock_count,
        )
        # Should have removed user+assistant pair
        assert removed == 2

    def test_trim_context_minimum_messages(self):
        """Should not trim below 2 messages."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "Only message"},
        ]

        removed = session.trim_context_to_limit(
            max_tokens=1,
            count_tokens_fn=lambda x: 999,  # Always over limit
        )
        assert len(session.conversation_context) >= 1

    def test_trim_context_shifts_cache_breakpoint(self):
        """Front-trimming must shift last_cached_context_length so the cache
        breakpoint keeps pointing at the same messages. Otherwise the cached
        prefix changes and every subsequent turn is a full cache miss."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": f"m{i}"} for i in range(10)
        ]
        session.last_cached_context_length = 6

        # Over limit until only 6 messages remain (removes the first 4).
        removed = session.trim_context_to_limit(
            max_tokens=50,
            count_tokens_fn=lambda text: 1000 if len(session.conversation_context) > 6 else 10,
        )
        assert removed == 4
        # Breakpoint shifts down by the number removed from the front.
        assert session.last_cached_context_length == 2

    def test_trim_context_collapses_breakpoint_inside_trimmed_region(self):
        """If the breakpoint was inside the trimmed region it collapses to 0."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": f"m{i}"} for i in range(10)
        ]
        session.last_cached_context_length = 2

        removed = session.trim_context_to_limit(
            max_tokens=50,
            count_tokens_fn=lambda text: 1000 if len(session.conversation_context) > 3 else 10,
        )
        assert removed > 2
        assert session.last_cached_context_length == 0


class TestTokenCalibration:
    """Tests for provider-usage token calibration."""

    def test_ratio_defaults_to_one(self):
        """Ratio is 1.0 before any usage has been recorded."""
        session = ConversationSession(conversation_id="conv-1")
        assert session.token_calibration_ratio == 1.0

    def test_record_prompt_usage_sets_ratio(self):
        """Ratio reflects provider-reported vs estimated tokens."""
        session = ConversationSession(conversation_id="conv-1")
        session.record_prompt_usage(actual_tokens=1200, estimated_tokens=1000)
        assert session.last_prompt_actual_tokens == 1200
        assert session.last_prompt_estimated_tokens == 1000
        assert session.token_calibration_ratio == 1.2

    def test_record_prompt_usage_ignores_missing_data(self):
        """Zero/absent usage (e.g. providers reporting nothing) is not recorded."""
        session = ConversationSession(conversation_id="conv-1")
        session.record_prompt_usage(actual_tokens=0, estimated_tokens=1000)
        assert session.last_prompt_actual_tokens is None
        assert session.token_calibration_ratio == 1.0

        session.record_prompt_usage(actual_tokens=1000, estimated_tokens=0)
        assert session.last_prompt_actual_tokens is None
        assert session.token_calibration_ratio == 1.0

    def test_ratio_is_clamped(self):
        """Pathological readings can't wildly distort trimming."""
        session = ConversationSession(conversation_id="conv-1")
        session.record_prompt_usage(actual_tokens=100_000, estimated_tokens=100)
        assert session.token_calibration_ratio == 2.0

        session.record_prompt_usage(actual_tokens=100, estimated_tokens=100_000)
        assert session.token_calibration_ratio == 0.5

    def test_trim_applies_calibration_ratio(self):
        """A raw count under the limit still trims when the calibrated count is over."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "M1"},
            {"role": "assistant", "content": "R1"},
            {"role": "user", "content": "M2"},
            {"role": "assistant", "content": "R2"},
        ]
        # Estimator says 90 (< 100 limit), but calibration says real usage
        # runs 1.5x the estimate -> 135 (> 100), so trimming must kick in.
        session.record_prompt_usage(actual_tokens=150, estimated_tokens=100)

        def mock_count(text):
            return 90 if len(session.conversation_context) > 2 else 10

        removed = session.trim_context_to_limit(
            max_tokens=100,
            count_tokens_fn=mock_count,
        )
        assert removed == 2
