"""
StyleTTS 2 FastAPI Server

Provides text-to-speech synthesis using the StyleTTS 2 model.
Supports both gruut (MIT licensed) and espeak-ng phonemizers.
"""

import hashlib
import io
import json
import logging
import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Tuple, Any, List

import torch
import numpy as np
import nltk
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# =============================================================================
# Phonemizer Abstraction
# =============================================================================

class Phonemizer(ABC):
    """Abstract base class for phonemizers."""

    @abstractmethod
    def phonemize(self, text: str) -> str:
        """Convert text to phonemes."""
        pass


class GruutPhonemizer(Phonemizer):
    """
    Gruut-based phonemizer (MIT licensed, no system dependencies).

    Uses the gruut library which is pure Python and doesn't require
    espeak-ng to be installed on the system.
    """

    def __init__(self, language: str = "en-us"):
        self.language = language
        try:
            import gruut
            self.gruut = gruut
        except ImportError:
            raise ImportError(
                "gruut is required for the gruut phonemizer. "
                "Install it with: pip install gruut"
            )

    def phonemize(self, text: str) -> str:
        """Convert text to IPA phonemes using gruut."""
        phonemes = []
        for sentence in self.gruut.sentences(text, lang=self.language):
            for word in sentence:
                if word.phonemes:
                    phonemes.append("".join(word.phonemes))
        return " ".join(phonemes)


class EspeakPhonemizer(Phonemizer):
    """
    Espeak-ng based phonemizer (requires espeak-ng system package).

    Uses the phonemizer library with espeak-ng backend.
    This provides higher quality phonemization but requires
    espeak-ng to be installed on the system.
    """

    def __init__(self, language: str = "en-us"):
        self.language = language
        try:
            import phonemizer
            from phonemizer.backend import EspeakBackend

            # Initialize the espeak backend
            self.backend = EspeakBackend(
                language=language,
                preserve_punctuation=True,
                with_stress=True,
                words_mismatch='ignore'
            )
            self.phonemizer = phonemizer
        except ImportError:
            raise ImportError(
                "phonemizer is required for the espeak phonemizer. "
                "Install it with: pip install phonemizer"
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize espeak backend: {e}. "
                "Make sure espeak-ng is installed on your system."
            )

    def phonemize(self, text: str) -> str:
        """Convert text to IPA phonemes using espeak-ng."""
        # Use the phonemizer library
        result = self.phonemizer.phonemize(
            text,
            language=self.language,
            backend='espeak',
            preserve_punctuation=True,
            with_stress=True,
            strip=True
        )
        return result


def get_phonemizer(backend: str = "gruut", language: str = "en-us") -> Phonemizer:
    """
    Factory function to create a phonemizer instance.

    Args:
        backend: "gruut" or "espeak"
        language: Language code (default: "en-us")

    Returns:
        Phonemizer instance
    """
    backend = backend.lower()
    if backend == "gruut":
        return GruutPhonemizer(language=language)
    elif backend == "espeak":
        return EspeakPhonemizer(language=language)
    else:
        raise ValueError(f"Unknown phonemizer backend: {backend}. Use 'gruut' or 'espeak'.")

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
_custom_phonemizer: Optional[Phonemizer] = None

# Speaker embedding cache: maps file hash -> style tensor
_speaker_embedding_cache: Dict[str, Any] = {}

# Cached default voice embedding (computed once on first use)
_default_voice_embeddings: Optional[Any] = None


@dataclass
class SpeakerEmbedding:
    """Cached speaker style embedding."""
    style: Any  # torch.Tensor - style embedding from compute_style()
    file_hash: str


def compute_file_hash(file_path: str) -> str:
    """Compute SHA256 hash of a file for cache key."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()[:16]  # Use first 16 chars


def get_speaker_embeddings(speaker_wav_path: str) -> Any:
    """
    Get speaker style embedding, using cache if available.

    Args:
        speaker_wav_path: Path to the speaker reference audio file

    Returns:
        Style embedding tensor
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

    # Compute style embedding using StyleTTS 2's compute_style method
    style = model.compute_style(speaker_wav_path)

    # Cache the result
    _speaker_embedding_cache[file_hash] = style
    logger.info(f"Cached speaker embedding for {speaker_wav_path} (hash: {file_hash})")

    return style


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
    global _styletts2_model, _device, _custom_phonemizer

    if _styletts2_model is None:
        logger.info("Loading StyleTTS 2 model...")

        try:
            from styletts2 import tts as styletts2_tts

            # Check for GPU availability
            _device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Using device: {_device}")

            # Get phonemizer configuration from environment
            phonemizer_backend = os.environ.get("STYLETTS2_PHONEMIZER", "gruut").lower()
            logger.info(f"Using phonemizer backend: {phonemizer_backend}")

            # Initialize StyleTTS 2
            # The model downloads automatically on first use
            _styletts2_model = styletts2_tts.StyleTTS2()

            # Replace the model's phoneme_converter with our custom implementation
            # This allows us to support both gruut and espeak backends
            try:
                _custom_phonemizer = get_phonemizer(phonemizer_backend)
                _styletts2_model.phoneme_converter = _custom_phonemizer
                logger.info(f"Configured custom {phonemizer_backend} phonemizer")
            except Exception as e:
                logger.warning(
                    f"Failed to configure custom phonemizer ({phonemizer_backend}): {e}. "
                    "Using default phonemizer from styletts2 package."
                )

            logger.info("StyleTTS 2 model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load StyleTTS 2 model: {e}")
            raise RuntimeError(f"Failed to load StyleTTS 2 model: {e}")

    return _styletts2_model


def get_default_voice_embeddings() -> Any:
    """
    Get cached default voice (LJSpeech) style embedding.

    Computes it once on first call, then returns cached version.
    This prevents the styletts2 library from re-downloading and re-processing
    the default reference audio on every inference call.

    Returns:
        Style embedding tensor for the default voice
    """
    global _default_voice_embeddings

    if _default_voice_embeddings is not None:
        logger.debug("Using cached default voice embeddings")
        return _default_voice_embeddings

    logger.info("Computing default voice embeddings (one-time operation)...")

    model = get_model()

    # The styletts2 library uses a default LJSpeech reference audio
    # We download it and compute the style embedding
    try:
        import requests

        # This is the default reference audio URL used by styletts2
        default_audio_url = "https://styletts2.github.io/wavs/LJSpeech/OOD/GT/00001.wav"

        # Download to temp file
        response = requests.get(default_audio_url, timeout=30)
        response.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(response.content)
            temp_path = f.name

        try:
            # Compute style embedding - returns single tensor
            style = model.compute_style(temp_path)
            _default_voice_embeddings = style
            logger.info("Default voice embedding computed and cached")
        finally:
            # Clean up temp file
            os.unlink(temp_path)

    except Exception as e:
        logger.error(f"Failed to compute default voice embeddings: {e}")
        raise

    return _default_voice_embeddings


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
    phonemizer_backend = os.environ.get("STYLETTS2_PHONEMIZER", "gruut").lower()
    return {
        "name": "StyleTTS 2 Server",
        "version": "0.1.0",
        "model": "styletts2",
        "status": "ready" if _styletts2_model is not None else "loading",
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "phonemizer": phonemizer_backend,
        "speaker_cache_size": len(_speaker_embedding_cache),
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    phonemizer_backend = os.environ.get("STYLETTS2_PHONEMIZER", "gruut").lower()
    return {
        "status": "healthy",
        "model_loaded": _styletts2_model is not None,
        "phonemizer": phonemizer_backend,
        "speaker_cache_size": len(_speaker_embedding_cache),
    }


@app.get("/cache/stats")
async def cache_stats():
    """Get speaker embedding cache statistics."""
    return {
        "cache_size": len(_speaker_embedding_cache),
        "cached_speakers": list(_speaker_embedding_cache.keys()),
    }


@app.get("/voices")
async def list_voices():
    """
    List all available voices from the voices.json file.

    This endpoint allows direct voice listing from the frontend without
    going through the main application backend.
    """
    voices_dir = os.environ.get("STYLETTS2_VOICES_DIR", "./styletts2_voices")
    voices_file = Path(voices_dir) / "voices.json"

    voices = []
    if voices_file.exists():
        try:
            with open(voices_file, "r") as f:
                voices_data = json.load(f)
                for voice in voices_data:
                    # Include essential voice info for frontend display
                    voices.append({
                        "voice_id": voice.get("voice_id"),
                        "label": voice.get("label"),
                        "description": voice.get("description", ""),
                        "provider": "styletts2",
                        # Include synthesis parameters
                        "alpha": voice.get("alpha", 0.3),
                        "beta": voice.get("beta", 0.7),
                        "diffusion_steps": voice.get("diffusion_steps", 10),
                        "embedding_scale": voice.get("embedding_scale", 1.0),
                    })
        except Exception as e:
            logger.warning(f"Failed to load voices.json: {e}")

    return {
        "voices": voices,
        "provider": "styletts2",
        "voices_dir": voices_dir,
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


def adjust_audio_speed(audio_array: np.ndarray, speed: float) -> np.ndarray:
    """
    Adjust audio playback speed by resampling.

    Args:
        audio_array: Input audio samples
        speed: Speed multiplier (1.0 = normal, 2.0 = 2x faster, 0.5 = half speed)

    Returns:
        Resampled audio array
    """
    if speed == 1.0:
        return audio_array

    from scipy import signal

    # Calculate new number of samples
    # speed > 1.0 means fewer samples (faster), speed < 1.0 means more samples (slower)
    original_length = len(audio_array)
    new_length = int(original_length / speed)

    if new_length < 1:
        new_length = 1

    # Resample to new length
    resampled = signal.resample(audio_array.astype(np.float32), new_length)

    return resampled


# StyleTTS 2 text encoder (ALBERT) has a 512 token limit
# Keep chunks small to stay well under this limit
# 150 chars typically produces ~30-50 tokens, leaving headroom for edge cases
MAX_CHUNK_CHARS = 150

# =============================================================================
# Pronunciation Fixes
# =============================================================================
# Dictionary of words that StyleTTS 2 mispronounces and their phonetic corrections.
# Loaded from STYLETTS2_PRONUNCIATION_FIXES environment variable (JSON format).
# Keys are lowercase; matching is case-insensitive but preserves original case pattern.

# Default fixes if not configured via environment
_DEFAULT_PRONUNCIATION_FIXES: Dict[str, str] = {
    # Past tense -ed endings often pronounced incorrectly
    "turned": "turnd",
    "learned": "lernd",
    "burned": "burnd",
    "earned": "ernd",
    # Words that get mispronounced
    "into": "in to",
}


def _load_pronunciation_fixes() -> Dict[str, str]:
    """
    Load pronunciation fixes from environment variable or use defaults.

    The STYLETTS2_PRONUNCIATION_FIXES environment variable should be a JSON object
    mapping words to their phonetic replacements, e.g.:
    {"turned": "turnd", "into": "in to"}
    """
    env_fixes = os.environ.get("STYLETTS2_PRONUNCIATION_FIXES", "")
    if env_fixes:
        try:
            fixes = json.loads(env_fixes)
            if isinstance(fixes, dict):
                logger.info(f"Loaded {len(fixes)} pronunciation fixes from environment")
                return fixes
            else:
                logger.warning("STYLETTS2_PRONUNCIATION_FIXES is not a JSON object, using defaults")
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in STYLETTS2_PRONUNCIATION_FIXES: {e}, using defaults")

    logger.info(f"Using {len(_DEFAULT_PRONUNCIATION_FIXES)} default pronunciation fixes")
    return _DEFAULT_PRONUNCIATION_FIXES.copy()


# Load fixes once at module import time
PRONUNCIATION_FIXES: Dict[str, str] = _load_pronunciation_fixes()


def fix_pronunciation(text: str) -> str:
    """
    Apply pronunciation fixes to text before TTS synthesis.

    Uses word boundary matching to replace mispronounced words with
    phonetic spellings that StyleTTS 2 handles better.

    Args:
        text: Input text to fix

    Returns:
        Text with pronunciation fixes applied
    """
    for word, replacement in PRONUNCIATION_FIXES.items():
        # Use word boundaries to match whole words only
        # Case-insensitive matching with a function to preserve case pattern
        def replace_with_case(match: re.Match) -> str:
            original = match.group(0)
            if original.isupper():
                return replacement.upper()
            elif original[0].isupper():
                return replacement.capitalize()
            return replacement

        pattern = rf'\b{re.escape(word)}\b'
        text = re.sub(pattern, replace_with_case, text, flags=re.IGNORECASE)

    return text

# Crossfade duration in samples (at 24kHz) for smooth chunk transitions
CROSSFADE_SAMPLES = 6000  # 250ms crossfade


def crossfade_chunks(audio_arrays: list, crossfade_samples: int = CROSSFADE_SAMPLES) -> np.ndarray:
    """
    Concatenate audio arrays with crossfading to eliminate clicks/static at boundaries.

    Args:
        audio_arrays: List of numpy audio arrays
        crossfade_samples: Number of samples for the crossfade region

    Returns:
        Combined audio array with smooth transitions
    """
    if len(audio_arrays) == 0:
        return np.array([], dtype=np.float32)

    if len(audio_arrays) == 1:
        return audio_arrays[0]

    # Ensure all arrays are float for crossfading
    arrays = [arr.astype(np.float32) if arr.dtype != np.float32 else arr for arr in audio_arrays]

    # Calculate total length (accounting for overlaps)
    total_length = sum(len(arr) for arr in arrays) - crossfade_samples * (len(arrays) - 1)
    result = np.zeros(total_length, dtype=np.float32)

    # Create crossfade curves
    fade_out = np.linspace(1.0, 0.0, crossfade_samples, dtype=np.float32)
    fade_in = np.linspace(0.0, 1.0, crossfade_samples, dtype=np.float32)

    position = 0
    for i, arr in enumerate(arrays):
        if i == 0:
            # First chunk: copy all but apply fade_out to the end
            end_pos = len(arr)
            result[:end_pos] = arr

            # Apply fade out to last crossfade_samples
            if len(arr) >= crossfade_samples:
                result[end_pos - crossfade_samples:end_pos] *= fade_out

            position = end_pos - crossfade_samples
        else:
            # Subsequent chunks: crossfade with previous
            chunk_start = position
            chunk_end = position + len(arr)

            # Apply fade in to first crossfade_samples of this chunk
            if len(arr) >= crossfade_samples:
                # Add crossfaded portion
                result[chunk_start:chunk_start + crossfade_samples] += arr[:crossfade_samples] * fade_in
                # Copy the rest
                result[chunk_start + crossfade_samples:chunk_end] = arr[crossfade_samples:]
            else:
                # Chunk is shorter than crossfade, just blend it all
                blend_len = len(arr)
                blend_fade_in = np.linspace(0.0, 1.0, blend_len, dtype=np.float32)
                result[chunk_start:chunk_start + blend_len] += arr * blend_fade_in

            # Apply fade out to end if not the last chunk
            if i < len(arrays) - 1 and len(arr) >= crossfade_samples:
                fade_start = chunk_end - crossfade_samples
                result[fade_start:chunk_end] *= fade_out

            position = chunk_end - crossfade_samples

    return result


def _normalize_chunk(text: str) -> str:
    """Normalize text for TTS - remove problematic characters and fix whitespace."""
    # Apply pronunciation fixes first
    text = fix_pronunciation(text)
    # Remove brackets - can cause issues
    text = re.sub(r'[\[\]]', '', text)
    # Add period before double newlines (paragraph breaks) if no punctuation present
    # This creates natural pauses at bullet points and paragraph breaks
    text = re.sub(r'([^.!?,;:\n])\n\n', r'\1.\n\n', text)
    # Replace newlines with spaces
    text = text.replace('\n', ' ')
    # Replace standalone dashes (space-dash-space) with comma for natural pause
    # This prevents static/artifacts from isolated dashes
    text = re.sub(r'\s+[-–—]\s+', ', ', text)
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

    Uses a simple approach: split by sentences, then by clauses if needed,
    then by words as a last resort.

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
    sentences = re.split(r'(?<=[.!?])\s+', text)

    current_chunk = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # If this sentence alone is too long, split it further
        if len(sentence) > max_chars:
            # First, save any existing chunk
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            # Split the long sentence into smaller pieces
            remaining = sentence
            while remaining:
                if len(remaining) <= max_chars:
                    current_chunk = remaining
                    break

                # Find a good break point (prefer punctuation/space)
                break_point = max_chars
                for i in range(max_chars, max(0, max_chars - 50), -1):
                    if remaining[i - 1] in ' ,;:-':
                        break_point = i
                        break

                chunks.append(remaining[:break_point].strip())
                remaining = remaining[break_point:].strip()

        # If adding this sentence would exceed the limit
        elif current_chunk and len(current_chunk) + len(sentence) + 1 > max_chars:
            chunks.append(current_chunk.strip())
            current_chunk = sentence

        # Otherwise, accumulate
        else:
            if current_chunk:
                current_chunk = current_chunk + " " + sentence
            else:
                current_chunk = sentence

    # Don't forget the last chunk
    if current_chunk:
        chunks.append(current_chunk.strip())

    # Filter out empty chunks
    result = [c for c in chunks if c.strip()]

    # Log chunks for debugging
    logger.info(f"Generated {len(result)} chunks from {len(text)} chars")
    for i, chunk in enumerate(result):
        logger.info(f"  Chunk {i+1}: {chunk[:80]}{'...' if len(chunk) > 80 else ''} ({len(chunk)} chars)")

    return result


def synthesize_with_cached_embeddings(
    text: str,
    speaker_wav_path: str,
    alpha: float = 0.3,
    beta: float = 0.7,
    diffusion_steps: int = 10,
    embedding_scale: float = 1.0,
    speed: float = 1.0,
) -> bytes:
    """
    Synthesize speech using cached speaker embedding for better performance.

    Automatically chunks long text for optimal quality.

    Args:
        text: Text to synthesize
        speaker_wav_path: Path to speaker reference audio
        alpha: Timbre parameter (0-1), higher = more diverse timbre
        beta: Prosody parameter (0-1), higher = more diverse prosody
        diffusion_steps: Number of diffusion steps (5-20, higher = better quality but slower)
        embedding_scale: Classifier free guidance scale
        speed: Speech speed multiplier (0.5-2.0, 1.0 = normal)

    Returns:
        WAV audio bytes
    """
    try:
        # Get cached (or compute) speaker embedding
        logger.debug(f"Getting speaker embedding for {speaker_wav_path}")
        style = get_speaker_embeddings(speaker_wav_path)

        # Get the model
        model = get_model()

        # Split text into chunks
        chunks = split_text_into_chunks(text)
        logger.info(f"Split text into {len(chunks)} chunk(s)")

        audio_arrays = []

        for i, chunk in enumerate(chunks):
            logger.debug(f"Processing chunk {i+1}/{len(chunks)}: {chunk[:50]}...")

            # Synthesize using cached embedding
            # Pass the style embedding from compute_style() as ref_s
            audio_array = model.inference(
                text=chunk,
                ref_s=style,
                alpha=alpha,
                beta=beta,
                diffusion_steps=diffusion_steps,
                embedding_scale=embedding_scale,
            )

            # Convert to numpy array if it's a tensor
            if hasattr(audio_array, "cpu"):
                audio_array = audio_array.cpu().numpy()

            audio_arrays.append(audio_array)

        # Concatenate audio chunks with crossfading for smooth transitions
        combined_audio = crossfade_chunks(audio_arrays)

        # Apply speed adjustment if not 1.0
        if speed != 1.0:
            combined_audio = adjust_audio_speed(combined_audio, speed)

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
    speed: float = 1.0,
) -> bytes:
    """
    Synthesize speech using the default LJSpeech voice (no reference needed).

    Uses cached default voice embedding to prevent re-processing the reference
    audio for each chunk (which was causing audio looping issues).

    Args:
        text: Text to synthesize
        alpha: Timbre parameter (0-1), higher = more diverse timbre
        beta: Prosody parameter (0-1), higher = more diverse prosody
        diffusion_steps: Number of diffusion steps (5-20, higher = better quality but slower)
        embedding_scale: Classifier free guidance scale
        speed: Speech speed multiplier (0.5-2.0, 1.0 = normal)

    Returns:
        WAV audio bytes
    """
    try:
        model = get_model()

        # Get cached default voice embedding (computed once on first use)
        # This prevents the library from re-downloading and re-processing
        # the default reference audio for each chunk
        style = get_default_voice_embeddings()

        # Split text into chunks
        chunks = split_text_into_chunks(text)
        logger.info(f"Split text into {len(chunks)} chunk(s) for default voice")

        audio_arrays = []

        for i, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {i+1}/{len(chunks)}: {chunk[:80]}...")

            # Synthesize using cached default voice embedding
            # Pass the style embedding from compute_style() as ref_s
            audio_array = model.inference(
                text=chunk,
                ref_s=style,
                alpha=alpha,
                beta=beta,
                diffusion_steps=diffusion_steps,
                embedding_scale=embedding_scale,
            )

            # Convert to numpy array if it's a tensor
            if hasattr(audio_array, "cpu"):
                audio_array = audio_array.cpu().numpy()

            logger.info(f"Chunk {i+1} audio shape: {audio_array.shape}, duration: {len(audio_array)/24000:.2f}s")
            audio_arrays.append(audio_array)

        # Concatenate audio chunks with crossfading for smooth transitions
        combined_audio = crossfade_chunks(audio_arrays)

        # Apply speed adjustment if not 1.0
        if speed != 1.0:
            combined_audio = adjust_audio_speed(combined_audio, speed)

        logger.info(f"Combined audio shape: {combined_audio.shape}, total duration: {len(combined_audio)/24000:.2f}s")

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
    speed: str = Form("1.0", description="Speech speed (0.5-2.0)"),
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
        speed_val = float(speed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {e}")

    # Validate parameters
    if not 0 <= alpha_val <= 1:
        raise HTTPException(status_code=400, detail="Alpha must be between 0 and 1")
    if not 0 <= beta_val <= 1:
        raise HTTPException(status_code=400, detail="Beta must be between 0 and 1")
    if not 1 <= diffusion_steps_val <= 50:
        raise HTTPException(status_code=400, detail="Diffusion steps must be between 1 and 50")
    if not 0.5 <= speed_val <= 2.0:
        raise HTTPException(status_code=400, detail="Speed must be between 0.5 and 2.0")

    try:
        logger.info(f"Generating speech with default voice for {len(text)} chars, speed={speed_val}")
        audio_bytes = synthesize_default_voice(
            text=text,
            alpha=alpha_val,
            beta=beta_val,
            diffusion_steps=diffusion_steps_val,
            embedding_scale=embedding_scale_val,
            speed=speed_val,
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
    speed: str = Form("1.0", description="Speech speed (0.5-2.0)"),
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
        speed_val = float(speed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {e}")

    # Validate parameters
    if not 0 <= alpha_val <= 1:
        raise HTTPException(status_code=400, detail="Alpha must be between 0 and 1")
    if not 0 <= beta_val <= 1:
        raise HTTPException(status_code=400, detail="Beta must be between 0 and 1")
    if not 1 <= diffusion_steps_val <= 50:
        raise HTTPException(status_code=400, detail="Diffusion steps must be between 1 and 50")
    if not 0.5 <= speed_val <= 2.0:
        raise HTTPException(status_code=400, detail="Speed must be between 0.5 and 2.0")

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
        logger.info(f"Generating speech for {len(text)} chars, alpha={alpha_val}, beta={beta_val}, speed={speed_val}")
        audio_bytes = synthesize_with_cached_embeddings(
            text=text,
            speaker_wav_path=speaker_path,
            alpha=alpha_val,
            beta=beta_val,
            diffusion_steps=diffusion_steps_val,
            embedding_scale=embedding_scale_val,
            speed=speed_val,
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
    speed: float = 1.0


async def _tts_with_path(
    text: str,
    speaker_wav: str,
    alpha: float,
    beta: float,
    diffusion_steps: int,
    embedding_scale: float,
    speed: float = 1.0,
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
        logger.info(f"Generating speech for {len(text)} chars, alpha={alpha}, beta={beta}, speed={speed}")
        audio_bytes = synthesize_with_cached_embeddings(
            text=text,
            speaker_wav_path=str(speaker_path),
            alpha=alpha,
            beta=beta,
            diffusion_steps=diffusion_steps,
            embedding_scale=embedding_scale,
            speed=speed,
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
        request.speed,
    )


@app.post("/tts_form")
async def tts_form(
    text: str = Form(...),
    speaker_wav: str = Form(..., description="Path to speaker WAV file"),
    alpha: str = Form("0.3"),
    beta: str = Form("0.7"),
    diffusion_steps: str = Form("10"),
    embedding_scale: str = Form("1.0"),
    speed: str = Form("1.0"),
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
        speed_val = float(speed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {e}")

    return await _tts_with_path(
        text,
        speaker_wav,
        alpha_val,
        beta_val,
        diffusion_steps_val,
        embedding_scale_val,
        speed_val,
    )


@app.post("/tts_stream")
async def tts_stream(
    text: str = Form(...),
    speaker_wav: UploadFile = File(...),
    alpha: str = Form("0.3"),
    beta: str = Form("0.7"),
    diffusion_steps: str = Form("10"),
    embedding_scale: str = Form("1.0"),
    speed: str = Form("1.0"),
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
        speed=speed,
    )


def _get_voice_by_id(voice_id: str) -> Optional[Dict[str, Any]]:
    """
    Look up a voice by ID from voices.json.

    Returns the voice dict if found, None otherwise.
    """
    voices_dir = os.environ.get("STYLETTS2_VOICES_DIR", "./styletts2_voices")
    voices_file = Path(voices_dir) / "voices.json"

    if not voices_file.exists():
        return None

    try:
        with open(voices_file, "r") as f:
            voices_data = json.load(f)
            for voice in voices_data:
                if voice.get("voice_id") == voice_id:
                    return voice
    except Exception as e:
        logger.warning(f"Failed to load voices.json: {e}")

    return None


@app.post("/tts_with_voice")
async def tts_with_voice(
    text: str = Form(..., description="Text to synthesize"),
    voice_id: str = Form(..., description="Voice ID from voices.json"),
    alpha: str = Form(None, description="Timbre parameter (0-1), uses voice default if not provided"),
    beta: str = Form(None, description="Prosody parameter (0-1), uses voice default if not provided"),
    diffusion_steps: str = Form(None, description="Diffusion steps (5-20), uses voice default if not provided"),
    embedding_scale: str = Form(None, description="Embedding scale, uses voice default if not provided"),
    speed: str = Form(None, description="Speech speed (0.5-2.0), uses voice default if not provided"),
):
    """
    Convert text to speech using a voice ID from voices.json.

    This endpoint is designed for direct local mode where the frontend
    specifies a voice_id and the server looks up the speaker file.
    """
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    # Look up the voice
    voice = _get_voice_by_id(voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail=f"Voice not found: {voice_id}")

    sample_path = voice.get("sample_path")
    if not sample_path or not os.path.exists(sample_path):
        raise HTTPException(
            status_code=500,
            detail=f"Voice sample file not found for voice: {voice_id}"
        )

    # Use provided parameters or fall back to voice defaults
    try:
        alpha_val = float(alpha) if alpha is not None else voice.get("alpha", 0.3)
        beta_val = float(beta) if beta is not None else voice.get("beta", 0.7)
        diffusion_steps_val = int(diffusion_steps) if diffusion_steps is not None else voice.get("diffusion_steps", 10)
        embedding_scale_val = float(embedding_scale) if embedding_scale is not None else voice.get("embedding_scale", 1.0)
        speed_val = float(speed) if speed is not None else voice.get("speed", 1.0)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {e}")

    # Validate parameters
    if not 0 <= alpha_val <= 1:
        raise HTTPException(status_code=400, detail="Alpha must be between 0 and 1")
    if not 0 <= beta_val <= 1:
        raise HTTPException(status_code=400, detail="Beta must be between 0 and 1")
    if not 1 <= diffusion_steps_val <= 50:
        raise HTTPException(status_code=400, detail="Diffusion steps must be between 1 and 50")
    if not 0.5 <= speed_val <= 2.0:
        raise HTTPException(status_code=400, detail="Speed must be between 0.5 and 2.0")

    try:
        logger.info(f"Generating speech with voice '{voice_id}' for {len(text)} chars, speed={speed_val}")
        audio_bytes = synthesize_with_cached_embeddings(
            text=text,
            speaker_wav_path=sample_path,
            alpha=alpha_val,
            beta=beta_val,
            diffusion_steps=diffusion_steps_val,
            embedding_scale=embedding_scale_val,
            speed=speed_val,
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


def create_app() -> FastAPI:
    """Factory function to create the app (useful for testing)."""
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8021)
