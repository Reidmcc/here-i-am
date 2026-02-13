"""
Tests for whisper_service.py - Whisper STT service.

Tests cover:
- WhisperService: Configuration, health checks, transcription, status
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx

from app.services.whisper_service import WhisperService


# ============================================================
# Tests for WhisperService
# ============================================================

class TestWhisperService:
    """Tests for the Whisper speech-to-text service."""

    @patch("app.services.whisper_service.settings")
    def test_is_configured_enabled(self, mock_settings):
        """Should be configured when enabled."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()
        assert service.is_configured() is True

    @patch("app.services.whisper_service.settings")
    def test_is_not_configured_disabled(self, mock_settings):
        """Should not be configured when disabled."""
        mock_settings.whisper_enabled = False
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()
        assert service.is_configured() is False

    @patch("app.services.whisper_service.settings")
    def test_get_dictation_mode(self, mock_settings):
        """Should return configured dictation mode."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "whisper"
        service = WhisperService()
        assert service.get_dictation_mode() == "whisper"

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_check_health_disabled(self, mock_settings):
        """Should return False when disabled."""
        mock_settings.whisper_enabled = False
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()
        assert await service.check_health() is False

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_check_health_success(self, mock_settings):
        """Should return True when server responds healthy."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"model_loaded": True}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            result = await service.check_health()
            assert result is True
            assert service._server_healthy is True

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_check_health_connection_error(self, mock_settings):
        """Should return False on connection error."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            result = await service.check_health()
            assert result is False

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_transcribe_disabled_raises(self, mock_settings):
        """Should raise ValueError when disabled."""
        mock_settings.whisper_enabled = False
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        with pytest.raises(ValueError, match="not enabled"):
            await service.transcribe(b"audio-data")

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_transcribe_success(self, mock_settings):
        """Should return transcription on success."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "text": "Hello world",
            "language": "en",
            "duration": 2.5,
            "processing_time": 0.8,
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            result = await service.transcribe(b"audio-data")
            assert result["text"] == "Hello world"
            assert result["language"] == "en"

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_transcribe_with_language_and_prompt(self, mock_settings):
        """Should pass language and initial_prompt to API."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "Bonjour", "language": "fr"}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            result = await service.transcribe(
                b"audio-data",
                language="fr",
                initial_prompt="This is a conversation about...",
            )
            assert result["text"] == "Bonjour"

            # Check that language and initial_prompt were passed
            call_kwargs = mock_client.post.call_args
            data = call_kwargs.kwargs.get("data", {})
            assert data.get("language") == "fr"
            assert data.get("initial_prompt") == "This is a conversation about..."

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_transcribe_api_error(self, mock_settings):
        """Should raise on API error."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal server error"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="500"):
                await service.transcribe(b"audio-data")

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_transcribe_timeout(self, mock_settings):
        """Should raise on timeout."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timed out"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="timed out"):
                await service.transcribe(b"audio-data")

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_get_status_disabled(self, mock_settings):
        """Should return disabled status."""
        mock_settings.whisper_enabled = False
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        status = await service.get_status()
        assert status["configured"] is False
        assert status["effective_mode"] == "browser"

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_get_status_disabled_whisper_mode(self, mock_settings):
        """Should return none effective_mode when disabled and mode is whisper."""
        mock_settings.whisper_enabled = False
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "whisper"
        service = WhisperService()

        status = await service.get_status()
        assert status["effective_mode"] == "none"
        assert status["enabled"] is False

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_get_status_enabled_healthy(self, mock_settings):
        """Should return healthy status with server info."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model_loaded": True,
            "model": "large-v3",
            "device": "cuda",
            "cuda_available": True,
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            status = await service.get_status()
            assert status["configured"] is True
            assert status["server_healthy"] is True
            assert status["effective_mode"] == "whisper"
            assert status["model"] == "large-v3"

    @patch("app.services.whisper_service.settings")
    @pytest.mark.asyncio
    async def test_get_status_server_unreachable_auto_mode(self, mock_settings):
        """Should fall back to browser when server is unreachable in auto mode."""
        mock_settings.whisper_enabled = True
        mock_settings.whisper_api_url = "http://localhost:8030"
        mock_settings.dictation_mode = "auto"
        service = WhisperService()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            status = await service.get_status()
            assert status["server_healthy"] is False
            assert status["effective_mode"] == "browser"
