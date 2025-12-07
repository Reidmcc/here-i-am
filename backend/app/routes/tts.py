"""
Text-to-Speech API routes.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from app.services import tts_service

router = APIRouter(prefix="/api/tts", tags=["tts"])


class TTSRequest(BaseModel):
    """Request body for text-to-speech conversion."""
    text: str
    voice_id: Optional[str] = None
    model_id: Optional[str] = None


@router.post("/speak")
async def text_to_speech(data: TTSRequest):
    """
    Convert text to speech and return audio as MP3.

    Returns audio/mpeg content directly for playback.
    """
    if not tts_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Text-to-speech service is not configured. Please set ELEVENLABS_API_KEY."
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

        return StreamingResponse(
            iter([audio_bytes]),
            media_type="audio/mpeg",
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
    Convert text to speech and stream audio as MP3.

    Streams audio chunks for faster playback start.
    """
    if not tts_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Text-to-speech service is not configured. Please set ELEVENLABS_API_KEY."
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

    return StreamingResponse(
        generate(),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": "inline",
        }
    )


@router.get("/status")
async def tts_status():
    """Check if TTS service is configured and available."""
    return {
        "configured": tts_service.is_configured(),
        "voice_id": tts_service.voice_id if tts_service.is_configured() else None,
        "model_id": tts_service.model_id if tts_service.is_configured() else None,
    }
