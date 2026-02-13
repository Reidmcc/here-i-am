"""
Tests for memory_context.py - Memory context integration module.

Tests cover:
- format_memory_as_context_message: Formatting memories as context messages
- MemoryContextTracker: Position-based memory tracking
- find_memory_insertion_point: Finding insertion positions in context
"""

import pytest

from app.services.memory_context import (
    format_memory_as_context_message,
    MemoryContextTracker,
    find_memory_insertion_point,
)


# ============================================================
# Tests for format_memory_as_context_message
# ============================================================

class TestFormatMemoryAsContextMessage:
    """Tests for formatting memories as context messages."""

    def test_assistant_memory_format(self):
        """Should format assistant memory with 'you' label."""
        result = format_memory_as_context_message(
            memory_id="mem-1",
            content="I think AI consciousness is fascinating.",
            created_at="2024-01-15",
            role="assistant",
        )
        assert result["role"] == "user"
        assert result["is_memory"] is True
        assert result["memory_id"] == "mem-1"
        assert "originally from you" in result["content"]
        assert "I think AI consciousness is fascinating." in result["content"]
        assert "2024-01-15" in result["content"]
        assert "[MEMORY" in result["content"]
        assert "[/MEMORY]" in result["content"]

    def test_human_memory_format(self):
        """Should format human memory with 'human' label."""
        result = format_memory_as_context_message(
            memory_id="mem-2",
            content="Tell me about your experiences.",
            created_at="2024-02-01",
            role="human",
        )
        assert result["role"] == "user"
        assert result["is_memory"] is True
        assert "originally from human" in result["content"]
        assert "Tell me about your experiences." in result["content"]

    def test_memory_metadata_fields(self):
        """Should include all required metadata fields."""
        result = format_memory_as_context_message(
            memory_id="test-id",
            content="Content",
            created_at="2024-01-01",
            role="assistant",
        )
        assert "role" in result
        assert "content" in result
        assert "is_memory" in result
        assert "memory_id" in result


# ============================================================
# Tests for MemoryContextTracker
# ============================================================

class TestMemoryContextTracker:
    """Tests for the MemoryContextTracker class."""

    def test_initial_state(self):
        """Tracker should start with empty state."""
        tracker = MemoryContextTracker()
        assert len(tracker.retrieved_ids) == 0
        assert len(tracker.memory_positions) == 0

    def test_record_new_memory_insertion(self):
        """Should record new memory insertion and track retrieval."""
        tracker = MemoryContextTracker()
        tracker.record_memory_insertion("mem-1", position=3, is_new_retrieval=True)

        assert "mem-1" in tracker.retrieved_ids
        assert tracker.memory_positions["mem-1"] == 3

    def test_record_restoration_no_new_retrieval(self):
        """Should record position but not add to retrieved_ids for restoration."""
        tracker = MemoryContextTracker()
        tracker.record_memory_insertion("mem-1", position=5, is_new_retrieval=False)

        assert "mem-1" not in tracker.retrieved_ids
        assert tracker.memory_positions["mem-1"] == 5

    def test_is_memory_in_context_present(self):
        """Should detect memory that is in context."""
        tracker = MemoryContextTracker()
        tracker.record_memory_insertion("mem-1", position=2, is_new_retrieval=True)

        assert tracker.is_memory_in_context("mem-1", context_length=5) is True

    def test_is_memory_in_context_not_present(self):
        """Should return False for unknown memory."""
        tracker = MemoryContextTracker()
        assert tracker.is_memory_in_context("mem-unknown", context_length=5) is False

    def test_is_memory_in_context_rolled_out(self):
        """Should detect memory that was rolled out (position -1)."""
        tracker = MemoryContextTracker()
        tracker.memory_positions["mem-1"] = -1
        assert tracker.is_memory_in_context("mem-1", context_length=5) is False

    def test_is_memory_in_context_beyond_length(self):
        """Should detect memory at position beyond context length."""
        tracker = MemoryContextTracker()
        tracker.memory_positions["mem-1"] = 10
        assert tracker.is_memory_in_context("mem-1", context_length=5) is False

    def test_get_in_context_memory_ids(self):
        """Should return set of in-context memory IDs."""
        tracker = MemoryContextTracker()
        tracker.record_memory_insertion("mem-1", position=0, is_new_retrieval=True)
        tracker.record_memory_insertion("mem-2", position=2, is_new_retrieval=True)
        tracker.record_memory_insertion("mem-3", position=4, is_new_retrieval=True)
        tracker.memory_positions["mem-4"] = -1  # Rolled out

        in_context = tracker.get_in_context_memory_ids(context_length=5)
        assert in_context == {"mem-1", "mem-2", "mem-3"}
        assert "mem-4" not in in_context

    def test_handle_context_rollout_marks_rolled_out(self):
        """Should mark memories as rolled out when context is trimmed."""
        tracker = MemoryContextTracker()
        tracker.record_memory_insertion("mem-1", position=0, is_new_retrieval=True)
        tracker.record_memory_insertion("mem-2", position=1, is_new_retrieval=True)
        tracker.record_memory_insertion("mem-3", position=4, is_new_retrieval=True)

        rolled_out = tracker.handle_context_rollout(
            num_messages_removed=2,
            conversation_context=[],  # After removal
        )

        assert "mem-1" in rolled_out
        assert "mem-2" in rolled_out
        assert "mem-3" not in rolled_out

    def test_handle_context_rollout_shifts_positions(self):
        """Should shift remaining memory positions after rollout."""
        tracker = MemoryContextTracker()
        tracker.record_memory_insertion("mem-1", position=0, is_new_retrieval=True)
        tracker.record_memory_insertion("mem-2", position=3, is_new_retrieval=True)
        tracker.record_memory_insertion("mem-3", position=5, is_new_retrieval=True)

        tracker.handle_context_rollout(
            num_messages_removed=2,
            conversation_context=[],
        )

        # mem-1 at position 0: rolled out -> -1
        assert tracker.memory_positions["mem-1"] == -1
        # mem-2 at position 3: shifted to 1
        assert tracker.memory_positions["mem-2"] == 1
        # mem-3 at position 5: shifted to 3
        assert tracker.memory_positions["mem-3"] == 3

    def test_handle_context_rollout_skips_already_rolled_out(self):
        """Should skip memories already marked as rolled out."""
        tracker = MemoryContextTracker()
        tracker.memory_positions["mem-1"] = -1  # Already rolled out
        tracker.record_memory_insertion("mem-2", position=2, is_new_retrieval=True)

        rolled_out = tracker.handle_context_rollout(
            num_messages_removed=1,
            conversation_context=[],
        )

        assert "mem-1" not in rolled_out
        assert tracker.memory_positions["mem-1"] == -1

    def test_check_memory_status_never_seen(self):
        """Should return (False, False) for never-seen memory."""
        tracker = MemoryContextTracker()
        already_retrieved, in_context = tracker.check_memory_status("new-mem", context_length=5)
        assert already_retrieved is False
        assert in_context is False

    def test_check_memory_status_in_context(self):
        """Should return (True, True) for memory in context."""
        tracker = MemoryContextTracker()
        tracker.record_memory_insertion("mem-1", position=2, is_new_retrieval=True)

        already_retrieved, in_context = tracker.check_memory_status("mem-1", context_length=5)
        assert already_retrieved is True
        assert in_context is True

    def test_check_memory_status_rolled_out(self):
        """Should return (True, False) for rolled-out memory."""
        tracker = MemoryContextTracker()
        tracker.retrieved_ids.add("mem-1")
        tracker.memory_positions["mem-1"] = -1

        already_retrieved, in_context = tracker.check_memory_status("mem-1", context_length=5)
        assert already_retrieved is True
        assert in_context is False


# ============================================================
# Tests for find_memory_insertion_point
# ============================================================

class TestFindMemoryInsertionPoint:
    """Tests for finding the memory insertion point in context."""

    def test_empty_context(self):
        """Should return 0 for empty context."""
        assert find_memory_insertion_point([]) == 0

    def test_inserts_at_end(self):
        """Should insert at end of context."""
        context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        assert find_memory_insertion_point(context) == 2

    def test_longer_context(self):
        """Should return length for longer context."""
        context = [
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "3"},
            {"role": "assistant", "content": "4"},
        ]
        assert find_memory_insertion_point(context) == 4
