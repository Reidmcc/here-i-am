"""
Unit tests for the Google (Gemini) Service.

Tests Gemini API interactions, message formatting, and streaming.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from typing import AsyncIterator

from app.services.google_service import GoogleService


@pytest.fixture
def google_service():
    """Create a fresh GoogleService instance for each test."""
    return GoogleService()


@pytest.fixture
def mock_settings_configured():
    """Mock settings with Google API key configured."""
    with patch("app.services.google_service.settings") as mock:
        mock.google_api_key = "test-google-api-key"
        mock.default_google_model = "gemini-2.5-flash"
        mock.default_temperature = 1.0
        mock.default_max_tokens = 4096
        yield mock


@pytest.fixture
def mock_settings_unconfigured():
    """Mock settings without Google API key."""
    with patch("app.services.google_service.settings") as mock:
        mock.google_api_key = ""
        yield mock


class TestGoogleServiceConfiguration:
    """Tests for service configuration."""

    def test_is_configured_with_key(self, mock_settings_configured):
        """Test is_configured returns True when API key is set."""
        service = GoogleService()
        assert service.is_configured() is True

    def test_is_configured_without_key(self, mock_settings_unconfigured):
        """Test is_configured returns False when API key is not set."""
        service = GoogleService()
        assert service.is_configured() is False

    def test_ensure_client_raises_without_key(self, mock_settings_unconfigured):
        """Test that _ensure_client raises when no API key."""
        service = GoogleService()
        with pytest.raises(ValueError) as exc:
            service._ensure_client()
        assert "not configured" in str(exc.value).lower()

    def test_ensure_client_creates_client(self, mock_settings_configured):
        """Test that _ensure_client creates a client."""
        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            service = GoogleService()
            client = service._ensure_client()

            assert client == mock_client
            mock_client_class.assert_called_once_with(api_key="test-google-api-key")

    def test_client_cached(self, mock_settings_configured):
        """Test that client is cached after first creation."""
        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            service = GoogleService()
            client1 = service._ensure_client()
            client2 = service._ensure_client()

            assert client1 is client2
            assert mock_client_class.call_count == 1  # Only called once


class TestSupportedModels:
    """Tests for supported models."""

    def test_supported_models_includes_gemini_2(self):
        """Test that Gemini 2.x models are supported."""
        assert "gemini-2.5-pro" in GoogleService.SUPPORTED_MODELS
        assert "gemini-2.5-flash" in GoogleService.SUPPORTED_MODELS
        assert "gemini-2.0-flash" in GoogleService.SUPPORTED_MODELS
        assert "gemini-2.0-flash-lite" in GoogleService.SUPPORTED_MODELS

    def test_supported_models_includes_gemini_3(self):
        """Test that Gemini 3.x models are supported."""
        assert "gemini-3.0-flash" in GoogleService.SUPPORTED_MODELS
        assert "gemini-3.0-pro" in GoogleService.SUPPORTED_MODELS

    def test_all_models_support_temperature(self):
        """Test that all models support temperature."""
        service = GoogleService()
        for model in GoogleService.SUPPORTED_MODELS:
            assert service._supports_temperature(model) is True


class TestTokenCounting:
    """Tests for token counting."""

    def test_count_tokens(self, google_service):
        """Test approximate token counting."""
        # Mock tiktoken to avoid network calls
        with patch("tiktoken.get_encoding") as mock_get_encoding:
            mock_encoder = MagicMock()
            mock_encoder.encode.return_value = [1, 2, 3, 4, 5, 6]  # 6 tokens
            mock_get_encoding.return_value = mock_encoder

            # Reset cached encoder
            google_service._encoder = None

            text = "Hello, this is a test."
            count = google_service.count_tokens(text)
            assert isinstance(count, int)
            assert count == 6

    def test_count_tokens_empty_string(self, google_service):
        """Test counting tokens for empty string."""
        # Mock tiktoken to avoid network calls
        with patch("tiktoken.get_encoding") as mock_get_encoding:
            mock_encoder = MagicMock()
            mock_encoder.encode.return_value = []  # 0 tokens
            mock_get_encoding.return_value = mock_encoder

            # Reset cached encoder
            google_service._encoder = None

            count = google_service.count_tokens("")
            assert count == 0

    def test_count_tokens_long_text(self, google_service):
        """Test counting tokens for longer text."""
        # Mock tiktoken to avoid network calls
        with patch("tiktoken.get_encoding") as mock_get_encoding:
            mock_encoder = MagicMock()
            mock_encoder.encode.return_value = list(range(1000))  # 1000 tokens
            mock_get_encoding.return_value = mock_encoder

            # Reset cached encoder
            google_service._encoder = None

            text = "word " * 1000
            count = google_service.count_tokens(text)
            assert count == 1000


class TestMessageConversion:
    """Tests for message format conversion."""

    def test_convert_user_message(self, google_service):
        """Test converting a user message."""
        messages = [{"role": "user", "content": "Hello"}]
        contents = google_service._convert_messages_to_contents(messages)

        assert len(contents) == 1
        assert contents[0].role == "user"

    def test_convert_assistant_message(self, google_service):
        """Test converting an assistant message to model role."""
        messages = [{"role": "assistant", "content": "Hi there"}]
        contents = google_service._convert_messages_to_contents(messages)

        assert len(contents) == 1
        assert contents[0].role == "model"  # Mapped from 'assistant'

    def test_convert_multiple_messages(self, google_service):
        """Test converting multiple messages."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "How are you?"},
        ]
        contents = google_service._convert_messages_to_contents(messages)

        assert len(contents) == 3
        assert contents[0].role == "user"
        assert contents[1].role == "model"
        assert contents[2].role == "user"

    def test_convert_array_content(self, google_service):
        """Test converting array content format (from Anthropic cache_control)."""
        messages = [{"role": "user", "content": [{"text": "Array content"}]}]
        contents = google_service._convert_messages_to_contents(messages)

        assert len(contents) == 1
        # Should have extracted the text from the array


class TestSendMessage:
    """Tests for send_message functionality."""

    @pytest.fixture
    def mock_response(self):
        """Create a mock API response."""
        mock_part = MagicMock()
        mock_part.text = "This is a response from Gemini."

        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        mock_candidate = MagicMock()
        mock_candidate.content = mock_content
        mock_candidate.finish_reason = "STOP"

        mock_usage = MagicMock()
        mock_usage.prompt_token_count = 100
        mock_usage.candidates_token_count = 50

        mock_resp = MagicMock()
        mock_resp.candidates = [mock_candidate]
        mock_resp.usage_metadata = mock_usage

        return mock_resp

    @pytest.mark.asyncio
    async def test_send_message_success(self, mock_settings_configured, mock_response):
        """Test successful message sending."""
        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            result = await service.send_message(
                messages=[{"role": "user", "content": "Hello"}]
            )

            assert result["content"] == "This is a response from Gemini."
            assert result["model"] == "gemini-2.5-flash"
            assert result["usage"]["input_tokens"] == 100
            assert result["usage"]["output_tokens"] == 50
            assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_send_message_with_system_prompt(
        self, mock_settings_configured, mock_response
    ):
        """Test sending message with system prompt."""
        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            await service.send_message(
                messages=[{"role": "user", "content": "Hello"}],
                system_prompt="You are a helpful assistant.",
            )

            # Verify the API was called
            mock_client.aio.models.generate_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_custom_model(
        self, mock_settings_configured, mock_response
    ):
        """Test sending message with custom model."""
        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            result = await service.send_message(
                messages=[{"role": "user", "content": "Hello"}],
                model="gemini-2.5-pro",
            )

            assert result["model"] == "gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_send_message_max_tokens_stop_reason(self, mock_settings_configured):
        """Test that max_tokens finish reason is mapped correctly."""
        mock_candidate = MagicMock()
        mock_candidate.content = MagicMock()
        mock_candidate.content.parts = [MagicMock(text="Truncated")]
        mock_candidate.finish_reason = "MAX_TOKENS"

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]
        mock_response.usage_metadata = MagicMock(
            prompt_token_count=100,
            candidates_token_count=1000
        )

        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            result = await service.send_message(
                messages=[{"role": "user", "content": "Hello"}]
            )

            assert result["stop_reason"] == "max_tokens"

    @pytest.mark.asyncio
    async def test_send_message_empty_response(self, mock_settings_configured):
        """Test handling of empty response."""
        mock_response = MagicMock()
        mock_response.candidates = []
        mock_response.usage_metadata = MagicMock(
            prompt_token_count=100,
            candidates_token_count=0
        )

        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            result = await service.send_message(
                messages=[{"role": "user", "content": "Hello"}]
            )

            assert result["content"] == ""


class TestSendMessageStream:
    """Tests for streaming message functionality."""

    @pytest.fixture
    def mock_stream_chunks(self):
        """Create mock streaming chunks."""
        chunk1 = MagicMock()
        chunk1.candidates = [MagicMock()]
        chunk1.candidates[0].content = MagicMock()
        chunk1.candidates[0].content.parts = [MagicMock(text="Hello ")]
        chunk1.candidates[0].finish_reason = None
        chunk1.usage_metadata = None

        chunk2 = MagicMock()
        chunk2.candidates = [MagicMock()]
        chunk2.candidates[0].content = MagicMock()
        chunk2.candidates[0].content.parts = [MagicMock(text="world!")]
        chunk2.candidates[0].finish_reason = "STOP"
        chunk2.usage_metadata = MagicMock(
            prompt_token_count=10,
            candidates_token_count=2
        )

        return [chunk1, chunk2]

    @pytest.mark.asyncio
    async def test_stream_yields_start_event(self, mock_settings_configured):
        """Test that streaming yields a start event."""
        async def mock_stream():
            yield MagicMock(candidates=[], usage_metadata=None)

        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content_stream = AsyncMock(
                return_value=mock_stream()
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            events = []
            async for event in service.send_message_stream(
                messages=[{"role": "user", "content": "Hello"}]
            ):
                events.append(event)

            assert events[0]["type"] == "start"
            assert events[0]["model"] == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_stream_yields_token_events(
        self, mock_settings_configured, mock_stream_chunks
    ):
        """Test that streaming yields token events."""
        async def mock_stream():
            for chunk in mock_stream_chunks:
                yield chunk

        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content_stream = AsyncMock(
                return_value=mock_stream()
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            events = []
            async for event in service.send_message_stream(
                messages=[{"role": "user", "content": "Hello"}]
            ):
                events.append(event)

            # Should have start, tokens, and done
            token_events = [e for e in events if e["type"] == "token"]
            assert len(token_events) >= 1

    @pytest.mark.asyncio
    async def test_stream_yields_done_event(
        self, mock_settings_configured, mock_stream_chunks
    ):
        """Test that streaming yields a done event with full content."""
        async def mock_stream():
            for chunk in mock_stream_chunks:
                yield chunk

        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content_stream = AsyncMock(
                return_value=mock_stream()
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            events = []
            async for event in service.send_message_stream(
                messages=[{"role": "user", "content": "Hello"}]
            ):
                events.append(event)

            done_events = [e for e in events if e["type"] == "done"]
            assert len(done_events) == 1
            assert done_events[0]["content"] == "Hello world!"
            assert done_events[0]["model"] == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_stream_error_handling(self, mock_settings_configured):
        """Test that streaming handles errors gracefully."""
        async def mock_stream():
            yield MagicMock(candidates=[], usage_metadata=None)
            raise Exception("API Error")

        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content_stream = AsyncMock(
                return_value=mock_stream()
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            events = []
            async for event in service.send_message_stream(
                messages=[{"role": "user", "content": "Hello"}]
            ):
                events.append(event)

            # Should have an error event
            error_events = [e for e in events if e["type"] == "error"]
            assert len(error_events) == 1
            assert "API Error" in error_events[0]["error"]


class TestUsageMetadata:
    """Tests for usage metadata handling."""

    @pytest.mark.asyncio
    async def test_cached_tokens_in_usage(self, mock_settings_configured):
        """Test that cached tokens are included when available."""
        mock_usage = MagicMock()
        mock_usage.prompt_token_count = 100
        mock_usage.candidates_token_count = 50
        mock_usage.cached_content_token_count = 80

        mock_candidate = MagicMock()
        mock_candidate.content = MagicMock()
        mock_candidate.content.parts = [MagicMock(text="Response")]
        mock_candidate.finish_reason = "STOP"

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]
        mock_response.usage_metadata = mock_usage

        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            result = await service.send_message(
                messages=[{"role": "user", "content": "Hello"}]
            )

            assert result["usage"]["cached_tokens"] == 80

    @pytest.mark.asyncio
    async def test_missing_usage_metadata(self, mock_settings_configured):
        """Test handling when usage metadata is missing."""
        mock_candidate = MagicMock()
        mock_candidate.content = MagicMock()
        mock_candidate.content.parts = [MagicMock(text="Response")]
        mock_candidate.finish_reason = "STOP"

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]
        mock_response.usage_metadata = None

        with patch("app.services.google_service.genai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response
            )
            mock_client_class.return_value = mock_client

            service = GoogleService()
            result = await service.send_message(
                messages=[{"role": "user", "content": "Hello"}]
            )

            assert result["usage"]["input_tokens"] == 0
            assert result["usage"]["output_tokens"] == 0


class TestFinishReasonMapping:
    """Tests for finish reason mapping."""

    @pytest.mark.asyncio
    async def test_stop_reason_mapping(self, mock_settings_configured):
        """Test various finish reason mappings."""
        test_cases = [
            ("STOP", "end_turn"),
            ("MAX_TOKENS", "max_tokens"),
            ("SAFETY", "safety"),
            ("LENGTH", "max_tokens"),
        ]

        for gemini_reason, expected_reason in test_cases:
            mock_candidate = MagicMock()
            mock_candidate.content = MagicMock()
            mock_candidate.content.parts = [MagicMock(text="Response")]
            mock_candidate.finish_reason = gemini_reason

            mock_response = MagicMock()
            mock_response.candidates = [mock_candidate]
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=10,
                candidates_token_count=5
            )

            with patch("app.services.google_service.genai.Client") as mock_client_class:
                mock_client = MagicMock()
                mock_client.aio.models.generate_content = AsyncMock(
                    return_value=mock_response
                )
                mock_client_class.return_value = mock_client

                service = GoogleService()
                result = await service.send_message(
                    messages=[{"role": "user", "content": "Hello"}]
                )

                assert result["stop_reason"] == expected_reason, (
                    f"Expected {gemini_reason} to map to {expected_reason}"
                )
