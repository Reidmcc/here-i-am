"""The local voice servers' origin guard and bind defaults (issue #399).

The servers import torch and their models at module load, so the guard is
tested on a stand-in app, and the servers' own wiring is pinned by reading
their source.
"""
import ast
from pathlib import Path

import pytest
from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient

from voice_common import origin_guard
from voice_common.origin_guard import OriginGuard

BACKEND = Path(__file__).resolve().parent.parent
SERVERS = ("xtts_server", "styletts2_server", "whisper_server")
APP_ORIGIN = "https://him.example.ts.net"


@pytest.fixture
def calls():
    return []


@pytest.fixture
def client(calls):
    app = FastAPI()

    @app.post("/tts_to_audio")
    async def tts(speaker_wav: UploadFile = File(...)):
        calls.append(await speaker_wav.read())
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.add_middleware(OriginGuard, allowed_origins=["http://localhost:8000", APP_ORIGIN])
    return TestClient(app)


def _post(client, origin=None):
    headers = {"Origin": origin} if origin else {}
    return client.post("/tts_to_audio", files={"speaker_wav": ("a.wav", b"x" * 10)}, headers=headers)


def test_request_without_origin_passes(client, calls):
    """curl and same-machine scripts send no Origin; loopback already limits them."""
    assert _post(client).status_code == 200
    assert len(calls) == 1


def test_allowed_origin_gets_cors_header(client, calls):
    r = _post(client, APP_ORIGIN)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == APP_ORIGIN
    assert len(calls) == 1


def test_other_origin_is_refused_before_the_route_runs(client, calls):
    """A multipart POST is a simple request: no preflight protects it, so the
    guard must refuse it outright or the route would act on it."""
    r = _post(client, "https://evil.example")
    assert r.status_code == 403
    assert "--allow-origin https://evil.example" in r.json()["detail"]
    assert "access-control-allow-origin" not in r.headers
    assert calls == []


def test_null_origin_is_refused(client, calls):
    assert _post(client, "null").status_code == 403
    assert calls == []


def test_origin_match_ignores_case_and_trailing_slash(client):
    assert _post(client, "HTTPS://him.example.ts.net/").status_code == 200


def test_servers_own_origin_is_allowed(client):
    """The server's own /docs page posts with its own origin."""
    r = client.get("/health", headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_preflight_for_allowed_origin(client, calls):
    r = client.options("/tts_to_audio", headers={
        "Origin": APP_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
        "Access-Control-Request-Private-Network": "true",
    })
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == APP_ORIGIN
    assert "POST" in r.headers["access-control-allow-methods"]
    assert r.headers["access-control-allow-headers"] == "content-type"
    assert r.headers["access-control-allow-private-network"] == "true"
    assert calls == []


def test_preflight_without_private_network_request_omits_that_header(client):
    r = client.options("/tts_to_audio", headers={
        "Origin": APP_ORIGIN, "Access-Control-Request-Method": "POST",
    })
    assert r.status_code == 204
    assert "access-control-allow-private-network" not in r.headers


def test_preflight_for_other_origin_is_refused(client):
    r = client.options("/tts_to_audio", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
    })
    assert r.status_code == 403


def test_default_origins_are_the_local_app(monkeypatch):
    monkeypatch.delenv(origin_guard.ALLOWED_ORIGINS_ENV, raising=False)
    assert origin_guard.allowed_origins_from_env() == [
        "http://localhost:8000", "http://127.0.0.1:8000",
    ]


def test_allow_origin_flag_adds_to_defaults_and_exports(monkeypatch):
    monkeypatch.delenv(origin_guard.ALLOWED_ORIGINS_ENV, raising=False)
    origins = origin_guard.set_allowed_origins_env([APP_ORIGIN + "/", APP_ORIGIN])
    assert origins == ["http://localhost:8000", "http://127.0.0.1:8000", APP_ORIGIN]
    assert origin_guard.allowed_origins_from_env() == origins


# --- the servers' own wiring, read from source -----------------------------

def _source(path):
    return (BACKEND / path).read_text(encoding="utf-8")


def _routes(path):
    """Every path registered with an @app.<method>("...") decorator."""
    routes = set()
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and getattr(dec.func.value, "id", None) == "app" and dec.args
                        and isinstance(dec.args[0], ast.Constant)):
                    routes.add(dec.args[0].value)
    return routes


@pytest.mark.parametrize("server", SERVERS)
def test_server_installs_the_guard_and_no_wildcard_cors(server):
    src = _source(f"{server}/server.py")
    assert "install_origin_guard(app)" in src
    assert "CORSMiddleware" not in src
    assert 'allow_origins=["*"]' not in src


@pytest.mark.parametrize("server", SERVERS)
def test_server_binds_loopback_by_default(server):
    main = _source(f"{server}/__main__.py") + _source(f"{server}/server.py")
    assert "0.0.0.0" not in main
    assert "127.0.0.1" in main
    assert "set_allowed_origins_env(args.allow_origin)" in main


@pytest.mark.parametrize("server", ["xtts_server", "styletts2_server"])
def test_no_endpoint_takes_a_path_on_the_servers_disk(server):
    """/tts and /tts_form opened any path they were given; /cache/preload
    hashed any path. Speaker audio arrives as an upload instead."""
    routes = _routes(f"{server}/server.py")
    assert not routes & {"/tts", "/tts_form", "/cache/preload"}
    assert "/tts_to_audio" in routes
