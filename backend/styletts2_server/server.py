"""
StyleTTS 2 FastAPI Server

Provides text-to-speech synthesis using the StyleTTS 2 model.
"""

import hashlib
import io
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Tuple, Any

import torch
import numpy as np
import nltk
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Download required NLTK data for text tokenization (used by styletts2)
# This runs once on module load and skips if already downloaded
try:
    nltk.data.find('tokenizers/punkt_tab')
except LookupError:
    print("Downloading NLTK punkt_tab tokenizer...")
    nltk.download('punkt_tab', quiet=True)

# Patch torch.load for PyTorch 2.6+ compatibility with styletts2
# The styletts2 package uses torch.load without weights_only=False,
# but PyTorch 2.6+ defaults to weights_only=True for security.
# We trust the HuggingFace models, so we patch to use the old behavior.
_original_torch_load = torch.load


def _patched_torch_load(*args, **kwargs):
    """Wrapper that defaults weights_only=False for backward compatibility."""
    if "weights_only" not in kwargs:
        kwargs["weights_only"] = False
    return _original_torch_load(*args, **kwargs)


torch.load = _patched_torch_load

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="StyleTTS 2 Server",
    description="Local text-to-speech server using StyleTTS 2",
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

# Global model instances
_styletts2_model = None
_device = None

# Speaker embedding cache: maps file hash -> (ref_s, ref_p)
_speaker_embedding_cache: Dict[str, Tuple[Any, Any]] = {}


@dataclass
class SpeakerEmbeddings:
    """Cached speaker style embeddings."""
    ref_s: Any  # torch.Tensor - style embedding
    ref_p: Any  # torch.Tensor - prosody embedding
    file_hash: str


def compute_file_hash(file_path: str) -> str:
    """Compute SHA256 hash of a file for cache key."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()[:16]  # Use first 16 chars


def get_speaker_embeddings(speaker_wav_path: str) -> Tuple[Any, Any]:
    """
    Get speaker style embeddings, using cache if available.

    Args:
        speaker_wav_path: Path to the speaker reference audio file

    Returns:
        Tuple of (ref_s, ref_p) tensors
    """
    global _speaker_embedding_cache

    # Compute file hash for cache key
    file_hash = compute_file_hash(speaker_wav_path)

    # Check cache
    if file_hash in _speaker_embedding_cache:
        logger.info(f"Speaker embedding cache HIT for hash {file_hash}")
        return _speaker_embedding_cache[file_hash]

    logger.info(f"Speaker embedding cache MISS for {speaker_wav_path} (hash: {file_hash}), computing...")

    # Get the model and compute embeddings
    model = get_model()

    # Compute reference embeddings using StyleTTS 2's compute_style method
    ref_s, ref_p = model.compute_style(speaker_wav_path)

    # Cache the result
    _speaker_embedding_cache[file_hash] = (ref_s, ref_p)
    logger.info(f"Cached speaker embeddings for {speaker_wav_path} (hash: {file_hash})")

    return ref_s, ref_p


def preload_speaker_embeddings(speaker_paths: list) -> int:
    """
    Pre-load speaker embeddings for a list of speaker files.

    Args:
        speaker_paths: List of paths to speaker reference audio files

    Returns:
        Number of speakers successfully pre-loaded
    """
    loaded = 0
    for path in speaker_paths:
        if os.path.exists(path):
            try:
                get_speaker_embeddings(path)
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to preload speaker embeddings for {path}: {e}")
        else:
            logger.warning(f"Speaker file not found for preloading: {path}")
    return loaded


def clear_speaker_cache(file_hash: Optional[str] = None) -> int:
    """
    Clear speaker embedding cache.

    Args:
        file_hash: Optional specific hash to clear. If None, clears all.

    Returns:
        Number of entries cleared
    """
    global _speaker_embedding_cache

    if file_hash:
        if file_hash in _speaker_embedding_cache:
            del _speaker_embedding_cache[file_hash]
            return 1
        return 0
    else:
        count = len(_speaker_embedding_cache)
        _speaker_embedding_cache = {}
        return count


def get_model():
    """Get or initialize the StyleTTS 2 model."""
    global _styletts2_model, _device

    if _styletts2_model is None:
        logger.info("Loading StyleTTS 2 model...")

        try:
            from styletts2 import tts as styletts2_tts

            # Check for GPU availability
            _device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Using device: {_device}")

            # Initialize StyleTTS 2
            # The model downloads automatically on first use
            _styletts2_model = styletts2_tts.StyleTTS2()

            logger.info("StyleTTS 2 model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load StyleTTS 2 model: {e}")
            raise RuntimeError(f"Failed to load StyleTTS 2 model: {e}")

    return _styletts2_model


def get_preload_speaker_paths() -> list:
    """
    Get list of speaker paths to preload from environment or voices directory.

    Checks STYLETTS2_PRELOAD_SPEAKERS env var (comma-separated paths) and
    STYLETTS2_VOICES_DIR for a voices.json file.
    """
    paths = []

    # Check for explicit preload paths
    preload_env = os.environ.get("STYLETTS2_PRELOAD_SPEAKERS", "")
    if preload_env:
        paths.extend([p.strip() for p in preload_env.split(",") if p.strip()])

    # Check for voices directory with voices.json
    voices_dir = os.environ.get("STYLETTS2_VOICES_DIR", "./styletts2_voices")
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
    default_speaker = os.environ.get("STYLETTS2_DEFAULT_SPEAKER", "")
    if default_speaker and os.path.exists(default_speaker):
        if default_speaker not in paths:
            paths.append(default_speaker)

    return paths


@app.on_event("startup")
async def startup_event():
    """Pre-load the model and optionally preload speaker embeddings on startup."""
    logger.info("StyleTTS 2 Server starting...")

    # Load the model
    try:
        get_model()
    except Exception as e:
        logger.warning(f"Model pre-loading failed: {e}. Will retry on first request.")
        return  # Can't preload speakers without model

    # Preload speaker embeddings for configured voices
    preload_paths = get_preload_speaker_paths()
    if preload_paths:
        logger.info(f"Pre-loading speaker embeddings for {len(preload_paths)} voice(s)...")
        loaded = preload_speaker_embeddings(preload_paths)
        logger.info(f"Pre-loaded {loaded} of {len(preload_paths)} speaker embeddings")
    else:
        logger.info("No speakers configured for preloading")


@app.get("/")
async def root():
    """Root endpoint with server info."""
    return {
        "name": "StyleTTS 2 Server",
        "version": "0.1.0",
        "model": "styletts2",
        "status": "ready" if _styletts2_model is not None else "loading",
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "speaker_cache_size": len(_speaker_embedding_cache),
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "model_loaded": _styletts2_model is not None,
        "speaker_cache_size": len(_speaker_embedding_cache),
    }


@app.get("/cache/stats")
async def cache_stats():
    """Get speaker embedding cache statistics."""
    return {
        "cache_size": len(_speaker_embedding_cache),
        "cached_speakers": list(_speaker_embedding_cache.keys()),
    }


@app.post("/cache/preload")
async def preload_cache(
    speaker_paths: str = Form(..., description="Comma-separated list of speaker file paths"),
):
    """
    Pre-load speaker embeddings for the given speaker files.

    This speeds up subsequent TTS requests for these voices.
    """
    paths = [p.strip() for p in speaker_paths.split(",") if p.strip()]
    if not paths:
        raise HTTPException(status_code=400, detail="No speaker paths provided")

    loaded = preload_speaker_embeddings(paths)
    return {
        "message": f"Pre-loaded {loaded} of {len(paths)} speakers",
        "loaded": loaded,
        "requested": len(paths),
        "cache_size": len(_speaker_embedding_cache),
    }


@app.delete("/cache/clear")
async def clear_cache(
    file_hash: Optional[str] = None,
):
    """
    Clear the speaker embedding cache.

    Args:
        file_hash: Optional specific hash to clear. If not provided, clears all.
    """
    cleared = clear_speaker_cache(file_hash)
    return {
        "message": f"Cleared {cleared} cache entries",
        "cleared": cleared,
        "cache_size": len(_speaker_embedding_cache),
    }


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


# StyleTTS 2 text encoder (ALBERT) has a 512 token limit
# Keep chunks small to stay well under this limit
# 150 chars typically produces ~30-50 tokens, leaving headroom for edge cases
MAX_CHUNK_CHARS = 150


def _normalize_chunk(text: str) -> str:
    """Normalize text for TTS - remove problematic characters and fix whitespace."""
    # Remove brackets - can cause issues
    text = re.sub(r'[\[\]]', '', text)
    # Replace newlines with spaces
    text = text.replace('\n', ' ')
    # Convert ALL CAPS words (2+ chars) to title case to prevent letter-by-letter spelling
    # This includes common words like "OF", "TO", "IN" that would otherwise be spelled out
    def fix_caps(match):
        word = match.group(0)
        if len(word) >= 2:
            return word.capitalize()
        return word
    text = re.sub(r'\b[A-Z]{2,}\b', fix_caps, text)
    # Collapse multiple spaces into one
    text = re.sub(r' +', ' ', text)
    return text.strip()


def split_text_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list:
    """
    Split text into chunks suitable for StyleTTS 2 processing.

    Uses a simple, robust approach:
    1. First split by paragraph breaks (double newlines)
    2. Then split by sentence-ending punctuation
    3. Finally split by any punctuation or word boundaries

    Args:
        text: The text to split
        max_chars: Maximum characters per chunk

    Returns:
        List of text chunks (normalized for TTS)
    """
    # Normalize first to get accurate length measurements
    text = _normalize_chunk(text)

    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    chunks = []

    # Split by sentence-ending punctuation (. ! ?) followed by space
    # This is more permissive than requiring specific letter patterns
    sentences = re.split(r'(?<=[.!?])\s+', text)

    current_chunk = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # If adding this sentence would exceed limit
        if len(current_chunk) + len(sentence) + 1 > max_chars:
            # Save current chunk if not empty
            if current_chunk:
                chunks.append(current_chunk)

            # If single sentence is too long, split it further
            if len(sentence) > max_chars:
                # Split by commas, semicolons, or dashes
                parts = re.split(r'(?<=[,;:\-])\s+', sentence)
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    if len(current_chunk) + len(part) + 1 > max_chars:
                        if current_chunk:
                            chunks.append(current_chunk)
                        # If still too long, split by words
                        if len(part) > max_chars:
                            words = part.split()
                            current_chunk = ""
                            for word in words:
                                if len(current_chunk) + len(word) + 1 > max_chars:
                                    if current_chunk:
                                        chunks.append(current_chunk)
                                    current_chunk = word
                                else:
                                    current_chunk = (current_chunk + " " + word).strip()
                        else:
                            current_chunk = part
                    else:
                        current_chunk = (current_chunk + " " + part).strip()
            else:
                current_chunk = sentence
        else:
            current_chunk = (current_chunk + " " + sentence).strip()

    # Don't forget the last chunk
    if current_chunk:
        chunks.append(current_chunk)

    # Filter out empty chunks
    return [c for c in chunks if c.strip()]


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


def synthesize_with_cached_embeddings(
    text: str,
    speaker_wav_path: str,
    alpha: float = 0.3,
    beta: float = 0.7,
    diffusion_steps: int = 10,
    embedding_scale: float = 1.0,
) -> bytes:
    """
    Synthesize speech using cached speaker embeddings for better performance.

    Automatically chunks long text for optimal quality.

    Args:
        text: Text to synthesize
        speaker_wav_path: Path to speaker reference audio
        alpha: Timbre parameter (0-1), higher = more diverse timbre
        beta: Prosody parameter (0-1), higher = more diverse prosody
        diffusion_steps: Number of diffusion steps (5-20, higher = better quality but slower)
        embedding_scale: Classifier free guidance scale

    Returns:
        WAV audio bytes
    """
    try:
        # Get cached (or compute) speaker embeddings
        logger.debug(f"Getting speaker embeddings for {speaker_wav_path}")
        ref_s, ref_p = get_speaker_embeddings(speaker_wav_path)

        # Get the model
        model = get_model()

        # Split text into chunks
        chunks = split_text_into_chunks(text)
        logger.info(f"Split text into {len(chunks)} chunk(s)")

        audio_arrays = []

        for i, chunk in enumerate(chunks):
            logger.debug(f"Processing chunk {i+1}/{len(chunks)}: {chunk[:50]}...")

            # Synthesize using cached embeddings
            audio_array = model.inference(
                text=chunk,
                ref_s=ref_s,
                ref_p=ref_p,
                alpha=alpha,
                beta=beta,
                diffusion_steps=diffusion_steps,
                embedding_scale=embedding_scale,
            )

            # Convert to numpy array if it's a tensor
            if hasattr(audio_array, "cpu"):
                audio_array = audio_array.cpu().numpy()

            audio_arrays.append(audio_array)

        # Concatenate all audio chunks
        if len(audio_arrays) == 1:
            combined_audio = audio_arrays[0]
        else:
            combined_audio = np.concatenate(audio_arrays)

        # StyleTTS 2 outputs at 24kHz
        sample_rate = 24000

        return numpy_to_wav_bytes(combined_audio, sample_rate)

    except Exception as e:
        logger.error(f"synthesize_with_cached_embeddings failed: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise


def synthesize_default_voice(
    text: str,
    alpha: float = 0.3,
    beta: float = 0.7,
    diffusion_steps: int = 10,
    embedding_scale: float = 1.0,
) -> bytes:
    """
    Synthesize speech using the default LJSpeech voice (no reference needed).

    Args:
        text: Text to synthesize
        alpha: Timbre parameter (0-1), higher = more diverse timbre
        beta: Prosody parameter (0-1), higher = more diverse prosody
        diffusion_steps: Number of diffusion steps (5-20, higher = better quality but slower)
        embedding_scale: Classifier free guidance scale

    Returns:
        WAV audio bytes
    """
    try:
        model = get_model()

        # Split text into chunks
        chunks = split_text_into_chunks(text)
        logger.info(f"Split text into {len(chunks)} chunk(s) for default voice")

        audio_arrays = []

        for i, chunk in enumerate(chunks):
            logger.debug(f"Processing chunk {i+1}/{len(chunks)}: {chunk[:50]}...")

            # Synthesize using default voice (no ref_s/ref_p means use LJSpeech)
            audio_array = model.inference(
                text=chunk,
                alpha=alpha,
                beta=beta,
                diffusion_steps=diffusion_steps,
                embedding_scale=embedding_scale,
            )

            # Convert to numpy array if it's a tensor
            if hasattr(audio_array, "cpu"):
                audio_array = audio_array.cpu().numpy()

            audio_arrays.append(audio_array)

        # Concatenate all audio chunks
        if len(audio_arrays) == 1:
            combined_audio = audio_arrays[0]
        else:
            combined_audio = np.concatenate(audio_arrays)

        # StyleTTS 2 outputs at 24kHz
        sample_rate = 24000

        return numpy_to_wav_bytes(combined_audio, sample_rate)

    except Exception as e:
        logger.error(f"synthesize_default_voice failed: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise


@app.post("/tts_default")
async def tts_default_voice(
    text: str = Form(..., description="Text to synthesize"),
    alpha: str = Form("0.3", description="Timbre parameter (0-1)"),
    beta: str = Form("0.7", description="Prosody parameter (0-1)"),
    diffusion_steps: str = Form("10", description="Diffusion steps (5-20)"),
    embedding_scale: str = Form("1.0", description="Embedding scale"),
):
    """
    Convert text to speech using the default LJSpeech voice.

    No speaker reference audio needed - uses the pre-trained LJSpeech voice.
    """
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    # Parse parameters
    try:
        alpha_val = float(alpha)
        beta_val = float(beta)
        diffusion_steps_val = int(diffusion_steps)
        embedding_scale_val = float(embedding_scale)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {e}")

    # Validate parameters
    if not 0 <= alpha_val <= 1:
        raise HTTPException(status_code=400, detail="Alpha must be between 0 and 1")
    if not 0 <= beta_val <= 1:
        raise HTTPException(status_code=400, detail="Beta must be between 0 and 1")
    if not 1 <= diffusion_steps_val <= 50:
        raise HTTPException(status_code=400, detail="Diffusion steps must be between 1 and 50")

    try:
        logger.info(f"Generating speech with default voice for {len(text)} chars")
        audio_bytes = synthesize_default_voice(
            text=text,
            alpha=alpha_val,
            beta=beta_val,
            diffusion_steps=diffusion_steps_val,
            embedding_scale=embedding_scale_val,
        )

        logger.info(f"Generated {len(audio_bytes)} bytes of audio")

        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": "inline"},
        )

    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")


@app.post("/tts_to_audio")
async def tts_to_audio(
    text: str = Form(..., description="Text to synthesize"),
    speaker_wav: UploadFile = File(..., description="Speaker reference audio"),
    alpha: str = Form("0.3", description="Timbre parameter (0-1)"),
    beta: str = Form("0.7", description="Prosody parameter (0-1)"),
    diffusion_steps: str = Form("10", description="Diffusion steps (5-20)"),
    embedding_scale: str = Form("1.0", description="Embedding scale"),
):
    """
    Convert text to speech using a speaker reference audio.

    This is the main endpoint for voice cloning TTS.
    Speaker embeddings are cached based on audio content hash for performance.
    """
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    # Parse parameters
    try:
        alpha_val = float(alpha)
        beta_val = float(beta)
        diffusion_steps_val = int(diffusion_steps)
        embedding_scale_val = float(embedding_scale)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {e}")

    # Validate parameters
    if not 0 <= alpha_val <= 1:
        raise HTTPException(status_code=400, detail="Alpha must be between 0 and 1")
    if not 0 <= beta_val <= 1:
        raise HTTPException(status_code=400, detail="Beta must be between 0 and 1")
    if not 1 <= diffusion_steps_val <= 50:
        raise HTTPException(status_code=400, detail="Diffusion steps must be between 1 and 50")

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

        # Generate speech using cached speaker embeddings
        logger.info(f"Generating speech for {len(text)} chars, alpha={alpha_val}, beta={beta_val}")
        audio_bytes = synthesize_with_cached_embeddings(
            text=text,
            speaker_wav_path=speaker_path,
            alpha=alpha_val,
            beta=beta_val,
            diffusion_steps=diffusion_steps_val,
            embedding_scale=embedding_scale_val,
        )

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


class TTSRequest(BaseModel):
    """JSON request body for TTS endpoint."""
    text: str
    speaker_wav: str
    alpha: float = 0.3
    beta: float = 0.7
    diffusion_steps: int = 10
    embedding_scale: float = 1.0


async def _tts_with_path(
    text: str,
    speaker_wav: str,
    alpha: float,
    beta: float,
    diffusion_steps: int,
    embedding_scale: float,
) -> Response:
    """
    Internal TTS function using a speaker file path.

    Speaker embeddings are cached for maximum performance with server-side voices.
    """
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    speaker_path = Path(speaker_wav)
    if not speaker_path.exists():
        raise HTTPException(status_code=400, detail=f"Speaker file not found: {speaker_wav}")

    try:
        # Generate speech using cached speaker embeddings
        logger.info(f"Generating speech for {len(text)} chars, alpha={alpha}, beta={beta}")
        audio_bytes = synthesize_with_cached_embeddings(
            text=text,
            speaker_wav_path=str(speaker_path),
            alpha=alpha,
            beta=beta,
            diffusion_steps=diffusion_steps,
            embedding_scale=embedding_scale,
        )

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


@app.post("/tts")
async def tts_json(request: TTSRequest):
    """
    TTS endpoint accepting JSON body with speaker_wav as a file path.

    This endpoint is for when the speaker file is already on the server.
    Speaker embeddings are cached for maximum performance with server-side voices.
    """
    return await _tts_with_path(
        request.text,
        request.speaker_wav,
        request.alpha,
        request.beta,
        request.diffusion_steps,
        request.embedding_scale,
    )


@app.post("/tts_form")
async def tts_form(
    text: str = Form(...),
    speaker_wav: str = Form(..., description="Path to speaker WAV file"),
    alpha: str = Form("0.3"),
    beta: str = Form("0.7"),
    diffusion_steps: str = Form("10"),
    embedding_scale: str = Form("1.0"),
):
    """
    TTS endpoint accepting Form data with speaker_wav as a file path.

    This endpoint is for when the speaker file is already on the server.
    Speaker embeddings are cached for maximum performance with server-side voices.
    """
    try:
        alpha_val = float(alpha)
        beta_val = float(beta)
        diffusion_steps_val = int(diffusion_steps)
        embedding_scale_val = float(embedding_scale)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {e}")

    return await _tts_with_path(
        text,
        speaker_wav,
        alpha_val,
        beta_val,
        diffusion_steps_val,
        embedding_scale_val,
    )


@app.post("/tts_stream")
async def tts_stream(
    text: str = Form(...),
    speaker_wav: UploadFile = File(...),
    alpha: str = Form("0.3"),
    beta: str = Form("0.7"),
    diffusion_steps: str = Form("10"),
    embedding_scale: str = Form("1.0"),
):
    """
    Stream TTS audio (currently just returns full audio, streaming TBD).

    Note: True streaming requires chunked synthesis which StyleTTS 2 supports
    but requires more complex implementation.
    """
    # For now, just return the full audio
    # True streaming would require chunked synthesis
    return await tts_to_audio(
        text=text,
        speaker_wav=speaker_wav,
        alpha=alpha,
        beta=beta,
        diffusion_steps=diffusion_steps,
        embedding_scale=embedding_scale,
    )


def create_app() -> FastAPI:
    """Factory function to create the app (useful for testing)."""
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8021)
