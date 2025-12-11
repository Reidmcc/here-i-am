"""
Unified Text-to-Speech service supporting ElevenLabs API and local XTTS v2.
"""
import logging
from typing import Optional, AsyncIterator, List, Dict, Any

import httpx

from app.config import settings, VoiceConfig

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
    """
    Unified TTS service that delegates to the appropriate provider.

    This service checks configuration to determine whether to use
    StyleTTS 2 (local), XTTS (local), or ElevenLabs (cloud) for
    text-to-speech conversion.
    """

    def __init__(self):
        self._elevenlabs = ElevenLabsService()
        self._xtts = None  # Lazy loaded to avoid circular imports
        self._styletts2 = None  # Lazy loaded to avoid circular imports

    @property
    def xtts(self):
        """Lazy load XTTS service to avoid circular imports."""
        if self._xtts is None:
            from app.services.xtts_service import xtts_service
            self._xtts = xtts_service
        return self._xtts

    @property
    def styletts2(self):
        """Lazy load StyleTTS 2 service to avoid circular imports."""
        if self._styletts2 is None:
            from app.services.styletts2_service import styletts2_service
            self._styletts2 = styletts2_service
        return self._styletts2

    def get_provider(self) -> str:
        """Get the current TTS provider name."""
        return settings.get_tts_provider()

    def is_configured(self) -> bool:
        """Check if any TTS provider is configured."""
        provider = self.get_provider()
        if provider == "styletts2":
            return self.styletts2.is_configured()
        elif provider == "xtts":
            return self.xtts.is_configured()
        elif provider == "elevenlabs":
            return self._elevenlabs.is_configured()
        return False

    def get_voices(self) -> List[Dict[str, Any]]:
        """
        Get all available voices from the active provider.

        Returns a list of voice dicts with provider information.
        """
        provider = self.get_provider()
        voices = []

        if provider == "styletts2":
            # StyleTTS 2 voices are loaded synchronously here for compatibility
            styletts2_voices = self.styletts2._load_voices_sync()
            voices = [v.to_dict() for v in styletts2_voices]
        elif provider == "xtts":
            # XTTS voices are loaded synchronously here for compatibility
            xtts_voices = self.xtts._load_voices_sync()
            voices = [v.to_dict() for v in xtts_voices]
        elif provider == "elevenlabs":
            el_voices = self._elevenlabs.get_voices()
            voices = [v.to_dict() for v in el_voices]

        return voices

    async def get_voices_async(self) -> List[Dict[str, Any]]:
        """
        Get all available voices from the active provider (async version).

        Returns a list of voice dicts with provider information.
        """
        provider = self.get_provider()
        voices = []

        if provider == "styletts2":
            styletts2_voices = await self.styletts2.get_voices()
            voices = [v.to_dict() for v in styletts2_voices]
        elif provider == "xtts":
            xtts_voices = await self.xtts.get_voices()
            voices = [v.to_dict() for v in xtts_voices]
        elif provider == "elevenlabs":
            el_voices = self._elevenlabs.get_voices()
            voices = [v.to_dict() for v in el_voices]

        return voices

    def get_default_voice_id(self) -> Optional[str]:
        """Get the default voice ID for the active provider."""
        provider = self.get_provider()

        if provider == "styletts2":
            voices = self.styletts2._load_voices_sync()
            if voices:
                return voices[0].voice_id
            return None
        elif provider == "xtts":
            voices = self.xtts._load_voices_sync()
            if voices:
                return voices[0].voice_id
            return None
        elif provider == "elevenlabs":
            return settings.elevenlabs_voice_id

        return None

    async def text_to_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
        diffusion_steps: Optional[int] = None,
        embedding_scale: Optional[float] = None,
        speed: Optional[float] = None,
    ) -> bytes:
        """
        Convert text to speech using the active provider.

        Args:
            text: The text to convert to speech
            voice_id: Optional voice ID override
            model_id: Optional model ID override (ElevenLabs only)
            alpha: StyleTTS 2 timbre parameter override (0-1)
            beta: StyleTTS 2 prosody parameter override (0-1)
            diffusion_steps: StyleTTS 2 quality/speed override (1-50)
            embedding_scale: StyleTTS 2 classifier free guidance override
            speed: StyleTTS 2 speech speed override (0.5-2.0)

        Returns:
            Audio bytes (MP3 for ElevenLabs, WAV for XTTS/StyleTTS 2)
        """
        provider = self.get_provider()

        if provider == "styletts2":
            return await self.styletts2.text_to_speech(
                text, voice_id,
                alpha=alpha,
                beta=beta,
                diffusion_steps=diffusion_steps,
                embedding_scale=embedding_scale,
                speed=speed,
            )
        elif provider == "xtts":
            return await self.xtts.text_to_speech(text, voice_id)
        elif provider == "elevenlabs":
            return await self._elevenlabs.text_to_speech(text, voice_id, model_id)
        else:
            raise ValueError("No TTS provider is configured")

    async def text_to_speech_stream(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
        diffusion_steps: Optional[int] = None,
        embedding_scale: Optional[float] = None,
        speed: Optional[float] = None,
    ) -> AsyncIterator[bytes]:
        """
        Convert text to speech and stream audio bytes.

        Args:
            text: The text to convert to speech
            voice_id: Optional voice ID override
            model_id: Optional model ID override (ElevenLabs only)
            alpha: StyleTTS 2 timbre parameter override (0-1)
            beta: StyleTTS 2 prosody parameter override (0-1)
            diffusion_steps: StyleTTS 2 quality/speed override (1-50)
            embedding_scale: StyleTTS 2 classifier free guidance override
            speed: StyleTTS 2 speech speed override (0.5-2.0)

        Yields:
            Audio bytes chunks
        """
        provider = self.get_provider()

        if provider == "styletts2":
            async for chunk in self.styletts2.text_to_speech_stream(
                text, voice_id,
                alpha=alpha,
                beta=beta,
                diffusion_steps=diffusion_steps,
                embedding_scale=embedding_scale,
                speed=speed,
            ):
                yield chunk
        elif provider == "xtts":
            async for chunk in self.xtts.text_to_speech_stream(text, voice_id):
                yield chunk
        elif provider == "elevenlabs":
            async for chunk in self._elevenlabs.text_to_speech_stream(text, voice_id, model_id):
                yield chunk
        else:
            raise ValueError("No TTS provider is configured")

    async def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive TTS status including provider info.

        Returns:
            Dict with provider, configuration status, and available voices
        """
        provider = self.get_provider()

        if provider == "none":
            return {
                "configured": False,
                "provider": "none",
                "voices": [],
                "default_voice_id": None,
                "model_id": None,
            }

        voices = await self.get_voices_async()
        default_voice_id = self.get_default_voice_id()

        status = {
            "configured": True,
            "provider": provider,
            "voices": voices,
            "default_voice_id": default_voice_id,
        }

        if provider == "elevenlabs":
            status["model_id"] = self._elevenlabs.model_id
        elif provider == "styletts2":
            # Check StyleTTS 2 server health
            health = await self.styletts2.check_server_health()
            status["server_healthy"] = health.get("healthy", False)
            status["server_url"] = settings.styletts2_api_url
            if not health.get("healthy"):
                status["server_error"] = health.get("error", "Unknown error")
        elif provider == "xtts":
            # Check XTTS server health
            health = await self.xtts.check_server_health()
            status["server_healthy"] = health.get("healthy", False)
            status["server_url"] = settings.xtts_api_url
            if not health.get("healthy"):
                status["server_error"] = health.get("error", "Unknown error")

        return status


# Singleton instance
tts_service = TTSService()
