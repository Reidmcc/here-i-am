"""
Tests for helper functions in routes/chat.py.

Covers assistant_token_count (persisted assistant messages should carry the
provider's exact output_tokens when it maps 1:1 to the content, falling back
to the local tiktoken estimate otherwise) and make_turn_timestamper (a turn's
rows must read back in the order they were written).
"""

from unittest.mock import patch

from app.routes.chat import assistant_token_count, make_turn_timestamper


class TestAssistantTokenCount:
    """Tests for choosing between provider-exact and estimated token counts."""

    def test_uses_provider_output_tokens(self):
        """With usage and no tool exchanges, the provider's count wins."""
        result = assistant_token_count("Hello there", {"output_tokens": 123})
        assert result == 123

    def test_falls_back_without_usage(self):
        """Without usage data, fall back to the local estimate."""
        with patch("app.routes.chat.llm_service") as mock_llm:
            mock_llm.count_tokens.return_value = 7
            assert assistant_token_count("Hello there", None) == 7
            assert assistant_token_count("Hello there", {}) == 7

    def test_falls_back_on_zero_output_tokens(self):
        """Providers that report 0 output tokens (e.g. Google sometimes) fall back."""
        with patch("app.routes.chat.llm_service") as mock_llm:
            mock_llm.count_tokens.return_value = 7
            assert assistant_token_count("Hello", {"output_tokens": 0}) == 7

    def test_falls_back_with_tool_exchanges(self):
        """With tool exchanges the content spans several API calls, so the
        final call's output_tokens doesn't cover it - use the estimate."""
        with patch("app.routes.chat.llm_service") as mock_llm:
            mock_llm.count_tokens.return_value = 7
            exchanges = [{"assistant": {}, "user": {}}]
            result = assistant_token_count("Hello", {"output_tokens": 123}, exchanges)
            assert result == 7


class TestMakeTurnTimestamper:
    """Tests for deterministic ordering of a turn's persisted rows."""

    def test_timestamps_strictly_increase(self):
        """Consecutive rows never share a created_at, so ORDER BY created_at
        cannot hand a tool_result back ahead of its tool_use."""
        next_time = make_turn_timestamper()

        stamps = [next_time() for _ in range(10)]

        assert stamps == sorted(stamps)
        assert len(set(stamps)) == len(stamps)

    def test_each_turn_starts_from_the_current_time(self):
        """Stamps stay anchored to now, so a turn sorts after earlier turns
        (and after the reflection rows memory_save wrote mid-stream)."""
        from datetime import datetime, timedelta

        before = datetime.utcnow()
        first = make_turn_timestamper()()
        after = datetime.utcnow()

        assert before <= first <= after + timedelta(milliseconds=1)
