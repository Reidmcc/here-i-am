"""The TTS servers' own voice library and routes (issue #399).

Tested on a stand-in app with a fake synthesizer: the real servers load
torch and a model at import.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from voice_common.voice_library import Param, VoiceLibrary
from voice_common.voice_routes import SPEAK_MAX_CHARS, make_voice_router

PARAMS = {
    "alpha": Param(0.3, 0.0, 1.0),
    "diffusion_steps": Param(10, 1, 50, integer=True),
}
SPEAK_PARAMS = {**PARAMS, "speed": Param(1.0, 0.5, 2.0)}
SAMPLE = b"RIFF" + b"\0" * 2000


@pytest.fixture
def library(tmp_path):
    return VoiceLibrary(str(tmp_path / "voices"), "styletts2", PARAMS)


@pytest.fixture
def spoken():
    return []


@pytest.fixture
def client(library, spoken):
    def synthesize(text, voice, overrides):
        if voice is not None and library.sample_path(voice) is None:
            raise ValueError("sample missing")
        spoken.append((text, voice and voice["voice_id"], library.settings_for(voice, overrides), overrides))
        return b"WAVDATA"

    app = FastAPI()
    app.include_router(make_voice_router(library, synthesize, SPEAK_PARAMS))
    return TestClient(app)


def _clone(client, label="Ada", filename="ada.wav", data=SAMPLE, **fields):
    return client.post(
        "/voices",
        data={"label": label, "description": "d", **{k: str(v) for k, v in fields.items()}},
        files={"audio_file": (filename, data, "audio/wav")},
    )


def test_clone_list_get_update_delete(client, library):
    r = _clone(client, alpha=0.5)
    assert r.status_code == 200, r.text
    voice = r.json()["voice"]
    assert voice["label"] == "Ada" and voice["alpha"] == 0.5 and voice["diffusion_steps"] == 10
    assert voice["provider"] == "styletts2"
    assert "sample_path" not in voice  # a path on the GPU machine, never sent out
    vid = voice["voice_id"]
    assert (library.voices_dir / f"{vid}.wav").read_bytes() == SAMPLE

    listed = client.get("/voices").json()
    assert listed["provider"] == "styletts2"
    assert [v["voice_id"] for v in listed["voices"]] == [vid]
    assert client.get(f"/voices/{vid}").json()["label"] == "Ada"

    r = client.put(f"/voices/{vid}", json={"label": "Ada L", "diffusion_steps": 20})
    assert r.status_code == 200
    assert r.json()["voice"]["label"] == "Ada L" and r.json()["voice"]["diffusion_steps"] == 20
    assert r.json()["voice"]["alpha"] == 0.5  # untouched settings stay

    assert client.delete(f"/voices/{vid}").json()["success"] is True
    assert not (library.voices_dir / f"{vid}.wav").exists()
    assert client.get("/voices").json()["voices"] == []
    assert client.delete(f"/voices/{vid}").status_code == 404


@pytest.mark.parametrize("kwargs, message", [
    ({"label": ""}, "needs a name"),
    ({"filename": "ada.exe"}, "WAV, MP3, FLAC, or OGG"),
    ({"data": b"x" * 10}, "too small"),
    ({"alpha": 1.5}, "alpha must be between"),
    ({"diffusion_steps": "many"}, "diffusion_steps must be a number"),
])
def test_clone_validation(client, library, kwargs, message):
    r = _clone(client, **kwargs)
    assert r.status_code == 400
    assert message in r.json()["detail"]
    assert not library.voices_file.exists()


def test_clone_requires_a_file(client):
    assert client.post("/voices", data={"label": "x"}).status_code == 400


def test_update_validates_and_404s(client):
    vid = _clone(client).json()["voice"]["voice_id"]
    assert client.put(f"/voices/{vid}", json={"alpha": -1}).status_code == 400
    assert client.put(f"/voices/{vid}", json={"label": "  "}).status_code == 400
    assert client.put("/voices/nope", json={"label": "x"}).status_code == 404
    assert client.put(f"/voices/{vid}", content=b"[1]").status_code == 400


def test_speak_uses_override_then_saved_setting_then_default(client, spoken):
    vid = _clone(client, alpha=0.6).json()["voice"]["voice_id"]
    r = client.post("/speak", json={"text": "hello", "voice_id": vid, "diffusion_steps": 5, "speed": 1.5})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav" and r.content == b"WAVDATA"
    text, voice_id, settings, overrides = spoken[-1]
    assert (text, voice_id) == ("hello", vid)
    assert settings == {"alpha": 0.6, "diffusion_steps": 5}
    assert overrides["speed"] == 1.5


def test_speak_without_voice_and_with_unknown_voice_uses_default(client, spoken):
    assert client.post("/speak", json={"text": "hi"}).status_code == 200
    assert client.post("/speak", json={"text": "hi", "voice_id": "gone"}).status_code == 200
    assert [s[1] for s in spoken] == [None, None]
    assert spoken[0][2] == {"alpha": 0.3, "diffusion_steps": 10}


@pytest.mark.parametrize("body, status", [
    ({"text": ""}, 400),
    ({"text": "x" * (SPEAK_MAX_CHARS + 1)}, 400),
    ({"text": "hi", "speed": 9}, 400),
    ({}, 400),
])
def test_speak_validation(client, body, status):
    assert client.post("/speak", json=body).status_code == status


def test_speak_reports_a_fixable_problem_as_400(client, library):
    vid = _clone(client).json()["voice"]["voice_id"]
    (library.voices_dir / f"{vid}.wav").unlink()
    r = client.post("/speak", json={"text": "hi", "voice_id": vid})
    assert r.status_code == 400 and "sample missing" in r.json()["detail"]


def test_reads_the_backends_old_voices_json(tmp_path):
    """The backend wrote sample_path relative to backend/ (./xtts_voices/...).
    Run from anywhere, the sample still resolves by filename inside the
    voices directory, and old entries without a setting get its default."""
    voices_dir = tmp_path / "xtts_voices"
    voices_dir.mkdir()
    (voices_dir / "ab12cd34.wav").write_bytes(SAMPLE)
    (voices_dir / "voices.json").write_text(json.dumps([{
        "voice_id": "ab12cd34", "label": "Old", "description": "",
        "sample_path": "xtts_voices/ab12cd34.wav", "provider": "xtts",
        "alpha": 0.4,
    }]))
    lib = VoiceLibrary(str(voices_dir), "xtts", PARAMS)
    voice = lib.get("ab12cd34")
    assert lib.sample_path(voice) == voices_dir / "ab12cd34.wav"
    assert lib.public(voice) == {
        "voice_id": "ab12cd34", "label": "Old", "description": "", "provider": "xtts",
        "is_cloned": True, "alpha": 0.4, "diffusion_steps": 10,
    }


def test_unreadable_voices_json_is_refused_not_overwritten(client, library):
    library.voices_dir.mkdir(parents=True)
    library.voices_file.write_text("{not json")
    assert client.get("/voices").status_code == 500
    assert _clone(client).status_code == 500
    assert library.voices_file.read_text() == "{not json"
