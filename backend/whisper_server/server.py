"""
Whisper STT FastAPI Server

Provides speech-to-text transcription using faster-whisper.
"""

import io
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

# Configure CUDA DLL paths on Windows before importing torch/faster-whisper
# This is necessary because ctranslate2 (used by faster-whisper) needs cuBLAS
# and the DLLs installed via pip aren't automatically discoverable
if sys.platform == "win32":
    try:
        # Build list of possible site-packages locations
        site_packages_paths = []
        
        # First, check for venv site-packages (most likely location)
        if hasattr(sys, 'prefix'):
            venv_site = Path(sys.prefix) / "Lib" / "site-packages"
            if venv_site.exists():
                site_packages_paths.append(venv_site)
        
        # Also check site.getsitepackages() as fallback
        import site
        for sp in site.getsitepackages():
            p = Path(sp)
            if p.exists() and p not in site_packages_paths:
                site_packages_paths.append(p)
        
        for site_path in site_packages_paths:
            nvidia_cublas_path = site_path / "nvidia" / "cublas" / "bin"
            nvidia_cudnn_path = site_path / "nvidia" / "cudnn" / "bin"
            
            if nvidia_cublas_path.exists():
                os.add_dll_directory(str(nvidia_cublas_path))
                print(f"[CUDA] Added cuBLAS DLL directory: {nvidia_cublas_path}")
            
            if nvidia_cudnn_path.exists():
                os.add_dll_directory(str(nvidia_cudnn_path))
                print(f"[CUDA] Added cuDNN DLL directory: {nvidia_cudnn_path}")
    except Exception as e:
        print(f"[CUDA] Warning: Failed to configure DLL paths: {e}")

import torch
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Whisper STT Server",
    description="Local speech-to-text server using faster-whisper",
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
_whisper_model = None
_model_name = "large-v3"  # Can be: tiny, base, small, medium, large-v2, large-v3, distil-large-v3


class TranscriptionResult(BaseModel):
    """Response model for transcription results."""
    text: str
    language: str
    language_probability: float
    duration: float
    processing_time: float
    segments: Optional[list] = None  # Optional detailed segments


class TranscriptionOptions(BaseModel):
    """Options for transcription."""
    language: Optional[str] = None  # Auto-detect if not specified
    task: str = "transcribe"  # "transcribe" or "translate" (to English)
    beam_size: int = 5
    vad_filter: bool = True  # Filter out non-speech segments
    word_timestamps: bool = False  # Include word-level timestamps
    initial_prompt: Optional[str] = None  # Context hint for better accuracy


def get_model():
    """Get or initialize the Whisper model."""
    global _whisper_model
    
    if _whisper_model is None:
        logger.info(f"Loading Whisper model '{_model_name}' (this may take a while on first run)...")
        
        try:
            from faster_whisper import WhisperModel
            
            # Check for GPU availability
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            
            logger.info(f"Using device: {device}, compute_type: {compute_type}")
            
            # Load the model
            _whisper_model = WhisperModel(
                _model_name,
                device=device,
                compute_type=compute_type,
            )
            
            logger.info("Whisper model loaded successfully")
            
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise RuntimeError(f"Failed to load Whisper model: {e}")
    
    return _whisper_model


@app.on_event("startup")
async def startup_event():
    """Pre-load model on startup for faster first request."""
    logger.info("Pre-loading Whisper model...")
    try:
        get_model()
        logger.info("Model pre-loaded successfully")
    except Exception as e:
        logger.error(f"Failed to pre-load model: {e}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    model_loaded = _whisper_model is not None
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    return {
        "status": "healthy" if model_loaded else "initializing",
        "model": _model_name,
        "model_loaded": model_loaded,
        "device": device,
        "cuda_available": torch.cuda.is_available(),
    }


@app.post("/transcribe", response_model=TranscriptionResult)
async def transcribe_audio(
    audio_file: UploadFile = File(..., description="Audio file to transcribe"),
    language: Optional[str] = Form(None, description="Language code (e.g., 'en'). Auto-detect if not specified."),
    task: str = Form("transcribe", description="'transcribe' or 'translate' (to English)"),
    beam_size: int = Form(5, description="Beam size for decoding"),
    vad_filter: bool = Form(True, description="Filter out non-speech segments"),
    word_timestamps: bool = Form(False, description="Include word-level timestamps"),
    initial_prompt: Optional[str] = Form(None, description="Context hint for better accuracy"),
):
    """
    Transcribe an audio file to text.
    
    Accepts various audio formats (WAV, MP3, M4A, WEBM, etc.).
    Returns the transcribed text along with language detection info.
    """
    start_time = time.time()
    
    # Read the audio file into memory
    try:
        audio_content = await audio_file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read audio file: {e}")
    
    if len(audio_content) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file")
    
    # Save to temporary file (faster-whisper needs a file path)
    try:
        # Determine file extension from content type or filename
        ext = ".wav"
        if audio_file.content_type:
            content_type_map = {
                "audio/wav": ".wav",
                "audio/x-wav": ".wav",
                "audio/wave": ".wav",
                "audio/mp3": ".mp3",
                "audio/mpeg": ".mp3",
                "audio/webm": ".webm",
                "audio/ogg": ".ogg",
                "audio/flac": ".flac",
                "audio/m4a": ".m4a",
                "audio/mp4": ".m4a",
            }
            ext = content_type_map.get(audio_file.content_type, ext)
        elif audio_file.filename:
            ext = Path(audio_file.filename).suffix or ext
        
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as temp_file:
            temp_file.write(audio_content)
            temp_path = temp_file.name
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save audio file: {e}")
    
    try:
        model = get_model()
        
        # Build transcription parameters
        transcribe_kwargs = {
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "word_timestamps": word_timestamps,
        }
        
        if language:
            transcribe_kwargs["language"] = language
        
        if initial_prompt:
            transcribe_kwargs["initial_prompt"] = initial_prompt
        
        if task == "translate":
            transcribe_kwargs["task"] = "translate"
        
        # Perform transcription
        segments, info = model.transcribe(temp_path, **transcribe_kwargs)
        
        # Collect all segments
        all_segments = []
        full_text_parts = []
        
        for segment in segments:
            segment_data = {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
            }
            
            if word_timestamps and hasattr(segment, 'words') and segment.words:
                segment_data["words"] = [
                    {"start": w.start, "end": w.end, "word": w.word}
                    for w in segment.words
                ]
            
            all_segments.append(segment_data)
            full_text_parts.append(segment.text.strip())
        
        full_text = " ".join(full_text_parts)
        
        processing_time = time.time() - start_time
        
        # Calculate audio duration from the last segment
        duration = all_segments[-1]["end"] if all_segments else 0.0
        
        logger.info(
            f"Transcription complete: {len(full_text)} chars, "
            f"language={info.language} ({info.language_probability:.2%}), "
            f"duration={duration:.1f}s, processing={processing_time:.2f}s"
        )
        
        return TranscriptionResult(
            text=full_text,
            language=info.language,
            language_probability=info.language_probability,
            duration=duration,
            processing_time=processing_time,
            segments=all_segments if word_timestamps else None,
        )
        
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
    
    finally:
        # Clean up temporary file
        try:
            os.unlink(temp_path)
        except Exception:
            pass


@app.get("/models")
async def list_models():
    """List available Whisper models and their characteristics."""
    return {
        "current_model": _model_name,
        "available_models": [
            {"name": "tiny", "size": "~75MB", "speed": "fastest", "quality": "lowest"},
            {"name": "base", "size": "~150MB", "speed": "very fast", "quality": "low"},
            {"name": "small", "size": "~500MB", "speed": "fast", "quality": "medium"},
            {"name": "medium", "size": "~1.5GB", "speed": "medium", "quality": "good"},
            {"name": "large-v2", "size": "~3GB", "speed": "slow", "quality": "excellent"},
            {"name": "large-v3", "size": "~3GB", "speed": "slow", "quality": "best"},
            {"name": "distil-large-v3", "size": "~1.5GB", "speed": "fast", "quality": "very good"},
        ],
    }


def main():
    """Run the server."""
    import argparse
    import uvicorn
    
    parser = argparse.ArgumentParser(description="Whisper STT Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8030, help="Port to bind to")
    parser.add_argument("--model", default="large-v3", help="Whisper model to use")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    
    args = parser.parse_args()
    
    global _model_name
    _model_name = args.model
    
    logger.info(f"Starting Whisper STT Server on {args.host}:{args.port}")
    logger.info(f"Using model: {_model_name}")
    
    uvicorn.run(
        "whisper_server.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
