"""
Tests for thinking effort: the canonical scale and its per-provider translation.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.config import settings
from app.services.thinking_effort import (
    EFFORT_LEVELS,
    clamp_effort,
    normalize_effort,
    resolve_effort,
)
from app.services.anthropic_service import (
    AnthropicService,
    resolve_effort_for_model,
    supported_effort_levels,
    supports_thinking_effort,
)
from app.services.openai_service import OpenAIService


def _empty_async_iterator():
    class _Empty:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    return _Empty()


class TestCanonicalScale:
    """The provider-neutral level vocabulary."""

    def test_levels_are_ordered_low_to_high(self):
        assert EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")

    @pytest.mark.parametrize("value", ["low", "MEDIUM", " high ", "xhigh", "max"])
    def test_normalize_accepts_known_levels(self, value):
        assert normalize_effort(value) == value.strip().lower()

    @pytest.mark.parametrize("value", [None, "", "   ", "ludicrous", 3, object()])
    def test_normalize_rejects_everything_else(self, value):
        assert normalize_effort(value) is None

    def test_resolve_falls_back_to_configured_default(self):
        with patch.object(settings, "default_thinking_effort", "medium"):
            assert resolve_effort(None) == "medium"
            assert resolve_effort("low") == "low"

    def test_resolve_survives_a_bad_configured_default(self):
        """A typo in .env should not take every turn down with it."""
        with patch.object(settings, "default_thinking_effort", "enormous"):
            assert resolve_effort(None) == "high"

    def test_default_is_high(self):
        assert settings.default_thinking_effort == "high"

    def test_clamp_lowers_to_the_model_ceiling(self):
        assert clamp_effort("max", ("low", "medium", "high")) == "high"
        assert clamp_effort("xhigh", ("low", "medium", "high", "max")) == "high"

    def test_clamp_keeps_supported_levels_untouched(self):
        assert clamp_effort("medium", EFFORT_LEVELS) == "medium"

    def test_clamp_returns_none_when_nothing_is_supported(self):
        assert clamp_effort("high", ()) is None


class TestAnthropicEffortLadders:
    """Which Claude models take `output_config.effort`, and at what levels."""

    @pytest.mark.parametrize("model", [
        "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
        "claude-sonnet-5", "claude-fable-5",
    ])
    def test_current_models_take_the_full_ladder(self, model):
        assert supported_effort_levels(model) == EFFORT_LEVELS
        assert resolve_effort_for_model(model, "max") == "max"

    def test_4_6_generation_has_no_xhigh(self):
        """xhigh arrived with Opus 4.7, so a request for it clamps to high."""
        assert resolve_effort_for_model("claude-opus-4-6", "xhigh") == "high"
        assert resolve_effort_for_model("claude-sonnet-4-6", "max") == "max"

    def test_opus_4_5_tops_out_at_high(self):
        assert resolve_effort_for_model("claude-opus-4-5-20251101", "max") == "high"

    @pytest.mark.parametrize("model", [
        "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001",
        "claude-opus-4-20250514", "MiniMax-M2.5",
    ])
    def test_models_without_effort_get_nothing(self, model):
        """These 400 on the parameter, so nothing is sent for them."""
        assert supports_thinking_effort(model) is False
        assert resolve_effort_for_model(model, "high") is None

    def test_date_suffixed_names_match_by_prefix(self):
        assert supports_thinking_effort("claude-opus-4-7-20260101") is True


class TestAnthropicRequests:
    """The effort actually placed on outgoing Anthropic requests."""

    def _streaming_service(self):
        service = AnthropicService()
        stream_ctx = MagicMock()
        stream_ctx.__aenter__ = AsyncMock(return_value=_empty_async_iterator())
        stream_ctx.__aexit__ = AsyncMock(return_value=False)
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=stream_ctx)
        service.client = client
        service._minimax_client = client
        return service, client

    @pytest.mark.asyncio
    async def test_send_message_sends_effort(self, mock_anthropic_client):
        service = AnthropicService()
        service.client = mock_anthropic_client

        await service.send_message(
            [{"role": "user", "content": "Hello!"}],
            model="claude-opus-5",
            thinking_effort="low",
        )

        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {"output_config": {"effort": "low"}}

    @pytest.mark.asyncio
    async def test_send_message_uses_the_default_when_unset(self, mock_anthropic_client):
        service = AnthropicService()
        service.client = mock_anthropic_client

        await service.send_message(
            [{"role": "user", "content": "Hello!"}], model="claude-opus-5"
        )

        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {"output_config": {"effort": "high"}}

    @pytest.mark.asyncio
    async def test_send_message_omits_effort_for_older_models(self, mock_anthropic_client):
        service = AnthropicService()
        service.client = mock_anthropic_client

        await service.send_message(
            [{"role": "user", "content": "Hello!"}],
            model="claude-sonnet-4-5-20250929",
            thinking_effort="high",
        )

        assert "extra_body" not in mock_anthropic_client.messages.create.call_args.kwargs

    @pytest.mark.asyncio
    async def test_stream_sends_effort(self):
        service, client = self._streaming_service()

        async for _ in service.send_message_stream(
            [], model="claude-fable-5", thinking_effort="xhigh"
        ):
            pass

        assert client.messages.stream.call_args.kwargs["extra_body"] == {
            "output_config": {"effort": "xhigh"}
        }

    @pytest.mark.asyncio
    async def test_stream_clamps_to_the_model_ceiling(self):
        service, client = self._streaming_service()

        async for _ in service.send_message_stream(
            [], model="claude-opus-4-5-20251101", thinking_effort="max"
        ):
            pass

        assert client.messages.stream.call_args.kwargs["extra_body"] == {
            "output_config": {"effort": "high"}
        }

    @pytest.mark.asyncio
    async def test_stream_omits_effort_for_minimax(self):
        """MiniMax speaks the Anthropic format but rejects output_config."""
        service, client = self._streaming_service()

        async for _ in service.send_message_stream(
            [], model="claude-fable-5", provider="minimax", thinking_effort="high"
        ):
            pass

        assert "extra_body" not in client.messages.stream.call_args.kwargs

    @pytest.mark.asyncio
    async def test_effort_and_thinking_display_coexist(self):
        """Effort controls depth, display controls visibility — both are sent."""
        service, client = self._streaming_service()

        async for _ in service.send_message_stream(
            [], model="claude-fable-5", thinking_effort="medium"
        ):
            pass

        call_kwargs = client.messages.stream.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert call_kwargs["extra_body"] == {"output_config": {"effort": "medium"}}


class TestOpenAIReasoningEffort:
    """Translation onto OpenAI's shorter reasoning_effort ladder."""

    def test_levels_above_high_collapse_onto_high(self):
        service = OpenAIService()
        assert service._reasoning_effort_for("gpt-5.1", "xhigh") == "high"
        assert service._reasoning_effort_for("gpt-5.1", "max") == "high"
        assert service._reasoning_effort_for("gpt-5.1", "low") == "low"

    def test_non_reasoning_models_get_nothing(self):
        service = OpenAIService()
        assert service._reasoning_effort_for("gpt-4o", "high") is None
        assert service._reasoning_effort_for("gpt-5.1-chat-latest", "high") is None

    def test_unset_effort_uses_the_configured_default(self):
        service = OpenAIService()
        with patch.object(settings, "default_thinking_effort", "low"):
            assert service._reasoning_effort_for("o3", None) == "low"

    @pytest.mark.asyncio
    async def test_send_message_sends_reasoning_effort(self):
        service = OpenAIService()
        client = MagicMock()
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content="hi", tool_calls=None), finish_reason="stop")]
        response.usage = MagicMock(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=None)
        response.model = "gpt-5.1"
        client.chat.completions.create = AsyncMock(return_value=response)
        service.client = client

        await service.send_message(
            [{"role": "user", "content": "Hello!"}],
            model="gpt-5.1",
            thinking_effort="medium",
        )

        assert client.chat.completions.create.call_args.kwargs["reasoning_effort"] == "medium"

    @pytest.mark.asyncio
    async def test_send_message_omits_reasoning_effort_for_gpt_4o(self):
        service = OpenAIService()
        client = MagicMock()
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content="hi", tool_calls=None), finish_reason="stop")]
        response.usage = MagicMock(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=None)
        response.model = "gpt-4o"
        client.chat.completions.create = AsyncMock(return_value=response)
        service.client = client

        await service.send_message(
            [{"role": "user", "content": "Hello!"}],
            model="gpt-4o",
            thinking_effort="high",
        )

        assert "reasoning_effort" not in client.chat.completions.create.call_args.kwargs


def _google_module():
    """
    The google_service *module*, not the singleton instance.

    `app.services` exports an instance under the same name, so the usual
    `from app.services import google_service` import shadows the module.
    """
    import sys

    return sys.modules["app.services.google_service"]


class TestGoogleThinkingConfig:
    """Translation onto Gemini's thinking level/budget, probed per SDK version."""

    def test_only_thinking_models_get_a_config(self):
        service = _google_module().GoogleService()
        assert service.supports_thinking("gemini-3.0-pro") is True
        assert service.supports_thinking("gemini-2.5-flash") is True
        assert service.supports_thinking("gemini-2.0-flash") is False
        assert service._build_thinking_config("gemini-2.0-flash", "high") is None

    def test_prefers_thinking_level_when_the_sdk_exposes_it(self):
        module = _google_module()
        service = module.GoogleService()
        fake_config = MagicMock()
        fake_types = MagicMock()
        fake_types.ThinkingConfig = MagicMock(
            model_fields={"thinking_level": object()},
            return_value=fake_config,
        )
        with patch.object(module, "types", fake_types):
            result = service._build_thinking_config("gemini-3.0-pro", "max")

        assert result is fake_config
        fake_types.ThinkingConfig.assert_called_once_with(thinking_level="high")

    def test_falls_back_to_thinking_budget(self):
        module = _google_module()
        service = module.GoogleService()
        fake_config = MagicMock()
        fake_types = MagicMock()
        fake_types.ThinkingConfig = MagicMock(
            model_fields={"thinking_budget": object()},
            return_value=fake_config,
        )
        with patch.object(module, "types", fake_types):
            result = service._build_thinking_config("gemini-2.5-flash", "low")

        assert result is fake_config
        fake_types.ThinkingConfig.assert_called_once_with(
            thinking_budget=module.GoogleService.THINKING_BUDGET_MAP["low"]
        )

    def test_sends_nothing_when_the_sdk_has_neither_field(self):
        """The pinned floor (google-genai>=1.0.0) predates both spellings."""
        module = _google_module()
        service = module.GoogleService()
        fake_types = MagicMock()
        fake_types.ThinkingConfig = MagicMock(model_fields={"include_thoughts": object()})
        with patch.object(module, "types", fake_types):
            assert service._build_thinking_config("gemini-2.5-flash", "high") is None

    def test_a_broken_sdk_costs_a_log_line_not_the_turn(self):
        module = _google_module()
        service = module.GoogleService()
        config = MagicMock()
        config.thinking_config = None
        fake_types = MagicMock()
        fake_types.ThinkingConfig = MagicMock(
            model_fields={"thinking_level": object()},
            side_effect=TypeError("unexpected keyword"),
        )
        with patch.object(module, "types", fake_types):
            service._apply_thinking_config(config, "gemini-3.0-pro", "high")

        assert config.thinking_config is None


class TestModelCapabilityFlags:
    """The per-model flag the settings UI uses to show/hide the control."""

    def test_flag_matches_provider_support(self):
        from app.services.llm_service import llm_service

        providers = [
            {
                "id": "anthropic",
                "name": "Anthropic",
                "models": [
                    {"id": "claude-opus-5", "name": "Claude Opus 5"},
                    {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
                ],
                "default_model": "claude-opus-5",
            },
            {
                "id": "openai",
                "name": "OpenAI",
                "models": [
                    {"id": "gpt-5.1", "name": "GPT-5.1"},
                    {"id": "gpt-4o", "name": "GPT-4o"},
                ],
                "default_model": "gpt-5.1",
            },
            {
                "id": "google",
                "name": "Google",
                "models": [
                    {"id": "gemini-3.0-pro", "name": "Gemini 3.0 Pro"},
                    {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
                ],
                "default_model": "gemini-3.0-pro",
            },
        ]
        with patch.object(llm_service, "get_available_providers", return_value=providers):
            flags = {
                m["id"]: m["thinking_effort_supported"]
                for m in llm_service.get_all_available_models()
            }

        assert flags == {
            "claude-opus-5": True,
            "claude-haiku-4-5-20251001": False,
            "gpt-5.1": True,
            "gpt-4o": False,
            "gemini-3.0-pro": True,
            "gemini-2.0-flash": False,
        }


class TestSessionEntityEffort:
    """The per-entity setting reaching the session that makes the API call."""

    class _StubDB:
        """Minimal stand-in for AsyncSession.get(EntitySetting, entity_id)."""

        def __init__(self, rows):
            self.rows = rows

        async def get(self, model, key):
            return self.rows.get(key)

    def _session(self, entity_id):
        from app.services.conversation_session import ConversationSession

        return ConversationSession(conversation_id="conv-1", entity_id=entity_id)

    @pytest.mark.asyncio
    async def test_reads_the_responding_entitys_effort(self):
        from app.models import EntitySetting
        from app.services.session_manager import SessionManager

        db = self._StubDB({"claude-test": EntitySetting(entity_id="claude-test", thinking_effort="max")})
        session = self._session("claude-test")

        await SessionManager().refresh_thinking_effort(session, db)

        assert session.thinking_effort == "max"

    @pytest.mark.asyncio
    async def test_entity_without_a_setting_stays_on_the_default(self):
        from app.services.session_manager import SessionManager

        session = self._session("claude-test")
        session.thinking_effort = "low"  # stale value from a previous entity

        await SessionManager().refresh_thinking_effort(session, self._StubDB({}))

        # None means "resolve to settings.default_thinking_effort" downstream
        assert session.thinking_effort is None

    @pytest.mark.asyncio
    async def test_follows_the_entity_switch_in_a_multi_entity_turn(self):
        """Each responder brings its own effort, turn to turn."""
        from app.models import EntitySetting
        from app.services.session_manager import SessionManager

        db = self._StubDB({
            "claude-test": EntitySetting(entity_id="claude-test", thinking_effort="max"),
            "gpt-test": EntitySetting(entity_id="gpt-test", thinking_effort="low"),
        })
        manager = SessionManager()
        session = self._session("claude-test")

        await manager.refresh_thinking_effort(session, db)
        assert session.thinking_effort == "max"

        session.entity_id = "gpt-test"
        await manager.refresh_thinking_effort(session, db)
        assert session.thinking_effort == "low"

    @pytest.mark.asyncio
    async def test_no_entity_means_no_lookup(self):
        from app.services.session_manager import SessionManager

        class _ExplodingDB:
            async def get(self, model, key):  # pragma: no cover - must not run
                raise AssertionError("should not query without an entity")

        session = self._session(None)
        await SessionManager().refresh_thinking_effort(session, _ExplodingDB())

        assert session.thinking_effort is None
