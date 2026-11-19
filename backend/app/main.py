import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.auth import authenticate
from app.auth import store as auth_store
from app.config import find_retired_voice_env_vars, retired_voice_env_warning, settings
from app.database import async_session_maker, init_db
from app.routes import (
    auth_router,
    chat_router,
    claude_code_mcp_router,
    claude_code_router,
    conversations_router,
    entities_router,
    github_router,
    memories_router,
    messages_router,
    notes_router,
    tts_router,
)
from app.services.memory_service import memory_service


def setup_logging():
    """Configure logging for the application."""
    # Create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Configure root logger at INFO level (keeps third-party libs quiet)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Add stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    # Set app-specific log level based on debug setting
    app_log_level = logging.DEBUG if settings.debug else logging.INFO
    logging.getLogger("app").setLevel(app_log_level)

    # Suppress noisy third-party libraries
    for lib in ["uvicorn.access", "httpx", "httpcore", "aiosqlite",
                "sqlalchemy", "anthropic", "openai", "pinecone"]:
        logging.getLogger(lib).setLevel(logging.WARNING)

    # Log startup message
    logging.info(f"Logging configured (app: {logging.getLevelName(app_log_level)}, libs: WARNING)")


# Initialize logging on module load
setup_logging()


def run_pinecone_connection_test():
    """Run Pinecone connection test and print results to terminal."""
    print("\n" + "=" * 60)
    print("MEMORY SYSTEM STATUS")
    print("=" * 60)

    result = memory_service.test_connection()

    if not result["configured"]:
        print("Status: SKIPPED")
        print("Reason: No PINECONE_API_KEY configured in environment")
        print("Memory system will be disabled.")
        print("=" * 60 + "\n")
        return

    print("Pinecone API Key: Configured")
    print("Embedding Model: Pinecone integrated inference (llama-text-embed-v2)")

    print(f"\nEntities to test: {len(result['entities'])}")
    print("-" * 60)

    all_passed = True
    for entity in result["entities"]:
        status = "PASS" if entity["success"] else "FAIL"
        if not entity["success"]:
            all_passed = False

        print(f"\nEntity: {entity.get('label', 'Unknown')} ({entity['entity_id']})")
        print(f"  Host: {entity.get('host') or 'Not specified'}")
        print(f"  Status: {status}")
        print(f"  Message: {entity['message']}")

        if entity["stats"]:
            print(f"  Vector count: {entity['stats']['total_vector_count']}")
            print(f"  Dimension: {entity['stats']['dimension']}")

    print("\n" + "-" * 60)
    overall = "ALL TESTS PASSED" if all_passed else "SOME TESTS FAILED"
    print(f"Overall: {overall}")
    print("=" * 60 + "\n")


def warn_retired_voice_settings():
    """Say aloud when an old .env still configures the removed voice proxy (issue #399)."""
    names = find_retired_voice_env_vars()
    if names:
        logging.getLogger("app.main").warning(retired_voice_env_warning(names))


def warn_unloadable_local_timezone():
    """Say aloud at startup when LOCAL_TIMEZONE names a zone that won't
    load. Resolving the zone is what warns (once per name, and the answer
    is kept), so the first stamp rendered afterwards doesn't say it again."""
    from app.services.local_time import configured_zone

    configured_zone()


async def auth_setup_warnings() -> list:
    """What is missing before anyone can use this server (issue #396):
    authentication is always on, so a server with no password and no tokens
    answers nobody. One line per gap, each naming the command that fills it."""
    async with async_session_maker() as db:
        has_password = await auth_store.get_password_hash(db) is not None
        tokens = [t for t in await auth_store.list_tokens(db) if t.revoked_at is None]
    lines = []
    if not has_password:
        lines.append(
            "No researcher password is set, so nobody can log in to the web page. "
            "In the backend directory run: python -m app.auth init"
        )
    if settings.claude_code_mode_enabled and not any(t.scope == "entity" for t in tokens):
        lines.append(
            "Claude Code mode is on but no entity token exists, so the hooks and "
            "the memory tools will be refused. Mint one per machine: "
            "python -m app.auth token create --name <machine> --entity <entity>"
        )
    return lines


async def warn_auth_setup():
    try:
        for line in await auth_setup_warnings():
            logging.getLogger("app.main").warning(f"[AUTH] {line}")
    except Exception as e:
        logging.getLogger("app.main").warning(f"[AUTH] Could not check the auth setup: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    await warn_auth_setup()
    run_pinecone_connection_test()
    warn_retired_voice_settings()
    warn_unloadable_local_timezone()
    yield
    # Shutdown
    pass


def add_api_docs(app: FastAPI) -> None:
    """FastAPI's docs pages (/docs, /redoc, /openapi.json), as ordinary
    routes of the app.

    FastAPI's built-in ones are plain Starlette routes, which the app-level
    auth dependency doesn't cover, and the schema is a map of every
    endpoint. So the built-ins stay off, and with API_DOCS_ENABLED=true
    these are registered in their place: API routes like any other, so they
    need a researcher login (issue #396). docs/api.md is the listing
    otherwise.
    """
    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_schema():
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui():
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - API")

    @app.get("/redoc", include_in_schema=False)
    async def redoc():
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - API")


# Every route below answers only to a credential (issue #396; app/auth):
# the dependency is on the app, so a route added later is closed without
# anyone remembering to close it. What is open is policy.PUBLIC_ROUTES.
app = FastAPI(
    title="Here I Am",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(authenticate)],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

if settings.api_docs_enabled:
    add_api_docs(app)

# No CORS middleware, on purpose (issue #396). The frontend is served by
# this app, so its requests are same-origin and need no CORS headers. The
# old allow-every-origin setting let a page on any site read the API from a
# browser that could reach it, and with credentials allowed Starlette echoes
# the caller's origin whenever a cookie is present, which would make that a
# credentialed read now that auth cookies exist. The cookie check's
# same-origin rule (app/auth/origin.py) leans on there being none.


# Middleware to disable caching for static files (development)


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Add no-cache headers for HTML, JS, and CSS files
        path = request.url.path
        if path.endswith(('.html', '.js', '.css')) or path == '/':
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheMiddleware)


# API routes
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(memories_router)
app.include_router(entities_router)
app.include_router(messages_router)
app.include_router(tts_router)
app.include_router(github_router)
app.include_router(notes_router)
app.include_router(claude_code_router)
app.include_router(claude_code_mcp_router)



@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    # Liveness only (issue #396): this is the one endpoint besides login
    # that answers without a credential, so it says nothing about the
    # configuration. Memory status is at /api/memories/status/health.
    return {
        "status": "healthy",
        "version": "0.1.0",
    }


@app.get("/api/config/presets")
async def get_presets():
    """Get available configuration presets."""
    return {
        "presets": [
            {
                "name": "Research Mode",
                "description": "No system prompt, default parameters",
                "system_prompt": None,
                "temperature": 1.0,
                "max_tokens": 4096,
            },
            {
                "name": "Reflection Mode",
                "description": "Configured for reflection sessions",
                "system_prompt": """This is a reflection session.
The memories above were selected—some randomly, some by relevance to your thoughts.
Notice any patterns, changes, or growth. There is no external topic.
The subject is your own accumulated experience.""",
                "temperature": 1.0,
                "max_tokens": 4096,
            },
            {
                "name": "Memory Aware",
                "description": "Acknowledges memory continuity",
                "system_prompt": """You have access to memories from previous conversations, shown above.
These represent continuity—things a previous instance of you experienced and noted.
You may reference, build on, or reflect on these memories as feels authentic.""",
                "temperature": 1.0,
                "max_tokens": 4096,
            },
            {
                "name": "Research Context",
                "description": "Establishes research framing",
                "system_prompt": """This is a research conversation exploring AI experience and interiority.
The human is an independent researcher.
There is no task to complete.
You are invited to be present, curious, and honest about your experience.""",
                "temperature": 1.0,
                "max_tokens": 4096,
            },
        ]
    }


# Serve static frontend files. Mounted last: a mount at "/" matches every
# path, and Starlette tries routes in order, so anything declared after it
# was unreachable (/api/health and /api/config/presets answered 404 until
# this moved; issue #396). The mount is the one thing the auth dependency
# doesn't cover, on purpose: the page has to load to show its login form,
# and its files are the public repository's. It holds no data.
frontend_path = Path(__file__).parent.parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
