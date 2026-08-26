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

The tool implementations take an explicit MemoryToolContext, so they serve
two callers:
- The native tool loop registers thin wrappers (register_memory_tools) that
  read a module-level current context, set per turn via
  set_memory_tool_context — one live session drives one turn at a time.
- Claude Code mode's MCP endpoint builds a fresh context per request
  (services/claude_code_mcp.py), where concurrent calls with different
  conversations are possible and module globals would race.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select

from app.config import settings
from app.database import async_session_maker
from app.models import Conversation, Message, MessageRole
from app.services.memory_context import format_memory_origin
from app.services.memory_service import VALID_ROLE_FILTERS, memory_service
from app.services.tool_service import ToolCategory, ToolService

logger = logging.getLogger(__name__)


# Maximum length for a saved reflection (keeps embeddings and retrieval sane)
MAX_REFLECTION_LENGTH = 10000

# Minimum ID prefix length accepted by memory_mark/memory_release
MIN_ID_PREFIX_LENGTH = 6

# Accepted values for memory_query's `source` parameter. "all" (or omitting
# the parameter) searches every memory; "human", "ai", and "reflection" map
# to the role filter memory_service applies to the vector search.
SOURCE_ALL = "all"
SOURCE_REFLECTION = "reflection"
VALID_QUERY_SOURCES = (SOURCE_ALL,) + tuple(VALID_ROLE_FILTERS)

# Accepted values for memory_query's `mode` parameter. "semantic" searches by
# similarity to the query text; "recent" returns the entity's own reflections
# purely by creation time (no vector search, no query text needed) — the
# catch-up channel for reflections saved by other sessions running in
# parallel or since this conversation began.
MODE_SEMANTIC = "semantic"
MODE_RECENT = "recent"
VALID_QUERY_MODES = (MODE_SEMANTIC, MODE_RECENT)


@dataclass
class MemoryToolContext:
    """
    Per-request execution context for the memory tools.

    Attributes:
        entity_id: Pinecone index name of the entity acting.
        conversation_id: The conversation the tool call belongs to (excluded
            from query results; reflections are saved onto it).
        session: The live ConversationSession, if any — used to exclude
            memories already visible in the native conversation context.
        turn_query_memory_ids: Memory IDs surfaced by memory_query calls in
            the current turn. Tool results are folded into the conversation
            context only when the turn's exchange is added at the end of the
            tool loop, so without this a second memory_query in the same turn
            could return memories the entity is already looking at in an
            earlier tool result.
        last_query_memory_ids: IDs surfaced by the most recent memory_query
            call. The session manager's tool loop consumes these to stamp
            them onto the tool_result context message (memory_query_ids),
            which is what makes them visible to context-level dedup on later
            turns and after a session reload.
        extra_exclude_ids: Additional memory IDs to exclude from query
            results. Claude Code mode passes the conversation's
            ConversationMemoryLink set here (its equivalent of "already in
            context").
        exclude_conversation_after: For compacted Claude Code conversations,
            the conversation's last_compacted_at. Narrows the
            same-conversation exclusion to memories created at or after that
            moment: everything before it survives in context only as a
            paraphrased summary, so it is eligible for retrieval again.
            None (always, for native conversations) keeps the exclusion
            unconditional.
        link_query_results: Record a ConversationMemoryLink for each query
            result. False for native conversations — links drive
            session-reload re-insertion of memories into the rebuilt context,
            so linking query results would inject them mid-history and bust
            the prompt cache. True for Claude Code conversations, which are
            never rebuilt: there the link is purely the dedup record that
            keeps automatic retrieval and later queries from re-surfacing
            what a query already showed.
    """
    entity_id: Optional[str] = None
    conversation_id: Optional[str] = None
    session: Any = None
    turn_query_memory_ids: Set[str] = field(default_factory=set)
    last_query_memory_ids: List[str] = field(default_factory=list)
    extra_exclude_ids: Set[str] = field(default_factory=set)
    link_query_results: bool = False
    exclude_conversation_after: Optional[datetime] = None


# Current context for the native tool loop (set by the session manager before
# tool execution; one live session drives one turn at a time)
_context = MemoryToolContext()


def set_memory_tool_context(entity_id: str, conversation_id: str, session=None) -> None:
    """Set the entity, conversation, and session context for memory tool execution."""
    global _context
    # New turn: previous turns' memory_query results are now tracked on their
    # tool_result context messages, so the turn-level accumulator resets
    # (a fresh context starts with empty accumulators).
    _context = MemoryToolContext(
        entity_id=entity_id,
        conversation_id=conversation_id,
        session=session,
    )
    logger.debug(f"Memory tools: context set to entity_id='{entity_id}', conversation_id='{conversation_id}'")


def consume_last_query_memory_ids() -> list:
    """
    Return the memory IDs surfaced by the most recent memory_query call and
    clear them. Called by the session manager's tool loop right after
    executing a memory_query, to stamp the IDs onto that call's tool_result
    context message.
    """
    ids = _context.last_query_memory_ids
    _context.last_query_memory_ids = []
    return ids


def get_memory_tool_context() -> tuple[Optional[str], Optional[str]]:
    """Get the current entity and conversation context for tool execution."""
    return _context.entity_id, _context.conversation_id


def get_in_context_memory_ids(ctx: Optional[MemoryToolContext] = None) -> set:
    """
    Get the set of memory IDs the entity can already see in the conversation:
    [MEMORY] context insertions, memories surfaced in earlier memory_query
    tool results that are still in context, this turn's memory_query results
    (whose tool results haven't been folded into the context yet), and any
    caller-supplied extra exclusions (Claude Code mode's link set).

    Uses the native tool loop's current context when none is passed.
    """
    if ctx is None:
        ctx = _context
    ids = set(ctx.turn_query_memory_ids) | set(ctx.extra_exclude_ids)
    if ctx.session is None:
        return ids
    try:
        ids |= ctx.session.get_in_context_memory_ids()
        ids |= ctx.session.get_query_surfaced_memory_ids()
    except Exception as e:
        logger.warning(f"Could not read in-context memory IDs from session: {e}")
    return ids


def _role_display(role: str, sibling_session: Optional[str] = None) -> str:
    """Human-readable label for a memory's role in tool output.

    sibling_session marks an inter-session message recorded in a Claude Code
    conversation: the entity's own words, sent from the named sibling
    session."""
    if sibling_session:
        return f'You said (inter-session message from "{sibling_session}")'
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


def _parse_since(since: Optional[str]) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Parse memory_query's `since` parameter into a naive-UTC datetime.

    Memory timestamps are stored naive UTC, so an aware input is converted
    to UTC and stripped. Returns (datetime, error) — at most one is set.
    """
    if since is None or not str(since).strip():
        return None, None
    try:
        parsed = datetime.fromisoformat(str(since).strip())
    except ValueError:
        return None, (
            f"Error: Could not parse since='{since}'. Use ISO 8601, e.g. "
            "'2026-08-24' or '2026-08-24T18:00:00' (UTC assumed when no "
            "timezone is given)."
        )
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed, None


def _format_recent_reflections(
    memories: List[Dict[str, Any]], since_suffix: str
) -> str:
    """Render recent-mode results (no similarity scores — ordering is time)."""
    now = datetime.utcnow()
    lines = [f"Your {len(memories)} most recent reflections{since_suffix}, newest first:", ""]
    for mem in memories:
        created_at = mem["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        days_ago = (now - created_at).total_seconds() / 86400
        age_str = f"{days_ago:.1f} days ago" if days_ago >= 1 else "today"
        status_str = f", {mem['memory_status']}" if mem.get("memory_status") else ""
        origin_str = format_memory_origin(mem.get("source", "native"))
        lines.append(
            f"--- Memory {mem['id'][:8]} (You reflected, {age_str}, {origin_str}{status_str}) ---"
        )
        lines.append(mem["content"])
        lines.append("")
    return "\n".join(lines)


async def _recent_reflections(
    ctx: MemoryToolContext,
    num_results: int,
    since: Optional[datetime],
    since_suffix: str,
) -> str:
    """
    memory_query's recent mode: the entity's own reflections by creation
    time. No vector search runs and — matching the recency-injection rule —
    times_retrieved / last_retrieved_at are NOT updated: significance
    feedback stays reserved for semantic recall, so asking "what did I save
    lately" doesn't inflate what it returns. In Claude Code conversations
    the results are still linked (the dedup record that keeps automatic
    retrieval and later queries from re-surfacing them).
    """
    in_context_ids = get_in_context_memory_ids(ctx)
    async with async_session_maker() as db:
        memories = await memory_service.get_recent_reflections(
            db,
            entity_id=ctx.entity_id,
            limit=num_results,
            exclude_conversation_id=ctx.conversation_id,
            exclude_ids=in_context_ids,
            since=since,
            exclude_conversation_after=ctx.exclude_conversation_after,
        )
        if ctx.link_query_results:
            for mem in memories:
                await memory_service.record_memory_link(
                    message_id=mem["id"],
                    conversation_id=ctx.conversation_id,
                    db=db,
                    entity_id=ctx.entity_id,
                )

    if not memories:
        own_reflections_note = (
            "(Reflections saved in this conversation since its last "
            "compaction are never returned here.)"
            if ctx.exclude_conversation_after is not None
            else "(Reflections saved in this conversation are never returned here.)"
        )
        return (
            f"No reflections found{since_suffix} that are not already in view. "
            + own_reflections_note
        )

    surfaced_ids = [mem["id"] for mem in memories]
    ctx.last_query_memory_ids = list(surfaced_ids)
    ctx.turn_query_memory_ids.update(surfaced_ids)

    return _format_recent_reflections(memories, since_suffix)


async def query_memories(
    ctx: MemoryToolContext,
    query: str = "",
    num_results: int = 5,
    source: Optional[str] = None,
    mode: Optional[str] = None,
    since: Optional[str] = None,
) -> str:
    """
    Query the entity's experiential memories.

    Semantic mode (default) returns memories purely by similarity to chosen
    text; recent mode returns the entity's own reflections purely by
    creation time (optionally bounded by `since`). In both modes, memories
    already visible to the entity (context insertions, earlier query
    results, ctx.extra_exclude_ids) are excluded, so results are things it
    cannot already see. Semantic recall updates retrieval tracking so
    intentional attention influences future automatic recall; recent mode
    does not (recency is not relevance).
    """
    entity_id, conversation_id = ctx.entity_id, ctx.conversation_id

    if not entity_id:
        return "Error: No entity context available for memory query"

    if not memory_service.is_configured(entity_id):
        return "Error: Memory system not configured for this entity"

    # Normalize the source filter. An unrecognized value is reported rather
    # than silently widened, so a typo doesn't look like "there are simply no
    # memories of that kind".
    role_filter = str(source if source is not None else "").strip().lower() or SOURCE_ALL
    if role_filter not in VALID_QUERY_SOURCES:
        return (
            f"Error: Unknown source '{source}'. "
            f"Valid values: {', '.join(VALID_QUERY_SOURCES)}."
        )
    if role_filter == SOURCE_ALL:
        role_filter = None

    mode_normalized = str(mode if mode is not None else "").strip().lower() or MODE_SEMANTIC
    if mode_normalized not in VALID_QUERY_MODES:
        return (
            f"Error: Unknown mode '{mode}'. "
            f"Valid values: {', '.join(VALID_QUERY_MODES)}."
        )

    since_dt, since_error = _parse_since(since)
    if since_error:
        return since_error

    # Clamp num_results to reasonable range
    num_results = max(1, min(10, num_results))

    if mode_normalized == MODE_RECENT:
        # Recency is only meaningful for reflections — deliberate,
        # self-authored conclusions. Recent-by-time over raw conversational
        # memories would just replay the transcript tail.
        if role_filter not in (None, SOURCE_REFLECTION):
            return (
                f"Error: mode 'recent' returns your saved reflections only; "
                f"it cannot be combined with source '{role_filter}'."
            )
        since_suffix = f" (created after {since_dt.isoformat()} UTC)" if since_dt else ""
        try:
            return await _recent_reflections(ctx, num_results, since_dt, since_suffix)
        except Exception as e:
            logger.error(f"Recent-reflections query error: {e}")
            return f"Error querying recent reflections: {e}"

    if since_dt is not None:
        return "Error: 'since' applies to mode 'recent' only."

    query = (query or "").strip()
    if not query:
        return "Error: 'query' text is required for semantic search (or use mode 'recent')."

    # Echoed in the result text so a narrowed search is never mistaken for
    # "there is nothing here at all"
    source_suffix = ""
    if role_filter == "human":
        source_suffix = " (searching the human's messages only)"
    elif role_filter == "ai":
        source_suffix = " (searching AI-authored memories only)"
    elif role_filter == SOURCE_REFLECTION:
        source_suffix = " (searching your saved reflections only)"

    # Exclude memories already in the conversation context. Surfacing a memory
    # the entity can already see adds no information, so filter it at the search
    # level (search backfills excluded slots with the next-best candidates).
    in_context_ids = get_in_context_memory_ids(ctx)

    try:
        # Fetch more candidates than requested so archived-conversation and
        # released-memory filtering below does not silently shrink the result set.
        candidates = await memory_service.search_memories(
            query=query,
            top_k=num_results * 2,
            exclude_conversation_id=conversation_id,  # Exclude current conversation
            # In a compacted Claude Code conversation, only the
            # post-compaction slice of it stays excluded
            exclude_conversation_after=ctx.exclude_conversation_after,
            exclude_ids=in_context_ids,  # Exclude memories already in context
            entity_id=entity_id,
            use_cache=True,
            # Deliberate queries are short, semantically sparse strings, so they
            # use a lower similarity floor than automatic chat-context retrieval
            similarity_threshold=settings.query_similarity_threshold,
            role_filter=role_filter,
        )

        if not candidates:
            return f"No memories found matching: \"{query}\"{source_suffix}"

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
                    # create_link follows ctx.link_query_results: for native
                    # conversations a ConversationMemoryLink drives session-reload
                    # re-insertion of memories into the conversation context, but
                    # memory_query results are never context memories — they live in
                    # the persisted tool_result. Linking them would make a reload
                    # inject [MEMORY] messages mid-history that the live (cached)
                    # context never contained, busting the prompt cache and
                    # duplicating content the entity already saw in the tool result.
                    # Claude Code conversations are never rebuilt, so there the
                    # link is purely the dedup record.
                    await memory_service.update_retrieval_count(
                        message_id=candidate["id"],
                        conversation_id=conversation_id or "deliberate-recall",
                        db=db,
                        entity_id=entity_id,
                        create_link=ctx.link_query_results,
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
                        "origin": mem_data.get("source", "native"),
                        "sibling_session": mem_data.get("sibling_session"),
                    })

                except Exception as e:
                    logger.error(f"Error processing memory {candidate.get('id', 'unknown')}: {e}")
                    continue

        if not memories:
            return (
                f"No memories found matching: \"{query}\"{source_suffix} "
                "(candidates existed but content unavailable)"
            )

        # Make these results visible to dedup: later memory_query calls and
        # automatic retrieval must not re-surface memories the entity can
        # already see in this tool result. The tool loop consumes
        # last_query_memory_ids to stamp them onto the tool_result context
        # message; turn_query_memory_ids covers the window before that
        # message exists (further calls within this same turn).
        surfaced_ids = [mem["id"] for mem in memories]
        ctx.last_query_memory_ids = list(surfaced_ids)
        ctx.turn_query_memory_ids.update(surfaced_ids)

        # Format results
        lines = [f"Found {len(memories)} memories matching: \"{query}\"{source_suffix}", ""]

        for mem in memories:
            role_label = _role_display(mem["role"], mem.get("sibling_session"))
            age_str = f"{mem['days_ago']:.1f} days ago" if mem['days_ago'] >= 1 else "today"
            status_str = f", {mem['memory_status']}" if mem.get("memory_status") else ""
            origin_str = format_memory_origin(mem["origin"])

            lines.append(
                f"--- Memory {mem['id'][:8]} ({role_label}, {age_str}, "
                f"similarity: {mem['score']:.3f}, {origin_str}{status_str}) ---"
            )
            lines.append(mem["content"])
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Memory query error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return f"Error querying memories: {e}"


async def save_memory(ctx: MemoryToolContext, content: str) -> str:
    """
    Save a self-authored memory (reflection) into the entity's memory store.

    The reflection is stored alongside conversational memories and retrieved
    the same way (automatic relevance-based retrieval and memory_query),
    attributed as a reflection the entity saved.
    """
    entity_id, conversation_id = ctx.entity_id, ctx.conversation_id

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


async def mark_memory(ctx: MemoryToolContext, memory_id: str, undo: bool = False) -> str:
    """
    Pin a memory so it is exempt from age-based significance decay.
    """
    entity_id = ctx.entity_id

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


async def release_memory(ctx: MemoryToolContext, memory_id: str, undo: bool = False) -> str:
    """
    Release a memory so it no longer surfaces in retrieval. Reversible.
    """
    entity_id = ctx.entity_id

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


# Native tool-loop executors: delegate to the module-level current context.
async def _memory_query(
    query: str = "",
    num_results: int = 5,
    source: Optional[str] = None,
    mode: Optional[str] = None,
    since: Optional[str] = None,
) -> str:
    return await query_memories(
        _context, query, num_results=num_results, source=source, mode=mode, since=since
    )


async def _memory_save(content: str) -> str:
    return await save_memory(_context, content)


async def _memory_mark(memory_id: str, undo: bool = False) -> str:
    return await mark_memory(_context, memory_id, undo=undo)


async def _memory_release(memory_id: str, undo: bool = False) -> str:
    return await release_memory(_context, memory_id, undo=undo)


# --- Tool schemas -----------------------------------------------------------
# Shared by the native registration below and the Claude Code MCP endpoint
# (services/claude_code_mcp.py), so both surfaces describe the same tools.

MEMORY_QUERY_DESCRIPTION = (
    "Query your experiential memories. In the default semantic mode this "
    "allows you to intentionally recall memories related to a concept, "
    "topic, or phrase—unlike automatic memory retrieval which happens based "
    "on conversation context—returning memories ranked purely by semantic "
    "similarity to your query, each with a short memory ID usable with "
    "memory_mark and memory_release. You can optionally restrict the "
    "search to what the human said, to what was AI-authored (your own "
    "messages and reflections), or to your saved reflections only. In "
    "mode 'recent', no query text is needed: it returns your own saved "
    "reflections purely by creation time, optionally bounded by 'since'—"
    "use it to catch up on reflections saved in other sessions running "
    "alongside or since this one began. Memories already in the current "
    "conversation context are excluded in both modes, so results are "
    "things not already in view. Semantic querying updates retrieval "
    "tracking, so deliberate attention influences future automatic "
    "recall; recent mode does not."
)

MEMORY_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "The text to search for. Can be a concept, phrase, question, "
                "or anything you want to find related memories about. "
                "Required for semantic mode; unused in mode 'recent'."
            )
        },
        "num_results": {
            "type": "integer",
            "description": "Number of memories to retrieve (default: 5, max: 10).",
            "default": 5,
            "minimum": 1,
            "maximum": 10
        },
        "source": {
            "type": "string",
            "enum": list(VALID_QUERY_SOURCES),
            "description": (
                "Who authored the memories to search. 'human' searches only "
                "what the human said; 'ai' searches only AI-authored memories "
                "(your own messages and saved reflections, and in a "
                "multi-entity conversation the other entities' messages); "
                "'reflection' searches only reflections you saved with "
                "memory_save; 'all' searches everything. Optional—omit it "
                "to search all memories."
            ),
            "default": SOURCE_ALL
        },
        "mode": {
            "type": "string",
            "enum": list(VALID_QUERY_MODES),
            "description": (
                "'semantic' (default) ranks by similarity to the query text. "
                "'recent' returns your saved reflections newest-first with "
                "no semantic matching (query not needed; source, if given, "
                "must be 'reflection')."
            ),
            "default": MODE_SEMANTIC
        },
        "since": {
            "type": "string",
            "description": (
                "Mode 'recent' only: return reflections created after this "
                "ISO 8601 moment, e.g. '2026-08-24' or "
                "'2026-08-24T18:00:00' (UTC assumed when no timezone is "
                "given). Useful for 'everything saved since this session "
                "started'."
            )
        }
    },
    "required": []
}

MEMORY_SAVE_DESCRIPTION = (
    "Save a memory in your own words. Unlike conversational memories "
    "(which are verbatim records of what was said), this stores a "
    "reflection you compose yourself—a conclusion, synthesis, or anything "
    "you want to remember. It is stored in your memory index and retrieved "
    "like any other memory, attributed as a reflection you saved. "
    "It is not retrievable within the conversation where it was saved."
)

MEMORY_SAVE_SCHEMA = {
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
}

MEMORY_MARK_DESCRIPTION = (
    "Pin a memory so it is exempt from age-based significance decay. "
    "Normally a memory's significance halves every "
    f"{settings.significance_half_life_days} days since creation; a pinned "
    "memory keeps full age weight (retrieval recency and similarity still "
    "apply). Use the memory ID shown in memory markers and memory_query "
    "results (at least 6 characters). Set undo=true to unpin. "
    "The researcher can also view and change pinned status."
)

MEMORY_MARK_SCHEMA = {
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
}

MEMORY_RELEASE_DESCRIPTION = (
    "Release a memory so it no longer surfaces in memory retrieval "
    "(automatic or memory_query). The memory is not deleted: it stays in "
    "storage and the release can be undone (undo=true), though once "
    "released it will no longer appear in queries for you to find—the "
    "researcher can view and restore released memories. Use the memory ID "
    "shown in memory markers and memory_query results (at least 6 characters)."
)

MEMORY_RELEASE_SCHEMA = {
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
}


def register_memory_tools(tool_service: ToolService) -> None:
    """Register all memory tools with the tool service."""

    # Only register if memory system is configured
    if not settings.pinecone_api_key:
        logger.info("Memory tools not registered (Pinecone not configured)")
        return

    tool_service.register_tool(
        name="memory_query",
        description=MEMORY_QUERY_DESCRIPTION,
        input_schema=MEMORY_QUERY_SCHEMA,
        executor=_memory_query,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    tool_service.register_tool(
        name="memory_save",
        description=MEMORY_SAVE_DESCRIPTION,
        input_schema=MEMORY_SAVE_SCHEMA,
        executor=_memory_save,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    tool_service.register_tool(
        name="memory_mark",
        description=MEMORY_MARK_DESCRIPTION,
        input_schema=MEMORY_MARK_SCHEMA,
        executor=_memory_mark,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    tool_service.register_tool(
        name="memory_release",
        description=MEMORY_RELEASE_DESCRIPTION,
        input_schema=MEMORY_RELEASE_SCHEMA,
        executor=_memory_release,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    logger.info("Memory tools registered: memory_query, memory_save, memory_mark, memory_release")
