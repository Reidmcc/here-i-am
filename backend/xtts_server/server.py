"""
XTTS v2 FastAPI Server

Provides text-to-speech synthesis using the Coqui XTTS v2 model.
"""

import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Tuple, Any

import torch
import numpy as np
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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

# Speaker latent cache: maps file hash -> (gpt_cond_latent, speaker_embedding)
_speaker_latent_cache: Dict[str, Tuple[Any, Any]] = {}


@dataclass
class SpeakerLatents:
    """Cached speaker conditioning latents."""
    gpt_cond_latent: Any  # torch.Tensor
    speaker_embedding: Any  # torch.Tensor
    file_hash: str


def compute_file_hash(file_path: str) -> str:
    """Compute SHA256 hash of a file for cache key."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()[:16]  # Use first 16 chars


def get_speaker_latents(speaker_wav_path: str) -> Tuple[Any, Any]:
    """
    Get speaker conditioning latents, using cache if available.

    Args:
        speaker_wav_path: Path to the speaker reference audio file

    Returns:
        Tuple of (gpt_cond_latent, speaker_embedding) tensors
    """
    global _speaker_latent_cache

    # Compute file hash for cache key
    file_hash = compute_file_hash(speaker_wav_path)

    # Check cache
    if file_hash in _speaker_latent_cache:
        logger.info(f"Speaker latent cache HIT for hash {file_hash}")
        return _speaker_latent_cache[file_hash]

    logger.info(f"Speaker latent cache MISS for {speaker_wav_path} (hash: {file_hash}), computing...")

    # Get the model and compute latents
    tts = get_model()

    # Access the underlying XTTS model to compute conditioning latents
    # The TTS wrapper provides access via synthesizer.tts_model
    xtts_model = tts.synthesizer.tts_model

    gpt_cond_latent, speaker_embedding = xtts_model.get_conditioning_latents(
        audio_path=speaker_wav_path
    )

    # Cache the result
    _speaker_latent_cache[file_hash] = (gpt_cond_latent, speaker_embedding)
    logger.info(f"Cached speaker latents for {speaker_wav_path} (hash: {file_hash})")

    return gpt_cond_latent, speaker_embedding


def preload_speaker_latents(speaker_paths: list) -> int:
    """
    Pre-load speaker latents for a list of speaker files.

    Args:
        speaker_paths: List of paths to speaker reference audio files

    Returns:
        Number of speakers successfully pre-loaded
    """
    loaded = 0
    for path in speaker_paths:
        if os.path.exists(path):
            try:
                get_speaker_latents(path)
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to preload speaker latents for {path}: {e}")
        else:
            logger.warning(f"Speaker file not found for preloading: {path}")
    return loaded


def clear_speaker_cache(file_hash: Optional[str] = None) -> int:
    """
    Clear speaker latent cache.

    Args:
        file_hash: Optional specific hash to clear. If None, clears all.

    Returns:
        Number of entries cleared
    """
    global _speaker_latent_cache

    if file_hash:
        if file_hash in _speaker_latent_cache:
            del _speaker_latent_cache[file_hash]
            return 1
        return 0
    else:
        count = len(_speaker_latent_cache)
        _speaker_latent_cache = {}
        return count


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

            # Override the tokenizer's character limits to match our chunking
            # The default 250 char limit is overly conservative; XTTS can handle 400 tokens
            try:
                tokenizer = _tts_model.synthesizer.tts_model.tokenizer
                if hasattr(tokenizer, 'char_limits'):
                    for lang in tokenizer.char_limits:
                        tokenizer.char_limits[lang] = MAX_CHUNK_CHARS
                    logger.info(f"Updated tokenizer char_limits to {MAX_CHUNK_CHARS}")
            except Exception as e:
                logger.warning(f"Could not update tokenizer char_limits: {e}")

            # Note: We intentionally do NOT use torch.compile here.
            # The "reduce-overhead" mode uses CUDA graphs which cache GPU execution states
            # for each unique input shape. With variable-length text chunks, this causes
            # VRAM to accumulate rapidly as each chunk length creates a new cached graph.
            # For XTTS inference with variable text lengths, the standard eager mode is safer.

            logger.info("XTTS v2 model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load XTTS model: {e}")
            raise RuntimeError(f"Failed to load XTTS model: {e}")

    return _tts_model


def get_preload_speaker_paths() -> list:
    """
    Get list of speaker paths to preload from environment or voices directory.

    Checks XTTS_PRELOAD_SPEAKERS env var (comma-separated paths) and
    XTTS_VOICES_DIR for a voices.json file.
    """
    paths = []

    # Check for explicit preload paths
    preload_env = os.environ.get("XTTS_PRELOAD_SPEAKERS", "")
    if preload_env:
        paths.extend([p.strip() for p in preload_env.split(",") if p.strip()])

    # Check for voices directory with voices.json
    voices_dir = os.environ.get("XTTS_VOICES_DIR", "./xtts_voices")
    voices_file = Path(voices_dir) / "voices.json"
    if voices_file.exists():
        try:
            with open(voices_file, "r") as f:
                voices_data = json.load(f)
                for voice in voices_data:
                    sample_path = voice.get("sample_path", "")
                    if sample_path and os.path.exists(sample_path):
                        paths.append(sample_path)
        except Exception as e:
            logger.warning(f"Failed to load voices.json for preloading: {e}")

    # Check for default speaker
    default_speaker = os.environ.get("XTTS_DEFAULT_SPEAKER", "")
    if default_speaker and os.path.exists(default_speaker):
        if default_speaker not in paths:
            paths.append(default_speaker)

    return paths


@app.on_event("startup")
async def startup_event():
    """Pre-load the model and optionally preload speaker latents on startup."""
    logger.info("XTTS Server starting...")

    # Load the model
    try:
        get_model()
    except Exception as e:
        logger.warning(f"Model pre-loading failed: {e}. Will retry on first request.")
        return  # Can't preload speakers without model

    # Preload speaker latents for configured voices
    preload_paths = get_preload_speaker_paths()
    if preload_paths:
        logger.info(f"Pre-loading speaker latents for {len(preload_paths)} voice(s)...")
        loaded = preload_speaker_latents(preload_paths)
        logger.info(f"Pre-loaded {loaded} of {len(preload_paths)} speaker latents")
    else:
        logger.info("No speakers configured for preloading")


@app.get("/")
async def root():
    """Root endpoint with server info."""
    return {
        "name": "XTTS v2 Server",
        "version": "0.1.0",
        "model": _model_name,
        "status": "ready" if _tts_model is not None else "loading",
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "speaker_cache_size": len(_speaker_latent_cache),
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "model_loaded": _tts_model is not None,
        "speaker_cache_size": len(_speaker_latent_cache),
    }


@app.get("/cache/stats")
async def cache_stats():
    """Get speaker latent cache statistics."""
    return {
        "cache_size": len(_speaker_latent_cache),
        "cached_speakers": list(_speaker_latent_cache.keys()),
    }


@app.post("/cache/preload")
async def preload_cache(
    speaker_paths: str = Form(..., description="Comma-separated list of speaker file paths"),
):
    """
    Pre-load speaker latents for the given speaker files.

    This speeds up subsequent TTS requests for these voices.
    """
    paths = [p.strip() for p in speaker_paths.split(",") if p.strip()]
    if not paths:
        raise HTTPException(status_code=400, detail="No speaker paths provided")

    loaded = preload_speaker_latents(paths)
    return {
        "message": f"Pre-loaded {loaded} of {len(paths)} speakers",
        "loaded": loaded,
        "requested": len(paths),
        "cache_size": len(_speaker_latent_cache),
    }


@app.delete("/cache/clear")
async def clear_cache(
    file_hash: Optional[str] = None,
):
    """
    Clear the speaker latent cache.

    Args:
        file_hash: Optional specific hash to clear. If not provided, clears all.
    """
    cleared = clear_speaker_cache(file_hash)
    return {
        "message": f"Cleared {cleared} cache entries",
        "cleared": cleared,
        "cache_size": len(_speaker_latent_cache),
    }


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


# XTTS has a 400 token limit (gpt_max_text_tokens: 402).
# English averages ~4 chars/token, so 400 tokens ≈ 1600 chars.
# We use 400 chars to avoid XTTS audio truncation at chunk ends -
# larger chunks (500-600) cause content loss at the end of chunks.
MAX_CHUNK_CHARS = 400


def _normalize_chunk(text: str) -> str:
    """Normalize text for TTS - remove problematic characters and fix whitespace."""
    # Remove brackets - XTTS doesn't handle them well
    text = re.sub(r'[\[\]]', '', text)
    # Replace newlines with spaces
    text = text.replace('\n', ' ')
    # Collapse multiple spaces into one
    text = re.sub(r' +', ' ', text)
    return text.strip()


def split_text_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list:
    """
    Split text into chunks suitable for XTTS processing.

    Splits at sentence boundaries first, then paragraph breaks, clause boundaries
    (commas, semicolons), and finally word boundaries as a last resort.
    All chunks are normalized (newlines replaced with spaces) for TTS compatibility.

    Args:
        text: The text to split
        max_chars: Maximum characters per chunk

    Returns:
        List of text chunks (normalized for TTS)
    """
    if len(text) <= max_chars:
        return [_normalize_chunk(text)]

    chunks = []

    # Split into sentences at punctuation followed by space and uppercase letter.
    # Require lowercase before punctuation to avoid splitting inside ALL CAPS
    # labels like [MEMORIES FROM PREVIOUS CONVERSATIONS. THESE ARE NOT...]
    sentence_pattern = r'(?<=[a-z][.!?])\s+(?=[A-Z])'
    sentences = re.split(sentence_pattern, text)
    sentences = [s for s in sentences if s.strip()]

    current_chunk = ""
    for sentence in sentences:
        # If adding this sentence would exceed limit
        if len(current_chunk) + len(sentence) + 1 > max_chars:
            # Save current chunk if not empty
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
                current_chunk = ""

            # If single sentence is too long, try splitting further
            if len(sentence) > max_chars:
                # First try paragraph breaks (double newlines)
                paragraphs = re.split(r'\n\n+', sentence)
                if len(paragraphs) > 1:
                    for para in paragraphs:
                        para = para.strip()
                        if not para:
                            continue
                        if len(current_chunk) + len(para) + 1 > max_chars:
                            if current_chunk.strip():
                                chunks.append(current_chunk.strip())
                                current_chunk = ""
                            if len(para) > max_chars:
                                # Paragraph still too long - try clause splits
                                _split_by_clauses(para, max_chars, chunks, current_chunk)
                                current_chunk = ""
                            else:
                                current_chunk = para + " "
                        else:
                            current_chunk += para + " "
                else:
                    # No paragraph breaks - try clause splits
                    current_chunk = _split_by_clauses(sentence, max_chars, chunks, current_chunk)
            else:
                current_chunk = sentence + " "
        else:
            current_chunk += sentence + " "

    # Don't forget the last chunk
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # Normalize all chunks for TTS compatibility
    return [_normalize_chunk(chunk) for chunk in chunks]


def _split_by_clauses(text: str, max_chars: int, chunks: list, current_chunk: str) -> str:
    """Helper to split text by clause boundaries (comma/semicolon) then words."""
    clauses = re.split(r'(?<=[,;])\s+', text)
    for clause in clauses:
        if len(current_chunk) + len(clause) + 1 > max_chars:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
                current_chunk = ""
            # If single clause is still too long, split by words
            if len(clause) > max_chars:
                words = clause.split()
                for word in words:
                    if len(current_chunk) + len(word) + 1 > max_chars:
                        if current_chunk.strip():
                            chunks.append(current_chunk.strip())
                        current_chunk = word + " "
                    else:
                        current_chunk += word + " "
            else:
                current_chunk = clause + " "
        else:
            current_chunk += clause + " "
    return current_chunk


def synthesize_with_cached_latents(
    text: str,
    speaker_wav_path: str,
    language: str = "en",
) -> bytes:
    """
    Synthesize speech using cached speaker latents for better performance.

    Automatically chunks long text to stay within XTTS's 400 token limit.

    Args:
        text: Text to synthesize
        speaker_wav_path: Path to speaker reference audio
        language: Language code

    Returns:
        WAV audio bytes
    """
    try:
        # Get cached (or compute) speaker latents
        logger.debug(f"Getting speaker latents for {speaker_wav_path}")
        gpt_cond_latent, speaker_embedding = get_speaker_latents(speaker_wav_path)

        # Get the model
        tts = get_model()
        xtts_model = tts.synthesizer.tts_model

        # Split text into chunks to avoid XTTS 400 token limit
        chunks = split_text_into_chunks(text)
        logger.info(f"Split text into {len(chunks)} chunk(s)")

        audio_arrays = []

        # Use no_grad to ensure no gradient computation and reduce memory
        with torch.no_grad():
            for i, chunk in enumerate(chunks):
                logger.debug(f"Processing chunk {i+1}/{len(chunks)}: {chunk[:50]}...")

                # Synthesize using cached latents
                audio_output = xtts_model.inference(
                    text=chunk,
                    language=language,
                    gpt_cond_latent=gpt_cond_latent,
                    speaker_embedding=speaker_embedding,
                )

                # Get the audio waveform from output dict
                audio_array = audio_output.get("wav")
                if audio_array is None:
                    logger.error(f"XTTS model returned: {audio_output.keys() if isinstance(audio_output, dict) else type(audio_output)}")
                    raise RuntimeError("XTTS model did not return audio")

                # Convert to numpy array if it's a tensor - move to CPU immediately
                if hasattr(audio_array, "cpu"):
                    audio_array = audio_array.cpu().numpy()

                audio_arrays.append(audio_array)

                # Explicitly clear GPU memory after each chunk to prevent VRAM accumulation
                # This is critical for long texts with many chunks
                del audio_output
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

        # Concatenate all audio chunks
        if len(audio_arrays) == 1:
            combined_audio = audio_arrays[0]
        else:
            combined_audio = np.concatenate(audio_arrays)

        # XTTS outputs at 24kHz
        sample_rate = 24000

        return numpy_to_wav_bytes(combined_audio, sample_rate)

    except Exception as e:
        logger.error(f"synthesize_with_cached_latents failed: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise


@app.post("/tts_to_audio")
async def tts_to_audio(
    text: str = Form(..., description="Text to synthesize"),
    language: str = Form("en", description="Language code"),
    speaker_wav: UploadFile = File(..., description="Speaker reference audio"),
):
    """
    Convert text to speech using a speaker reference audio.

    This is the main endpoint for voice cloning TTS.
    Speaker latents are cached based on audio content hash for performance.
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

        # Generate speech using cached speaker latents
        logger.info(f"Generating speech for {len(text)} chars, language={language}")
        start_time = time.time()
        audio_bytes = synthesize_with_cached_latents(
            text=text,
            speaker_wav_path=speaker_path,
            language=language,
        )
        elapsed = time.time() - start_time

        logger.info(f"Generated {len(audio_bytes)} bytes of audio in {elapsed:.2f} seconds")

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


class TTSRequest(BaseModel):
    """JSON request body for TTS endpoint."""
    text: str
    speaker_wav: str
    language: str = "en"


async def _tts_with_path(text: str, speaker_wav: str, language: str) -> Response:
    """
    Internal TTS function using a speaker file path.

    Speaker latents are cached for maximum performance with server-side voices.
    """
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    speaker_path = Path(speaker_wav)
    if not speaker_path.exists():
        raise HTTPException(status_code=400, detail=f"Speaker file not found: {speaker_wav}")

    try:
        # Generate speech using cached speaker latents
        logger.info(f"Generating speech for {len(text)} chars, language={language}")
        start_time = time.time()
        audio_bytes = synthesize_with_cached_latents(
            text=text,
            speaker_wav_path=str(speaker_path),
            language=language,
        )
        elapsed = time.time() - start_time

        logger.info(f"Generated {len(audio_bytes)} bytes of audio in {elapsed:.2f} seconds")

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


@app.post("/tts")
async def tts_json(request: TTSRequest):
    """
    TTS endpoint accepting JSON body with speaker_wav as a file path.

    This endpoint is for when the speaker file is already on the server.
    Speaker latents are cached for maximum performance with server-side voices.
    """
    return await _tts_with_path(request.text, request.speaker_wav, request.language)


@app.post("/tts_form")
async def tts_form(
    text: str = Form(...),
    speaker_wav: str = Form(..., description="Path to speaker WAV file"),
    language: str = Form("en"),
):
    """
    TTS endpoint accepting Form data with speaker_wav as a file path.

    This endpoint is for when the speaker file is already on the server.
    Speaker latents are cached for maximum performance with server-side voices.
    """
    return await _tts_with_path(text, speaker_wav, language)


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
