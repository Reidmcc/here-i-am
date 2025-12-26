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
from app.services.github_service import GitHubService, github_service
from app.services.github_tools import register_github_tools
from app.services.notes_service import NotesService, notes_service
from app.services.notes_tools import register_notes_tools, set_current_entity_label
from app.services.memory_tools import register_memory_tools, set_memory_tool_context

# Register tools at module load time
register_web_tools(tool_service)
register_github_tools(tool_service)
register_notes_tools(tool_service)
register_memory_tools(tool_service)

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
    "GitHubService",
    "NotesService",
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
    "github_service",
    "notes_service",
    # Tool registration functions
    "register_web_tools",
    "register_github_tools",
    "register_notes_tools",
    "register_memory_tools",
    # Context helpers
    "set_current_entity_label",
    "set_memory_tool_context",
]
