"""
Whisper STT Service

Client service for communicating with the local Whisper STT server.
"""
import logging
from typing import Optional, Dict, Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class WhisperService:
    """Service for speech-to-text using local Whisper server."""

    def __init__(self):
        self.api_url = getattr(settings, 'whisper_api_url', 'http://localhost:8030')
        self.enabled = getattr(settings, 'whisper_enabled', False)
        self._server_healthy = False

    def is_configured(self) -> bool:
        """Check if Whisper STT is configured."""
        return self.enabled

    async def check_health(self) -> bool:
        """Check if the Whisper server is healthy and responding."""
        if not self.enabled:
            return False

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.api_url}/health")
                if response.status_code == 200:
                    data = response.json()
                    self._server_healthy = data.get("model_loaded", False)
                    return self._server_healthy
        except Exception as e:
            logger.warning(f"Whisper server health check failed: {e}")
            self._server_healthy = False

        return False

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        language: Optional[str] = None,
        initial_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Transcribe audio data to text.

        Args:
            audio_data: Raw audio bytes
            filename: Original filename (for format detection)
            content_type: MIME type of the audio
            language: Optional language code (auto-detect if not specified)
            initial_prompt: Optional context hint for better accuracy

        Returns:
            Dict with transcription result:
            {
                "text": str,           # The transcribed text
                "language": str,       # Detected language code
                "language_probability": float,
                "duration": float,     # Audio duration in seconds
                "processing_time": float,
            }
        """
        if not self.enabled:
            raise ValueError("Whisper STT is not enabled")

        try:
            # Build multipart form data
            files = {
                "audio_file": (filename, audio_data, content_type)
            }

            data = {
                "vad_filter": "true",
                "beam_size": "5",
            }

            if language:
                data["language"] = language

            if initial_prompt:
                data["initial_prompt"] = initial_prompt

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.api_url}/transcribe",
                    files=files,
                    data=data,
                )

                if response.status_code != 200:
                    error_detail = response.text
                    logger.error(f"Whisper API error: {response.status_code} - {error_detail}")
                    raise ValueError(f"Transcription failed: {response.status_code}")

                return response.json()

        except httpx.TimeoutException:
            logger.error("Whisper transcription timed out")
            raise ValueError("Transcription timed out - audio may be too long")
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            raise

    async def get_status(self) -> Dict[str, Any]:
        """Get Whisper server status information."""
        if not self.enabled:
            return {
                "configured": False,
                "provider": "none",
                "server_healthy": False,
            }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.api_url}/health")
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "configured": True,
                        "provider": "whisper",
                        "server_healthy": data.get("model_loaded", False),
                        "model": data.get("model", "unknown"),
                        "device": data.get("device", "unknown"),
                        "cuda_available": data.get("cuda_available", False),
                    }
        except Exception as e:
            logger.warning(f"Failed to get Whisper status: {e}")

        return {
            "configured": True,
            "provider": "whisper",
            "server_healthy": False,
        }


# Global service instance
whisper_service = WhisperService()
