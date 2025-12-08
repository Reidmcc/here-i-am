"""
Text-to-Speech API routes.

Supports both ElevenLabs (cloud) and XTTS v2 (local) TTS providers.
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
        content_type = "audio/wav" if provider == "xtts" else "audio/mpeg"

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
    content_type = "audio/wav" if provider == "xtts" else "audio/mpeg"

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
# XTTS Voice Management Endpoints
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
):
    """
    Clone a voice from an audio sample (XTTS only).

    Upload a WAV audio file (6-30 seconds of clear speech recommended)
    to create a new cloned voice for XTTS synthesis.
    """
    provider = tts_service.get_provider()

    if provider != "xtts":
        raise HTTPException(
            status_code=400,
            detail="Voice cloning is only available with XTTS provider"
        )

    if not tts_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="XTTS is not configured or server is not available"
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
        from app.services.xtts_service import xtts_service

        voice = await xtts_service.clone_voice(
            audio_data=audio_data,
            label=label,
            description=description,
            filename=audio_file.filename or "sample.wav",
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
    elif provider == "elevenlabs":
        voices = tts_service.get_voices()
        for v in voices:
            if v.get("voice_id") == voice_id:
                return v

    raise HTTPException(status_code=404, detail="Voice not found")


@router.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str):
    """
    Delete a cloned voice (XTTS only).
    """
    provider = tts_service.get_provider()

    if provider != "xtts":
        raise HTTPException(
            status_code=400,
            detail="Voice deletion is only available with XTTS provider"
        )

    from app.services.xtts_service import xtts_service

    success = await xtts_service.delete_voice(voice_id)
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
