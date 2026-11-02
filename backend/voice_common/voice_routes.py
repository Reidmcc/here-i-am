"""Voice management and synthesis routes shared by the TTS servers (issue #399).

The browser calls these directly:

    GET    /voices             {"provider", "voices": [...]}
    GET    /voices/{id}        one voice
    POST   /voices             multipart: audio_file, label, description, settings
    PUT    /voices/{id}        JSON: label, description, settings
    DELETE /voices/{id}
    POST   /speak              JSON: text, voice_id?, setting overrides -> audio/wav

These replace the backend's ``/api/tts/voices*`` and ``/api/tts/speak`` for the
local providers, with the same validation, messages and response shapes.
"""

import logging
from typing import Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from voice_common.voice_library import Param, VoiceLibrary, validate_params

logger = logging.getLogger(__name__)

SPEAK_MAX_CHARS = 5000

# synthesize(text, voice or None, validated overrides) -> WAV bytes.
# Raises ValueError for a problem the caller can fix (no sample configured).
Synthesize = Callable[[str, Optional[dict], Dict[str, float]], bytes]


async def _json_object(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Expected a JSON body") from e
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    return body


def make_voice_router(
    library: VoiceLibrary,
    synthesize: Synthesize,
    speak_params: Dict[str, Param],
) -> APIRouter:
    router = APIRouter()

    def _library_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @router.get("/voices")
    async def list_voices():
        return {"provider": library.provider, "voices": _library_call(library.list_public)}

    @router.get("/voices/{voice_id}")
    async def get_voice(voice_id: str):
        voice = _library_call(library.get, voice_id)
        if voice is None:
            raise HTTPException(status_code=404, detail="Voice not found")
        return library.public(voice)

    @router.post("/voices")
    async def clone_voice(request: Request):
        form = await request.form()
        audio_file = form.get("audio_file")
        if not isinstance(audio_file, UploadFile):
            raise HTTPException(status_code=400, detail="audio_file is required")
        label = str(form.get("label") or "")
        voice = _library_call(
            library.clone,
            audio=await audio_file.read(),
            filename=audio_file.filename or "",
            label=label,
            description=str(form.get("description") or ""),
            params={name: form.get(name) for name in library.params},
        )
        return {
            "success": True,
            "voice": library.public(voice),
            "message": f"Voice '{voice['label']}' created successfully",
        }

    @router.put("/voices/{voice_id}")
    async def update_voice(voice_id: str, request: Request):
        body = await _json_object(request)
        voice = _library_call(
            library.update,
            voice_id,
            label=body.get("label"),
            description=body.get("description"),
            params={name: body.get(name) for name in library.params},
        )
        if voice is None:
            raise HTTPException(status_code=404, detail="Voice not found")
        return {"success": True, "voice": library.public(voice), "message": "Voice updated successfully"}

    @router.delete("/voices/{voice_id}")
    async def delete_voice(voice_id: str):
        if not _library_call(library.delete, voice_id):
            raise HTTPException(status_code=404, detail="Voice not found")
        return {"success": True, "message": "Voice deleted successfully"}

    @router.post("/speak")
    async def speak(request: Request):
        body = await _json_object(request)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=400, detail="Text is required")
        if len(text) > SPEAK_MAX_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"Text exceeds maximum length of {SPEAK_MAX_CHARS} characters",
            )
        overrides = _library_call(
            validate_params, speak_params, {name: body.get(name) for name in speak_params}
        )
        # An unknown voice_id (a selection saved before the voice was
        # deleted) falls back to the default voice, as the backend did.
        voice_id = body.get("voice_id")
        voice = _library_call(library.get, voice_id) if voice_id else None
        try:
            audio = synthesize(text, voice, overrides)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error(f"TTS generation failed: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to generate speech: {e}") from e
        return Response(content=audio, media_type="audio/wav", headers={"Content-Disposition": "inline"})

    return router
