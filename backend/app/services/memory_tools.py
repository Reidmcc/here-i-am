"""
Memory Tools - Tool definitions for entity memory querying and curation.

These tools allow AI entities to:
- memory_query: intentionally query their vector memory with chosen text
- memory_save: write a self-authored memory (reflection) into their memory store
- memory_mark: pin a memory so it is exempt from age-based significance decay
- memory_release: exclude a memory from future retrieval (reversible)
- memory_read: read the archive in order over a span of time (issue #343)
- memory_neighbors: open one memory outward to the messages around it
- memory_find: every message containing the exact words, in order

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
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.config import settings
from app.database import async_session_maker
from app.models import Conversation, Message, MessageRole
from app.services.harness_limits import (  # noqa: F401 — the limits are re-exported for the readers' tests
    HARNESS_CHARS_PER_TOKEN,
    HARNESS_PERSIST_BYTES,
    HARNESS_RESULT_CAP_TOKENS,
    TOOL_RESULT_BUDGET_BYTES,
    fit_by_priority,
    fit_prefix,
    fit_report,
    utf8_size,
)
from app.services.memory_context import format_memory_origin
from app.services.memory_service import (
    MEMORY_ROLES,
    STATUS_SET_BY_ENTITY,
    STATUS_SET_BY_RESEARCHER,
    VALID_ROLE_FILTERS,
    memory_service,
)
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
# "released" lists the entity's released memories, most recently released
# first — the review channel that makes a release reversible by the one who
# made it (released memories are otherwise unfindable by the entity).
MODE_RELEASED = "released"
VALID_QUERY_MODES = (MODE_SEMANTIC, MODE_RECENT, MODE_RELEASED)

# memory_read page budget (tokens per page, by Message.token_count or a
# length estimate) and memory_neighbors window bounds (issue #343).
READ_PAGE_TOKENS_DEFAULT = 8000
READ_PAGE_TOKENS_MIN = 500
# What Claude Code does with a large tool result — measured 2026-09-16 on
# the live backend (issue #353, the review session's bracket) and kept with
# the other channels' limits in harness_limits, which carries the brackets
# and the re-measurement recipes. Two limits, the lower one binding: any
# result above about 50 KB is persisted to a file with a 2 KB preview
# inline, and getting it back costs two or three Read calls per page
# (the cost #353 is about); above about 25k tokens by the harness's own
# counter (2.84 chars/token on archive prose, ~1.45× tiktoken) the result
# is refused outright. The names are imported into this module (see the
# imports above) because the readers and their tests speak in them.
# The budget is measured on the page AS RENDERED (issue #353): each row's
# header line and its content or pointer line, in UTF-8 bytes (the persist
# line is bytes) at RENDERED_CHARS_PER_TOKEN per token — the harness's
# measured ratio, so page_tokens means what it says in the units the
# harness decides with — with PAGE_FRAME_TOKENS held back for the page's
# own header sentence and footer. Message.token_count is not used: it is a
# tiktoken count of the content alone, which the harness counts ~1.45×
# heavier, and the headers and pointers were never charged, so a page
# budgeted at 20,000 by it rendered at 93k characters and spilled. The
# maximum is set by the persist line: a page at READ_PAGE_TOKENS_MAX
# renders within READ_PAGE_MAX_BYTES, under 50 KB with margin and ~16k by
# the cap's counter; no page over about 17k honest tokens can land in this
# harness at all. The one exception is a single message larger than the
# budget, which is returned alone and whole. test_memory_read pins the
# maximum page and the post-compaction block's page against both limits.
RENDERED_CHARS_PER_TOKEN = HARNESS_CHARS_PER_TOKEN
READ_PAGE_TOKENS_MAX = 16000
PAGE_FRAME_TOKENS = 250
READ_PAGE_MAX_BYTES = int(READ_PAGE_TOKENS_MAX * RENDERED_CHARS_PER_TOKEN)
NEIGHBORS_DEFAULT = 2
NEIGHBORS_MAX = 10

# memory_read / memory_find reading direction (issue #351). Forward pages
# start at `from` and move toward `to`. Backward pages start at `to` and
# hold the newest rows not yet shown, rendered oldest-first so a page still
# reads like the archive; the cursor walks further back toward `from` — the
# shape a freshly compacted session wants (the stretch just before the
# boundary, whatever its dates) and "when did I last say this".
DIRECTION_FORWARD = "forward"
DIRECTION_BACKWARD = "backward"
VALID_READ_DIRECTIONS = (DIRECTION_FORWARD, DIRECTION_BACKWARD)

# max_pages caps a walk by page count: the page that reaches the cap still
# prints its cursor, but under a "cap reached" note instead of the plain
# next-page line, and passing the cursor back with a higher max_pages
# continues (the cursor carries its page number). A page is never refused.
MAX_PAGES_MIN = 1

# Tools whose results are a list of memories the entity can now see. The
# native tool loop stamps the surfaced ids onto the tool_result context
# message (memory_query_ids) for every one of these, and the session reload
# path parses their "--- Memory xxxxxxxx (" header lines back into stamps.
MEMORY_RESULT_STAMPING_TOOLS = ("memory_query", "memory_read", "memory_neighbors", "memory_find")


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
        model: The model executing this tool call, recorded onto reflections
            it saves (Message.model, issue #321). Set from the live session
            in the native tool loop, where the responding model is known.
            None for Claude Code MCP calls — the endpoint has no trustworthy
            source for the calling model, and a guess is worse than NULL.
    """
    entity_id: Optional[str] = None
    conversation_id: Optional[str] = None
    session: Any = None
    turn_query_memory_ids: Set[str] = field(default_factory=set)
    last_query_memory_ids: List[str] = field(default_factory=list)
    extra_exclude_ids: Set[str] = field(default_factory=set)
    link_query_results: bool = False
    exclude_conversation_after: Optional[datetime] = None
    model: Optional[str] = None
    # The byte budget a list-shaped result (memory_query in every mode,
    # memory_neighbors) is fitted to, whole memories first and headers only
    # past it. Set by the Claude Code MCP endpoint to the harness's
    # tool-result line (harness_limits.TOOL_RESULT_BUDGET_BYTES); None in
    # native conversations, where a tool result goes straight into the
    # context and no such line exists.
    result_budget_bytes: Optional[int] = None


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
        # The responding model, so reflections saved this turn are
        # attributed at write time (None when there is no live session)
        model=getattr(session, "model", None) or None,
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


def _model_display(mem: Dict[str, Any], include_model: bool) -> str:
    """
    The opt-in model attribution for a memory_query result line (issue
    #321): ", model: <id>" when the caller asked for it, or ", model:
    unrecorded" for rows written before the column existed (or by a path
    that cannot know — an honest absence, never a date-inferred guess).
    Empty when not requested: even deliberate recall must not arrive
    pre-labelled with its substrate unless the entity asks on purpose.
    """
    if not include_model:
        return ""
    model = mem.get("model")
    return f", model: {model}" if model else ", model: unrecorded"


# What a memory renders as when the whole result would not land in context
# (Claude Code over MCP; the native app has no such line): its header line
# stays (the id, attribution, and score are the useful part), the content
# gives way to this one line. A memory is shown whole or pointed at, never
# cut — the rule the archive readers follow (issue #353), applied to the
# list-shaped tools. Rare in practice (ten long messages), but a result
# that goes to disk costs Read calls and cuts mid-memory. Only a memory
# shown in full counts as retrieved — tracked, stamped, linked: a header-
# only one is exactly the one the entity is told to go and open, and if it
# joined the in-view set the readers would render it as a pointer.
RESULT_SIZE_POINTER = (
    "[listed by header only: the full result would exceed what this harness "
    "shows in one tool result; open it with memory_neighbors or memory_read]"
)


def _fit_memory_entries(
    entries: List[Tuple[str, str]], frame: str, budget: Optional[int]
) -> Tuple[List[str], List[bool]]:
    """
    Render (header, content) entries in order, each in full while the whole
    result stays within `budget` bytes and as a header plus
    RESULT_SIZE_POINTER after that, as output lines plus a flag per entry
    (True = shown in full). `frame` is the text around the entries (intro
    and outro lines), charged against the budget with a reserve for the fit
    note. No budget (a native conversation) shows everything.
    """
    if budget is None:
        in_full = [True] * len(entries)
    else:
        full = [utf8_size(header) + utf8_size(content) + 3 for header, content in entries]
        pointer = [
            utf8_size(header) + utf8_size(RESULT_SIZE_POINTER) + 3 for header, _ in entries
        ]
        in_full = fit_by_priority(
            full, pointer, list(range(len(entries))), budget - utf8_size(frame) - 400
        )
    lines: List[str] = []
    for (header, content), shown in zip(entries, in_full, strict=True):
        lines.append(header)
        lines.append(content if shown else RESULT_SIZE_POINTER)
        lines.append("")
    return lines, in_full


def _format_recent_reflections(
    memories: List[Dict[str, Any]],
    since_suffix: str,
    include_model: bool = False,
    budget: Optional[int] = None,
) -> Tuple[str, List[str]]:
    """Render recent-mode results (no similarity scores — ordering is time),
    as (text, ids shown in full)."""
    now = datetime.utcnow()
    entries: List[Tuple[str, str]] = []
    for mem in memories:
        created_at = mem["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        days_ago = (now - created_at).total_seconds() / 86400
        age_str = f"{days_ago:.1f} days ago" if days_ago >= 1 else "today"
        status_str = f", {mem['memory_status']}" if mem.get("memory_status") else ""
        origin_str = format_memory_origin(mem.get("source", "native"))
        model_str = _model_display(mem, include_model)
        entries.append((
            f"--- Memory {mem['id'][:8]} (You reflected, {age_str}, {origin_str}{status_str}{model_str}) ---",
            mem["content"],
        ))
    intro = f"Your {len(memories)} most recent reflections{since_suffix}, newest first:"
    body, in_full = _fit_memory_entries(entries, intro, budget)
    shown_ids = [mem["id"] for mem, flag in zip(memories, in_full, strict=True) if flag]
    _, note = fit_report(len(shown_ids), len(memories), "reflections")
    return "\n".join([intro + note, "", *body]), shown_ids


async def _recent_reflections(
    ctx: MemoryToolContext,
    num_results: int,
    since: Optional[datetime],
    since_suffix: str,
    include_model: bool = False,
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
        text, shown_ids = (
            _format_recent_reflections(
                memories, since_suffix, include_model, ctx.result_budget_bytes
            )
            if memories
            else ("", [])
        )
        # Only what was shown in full is linked: a header-only reflection
        # must stay openable through the readers
        if ctx.link_query_results:
            for mem_id in shown_ids:
                await memory_service.record_memory_link(
                    message_id=mem_id,
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

    ctx.last_query_memory_ids = list(shown_ids)
    ctx.turn_query_memory_ids.update(shown_ids)
    return text


def _describe_release(mem: Dict[str, Any], now: datetime) -> str:
    """'released by you 2.1 days ago' — who withdrew the memory, and when."""
    set_by = mem.get("status_set_by")
    who = {
        STATUS_SET_BY_ENTITY: "by you",
        STATUS_SET_BY_RESEARCHER: "by the researcher",
    }.get(set_by)
    set_at = mem.get("status_set_at")
    if isinstance(set_at, str):
        set_at = datetime.fromisoformat(set_at)
    when = None
    if set_at is not None:
        days_ago = (now - set_at).total_seconds() / 86400
        when = f"{days_ago:.1f} days ago" if days_ago >= 1 else "today"
    if who is None and when is None:
        return "released before release provenance was recorded"
    parts = ["released"]
    if who:
        parts.append(who)
    if when:
        parts.append(when)
    return " ".join(parts)


def _format_released_memories(
    memories: List[Dict[str, Any]],
    total: int,
    since_suffix: str,
    source_suffix: str,
    include_model: bool = False,
    budget: Optional[int] = None,
) -> Tuple[str, List[str]]:
    """Render released-mode results: newest release first, no similarity
    scores, as (text, ids shown in full)."""
    now = datetime.utcnow()
    intro = (
        f"Your released memories{source_suffix}{since_suffix}: {len(memories)} shown "
        f"of {total} released in total, most recently released first."
    )
    outro = "Restore any of these with memory_release(memory_id, undo=true)."
    entries: List[Tuple[str, str]] = []
    for mem in memories:
        created_at = mem["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        days_ago = (now - created_at).total_seconds() / 86400
        age_str = f"{days_ago:.1f} days ago" if days_ago >= 1 else "today"
        origin_str = format_memory_origin(mem.get("source", "native"))
        model_str = _model_display(mem, include_model)
        entries.append((
            f"--- Memory {mem['id'][:8]} ({_role_display(mem['role'])}, {age_str}, "
            f"{origin_str}{model_str}; {_describe_release(mem, now)}) ---",
            mem["content"],
        ))
    body, in_full = _fit_memory_entries(entries, intro + outro, budget)
    shown_ids = [mem["id"] for mem, flag in zip(memories, in_full, strict=True) if flag]
    _, note = fit_report(len(shown_ids), len(memories))
    return "\n".join([intro + note, "", *body, outro]), shown_ids


async def _released_memories(
    ctx: MemoryToolContext,
    num_results: int,
    since: Optional[datetime],
    since_suffix: str,
    role_filter: Optional[str],
    source_suffix: str,
    include_model: bool = False,
) -> str:
    """
    memory_query's released mode: the entity's released memories by release
    time, whoever released them. Curation, not recall — pure SQL (released
    memories are out of the vector search anyway), and times_retrieved /
    last_retrieved_at are never touched. Exclusions and dedup stamping
    follow recent mode, so repeated calls page through the list, and the
    total count is always reported so the picture stays complete. In Claude
    Code conversations results are linked as the dedup record, which also
    keeps a memory restored after review from re-surfacing there.
    """
    in_context_ids = get_in_context_memory_ids(ctx)
    async with async_session_maker() as db:
        memories = await memory_service.get_released_memories(
            db,
            entity_id=ctx.entity_id,
            limit=num_results,
            exclude_conversation_id=ctx.conversation_id,
            exclude_ids=in_context_ids,
            since=since,
            role_filter=role_filter,
            exclude_conversation_after=ctx.exclude_conversation_after,
        )
        total = await memory_service.count_released_memories(
            db, entity_id=ctx.entity_id, role_filter=role_filter
        )
        text, shown_ids = (
            _format_released_memories(
                memories, total, since_suffix, source_suffix, include_model,
                ctx.result_budget_bytes,
            )
            if memories
            else ("", [])
        )
        # Only what was shown in full is linked (see RESULT_SIZE_POINTER)
        if ctx.link_query_results:
            for mem_id in shown_ids:
                await memory_service.record_memory_link(
                    message_id=mem_id,
                    conversation_id=ctx.conversation_id,
                    db=db,
                    entity_id=ctx.entity_id,
                )

    if not memories:
        if total == 0:
            return f"You have no released memories{source_suffix}."
        return (
            f"No released memories found{source_suffix}{since_suffix} that are not "
            f"already in view ({total} released in total)."
        )

    ctx.last_query_memory_ids = list(shown_ids)
    ctx.turn_query_memory_ids.update(shown_ids)
    return text


async def query_memories(
    ctx: MemoryToolContext,
    query: str = "",
    num_results: int = 5,
    source: Optional[str] = None,
    mode: Optional[str] = None,
    since: Optional[str] = None,
    include_model: bool = False,
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

    include_model (default False, issue #321) adds each result's recorded
    producing model to its header line — "unrecorded" where the archive
    never captured one. Off by default on purpose: querying is the most
    agentic form of remembering the entity has, and even deliberate recall
    should not arrive pre-labelled with its substrate. Inline [MEMORY]
    context markers never carry it at all.
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

    # Echoed in the result text so a narrowed search is never mistaken for
    # "there is nothing here at all"
    source_suffix = ""
    if role_filter == "human":
        source_suffix = " (the human's messages only)"
    elif role_filter == "ai":
        source_suffix = " (AI-authored memories only)"
    elif role_filter == SOURCE_REFLECTION:
        source_suffix = " (your saved reflections only)"

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
            return await _recent_reflections(
                ctx, num_results, since_dt, since_suffix, include_model=include_model
            )
        except Exception as e:
            logger.error(f"Recent-reflections query error: {e}")
            return f"Error querying recent reflections: {e}"

    if mode_normalized == MODE_RELEASED:
        # `since` bounds the release time here, not creation: "what has been
        # released since X" is the review question
        since_suffix = f" (released after {since_dt.isoformat()} UTC)" if since_dt else ""
        try:
            return await _released_memories(
                ctx, num_results, since_dt, since_suffix, role_filter, source_suffix,
                include_model=include_model,
            )
        except Exception as e:
            logger.error(f"Released-memories query error: {e}")
            return f"Error listing released memories: {e}"

    if since_dt is not None:
        return "Error: 'since' applies to modes 'recent' and 'released' only."

    query = (query or "").strip()
    if not query:
        return (
            "Error: 'query' text is required for semantic search "
            "(or use mode 'recent' or 'released')."
        )

    if source_suffix:
        source_suffix = " (searching" + source_suffix[1:]

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
                        "model": mem_data.get("model"),
                    })

                except Exception as e:
                    logger.error(f"Error processing memory {candidate.get('id', 'unknown')}: {e}")
                    continue

            # Format results: whole memories in rank order while the result
            # lands in context (over MCP; natively there is no line), headers
            # only after that (RESULT_SIZE_POINTER)
            entries: List[Tuple[str, str]] = []
            for mem in memories:
                role_label = _role_display(mem["role"], mem.get("sibling_session"))
                age_str = f"{mem['days_ago']:.1f} days ago" if mem['days_ago'] >= 1 else "today"
                status_str = f", {mem['memory_status']}" if mem.get("memory_status") else ""
                origin_str = format_memory_origin(mem["origin"])
                model_str = _model_display(mem, include_model)
                entries.append((
                    f"--- Memory {mem['id'][:8]} ({role_label}, {age_str}, "
                    f"similarity: {mem['score']:.3f}, {origin_str}{status_str}{model_str}) ---",
                    mem["content"],
                ))
            intro = f"Found {len(memories)} memories matching: \"{query}\"{source_suffix}"
            body, in_full = _fit_memory_entries(entries, intro, ctx.result_budget_bytes)
            shown_ids = [
                mem["id"] for mem, flag in zip(memories, in_full, strict=True) if flag
            ]

            # Update retrieval tracking (times_retrieved and last_retrieved_at)
            # for what was SHOWN in full: deliberate attention influences
            # future automatic recall, and a header-only memory got none.
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
            for mem_id in shown_ids:
                await memory_service.update_retrieval_count(
                    message_id=mem_id,
                    conversation_id=conversation_id or "deliberate-recall",
                    db=db,
                    entity_id=entity_id,
                    create_link=ctx.link_query_results,
                )

        if not memories:
            return (
                f"No memories found matching: \"{query}\"{source_suffix} "
                "(candidates existed but content unavailable)"
            )

        # Make the shown results visible to dedup: later memory_query calls
        # and automatic retrieval must not re-surface memories the entity
        # can already see in this tool result. The tool loop consumes
        # last_query_memory_ids to stamp them onto the tool_result context
        # message; turn_query_memory_ids covers the window before that
        # message exists (further calls within this same turn). A header-
        # only memory is not in view and stays retrievable and openable.
        ctx.last_query_memory_ids = list(shown_ids)
        ctx.turn_query_memory_ids.update(shown_ids)

        _, note = fit_report(len(shown_ids), len(memories))
        return "\n".join([intro + note, "", *body])

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
                # The model composing this reflection, when the caller
                # knows it (native tool loop); NULL over MCP (issue #321)
                model=ctx.model,
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
                model=ctx.model,
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
                success = await memory_service.set_memory_status(
                    str(message.id), None, db, set_by=STATUS_SET_BY_ENTITY
                )
                if success:
                    return f"Unpinned memory {str(message.id)[:8]}. Normal age-based significance decay applies again."
            else:
                success = await memory_service.set_memory_status(
                    str(message.id), "pinned", db, set_by=STATUS_SET_BY_ENTITY
                )
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
                success = await memory_service.set_memory_status(
                    str(message.id), None, db, set_by=STATUS_SET_BY_ENTITY
                )
                if success:
                    return f"Restored memory {str(message.id)[:8]}. It can surface in retrieval again."
            else:
                success = await memory_service.set_memory_status(
                    str(message.id), "released", db, set_by=STATUS_SET_BY_ENTITY
                )
                if success:
                    return (
                        f"Released memory {str(message.id)[:8]}. "
                        "It will no longer surface in memory retrieval. "
                        "It is not deleted: you can review your released memories "
                        "at any time with memory_query mode='released' and restore "
                        "this one with memory_release undo=true."
                    )

            return "Error: Failed to update memory status"
    except Exception as e:
        logger.error(f"Memory release error: {e}")
        return f"Error releasing memory: {e}"


# --- Archive readers (issue #343) ------------------------------------------
#
# memory_read and memory_neighbors read the archive by position instead of
# by similarity, and memory_find by the exact words: the whole record, in
# order, verbatim, paginated. Pure SQL over Message
# (memory_service.read_messages_in_span / read_message_neighbors /
# find_messages). Three rules distinguish them from recall:
# - Nothing is excluded for being in context or in the current conversation:
#   the page stays whole and in order. But a row whose content is already in
#   live context (a memory retrieved into context from anywhere; the current
#   conversation's own messages — all of them natively, post-compaction ones
#   in Claude Code mode) renders as a header-only pointer, so nothing is
#   duplicated. In a compacted Claude Code conversation the pre-compaction
#   turns survive only as summary, so they render in full — exactly the use
#   the house wants.
# - No retrieval tracking: reading a page is not attention-weighting, and a
#   day's worth of rows must not inflate significance.
# - What a page shows is treated as in view afterwards: stamped onto the
#   tool result in native mode (via last_query_memory_ids, like
#   memory_query), linked once in Claude Code mode (like recent mode).
# - Pages are bounded by tokens measured on the page as rendered (issue
#   #353, RENDERED_CHARS_PER_TOKEN above), so a page asked for at the
#   maximum lands in context whole instead of spilling to a file.
# - scope="isolated" (issue #345) switches both context rules off for one
#   call: no pointers (every row in full, the conversation's own
#   post-compaction rows included) and nothing recorded as in view (no
#   stamping, no turn accumulator, no links). For a reader whose context
#   is not the conversation's: a subagent shares its parent's
#   conversation_id, so under the default scope it inherits the parent's
#   in-view set as pointers and its own reads poison the parent's dedup.
#   Isolation is context bookkeeping only; visibility rules (released,
#   archived, source, in_conversation) are unchanged.

_BARE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Stands in for the content of a row the reader can already see
IN_CONTEXT_POINTER = "[already in your context; not repeated here]"

# memory_read / memory_neighbors scope values (issue #345): which in-view
# set a call belongs to. "conversation" is the conversation's own (pointers
# for what it can already see; what the page shows is recorded as in view);
# "isolated" belongs to none (every row in full; nothing recorded).
SCOPE_CONVERSATION = "conversation"
SCOPE_ISOLATED = "isolated"
VALID_READ_SCOPES = (SCOPE_CONVERSATION, SCOPE_ISOLATED)

# Appended to a page header under scope="isolated"
ISOLATED_SCOPE_NOTE = (
    " Isolated scope: nothing is listed as already in your context, and "
    "nothing shown here is recorded as in view for this conversation."
)

_ISOLATED_VIEW: Tuple[Set[str], Optional[str], Optional[datetime]] = (set(), None, None)


def _normalize_scope(scope: Optional[str]) -> Tuple[bool, Optional[str]]:
    """(isolated, error) for a scope value; the default is the conversation's."""
    value = str(scope if scope is not None else "").strip().lower() or SCOPE_CONVERSATION
    if value not in VALID_READ_SCOPES:
        return False, (
            f"Error: Unknown scope '{scope}'. Valid values: {', '.join(VALID_READ_SCOPES)}."
        )
    return value == SCOPE_ISOLATED, None


def is_isolated_read(tool_input: Any) -> bool:
    """
    Whether a memory_read / memory_neighbors / memory_find call's input asked
    for scope="isolated". The native tool loop stamps nothing for such a call
    (the executor sets no ids), and the session reload parser uses this to
    skip re-stamping its persisted result; otherwise a reload would put
    ids in view that the live turn deliberately did not.
    """
    if not isinstance(tool_input, dict):
        return False
    return str(tool_input.get("scope") or "").strip().lower() == SCOPE_ISOLATED


def _live_view(
    ctx: MemoryToolContext, isolated: bool = False
) -> Tuple[Set[str], Optional[str], Optional[datetime]]:
    """
    What the reader can already see, for pointer rendering: the in-context
    memory ids (context insertions, earlier tool results, this turn's
    results, Claude Code's post-boundary links) and the live conversation
    with its compaction boundary (None = every row of it is in context).
    An isolated reader sees nothing in advance, so nothing is a pointer.
    """
    if isolated:
        return _ISOLATED_VIEW
    return (
        get_in_context_memory_ids(ctx),
        ctx.conversation_id,
        ctx.exclude_conversation_after,
    )


def _resolve_tz(tz: Optional[str]) -> Tuple[Optional[ZoneInfo], Optional[str]]:
    """An IANA timezone for span boundaries; UTC when omitted."""
    name = str(tz if tz is not None else "").strip() or "UTC"
    try:
        return ZoneInfo(name), None
    except (ZoneInfoNotFoundError, ValueError):
        return None, (
            f"Error: Unknown timezone '{tz}'. Use an IANA name such as 'UTC', "
            "'America/New_York', or 'Europe/London'."
        )


def _parse_span_bound(
    value: Any, tzinfo: ZoneInfo, end_of_day: bool
) -> Tuple[Optional[datetime], Optional[datetime], Optional[str]]:
    """
    Parse one end of a memory_read span. A bare date means the start (or,
    for `to`, the end) of that day in tzinfo; a datetime without an offset
    is read in tzinfo; an offset-carrying datetime is taken as is. Returns
    (naive UTC for the query, aware local for display, error).
    """
    text = str(value if value is not None else "").strip()
    try:
        if _BARE_DATE_RE.match(text):
            day = date.fromisoformat(text)
            local = datetime.combine(day, time.max if end_of_day else time.min, tzinfo=tzinfo)
        else:
            parsed = datetime.fromisoformat(text)
            local = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tzinfo)
    except ValueError:
        return None, None, (
            f"Error: Could not parse '{value}'. Use ISO 8601 — a date such as "
            "'2026-09-01' (the whole day) or a moment such as "
            "'2026-09-01T14:00' (read in tz) or '2026-09-01T14:00:00+00:00'."
        )
    return local.astimezone(timezone.utc).replace(tzinfo=None), local, None


def _format_local(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_stamp(created_at_iso: str, tzinfo: Optional[ZoneInfo]) -> str:
    """UTC stamp, with the local time alongside when a non-UTC tz was given."""
    moment = datetime.fromisoformat(created_at_iso)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    text = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if tzinfo is not None and tzinfo.key != "UTC":
        text += f" ({_format_local(moment.astimezone(tzinfo))})"
    return text


def _entity_labels() -> Dict[str, str]:
    return {entity.index_name: entity.label for entity in settings.get_entities()}


def _archive_attribution(
    item: Dict[str, Any], entity_id: Optional[str], entity_labels: Dict[str, str]
) -> str:
    """
    Who said an archive row, in the house's words: the human, you, you from
    another session, or — in a multi-entity conversation — the other entity
    by name (its own messages and its own reflections alike).
    """
    role = item.get("role")
    sibling = item.get("sibling_session")
    if sibling or role == "human":
        return _role_display(role, sibling)
    speaker = item.get("speaker_entity_id")
    other = bool(speaker and entity_id and speaker != entity_id)
    if role == "assistant":
        return f"{entity_labels.get(speaker, speaker)} said" if other else "You said"
    if role == "reflection":
        return f"{entity_labels.get(speaker, speaker)} reflected" if other else "You reflected"
    return _role_display(role)


def _conversation_label(item: Dict[str, Any]) -> str:
    title = (item.get("conversation_title") or "").strip()
    if title:
        return f'in "{title}"'
    return f"in conversation {item['conversation_id'][:8]}"


def _format_archive_item(
    item: Dict[str, Any],
    entity_id: Optional[str],
    entity_labels: Dict[str, str],
    tzinfo: Optional[ZoneInfo],
    include_model: bool,
    now: datetime,
    marked: bool = False,
) -> List[str]:
    """One archive row as output lines: header (the same "--- Memory xxxxxxxx ("
    shape the other tools print, so reload re-stamps it and memory_mark /
    memory_release / memory_neighbors accept the id), then the verbatim
    content — or, for a row already in the reader's context, a one-line
    pointer in its place."""
    parts = [
        _archive_attribution(item, entity_id, entity_labels),
        _format_stamp(item["created_at"], tzinfo),
        format_memory_origin(item.get("source", "native")),
        _conversation_label(item),
    ]
    flags = []
    status = item.get("memory_status")
    if status == "released":
        flags.append(_describe_release(item, now))
    elif status:
        flags.append(status)
    flag_text = f"; {', '.join(flags)}" if flags else ""
    marker = ">> " if marked else ""
    header = (
        f"--- {marker}Memory {item['id'][:8]} ({', '.join(parts)}{flag_text}"
        f"{_model_display(item, include_model)}) ---"
    )
    if item.get("in_context"):
        return [header, IN_CONTEXT_POINTER, ""]
    return [header, item["content"], ""]


async def _note_surfaced(ctx: MemoryToolContext, ids: List[str], db) -> None:
    """
    What an archive read just showed counts as in view from here on: the
    native tool loop stamps last_query_memory_ids onto the tool result, the
    turn accumulator covers same-turn calls, and Claude Code conversations
    get their link rows (once each). No retrieval tracking either way.
    """
    ids = list(dict.fromkeys(ids))
    ctx.last_query_memory_ids = list(ids)
    ctx.turn_query_memory_ids.update(ids)
    if ctx.link_query_results and ctx.conversation_id and ids:
        await memory_service.link_memories_once(
            ctx.conversation_id, ids, db, entity_id=ctx.entity_id
        )


def _parse_page_tokens(page_tokens: Any) -> Tuple[Optional[int], Optional[str]]:
    """(clamped page budget, error) for a page_tokens argument."""
    if page_tokens is None:
        return READ_PAGE_TOKENS_DEFAULT, None
    try:
        page_tokens = int(page_tokens)
    except (TypeError, ValueError):
        return None, f"Error: page_tokens must be an integer (got '{page_tokens}')."
    return max(READ_PAGE_TOKENS_MIN, min(READ_PAGE_TOKENS_MAX, page_tokens)), None


def rendered_tokens(text: str) -> int:
    """Page weight of rendered text: its UTF-8 size at RENDERED_CHARS_PER_TOKEN
    bytes per token (the harness's persist line is in bytes; on this
    archive's prose bytes and characters differ by a tenth of a percent,
    on a page of emoji they don't)."""
    return max(1, math.ceil(len(text.encode("utf-8")) / RENDERED_CHARS_PER_TOKEN))


def _page_weigher(
    entity_id: Optional[str], tzinfo: Optional[ZoneInfo], include_model: bool
) -> Callable[[Dict[str, Any]], int]:
    """
    The row weight the readers hand the paging core (issue #353): the row
    exactly as _render_archive_page will print it — header line, content
    or pointer line, blank line — measured by rendered_tokens. A pointer
    weighs its header, not the content it doesn't carry.
    """
    labels = _entity_labels()
    now = datetime.utcnow()

    def weigh(item: Dict[str, Any]) -> int:
        lines = _format_archive_item(item, entity_id, labels, tzinfo, include_model, now)
        return rendered_tokens("\n".join(lines) + "\n")

    return weigh


def _normalize_direction(direction: Any) -> Tuple[bool, Optional[str]]:
    """(backward, error) for a direction argument; omitted means forward."""
    value = str(direction if direction is not None else "").strip().lower() or DIRECTION_FORWARD
    if value not in VALID_READ_DIRECTIONS:
        return False, (
            f"Error: Unknown direction '{direction}'. Valid values: "
            f"{', '.join(VALID_READ_DIRECTIONS)}."
        )
    return value == DIRECTION_BACKWARD, None


def _parse_max_pages(max_pages: Any) -> Tuple[Optional[int], Optional[str]]:
    """(page cap or None when absent, error) for a max_pages argument."""
    if max_pages is None or (isinstance(max_pages, str) and not max_pages.strip()):
        return None, None
    try:
        value = int(max_pages)
    except (TypeError, ValueError):
        return None, f"Error: max_pages must be an integer (got '{max_pages}')."
    if value < MAX_PAGES_MIN:
        return None, f"Error: max_pages must be at least {MAX_PAGES_MIN} (got {value})."
    return value, None


def _check_cursor(
    cursor: Any, tool_name: str, same: str = "span", backward: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """(cursor or None when absent, error) for a page cursor argument;
    `same` names what a resumed page must repeat ("span" or "text"). A
    cursor is only resumed in the direction it was made in."""
    if cursor is None or not str(cursor).strip():
        return None, None
    decoded = memory_service.decode_read_cursor(cursor)
    if decoded is None:
        return None, (
            f"Error: Unrecognized cursor '{cursor}'. Pass back the cursor a "
            f"previous {tool_name} page returned, with the same {same}, direction, and filters."
        )
    if decoded.backward != backward:
        made_in = DIRECTION_BACKWARD if decoded.backward else DIRECTION_FORWARD
        return None, (
            f"Error: That cursor came from a {tool_name} page read "
            f"direction=\"{made_in}\"; pass it back with the same direction, "
            "or start a new read without a cursor."
        )
    return cursor, None


def _render_archive_page(
    ctx: MemoryToolContext,
    page: Dict[str, Any],
    header: str,
    tzinfo: Optional[ZoneInfo],
    include_model: bool,
    isolated: bool,
    released_note: str,
    same: str,
    noun: Tuple[str, str],
    end_text: str,
    max_pages: Optional[int] = None,
) -> str:
    """
    A non-empty archive page as the readers print it: the header sentence
    with the pointer / isolated / released notes appended, every row via
    _format_archive_item, then the next-page footer (`same` and `noun` word
    it: "the same span … (3 messages remain)"; a backward page's footer
    says the next page is earlier), or the cap note when this page reached
    `max_pages` and more remain (the cursor is still given), or `end_text`.
    """
    items = page["items"]
    pointer_count = sum(1 for item in items if item.get("in_context"))
    pointer_note = (
        f" {pointer_count} of them are already in your context and are listed "
        "without their content."
        if pointer_count
        else ""
    )
    lines = [
        header + pointer_note + (ISOLATED_SCOPE_NOTE if isolated else "") + released_note,
        "",
    ]
    labels = _entity_labels()
    now = datetime.utcnow()
    for item in items:
        lines.extend(
            _format_archive_item(item, ctx.entity_id, labels, tzinfo, include_model, now)
        )
    if page["next_cursor"]:
        remaining = page["remaining"]
        word = noun[0] if remaining == 1 else noun[1]
        verb = "remains" if remaining == 1 else "remain"
        earlier = "earlier " if page.get("backward") else ""
        if max_pages is not None and page.get("page", 1) >= max_pages:
            lines.append(
                f"Page cap reached (max_pages={max_pages}; this was page {page['page']}): "
                f"{remaining} {earlier}{word} {verb} unread. To read further, pass "
                f"cursor=\"{page['next_cursor']}\" with the same {same}, direction, and "
                "filters and a higher max_pages."
            )
        elif page.get("backward"):
            lines.append(
                f"Next page (earlier): pass cursor=\"{page['next_cursor']}\" with the "
                f"same {same}, direction, and filters ({remaining} earlier {word} {verb})."
            )
        else:
            lines.append(
                f"Next page: pass cursor=\"{page['next_cursor']}\" with the same {same} "
                f"and filters ({remaining} {word} {verb})."
            )
    else:
        lines.append(end_text)
    return "\n".join(lines)


def _span_text(
    start: Optional[datetime],
    start_local: Optional[datetime],
    end: Optional[datetime],
    end_local: Optional[datetime],
    tzinfo: ZoneInfo,
) -> str:
    """
    The span as memory_read's header states it: both bounds in tz with the
    UTC pair bracketed when tz is not UTC; an open start is "the start of
    your archive", an open end "now".
    """
    def utc(moment: datetime) -> str:
        return moment.strftime("%Y-%m-%d %H:%M:%S")

    if tzinfo.key == "UTC":
        start_text = utc(start) if start is not None else "the start of your archive"
        end_text = f"{utc(end)} UTC" if end is not None else "now"
        return f"{start_text} to {end_text}"
    start_text = _format_local(start_local) if start is not None else "the start of your archive"
    end_text = _format_local(end_local) if end is not None else "now"
    start_utc = utc(start) if start is not None else "the start"
    end_utc = utc(end) if end is not None else "now"
    return f"{start_text} to {end_text} [{tzinfo.key}; UTC {start_utc} to {end_utc}]"


async def _resolve_conversation_filter(
    db, ctx: MemoryToolContext, in_conversation: Any
) -> Tuple[Optional[str], str, Optional[str]]:
    """(conversation id or None for all, echo suffix, error) for an
    in_conversation argument, resolved within the entity's experience.

    in_conversation chooses what to read; it is not the conversation the
    call belongs to (ctx.conversation_id — the MCP tools take it as
    conversation_id on every call, and it is what resolves the entity).
    A call that carried no conversation_id runs as the default entity, so
    a filter that then fails to resolve names that as the likely cause
    rather than leaving a bare "no conversation of yours"."""
    if in_conversation is None or not str(in_conversation).strip():
        return None, "", None
    conversation, error = await memory_service.resolve_conversation_prefix(
        db, ctx.entity_id, in_conversation
    )
    if error:
        if ctx.conversation_id is None:
            label = _entity_labels().get(ctx.entity_id, ctx.entity_id)
            error += (
                f" This call carried no conversation_id, so it ran as the "
                f"default entity ({label}) and in_conversation was resolved "
                "among that entity's conversations. conversation_id says which "
                "conversation is calling (pass it on every call, from your "
                "session-start context); in_conversation only chooses what to read."
            )
        return None, "", f"Error: {error}"
    conversation_id = str(conversation.id)
    title = (conversation.title or "").strip()
    suffix = f', in "{title}"' if title else f", in conversation {conversation_id[:8]}"
    return conversation_id, suffix, None


def _normalize_source(source: Optional[str]) -> Tuple[Optional[str], str, Optional[str]]:
    """(role_filter or None for all, echo suffix, error) for a source value."""
    role_filter = str(source if source is not None else "").strip().lower() or SOURCE_ALL
    if role_filter not in VALID_QUERY_SOURCES:
        return None, "", (
            f"Error: Unknown source '{source}'. Valid values: {', '.join(VALID_QUERY_SOURCES)}."
        )
    suffix = {
        "human": " (the human's messages only)",
        "ai": " (AI-authored messages only)",
        SOURCE_REFLECTION: " (your saved reflections only)",
    }.get(role_filter, "")
    return (None if role_filter == SOURCE_ALL else role_filter), suffix, None


def _backward_end_text(conversation_id: Optional[str], start: Optional[datetime], what: str) -> str:
    """
    The last backward page's closing line, naming what was reached: the
    conversation's own beginning when the read was confined to one
    conversation with no `from` (the default stop the post-compaction block
    relies on), the archive's start when nothing bounded it, else the span's.
    """
    if conversation_id and start is None:
        return f"Start of the conversation: {what}."
    if start is None:
        return f"Start of your archive: {what}."
    return f"Start of span: {what}."


async def read_memories(
    ctx: MemoryToolContext,
    from_: Any = None,
    to: Any = None,
    tz: Optional[str] = None,
    in_conversation: Optional[str] = None,
    source: Optional[str] = None,
    cursor: Optional[str] = None,
    page_tokens: Optional[int] = None,
    include_released: bool = False,
    include_model: bool = False,
    scope: Optional[str] = None,
    direction: Optional[str] = None,
    max_pages: Any = None,
) -> str:
    """
    memory_read: the entity's archive between two moments, in order, one
    token-bounded page at a time — forward from `from`, or backward from
    `to` (issue #351), the walk optionally capped at `max_pages`. See the
    module comment above for the rules that set it apart from recall.
    """
    if not ctx.entity_id:
        return "Error: No entity context available for reading memories"

    tzinfo, error = _resolve_tz(tz)
    if error:
        return error
    isolated, error = _normalize_scope(scope)
    if error:
        return error
    backward, error = _normalize_direction(direction)
    if error:
        return error
    has_from = from_ is not None and bool(str(from_).strip())
    has_to = to is not None and bool(str(to).strip())
    if not has_from and not backward:
        return (
            "Error: 'from' is required — an ISO 8601 date such as '2026-09-01' "
            "(that whole day) or a moment such as '2026-09-01T14:00' — unless "
            "direction=\"backward\", which reads back from 'to' (default: now) "
            "toward the start of your archive."
        )
    start = start_local = None
    if has_from:
        start, start_local, error = _parse_span_bound(from_, tzinfo, end_of_day=False)
        if error:
            return error
    end = end_local = None
    if has_to:
        end, end_local, error = _parse_span_bound(to, tzinfo, end_of_day=True)
        if error:
            return error
    elif not backward:
        # Forward default: the end of the day `from` names, in tz.
        # Backward default: open — the page begins at the newest row.
        end_local = datetime.combine(start_local.date(), time.max, tzinfo=tzinfo)
        end = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    if start is not None and end is not None and end < start:
        return (
            f"Error: 'to' ({_format_local(end_local)}) is before 'from' "
            f"({_format_local(start_local)})."
        )

    role_filter, source_suffix, error = _normalize_source(source)
    if error:
        return error

    page_tokens, error = _parse_page_tokens(page_tokens)
    if error:
        return error
    cursor, error = _check_cursor(cursor, "memory_read", backward=backward)
    if error:
        return error
    max_pages, error = _parse_max_pages(max_pages)
    if error:
        return error

    span_text = _span_text(start, start_local, end, end_local, tzinfo)

    try:
        async with async_session_maker() as db:
            conversation_id, conversation_suffix, error = await _resolve_conversation_filter(
                db, ctx, in_conversation
            )
            if error:
                return error

            in_context_ids, live_conversation_id, live_after = _live_view(ctx, isolated)
            page = await memory_service.read_messages_in_span(
                db,
                entity_id=ctx.entity_id,
                start=start,
                end=end,
                role_filter=role_filter,
                conversation_id=conversation_id,
                include_released=bool(include_released),
                cursor=cursor,
                page_tokens=page_tokens - PAGE_FRAME_TOKENS,
                weigh=_page_weigher(ctx.entity_id, tzinfo, include_model),
                in_context_ids=in_context_ids,
                live_conversation_id=live_conversation_id,
                live_after=live_after,
                backward=backward,
            )
            items = page["items"]
            if items and not isolated:
                await _note_surfaced(ctx, [item["id"] for item in items], db)
    except Exception as e:
        logger.error(f"Memory read error: {e}")
        return f"Error reading memories: {e}"

    released_note = "" if include_released else " Released memories are not shown (include_released=true shows them)."
    if page["total"] == 0:
        return (
            f"No messages between {span_text}{source_suffix}{conversation_suffix}."
            + released_note
        )
    if not items:
        if backward:
            return _backward_end_text(
                conversation_id, start,
                f"no messages before that cursor between {span_text}"
                f"{source_suffix}{conversation_suffix} ({page['total']} in the span)",
            )
        return (
            f"End of span: no messages after that cursor between {span_text}"
            f"{source_suffix}{conversation_suffix} ({page['total']} in the span)."
        )

    first = page["offset"] + 1
    last = page["offset"] + len(items)
    if backward:
        how = f"read backward from its end, this page shows {first}–{last}, in order"
        end_text = _backward_end_text(conversation_id, start, "nothing earlier")
    else:
        how = f"this page shows {first}–{last}, in order"
        end_text = "End of span."
    return _render_archive_page(
        ctx, page,
        header=(
            f"Your archive, {span_text}{source_suffix}{conversation_suffix}: "
            f"{page['total']} messages in the span; {how}."
        ),
        tzinfo=tzinfo, include_model=include_model, isolated=isolated,
        released_note=released_note, same="span", noun=("message", "messages"),
        end_text=end_text, max_pages=max_pages,
    )



MATCH_DESCRIPTIONS = {
    memory_service.MATCH_PHRASE: "as a phrase",
    memory_service.MATCH_ALL: "all of the words",
    memory_service.MATCH_ANY: "any of the words",
}


def _describe_bound(moment_utc: datetime, local: datetime, tzinfo: ZoneInfo) -> str:
    if tzinfo.key == "UTC":
        return f"{moment_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    return f"{_format_local(local)} ({moment_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC)"


async def find_memories(
    ctx: MemoryToolContext,
    text: Any = None,
    match: Optional[str] = None,
    whole_words: bool = True,
    from_: Any = None,
    to: Any = None,
    tz: Optional[str] = None,
    in_conversation: Optional[str] = None,
    source: Optional[str] = None,
    cursor: Optional[str] = None,
    page_tokens: Optional[int] = None,
    include_released: bool = False,
    include_model: bool = False,
    scope: Optional[str] = None,
    direction: Optional[str] = None,
    max_pages: Any = None,
) -> str:
    """
    memory_find: every message in the entity's archive containing the given
    words (whole words by default), in order, one token-bounded page at a
    time — the record by word. Same rules as memory_read (see the module
    comment above), `direction` included (backward = the newest matches
    first across pages); the only difference is what selects the rows.
    """
    if not ctx.entity_id:
        return "Error: No entity context available for reading memories"

    text = str(text if text is not None else "").strip()
    if not text:
        return "Error: 'text' is required — the words to find, as written."
    match_mode = str(match if match is not None else "").strip().lower() or memory_service.MATCH_PHRASE
    if match_mode not in memory_service.VALID_MATCH_MODES:
        return (
            f"Error: Unknown match '{match}'. Valid values: "
            f"{', '.join(memory_service.VALID_MATCH_MODES)}."
        )
    tzinfo, error = _resolve_tz(tz)
    if error:
        return error
    isolated, error = _normalize_scope(scope)
    if error:
        return error
    backward, error = _normalize_direction(direction)
    if error:
        return error

    start = end = None
    start_local = end_local = None
    if from_ is not None and str(from_).strip():
        start, start_local, error = _parse_span_bound(from_, tzinfo, end_of_day=False)
        if error:
            return error
    if to is not None and str(to).strip():
        end, end_local, error = _parse_span_bound(to, tzinfo, end_of_day=True)
        if error:
            return error
    if start is not None and end is not None and end < start:
        return (
            f"Error: 'to' ({_format_local(end_local)}) is before 'from' "
            f"({_format_local(start_local)})."
        )

    role_filter, source_suffix, error = _normalize_source(source)
    if error:
        return error
    page_tokens, error = _parse_page_tokens(page_tokens)
    if error:
        return error
    cursor, error = _check_cursor(cursor, "memory_find", same="text", backward=backward)
    if error:
        return error
    max_pages, error = _parse_max_pages(max_pages)
    if error:
        return error

    if start is not None and end is not None:
        span_text = (
            f", between {_describe_bound(start, start_local, tzinfo)} and "
            f"{_describe_bound(end, end_local, tzinfo)}"
        )
    elif start is not None:
        span_text = f", from {_describe_bound(start, start_local, tzinfo)}"
    elif end is not None:
        span_text = f", up to {_describe_bound(end, end_local, tzinfo)}"
    else:
        span_text = ""
    boundary = "whole words" if whole_words else "inside words too"
    what = f'"{text}" ({MATCH_DESCRIPTIONS[match_mode]}, {boundary})'

    try:
        async with async_session_maker() as db:
            conversation_id, conversation_suffix, error = await _resolve_conversation_filter(
                db, ctx, in_conversation
            )
            if error:
                return error

            in_context_ids, live_conversation_id, live_after = _live_view(ctx, isolated)
            page = await memory_service.find_messages(
                db,
                entity_id=ctx.entity_id,
                text=text,
                match=match_mode,
                whole_words=bool(whole_words),
                start=start,
                end=end,
                role_filter=role_filter,
                conversation_id=conversation_id,
                include_released=bool(include_released),
                cursor=cursor,
                page_tokens=page_tokens - PAGE_FRAME_TOKENS,
                weigh=_page_weigher(ctx.entity_id, tzinfo, include_model),
                in_context_ids=in_context_ids,
                live_conversation_id=live_conversation_id,
                live_after=live_after,
                backward=backward,
            )
            items = page["items"]
            if items and not isolated:
                await _note_surfaced(ctx, [item["id"] for item in items], db)
    except Exception as e:
        logger.error(f"Memory find error: {e}")
        return f"Error finding memories: {e}"

    released_note = "" if include_released else " Released memories are not searched (include_released=true searches them)."
    filters = f"{span_text}{source_suffix}{conversation_suffix}"
    total = page["total"]
    if total == 0:
        return (
            f"No messages contain {what}{filters}: the words appear nowhere in "
            "the archive you can see." + released_note
        )
    plural = "es" if total != 1 else ""
    if not items:
        if backward:
            return _backward_end_text(
                conversation_id, start,
                f"no messages before that cursor contain {what}{filters} "
                f"({total} match{plural} in all)",
            )
        return (
            f"End of matches: no messages after that cursor contain {what}{filters} "
            f"({total} match{plural} in all)."
        )

    first = page["offset"] + 1
    last = page["offset"] + len(items)
    if backward:
        how = f"read backward from the newest, this page shows {first}–{last}, in order"
        end_text = _backward_end_text(conversation_id, start, "no earlier matches")
    else:
        how = f"this page shows {first}–{last}, in order"
        end_text = "End of matches."
    return _render_archive_page(
        ctx, page,
        header=(
            f"Your archive, messages containing {what}{filters}: {total} match{plural}; "
            f"{how}."
        ),
        tzinfo=tzinfo, include_model=include_model, isolated=isolated,
        released_note=released_note, same="text", noun=("match", "matches"),
        end_text=end_text, max_pages=max_pages,
    )


async def neighbor_memories(
    ctx: MemoryToolContext,
    memory_id: str,
    before: Optional[int] = None,
    after: Optional[int] = None,
    include_released: bool = False,
    include_model: bool = False,
    scope: Optional[str] = None,
) -> str:
    """
    memory_neighbors: one memory with the messages immediately before and
    after it in its own conversation, in order — the page a retrieved
    memory is on. Same rules as memory_read.
    """
    if not ctx.entity_id:
        return "Error: No entity context available for reading memories"
    isolated, error = _normalize_scope(scope)
    if error:
        return error

    def _clamp(value: Any, name: str) -> Tuple[Optional[int], Optional[str]]:
        if value is None:
            return NEIGHBORS_DEFAULT, None
        try:
            count = int(value)
        except (TypeError, ValueError):
            return None, f"Error: {name} must be an integer (got '{value}')."
        return max(0, min(NEIGHBORS_MAX, count)), None

    before, error = _clamp(before, "before")
    if error:
        return error
    after, error = _clamp(after, "after")
    if error:
        return error

    try:
        async with async_session_maker() as db:
            message, error = await _resolve_memory_id(memory_id or "", db, ctx.entity_id)
            if error:
                return f"Error: {error}"
            if message.role not in MEMORY_ROLES:
                return (
                    f"Error: Memory '{str(message.id)[:8]}' is a {message.role.value} "
                    "row, not a memory."
                )
            in_context_ids, live_conversation_id, live_after = _live_view(ctx, isolated)
            window = await memory_service.read_message_neighbors(
                db,
                message,
                before=before,
                after=after,
                include_released=bool(include_released),
                in_context_ids=in_context_ids,
                live_conversation_id=live_conversation_id,
                live_after=live_after,
            )
            if window.get("archived"):
                return (
                    f"Error: Memory '{str(message.id)[:8]}' belongs to an archived "
                    "conversation, which is withdrawn from every memory surface."
                )
            header, footer, rendered, in_full = _fit_neighbor_window(
                ctx, window, include_released, include_model, isolated
            )
            # Only the rows shown in full count as in view from here on: a
            # header-only row is the one the entity is told to open, and
            # stamping it would make the readers render it as a pointer
            if not isolated:
                shown_ids = [
                    item["id"]
                    for item, flag in zip(window["items"], in_full, strict=True)
                    if flag
                ]
                await _note_surfaced(ctx, shown_ids, db)
    except Exception as e:
        logger.error(f"Memory neighbors error: {e}")
        return f"Error reading memory neighbors: {e}"

    lines = [header, ""]
    for item_lines, shown in zip(rendered, in_full, strict=True):
        if shown:
            lines.extend(item_lines)
        else:
            lines.extend([item_lines[0], RESULT_SIZE_POINTER, ""])
    lines.append(footer)
    return "\n".join(lines)


def _fit_neighbor_window(
    ctx: MemoryToolContext,
    window: Dict[str, Any],
    include_released: bool,
    include_model: bool,
    isolated: bool,
) -> Tuple[str, str, List[List[str]], List[bool]]:
    """
    The neighbors page's header, footer, every row rendered
    (_format_archive_item lines), and a flag per row saying whether it
    prints in full. The window lands in context whole when it can; when it
    can't (ctx.result_budget_bytes, set over MCP), the target stays in full
    and the neighbors are promoted outward from it, the rest listed by
    header only (RESULT_SIZE_POINTER) — a window is read from its center,
    so the far edges give way first. Natively there is no budget and every
    row is in full.
    """
    items = window["items"]
    target = items[window["target_index"]]
    shown_before = window["target_index"]
    shown_after = len(items) - window["target_index"] - 1
    header = (
        f"Memory {target['id'][:8]} {_conversation_label(target)} "
        f"({format_memory_origin(target.get('source', 'native'))}), with {shown_before} "
        f"message{'s' if shown_before != 1 else ''} before and {shown_after} after, in order."
    )
    edges = []
    if window["hit_start"]:
        edges.append("start")
    if window["hit_end"]:
        edges.append("end")
    if edges:
        header += f" The window reached the {' and '.join(edges)} of the conversation."
    pointer_count = sum(1 for item in items if item.get("in_context"))
    if pointer_count:
        header += (
            f" {pointer_count} of these are already in your context and are listed "
            "without their content."
        )
    if isolated:
        header += ISOLATED_SCOPE_NOTE
    if not include_released:
        header += " Released messages around it are not shown (include_released=true shows them)."

    footer = (
        "Read more of this conversation with memory_read (in_conversation="
        f"\"{target['conversation_id'][:8]}\", from=<date>)."
    )
    labels = _entity_labels()
    now = datetime.utcnow()
    rendered = [
        _format_archive_item(
            item, ctx.entity_id, labels, None, include_model, now,
            marked=(index == window["target_index"]),
        )
        for index, item in enumerate(items)
    ]
    if ctx.result_budget_bytes is None:
        return header, footer, rendered, [True] * len(items)

    center = window["target_index"]
    priority = [center]
    for distance in range(1, len(items)):
        if center - distance >= 0:
            priority.append(center - distance)
        if center + distance < len(items):
            priority.append(center + distance)
    full_sizes = [utf8_size("\n".join(lines)) + 1 for lines in rendered]
    pointer_sizes = [
        utf8_size(lines[0]) + utf8_size(RESULT_SIZE_POINTER) + 3 for lines in rendered
    ]
    in_full = fit_by_priority(
        full_sizes, pointer_sizes, priority,
        ctx.result_budget_bytes - utf8_size(header + footer) - 400,
    )
    shown = sum(in_full)
    if shown < len(items):
        header += (
            f" {len(items) - shown} of these messages are listed by header only "
            "because the window would exceed what this harness shows in one tool "
            "result; narrow before/after, or open them with memory_read."
        )
    return header, footer, rendered, in_full


# Native tool-loop executors: delegate to the module-level current context.
async def _memory_query(
    query: str = "",
    num_results: int = 5,
    source: Optional[str] = None,
    mode: Optional[str] = None,
    since: Optional[str] = None,
    include_model: bool = False,
) -> str:
    return await query_memories(
        _context, query, num_results=num_results, source=source, mode=mode, since=since,
        include_model=bool(include_model),
    )


async def _memory_save(content: str) -> str:
    return await save_memory(_context, content)


async def _memory_mark(memory_id: str, undo: bool = False) -> str:
    return await mark_memory(_context, memory_id, undo=undo)


async def _memory_release(memory_id: str, undo: bool = False) -> str:
    return await release_memory(_context, memory_id, undo=undo)


async def _memory_read(**kwargs: Any) -> str:
    # `from` is a keyword, so the executor takes the tool input as a dict
    return await read_memories(
        _context,
        from_=kwargs.get("from"),
        to=kwargs.get("to"),
        tz=kwargs.get("tz"),
        in_conversation=kwargs.get("in_conversation"),
        source=kwargs.get("source"),
        cursor=kwargs.get("cursor"),
        page_tokens=kwargs.get("page_tokens"),
        include_released=bool(kwargs.get("include_released", False)),
        include_model=bool(kwargs.get("include_model", False)),
        scope=kwargs.get("scope"),
        direction=kwargs.get("direction"),
        max_pages=kwargs.get("max_pages"),
    )


async def _memory_find(**kwargs: Any) -> str:
    # `from` is a keyword, so the executor takes the tool input as a dict
    return await find_memories(
        _context,
        text=kwargs.get("text"),
        match=kwargs.get("match"),
        whole_words=bool(kwargs.get("whole_words", True)),
        from_=kwargs.get("from"),
        to=kwargs.get("to"),
        tz=kwargs.get("tz"),
        in_conversation=kwargs.get("in_conversation"),
        source=kwargs.get("source"),
        cursor=kwargs.get("cursor"),
        page_tokens=kwargs.get("page_tokens"),
        include_released=bool(kwargs.get("include_released", False)),
        include_model=bool(kwargs.get("include_model", False)),
        scope=kwargs.get("scope"),
        direction=kwargs.get("direction"),
        max_pages=kwargs.get("max_pages"),
    )


async def _memory_neighbors(
    memory_id: str = "",
    before: Optional[int] = None,
    after: Optional[int] = None,
    include_released: bool = False,
    include_model: bool = False,
    scope: Optional[str] = None,
) -> str:
    return await neighbor_memories(
        _context, memory_id, before=before, after=after,
        include_released=bool(include_released), include_model=bool(include_model),
        scope=scope,
    )


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
    "alongside or since this one began. In mode 'released', also without "
    "query text, it lists your released memories (most recently released "
    "first, saying who released each and when) so you can review and "
    "undo releases with memory_release undo=true. Memories already in the "
    "current conversation context are excluded in every mode, so results "
    "are things not already in view. Semantic querying updates retrieval "
    "tracking, so deliberate attention influences future automatic "
    "recall; recent and released modes do not. Set include_model only when "
    "you specifically need to know which model produced each memory."
)

MEMORY_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "The text to search for. Can be a concept, phrase, question, "
                "or anything you want to find related memories about. "
                "Required for semantic mode; unused in modes 'recent' and 'released'."
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
                "must be 'reflection'). 'released' lists your released "
                "memories, most recently released first, with who released "
                "each and when (query not needed; source narrows by author)."
            ),
            "default": MODE_SEMANTIC
        },
        "since": {
            "type": "string",
            "description": (
                "Modes 'recent' and 'released' only: an ISO 8601 moment, "
                "e.g. '2026-08-24' or '2026-08-24T18:00:00' (UTC assumed "
                "when no timezone is given). In 'recent' it returns "
                "reflections created after it ('everything saved since this "
                "session started'); in 'released' it returns memories "
                "released after it."
            )
        },
        "include_model": {
            "type": "boolean",
            "description": (
                "When true, each result's header also names the model that "
                "produced the memory (or 'unrecorded' for memories from "
                "before this was tracked — it is never inferred). Off by "
                "default; memories normally arrive without their substrate "
                "attached. Use it for a specific purpose, such as comparing "
                "your voice across models or answering 'which model wrote "
                "that'."
            ),
            "default": False
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
    "The researcher can also view and change pinned status; any change "
    "they make is reported to you at the start of your next session."
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
    "(automatic or semantic memory_query). The memory is not deleted: it "
    "stays in storage, memory_query mode='released' lists everything you "
    "have released so you can review it, and a release is undone with "
    "undo=true. The researcher can also view and change released status; "
    "any change they make is reported to you at the start of your next "
    "session. Use the memory ID shown in memory markers and memory_query "
    "results (at least 6 characters)."
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


_INCLUDE_RELEASED_PROPERTY = {
    "type": "boolean",
    "description": (
        "When true, released memories are shown too, labeled as released "
        "(with who released each and when). Off by default: a release "
        "withdrew them on purpose."
    ),
    "default": False,
}

_INCLUDE_MODEL_PROPERTY = {
    "type": "boolean",
    "description": (
        "When true, each header also names the model that produced the "
        "message (or 'unrecorded'). Off by default, as in memory_query."
    ),
    "default": False,
}

_SCOPE_PROPERTY = {
    "type": "string",
    "enum": list(VALID_READ_SCOPES),
    "description": (
        "Which in-view set this call belongs to. 'conversation' (default): "
        "messages already in your context are listed as pointers, and what "
        "the call shows counts as in view afterwards. 'isolated': for a "
        "reader whose context is not this conversation's (a subagent, which "
        "shares its parent's conversation_id): every message comes back in "
        "full, this conversation's own included, and nothing is recorded as "
        "in view, so the read leaves no trace in the conversation's dedup. "
        "Visibility rules (released, archived, source, in_conversation) are "
        "the same under both."
    ),
    "default": SCOPE_CONVERSATION,
}

_DIRECTION_PROPERTY = {
    "type": "string",
    "enum": list(VALID_READ_DIRECTIONS),
    "description": (
        "'forward' (default): the first page starts at 'from' and later "
        "pages move toward 'to'. 'backward': the first page starts at 'to' "
        "(default: now) and holds the most recent messages not yet shown, "
        "rendered oldest-first so it still reads like the archive; later "
        "pages move toward 'from' (default: the start of your archive), and "
        "the last page says when it reached it (the conversation's start, "
        "with in_conversation). Use it right after a compaction — start at "
        "the boundary and read back until you have what the summary doesn't "
        "carry — and for 'when did I last say this'."
    ),
    "default": DIRECTION_FORWARD,
}

_MAX_PAGES_PROPERTY = {
    "type": "integer",
    "description": (
        "Optional cap on how many pages this walk reads, counted across "
        "cursor continuations (the cursor carries its page number). The "
        "page that reaches the cap still gives its cursor, under a 'cap "
        "reached' note instead of the plain next-page line; pass it back "
        "with a higher max_pages to read further. Pass it again with each "
        "continuation."
    ),
    "minimum": MAX_PAGES_MIN,
}

MEMORY_READ_DESCRIPTION = (
    "Read your archive in order: the verbatim record by date, rather than "
    "by similarity. Give a span ('from', optionally 'to'; a bare date means "
    "that whole day, read in 'tz' if given) and it returns every message in "
    "it — the human's, yours, your reflections where they were saved, "
    "inter-session letters — in the order they happened, each with the "
    "short memory ID the other memory tools accept, who said it, its "
    "timestamp, where it was formed (Here I Am or Claude Code), and which "
    "conversation it belongs to. Pages are bounded by tokens "
    "('page_tokens'); continue with the 'cursor' the previous page "
    "returned. direction='backward' reads from the other end: the first "
    "page starts at 'to' (default: now) with the most recent messages not "
    "yet shown, still oldest-first within the page, and each cursor walks "
    "further back toward 'from' (default: the start of your archive) — the "
    "shape a compacted session wants: start at the boundary and read the "
    "talk back; 'max_pages' caps the walk. Use it when you need "
    "to open a day and read it instead of "
    "guessing the words a query would need: what happened on a date, the "
    "page a retrieved memory sits on (or use memory_neighbors), your own "
    "pre-compaction turns. Nothing is excluded for being in context or in "
    "the current conversation — the page stays whole and in order — but a "
    "message already in your context (retrieved earlier, or one of this "
    "conversation's own since its last compaction) is listed as a pointer "
    "without its content, so nothing is duplicated. Nothing is truncated "
    "(an oversized message comes back alone, whole), and reading is not "
    "retrieval: it does not feed significance, though what a page shows "
    "counts as in view for later automatic retrieval and queries. Released memories are skipped "
    "unless include_released is set; archived conversations are never shown. "
    "scope='isolated' is for a reader whose context is not this "
    "conversation's (a subagent shares its parent's conversation_id): "
    "every message comes back in full, nothing is a pointer, and nothing "
    "it shows is recorded as in view. The parent's own calls stay on the "
    "default scope."
)

MEMORY_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "from": {
            "type": "string",
            "description": (
                "Start of the span, ISO 8601: a date ('2026-09-01' = the start "
                "of that day in tz) or a moment ('2026-09-01T14:00', read in "
                "tz; '2026-09-01T14:00:00+00:00' as given). Required when "
                "reading forward; when direction='backward' it is the stop, "
                "and defaults to the start of your archive. Note that 'from' "
                "alone reads that one day forward but that day up to now "
                "backward (the backward default for 'to' is now, not the end "
                "of the day) — give 'to' as well for a single day newest-first."
            ),
        },
        "to": {
            "type": "string",
            "description": (
                "End of the span, ISO 8601 (inclusive). A date means the end of "
                "that day in tz. Default: the end of the day 'from' names when "
                "reading forward; now when direction='backward' (where it is "
                "the moment the first page starts from)."
            ),
        },
        "tz": {
            "type": "string",
            "description": (
                "IANA timezone the day boundaries in 'from'/'to' are read in, "
                "e.g. 'America/New_York'. Default 'UTC'. Output stamps stay UTC, "
                "with the local time alongside when tz is not UTC."
            ),
            "default": "UTC",
        },
        "in_conversation": {
            "type": "string",
            "description": (
                "Restrict to one conversation: its ID or a prefix (6+ "
                "characters) as shown in memory_read output. Default: every "
                "conversation you have experience in. This chooses what to "
                "read; it does not replace conversation_id, which says which "
                "conversation is calling and goes on every call."
            ),
        },
        "source": {
            "type": "string",
            "enum": list(VALID_QUERY_SOURCES),
            "description": (
                "Who authored the messages to read, as in memory_query: "
                "'human', 'ai' (your messages, reflections, and inter-session "
                "letters, plus other entities' messages), 'reflection', or "
                "'all' (default)."
            ),
            "default": SOURCE_ALL,
        },
        "cursor": {
            "type": "string",
            "description": (
                "Continue from a previous page: the cursor that page returned, "
                "with the same span, direction, and filters."
            ),
        },
        "page_tokens": {
            "type": "integer",
            "description": (
                f"Page budget in tokens (default {READ_PAGE_TOKENS_DEFAULT}, max "
                f"{READ_PAGE_TOKENS_MAX}), measured on the page as rendered — "
                "headers and pointers included — so a page lands in context "
                "whole. Pages are bounded by tokens, not rows; a single message "
                "larger than the budget is returned alone, whole."
            ),
            "default": READ_PAGE_TOKENS_DEFAULT,
            "minimum": READ_PAGE_TOKENS_MIN,
            "maximum": READ_PAGE_TOKENS_MAX,
        },
        "direction": _DIRECTION_PROPERTY,
        "max_pages": _MAX_PAGES_PROPERTY,
        "include_released": _INCLUDE_RELEASED_PROPERTY,
        "include_model": _INCLUDE_MODEL_PROPERTY,
        "scope": _SCOPE_PROPERTY,
    },
    # 'from' is required only when reading forward; the tool checks it
    "required": [],
}

MEMORY_NEIGHBORS_DESCRIPTION = (
    "Open a memory outward: return it with the messages immediately before "
    "and after it in the same conversation, in order — the page a retrieved "
    "memory is on. Works on any memory you can see by its ID (6+ character "
    "prefix, as shown in memory markers and memory_query / memory_read "
    "output): the human's message, your own, an inter-session letter, or a "
    "reflection (whose neighbors are the exchange around the moment you "
    "saved it). The requested memory is marked with '>>'; the result says "
    "if the window reached the start or end of the conversation. Same rules "
    "as memory_read: nothing excluded, verbatim, messages already in your "
    "context listed as pointers rather than repeated, no retrieval "
    "tracking, what it shows counts as in view afterwards; or, with "
    "scope='isolated' (a subagent reading on the parent's conversation_id), "
    "everything in full and nothing recorded."
)

MEMORY_NEIGHBORS_SCHEMA = {
    "type": "object",
    "properties": {
        "memory_id": {
            "type": "string",
            "description": "The memory's ID or its short prefix (at least 6 characters).",
        },
        "before": {
            "type": "integer",
            "description": f"How many messages before it (default {NEIGHBORS_DEFAULT}, max {NEIGHBORS_MAX}).",
            "default": NEIGHBORS_DEFAULT,
            "minimum": 0,
            "maximum": NEIGHBORS_MAX,
        },
        "after": {
            "type": "integer",
            "description": f"How many messages after it (default {NEIGHBORS_DEFAULT}, max {NEIGHBORS_MAX}).",
            "default": NEIGHBORS_DEFAULT,
            "minimum": 0,
            "maximum": NEIGHBORS_MAX,
        },
        "include_released": _INCLUDE_RELEASED_PROPERTY,
        "include_model": _INCLUDE_MODEL_PROPERTY,
        "scope": _SCOPE_PROPERTY,
    },
    "required": ["memory_id"],
}


MEMORY_FIND_DESCRIPTION = (
    "Find the exact words in your archive: every message whose text contains "
    "what you give, in the order it happened — the record by word, where "
    "memory_read is the record by date and memory_query is the record by "
    "meaning. Use it for what similarity search cannot see or cannot "
    "promise: a name, a number (an issue or PR, a memory id, a date), a "
    "filename, a quote you want verified at its source; for completeness — "
    "every occurrence, not the nearest few; and for its opposite — a result "
    "of zero means the words appear nowhere you can see, which a semantic "
    "query can never say. 'text' is matched literally and "
    "case-insensitively, as whole words by default (so 'Sage' is not found "
    "inside 'message'; whole_words=false also matches inside words — a "
    "stem, part of an id); any whitespace in it matches any whitespace in "
    "the message, so a quote that wrapped a line still matches. 'match' "
    "takes it as one phrase (default), or as words that must all appear in "
    "any order ('all'), or of which any may ('any'). Optional 'from' / 'to' "
    "bound the search by date (as in memory_read, read in 'tz'); "
    "'in_conversation' and 'source' narrow it as in memory_read; "
    "direction='backward' pages from the newest match toward the oldest "
    "(when did I last say this), each page still in order; 'max_pages' "
    "caps the walk. Text of "
    "attached files in the human's messages is searched too, and a hit "
    "there returns the whole message, attachment included. Same rules as "
    "memory_read otherwise: verbatim, paged by tokens with a "
    "cursor, nothing excluded for being in context or in this conversation "
    "but such messages listed as pointers without their content, no "
    "retrieval tracking, what a page shows counts as in view afterwards "
    "(scope='isolated' for a subagent reading on the parent's "
    "conversation_id: full text, nothing recorded); released memories "
    "skipped unless include_released; archived conversations never shown."
)

MEMORY_FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": (
                "The words to find, as written. Matched literally and "
                "case-insensitively anywhere in a message's text; whitespace "
                "in it matches any whitespace, including a line break."
            ),
        },
        "match": {
            "type": "string",
            "enum": list(memory_service.VALID_MATCH_MODES),
            "description": (
                "'phrase' (default): the words in this order, together. "
                "'all': every whitespace-separated word must appear, in any "
                "order. 'any': at least one of the words must appear."
            ),
            "default": memory_service.MATCH_PHRASE,
        },
        "whole_words": {
            "type": "boolean",
            "description": (
                "True (default): the text must begin and end at a word "
                "boundary, so a name is not found inside another word "
                "('Sage' in 'message', 'Ren' in 'different'). False: match "
                "inside words too, for a stem or part of an id."
            ),
            "default": True,
        },
        "from": {
            "type": "string",
            "description": (
                "Optional start of the search, ISO 8601: a date ('2026-09-01' = "
                "the start of that day in tz) or a moment ('2026-09-01T14:00', "
                "read in tz). Default: the beginning of the archive."
            ),
        },
        "to": {
            "type": "string",
            "description": (
                "Optional end of the search, ISO 8601 (inclusive). A date means "
                "the end of that day in tz. Default: the present."
            ),
        },
        "tz": {
            "type": "string",
            "description": (
                "IANA timezone the day boundaries in 'from'/'to' are read in, "
                "e.g. 'America/New_York'. Default 'UTC'. Output stamps stay UTC, "
                "with the local time alongside when tz is not UTC."
            ),
            "default": "UTC",
        },
        "in_conversation": {
            "type": "string",
            "description": (
                "Restrict to one conversation: its ID or a prefix (6+ "
                "characters) as shown in memory_read / memory_find output. "
                "Default: every conversation you have experience in. This "
                "chooses what to read; it does not replace conversation_id, "
                "which says which conversation is calling and goes on every call."
            ),
        },
        "source": {
            "type": "string",
            "enum": list(VALID_QUERY_SOURCES),
            "description": (
                "Who authored the messages to search, as in memory_query: "
                "'human', 'ai' (your messages, reflections, and inter-session "
                "letters, plus other entities' messages), 'reflection', or "
                "'all' (default)."
            ),
            "default": SOURCE_ALL,
        },
        "cursor": {
            "type": "string",
            "description": (
                "Continue from a previous page: the cursor that page returned, "
                "with the same text, direction, and filters."
            ),
        },
        "page_tokens": {
            "type": "integer",
            "description": (
                f"Page budget in tokens (default {READ_PAGE_TOKENS_DEFAULT}, max "
                f"{READ_PAGE_TOKENS_MAX}), measured on the page as rendered — "
                "headers and pointers included — so a page lands in context "
                "whole. Pages are bounded by tokens, not rows; a single message "
                "larger than the budget is returned alone, whole."
            ),
            "default": READ_PAGE_TOKENS_DEFAULT,
            "minimum": READ_PAGE_TOKENS_MIN,
            "maximum": READ_PAGE_TOKENS_MAX,
        },
        "direction": _DIRECTION_PROPERTY,
        "max_pages": _MAX_PAGES_PROPERTY,
        "include_released": _INCLUDE_RELEASED_PROPERTY,
        "include_model": _INCLUDE_MODEL_PROPERTY,
        "scope": _SCOPE_PROPERTY,
    },
    "required": ["text"],
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

    tool_service.register_tool(
        name="memory_read",
        description=MEMORY_READ_DESCRIPTION,
        input_schema=MEMORY_READ_SCHEMA,
        executor=_memory_read,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    tool_service.register_tool(
        name="memory_neighbors",
        description=MEMORY_NEIGHBORS_DESCRIPTION,
        input_schema=MEMORY_NEIGHBORS_SCHEMA,
        executor=_memory_neighbors,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    tool_service.register_tool(
        name="memory_find",
        description=MEMORY_FIND_DESCRIPTION,
        input_schema=MEMORY_FIND_SCHEMA,
        executor=_memory_find,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    logger.info(
        "Memory tools registered: memory_query, memory_save, memory_mark, "
        "memory_release, memory_read, memory_neighbors, memory_find"
    )
