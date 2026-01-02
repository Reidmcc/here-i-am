from app.routes.conversations import router as conversations_router
from app.routes.chat import router as chat_router
from app.routes.memories import router as memories_router
from app.routes.entities import router as entities_router
from app.routes.messages import router as messages_router
from app.routes.tts import router as tts_router
from app.routes.github import router as github_router
from app.routes.stt import router as stt_router

__all__ = ["conversations_router", "chat_router", "memories_router", "entities_router", "messages_router", "tts_router", "github_router", "stt_router"]
