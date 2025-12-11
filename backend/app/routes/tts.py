"""
Text-to-Speech API routes.

Supports ElevenLabs (cloud), XTTS v2 (local), and StyleTTS 2 (local) TTS providers.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from app.services import tts_service
from app.config import settings

router = APIRouter(prefix="/api/tts", tags=["tts"])


class TTSRequest(BaseModel):
    """Request body for text-to-speech conversion."""
    model_config = {"protected_namespaces": ()}

    text: str
    voice_id: Optional[str] = None
    model_id: Optional[str] = None


@router.post("/speak")
async def text_to_speech(data: TTSRequest):
    """
    Convert text to speech and return audio.

    Returns audio/mpeg for ElevenLabs or audio/wav for XTTS.
    """
    if not tts_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Text-to-speech service is not configured."
        )

    if not data.text or not data.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    # Limit text length to prevent abuse
    if len(data.text) > 5000:
        raise HTTPException(status_code=400, detail="Text exceeds maximum length of 5000 characters")

    try:
        audio_bytes = await tts_service.text_to_speech(
            text=data.text,
            voice_id=data.voice_id,
            model_id=data.model_id,
        )

        # Determine content type based on provider
        provider = tts_service.get_provider()
        content_type = "audio/wav" if provider in ("xtts", "styletts2") else "audio/mpeg"

        return StreamingResponse(
            iter([audio_bytes]),
            media_type=content_type,
            headers={
                "Content-Disposition": "inline",
            }
        )
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate speech: {str(e)}")


@router.post("/speak/stream")
async def text_to_speech_stream(data: TTSRequest):
    """
    Convert text to speech and stream audio.

    Streams audio chunks for faster playback start.
    """
    if not tts_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Text-to-speech service is not configured."
        )

    if not data.text or not data.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    if len(data.text) > 5000:
        raise HTTPException(status_code=400, detail="Text exceeds maximum length of 5000 characters")

    async def generate():
        async for chunk in tts_service.text_to_speech_stream(
            text=data.text,
            voice_id=data.voice_id,
            model_id=data.model_id,
        ):
            yield chunk

    # Determine content type based on provider
    provider = tts_service.get_provider()
    content_type = "audio/wav" if provider in ("xtts", "styletts2") else "audio/mpeg"

    return StreamingResponse(
        generate(),
        media_type=content_type,
        headers={
            "Content-Disposition": "inline",
        }
    )


@router.get("/status")
async def tts_status():
    """
    Check if TTS service is configured and available.

    Returns provider information, available voices, and health status.
    """
    return await tts_service.get_status()


# ============================================================================
# Voice Management Endpoints (XTTS and StyleTTS 2)
# ============================================================================

@router.get("/voices")
async def list_voices():
    """
    List all available voices for the current TTS provider.
    """
    if not tts_service.is_configured():
        return {"voices": [], "provider": "none"}

    voices = await tts_service.get_voices_async()
    provider = tts_service.get_provider()

    return {
        "voices": voices,
        "provider": provider,
    }


@router.post("/voices/clone")
async def clone_voice(
    audio_file: UploadFile = File(..., description="Audio sample for voice cloning (WAV preferred)"),
    label: str = Form(..., description="Display name for the voice"),
    description: str = Form("", description="Optional description"),
    # XTTS parameters
    temperature: float = Form(0.75, description="XTTS: Sampling temperature (0.0-1.0)"),
    length_penalty: float = Form(1.0, description="XTTS: Length penalty for generation"),
    repetition_penalty: float = Form(5.0, description="XTTS: Repetition penalty"),
    speed: float = Form(1.0, description="XTTS: Speech speed multiplier"),
    # StyleTTS 2 parameters
    alpha: float = Form(0.3, description="StyleTTS2: Timbre parameter (0.0-1.0)"),
    beta: float = Form(0.7, description="StyleTTS2: Prosody parameter (0.0-1.0)"),
    diffusion_steps: int = Form(10, description="StyleTTS2: Diffusion steps (1-50)"),
    embedding_scale: float = Form(1.0, description="StyleTTS2: Embedding scale"),
):
    """
    Clone a voice from an audio sample (XTTS/StyleTTS 2 only).

    Upload a WAV audio file (6-30 seconds of clear speech recommended)
    to create a new cloned voice for TTS synthesis.
    """
    provider = tts_service.get_provider()

    if provider not in ("xtts", "styletts2"):
        raise HTTPException(
            status_code=400,
            detail="Voice cloning is only available with XTTS or StyleTTS 2 providers"
        )

    if not tts_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail=f"{provider.upper()} is not configured or server is not available"
        )

    # Validate file type
    allowed_types = [
        "audio/wav", "audio/x-wav", "audio/wave",
        "audio/mp3", "audio/mpeg",
        "audio/flac", "audio/x-flac",
        "audio/ogg", "audio/x-ogg",
    ]
    content_type = audio_file.content_type or ""
    if content_type not in allowed_types and not audio_file.filename.endswith(('.wav', '.mp3', '.flac', '.ogg')):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Please upload a WAV, MP3, FLAC, or OGG audio file."
        )

    # Validate file size (max 50MB)
    audio_data = await audio_file.read()
    if len(audio_data) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="File too large. Maximum size is 50MB."
        )

    if len(audio_data) < 1000:
        raise HTTPException(
            status_code=400,
            detail="Audio file too small. Please provide a longer sample."
        )

    try:
        if provider == "xtts":
            # Validate XTTS parameter ranges
            if not 0.0 <= temperature <= 1.0:
                raise HTTPException(status_code=400, detail="Temperature must be between 0.0 and 1.0")
            if not 0.1 <= speed <= 3.0:
                raise HTTPException(status_code=400, detail="Speed must be between 0.1 and 3.0")
            if not 0.1 <= length_penalty <= 10.0:
                raise HTTPException(status_code=400, detail="Length penalty must be between 0.1 and 10.0")
            if not 0.1 <= repetition_penalty <= 20.0:
                raise HTTPException(status_code=400, detail="Repetition penalty must be between 0.1 and 20.0")

            from app.services.xtts_service import xtts_service

            voice = await xtts_service.clone_voice(
                audio_data=audio_data,
                label=label,
                description=description,
                filename=audio_file.filename or "sample.wav",
                temperature=temperature,
                length_penalty=length_penalty,
                repetition_penalty=repetition_penalty,
                speed=speed,
            )
        else:  # styletts2
            # Validate StyleTTS 2 parameter ranges
            if not 0.0 <= alpha <= 1.0:
                raise HTTPException(status_code=400, detail="Alpha must be between 0.0 and 1.0")
            if not 0.0 <= beta <= 1.0:
                raise HTTPException(status_code=400, detail="Beta must be between 0.0 and 1.0")
            if not 1 <= diffusion_steps <= 50:
                raise HTTPException(status_code=400, detail="Diffusion steps must be between 1 and 50")
            if not 0.0 <= embedding_scale <= 10.0:
                raise HTTPException(status_code=400, detail="Embedding scale must be between 0.0 and 10.0")

            from app.services.styletts2_service import styletts2_service

            voice = await styletts2_service.clone_voice(
                audio_data=audio_data,
                label=label,
                description=description,
                filename=audio_file.filename or "sample.wav",
                alpha=alpha,
                beta=beta,
                diffusion_steps=diffusion_steps,
                embedding_scale=embedding_scale,
            )

        return {
            "success": True,
            "voice": voice.to_dict(),
            "message": f"Voice '{label}' created successfully",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clone voice: {str(e)}")


@router.get("/voices/{voice_id}")
async def get_voice(voice_id: str):
    """
    Get details for a specific voice.
    """
    provider = tts_service.get_provider()

    if provider == "xtts":
        from app.services.xtts_service import xtts_service
        voice = await xtts_service.get_voice(voice_id)
        if voice:
            return voice.to_dict()
    elif provider == "styletts2":
        from app.services.styletts2_service import styletts2_service
        voice = await styletts2_service.get_voice(voice_id)
        if voice:
            return voice.to_dict()
    elif provider == "elevenlabs":
        voices = tts_service.get_voices()
        for v in voices:
            if v.get("voice_id") == voice_id:
                return v

    raise HTTPException(status_code=404, detail="Voice not found")


class VoiceUpdateRequest(BaseModel):
    """Request body for updating voice settings."""
    model_config = {"protected_namespaces": ()}

    label: Optional[str] = None
    description: Optional[str] = None
    # XTTS parameters
    temperature: Optional[float] = None
    length_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    speed: Optional[float] = None
    # StyleTTS 2 parameters
    alpha: Optional[float] = None
    beta: Optional[float] = None
    diffusion_steps: Optional[int] = None
    embedding_scale: Optional[float] = None


@router.put("/voices/{voice_id}")
async def update_voice(voice_id: str, data: VoiceUpdateRequest):
    """
    Update a voice's settings (XTTS/StyleTTS 2 only).

    Update the label, description, or synthesis parameters for a cloned voice.
    """
    provider = tts_service.get_provider()

    if provider not in ("xtts", "styletts2"):
        raise HTTPException(
            status_code=400,
            detail="Voice updates are only available with XTTS or StyleTTS 2 providers"
        )

    if provider == "xtts":
        # Validate XTTS parameter ranges if provided
        if data.temperature is not None and not 0.0 <= data.temperature <= 1.0:
            raise HTTPException(status_code=400, detail="Temperature must be between 0.0 and 1.0")
        if data.speed is not None and not 0.1 <= data.speed <= 3.0:
            raise HTTPException(status_code=400, detail="Speed must be between 0.1 and 3.0")
        if data.length_penalty is not None and not 0.1 <= data.length_penalty <= 10.0:
            raise HTTPException(status_code=400, detail="Length penalty must be between 0.1 and 10.0")
        if data.repetition_penalty is not None and not 0.1 <= data.repetition_penalty <= 20.0:
            raise HTTPException(status_code=400, detail="Repetition penalty must be between 0.1 and 20.0")

        from app.services.xtts_service import xtts_service

        voice = await xtts_service.update_voice(
            voice_id=voice_id,
            label=data.label,
            description=data.description,
            temperature=data.temperature,
            length_penalty=data.length_penalty,
            repetition_penalty=data.repetition_penalty,
            speed=data.speed,
        )
    else:  # styletts2
        # Validate StyleTTS 2 parameter ranges if provided
        if data.alpha is not None and not 0.0 <= data.alpha <= 1.0:
            raise HTTPException(status_code=400, detail="Alpha must be between 0.0 and 1.0")
        if data.beta is not None and not 0.0 <= data.beta <= 1.0:
            raise HTTPException(status_code=400, detail="Beta must be between 0.0 and 1.0")
        if data.diffusion_steps is not None and not 1 <= data.diffusion_steps <= 50:
            raise HTTPException(status_code=400, detail="Diffusion steps must be between 1 and 50")
        if data.embedding_scale is not None and not 0.0 <= data.embedding_scale <= 10.0:
            raise HTTPException(status_code=400, detail="Embedding scale must be between 0.0 and 10.0")

        from app.services.styletts2_service import styletts2_service

        voice = await styletts2_service.update_voice(
            voice_id=voice_id,
            label=data.label,
            description=data.description,
            alpha=data.alpha,
            beta=data.beta,
            diffusion_steps=data.diffusion_steps,
            embedding_scale=data.embedding_scale,
        )

    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    return {
        "success": True,
        "voice": voice.to_dict(),
        "message": "Voice updated successfully",
    }


@router.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str):
    """
    Delete a cloned voice (XTTS/StyleTTS 2 only).
    """
    provider = tts_service.get_provider()

    if provider not in ("xtts", "styletts2"):
        raise HTTPException(
            status_code=400,
            detail="Voice deletion is only available with XTTS or StyleTTS 2 providers"
        )

    if provider == "xtts":
        from app.services.xtts_service import xtts_service
        success = await xtts_service.delete_voice(voice_id)
    else:  # styletts2
        from app.services.styletts2_service import styletts2_service
        success = await styletts2_service.delete_voice(voice_id)

    if not success:
        raise HTTPException(status_code=404, detail="Voice not found")

    return {"success": True, "message": "Voice deleted successfully"}


@router.get("/xtts/health")
async def xtts_health():
    """
    Check XTTS server health (XTTS only).
    """
    if not settings.xtts_enabled:
        return {
            "enabled": False,
            "healthy": False,
            "error": "XTTS is not enabled",
        }

    from app.services.xtts_service import xtts_service

    health = await xtts_service.check_server_health()
    return {
        "enabled": True,
        "server_url": settings.xtts_api_url,
        **health,
    }


@router.get("/styletts2/health")
async def styletts2_health():
    """
    Check StyleTTS 2 server health (StyleTTS 2 only).
    """
    if not settings.styletts2_enabled:
        return {
            "enabled": False,
            "healthy": False,
            "error": "StyleTTS 2 is not enabled",
        }

    from app.services.styletts2_service import styletts2_service

    health = await styletts2_service.check_server_health()
    return {
        "enabled": True,
        "server_url": settings.styletts2_api_url,
        **health,
    }
