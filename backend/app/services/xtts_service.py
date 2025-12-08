"""
XTTS v2 Local Text-to-Speech service.

This service integrates with a locally running XTTS API server for voice synthesis
and voice cloning capabilities.
"""
import logging
import os
import uuid
import json
import aiofiles
from pathlib import Path
from typing import Optional, AsyncIterator, List, Dict, Any

import httpx

from app.config import settings, XTTSVoiceConfig

logger = logging.getLogger(__name__)


class XTTSService:
    """Service for converting text to speech using local XTTS v2."""

    def __init__(self):
        self.api_url = settings.xtts_api_url
        self.default_speaker = settings.xtts_default_speaker
        self.language = settings.xtts_language
        self.voices_dir = Path(settings.xtts_voices_dir)
        self.voices_file = self.voices_dir / "voices.json"
        self._voices_cache: Optional[List[XTTSVoiceConfig]] = None

    def is_configured(self) -> bool:
        """Check if XTTS is enabled and configured."""
        return settings.xtts_enabled

    def _ensure_voices_dir(self) -> None:
        """Ensure the voices directory exists."""
        self.voices_dir.mkdir(parents=True, exist_ok=True)

    def _load_voices_sync(self) -> List[XTTSVoiceConfig]:
        """Load voices from the voices.json file synchronously."""
        if self._voices_cache is not None:
            return self._voices_cache

        self._ensure_voices_dir()

        if not self.voices_file.exists():
            self._voices_cache = []
            return self._voices_cache

        try:
            with open(self.voices_file, "r") as f:
                voices_data = json.load(f)
                self._voices_cache = [
                    XTTSVoiceConfig(
                        voice_id=v.get("voice_id", ""),
                        label=v.get("label", ""),
                        description=v.get("description", ""),
                        sample_path=v.get("sample_path", ""),
                        temperature=v.get("temperature", 0.75),
                        length_penalty=v.get("length_penalty", 1.0),
                        repetition_penalty=v.get("repetition_penalty", 5.0),
                        speed=v.get("speed", 1.0),
                    )
                    for v in voices_data
                ]
                return self._voices_cache
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load XTTS voices: {e}")
            self._voices_cache = []
            return self._voices_cache

    async def _load_voices(self) -> List[XTTSVoiceConfig]:
        """Load voices from the voices.json file."""
        if self._voices_cache is not None:
            return self._voices_cache

        self._ensure_voices_dir()

        if not self.voices_file.exists():
            self._voices_cache = []
            return self._voices_cache

        try:
            async with aiofiles.open(self.voices_file, "r") as f:
                content = await f.read()
                voices_data = json.loads(content)
                self._voices_cache = [
                    XTTSVoiceConfig(
                        voice_id=v.get("voice_id", ""),
                        label=v.get("label", ""),
                        description=v.get("description", ""),
                        sample_path=v.get("sample_path", ""),
                        temperature=v.get("temperature", 0.75),
                        length_penalty=v.get("length_penalty", 1.0),
                        repetition_penalty=v.get("repetition_penalty", 5.0),
                        speed=v.get("speed", 1.0),
                    )
                    for v in voices_data
                ]
                return self._voices_cache
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load XTTS voices: {e}")
            self._voices_cache = []
            return self._voices_cache

    async def _save_voices(self, voices: List[XTTSVoiceConfig]) -> None:
        """Save voices to the voices.json file."""
        self._ensure_voices_dir()
        self._voices_cache = voices

        voices_data = [v.to_dict() for v in voices]
        async with aiofiles.open(self.voices_file, "w") as f:
            await f.write(json.dumps(voices_data, indent=2))

    async def get_voices(self) -> List[XTTSVoiceConfig]:
        """Get all configured XTTS voices."""
        return await self._load_voices()

    async def get_voice(self, voice_id: str) -> Optional[XTTSVoiceConfig]:
        """Get a specific voice by ID."""
        voices = await self._load_voices()
        for voice in voices:
            if voice.voice_id == voice_id:
                return voice
        return None

    async def check_server_health(self) -> Dict[str, Any]:
        """Check if the XTTS server is running and responsive."""
        if not self.is_configured():
            return {"healthy": False, "error": "XTTS is not enabled"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Try common health endpoints
                for endpoint in ["/health", "/", "/docs"]:
                    try:
                        response = await client.get(f"{self.api_url}{endpoint}")
                        if response.status_code == 200:
                            return {"healthy": True, "endpoint": endpoint}
                    except Exception:
                        continue

                # If no health endpoint works, try the TTS endpoint with a minimal request
                return {"healthy": False, "error": "XTTS server not responding"}
        except httpx.ConnectError:
            return {"healthy": False, "error": f"Cannot connect to XTTS server at {self.api_url}"}
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    async def clone_voice(
        self,
        audio_data: bytes,
        label: str,
        description: str = "",
        filename: str = "sample.wav",
        temperature: float = 0.75,
        length_penalty: float = 1.0,
        repetition_penalty: float = 5.0,
        speed: float = 1.0,
    ) -> XTTSVoiceConfig:
        """
        Clone a voice from an audio sample.

        Args:
            audio_data: The audio file bytes (WAV format preferred)
            label: Display name for the voice
            description: Optional description
            filename: Original filename for extension detection
            temperature: Sampling temperature (0.0-1.0, default 0.75)
            length_penalty: Length penalty for generation (default 1.0)
            repetition_penalty: Repetition penalty (default 5.0)
            speed: Speech speed multiplier (default 1.0)

        Returns:
            XTTSVoiceConfig for the new cloned voice
        """
        if not self.is_configured():
            raise ValueError("XTTS is not enabled")

        self._ensure_voices_dir()

        # Generate a unique voice ID
        voice_id = str(uuid.uuid4())[:8]

        # Determine file extension
        ext = Path(filename).suffix.lower() or ".wav"
        if ext not in [".wav", ".mp3", ".flac", ".ogg"]:
            ext = ".wav"

        # Save the audio sample
        sample_filename = f"{voice_id}{ext}"
        sample_path = self.voices_dir / sample_filename

        async with aiofiles.open(sample_path, "wb") as f:
            await f.write(audio_data)

        # Create voice config
        voice = XTTSVoiceConfig(
            voice_id=voice_id,
            label=label,
            description=description,
            sample_path=str(sample_path),
            temperature=temperature,
            length_penalty=length_penalty,
            repetition_penalty=repetition_penalty,
            speed=speed,
        )

        # Add to voices list
        voices = await self._load_voices()
        voices.append(voice)
        await self._save_voices(voices)

        logger.info(f"Created new XTTS voice: {voice_id} ({label})")
        return voice

    async def delete_voice(self, voice_id: str) -> bool:
        """
        Delete a cloned voice.

        Args:
            voice_id: The ID of the voice to delete

        Returns:
            True if voice was deleted, False if not found
        """
        voices = await self._load_voices()
        voice_to_delete = None

        for voice in voices:
            if voice.voice_id == voice_id:
                voice_to_delete = voice
                break

        if not voice_to_delete:
            return False

        # Remove the sample file
        if voice_to_delete.sample_path:
            sample_path = Path(voice_to_delete.sample_path)
            if sample_path.exists():
                try:
                    sample_path.unlink()
                except OSError as e:
                    logger.warning(f"Failed to delete voice sample file: {e}")

        # Remove from voices list
        voices = [v for v in voices if v.voice_id != voice_id]
        await self._save_voices(voices)

        logger.info(f"Deleted XTTS voice: {voice_id}")
        return True

    async def update_voice(
        self,
        voice_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        temperature: Optional[float] = None,
        length_penalty: Optional[float] = None,
        repetition_penalty: Optional[float] = None,
        speed: Optional[float] = None,
    ) -> Optional[XTTSVoiceConfig]:
        """
        Update a voice's settings.

        Args:
            voice_id: The ID of the voice to update
            label: New display name (optional)
            description: New description (optional)
            temperature: New temperature setting (optional)
            length_penalty: New length penalty setting (optional)
            repetition_penalty: New repetition penalty setting (optional)
            speed: New speed setting (optional)

        Returns:
            Updated XTTSVoiceConfig or None if not found
        """
        voices = await self._load_voices()
        updated_voice = None

        for voice in voices:
            if voice.voice_id == voice_id:
                if label is not None:
                    voice.label = label
                if description is not None:
                    voice.description = description
                if temperature is not None:
                    voice.temperature = temperature
                if length_penalty is not None:
                    voice.length_penalty = length_penalty
                if repetition_penalty is not None:
                    voice.repetition_penalty = repetition_penalty
                if speed is not None:
                    voice.speed = speed
                updated_voice = voice
                break

        if updated_voice:
            await self._save_voices(voices)
            logger.info(f"Updated XTTS voice: {voice_id}")

        return updated_voice

    async def text_to_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        language: Optional[str] = None,
    ) -> bytes:
        """
        Convert text to speech using XTTS.

        Args:
            text: The text to convert to speech
            voice_id: Optional voice ID (uses default if not specified)
            language: Optional language code override

        Returns:
            Audio bytes in WAV format
        """
        if not self.is_configured():
            raise ValueError("XTTS is not enabled")

        lang = language or self.language

        # Get the speaker file path and voice settings
        speaker_wav = None
        voice = None
        if voice_id:
            voice = await self.get_voice(voice_id)
            if voice and voice.sample_path:
                speaker_wav = voice.sample_path
        if not speaker_wav and self.default_speaker:
            speaker_wav = self.default_speaker

        if not speaker_wav:
            raise ValueError("No voice sample configured. Please clone a voice first.")

        # Get voice parameters (use defaults if no voice config found)
        temperature = voice.temperature if voice else 0.75
        length_penalty = voice.length_penalty if voice else 1.0
        repetition_penalty = voice.repetition_penalty if voice else 5.0
        speed = voice.speed if voice else 1.0

        # Call the XTTS API
        # The xtts-api-server typically exposes /tts_to_audio endpoint
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Try the common xtts-api-server endpoint format
            url = f"{self.api_url}/tts_to_audio"

            # Read the speaker wav file for multipart upload
            speaker_path = Path(speaker_wav)
            if not speaker_path.exists():
                raise ValueError(f"Speaker sample file not found: {speaker_wav}")

            async with aiofiles.open(speaker_path, "rb") as f:
                speaker_data = await f.read()

            files = {
                "speaker_wav": (speaker_path.name, speaker_data, "audio/wav"),
            }
            data = {
                "text": text,
                "language": lang,
                "temperature": str(temperature),
                "length_penalty": str(length_penalty),
                "repetition_penalty": str(repetition_penalty),
                "speed": str(speed),
            }

            try:
                response = await client.post(url, data=data, files=files)
            except httpx.ConnectError:
                raise ValueError(f"Cannot connect to XTTS server at {self.api_url}")

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"XTTS API error: {response.status_code} - {error_detail}")

                # Try alternative endpoint format (some XTTS servers use different APIs)
                alt_url = f"{self.api_url}/tts"
                try:
                    alt_response = await client.post(
                        alt_url,
                        json={
                            "text": text,
                            "speaker_wav": speaker_wav,
                            "language": lang,
                            "temperature": temperature,
                            "length_penalty": length_penalty,
                            "repetition_penalty": repetition_penalty,
                            "speed": speed,
                        }
                    )
                    if alt_response.status_code == 200:
                        return alt_response.content
                except Exception:
                    pass

                raise ValueError(f"XTTS API error: {response.status_code}")

            return response.content

    async def text_to_speech_stream(
        self,
        text: str,
        voice_id: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        """
        Convert text to speech and stream audio chunks.

        Args:
            text: The text to convert to speech
            voice_id: Optional voice ID (uses default if not specified)
            language: Optional language code override

        Yields:
            Audio bytes chunks
        """
        if not self.is_configured():
            raise ValueError("XTTS is not enabled")

        lang = language or self.language

        # Get the speaker file path and voice settings
        speaker_wav = None
        voice = None
        if voice_id:
            voice = await self.get_voice(voice_id)
            if voice and voice.sample_path:
                speaker_wav = voice.sample_path
        if not speaker_wav and self.default_speaker:
            speaker_wav = self.default_speaker

        if not speaker_wav:
            raise ValueError("No voice sample configured. Please clone a voice first.")

        # Get voice parameters (use defaults if no voice config found)
        temperature = voice.temperature if voice else 0.75
        length_penalty = voice.length_penalty if voice else 1.0
        repetition_penalty = voice.repetition_penalty if voice else 5.0
        speed = voice.speed if voice else 1.0

        # Call the XTTS API with streaming
        async with httpx.AsyncClient(timeout=120.0) as client:
            url = f"{self.api_url}/tts_stream"

            speaker_path = Path(speaker_wav)
            if not speaker_path.exists():
                raise ValueError(f"Speaker sample file not found: {speaker_wav}")

            async with aiofiles.open(speaker_path, "rb") as f:
                speaker_data = await f.read()

            files = {
                "speaker_wav": (speaker_path.name, speaker_data, "audio/wav"),
            }
            data = {
                "text": text,
                "language": lang,
                "temperature": str(temperature),
                "length_penalty": str(length_penalty),
                "repetition_penalty": str(repetition_penalty),
                "speed": str(speed),
            }

            try:
                async with client.stream("POST", url, data=data, files=files) as response:
                    if response.status_code != 200:
                        # Fall back to non-streaming
                        audio = await self.text_to_speech(text, voice_id, language)
                        yield audio
                        return

                    async for chunk in response.aiter_bytes():
                        yield chunk
            except httpx.ConnectError:
                raise ValueError(f"Cannot connect to XTTS server at {self.api_url}")


# Singleton instance
xtts_service = XTTSService()
