"""
StyleTTS 2 Local Text-to-Speech service.

This service integrates with a locally running StyleTTS 2 API server for voice synthesis
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

from app.config import settings, StyleTTS2VoiceConfig

logger = logging.getLogger(__name__)


class StyleTTS2Service:
    """Service for converting text to speech using local StyleTTS 2."""

    def __init__(self):
        self.api_url = settings.styletts2_api_url
        self.default_speaker = settings.styletts2_default_speaker
        self.voices_dir = Path(settings.styletts2_voices_dir)
        self.voices_file = self.voices_dir / "voices.json"
        self._voices_cache: Optional[List[StyleTTS2VoiceConfig]] = None
        self._libritts_voices_cache: Optional[List[StyleTTS2VoiceConfig]] = None

    def is_configured(self) -> bool:
        """Check if StyleTTS 2 is enabled and configured."""
        return settings.styletts2_enabled

    def _ensure_voices_dir(self) -> None:
        """Ensure the voices directory exists."""
        self.voices_dir.mkdir(parents=True, exist_ok=True)

    def _load_voices_sync(self) -> List[StyleTTS2VoiceConfig]:
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
                    StyleTTS2VoiceConfig(
                        voice_id=v.get("voice_id", ""),
                        label=v.get("label", ""),
                        description=v.get("description", ""),
                        sample_path=v.get("sample_path", ""),
                        alpha=v.get("alpha", 0.3),
                        beta=v.get("beta", 0.7),
                        diffusion_steps=v.get("diffusion_steps", 10),
                        embedding_scale=v.get("embedding_scale", 1.0),
                    )
                    for v in voices_data
                ]
                return self._voices_cache
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load StyleTTS 2 voices: {e}")
            self._voices_cache = []
            return self._voices_cache

    async def _load_voices(self) -> List[StyleTTS2VoiceConfig]:
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
                    StyleTTS2VoiceConfig(
                        voice_id=v.get("voice_id", ""),
                        label=v.get("label", ""),
                        description=v.get("description", ""),
                        sample_path=v.get("sample_path", ""),
                        alpha=v.get("alpha", 0.3),
                        beta=v.get("beta", 0.7),
                        diffusion_steps=v.get("diffusion_steps", 10),
                        embedding_scale=v.get("embedding_scale", 1.0),
                    )
                    for v in voices_data
                ]
                return self._voices_cache
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load StyleTTS 2 voices: {e}")
            self._voices_cache = []
            return self._voices_cache

    async def _save_voices(self, voices: List[StyleTTS2VoiceConfig]) -> None:
        """Save voices to the voices.json file."""
        self._ensure_voices_dir()
        self._voices_cache = voices

        voices_data = [v.to_dict() for v in voices]
        async with aiofiles.open(self.voices_file, "w") as f:
            await f.write(json.dumps(voices_data, indent=2))

    async def _get_libritts_voices(self) -> List[StyleTTS2VoiceConfig]:
        """Get LibriTTS pre-built voices from the server."""
        if self._libritts_voices_cache is not None:
            return self._libritts_voices_cache

        if not self.is_configured():
            return []

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.api_url}/voices/libritts")
                if response.status_code == 200:
                    data = response.json()
                    voices = data.get("voices", [])
                    self._libritts_voices_cache = [
                        StyleTTS2VoiceConfig(
                            voice_id=f"libritts_{v['speaker_id']}",
                            label=v["label"],
                            description=v.get("description", ""),
                            sample_path="",  # Pre-built voices don't have sample paths
                            voice_type="prebuilt",
                            speaker_id=v["speaker_id"],
                        )
                        for v in voices
                    ]
                    return self._libritts_voices_cache
        except Exception as e:
            logger.warning(f"Failed to fetch LibriTTS voices: {e}")

        return []

    def _get_default_voice(self) -> StyleTTS2VoiceConfig:
        """Get the default LJSpeech voice as a pre-built voice config."""
        return StyleTTS2VoiceConfig(
            voice_id="default",
            label="Default (LJSpeech)",
            description="Default female voice, clear and expressive",
            sample_path="",
            voice_type="prebuilt",
            speaker_id=None,
        )

    async def get_voices(self) -> List[StyleTTS2VoiceConfig]:
        """Get all StyleTTS 2 voices (pre-built + cloned)."""
        # Start with the default LJSpeech voice
        voices = [self._get_default_voice()]

        # Add LibriTTS pre-built voices
        libritts_voices = await self._get_libritts_voices()
        voices.extend(libritts_voices)

        # Add user-cloned voices
        cloned_voices = await self._load_voices()
        voices.extend(cloned_voices)

        return voices

    async def get_cloned_voices(self) -> List[StyleTTS2VoiceConfig]:
        """Get only user-cloned voices (not pre-built)."""
        return await self._load_voices()

    async def get_voice(self, voice_id: str) -> Optional[StyleTTS2VoiceConfig]:
        """Get a specific voice by ID (searches all voices including pre-built)."""
        # Check default voice
        if voice_id == "default":
            return self._get_default_voice()

        # Check LibriTTS voices
        if voice_id.startswith("libritts_"):
            libritts_voices = await self._get_libritts_voices()
            for voice in libritts_voices:
                if voice.voice_id == voice_id:
                    return voice

        # Check cloned voices
        voices = await self._load_voices()
        for voice in voices:
            if voice.voice_id == voice_id:
                return voice

        return None

    async def check_server_health(self) -> Dict[str, Any]:
        """Check if the StyleTTS 2 server is running and responsive."""
        if not self.is_configured():
            return {"healthy": False, "error": "StyleTTS 2 is not enabled"}

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

                return {"healthy": False, "error": "StyleTTS 2 server not responding"}
        except httpx.ConnectError:
            return {"healthy": False, "error": f"Cannot connect to StyleTTS 2 server at {self.api_url}"}
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    async def clone_voice(
        self,
        audio_data: bytes,
        label: str,
        description: str = "",
        filename: str = "sample.wav",
        alpha: float = 0.3,
        beta: float = 0.7,
        diffusion_steps: int = 10,
        embedding_scale: float = 1.0,
    ) -> StyleTTS2VoiceConfig:
        """
        Clone a voice from an audio sample.

        Args:
            audio_data: The audio file bytes (WAV format preferred)
            label: Display name for the voice
            description: Optional description
            filename: Original filename for extension detection
            alpha: Timbre parameter (0-1, default 0.3)
            beta: Prosody parameter (0-1, default 0.7)
            diffusion_steps: Diffusion steps for generation (default 10)
            embedding_scale: Classifier free guidance scale (default 1.0)

        Returns:
            StyleTTS2VoiceConfig for the new cloned voice
        """
        if not self.is_configured():
            raise ValueError("StyleTTS 2 is not enabled")

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
        voice = StyleTTS2VoiceConfig(
            voice_id=voice_id,
            label=label,
            description=description,
            sample_path=str(sample_path),
            alpha=alpha,
            beta=beta,
            diffusion_steps=diffusion_steps,
            embedding_scale=embedding_scale,
        )

        # Add to voices list
        voices = await self._load_voices()
        voices.append(voice)
        await self._save_voices(voices)

        logger.info(f"Created new StyleTTS 2 voice: {voice_id} ({label})")
        return voice

    async def delete_voice(self, voice_id: str) -> bool:
        """
        Delete a cloned voice.

        Args:
            voice_id: The ID of the voice to delete

        Returns:
            True if voice was deleted, False if not found

        Raises:
            ValueError if trying to delete a pre-built voice
        """
        # Check if this is a pre-built voice (cannot be deleted)
        if voice_id == "default" or voice_id.startswith("libritts_"):
            raise ValueError("Cannot delete pre-built voices")

        voices = await self._load_voices()
        voice_to_delete = None

        for voice in voices:
            if voice.voice_id == voice_id:
                voice_to_delete = voice
                break

        if not voice_to_delete:
            return False

        # Double-check voice type (in case of data corruption)
        if voice_to_delete.voice_type == "prebuilt":
            raise ValueError("Cannot delete pre-built voices")

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

        logger.info(f"Deleted StyleTTS 2 voice: {voice_id}")
        return True

    async def update_voice(
        self,
        voice_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
        diffusion_steps: Optional[int] = None,
        embedding_scale: Optional[float] = None,
    ) -> Optional[StyleTTS2VoiceConfig]:
        """
        Update a voice's settings.

        Args:
            voice_id: The ID of the voice to update
            label: New display name (optional)
            description: New description (optional)
            alpha: New alpha/timbre setting (optional)
            beta: New beta/prosody setting (optional)
            diffusion_steps: New diffusion steps setting (optional)
            embedding_scale: New embedding scale setting (optional)

        Returns:
            Updated StyleTTS2VoiceConfig or None if not found
        """
        voices = await self._load_voices()
        updated_voice = None

        for voice in voices:
            if voice.voice_id == voice_id:
                if label is not None:
                    voice.label = label
                if description is not None:
                    voice.description = description
                if alpha is not None:
                    voice.alpha = alpha
                if beta is not None:
                    voice.beta = beta
                if diffusion_steps is not None:
                    voice.diffusion_steps = diffusion_steps
                if embedding_scale is not None:
                    voice.embedding_scale = embedding_scale
                updated_voice = voice
                break

        if updated_voice:
            await self._save_voices(voices)
            logger.info(f"Updated StyleTTS 2 voice: {voice_id}")

        return updated_voice

    async def text_to_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
        diffusion_steps: Optional[int] = None,
        embedding_scale: Optional[float] = None,
        speed: Optional[float] = None,
    ) -> bytes:
        """
        Convert text to speech using StyleTTS 2.

        Args:
            text: The text to convert to speech
            voice_id: Optional voice ID (uses default LJSpeech voice if not specified)
                      - "default" or None: Uses default LJSpeech voice
                      - "libritts_N": Uses LibriTTS pre-built voice with speaker_id N
                      - Other: Uses cloned voice with sample file
            alpha: Override timbre parameter (0-1), uses voice setting or default if None
            beta: Override prosody parameter (0-1), uses voice setting or default if None
            diffusion_steps: Override quality/speed (1-50), uses voice setting or default if None
            embedding_scale: Override classifier free guidance, uses voice setting or default if None
            speed: Override speech speed (0.5-2.0), uses voice setting or default if None

        Returns:
            Audio bytes in WAV format
        """
        if not self.is_configured():
            raise ValueError("StyleTTS 2 is not enabled")

        # Get voice configuration
        voice = None
        if voice_id and voice_id != "default":
            voice = await self.get_voice(voice_id)

        # Get voice parameters: override > voice config > defaults
        alpha = alpha if alpha is not None else (voice.alpha if voice else 0.3)
        beta = beta if beta is not None else (voice.beta if voice else 0.7)
        diffusion_steps = diffusion_steps if diffusion_steps is not None else (voice.diffusion_steps if voice else 10)
        embedding_scale = embedding_scale if embedding_scale is not None else (voice.embedding_scale if voice else 1.0)
        speed = speed if speed is not None else (getattr(voice, 'speed', None) if voice else 1.0) or 1.0

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Case 1: Default LJSpeech voice
            if not voice_id or voice_id == "default":
                logger.info("Using default LJSpeech voice")
                url = f"{self.api_url}/tts_default"
                data = {
                    "text": text,
                    "alpha": str(alpha),
                    "beta": str(beta),
                    "diffusion_steps": str(diffusion_steps),
                    "embedding_scale": str(embedding_scale),
                    "speed": str(speed),
                }

                try:
                    response = await client.post(url, data=data)
                except httpx.ConnectError:
                    raise ValueError(f"Cannot connect to StyleTTS 2 server at {self.api_url}")

                if response.status_code != 200:
                    error_detail = response.text
                    logger.error(f"StyleTTS 2 API error: {response.status_code} - {error_detail}")
                    raise ValueError(f"StyleTTS 2 API error: {response.status_code}")

                return response.content

            # Case 2: LibriTTS pre-built voice
            if voice and voice.voice_type == "prebuilt" and voice.speaker_id is not None:
                logger.info(f"Using LibriTTS voice with speaker_id={voice.speaker_id}")
                url = f"{self.api_url}/tts_libritts"
                data = {
                    "text": text,
                    "speaker_id": str(voice.speaker_id),
                    "alpha": str(alpha),
                    "beta": str(beta),
                    "diffusion_steps": str(diffusion_steps),
                    "embedding_scale": str(embedding_scale),
                    "speed": str(speed),
                }

                try:
                    response = await client.post(url, data=data)
                except httpx.ConnectError:
                    raise ValueError(f"Cannot connect to StyleTTS 2 server at {self.api_url}")

                if response.status_code != 200:
                    error_detail = response.text
                    logger.error(f"StyleTTS 2 API error: {response.status_code} - {error_detail}")
                    raise ValueError(f"StyleTTS 2 API error: {response.status_code}")

                return response.content

            # Case 3: Cloned voice with speaker sample file
            speaker_wav = voice.sample_path if voice and voice.sample_path else self.default_speaker

            if not speaker_wav:
                # Fall back to default voice if no speaker configured
                logger.info("No speaker sample found, falling back to default LJSpeech voice")
                url = f"{self.api_url}/tts_default"
                data = {
                    "text": text,
                    "alpha": str(alpha),
                    "beta": str(beta),
                    "diffusion_steps": str(diffusion_steps),
                    "embedding_scale": str(embedding_scale),
                    "speed": str(speed),
                }

                try:
                    response = await client.post(url, data=data)
                except httpx.ConnectError:
                    raise ValueError(f"Cannot connect to StyleTTS 2 server at {self.api_url}")

                if response.status_code != 200:
                    error_detail = response.text
                    logger.error(f"StyleTTS 2 API error: {response.status_code} - {error_detail}")
                    raise ValueError(f"StyleTTS 2 API error: {response.status_code}")

                return response.content

            # Use cloned voice with speaker reference
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
                "alpha": str(alpha),
                "beta": str(beta),
                "diffusion_steps": str(diffusion_steps),
                "embedding_scale": str(embedding_scale),
                "speed": str(speed),
            }

            try:
                response = await client.post(url, data=data, files=files)
            except httpx.ConnectError:
                raise ValueError(f"Cannot connect to StyleTTS 2 server at {self.api_url}")

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"StyleTTS 2 API error: {response.status_code} - {error_detail}")
                raise ValueError(f"StyleTTS 2 API error: {response.status_code}")

            return response.content

    async def text_to_speech_stream(
        self,
        text: str,
        voice_id: Optional[str] = None,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
        diffusion_steps: Optional[int] = None,
        embedding_scale: Optional[float] = None,
        speed: Optional[float] = None,
    ) -> AsyncIterator[bytes]:
        """
        Convert text to speech and stream audio chunks.

        Args:
            text: The text to convert to speech
            voice_id: Optional voice ID (uses default LJSpeech voice if not specified)
            alpha: Override timbre parameter (0-1), uses voice setting or default if None
            beta: Override prosody parameter (0-1), uses voice setting or default if None
            diffusion_steps: Override quality/speed (1-50), uses voice setting or default if None
            embedding_scale: Override classifier free guidance, uses voice setting or default if None
            speed: Override speech speed (0.5-2.0), uses voice setting or default if None

        Yields:
            Audio bytes chunks
        """
        if not self.is_configured():
            raise ValueError("StyleTTS 2 is not enabled")

        # Get the speaker file path and voice settings
        speaker_wav = None
        voice = None
        if voice_id:
            voice = await self.get_voice(voice_id)
            if voice and voice.sample_path:
                speaker_wav = voice.sample_path
        if not speaker_wav and self.default_speaker:
            speaker_wav = self.default_speaker

        # Get voice parameters: override > voice config > defaults
        alpha = alpha if alpha is not None else (voice.alpha if voice else 0.3)
        beta = beta if beta is not None else (voice.beta if voice else 0.7)
        diffusion_steps = diffusion_steps if diffusion_steps is not None else (voice.diffusion_steps if voice else 10)
        embedding_scale = embedding_scale if embedding_scale is not None else (voice.embedding_scale if voice else 1.0)
        speed = speed if speed is not None else (getattr(voice, 'speed', None) if voice else 1.0) or 1.0

        # If no speaker configured, fall back to non-streaming with default voice
        if not speaker_wav:
            logger.info("No voice configured, using default LJSpeech voice (non-streaming)")
            audio = await self.text_to_speech(
                text, voice_id,
                alpha=alpha,
                beta=beta,
                diffusion_steps=diffusion_steps,
                embedding_scale=embedding_scale,
                speed=speed,
            )
            yield audio
            return

        # Call the StyleTTS 2 API with streaming for cloned voices
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
                "alpha": str(alpha),
                "beta": str(beta),
                "diffusion_steps": str(diffusion_steps),
                "embedding_scale": str(embedding_scale),
                "speed": str(speed),
            }

            try:
                async with client.stream("POST", url, data=data, files=files) as response:
                    if response.status_code != 200:
                        # Fall back to non-streaming
                        audio = await self.text_to_speech(
                            text, voice_id,
                            alpha=alpha,
                            beta=beta,
                            diffusion_steps=diffusion_steps,
                            embedding_scale=embedding_scale,
                            speed=speed,
                        )
                        yield audio
                        return

                    async for chunk in response.aiter_bytes():
                        yield chunk
            except httpx.ConnectError:
                raise ValueError(f"Cannot connect to StyleTTS 2 server at {self.api_url}")


# Singleton instance
styletts2_service = StyleTTS2Service()
