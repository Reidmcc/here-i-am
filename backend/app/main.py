from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.database import init_db
from app.routes import conversations_router, chat_router, memories_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    yield
    # Shutdown
    pass


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

# API routes
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(memories_router)

# Serve static frontend files
frontend_path = Path(__file__).parent.parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "0.1.0",
        "debug": settings.debug,
        "memory_system": "configured" if settings.pinecone_api_key else "not configured",
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
