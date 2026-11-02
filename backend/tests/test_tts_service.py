"""
Tests for the backend's TTS: ElevenLabs only (issue #399).

The local voice servers (XTTS, StyleTTS 2, Whisper) are called by the browser
directly; the backend no longer proxies them.

Tests cover:
- ElevenLabsService: Configuration, text-to-speech
- TTSService: Provider selection, configuration status, voice listing, status shape
- /api/tts routes: what remains, audio/mpeg, the removed proxy routes are gone
- The startup warning for retired voice settings in an old environment/.env
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import (
    RETIRED_VOICE_ENV_VARS,
    Settings,
    VoiceConfig,
    find_retired_voice_env_vars,
    retired_voice_env_warning,
)
from app.services.tts_service import ElevenLabsService, TTSService

STATUS_KEYS = {"configured", "provider", "voices", "default_voice_id", "model_id"}

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

    @patch("app.services.tts_service.settings")
    @pytest.mark.asyncio
    async def test_get_status_elevenlabs_shape(self, mock_settings):
        """ElevenLabs status carries the same keys as the unconfigured one."""
        mock_settings.get_tts_provider.return_value = "elevenlabs"
        mock_settings.elevenlabs_api_key = "key"
        mock_settings.elevenlabs_voice_id = "voice-1"
        mock_settings.elevenlabs_model_id = "model-1"
        mock_settings.get_voices.return_value = [
            VoiceConfig(voice_id="voice-1", label="Rachel", description="Calm"),
        ]
        service = TTSService()

        status = await service.get_status()
        assert set(status) == STATUS_KEYS
        assert status["configured"] is True
        assert status["provider"] == "elevenlabs"
        assert status["default_voice_id"] == "voice-1"
        assert status["model_id"] == "model-1"
        assert status["voices"] == [{
            "voice_id": "voice-1",
            "label": "Rachel",
            "description": "Calm",
            "provider": "elevenlabs",
        }]

    @patch("app.services.tts_service.settings")
    @pytest.mark.asyncio
    async def test_get_status_none_shape(self, mock_settings):
        mock_settings.get_tts_provider.return_value = "none"
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        status = await TTSService().get_status()
        assert set(status) == STATUS_KEYS

    @patch("app.services.tts_service.settings")
    def test_get_voices_none(self, mock_settings):
        mock_settings.get_tts_provider.return_value = "none"
        mock_settings.elevenlabs_api_key = ""
        mock_settings.elevenlabs_voice_id = ""
        mock_settings.elevenlabs_model_id = ""
        assert TTSService().get_voices() == []


# ============================================================
# Provider selection (Settings.get_tts_provider)
# ============================================================

class TestProviderSelection:
    """ElevenLabs is the only provider the backend selects."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for name in ("ELEVENLABS_API_KEY", *RETIRED_VOICE_ENV_VARS):
            monkeypatch.delenv(name, raising=False)

    def test_elevenlabs_when_key_set(self):
        assert Settings(_env_file=None, elevenlabs_api_key="k").get_tts_provider() == "elevenlabs"

    def test_none_without_key(self):
        assert Settings(_env_file=None, elevenlabs_api_key="").get_tts_provider() == "none"

    def test_retired_local_flags_select_nothing(self, monkeypatch):
        """An old XTTS_ENABLED/STYLETTS2_ENABLED no longer picks a provider, and still loads."""
        monkeypatch.setenv("XTTS_ENABLED", "true")
        monkeypatch.setenv("STYLETTS2_ENABLED", "true")
        settings = Settings(_env_file=None, elevenlabs_api_key="")
        assert settings.get_tts_provider() == "none"
        assert not hasattr(settings, "xtts_enabled")
        assert not hasattr(settings, "styletts2_enabled")

    def test_retired_local_flags_do_not_shadow_elevenlabs(self, monkeypatch):
        monkeypatch.setenv("STYLETTS2_ENABLED", "true")
        assert Settings(_env_file=None, elevenlabs_api_key="k").get_tts_provider() == "elevenlabs"


# ============================================================
# /api/tts routes
# ============================================================

class TestTTSRoutes:
    """The router keeps only the ElevenLabs endpoints."""

    @pytest.fixture
    def client(self):
        from app.routes.tts import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_only_elevenlabs_routes_remain(self):
        from app.routes.tts import router

        routes = {(r.path, method) for r in router.routes for method in r.methods}
        assert routes == {
            ("/api/tts/speak", "POST"),
            ("/api/tts/speak/stream", "POST"),
            ("/api/tts/status", "GET"),
            ("/api/tts/voices", "GET"),
        }

    def test_no_local_voice_routes_on_app(self):
        from app.main import app

        paths = {getattr(r, "path", "") for r in app.routes}
        assert not any(p.startswith("/api/stt") for p in paths)
        assert not any("xtts" in p or "styletts2" in p for p in paths)

    def test_speak_returns_mpeg(self, client):
        with patch("app.routes.tts.tts_service") as svc:
            svc.is_configured.return_value = True
            svc.text_to_speech = AsyncMock(return_value=b"mp3")
            response = client.post("/api/tts/speak", json={"text": "Hello"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.content == b"mp3"
        svc.text_to_speech.assert_awaited_once_with(text="Hello", voice_id=None, model_id=None)

    def test_speak_stream_returns_mpeg(self, client):
        async def chunks(**_kwargs):
            yield b"a"
            yield b"b"

        with patch("app.routes.tts.tts_service") as svc:
            svc.is_configured.return_value = True
            svc.text_to_speech_stream = chunks
            response = client.post("/api/tts/speak/stream", json={"text": "Hello"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.content == b"ab"

    def test_speak_unconfigured_is_503(self, client):
        with patch("app.routes.tts.tts_service") as svc:
            svc.is_configured.return_value = False
            response = client.post("/api/tts/speak", json={"text": "Hello"})
        assert response.status_code == 503

    def test_voices_unconfigured(self, client):
        with patch("app.routes.tts.tts_service") as svc:
            svc.is_configured.return_value = False
            response = client.get("/api/tts/voices")
        assert response.json() == {"voices": [], "provider": "none"}


# ============================================================
# Retired voice settings (startup warning)
# ============================================================

class TestRetiredVoiceEnvVars:
    """An old .env that still configures the removed proxy is said aloud, not ignored silently."""

    def test_none_found(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("ELEVENLABS_API_KEY=k\n# XTTS_ENABLED=true\n")
        assert find_retired_voice_env_vars(environ={"PATH": "x"}, env_file=str(env_file)) == []

    def test_from_environment_case_insensitive(self, tmp_path):
        found = find_retired_voice_env_vars(
            environ={"whisper_enabled": "true", "XTTS_API_URL": "http://localhost:8020"},
            env_file=str(tmp_path / "missing.env"),
        )
        assert found == ["WHISPER_ENABLED", "XTTS_API_URL"]

    def test_from_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "STYLETTS2_ENABLED=true\n"
            "DICTATION_MODE=auto\n"
            "WHISPER_API_URL=http://localhost:8030\n"
            "STYLETTS2_API_URL=http://localhost:8021\n"
        )
        found = find_retired_voice_env_vars(environ={}, env_file=str(env_file))
        assert found == ["STYLETTS2_ENABLED", "DICTATION_MODE", "STYLETTS2_API_URL", "WHISPER_API_URL"]

    def test_environment_and_file_merge_without_duplicates(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("XTTS_ENABLED=true\n")
        found = find_retired_voice_env_vars(environ={"XTTS_ENABLED": "1"}, env_file=str(env_file))
        assert found == ["XTTS_ENABLED"]

    def test_default_env_file_is_settings_env_file(self, tmp_path, monkeypatch):
        """With no env_file given, the helper reads the same .env Settings does."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / Settings.Config.env_file).write_text("WHISPER_ENABLED=true\n")
        assert find_retired_voice_env_vars(environ={}) == ["WHISPER_ENABLED"]

    def test_warning_names_vars_issue_and_docs(self):
        message = retired_voice_env_warning(["XTTS_ENABLED", "WHISPER_API_URL"])
        assert "XTTS_ENABLED, WHISPER_API_URL" in message
        assert "no longer do anything" in message
        assert "#399" in message
        assert "docs/local-services.md" in message
        assert "Settings" in message

    def test_warning_agrees_with_one_name(self):
        assert "XTTS_ENABLED no longer does anything" in retired_voice_env_warning(["XTTS_ENABLED"])

    def test_startup_logs_warning(self, caplog):
        from app import main

        with patch.object(main, "find_retired_voice_env_vars", return_value=["XTTS_ENABLED"]):
            with caplog.at_level(logging.WARNING):
                main.warn_retired_voice_settings()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "XTTS_ENABLED" in warnings[0].getMessage()

    def test_startup_silent_when_clean(self, caplog):
        from app import main

        with patch.object(main, "find_retired_voice_env_vars", return_value=[]):
            with caplog.at_level(logging.WARNING):
                main.warn_retired_voice_settings()
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
