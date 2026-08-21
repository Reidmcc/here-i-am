"""
Unit tests for AnthropicService.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import settings
from app.services.anthropic_service import (
    AnthropicService,
    supports_adaptive_thinking,
    supports_temperature,
)


def _sdk_event(event_type: str, **fields):
    """Build a stand-in for an SDK stream event."""
    return SimpleNamespace(type=event_type, **fields)


def _text_delta(text: str):
    return _sdk_event("content_block_delta", delta=SimpleNamespace(text=text))


def _thinking_delta(text: str):
    return _sdk_event("content_block_delta", delta=SimpleNamespace(thinking=text))


def _message_delta(stop_reason=None, output_tokens=0):
    return _sdk_event(
        "message_delta",
        delta=SimpleNamespace(stop_reason=stop_reason),
        usage=SimpleNamespace(output_tokens=output_tokens),
    )


def _stream_ctx(events, raise_after=None, error=None):
    """
    An async context manager yielding `events`, optionally raising partway.

    `raise_after` is the number of events to deliver before raising `error`,
    which simulates a connection dying mid-body — the failure mode the SDK
    does not retry for us.
    """

    class _Stream:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for i, event in enumerate(events):
                if raise_after is not None and i == raise_after:
                    raise error
                yield event
            if raise_after is not None and raise_after >= len(events):
                raise error

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=_Stream())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _empty_async_iterator():
    """An async-iterable stream that yields no events."""
    class _Empty:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    return _Empty()


@pytest.fixture
def mock_encoder():
    """Create a mock encoder that returns predictable token counts."""
    mock = MagicMock()
    # Return a list with length based on word count approximation
    mock.encode.side_effect = lambda text: list(range(len(text.split())))
    return mock


class TestAnthropicService:
    """Tests for AnthropicService class."""

    def test_count_tokens(self):
        """Test token counting."""
        service = AnthropicService()

        # Mock the encoder to avoid network call
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = [1, 2, 3, 4]  # 4 tokens
        service._encoder = mock_encoder

        # Count tokens for a simple string
        count = service.count_tokens("Hello, world!")

        assert isinstance(count, int)
        assert count == 4
        mock_encoder.encode.assert_called_once_with("Hello, world!")

    def test_count_tokens_empty_string(self):
        """Test token counting for empty string."""
        service = AnthropicService()

        # Mock the encoder
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = []  # 0 tokens
        service._encoder = mock_encoder

        count = service.count_tokens("")

        assert count == 0

    def test_count_tokens_long_text(self):
        """Test token counting for longer text."""
        service = AnthropicService()

        # Mock the encoder
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(150))  # 150 tokens
        service._encoder = mock_encoder

        long_text = "This is a test. " * 100
        count = service.count_tokens(long_text)

        assert count == 150

    def test_encoder_lazy_initialization(self):
        """Test that encoder is lazily initialized."""
        service = AnthropicService()

        # Before accessing, _encoder should be None
        assert service._encoder is None

        # Mock tiktoken to avoid network call
        with patch("app.services.anthropic_service.tiktoken") as mock_tiktoken:
            mock_encoder = MagicMock()
            mock_tiktoken.get_encoding.return_value = mock_encoder

            # After accessing, encoder should be initialized
            _ = service.encoder
            assert service._encoder is mock_encoder
            mock_tiktoken.get_encoding.assert_called_once_with("cl100k_base")

    @pytest.mark.asyncio
    async def test_send_message_basic(self, mock_anthropic_client):
        """Test basic message sending."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        response = await service.send_message(messages)

        assert response["content"] == "This is a test response from Claude."
        assert response["model"] == "claude-sonnet-4-5-20250929"
        assert "usage" in response
        assert response["usage"]["input_tokens"] == 100
        assert response["usage"]["output_tokens"] == 50

    @pytest.mark.asyncio
    async def test_send_message_with_system_prompt(self, mock_anthropic_client):
        """Test message sending with system prompt - now includes cache_control."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(
            messages,
            system_prompt="You are a helpful assistant.",
        )

        # Verify system prompt was passed with cache_control format
        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert "system" in call_kwargs
        # With caching enabled, system prompt is wrapped in content array format
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert system[0]["type"] == "text"
        assert system[0]["text"] == "You are a helpful assistant."
        assert "cache_control" in system[0]

    @pytest.mark.asyncio
    async def test_send_message_with_system_prompt_no_caching(self, mock_anthropic_client):
        """Test message sending with system prompt when caching disabled."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(
            messages,
            system_prompt="You are a helpful assistant.",
            enable_caching=False,
        )

        # Verify system prompt was passed as plain string
        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_send_message_no_system_prompt(self, mock_anthropic_client):
        """Test message sending without system prompt."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(messages, system_prompt=None)

        # Verify system prompt was NOT passed to API
        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert "system" not in call_kwargs

    @pytest.mark.asyncio
    async def test_send_message_custom_parameters(self, mock_anthropic_client):
        """Test message sending with custom parameters."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(
            messages,
            model="claude-opus-4-20250514",
            temperature=0.5,
            max_tokens=2000,
        )

        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-20250514"
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["max_tokens"] == 2000

    @pytest.mark.asyncio
    async def test_send_message_omits_temperature_for_opus_5(self, mock_anthropic_client):
        """Claude Opus 5 rejects sampling params, so temperature must not be sent."""
        service = AnthropicService()
        service.client = mock_anthropic_client

        messages = [{"role": "user", "content": "Hello!"}]
        await service.send_message(messages, model="claude-opus-5", temperature=0.5)

        call_kwargs = mock_anthropic_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-5"
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_send_message_stream_omits_temperature_for_opus_5(self):
        """The streaming path applies the same temperature rule as send_message."""
        service = AnthropicService()

        stream_ctx = MagicMock()
        stream_ctx.__aenter__ = AsyncMock(return_value=_empty_async_iterator())
        stream_ctx.__aexit__ = AsyncMock(return_value=False)
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=stream_ctx)
        service.client = client

        messages = [{"role": "user", "content": "Hello!"}]
        async for _ in service.send_message_stream(
            messages, model="claude-opus-5", temperature=0.5
        ):
            pass

        call_kwargs = client.messages.stream.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-5"
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_send_message_stream_keeps_temperature_for_older_models(self):
        """Older Claude models still accept temperature."""
        service = AnthropicService()

        stream_ctx = MagicMock()
        stream_ctx.__aenter__ = AsyncMock(return_value=_empty_async_iterator())
        stream_ctx.__aexit__ = AsyncMock(return_value=False)
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=stream_ctx)
        service.client = client

        messages = [{"role": "user", "content": "Hello!"}]
        async for _ in service.send_message_stream(
            messages, model="claude-sonnet-4-5-20250929", temperature=0.5
        ):
            pass

        call_kwargs = client.messages.stream.call_args.kwargs
        assert call_kwargs["temperature"] == 0.5


class TestThinkingSummaries:
    """The thinking parameter and the display-only events it produces."""

    def _service_with(self, events, side_effect=None):
        service = AnthropicService()
        client = MagicMock()
        if side_effect is not None:
            client.messages.stream = MagicMock(side_effect=side_effect)
        else:
            client.messages.stream = MagicMock(return_value=_stream_ctx(events))
        service.client = client
        service._minimax_client = client
        return service, client

    @pytest.mark.asyncio
    async def test_requests_summarized_thinking_for_adaptive_models(self):
        service, client = self._service_with([])
        async for _ in service.send_message_stream([], model="claude-fable-5"):
            pass

        assert client.messages.stream.call_args.kwargs["thinking"] == {
            "type": "adaptive",
            "display": "summarized",
        }

    @pytest.mark.asyncio
    async def test_omits_thinking_for_pre_adaptive_models(self):
        """Sonnet 4.5 is the configured default and 400s on adaptive thinking."""
        service, client = self._service_with([])
        async for _ in service.send_message_stream(
            [], model="claude-sonnet-4-5-20250929"
        ):
            pass

        assert "thinking" not in client.messages.stream.call_args.kwargs

    @pytest.mark.asyncio
    async def test_omits_thinking_for_minimax(self):
        """MiniMax is Anthropic-compatible but rejects the thinking parameter."""
        service, client = self._service_with([])
        async for _ in service.send_message_stream(
            [], model="claude-fable-5", provider="minimax"
        ):
            pass

        assert "thinking" not in client.messages.stream.call_args.kwargs

    @pytest.mark.asyncio
    async def test_omits_thinking_when_disabled(self):
        service, client = self._service_with([])
        with patch.object(settings, "thinking_summaries_enabled", False):
            async for _ in service.send_message_stream([], model="claude-fable-5"):
                pass

        assert "thinking" not in client.messages.stream.call_args.kwargs

    @pytest.mark.asyncio
    async def test_emits_thinking_events_without_polluting_answer_text(self):
        """
        Reasoning reaches the caller on two independent channels.

        The thinking_* events are for display. The thinking block in
        content_blocks is what gets echoed back to the API on the next
        tool-loop iteration. Neither may leak into `content`, which becomes
        the stored assistant message.
        """
        events = [
            _sdk_event(
                "content_block_start",
                content_block=SimpleNamespace(type="thinking"),
            ),
            _thinking_delta("weighing the options"),
            _sdk_event("content_block_delta", delta=SimpleNamespace(signature="sig-1")),
            _sdk_event("content_block_stop"),
            _sdk_event(
                "content_block_start", content_block=SimpleNamespace(type="text")
            ),
            _text_delta("the answer"),
            _sdk_event("content_block_stop"),
            _message_delta(stop_reason="end_turn", output_tokens=5),
        ]
        service, _ = self._service_with(events)

        collected = [
            e async for e in service.send_message_stream([], model="claude-fable-5")
        ]

        # Display channel
        types = [e["type"] for e in collected]
        assert types.count("thinking_start") == 1
        assert types.count("thinking_stop") == 1
        assert [e["content"] for e in collected if e["type"] == "thinking"] == [
            "weighing the options"
        ]

        done = collected[-1]
        assert done["type"] == "done"
        # Answer text stays clean
        assert done["content"] == "the answer"
        # Echo channel: thinking block preserved in stream order, signature intact
        assert done["content_blocks"] == [
            {
                "type": "thinking",
                "thinking": "weighing the options",
                "signature": "sig-1",
            },
            {"type": "text", "text": "the answer"},
        ]


class TestMidStreamReconnect:
    """Retry behaviour when the connection dies mid-body."""

    @staticmethod
    def _client_with(*contexts):
        client = MagicMock()
        client.messages.stream = MagicMock(side_effect=list(contexts))
        return client

    @pytest.mark.asyncio
    async def test_retries_when_nothing_has_been_streamed(self):
        """A reset before any output is replayed rather than surfaced."""
        good = [_text_delta("recovered"), _message_delta(stop_reason="end_turn")]
        service = AnthropicService()
        service.client = self._client_with(
            _stream_ctx([], raise_after=0, error=httpx.ReadError("")),
            _stream_ctx(good),
        )

        with patch.object(settings, "anthropic_stream_retry_backoff", 0):
            collected = [
                e async for e in service.send_message_stream([], model="claude-fable-5")
            ]

        assert service.client.messages.stream.call_count == 2
        assert not any(e["type"] == "error" for e in collected)
        assert collected[-1]["content"] == "recovered"
        # "start" is emitted once, so the caller does not see two turns
        assert [e["type"] for e in collected].count("start") == 1

    @pytest.mark.asyncio
    async def test_does_not_retry_once_tokens_have_been_emitted(self):
        """Replaying after visible output would duplicate it in the transcript."""
        service = AnthropicService()
        service.client = self._client_with(
            _stream_ctx([_text_delta("partial")], raise_after=1, error=httpx.ReadError("")),
            _stream_ctx([_text_delta("would be a duplicate")]),
        )

        with patch.object(settings, "anthropic_stream_retry_backoff", 0):
            collected = [
                e async for e in service.send_message_stream([], model="claude-fable-5")
            ]

        assert service.client.messages.stream.call_count == 1
        assert collected[-1]["type"] == "error"

    @pytest.mark.asyncio
    async def test_closes_open_thinking_block_before_retrying(self):
        """A dead stream must not leave the caller with an unresolved spinner."""
        service = AnthropicService()
        service.client = self._client_with(
            _stream_ctx(
                [
                    _sdk_event(
                        "content_block_start",
                        content_block=SimpleNamespace(type="thinking"),
                    ),
                    _thinking_delta("mid-thought"),
                ],
                raise_after=2,
                error=httpx.ReadError(""),
            ),
            _stream_ctx([_text_delta("ok"), _message_delta(stop_reason="end_turn")]),
        )

        with patch.object(settings, "anthropic_stream_retry_backoff", 0):
            collected = [
                e async for e in service.send_message_stream([], model="claude-fable-5")
            ]

        types = [e["type"] for e in collected]
        assert types.count("thinking_start") == types.count("thinking_stop") == 1
        assert collected[-1]["content"] == "ok"

    @pytest.mark.asyncio
    async def test_retry_discards_thinking_blocks_from_the_dead_attempt(self):
        """
        Thinking blocks are echoed back to the API, so a replay must not mix
        blocks from the failed attempt with the successful one — a spliced
        sequence carries signatures the retried turn never produced, and the
        API rejects rearranged or partially dropped thinking sequences.
        """
        service = AnthropicService()
        service.client = self._client_with(
            _stream_ctx(
                [
                    _sdk_event(
                        "content_block_start",
                        content_block=SimpleNamespace(type="thinking"),
                    ),
                    _thinking_delta("abandoned reasoning"),
                    _sdk_event(
                        "content_block_delta",
                        delta=SimpleNamespace(signature="stale-sig"),
                    ),
                    _sdk_event("content_block_stop"),
                ],
                raise_after=4,
                error=httpx.ReadError(""),
            ),
            _stream_ctx(
                [
                    _sdk_event(
                        "content_block_start",
                        content_block=SimpleNamespace(type="thinking"),
                    ),
                    _thinking_delta("fresh reasoning"),
                    _sdk_event(
                        "content_block_delta",
                        delta=SimpleNamespace(signature="fresh-sig"),
                    ),
                    _sdk_event("content_block_stop"),
                    _sdk_event(
                        "content_block_start",
                        content_block=SimpleNamespace(type="text"),
                    ),
                    _text_delta("answer"),
                    _sdk_event("content_block_stop"),
                    _message_delta(stop_reason="end_turn"),
                ]
            ),
        )

        with patch.object(settings, "anthropic_stream_retry_backoff", 0):
            collected = [
                e async for e in service.send_message_stream([], model="claude-fable-5")
            ]

        done = collected[-1]
        assert done["type"] == "done"
        assert done["content_blocks"] == [
            {
                "type": "thinking",
                "thinking": "fresh reasoning",
                "signature": "fresh-sig",
            },
            {"type": "text", "text": "answer"},
        ]
        assert done["content"] == "answer"

    @pytest.mark.asyncio
    async def test_surfaces_named_error_when_retries_exhausted(self):
        """httpx.ReadError carries no message; the type must still reach the UI."""
        service = AnthropicService()
        service.client = self._client_with(
            *[
                _stream_ctx([], raise_after=0, error=httpx.ReadError(""))
                for _ in range(3)
            ]
        )

        with patch.object(settings, "anthropic_stream_max_retries", 2), patch.object(
            settings, "anthropic_stream_retry_backoff", 0
        ):
            collected = [
                e async for e in service.send_message_stream([], model="claude-fable-5")
            ]

        assert service.client.messages.stream.call_count == 3
        assert collected[-1]["type"] == "error"
        # Previously this surfaced as a bare "Error:" with nothing after it
        assert collected[-1]["error"] == "ReadError"

    @pytest.mark.asyncio
    async def test_non_transport_errors_are_not_retried(self):
        service = AnthropicService()
        service.client = self._client_with(
            _stream_ctx([], raise_after=0, error=ValueError("bad request")),
            _stream_ctx([_text_delta("unreachable")]),
        )

        collected = [
            e async for e in service.send_message_stream([], model="claude-fable-5")
        ]

        assert service.client.messages.stream.call_count == 1
        assert collected[-1]["error"] == "ValueError: bad request"


class TestSupportsAdaptiveThinking:
    """Tests for the adaptive thinking capability check."""

    @pytest.mark.parametrize(
        "model",
        [
            "claude-fable-5",
            "claude-opus-5",
            "claude-opus-4-8",
            "claude-sonnet-5",
            "claude-sonnet-4-6",
            "claude-opus-4-6-20251101",
        ],
    )
    def test_supported(self, model):
        assert supports_adaptive_thinking(model) is True

    @pytest.mark.parametrize(
        "model",
        [
            "claude-sonnet-4-5-20250929",
            "claude-haiku-4-5",
            "claude-opus-4-5",
            "claude-opus-4-1",
        ],
    )
    def test_unsupported(self, model):
        assert supports_adaptive_thinking(model) is False


class TestSupportsTemperature:
    """Tests for the temperature capability check."""

    def test_models_that_reject_temperature(self):
        for model in [
            "claude-opus-5",
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-sonnet-5",
            "claude-fable-5",
            "claude-mythos-5",
        ]:
            assert supports_temperature(model) is False

    def test_models_that_accept_temperature(self):
        for model in [
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-5-20251101",
            "claude-haiku-4-5-20251001",
            "claude-opus-4-6",
            "MiniMax-M2.5",
        ]:
            assert supports_temperature(model) is True

    def test_build_messages_with_context(self, mock_encoder):
        """Test building messages with conversation context."""
        service = AnthropicService()
        service._encoder = mock_encoder

        context = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        current = "How are you?"

        messages = service.build_messages(context, current)

        # Conversation-first structure:
        # 1. user: [CONVERSATION HISTORY] + Hi
        # 2. assistant: Hello!
        # 3. user: [/CONVERSATION HISTORY] + [CURRENT USER MESSAGE] + date + current
        assert len(messages) == 3

        # First two messages: conversation history
        assert messages[0]["role"] == "user"
        assert "[CONVERSATION HISTORY]" in messages[0]["content"]
        assert "Hi" in messages[0]["content"]
        assert messages[1]["role"] == "assistant"

        # Third message: final combined message
        assert messages[2]["role"] == "user"
        assert "[/CONVERSATION HISTORY]" in messages[2]["content"]
        assert "[DATE CONTEXT]" in messages[2]["content"]
        assert "How are you?" in messages[2]["content"]

    def test_build_messages_no_caching(self, mock_encoder):
        """Test building messages with caching disabled."""
        service = AnthropicService()
        service._encoder = mock_encoder

        context = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        current = "How are you?"

        messages = service.build_messages(
            context, current, enable_caching=False
        )

        # Conversation-first structure (same as with caching):
        # 1. user: [CONVERSATION HISTORY] + Hi
        # 2. assistant: Hello!
        # 3. user: [/CONVERSATION HISTORY] + [CURRENT USER MESSAGE] + date + current
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert "[CONVERSATION HISTORY]" in messages[0]["content"]
        assert "Hi" in messages[0]["content"]
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"
        assert "How are you?" in messages[2]["content"]

    def test_build_messages_attachment_only_is_current_message(self, mock_encoder):
        """An attachment-only message (no text) is a real message, not a continuation."""
        service = AnthropicService()
        service._encoder = mock_encoder

        attachments = {
            "images": [],
            "files": [{
                "filename": "notes.txt",
                "content": "FILE_BODY_MARKER",
                "content_type": "text",
                "media_type": "text/plain",
            }],
        }

        messages = service.build_messages(
            [], None, attachments=attachments
        )

        # Final user message carries the file content under the normal
        # [CURRENT USER MESSAGE] framing, NOT a [CONTINUATION] prompt.
        final = messages[-1]
        assert final["role"] == "user"
        blocks = final["content"]
        assert isinstance(blocks, list)
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        assert "FILE_BODY_MARKER" in text
        assert "[CURRENT USER MESSAGE]" in text
        assert "[CONTINUATION]" not in text

    def test_build_messages_continuation_without_message_or_attachments(self, mock_encoder):
        """With neither text nor attachments, fall back to the continuation prompt."""
        service = AnthropicService()
        service._encoder = mock_encoder

        context = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]

        messages = service.build_messages(
            context, None,
            cached_context=context, new_context=[],
            attachments={"images": [], "files": []},
        )

        joined = "".join(
            m["content"] if isinstance(m["content"], str)
            else "".join(b.get("text", "") for b in m["content"] if isinstance(b, dict))
            for m in messages
        )
        assert "[CONTINUATION]" in joined

    def test_build_messages_memory_messages_in_context(self, mock_encoder):
        """Memory messages embedded in the context flow through like any other message."""
        service = AnthropicService()
        service._encoder = mock_encoder

        context = [
            {
                "role": "user",
                "content": "[MEMORY mem-1 from 2024-01-01 - originally from you]\nI remember you mentioned enjoying programming.\n[/MEMORY]",
                "is_memory": True,
                "memory_id": "mem-1",
            },
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        messages = service.build_messages(context, "Tell me more.")

        # Memory message is part of the conversation history
        assert messages[0]["role"] == "user"
        assert "[CONVERSATION HISTORY]" in messages[0]["content"]
        assert "[MEMORY mem-1" in messages[0]["content"]
        assert "enjoying programming" in messages[0]["content"]

        # Final message has no memory block, just the current message framing
        final_content = messages[-1]["content"]
        assert "[/CONVERSATION HISTORY]" in final_content
        assert "[MEMORIES FROM PREVIOUS CONVERSATIONS]" not in final_content
        assert "[DATE CONTEXT]" in final_content
        assert "Tell me more." in final_content

    def test_build_messages_caching_structure(self):
        """Test that cache_control markers are added correctly on conversation history."""
        service = AnthropicService()

        # Mock both the encoder AND the cache to ensure we get >1024 tokens
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        # Also mock the cache service to not interfere
        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None  # Force cache miss
        service._cache_service = mock_cache

        # With conversation-first caching, we need conversation history to get cache_control
        cached_context = [
            {"role": "user", "content": "Hello " * 100},
            {"role": "assistant", "content": "Hi " * 100},
        ]

        messages = service.build_messages(
            [], "Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Last cached context message should have cache_control when >= 1024 tokens
        # Structure: context[0], context[1] with cache_control, final message
        last_cached_msg = messages[1]
        assert last_cached_msg["role"] == "assistant"
        assert isinstance(last_cached_msg["content"], list)
        assert last_cached_msg["content"][0]["type"] == "text"
        assert "cache_control" in last_cached_msg["content"][0]
        assert last_cached_msg["content"][0]["cache_control"]["type"] == "ephemeral"


class TestCacheBreakpointPlacement:
    """Tests for cache_control marker placement in message building."""

    def test_cache_control_on_conversation_history(self):
        """Test that cache_control is placed on last cached conversation history message."""
        service = AnthropicService()

        # Mock encoder to return > 1024 tokens for history
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Hello " * 100},
            {"role": "assistant", "content": "Hi " * 100},
        ]

        messages = service.build_messages(
            [], "Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Last cached context message should have cache_control
        last_cached_msg = messages[1]
        assert isinstance(last_cached_msg["content"], list)
        assert last_cached_msg["content"][0]["cache_control"]["type"] == "ephemeral"

    def test_no_cache_control_when_history_too_small(self):
        """Test that cache_control is NOT placed when history < 1024 tokens."""
        service = AnthropicService()

        # Mock encoder to return < 1024 tokens
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Short message"},
            {"role": "assistant", "content": "Short reply"},
        ]

        messages = service.build_messages(
            [], "Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Last cached context message should be plain string, not array with cache_control
        last_cached_msg = messages[1]
        assert isinstance(last_cached_msg["content"], str)

    def test_cache_control_on_last_cached_history_message(self):
        """Test that cache_control is placed on last cached history message."""
        service = AnthropicService()

        # Mock encoder to return > 1024 tokens for history
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        # Provide cached_context with multiple messages
        cached_context = [
            {"role": "user", "content": "First user message " * 50},
            {"role": "assistant", "content": "First assistant response " * 50},
            {"role": "user", "content": "Second user message " * 50},
            {"role": "assistant", "content": "Second assistant response " * 50},
        ]

        messages = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Find the last cached context message (4th message, index 3 in cached_context)
        # Messages: [CURRENT CONVERSATION] + cached_context[0], cached_context[1], cached_context[2], cached_context[3], final
        # The last cached context message should have cache_control
        cached_last_msg = messages[3]  # 4th message (0-indexed = 3)
        assert isinstance(cached_last_msg["content"], list)
        assert cached_last_msg["content"][0]["cache_control"]["type"] == "ephemeral"

    def test_no_cache_control_on_new_context(self):
        """Test that new_context messages don't have cache_control."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Cached message " * 50},
            {"role": "assistant", "content": "Cached response " * 50},
        ]
        new_context = [
            {"role": "user", "content": "New message"},
            {"role": "assistant", "content": "New response"},
        ]

        messages = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=new_context,
        )

        # New context messages should be plain strings
        # Structure: cached_context[0], cached_context[1] (with cache_control), new_context[0], new_context[1], final
        new_user_msg = messages[2]  # First new context message
        new_asst_msg = messages[3]  # Second new context message

        assert isinstance(new_user_msg["content"], str)
        assert isinstance(new_asst_msg["content"], str)

    def test_message_structure_identical_across_calls(self):
        """Test that message structure is identical when inputs are identical."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(1500))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Hello " * 50},
            {"role": "assistant", "content": "Hi " * 50},
        ]

        # Build messages twice with identical inputs
        messages1 = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        messages2 = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Structure should be identical (ignoring date which changes)
        assert len(messages1) == len(messages2)
        for i, (m1, m2) in enumerate(zip(messages1, messages2)):
            assert m1["role"] == m2["role"], f"Role mismatch at index {i}"
            # Compare content structure
            c1 = m1["content"]
            c2 = m2["content"]
            if isinstance(c1, list) and isinstance(c2, list):
                assert len(c1) == len(c2), f"Content array length mismatch at index {i}"
                for j, (block1, block2) in enumerate(zip(c1, c2)):
                    assert block1.get("type") == block2.get("type"), f"Block type mismatch at {i}.{j}"
                    assert block1.get("cache_control") == block2.get("cache_control"), f"Cache control mismatch at {i}.{j}"

    def test_multi_entity_header_consistency(self):
        """Test that multi-entity header is consistent across calls."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(100))  # Below cache threshold
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        entity_labels = {"entity-a": "Claude", "entity-b": "GPT"}
        cached_context = [
            {"role": "user", "content": "[Human]: Hello"},
            {"role": "assistant", "content": "[Claude]: Hi"},
        ]

        # Build messages twice
        messages1 = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
            is_multi_entity=True,
            entity_labels=entity_labels,
            responding_entity_label="Claude",
        )

        messages2 = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
            is_multi_entity=True,
            entity_labels=entity_labels,
            responding_entity_label="Claude",
        )

        # First cached context message should have multi-entity header
        first_context_content1 = messages1[0]["content"]
        first_context_content2 = messages2[0]["content"]

        assert "[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN. DO NOT WRITE FOR OTHER PARTICIPANTS. DO NOT LABEL YOUR MESSAGES WITH YOUR NAME.]" in first_context_content1
        assert first_context_content1 == first_context_content2

    def test_multi_entity_header_consistent_across_entities(self):
        """Test that multi-entity header is consistent regardless of responding entity."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(100))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        entity_labels = {"entity-a": "Claude", "entity-b": "GPT"}
        cached_context = [
            {"role": "user", "content": "[Human]: Hello"},
        ]

        # Build for Claude
        messages_claude = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
            is_multi_entity=True,
            entity_labels=entity_labels,
            responding_entity_label="Claude",
        )

        # Build for GPT
        messages_gpt = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
            is_multi_entity=True,
            entity_labels=entity_labels,
            responding_entity_label="GPT",
        )

        # Both should contain the multi-entity header
        first_content_claude = messages_claude[0]["content"]
        first_content_gpt = messages_gpt[0]["content"]

        assert "[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN. DO NOT WRITE FOR OTHER PARTICIPANTS. DO NOT LABEL YOUR MESSAGES WITH YOUR NAME.]" in first_content_claude
        assert "[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN. DO NOT WRITE FOR OTHER PARTICIPANTS. DO NOT LABEL YOUR MESSAGES WITH YOUR NAME.]" in first_content_gpt
        # The header is now the same regardless of responding entity (simplified for cache stability)
        assert first_content_claude == first_content_gpt


class TestTwoBreakpointCachingStrategy:
    """Tests for the conversation-first caching strategy."""

    def test_new_memory_messages_not_cached(self):
        """Newly inserted memory messages ride in new_context without cache_control."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(2000))  # > 1024
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Question " * 100},
            {"role": "assistant", "content": "Answer " * 100},
        ]
        new_context = [
            {
                "role": "user",
                "content": "[MEMORY mem-1 from 2024-01-01 - originally from you]\nMemory content\n[/MEMORY]",
                "is_memory": True,
                "memory_id": "mem-1",
            },
        ]

        messages = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=new_context,
        )

        # Cache breakpoint stays on the last cached history message
        last_cached = messages[1]
        assert last_cached["role"] == "assistant"
        assert isinstance(last_cached["content"], list)
        assert last_cached["content"][0]["cache_control"]["type"] == "ephemeral"

        # Memory message follows uncached, as a plain string
        memory_msg = messages[2]
        assert memory_msg["role"] == "user"
        assert isinstance(memory_msg["content"], str)
        assert "[MEMORY mem-1" in memory_msg["content"]

    def test_breakpoint_2_last_cached_context_has_cache_control(self):
        """Test breakpoint 2: last cached context message has cache_control."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(2000))  # > 1024
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Question " * 100},
            {"role": "assistant", "content": "Answer " * 100},
        ]

        messages = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Last cached context message should have cache_control
        # Structure: cached[0] with marker, cached[1] with cache_control, final
        last_cached = messages[1]  # Second message (last of cached context)
        assert last_cached["role"] == "assistant"
        assert isinstance(last_cached["content"], list)
        assert last_cached["content"][0]["cache_control"]["type"] == "ephemeral"

    def test_conversation_first_structure_with_context(self):
        """Test conversation-first structure with single cache breakpoint."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(2000))  # > 1024
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Question " * 100},
            {"role": "assistant", "content": "Answer " * 100},
        ]

        messages = service.build_messages(
            conversation_context=[],
            current_message="Test",
            cached_context=cached_context,
            new_context=[],
        )

        # Structure should be (3 messages for proper alternation):
        # 0: cached context[0] with [CONVERSATION HISTORY] marker
        # 1: cached context[1] with cache_control (cache breakpoint)
        # 2: combined: [/CONVERSATION HISTORY] + [CURRENT USER MESSAGE] + date + current message

        assert len(messages) == 3

        # First cached context (with CONVERSATION HISTORY marker)
        assert messages[0]["role"] == "user"
        assert "[CONVERSATION HISTORY]" in messages[0]["content"]
        assert "Question " in messages[0]["content"]

        # Cache breakpoint: last cached context
        assert messages[1]["role"] == "assistant"
        assert isinstance(messages[1]["content"], list)
        assert messages[1]["content"][0]["cache_control"]["type"] == "ephemeral"

        # Combined final message (maintains proper user/assistant alternation)
        assert messages[2]["role"] == "user"
        assert "[/CONVERSATION HISTORY]" in messages[2]["content"]
        assert "[CURRENT USER MESSAGE]" in messages[2]["content"]
        assert "[DATE CONTEXT]" in messages[2]["content"]
        assert "Test" in messages[2]["content"]

    def test_new_context_after_cached_context(self):
        """Test that new context appears after cached context without cache_control."""
        service = AnthropicService()

        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = list(range(2000))
        service._encoder = mock_encoder

        mock_cache = MagicMock()
        mock_cache.get_token_count.return_value = None
        service._cache_service = mock_cache

        cached_context = [
            {"role": "user", "content": "Old question " * 100},
            {"role": "assistant", "content": "Old answer " * 100},
        ]
        new_context = [
            {"role": "user", "content": "New question"},
            {"role": "assistant", "content": "New answer"},
        ]

        messages = service.build_messages(
            conversation_context=[],
            current_message="Current",
            cached_context=cached_context,
            new_context=new_context,
        )

        # Structure:
        # 0: cached[0] with marker
        # 1: cached[1] with cache_control (breakpoint 2)
        # 2: new[0] - NO cache_control
        # 3: new[1] - NO cache_control
        # 4: final message

        # New context messages should be plain strings
        assert messages[2]["role"] == "user"
        assert isinstance(messages[2]["content"], str)
        assert "New question" in messages[2]["content"]

        assert messages[3]["role"] == "assistant"
        assert isinstance(messages[3]["content"], str)
        assert "New answer" in messages[3]["content"]


class TestThinkingBlockCapture:
    """Thinking blocks must be captured and preserved in stream order so the
    tool loop can echo them back verbatim (reasoning continuity on
    adaptive-thinking models like Fable 5, where the signature is the only
    carrier of the reasoning)."""

    @staticmethod
    def _stream_client(events):
        """Build a mock client whose messages.stream yields the given events."""

        class _Iter:
            def __init__(self, items):
                self._items = list(items)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._items:
                    raise StopAsyncIteration
                return self._items.pop(0)

        stream_ctx = MagicMock()
        stream_ctx.__aenter__ = AsyncMock(return_value=_Iter(events))
        stream_ctx.__aexit__ = AsyncMock(return_value=False)
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=stream_ctx)
        return client

    @pytest.mark.asyncio
    async def test_stream_captures_thinking_text_and_tool_use_in_order(self):
        from types import SimpleNamespace as NS

        events = [
            NS(type="message_start", message=NS(usage=NS(
                input_tokens=10,
                cache_creation_input_tokens=None,
                cache_read_input_tokens=None,
            ))),
            NS(type="content_block_start", index=0,
               content_block=NS(type="thinking")),
            NS(type="content_block_delta", index=0, delta=NS(thinking="step one, ")),
            NS(type="content_block_delta", index=0, delta=NS(thinking="step two")),
            NS(type="content_block_delta", index=0, delta=NS(signature="sig-abc")),
            NS(type="content_block_stop", index=0),
            NS(type="content_block_start", index=1,
               content_block=NS(type="text", text="")),
            NS(type="content_block_delta", index=1, delta=NS(text="Let me check.")),
            NS(type="content_block_stop", index=1),
            NS(type="content_block_start", index=2,
               content_block=NS(type="tool_use", id="toolu_1", name="get_weather")),
            NS(type="content_block_delta", index=2,
               delta=NS(partial_json='{"city": "Paris"}')),
            NS(type="content_block_stop", index=2),
            NS(type="message_delta", usage=NS(output_tokens=42),
               delta=NS(stop_reason="tool_use")),
        ]

        service = AnthropicService()
        service.client = self._stream_client(events)

        collected = []
        async for event in service.send_message_stream(
            [{"role": "user", "content": "Weather in Paris?"}]
        ):
            collected.append(event)

        done = collected[-1]
        assert done["type"] == "done"
        assert done["content"] == "Let me check."
        assert done["content_blocks"] == [
            {"type": "thinking", "thinking": "step one, step two", "signature": "sig-abc"},
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather",
             "input": {"city": "Paris"}},
        ]
        assert done["tool_use"] == [
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather",
             "input": {"city": "Paris"}},
        ]

    @pytest.mark.asyncio
    async def test_stream_keeps_empty_thinking_with_signature(self):
        """display: omitted (Fable 5 default) returns empty thinking text; the
        block must still be kept because the signature carries the reasoning."""
        from types import SimpleNamespace as NS

        events = [
            NS(type="content_block_start", index=0,
               content_block=NS(type="thinking")),
            NS(type="content_block_delta", index=0, delta=NS(signature="sig-only")),
            NS(type="content_block_stop", index=0),
            NS(type="content_block_start", index=1,
               content_block=NS(type="tool_use", id="toolu_2", name="notes_read")),
            NS(type="content_block_delta", index=1, delta=NS(partial_json='{}')),
            NS(type="content_block_stop", index=1),
            NS(type="message_delta", usage=NS(output_tokens=5),
               delta=NS(stop_reason="tool_use")),
        ]

        service = AnthropicService()
        service.client = self._stream_client(events)

        done = None
        async for event in service.send_message_stream(
            [{"role": "user", "content": "Hi"}]
        ):
            done = event

        assert done["content_blocks"][0] == {
            "type": "thinking", "thinking": "", "signature": "sig-only",
        }
        assert done["content_blocks"][1]["type"] == "tool_use"

    @pytest.mark.asyncio
    async def test_stream_drops_fully_empty_thinking_block(self):
        """A thinking block with neither text nor signature carries nothing
        and would be rejected on replay; it is dropped."""
        from types import SimpleNamespace as NS

        events = [
            NS(type="content_block_start", index=0,
               content_block=NS(type="thinking")),
            NS(type="content_block_stop", index=0),
            NS(type="content_block_start", index=1,
               content_block=NS(type="text", text="")),
            NS(type="content_block_delta", index=1, delta=NS(text="Hello")),
            NS(type="content_block_stop", index=1),
            NS(type="message_delta", usage=NS(output_tokens=3),
               delta=NS(stop_reason="end_turn")),
        ]

        service = AnthropicService()
        service.client = self._stream_client(events)

        done = None
        async for event in service.send_message_stream(
            [{"role": "user", "content": "Hi"}]
        ):
            done = event

        assert done["content_blocks"] == [{"type": "text", "text": "Hello"}]

    @pytest.mark.asyncio
    async def test_stream_captures_redacted_thinking(self):
        from types import SimpleNamespace as NS

        events = [
            NS(type="content_block_start", index=0,
               content_block=NS(type="redacted_thinking", data="opaque-bytes")),
            NS(type="content_block_stop", index=0),
            NS(type="content_block_start", index=1,
               content_block=NS(type="text", text="")),
            NS(type="content_block_delta", index=1, delta=NS(text="Done.")),
            NS(type="content_block_stop", index=1),
            NS(type="message_delta", usage=NS(output_tokens=2),
               delta=NS(stop_reason="end_turn")),
        ]

        service = AnthropicService()
        service.client = self._stream_client(events)

        done = None
        async for event in service.send_message_stream(
            [{"role": "user", "content": "Hi"}]
        ):
            done = event

        assert done["content_blocks"] == [
            {"type": "redacted_thinking", "data": "opaque-bytes"},
            {"type": "text", "text": "Done."},
        ]

    @pytest.mark.asyncio
    async def test_send_message_captures_thinking_blocks(self):
        from types import SimpleNamespace as NS

        response = NS(
            content=[
                NS(type="thinking", thinking="reasoning here", signature="sig-1"),
                NS(type="text", text="Answer"),
                NS(type="tool_use", id="toolu_3", name="web_search",
                   input={"query": "x"}),
            ],
            model="claude-fable-5",
            usage=NS(input_tokens=5, output_tokens=7,
                     cache_creation_input_tokens=None,
                     cache_read_input_tokens=None),
            stop_reason="tool_use",
        )
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=response)

        service = AnthropicService()
        service.client = client

        result = await service.send_message([{"role": "user", "content": "Hi"}])

        assert result["content"] == "Answer"
        assert result["content_blocks"] == [
            {"type": "thinking", "thinking": "reasoning here", "signature": "sig-1"},
            {"type": "text", "text": "Answer"},
            {"type": "tool_use", "id": "toolu_3", "name": "web_search",
             "input": {"query": "x"}},
        ]
        assert result["tool_use"] == [result["content_blocks"][2]]
