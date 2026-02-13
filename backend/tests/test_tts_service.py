"""
Tests for tts_service.py - Unified TTS service and ElevenLabs service.

Tests cover:
- ElevenLabsService: Configuration, text-to-speech
- TTSService: Provider routing, configuration status, voice listing, status
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
import httpx

from app.services.tts_service import ElevenLabsService, TTSService


# ============================================================
# Tests for ElevenLabsService
# ============================================================

class TestElevenLabsService:
    """Tests for ElevenLabs cloud TTS service."""

    @patch("app.services.tts_service.settings")
    def test_is_configured_with_key(self, mock_settings):
        """Should be configured when API key is set."""
        mock_settings.elevenlabs_api_key = "test-key"
        mock_settings.elevenlabs_voice_id = "voice-1"
        mock_settings.elevenlabs_model_id = "model-1"
        service = ElevenLabsService()
        assert service.is_configured() is True

    @patch("app.services.tts_service.settings")
    def test_is_not_configured_without_key(self, mock_settings):
        """Should not be configured without API key."""
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        service = ElevenLabsService()
        assert service.is_configured() is False

    @patch("app.services.tts_service.settings")
    @pytest.mark.asyncio
    async def test_text_to_speech_not_configured_raises(self, mock_settings):
        """Should raise ValueError when not configured."""
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        service = ElevenLabsService()

        with pytest.raises(ValueError, match="not configured"):
            await service.text_to_speech("Hello")

    @patch("app.services.tts_service.settings")
    @pytest.mark.asyncio
    async def test_text_to_speech_success(self, mock_settings):
        """Should return audio bytes on success."""
        mock_settings.elevenlabs_api_key = "test-key"
        mock_settings.elevenlabs_voice_id = "voice-1"
        mock_settings.elevenlabs_model_id = "model-1"
        service = ElevenLabsService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"audio-bytes"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            result = await service.text_to_speech("Hello world")
            assert result == b"audio-bytes"

    @patch("app.services.tts_service.settings")
    @pytest.mark.asyncio
    async def test_text_to_speech_api_error(self, mock_settings):
        """Should raise on API error."""
        mock_settings.elevenlabs_api_key = "test-key"
        mock_settings.elevenlabs_voice_id = "voice-1"
        mock_settings.elevenlabs_model_id = "model-1"
        service = ElevenLabsService()

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            with pytest.raises(ValueError, match="429"):
                await service.text_to_speech("Hello")

    @patch("app.services.tts_service.settings")
    @pytest.mark.asyncio
    async def test_text_to_speech_stream_not_configured_raises(self, mock_settings):
        """Should raise ValueError for stream when not configured."""
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        service = ElevenLabsService()

        with pytest.raises(ValueError, match="not configured"):
            async for _ in service.text_to_speech_stream("Hello"):
                pass


# ============================================================
# Tests for TTSService
# ============================================================

class TestTTSService:
    """Tests for the unified TTS service."""

    @patch("app.services.tts_service.settings")
    def test_get_provider_none(self, mock_settings):
        """Should return 'none' when no provider configured."""
        mock_settings.get_tts_provider.return_value = "none"
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        service = TTSService()
        assert service.get_provider() == "none"

    @patch("app.services.tts_service.settings")
    def test_get_provider_elevenlabs(self, mock_settings):
        """Should return 'elevenlabs' when ElevenLabs is configured."""
        mock_settings.get_tts_provider.return_value = "elevenlabs"
        mock_settings.elevenlabs_api_key = "key"
        mock_settings.elevenlabs_voice_id = "voice"
        mock_settings.elevenlabs_model_id = "model"
        service = TTSService()
        assert service.get_provider() == "elevenlabs"

    @patch("app.services.tts_service.settings")
    def test_is_configured_elevenlabs(self, mock_settings):
        """Should check ElevenLabs configuration."""
        mock_settings.get_tts_provider.return_value = "elevenlabs"
        mock_settings.elevenlabs_api_key = "key"
        mock_settings.elevenlabs_voice_id = "voice"
        mock_settings.elevenlabs_model_id = "model"
        service = TTSService()
        assert service.is_configured() is True

    @patch("app.services.tts_service.settings")
    def test_is_configured_none(self, mock_settings):
        """Should not be configured when no provider."""
        mock_settings.get_tts_provider.return_value = "none"
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        service = TTSService()
        assert service.is_configured() is False

    @patch("app.services.tts_service.settings")
    def test_get_default_voice_id_elevenlabs(self, mock_settings):
        """Should return ElevenLabs default voice ID."""
        mock_settings.get_tts_provider.return_value = "elevenlabs"
        mock_settings.elevenlabs_api_key = "key"
        mock_settings.elevenlabs_voice_id = "default-voice-id"
        mock_settings.elevenlabs_model_id = "model"
        service = TTSService()
        assert service.get_default_voice_id() == "default-voice-id"

    @patch("app.services.tts_service.settings")
    def test_get_default_voice_id_no_provider(self, mock_settings):
        """Should return None when no provider configured."""
        mock_settings.get_tts_provider.return_value = "none"
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        service = TTSService()
        assert service.get_default_voice_id() is None

    @patch("app.services.tts_service.settings")
    @pytest.mark.asyncio
    async def test_text_to_speech_no_provider_raises(self, mock_settings):
        """Should raise ValueError when no provider configured."""
        mock_settings.get_tts_provider.return_value = "none"
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        service = TTSService()

        with pytest.raises(ValueError, match="No TTS provider"):
            await service.text_to_speech("Hello")

    @patch("app.services.tts_service.settings")
    @pytest.mark.asyncio
    async def test_get_status_no_provider(self, mock_settings):
        """Should return unconfigured status when no provider."""
        mock_settings.get_tts_provider.return_value = "none"
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        service = TTSService()

        status = await service.get_status()
        assert status["configured"] is False
        assert status["provider"] == "none"
        assert status["voices"] == []

    @patch("app.services.tts_service.settings")
    @pytest.mark.asyncio
    async def test_text_to_speech_stream_no_provider_raises(self, mock_settings):
        """Should raise ValueError for stream when no provider configured."""
        mock_settings.get_tts_provider.return_value = "none"
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        service = TTSService()

        with pytest.raises(ValueError, match="No TTS provider"):
            async for _ in service.text_to_speech_stream("Hello"):
                pass
