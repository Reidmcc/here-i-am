"""
Text-to-Speech API routes (ElevenLabs).

The local voice servers (StyleTTS 2, XTTS, Whisper STT) are not proxied here:
the browser on the machine running them calls them directly (issue #399; see
docs/local-services.md). ElevenLabs stays on the backend because its API key
is server-side.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services import tts_service

router = APIRouter(prefix="/api/tts", tags=["tts"])


class TTSRequest(BaseModel):
    """Request body for text-to-speech conversion."""
    model_config = {"protected_namespaces": ()}

    text: str
    voice_id: Optional[str] = None
    model_id: Optional[str] = None


def _validate_request(data: TTSRequest) -> None:
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


@router.post("/speak")
async def text_to_speech(data: TTSRequest):
    """
    Convert text to speech and return MP3 audio (audio/mpeg).
    """
    _validate_request(data)

    try:
        audio_bytes = await tts_service.text_to_speech(
            text=data.text,
            voice_id=data.voice_id,
            model_id=data.model_id,
        )

        return StreamingResponse(
            iter([audio_bytes]),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline",
            }
        )
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate speech: {str(e)}") from e


@router.post("/speak/stream")
async def text_to_speech_stream(data: TTSRequest):
    """
    Convert text to speech and stream MP3 audio (audio/mpeg).

    Streams audio chunks for faster playback start.
    """
    _validate_request(data)

    async def generate():
        async for chunk in tts_service.text_to_speech_stream(
            text=data.text,
            voice_id=data.voice_id,
            model_id=data.model_id,
        ):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": "inline",
        }
    )


@router.get("/status")
async def tts_status():
    """
    Check if ElevenLabs TTS is configured.

    Returns {configured, provider ("elevenlabs" or "none"), voices,
    default_voice_id, model_id}.
    """
    return await tts_service.get_status()


@router.get("/voices")
async def list_voices():
    """
    List the configured ElevenLabs voices.
    """
    if not tts_service.is_configured():
        return {"voices": [], "provider": "none"}

    return {
        "voices": tts_service.get_voices(),
        "provider": tts_service.get_provider(),
    }
