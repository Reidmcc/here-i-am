"""
Text-to-speech through ElevenLabs, the one provider the backend proxies.

ElevenLabs is a cloud API whose key lives on the server, so the browser asks
the backend. The local voice servers (StyleTTS 2, XTTS, Whisper) are not
reached from here: the browser on the machine running them calls them
directly (issue #399), and chooses between them and this service.
"""
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.config import VoiceConfig, settings

logger = logging.getLogger(__name__)


class ElevenLabsService:
    """Service for converting text to speech using ElevenLabs API."""

    def __init__(self):
        self.api_key = settings.elevenlabs_api_key
        self.voice_id = settings.elevenlabs_voice_id
        self.model_id = settings.elevenlabs_model_id
        self.base_url = "https://api.elevenlabs.io/v1"

    def is_configured(self) -> bool:
        """Check if ElevenLabs API is configured."""
        return bool(self.api_key)

    def get_voices(self) -> List[VoiceConfig]:
        """Get configured ElevenLabs voices."""
        return settings.get_voices()

    async def text_to_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> bytes:
        """
        Convert text to speech and return audio bytes.

        Args:
            text: The text to convert to speech
            voice_id: Optional voice ID override
            model_id: Optional model ID override

        Returns:
            Audio bytes in MP3 format
        """
        if not self.is_configured():
            raise ValueError("ElevenLabs API key is not configured")

        voice = voice_id or self.voice_id
        model = model_id or self.model_id

        url = f"{self.base_url}/text-to-speech/{voice}"

        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.api_key,
        }

        payload = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            }
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=headers)

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"ElevenLabs API error: {response.status_code} - {error_detail}")
                raise ValueError(f"ElevenLabs API error: {response.status_code}")

            return response.content

    async def text_to_speech_stream(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        """
        Convert text to speech and stream audio bytes.

        Args:
            text: The text to convert to speech
            voice_id: Optional voice ID override
            model_id: Optional model ID override

        Yields:
            Audio bytes chunks in MP3 format
        """
        if not self.is_configured():
            raise ValueError("ElevenLabs API key is not configured")

        voice = voice_id or self.voice_id
        model = model_id or self.model_id

        url = f"{self.base_url}/text-to-speech/{voice}/stream"

        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.api_key,
        }

        payload = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            }
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    error_detail = await response.aread()
                    logger.error(f"ElevenLabs API error: {response.status_code} - {error_detail}")
                    raise ValueError(f"ElevenLabs API error: {response.status_code}")

                async for chunk in response.aiter_bytes():
                    yield chunk


class TTSService:
    """The backend's TTS endpoint: ElevenLabs when its API key is set, else none."""

    def __init__(self):
        self._elevenlabs = ElevenLabsService()

    def get_provider(self) -> str:
        """The configured provider: "elevenlabs" or "none"."""
        return settings.get_tts_provider()

    def is_configured(self) -> bool:
        """Check if ElevenLabs is configured."""
        return self.get_provider() == "elevenlabs" and self._elevenlabs.is_configured()

    def get_voices(self) -> List[Dict[str, Any]]:
        """The configured ElevenLabs voices, as dicts (empty when unconfigured)."""
        if self.get_provider() != "elevenlabs":
            return []
        return [v.to_dict() for v in self._elevenlabs.get_voices()]

    def get_default_voice_id(self) -> Optional[str]:
        """The default ElevenLabs voice ID, or None when unconfigured."""
        if self.get_provider() != "elevenlabs":
            return None
        return settings.elevenlabs_voice_id

    async def text_to_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> bytes:
        """Convert text to speech. Returns MP3 bytes."""
        if self.get_provider() != "elevenlabs":
            raise ValueError("No TTS provider is configured")
        return await self._elevenlabs.text_to_speech(text, voice_id, model_id)

    async def text_to_speech_stream(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        """Convert text to speech and stream MP3 chunks."""
        if self.get_provider() != "elevenlabs":
            raise ValueError("No TTS provider is configured")
        async for chunk in self._elevenlabs.text_to_speech_stream(text, voice_id, model_id):
            yield chunk

    async def get_status(self) -> Dict[str, Any]:
        """
        TTS status for the frontend.

        Always the same keys: configured, provider ("elevenlabs" or "none"),
        voices, default_voice_id, model_id.
        """
        if self.get_provider() != "elevenlabs":
            return {
                "configured": False,
                "provider": "none",
                "voices": [],
                "default_voice_id": None,
                "model_id": None,
            }

        return {
            "configured": True,
            "provider": "elevenlabs",
            "voices": self.get_voices(),
            "default_voice_id": self.get_default_voice_id(),
            "model_id": self._elevenlabs.model_id,
        }


# Singleton instance
tts_service = TTSService()
