"""
Speech-to-Text API routes.

Supports local Whisper STT for transcribing audio to text.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional

from app.services.whisper_service import whisper_service

router = APIRouter(prefix="/api/stt", tags=["stt"])


class TranscriptionResponse(BaseModel):
    """Response model for transcription results."""
    text: str
    language: str
    language_probability: float
    duration: float
    processing_time: float


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    audio_file: UploadFile = File(..., description="Audio file to transcribe"),
    language: Optional[str] = Form(None, description="Language code (auto-detect if not specified)"),
    initial_prompt: Optional[str] = Form(None, description="Context hint for better accuracy"),
):
    """
    Transcribe an audio file to text using Whisper.

    Accepts various audio formats (WAV, WebM, MP3, etc.).
    Returns the transcribed text along with language detection info.
    """
    if not whisper_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Speech-to-text service is not configured. Set WHISPER_ENABLED=true and start the Whisper server."
        )

    # Read the audio file
    try:
        audio_data = await audio_file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read audio file: {e}")

    if len(audio_data) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Limit file size (50MB max)
    max_size = 50 * 1024 * 1024
    if len(audio_data) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"Audio file too large. Maximum size is {max_size // (1024*1024)}MB"
        )

    try:
        result = await whisper_service.transcribe(
            audio_data=audio_data,
            filename=audio_file.filename or "audio.wav",
            content_type=audio_file.content_type or "audio/wav",
            language=language,
            initial_prompt=initial_prompt,
        )

        return TranscriptionResponse(
            text=result["text"],
            language=result["language"],
            language_probability=result["language_probability"],
            duration=result["duration"],
            processing_time=result["processing_time"],
        )

    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


@router.get("/status")
async def stt_status():
    """
    Check if STT service is configured and available.

    Returns provider information and health status.
    """
    return await whisper_service.get_status()
