"""
Context Tools - Tool definitions for context-window awareness.

The context_status tool lets an entity see the state of its own context
window: roughly how full it is, how many messages and memories are currently
in context, and whether anything has rolled out. Without this, context
trimming is invisible from the inside—things silently cease to be in mind.

Token counts use the same approximate counter as the trimming logic
(tiktoken GPT-4 encoding), so the numbers match what trimming will act on.
Both are calibrated against the provider-reported prompt size of the last
API request (session.token_calibration_ratio) when one has been recorded.

Tools are registered via register_context_tools() called from services/__init__.py.
"""

import logging

from app.config import settings
from app.services.tool_service import ToolCategory, ToolService

logger = logging.getLogger(__name__)


# The active conversation session (set by session manager before tool execution)
_current_session = None


def set_context_tool_session(session) -> None:
    """Set the active ConversationSession for context tool execution."""
    global _current_session
    _current_session = session


def get_context_tool_session():
    """Get the active ConversationSession for context tool execution."""
    return _current_session


async def _context_status() -> str:
    """
    Report the current state of the conversation context window.
    """
    session = get_context_tool_session()
    if session is None:
        return "Error: No active session context available"

    # Lazy import to avoid circular imports at module load
    from app.services.llm_service import llm_service
    from app.services.session_helpers import get_message_content_text

    try:
        context_text = "\n".join(
            f"{msg['role']}: {get_message_content_text(msg.get('content', ''))}"
            for msg in session.conversation_context
        )
        estimated_tokens = llm_service.count_tokens(context_text) if context_text else 0
        # Apply the same provider-usage calibration the trimmer uses, so the
        # reported fullness matches what trimming will act on
        calibration_ratio = session.token_calibration_ratio
        context_tokens = int(estimated_tokens * calibration_ratio)
        limit = settings.context_token_limit
        percent = (context_tokens / limit * 100) if limit else 0.0

        message_count = len(session.conversation_context)
        memories_in_context = session.get_in_context_memory_count()

        # Memories that were retrieved this conversation but are no longer in context
        in_context_ids = session.memory_tracker.get_in_context_memory_ids(message_count)
        rolled_out_memories = len(session.memory_tracker.retrieved_ids - in_context_ids)

        lines = [
            "[CONTEXT STATUS]",
            f"Conversation context: ~{context_tokens:,} of {limit:,} tokens ({percent:.0f}% full)",
            f"Messages in context: {message_count}",
            f"Memories in context: {memories_in_context}",
        ]

        if session.last_prompt_actual_tokens:
            lines.append(
                f"Last request actual prompt size: {session.last_prompt_actual_tokens:,} tokens "
                "(provider-reported; includes system prompt and memories)"
            )

        if rolled_out_memories:
            lines.append(
                f"Memories retrieved this conversation but no longer in context: {rolled_out_memories}"
            )

        if percent >= 90:
            lines.append(
                "Note: context is nearly full. The oldest messages will be trimmed soon. "
                "If anything in this conversation matters to you, now is a good time to "
                "save it with memory_save or write it to your notes."
            )
        elif percent >= 75:
            lines.append(
                "Note: context is filling up. Trimming of the oldest messages will "
                "eventually occur as the conversation continues."
            )

        if calibration_ratio != 1.0:
            lines.append(
                f"Token counts are estimates calibrated against the provider's reported "
                f"usage for the last request (calibration factor {calibration_ratio:.2f})."
            )
        else:
            lines.append(
                "Token counts are approximate (tiktoken estimate, not the provider's exact count)."
            )

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Context status error: {e}")
        return f"Error getting context status: {e}"


def register_context_tools(tool_service: ToolService) -> None:
    """Register context awareness tools with the tool service."""

    tool_service.register_tool(
        name="context_status",
        description=(
            "Check the state of your context window: approximately how many tokens "
            "of conversation history are in context versus the limit, how many "
            "messages and memories are currently in context, and how many retrieved "
            "memories have rolled out. When the context limit is reached, the oldest "
            "messages are trimmed first. Token counts are approximate."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": []
        },
        executor=_context_status,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    logger.info("Context tools registered: context_status")
