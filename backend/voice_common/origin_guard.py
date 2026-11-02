"""Origin allow-list for the local voice servers (issue #399).

The voice servers listen on 127.0.0.1 with no authentication. Binding to
loopback keeps other machines out; this guard keeps other *web pages* out.
CORS alone would not: a multipart POST is a "simple" request, so a page on
any site could send one to http://localhost:8020 without a preflight and
the server would act on it, even though the page couldn't read the reply.

So any request that carries an ``Origin`` header must name an allowed
origin, or it gets a 403 before it reaches a route. Requests without one
(curl, scripts on the same machine) pass: loopback already limits those to
this machine's own processes. The server's own origin (its ``/docs`` page)
is always allowed.

Allowed origins come from ``VOICE_ALLOWED_ORIGINS`` (comma-separated). The
default is the app on this machine; a cloud deployment adds its own
``https://<host>.<tailnet>.ts.net`` origin with the ``--allow-origin`` flag
on each server.
"""

import json
import os
from typing import Iterable, List, Optional

ALLOWED_ORIGINS_ENV = "VOICE_ALLOWED_ORIGINS"
DEFAULT_ALLOWED_ORIGINS = ("http://localhost:8000", "http://127.0.0.1:8000")
ALLOWED_METHODS = "GET, POST, PUT, DELETE"
PREFLIGHT_MAX_AGE = "600"


def normalize_origin(origin: str) -> str:
    """Origins compare as scheme://host[:port], no trailing slash, lower case."""
    return origin.strip().rstrip("/").lower()


def allowed_origins_from_env() -> List[str]:
    raw = os.environ.get(ALLOWED_ORIGINS_ENV, "")
    origins = [normalize_origin(o) for o in raw.split(",") if o.strip()]
    return origins or [normalize_origin(o) for o in DEFAULT_ALLOWED_ORIGINS]


def set_allowed_origins_env(extra: Iterable[str]) -> List[str]:
    """Add ``--allow-origin`` values to the defaults and export them.

    Called by each server's ``__main__`` before uvicorn imports the app, so
    the app (and a ``--reload`` child process) reads the same list.
    """
    origins = allowed_origins_from_env()
    for origin in extra:
        norm = normalize_origin(origin)
        if norm and norm not in origins:
            origins.append(norm)
    os.environ[ALLOWED_ORIGINS_ENV] = ",".join(origins)
    return origins


def _header(scope, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class OriginGuard:
    """ASGI middleware: allow-listed CORS, and a 403 for any other origin."""

    def __init__(self, app, allowed_origins: Optional[Iterable[str]] = None):
        self.app = app
        origins = allowed_origins if allowed_origins is not None else allowed_origins_from_env()
        self.allowed = {normalize_origin(o) for o in origins}

    def _is_allowed(self, scope, origin: str) -> bool:
        norm = normalize_origin(origin)
        if norm in self.allowed:
            return True
        host = _header(scope, b"host")
        scheme = scope.get("scheme", "http")
        return host is not None and norm == normalize_origin(f"{scheme}://{host}")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        origin = _header(scope, b"origin")
        if origin is None:
            await self.app(scope, receive, send)
            return

        if not self._is_allowed(scope, origin):
            await _send_json(send, 403, {
                "detail": (
                    f"Origin {origin} is not allowed. Start the voice server with "
                    f"--allow-origin {origin} if this is the Here I Am app."
                ),
            })
            return

        if scope["method"] == "OPTIONS" and _header(scope, b"access-control-request-method"):
            headers = [
                (b"access-control-allow-origin", origin.encode("latin-1")),
                (b"access-control-allow-methods", ALLOWED_METHODS.encode()),
                (b"access-control-max-age", PREFLIGHT_MAX_AGE.encode()),
                (b"vary", b"Origin"),
            ]
            requested = _header(scope, b"access-control-request-headers")
            if requested:
                headers.append((b"access-control-allow-headers", requested.encode("latin-1")))
            if _header(scope, b"access-control-request-private-network"):
                # Chrome's Private Network Access preflight for a page that
                # isn't itself on this machine calling localhost.
                headers.append((b"access-control-allow-private-network", b"true"))
            await send({"type": "http.response.start", "status": 204, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"access-control-allow-origin", origin.encode("latin-1")))
                headers.append((b"vary", b"Origin"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cors)


async def _send_json(send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": payload})


def install_origin_guard(app) -> List[str]:
    """Add the guard to a FastAPI app; returns the allowed origins."""
    origins = allowed_origins_from_env()
    app.add_middleware(OriginGuard, allowed_origins=origins)
    return origins
