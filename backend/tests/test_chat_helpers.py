"""
Tests for helper functions in routes/chat.py.

Covers assistant_token_count (persisted assistant messages should carry the
provider's exact output_tokens when it maps 1:1 to the content, falling back
to the local tiktoken estimate otherwise) and build_done_event_payload (the
done event carries persistence-only keys the client never reads).
"""

from unittest.mock import patch

from app.routes.chat import assistant_token_count, build_done_event_payload


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


class TestBuildDoneEventPayload:
    """Tests for trimming the done event before it goes on the wire."""

    def test_strips_persistence_only_keys(self):
        """tool_exchanges/tool_uses are for the route's own bookkeeping."""
        event = {
            "type": "done",
            "content": "Here you go.",
            "model": "claude-sonnet-4-5-20250929",
            "usage": {"output_tokens": 12},
            "stop_reason": "end_turn",
            "tool_exchanges": [{"assistant": {}, "user": {}}],
            "tool_uses": [{"name": "web_search"}],
        }

        payload = build_done_event_payload(event)

        assert "tool_exchanges" not in payload
        assert "tool_uses" not in payload
        # Everything the client actually reads survives.
        assert payload == {
            "type": "done",
            "content": "Here you go.",
            "model": "claude-sonnet-4-5-20250929",
            "usage": {"output_tokens": 12},
            "stop_reason": "end_turn",
        }

    def test_leaves_the_source_event_intact(self):
        """The route still needs tool_exchanges to persist the exchange."""
        event = {"type": "done", "content": "hi", "tool_exchanges": [{"assistant": {}}]}

        build_done_event_payload(event)

        assert event["tool_exchanges"] == [{"assistant": {}}]

    def test_passes_through_a_toolless_turn(self):
        event = {"type": "done", "content": "hi", "usage": {}}
        assert build_done_event_payload(event) == event
