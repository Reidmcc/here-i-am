"""
XTTS v2 FastAPI Server

Provides text-to-speech synthesis using the Coqui XTTS v2 model.
"""

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import torch
import numpy as np
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="XTTS v2 Server",
    description="Local text-to-speech server using Coqui XTTS v2",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global model instance
_tts_model = None
_model_name = "tts_models/multilingual/multi-dataset/xtts_v2"


def get_model():
    """Get or initialize the TTS model."""
    global _tts_model

    if _tts_model is None:
        logger.info("Loading XTTS v2 model (this may take a while on first run)...")

        try:
            from TTS.api import TTS

            # Check for GPU availability
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Using device: {device}")

            # Load the model
            _tts_model = TTS(_model_name).to(device)

            # Apply FP16 precision and reduce-overhead optimizations for GPU
            if device == "cuda":
                logger.info("Applying FP16 precision...")
                _tts_model.synthesizer.tts_model.half()

                logger.info("Applying torch.compile with reduce-overhead mode...")
                _tts_model.synthesizer.tts_model = torch.compile(
                    _tts_model.synthesizer.tts_model,
                    mode="reduce-overhead"
                )
                logger.info("Model optimizations applied successfully")

            logger.info("XTTS v2 model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load XTTS model: {e}")
            raise RuntimeError(f"Failed to load XTTS model: {e}")

    return _tts_model


@app.on_event("startup")
async def startup_event():
    """Pre-load the model on startup."""
    logger.info("XTTS Server starting...")
    try:
        get_model()
    except Exception as e:
        logger.warning(f"Model pre-loading failed: {e}. Will retry on first request.")


@app.get("/")
async def root():
    """Root endpoint with server info."""
    return {
        "name": "XTTS v2 Server",
        "version": "0.1.0",
        "model": _model_name,
        "status": "ready" if _tts_model is not None else "loading",
        "device": "cuda" if torch.cuda.is_available() else "cpu",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "model_loaded": _tts_model is not None}


@app.get("/languages")
async def get_languages():
    """Get supported languages."""
    # XTTS v2 supported languages
    languages = [
        "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru",
        "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko", "hi"
    ]
    return {"languages": languages}


def save_temp_audio(audio_data: bytes, suffix: str = ".wav") -> str:
    """Save audio data to a temporary file and return the path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(fd, audio_data)
    finally:
        os.close(fd)
    return path


def numpy_to_wav_bytes(audio_array: np.ndarray, sample_rate: int = 24000) -> bytes:
    """Convert numpy audio array to WAV bytes."""
    import scipy.io.wavfile as wavfile

    # Ensure audio is in the right format
    if audio_array.dtype != np.int16:
        # Normalize and convert to int16
        audio_array = np.clip(audio_array, -1.0, 1.0)
        audio_array = (audio_array * 32767).astype(np.int16)

    # Write to bytes buffer
    buffer = io.BytesIO()
    wavfile.write(buffer, sample_rate, audio_array)
    buffer.seek(0)
    return buffer.read()


@app.post("/tts_to_audio")
async def tts_to_audio(
    text: str = Form(..., description="Text to synthesize"),
    language: str = Form("en", description="Language code"),
    speaker_wav: UploadFile = File(..., description="Speaker reference audio"),
):
    """
    Convert text to speech using a speaker reference audio.

    This is the main endpoint for voice cloning TTS.
    """
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    # Validate language
    valid_languages = [
        "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru",
        "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko", "hi"
    ]
    if language not in valid_languages:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid language. Supported: {', '.join(valid_languages)}"
        )

    # Save speaker audio to temp file
    speaker_audio_data = await speaker_wav.read()
    if len(speaker_audio_data) < 1000:
        raise HTTPException(status_code=400, detail="Speaker audio too short")

    speaker_path = None
    try:
        # Determine file extension
        filename = speaker_wav.filename or "speaker.wav"
        suffix = Path(filename).suffix or ".wav"
        speaker_path = save_temp_audio(speaker_audio_data, suffix)

        # Get the model
        tts = get_model()

        # Generate speech
        logger.info(f"Generating speech for {len(text)} chars, language={language}")

        # Use tts_to_file to generate audio
        output_path = tempfile.mktemp(suffix=".wav")
        try:
            tts.tts_to_file(
                text=text,
                file_path=output_path,
                speaker_wav=speaker_path,
                language=language,
            )

            # Read the generated audio
            with open(output_path, "rb") as f:
                audio_bytes = f.read()

        finally:
            # Clean up output file
            if os.path.exists(output_path):
                os.unlink(output_path)

        logger.info(f"Generated {len(audio_bytes)} bytes of audio")

        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": "inline"},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")

    finally:
        # Clean up temp speaker file
        if speaker_path and os.path.exists(speaker_path):
            try:
                os.unlink(speaker_path)
            except Exception:
                pass


@app.post("/tts")
async def tts_json(
    text: str = Form(...),
    speaker_wav: str = Form(..., description="Path to speaker WAV file"),
    language: str = Form("en"),
):
    """
    Alternative TTS endpoint accepting speaker_wav as a file path.

    This endpoint is for when the speaker file is already on the server.
    """
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    speaker_path = Path(speaker_wav)
    if not speaker_path.exists():
        raise HTTPException(status_code=400, detail=f"Speaker file not found: {speaker_wav}")

    try:
        tts = get_model()

        output_path = tempfile.mktemp(suffix=".wav")
        try:
            tts.tts_to_file(
                text=text,
                file_path=output_path,
                speaker_wav=str(speaker_path),
                language=language,
            )

            with open(output_path, "rb") as f:
                audio_bytes = f.read()

        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": "inline"},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")


@app.post("/tts_stream")
async def tts_stream(
    text: str = Form(...),
    language: str = Form("en"),
    speaker_wav: UploadFile = File(...),
):
    """
    Stream TTS audio (currently just returns full audio, streaming TBD).

    Note: True streaming requires chunked synthesis which XTTS supports
    but requires more complex implementation.
    """
    # For now, just return the full audio
    # True streaming would require chunked synthesis
    return await tts_to_audio(text=text, language=language, speaker_wav=speaker_wav)


def create_app() -> FastAPI:
    """Factory function to create the app (useful for testing)."""
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8020)
