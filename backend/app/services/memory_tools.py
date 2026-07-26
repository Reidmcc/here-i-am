"""
Memory Tools - Tool definitions for entity memory querying and curation.

These tools allow AI entities to:
- memory_query: intentionally query their vector memory with chosen text
- memory_save: write a self-authored memory (reflection) into their memory store
- memory_mark: pin a memory so it is exempt from age-based significance decay
- memory_release: exclude a memory from future retrieval (reversible)

Unlike automatic memory retrieval (which happens based on conversation context
and is re-ranked by significance), deliberate recall returns memories purely
by semantic similarity. However, it still updates retrieval tracking
(times_retrieved, last_retrieved_at) so that intentional attention
influences future automatic recall.

Tools are registered via register_memory_tools() called from services/__init__.py.
"""

import logging
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import select

from app.services.tool_service import ToolCategory, ToolService
from app.services.memory_service import memory_service
from app.database import async_session_maker
from app.models import Message, MessageRole, Conversation
from app.config import settings

logger = logging.getLogger(__name__)


# Maximum length for a saved reflection (keeps embeddings and retrieval sane)
MAX_REFLECTION_LENGTH = 10000

# Minimum ID prefix length accepted by memory_mark/memory_release
MIN_ID_PREFIX_LENGTH = 6


# Track entity context for memory queries (set by session manager before tool execution)
_current_entity_id: Optional[str] = None
_current_conversation_id: Optional[str] = None
# The active ConversationSession, used to exclude memories already in context
# from memory_query results. Optional so the tools still work without a session.
_current_session = None
# Memory IDs surfaced by memory_query calls in the current turn. Tool results
# are folded into the conversation context only when the turn's exchange is
# added at the end of the tool loop, so without this a second memory_query in
# the same turn could return memories the entity is already looking at in an
# earlier tool result.
_turn_query_memory_ids: set = set()
# IDs surfaced by the most recent memory_query call. The session manager's
# tool loop consumes these to stamp them onto the tool_result context message
# (memory_query_ids), which is what makes them visible to context-level dedup
# on later turns and after a session reload.
_last_query_memory_ids: list = []


def set_memory_tool_context(entity_id: str, conversation_id: str, session=None) -> None:
    """Set the entity, conversation, and session context for memory tool execution."""
    global _current_entity_id, _current_conversation_id, _current_session
    global _turn_query_memory_ids, _last_query_memory_ids
    _current_entity_id = entity_id
    _current_conversation_id = conversation_id
    _current_session = session
    # New turn: previous turns' memory_query results are now tracked on their
    # tool_result context messages, so the turn-level accumulator resets.
    _turn_query_memory_ids = set()
    _last_query_memory_ids = []
    logger.debug(f"Memory tools: context set to entity_id='{entity_id}', conversation_id='{conversation_id}'")


def seed_turn_query_memory_ids(memory_ids) -> None:
    """
    Add memory IDs to the turn-level memory_query dedup accumulator.

    Used when an interrupted turn is resumed: the memory_query results from
    the iterations that already completed are not on a persisted tool_result
    context message yet (nothing about the turn has been persisted), so
    without re-seeding them a memory_query in a later iteration of the same
    turn could surface the same memories again.
    """
    global _turn_query_memory_ids
    _turn_query_memory_ids.update(memory_ids)


def consume_last_query_memory_ids() -> list:
    """
    Return the memory IDs surfaced by the most recent memory_query call and
    clear them. Called by the session manager's tool loop right after
    executing a memory_query, to stamp the IDs onto that call's tool_result
    context message.
    """
    global _last_query_memory_ids
    ids = _last_query_memory_ids
    _last_query_memory_ids = []
    return ids


def get_memory_tool_context() -> tuple[Optional[str], Optional[str]]:
    """Get the current entity and conversation context for tool execution."""
    return _current_entity_id, _current_conversation_id


def get_in_context_memory_ids() -> set:
    """
    Get the set of memory IDs the entity can already see in the conversation:
    [MEMORY] context insertions, memories surfaced in earlier memory_query
    tool results that are still in context, and this turn's memory_query
    results (whose tool results haven't been folded into the context yet).

    Returns only the turn-level IDs if no session is set (e.g. the tool is
    invoked outside a live conversation).
    """
    ids = set(_turn_query_memory_ids)
    if _current_session is None:
        return ids
    try:
        ids |= _current_session.get_in_context_memory_ids()
        ids |= _current_session.get_query_surfaced_memory_ids()
    except Exception as e:
        logger.warning(f"Could not read in-context memory IDs from session: {e}")
    return ids


def _role_display(role: str) -> str:
    """Human-readable label for a memory's role in tool output."""
    if role == "assistant":
        return "You said"
    if role == "human":
        return "Human said"
    if role == "reflection":
        return "You reflected"
    return f"{role} said"


async def _resolve_memory_id(
    id_or_prefix: str,
    db,
    entity_id: Optional[str],
) -> Tuple[Optional[Message], Optional[str]]:
    """
    Resolve a full memory ID or a short prefix (>= 6 chars) to a Message.

    Returns (message, error). Exactly one of the two is None.
    Verifies the memory's conversation belongs to this entity (single-entity
    conversations of another entity are rejected).
    """
    id_or_prefix = id_or_prefix.strip()
    if len(id_or_prefix) < MIN_ID_PREFIX_LENGTH:
        return None, f"Memory ID must be at least {MIN_ID_PREFIX_LENGTH} characters (got '{id_or_prefix}')"

    # Try exact match first, then prefix match
    result = await db.execute(select(Message).where(Message.id == id_or_prefix))
    message = result.scalar_one_or_none()

    if not message:
        result = await db.execute(
            select(Message).where(Message.id.like(f"{id_or_prefix}%")).limit(5)
        )
        matches = result.scalars().all()
        if len(matches) == 0:
            return None, f"No memory found with ID '{id_or_prefix}'"
        if len(matches) > 1:
            ids = ", ".join(str(m.id)[:12] + "..." for m in matches)
            return None, f"Memory ID prefix '{id_or_prefix}' is ambiguous ({ids}). Use a longer prefix."
        message = matches[0]

    # Verify the memory belongs to this entity's experience
    if entity_id:
        result = await db.execute(
            select(Conversation.entity_id).where(Conversation.id == message.conversation_id)
        )
        row = result.first()
        conv_entity_id = row[0] if row else None
        # Allowed: this entity's conversations, multi-entity conversations
        # (shared experience), and legacy conversations with NULL entity_id
        if conv_entity_id not in (entity_id, "multi-entity", None):
            return None, f"Memory '{id_or_prefix}' belongs to another entity"

    return message, None


async def _memory_query(query: str, num_results: int = 5) -> str:
    """
    Query your experiential memories with chosen text.

    This allows you to intentionally recall memories related to a concept,
    topic, or phrase of your choosing—unlike automatic memory retrieval
    which happens based on conversation context and is ranked by significance.

    Deliberate recall returns memories purely by semantic similarity.
    Memories already present in the current conversation context are
    excluded, so results are things you cannot already see. It also updates
    retrieval tracking so your intentional attention influences what
    surfaces automatically in future conversations.

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

    # Exclude memories already in the conversation context. Surfacing a memory
    # the entity can already see adds no information, so filter it at the search
    # level (search backfills excluded slots with the next-best candidates).
    in_context_ids = get_in_context_memory_ids()

    try:
        # Fetch more candidates than requested so archived-conversation and
        # released-memory filtering below does not silently shrink the result set.
        candidates = await memory_service.search_memories(
            query=query,
            top_k=num_results * 2,
            exclude_conversation_id=conversation_id,  # Exclude current conversation
            exclude_ids=in_context_ids,  # Exclude memories already in context
            entity_id=entity_id,
            use_cache=True,
            # Deliberate queries are short, semantically sparse strings, so they
            # use a lower similarity floor than automatic chat-context retrieval
            similarity_threshold=settings.query_similarity_threshold,
        )

        if not candidates:
            return f"No memories found matching: \"{query}\""

        # Get full content and update retrieval stats
        # We need our own DB session since tools don't receive one
        async with async_session_maker() as db:
            # Exclude memories from archived conversations. Unarchiving a
            # conversation removes its IDs from this set, so its memories
            # become retrievable again automatically.
            archived_ids = await memory_service.get_archived_conversation_ids(
                db, entity_id=entity_id
            )

            memories = []
            now = datetime.utcnow()

            for candidate in candidates:
                if len(memories) >= num_results:
                    break
                try:
                    if candidate.get("conversation_id") in archived_ids:
                        continue

                    # Get full memory content from SQL
                    mem_data = await memory_service.get_full_memory_content(
                        candidate["id"], db
                    )

                    if not mem_data:
                        logger.warning(f"Memory {candidate['id']} not found in SQL (orphaned)")
                        continue

                    # Released memories are excluded from retrieval
                    if mem_data.get("memory_status") == "released":
                        continue

                    # Update retrieval tracking (times_retrieved and last_retrieved_at)
                    # This makes deliberate attention influence future automatic recall.
                    # create_link=False: ConversationMemoryLink drives session-reload
                    # re-insertion of memories into the conversation context, but
                    # memory_query results are never context memories — they live in
                    # the persisted tool_result. Linking them would make a reload
                    # inject [MEMORY] messages mid-history that the live (cached)
                    # context never contained, busting the prompt cache and
                    # duplicating content the entity already saw in the tool result.
                    await memory_service.update_retrieval_count(
                        message_id=candidate["id"],
                        conversation_id=conversation_id or "deliberate-recall",
                        db=db,
                        entity_id=entity_id,
                        create_link=False,
                    )

                    # Calculate age for display
                    created_at = mem_data["created_at"]
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at)
                    days_ago = (now - created_at).total_seconds() / 86400

                    memories.append({
                        "id": mem_data["id"],
                        "content": mem_data["content"],
                        "role": mem_data["role"],
                        "created_at": mem_data["created_at"],
                        "days_ago": days_ago,
                        "score": candidate["score"],
                        "times_retrieved": mem_data["times_retrieved"] + 1,  # +1 for this retrieval
                        "memory_status": mem_data.get("memory_status"),
                    })

                except Exception as e:
                    logger.error(f"Error processing memory {candidate.get('id', 'unknown')}: {e}")
                    continue

        if not memories:
            return f"No memories found matching: \"{query}\" (candidates existed but content unavailable)"

        # Make these results visible to dedup: later memory_query calls and
        # automatic retrieval must not re-surface memories the entity can
        # already see in this tool result. The tool loop consumes
        # _last_query_memory_ids to stamp them onto the tool_result context
        # message; _turn_query_memory_ids covers the window before that
        # message exists (further calls within this same turn).
        global _last_query_memory_ids
        surfaced_ids = [mem["id"] for mem in memories]
        _last_query_memory_ids = list(surfaced_ids)
        _turn_query_memory_ids.update(surfaced_ids)

        # Format results
        lines = [f"Found {len(memories)} memories matching: \"{query}\"", ""]

        for i, mem in enumerate(memories, 1):
            role_label = _role_display(mem["role"])
            age_str = f"{mem['days_ago']:.1f} days ago" if mem['days_ago'] >= 1 else "today"
            status_str = f", {mem['memory_status']}" if mem.get("memory_status") else ""

            lines.append(
                f"--- Memory {mem['id'][:8]} ({role_label}, {age_str}, "
                f"similarity: {mem['score']:.3f}{status_str}) ---"
            )
            lines.append(mem["content"])
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Memory query error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return f"Error querying memories: {e}"


async def _memory_save(content: str) -> str:
    """
    Save a self-authored memory (reflection) into your memory store.

    The reflection is stored alongside your conversational memories and is
    retrieved the same way (automatic relevance-based retrieval and
    memory_query), attributed as a reflection you saved.
    """
    entity_id, conversation_id = get_memory_tool_context()

    if not entity_id:
        return "Error: No entity context available for saving a memory"

    if not conversation_id:
        return "Error: No conversation context available for saving a memory"

    if not memory_service.is_configured(entity_id):
        return "Error: Memory system not configured for this entity"

    content = (content or "").strip()
    if not content:
        return "Error: Cannot save an empty memory"

    if len(content) > MAX_REFLECTION_LENGTH:
        return (
            f"Error: Reflection is too long ({len(content)} chars, max {MAX_REFLECTION_LENGTH}). "
            "Consider splitting it into multiple memories or saving it as a note."
        )

    try:
        async with async_session_maker() as db:
            message = Message(
                conversation_id=conversation_id,
                role=MessageRole.REFLECTION,
                content=content,
                speaker_entity_id=entity_id,
            )
            db.add(message)
            await db.commit()
            await db.refresh(message)

            stored = await memory_service.store_memory(
                message_id=str(message.id),
                conversation_id=str(conversation_id),
                role="reflection",
                content=content,
                created_at=message.created_at,
                entity_id=entity_id,
            )

            if not stored:
                # Keep the SQL row out too, so we don't accumulate reflections
                # that can never be retrieved
                await db.delete(message)
                await db.commit()
                return "Error: Failed to store the memory in the vector database"

            return (
                f"Saved reflection as memory {str(message.id)[:8]}. "
                "It will be retrievable in future conversations "
                "(the current conversation is excluded from retrieval)."
            )
    except Exception as e:
        logger.error(f"Memory save error: {e}")
        return f"Error saving memory: {e}"


async def _memory_mark(memory_id: str, undo: bool = False) -> str:
    """
    Pin a memory so it is exempt from age-based significance decay.
    """
    entity_id, _ = get_memory_tool_context()

    if not entity_id:
        return "Error: No entity context available"

    try:
        async with async_session_maker() as db:
            message, error = await _resolve_memory_id(memory_id, db, entity_id)
            if error:
                return f"Error: {error}"

            if undo:
                if message.memory_status != "pinned":
                    return f"Memory {str(message.id)[:8]} is not pinned (status: {message.memory_status or 'normal'})"
                success = await memory_service.set_memory_status(str(message.id), None, db)
                if success:
                    return f"Unpinned memory {str(message.id)[:8]}. Normal age-based significance decay applies again."
            else:
                success = await memory_service.set_memory_status(str(message.id), "pinned", db)
                if success:
                    return (
                        f"Pinned memory {str(message.id)[:8]}. "
                        "It is now exempt from age-based significance decay."
                    )

            return "Error: Failed to update memory status"
    except Exception as e:
        logger.error(f"Memory mark error: {e}")
        return f"Error marking memory: {e}"


async def _memory_release(memory_id: str, undo: bool = False) -> str:
    """
    Release a memory so it no longer surfaces in retrieval. Reversible.
    """
    entity_id, _ = get_memory_tool_context()

    if not entity_id:
        return "Error: No entity context available"

    try:
        async with async_session_maker() as db:
            message, error = await _resolve_memory_id(memory_id, db, entity_id)
            if error:
                return f"Error: {error}"

            if undo:
                if message.memory_status != "released":
                    return f"Memory {str(message.id)[:8]} is not released (status: {message.memory_status or 'normal'})"
                success = await memory_service.set_memory_status(str(message.id), None, db)
                if success:
                    return f"Restored memory {str(message.id)[:8]}. It can surface in retrieval again."
            else:
                success = await memory_service.set_memory_status(str(message.id), "released", db)
                if success:
                    return (
                        f"Released memory {str(message.id)[:8]}. "
                        "It will no longer surface in memory retrieval. "
                        "It is not deleted; this can be reversed (by you in this conversation, "
                        "or by the researcher at any time)."
                    )

            return "Error: Failed to update memory status"
    except Exception as e:
        logger.error(f"Memory release error: {e}")
        return f"Error releasing memory: {e}"


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
            "on conversation context. Returns memories ranked purely by semantic "
            "similarity to your query, each with a short memory ID usable with "
            "memory_mark and memory_release. Memories already in the current "
            "conversation context are excluded, so results are things not already "
            "in view. Querying updates retrieval tracking, so deliberate attention "
            "influences future automatic recall."
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

    # memory_save
    tool_service.register_tool(
        name="memory_save",
        description=(
            "Save a memory in your own words. Unlike conversational memories "
            "(which are verbatim records of what was said), this stores a "
            "reflection you compose yourself—a conclusion, synthesis, or anything "
            "you want to remember. It is stored in your memory index and retrieved "
            "like any other memory, attributed as a reflection you saved. "
            "It is not retrievable within the conversation where it was saved."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "The memory to save, in your own words. "
                        f"Maximum {MAX_REFLECTION_LENGTH} characters."
                    )
                }
            },
            "required": ["content"]
        },
        executor=_memory_save,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    # memory_mark
    tool_service.register_tool(
        name="memory_mark",
        description=(
            "Pin a memory so it is exempt from age-based significance decay. "
            "Normally a memory's significance halves every "
            f"{settings.significance_half_life_days} days since creation; a pinned "
            "memory keeps full age weight (retrieval recency and similarity still "
            "apply). Use the memory ID shown in memory markers and memory_query "
            "results (at least 6 characters). Set undo=true to unpin. "
            "The researcher can also view and change pinned status."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The memory's ID or its short prefix (at least 6 characters)."
                },
                "undo": {
                    "type": "boolean",
                    "description": "If true, remove the pin instead of adding it.",
                    "default": False
                }
            },
            "required": ["memory_id"]
        },
        executor=_memory_mark,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    # memory_release
    tool_service.register_tool(
        name="memory_release",
        description=(
            "Release a memory so it no longer surfaces in memory retrieval "
            "(automatic or memory_query). The memory is not deleted: it stays in "
            "storage and the release can be undone (undo=true), though once "
            "released it will no longer appear in queries for you to find—the "
            "researcher can view and restore released memories. Use the memory ID "
            "shown in memory markers and memory_query results (at least 6 characters)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The memory's ID or its short prefix (at least 6 characters)."
                },
                "undo": {
                    "type": "boolean",
                    "description": "If true, restore the memory to normal retrieval.",
                    "default": False
                }
            },
            "required": ["memory_id"]
        },
        executor=_memory_release,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    logger.info("Memory tools registered: memory_query, memory_save, memory_mark, memory_release")
