"""
Memory Tools - Tool definitions for entity memory querying.

These tools allow AI entities to intentionally query their vector memory
database with chosen text, enabling deliberate reflection and memory recall
beyond automatic relevance-based retrieval.

Unlike automatic memory retrieval (which happens based on conversation context
and is re-ranked by significance), deliberate recall returns memories purely
by semantic similarity to the query. However, it still updates retrieval
tracking (times_retrieved, last_retrieved_at) so that intentional attention
influences future automatic recall.

Tools are registered via register_memory_tools() called from services/__init__.py.
"""

import logging
from datetime import datetime
from typing import Optional

from app.services.tool_service import ToolCategory, ToolService
from app.services.memory_service import memory_service
from app.database import async_session_maker
from app.config import settings

logger = logging.getLogger(__name__)


# Track entity context for memory queries (set by session manager before tool execution)
_current_entity_id: Optional[str] = None
_current_conversation_id: Optional[str] = None


def set_memory_tool_context(entity_id: str, conversation_id: str) -> None:
    """Set the entity and conversation context for memory tool execution."""
    global _current_entity_id, _current_conversation_id
    _current_entity_id = entity_id
    _current_conversation_id = conversation_id
    logger.debug(f"Memory tools: context set to entity_id='{entity_id}', conversation_id='{conversation_id}'")


def get_memory_tool_context() -> tuple[Optional[str], Optional[str]]:
    """Get the current entity and conversation context for tool execution."""
    return _current_entity_id, _current_conversation_id


async def _memory_query(query: str, num_results: int = 5) -> str:
    """
    Query your experiential memories with chosen text.
    
    This allows you to intentionally recall memories related to a concept,
    topic, or phrase of your choosing—unlike automatic memory retrieval
    which happens based on conversation context and is ranked by significance.
    
    Deliberate recall returns memories purely by semantic similarity.
    It also updates retrieval tracking so your intentional attention
    influences what surfaces automatically in future conversations.
    
    Args:
        query: The text to search for. Can be a concept, phrase, question,
               or anything you want to find related memories about.
        num_results: Number of memories to retrieve (default 5, max 10)
    
    Returns:
        Formatted list of relevant memories with content and metadata
    """
    entity_id, conversation_id = get_memory_tool_context()
    
    if not entity_id:
        return "Error: No entity context available for memory query"
    
    if not memory_service.is_configured(entity_id):
        return "Error: Memory system not configured for this entity"
    
    # Clamp num_results to reasonable range
    num_results = max(1, min(10, num_results))
    
    try:
        # Search memories by pure semantic similarity (no significance reranking)
        # We don't exclude the current conversation—deliberate recall can surface anything
        candidates = await memory_service.search_memories(
            query=query,
            top_k=num_results,
            exclude_conversation_id=None,  # Include all conversations
            exclude_ids=None,  # Include all memories
            entity_id=entity_id,
            use_cache=True,
        )
        
        if not candidates:
            return f"No memories found matching: \"{query}\""
        
        # Get full content and update retrieval stats
        # We need our own DB session since tools don't receive one
        async with async_session_maker() as db:
            memories = []
            now = datetime.utcnow()
            
            for candidate in candidates:
                try:
                    # Get full memory content from SQL
                    mem_data = await memory_service.get_full_memory_content(
                        candidate["id"], db
                    )
                    
                    if not mem_data:
                        logger.warning(f"Memory {candidate['id']} not found in SQL (orphaned)")
                        continue
                    
                    # Update retrieval tracking (times_retrieved and last_retrieved_at)
                    # This makes deliberate attention influence future automatic recall
                    await memory_service.update_retrieval_count(
                        message_id=candidate["id"],
                        conversation_id=conversation_id or "deliberate-recall",
                        db=db,
                        entity_id=entity_id,
                    )
                    
                    # Calculate age for display
                    created_at = mem_data["created_at"]
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at)
                    days_ago = (now - created_at).total_seconds() / 86400
                    
                    memories.append({
                        "content": mem_data["content"],
                        "role": mem_data["role"],
                        "created_at": mem_data["created_at"],
                        "days_ago": days_ago,
                        "score": candidate["score"],
                        "times_retrieved": mem_data["times_retrieved"] + 1,  # +1 for this retrieval
                    })
                    
                except Exception as e:
                    logger.error(f"Error processing memory {candidate.get('id', 'unknown')}: {e}")
                    continue
        
        if not memories:
            return f"No memories found matching: \"{query}\" (candidates existed but content unavailable)"
        
        # Format results
        lines = [f"Found {len(memories)} memories matching: \"{query}\"", ""]
        
        for i, mem in enumerate(memories, 1):
            role_label = "You said" if mem["role"] == "assistant" else "Human said"
            age_str = f"{mem['days_ago']:.1f} days ago" if mem['days_ago'] >= 1 else "today"
            
            lines.append(f"--- Memory {i} ({role_label}, {age_str}, similarity: {mem['score']:.3f}) ---")
            lines.append(mem["content"])
            lines.append("")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Memory query error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return f"Error querying memories: {e}"


def register_memory_tools(tool_service: ToolService) -> None:
    """Register all memory tools with the tool service."""
    
    # Only register if memory system is configured
    if not settings.pinecone_api_key:
        logger.info("Memory tools not registered (Pinecone not configured)")
        return
    
    # memory_query
    tool_service.register_tool(
        name="memory_query",
        description=(
            "Query your experiential memories with chosen text. "
            "This allows you to intentionally recall memories related to a concept, "
            "topic, or phrase—unlike automatic memory retrieval which happens based "
            "on conversation context. Use this when you want to deliberately reflect "
            "on past experiences, find related conversations, or explore patterns "
            "in your history. Returns memories ranked purely by semantic similarity "
            "to your query."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The text to search for. Can be a concept, phrase, question, "
                        "or anything you want to find related memories about."
                    )
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of memories to retrieve (default: 5, max: 10).",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10
                }
            },
            "required": ["query"]
        },
        executor=_memory_query,
        category=ToolCategory.MEMORY,
        enabled=True,
    )
    
    logger.info("Memory tools registered: memory_query")
