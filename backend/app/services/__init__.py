from app.services.anthropic_service import AnthropicService, anthropic_service
from app.services.openai_service import OpenAIService, openai_service
from app.services.google_service import GoogleService, google_service
from app.services.llm_service import LLMService, llm_service
from app.services.memory_service import MemoryService, memory_service
from app.services.session_manager import ConversationSession, SessionManager, session_manager
from app.services.cache_service import CacheService, TTLCache, cache_service
from app.services.tts_service import TTSService, tts_service
from app.services.xtts_service import XTTSService, xtts_service
from app.services.tool_service import ToolService, ToolCategory, ToolResult, tool_service
from app.services.web_tools import register_web_tools

# Register web tools at module load time
register_web_tools(tool_service)

__all__ = [
    # Classes
    "AnthropicService",
    "OpenAIService",
    "GoogleService",
    "LLMService",
    "MemoryService",
    "ConversationSession",
    "SessionManager",
    "CacheService",
    "TTLCache",
    "TTSService",
    "XTTSService",
    "ToolService",
    "ToolCategory",
    "ToolResult",
    # Singleton instances
    "anthropic_service",
    "openai_service",
    "google_service",
    "llm_service",
    "memory_service",
    "session_manager",
    "cache_service",
    "tts_service",
    "xtts_service",
    "tool_service",
    # Tool registration functions
    "register_web_tools",
]
