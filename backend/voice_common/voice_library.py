"""A TTS server's own library of cloned voices (issue #399).

Each TTS server (XTTS, StyleTTS 2) keeps its voices in its voices directory:
the reference samples and a ``voices.json`` listing them with their
synthesis settings. Until #399 the backend kept these files and uploaded the
sample on every request; now the server keeps them, because the browser on
the GPU machine calls it directly and the backend may live on another
machine.

The ``voices.json`` format is the one the backend wrote, so an existing
voices directory works as-is: point ``XTTS_VOICES_DIR`` /
``STYLETTS2_VOICES_DIR`` at it (the defaults, ``./xtts_voices`` and
``./styletts2_voices`` under ``backend/``, are the directories the backend
used). Sample paths in old files may be relative to ``backend/``; a path that
doesn't resolve falls back to the same filename inside the voices directory.

``sample_path`` is never returned to a client: it's a path on this machine.
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SAMPLE_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg")
SAMPLE_MIN_BYTES = 1000
SAMPLE_MAX_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class Param:
    """A numeric synthesis setting: its default and its allowed range."""
    default: float
    low: float
    high: float
    integer: bool = False

    def coerce(self, name: str, value) -> float:
        try:
            number = int(value) if self.integer else float(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{name} must be a number") from e
        if not self.low <= number <= self.high:
            raise ValueError(f"{name} must be between {self.low} and {self.high}")
        return number


def validate_params(specs: Dict[str, Param], values: Dict[str, object]) -> Dict[str, float]:
    """Coerce and range-check the values given; None means "not given"."""
    return {
        name: specs[name].coerce(name, value)
        for name, value in values.items()
        if name in specs and value is not None
    }


class VoiceLibrary:
    def __init__(self, voices_dir: str, provider: str, params: Dict[str, Param]):
        self.voices_dir = Path(voices_dir)
        self.voices_file = self.voices_dir / "voices.json"
        self.provider = provider
        self.params = params

    # -- storage ------------------------------------------------------------

    def _load(self) -> List[dict]:
        if not self.voices_file.exists():
            return []
        try:
            data = json.loads(self.voices_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            # Refuse rather than treat as empty: a save would overwrite the
            # unreadable file and lose every voice in it.
            raise RuntimeError(f"Cannot read {self.voices_file}: {e}") from e
        return [v for v in data if isinstance(v, dict) and v.get("voice_id")]

    def _save(self, voices: List[dict]) -> None:
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.voices_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(voices, indent=2), encoding="utf-8")
        os.replace(tmp, self.voices_file)

    # -- reading ------------------------------------------------------------

    def public(self, voice: dict) -> dict:
        """What a client sees: settings filled with defaults, no path."""
        out = {
            "voice_id": voice["voice_id"],
            "label": voice.get("label", ""),
            "description": voice.get("description", ""),
            "provider": self.provider,
            # Every library voice is cloned; the settings UI lists these
            # for editing and deleting.
            "is_cloned": True,
        }
        for name, spec in self.params.items():
            out[name] = voice.get(name, spec.default)
        return out

    def list_public(self) -> List[dict]:
        return [self.public(v) for v in self._load()]

    def get(self, voice_id: str) -> Optional[dict]:
        for voice in self._load():
            if voice["voice_id"] == voice_id:
                return voice
        return None

    def sample_path(self, voice: dict) -> Optional[Path]:
        stored = voice.get("sample_path") or ""
        if not stored:
            return None
        path = Path(stored)
        if path.is_file():
            return path
        fallback = self.voices_dir / path.name
        return fallback if fallback.is_file() else None

    def settings_for(self, voice: Optional[dict], overrides: Dict[str, float]) -> Dict[str, float]:
        """override > the voice's saved setting > default."""
        return {
            name: overrides.get(name, (voice or {}).get(name, spec.default))
            for name, spec in self.params.items()
        }

    # -- writing ------------------------------------------------------------

    def clone(
        self,
        audio: bytes,
        filename: str,
        label: str,
        description: str = "",
        params: Optional[Dict[str, object]] = None,
    ) -> dict:
        label = (label or "").strip()
        if not label:
            raise ValueError("A voice needs a name")
        ext = Path(filename or "").suffix.lower()
        if ext not in SAMPLE_EXTENSIONS:
            raise ValueError("Upload a WAV, MP3, FLAC, or OGG audio file")
        if len(audio) < SAMPLE_MIN_BYTES:
            raise ValueError("Audio file too small. Please provide a longer sample.")
        if len(audio) > SAMPLE_MAX_BYTES:
            raise ValueError("File too large. Maximum size is 50MB.")
        settings = {name: spec.default for name, spec in self.params.items()}
        settings.update(validate_params(self.params, params or {}))

        voices = self._load()
        taken = {v["voice_id"] for v in voices}
        voice_id = uuid.uuid4().hex[:8]
        while voice_id in taken:
            voice_id = uuid.uuid4().hex[:8]

        self.voices_dir.mkdir(parents=True, exist_ok=True)
        sample = self.voices_dir / f"{voice_id}{ext}"
        sample.write_bytes(audio)

        voice = {
            "voice_id": voice_id,
            "label": label,
            "description": description or "",
            "sample_path": str(sample),
            "provider": self.provider,
            **settings,
        }
        voices.append(voice)
        self._save(voices)
        logger.info(f"Created {self.provider} voice {voice_id} ({label})")
        return voice

    def update(
        self,
        voice_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        params: Optional[Dict[str, object]] = None,
    ) -> Optional[dict]:
        changes = validate_params(self.params, params or {})
        if label is not None and not label.strip():
            raise ValueError("A voice needs a name")
        voices = self._load()
        for voice in voices:
            if voice["voice_id"] == voice_id:
                if label is not None:
                    voice["label"] = label.strip()
                if description is not None:
                    voice["description"] = description
                voice.update(changes)
                self._save(voices)
                logger.info(f"Updated {self.provider} voice {voice_id}")
                return voice
        return None

    def delete(self, voice_id: str) -> bool:
        voices = self._load()
        voice = next((v for v in voices if v["voice_id"] == voice_id), None)
        if voice is None:
            return False
        sample = self.sample_path(voice)
        if sample is not None:
            try:
                sample.unlink()
            except OSError as e:
                logger.warning(f"Failed to delete voice sample {sample}: {e}")
        self._save([v for v in voices if v["voice_id"] != voice_id])
        logger.info(f"Deleted {self.provider} voice {voice_id}")
        return True
