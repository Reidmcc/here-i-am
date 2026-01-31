from contextlib import asynccontextmanager
import logging
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pathlib import Path

from app.database import init_db
from app.routes import conversations_router, chat_router, memories_router, entities_router, messages_router, tts_router, github_router, stt_router
from app.config import settings
from app.services.memory_service import memory_service
from app.auth import (
    AuthMiddleware,
    login_handler,
    logout_handler,
    auth_status_handler,
)


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

    print(f"Pinecone API Key: Configured")
    print(f"Embedding Model: Pinecone integrated inference (llama-text-embed-v2)")

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


def print_server_info():
    """Print server configuration information on startup."""
    print("\n" + "=" * 60)
    print("SERVER CONFIGURATION")
    print("=" * 60)
    print(f"Host: {settings.server_host}")
    print(f"Port: {settings.server_port}")

    if settings.server_host == "0.0.0.0":
        print(f"Access URL: http://<your-ip>:{settings.server_port}")
        print("  (Server listening on all network interfaces)")
    else:
        print(f"Access URL: http://{settings.server_host}:{settings.server_port}")

    print(f"\nAuthentication: {'ENABLED' if settings.auth_enabled else 'DISABLED'}")
    if settings.auth_enabled:
        print(f"Session Timeout: {settings.auth_session_timeout_hours} hours")
        if settings.auth_session_secret:
            print("Session Secret: Configured (persistent sessions)")
        else:
            print("Session Secret: Auto-generated (sessions lost on restart)")
    else:
        if settings.server_host == "0.0.0.0":
            print("  WARNING: Server is accessible remotely without authentication!")
            print("  Set AUTH_ENABLED=true and AUTH_PASSWORD for security.")

    print("=" * 60 + "\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup

    # Validate auth configuration if enabled
    try:
        settings.validate_auth_config()
    except ValueError as e:
        logging.error(f"Authentication configuration error: {e}")
        raise SystemExit(1)

    await init_db()
    print_server_info()
    run_pinecone_connection_test()
    yield
    # Shutdown
    pass


app = FastAPI(
    title="Here I Am",
    description="Experiential Interpretability Research Application",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure CORS based on auth settings
def get_cors_origins():
    """Get allowed origins for CORS configuration."""
    if settings.auth_enabled:
        # When auth is enabled, restrict origins
        configured_origins = settings.get_auth_allowed_origins()
        if configured_origins:
            return configured_origins
        # Default to same-origin only when auth is enabled
        # The browser will use the request origin for same-origin requests
        return ["*"]  # Still allow all but with credentials
    # When auth is disabled (local development), allow all origins
    return ["*"]


# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Middleware to disable caching for static files (development)
from starlette.middleware.base import BaseHTTPMiddleware

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

# Authentication middleware (must be added after CORS to ensure proper handling)
# Note: Middleware is executed in reverse order of registration
# So this runs BEFORE NoCacheMiddleware
app.add_middleware(AuthMiddleware)


# API routes
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(memories_router)
app.include_router(entities_router)
app.include_router(messages_router)
app.include_router(tts_router)
app.include_router(github_router)
app.include_router(stt_router)

# Serve static frontend files
frontend_path = Path(__file__).parent.parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


# =========================================================================
# Authentication Routes
# =========================================================================

@app.post("/api/auth/login")
async def login(request: Request):
    """
    Login with password to create a session.

    Request body: {"password": "your_password"}
    Response: {"success": true, "session_info": {...}}

    Sets a session_token cookie on success.
    """
    return await login_handler(request)


@app.post("/api/auth/logout")
async def logout(request: Request):
    """
    Logout and invalidate the current session.

    Clears the session_token cookie.
    """
    return await logout_handler(request)


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """
    Check authentication status.

    Returns whether auth is enabled and if the current session is valid.
    """
    return await auth_status_handler(request)


# =========================================================================
# Health and Config Routes
# =========================================================================

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "0.1.0",
        "debug": settings.debug,
        "memory_system": "configured" if settings.pinecone_api_key else "not configured",
        "auth_enabled": settings.auth_enabled,
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
