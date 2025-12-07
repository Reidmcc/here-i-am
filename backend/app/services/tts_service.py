"""
Text-to-Speech service using ElevenLabs API.
"""
import logging
from typing import Optional, AsyncIterator
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class TTSService:
    """Service for converting text to speech using ElevenLabs API."""

    def __init__(self):
        self.api_key = settings.elevenlabs_api_key
        self.voice_id = settings.elevenlabs_voice_id
        self.model_id = settings.elevenlabs_model_id
        self.base_url = "https://api.elevenlabs.io/v1"

    def is_configured(self) -> bool:
        """Check if ElevenLabs API is configured."""
        return bool(self.api_key)

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


# Singleton instance
tts_service = TTSService()
