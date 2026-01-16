from contextlib import asynccontextmanager
import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.database import init_db
from app.routes import conversations_router, chat_router, memories_router, entities_router, messages_router, tts_router, github_router, stt_router, games_router
from app.config import settings
from app.services.memory_service import memory_service
from app.services.event_service import event_service


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


async def initialize_event_listeners():
    """Initialize and start event listeners based on configuration."""
    # Import here to avoid circular imports
    from app.config import settings

    # OGS listener will be registered here when OGS is configured
    if settings.ogs_enabled:
        try:
            from app.services.ogs_service import ogs_service
            from app.services.event_listeners.ogs_listener import OGSEventListener

            # Create and register OGS listener
            ogs_listener = OGSEventListener(
                entity_id=settings.ogs_entity_id,
                ogs_service=ogs_service
            )
            event_service.register_listener("ogs", ogs_listener)

            # Register OGS event handler
            event_service.register_event_handler("ogs", ogs_service)

            logging.getLogger(__name__).info("OGS event listener registered")
        except ImportError as e:
            logging.getLogger(__name__).warning(f"OGS integration not available: {e}")
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to initialize OGS listener: {e}")


def run_event_service_status():
    """Print event service status to terminal."""
    print("\n" + "=" * 60)
    print("EVENT SERVICE STATUS")
    print("=" * 60)

    status = event_service.get_status()

    if not status["listeners"]:
        print("Status: NO LISTENERS CONFIGURED")
        print("No external event listeners are active.")
    else:
        print(f"Status: {'RUNNING' if status['running'] else 'STOPPED'}")
        print(f"Listeners: {len(status['listeners'])}")
        print("-" * 60)

        for listener_id, listener_info in status["listeners"].items():
            print(f"\n  {listener_id}:")
            print(f"    Entity: {listener_info['entity_id']}")
            print(f"    State: {listener_info['state']}")
            if listener_info.get('connected_at'):
                print(f"    Connected: {listener_info['connected_at']}")
            if listener_info.get('last_error'):
                print(f"    Last Error: {listener_info['last_error']}")

    print("=" * 60 + "\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    run_pinecone_connection_test()

    # Initialize and start event listeners
    await initialize_event_listeners()
    if event_service.get_all_listeners():
        await event_service.start()
        run_event_service_status()

    yield

    # Shutdown
    if event_service.is_running:
        await event_service.stop()


app = FastAPI(
    title="Here I Am",
    description="Experiential Interpretability Research Application",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Middleware to disable caching for static files (development)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

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
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(memories_router)
app.include_router(entities_router)
app.include_router(messages_router)
app.include_router(tts_router)
app.include_router(github_router)
app.include_router(stt_router)
app.include_router(games_router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "0.1.0",
        "debug": settings.debug,
        "memory_system": "configured" if settings.pinecone_api_key else "not configured",
    }


@app.get("/api/events/status")
async def events_status():
    """Get the status of the external event system."""
    return event_service.get_status()


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


# Serve static frontend files (must be after all API routes)
frontend_path = Path(__file__).parent.parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
