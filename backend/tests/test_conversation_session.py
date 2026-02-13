"""
Tests for conversation_session.py - ConversationSession and MemoryEntry dataclasses.

Tests cover:
- MemoryEntry: Dataclass initialization
- ConversationSession: Legacy memory system (add_memory, get_memories_for_injection, trim_memories_to_limit)
- ConversationSession: New memory-in-context system (insert_memory_into_context, get_in_context_memory_count)
- ConversationSession: Shared methods (add_exchange, get_cache_aware_content, should_consolidate_cache,
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
# Tests for ConversationSession - Legacy Memory System
# ============================================================

class TestConversationSessionLegacyMemory:
    """Tests for the legacy memory block system in ConversationSession."""

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

    def test_add_new_memory(self):
        """Should add a new memory and return (True, True)."""
        session = ConversationSession(conversation_id="conv-1")
        memory = self._make_memory()

        added, is_new = session.add_memory(memory)
        assert added is True
        assert is_new is True
        assert "mem-1" in session.retrieved_ids
        assert "mem-1" in session.in_context_ids
        assert "mem-1" in session.session_memories

    def test_add_duplicate_memory(self):
        """Should not re-add memory already in context."""
        session = ConversationSession(conversation_id="conv-1")
        memory = self._make_memory()

        session.add_memory(memory)
        added, is_new = session.add_memory(memory)
        assert added is False
        assert is_new is False

    def test_restore_trimmed_memory(self):
        """Should restore trimmed memory without new retrieval."""
        session = ConversationSession(conversation_id="conv-1")
        memory = self._make_memory()

        # Add, then simulate trimming
        session.add_memory(memory)
        session.in_context_ids.discard("mem-1")

        # Re-add (restore)
        added, is_new = session.add_memory(memory)
        assert added is True
        assert is_new is False  # Not a new retrieval
        assert "mem-1" in session.in_context_ids

    def test_get_memories_for_injection_empty(self):
        """Should return empty list when no memories."""
        session = ConversationSession(conversation_id="conv-1")
        assert session.get_memories_for_injection() == []

    def test_get_memories_for_injection_sorted(self):
        """Should return memories sorted by ID for cache stability."""
        session = ConversationSession(conversation_id="conv-1")
        session.add_memory(self._make_memory("mem-b", content="B"))
        session.add_memory(self._make_memory("mem-a", content="A"))

        memories = session.get_memories_for_injection()
        assert len(memories) == 2
        assert memories[0]["id"] == "mem-a"
        assert memories[1]["id"] == "mem-b"

    def test_get_memories_excludes_trimmed(self):
        """Should exclude trimmed memories from injection."""
        session = ConversationSession(conversation_id="conv-1")
        session.add_memory(self._make_memory("mem-1"))
        session.add_memory(self._make_memory("mem-2"))
        session.in_context_ids.discard("mem-1")

        memories = session.get_memories_for_injection()
        assert len(memories) == 1
        assert memories[0]["id"] == "mem-2"

    def test_trim_memories_to_limit(self):
        """Should trim oldest memories when over token limit."""
        session = ConversationSession(conversation_id="conv-1")
        session.add_memory(self._make_memory("mem-1", content="First memory"))
        session.add_memory(self._make_memory("mem-2", content="Second memory"))
        session.add_memory(self._make_memory("mem-3", content="Third memory"))

        # Mock token counter that returns a high count initially
        call_count = [0]
        def mock_count(text):
            call_count[0] += 1
            # Return high on first call, lower after removing memories
            if "mem-1" in str(session.in_context_ids) and "mem-2" in str(session.in_context_ids) and "mem-3" in str(session.in_context_ids):
                return 1000  # Over limit
            return 5  # Under limit

        removed = session.trim_memories_to_limit(max_tokens=50, count_tokens_fn=mock_count)
        assert len(removed) > 0
        # First memory should be removed (FIFO order)
        assert "mem-1" in removed


# ============================================================
# Tests for ConversationSession - New Memory-in-Context System
# ============================================================

class TestConversationSessionMemoryInContext:
    """Tests for the new memory-in-context system."""

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
        session = ConversationSession(conversation_id="conv-1", use_memory_in_context=True)
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
        session = ConversationSession(conversation_id="conv-1", use_memory_in_context=True)
        session.conversation_context = [
            {"role": "user", "content": "Hello"},
        ]
        memory = self._make_memory()

        session.insert_memory_into_context(memory)
        inserted, is_new = session.insert_memory_into_context(memory)
        assert inserted is False
        assert is_new is False

    def test_get_in_context_memory_count_legacy(self):
        """Should count using legacy system when not using memory-in-context."""
        session = ConversationSession(
            conversation_id="conv-1",
            use_memory_in_context=False,
        )
        session.in_context_ids = {"mem-1", "mem-2"}
        assert session.get_in_context_memory_count() == 2

    def test_get_in_context_memory_count_new_system(self):
        """Should count using tracker when using memory-in-context."""
        session = ConversationSession(
            conversation_id="conv-1",
            use_memory_in_context=True,
        )
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
    """Tests for shared methods that work with both memory systems."""

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

    def test_should_consolidate_cache_empty_context(self):
        """Should not consolidate empty context."""
        session = ConversationSession(conversation_id="conv-1")
        assert session.should_consolidate_cache(lambda x: len(x)) is False

    def test_should_consolidate_cache_no_new_context(self):
        """Should not consolidate when there's no new context."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [{"role": "user", "content": "Hello"}]
        session.last_cached_context_length = 1
        assert session.should_consolidate_cache(lambda x: len(x)) is False

    def test_should_consolidate_cache_small_cached(self):
        """Should consolidate when cached context is too small (< 1024 tokens)."""
        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context = [
            {"role": "user", "content": "Short"},
            {"role": "assistant", "content": "Also short"},
            {"role": "user", "content": "New message"},
        ]
        session.last_cached_context_length = 2
        # Token counter returns small numbers
        assert session.should_consolidate_cache(lambda x: 100) is True

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
