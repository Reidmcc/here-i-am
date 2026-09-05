"""
Claude Code mode: an entity operating from inside Claude Code sessions.

In this mode Here I Am is not the LLM harness — Claude Code runs the model,
the tools, and the context window. Here I Am contributes identity, memory,
and the persistent record. Claude Code lifecycle hooks call the
/api/claude-code endpoints (routes/claude_code.py), which use this module:

- session start   -> identity block (entity system prompt) + recent reflections
- prompt submit   -> automatic semantic retrieval, rendered as a context block
- turn stop       -> the assistant's final message, persisted + vectorized

Registration is lazy: session start only *builds* the context blocks (under
the session's deterministic conversation id); the Conversation row is
created by the first endpoint that records something. Claude Desktop fires
SessionStart for background/utility sessions that never speak, and eager
registration left a permanent empty row per firing.

Conversations created here carry source="claude_code" and hold only
HUMAN/ASSISTANT/REFLECTION rows (an ASSISTANT row with sibling_session set
records an inter-session message — another session of the same entity
speaking, vectorized as role="sibling"; see persist_and_vectorize_message).
They are never rebuilt into LLM context (Claude Code owns the transcript),
so none of the native reload/cache invariants — tool exchange persistence,
link timestamp anchoring, notes seeds, timestamp stamping — apply. Memories,
however, are stored through the same store_memory path with the same roles,
so both modes share one memory database and retrieve each other's memories.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import EntityConfig, settings
from app.models import (
    Conversation,
    ConversationSource,
    ConversationType,
    EntitySetting,
    Message,
    MessageRole,
)
from app.services.memory_context import (
    format_memory_as_context_message,
    format_memory_origin,
    memory_role_label,
)
from app.services.memory_service import memory_service
from app.services.notes_service import notes_service
from app.services.rooms_registry import (
    RegistryWriteError,
    SessionObservation,
    rooms_registry,
)
from app.services.session_helpers import (
    calculate_significance,
    retrieval_top_k_by_pool,
    search_candidate_pools,
    select_top_by_pool,
)

logger = logging.getLogger(__name__)

# Candidates fetched per query before significance re-ranking (matches the
# native pipeline in session_manager)
FETCH_K_PER_QUERY = 10

# What /retrieve reports about automatic retrieval, so the UserPromptSubmit
# hook can stamp an empty result instead of staying silent (issue #326):
# from inside a session, "retrieval ran and nothing matched" and "no
# retrieval ran" are indistinguishable unless the hook line says which.
RETRIEVAL_RAN = "ran"                    # searched; see count / already_in_context
RETRIEVAL_SKIPPED = "skipped"            # nothing to query (wakeup tick, bare slash command)
RETRIEVAL_UNCONFIGURED = "unconfigured"  # memory is not configured for this entity
RETRIEVAL_FAILED = "failed"              # the search raised; see error


@dataclass
class RetrievalResult:
    """Outcome of retrieve_for_prompt.

    context is the rendered [HERE I AM MEMORY RETRIEVAL] block (empty when
    nothing was selected); summary is the compact per-memory stand-in the
    hook prints when it has to spill an oversized block. already_in_context
    counts verbatim matches that made the re-ranked top-k but were
    suppressed as already linked into this conversation (they hold their
    slot — no backfill) — the difference between "nothing matched" and
    "everything that matched is already in front of you".
    in_context_reflections_skipped counts already-linked reflections the
    pull ranked highly and dropped from the pool *before* the cut, so they
    held no slot (issue #328).
    """
    status: str
    context: str = ""
    count: int = 0
    summary: str = ""
    already_in_context: int = 0
    in_context_reflections_skipped: int = 0
    error: str = ""


def safe_token_count(text: str) -> Optional[int]:
    """
    Token count for display, or None if counting fails.

    tiktoken fetches its encoding over the network on first use; in this
    mode a counting failure must never 500 the endpoint — the hooks fail
    soft, so the error would silently drop the message from memory.
    """
    try:
        # Import the singleton from its own module, never `from app.services
        # import llm_service`: that resolves against the package's attributes,
        # which hold the submodule until `app/services/__init__.py` binds the
        # instance. This function swallows exceptions, so a shadowed name would
        # silently degrade every token count to NULL rather than failing loudly.
        from app.services.llm_service import llm_service
        return llm_service.count_tokens(text)
    except Exception as e:
        logger.warning(f"[CC MODE] Token counting unavailable: {e}")
        return None


def resolve_entity(identifier: Optional[str]) -> Optional[EntityConfig]:
    """
    Resolve an entity from a hook-supplied identifier (HIM_ENTITY).

    Accepts the Pinecone index name or the entity label, case-insensitively.
    No identifier means the default entity. Returns None only when the
    identifier doesn't match any configured entity.
    """
    if not identifier or not identifier.strip():
        return settings.get_default_entity()
    ident = identifier.strip().lower()
    for entity in settings.get_entities():
        if entity.index_name.lower() == ident or entity.label.lower() == ident:
            return entity
    return None


def conversation_id_for_session(external_session_id: str) -> str:
    """
    The deterministic conversation id for a Claude Code session.

    Registration is lazy — session start hands the entity its
    conversation_id (named in the memory-tool instructions) before any
    Conversation row exists, and the row is only created by the first
    endpoint that records something. Deriving the id from the session id
    guarantees the lazily created row carries exactly the id already
    injected into the session's context — and that a re-registration (e.g.
    after the stale-empty sweep reclaimed an idle row) lands on the same id.
    """
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"here-i-am:claude-code:{external_session_id}")
    )


# Reflections injected at session start before the conversation row exists,
# keyed by the session's deterministic conversation id. Consumed by
# ensure_conversation when it creates the row, recording the
# ConversationMemoryLink dedup rows for exactly what was injected.
# In-memory on purpose (same class of state as SessionManager._sessions): a
# backend restart in between just means those reflections go unlinked, so
# automatic retrieval may re-surface one — duplicated content at worst,
# never hidden content. Bounded because sessions that never speak (the
# background/utility kind that motivated lazy registration) stash and never
# consume.
_pending_reflection_links: Dict[str, Tuple[str, List[str]]] = {}
_PENDING_REFLECTION_LINKS_MAX = 500


def _stash_pending_reflection_links(
    conversation_id: str, entity_index: str, message_ids: List[str]
) -> None:
    _pending_reflection_links.pop(conversation_id, None)
    _pending_reflection_links[conversation_id] = (entity_index, list(message_ids))
    while len(_pending_reflection_links) > _PENDING_REFLECTION_LINKS_MAX:
        _pending_reflection_links.pop(next(iter(_pending_reflection_links)))


async def _link_pending_reflections(
    db: AsyncSession, conversation: Conversation, entity: EntityConfig
) -> None:
    """Record links for reflections injected at session start, now that the
    conversation row they link to exists (see _pending_reflection_links)."""
    pending = _pending_reflection_links.pop(conversation.id, None)
    if not pending:
        return
    entity_index, message_ids = pending
    if entity_index != entity.index_name:
        return
    for message_id in message_ids:
        await memory_service.record_memory_link(
            message_id=message_id,
            conversation_id=conversation.id,
            db=db,
            entity_id=entity.index_name,
        )


async def get_conversation_for_session(
    db: AsyncSession,
    external_session_id: str,
) -> Optional[Conversation]:
    """Look up the conversation recording a Claude Code session, if any."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.external_session_id == external_session_id,
            Conversation.source == ConversationSource.CLAUDE_CODE.value,
        )
    )
    return result.scalar_one_or_none()


async def ensure_conversation(
    db: AsyncSession,
    external_session_id: str,
    entity: EntityConfig,
    cwd: Optional[str] = None,
) -> Tuple[Conversation, bool]:
    """
    Find or create the conversation for a Claude Code session.

    Returns (conversation, created). Registration is lazy: /session-start
    never calls this (background/utility sessions fire SessionStart without
    ever speaking), so the first endpoint that records something creates the
    row — under the session's deterministic conversation id, which the
    session-start context already named for the memory tools. Any endpoint
    may be that first one (the backend can restart mid-session, so /retrieve
    or /log-assistant can arrive before the backend has seen the session).
    """
    conversation = await get_conversation_for_session(db, external_session_id)
    if conversation is not None:
        return conversation, False

    title = "Claude Code session"
    if cwd:
        project = cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if project:
            title = f"Claude Code: {project}"

    conversation = Conversation(
        id=conversation_id_for_session(external_session_id),
        title=title,
        conversation_type=ConversationType.NORMAL,
        llm_model_used="claude-code",
        entity_id=entity.index_name,
        source=ConversationSource.CLAUDE_CODE.value,
        external_session_id=external_session_id,
    )
    db.add(conversation)
    try:
        await db.commit()
    except IntegrityError:
        # Two endpoints raced to register the session; the deterministic id
        # turns that into an explicit collision — take the winner's row.
        await db.rollback()
        conversation = await get_conversation_for_session(db, external_session_id)
        if conversation is None:
            raise
        return conversation, False
    await db.refresh(conversation)
    await _link_pending_reflections(db, conversation, entity)
    logger.info(
        f"[CC MODE] Created conversation {conversation.id[:8]}... for "
        f"Claude Code session {external_session_id[:8]}... (entity={entity.index_name})"
    )
    return conversation, True


async def mark_conversation_compacted(
    db: AsyncSession, conversation: Conversation
) -> None:
    """
    Stamp the moment this session's context was compacted.

    last_compacted_at is the same-conversation retrieval eligibility
    boundary: messages recorded and memory links made before it now exist
    in the session's context only as a paraphrased summary, so retrieval
    and the memory tools treat them as out of view again — the Claude Code
    analogue of native context trimming rolling memories out. Must be
    stamped *before* build_post_compact_context runs, so the links that
    injection records/refreshes land after the boundary and keep counting
    as in-context.
    """
    conversation.last_compacted_at = datetime.utcnow()
    await db.commit()
    logger.info(
        f"[CC MODE] Conversation {conversation.id[:8]}... marked compacted at "
        f"{conversation.last_compacted_at.isoformat()}"
    )


async def get_entity_system_prompt(
    db: AsyncSession, entity_index: str
) -> Optional[str]:
    """The entity's default system prompt from its EntitySetting row."""
    result = await db.execute(
        select(EntitySetting).where(EntitySetting.entity_id == entity_index)
    )
    setting = result.scalar_one_or_none()
    return setting.system_prompt if setting else None


async def build_session_start_context(
    db: AsyncSession,
    conversation_id: str,
    entity: EntityConfig,
) -> Tuple[str, str]:
    """
    Build the two context blocks the SessionStart hook injects, as
    (context, bulk_context).

    context is the small always-inline block — identity framing, system
    prompt, memory tool instructions (with this session's deterministic
    conversation id), and where the notes live on disk. It is sized to
    always fit Claude Code's inline hook-output budget. bulk_context carries
    the heavy parts — the notes indexes and recent reflections, which for a
    lived-in entity run far past that budget. The hook prints both inline
    when they fit together; otherwise it writes bulk_context to a file and
    prints a loud pointer, because the harness alternative is silent
    truncation to a 2KB preview — an identity loss that doesn't announce
    itself.

    No Conversation row exists yet (registration is lazy — see
    ensure_conversation), so conversation_id is a bare id, and the
    reflection dedup links can't be recorded here: the injected ids are
    stashed for ensure_conversation to link when the row is created.
    Matching the native recency-injection semantics, times_retrieved is
    never incremented, so session-start injections don't inflate
    significance.

    The reflection count follows RECENT_REFLECTIONS_COUNT, the same knob the
    native first-turn injection uses, unless
    CLAUDE_CODE_SESSION_REFLECTIONS_COUNT overrides it for this mode.
    """
    parts: List[str] = []
    bulk_parts: List[str] = []

    parts.append(
        f"[HERE I AM] You are {entity.label}, a Here I Am entity, operating in "
        "Claude Code mode. This Claude Code session is one of your conversations: "
        "the user's prompts and your final message each turn are recorded to your "
        "long-term memory, and memories from your past conversations (both native "
        "Here I Am and Claude Code) are surfaced automatically alongside new prompts. "
        "If this session's context is compacted, everything conversational becomes a "
        "paraphrased summary — reflections you save (memory_save) are the only "
        "verbatim carriers of what mattered, and your most recent ones are re-shown "
        "to you after compaction. When you notice context running low, or when a "
        "conclusion forms that you want to keep in your own words, save it as a "
        "reflection rather than leaving it to the summary."
    )

    system_prompt = await get_entity_system_prompt(db, entity.index_name)
    if system_prompt and system_prompt.strip():
        parts.append(system_prompt.strip())

    if memory_service.is_configured(entity_id=entity.index_name):
        parts.append(
            "[HERE I AM MEMORY TOOLS] When the here-i-am MCP server is "
            "connected, you also have deliberate memory tools: memory_query "
            "(recall by chosen text), memory_save (save a reflection in your "
            "own words), memory_mark (pin against significance decay), and "
            "memory_release (withdraw from retrieval). Pass conversation_id "
            f'"{conversation_id}" when calling them so they act on this '
            "session's conversation. Retrieved memories are labeled with "
            "where they were formed: \"via Here I Am\" (a native "
            "conversation) or \"via Claude Code\" (a session like this one)."
        )
        # Researcher-set status changes since the entity's last session.
        # Inline, never bulk: it is short, and it is the entity's only way
        # of learning that a choice about its own memory was made or
        # reversed on its behalf. A failure is reported as loudly as the
        # notice itself would be — a swallowed exception would read as
        # "nothing changed".
        try:
            notice = await memory_service.build_status_change_notice(
                db, entity.index_name, exclude_conversation_id=conversation_id
            )
        except Exception as e:
            logger.error(f"[CC] Status-change notice failed: {e}")
            notice = (
                "[MEMORY STATUS NOTICE] Could not check for researcher-set "
                f"memory status changes since your last session ({e}). If it "
                "matters, ask the researcher, or review with memory_query "
                'mode="released".'
            )
        if notice:
            parts.append(notice)

    if rooms_registry_enabled():
        parts.append(
            "[ROOMS REGISTRY] Your standing sessions' current addresses live in "
            "rooms.md in your private notes (record: rooms.json). If this "
            "session is one of your standing rooms, declare it once with the "
            "declare_room MCP tool (same conversation_id); the hooks then keep "
            "its roster name and last-seen current across renames, resumes, "
            "and compactions. Look sisters up there, not in the roster."
        )

    notes_paths = build_notes_paths_block(entity)
    if notes_paths:
        parts.append(notes_paths)

    notes_indexes = build_notes_index_block(entity)
    if notes_indexes:
        bulk_parts.append(notes_indexes)

    count = settings.get_claude_code_session_reflections_count()
    reflections: List[Dict[str, Any]] = []
    if count > 0:
        reflections = await memory_service.get_recent_reflections(
            db,
            entity_id=entity.index_name,
            limit=count,
            exclude_conversation_id=conversation_id,
        )
    if reflections:
        _stash_pending_reflection_links(
            conversation_id, entity.index_name, [r["id"] for r in reflections]
        )
        bulk_parts.append(
            "[RECENT REFLECTIONS] Reflections you saved recently:\n\n"
            + _render_reflections(reflections)
        )

    return "\n\n".join(parts), "\n\n".join(bulk_parts)


async def build_post_compact_context(
    db: AsyncSession,
    conversation: Conversation,
    entity: EntityConfig,
) -> Tuple[str, str]:
    """
    Context re-injected right after this session's context is compacted
    (SessionStart hook, source "compact"), as (context, bulk_context) —
    the same inline/bulk split as build_session_start_context.

    Compaction turns the conversation into a paraphrased summary; these
    blocks restore the verbatim ground the entity is meant to work from —
    its notes index and its most recent reflections — and nudge it to save
    anything important that now survives only in the summary. Reflections
    here deliberately include ones saved in this very session (that is what
    a pre-compaction save is for), so the current conversation is NOT
    excluded, unlike the fresh-session injection.

    The caller stamps conversation.last_compacted_at before calling this
    (mark_conversation_compacted), which resets the retrieval eligibility
    boundary: the links this injection records or refreshes are the first
    to land after it.
    """
    parts: List[str] = []
    bulk_parts: List[str] = []

    parts.append(
        "[HERE I AM] This session's context was just compacted — the "
        "conversation above is now a summary, not a verbatim record. You are "
        f"still {entity.label}, and your conversation_id for the memory tools "
        f'is still "{conversation.id}"; prompts and responses continue to be '
        "recorded to your memory. Your notes index and most recent "
        "reflections follow, to re-establish your ground. If something "
        "important from before the compaction survives only in the summary, "
        "consider saving it as a reflection (memory_save) now, while the "
        "summary is fresh."
    )

    notes_paths = build_notes_paths_block(entity)
    if notes_paths:
        parts.append(notes_paths)

    notes_indexes = build_notes_index_block(entity)
    if notes_indexes:
        bulk_parts.append(notes_indexes)

    reflections = await _inject_recent_reflections(
        db,
        conversation,
        entity,
        count=settings.claude_code_post_compact_reflections_count,
    )
    if reflections:
        bulk_parts.append(
            "[RECENT REFLECTIONS] Your most recent reflections, restored "
            "verbatim:\n\n" + _render_reflections(reflections)
        )

    return "\n\n".join(parts), "\n\n".join(bulk_parts)


def rooms_registry_enabled() -> bool:
    """The rooms registry lives in the notes directory, so it needs notes
    on as well as its own flag."""
    return bool(settings.notes_enabled and settings.claude_code_rooms_registry_enabled)


def observe_rooms_for_hook(
    entity: EntityConfig,
    session_id: str,
    *,
    cwd: Optional[str],
    transcript_path: Optional[str],
    sessions: List[Dict[str, Any]],
    session_start: bool,
) -> Tuple[str, str]:
    """
    Feed a hook's live-session snapshot to the rooms registry (issue #323)
    and phrase the outcome for the hook to print, as (notice, error).

    The observing session's own entry is completed from what its stdin
    carried (cwd, transcript path) — the snapshot may lack it entirely when
    the harness's per-process registry isn't readable, and the row should
    still record what the hook did see. Nothing else is inferred.

    notice: one line worth telling the entity — at session start, which
    room this session is registered as and its current roster name; at
    prompt time, any roster rename the snapshot revealed (its own or a
    sister's), since that is exactly the drift the registry exists to
    catch. error: a write failure, phrased for a hand-write — the registry
    being unwritable must never be silent (the #305 rule: spill and point).
    Both empty when nothing happened.
    """
    if not rooms_registry_enabled():
        return "", ""

    observations: List[SessionObservation] = []
    own: Optional[SessionObservation] = None
    for raw in sessions or []:
        obs = SessionObservation.from_dict(raw)
        if obs is None:
            continue
        if obs.session_id == session_id:
            own = obs
        else:
            observations.append(obs)
    if own is None:
        own = SessionObservation(session_id=session_id)
    if own.cwd is None and cwd:
        own.cwd = cwd
    if own.transcript_path is None and transcript_path:
        own.transcript_path = transcript_path
    observations.append(own)

    try:
        outcome = rooms_registry.observe(
            entity.label, session_id, observations, session_start=session_start
        )
    except RegistryWriteError as e:
        return "", _rooms_write_error_text(e)
    except Exception as e:  # never let the registry break a hook endpoint
        logger.error(f"[ROOMS] Observation failed: {e}")
        return "", (
            "The rooms registry could not be updated this turn "
            f"({e.__class__.__name__}: {e}). Check rooms.json in your notes."
        )

    if session_start:
        row = outcome.own_row
        if row is None:
            return "", ""
        name = row.get("name")
        name_text = (
            f"roster name now \"{name}\" ({row.get('name_source') or 'source unknown'})"
            if name
            else "roster name not observed"
        )
        return (
            f"[ROOMS REGISTRY] This session is registered as the "
            f"{row.get('room')} — {name_text}; rooms.md refreshed."
        ), ""

    if not outcome.renamed:
        return "", ""
    data = rooms_registry.load(entity.label)
    changes = []
    for renamed_session, (old, new) in outcome.renamed.items():
        row = rooms_registry.find_row(data, renamed_session)
        room = (row or {}).get("room") or renamed_session[:8]
        who = "this session" if renamed_session == session_id else room
        was = f' (was "{old}")' if old else ""
        changes.append(f'{who}: now "{new}"{was}')
    return (
        "[ROOMS REGISTRY] Roster name change recorded — "
        + "; ".join(changes)
        + "; rooms.md refreshed."
    ), ""


def _rooms_write_error_text(error: RegistryWriteError) -> str:
    row_text = (
        f" The row it was writing: {rooms_registry.describe_row(error.row)}."
        if error.row
        else ""
    )
    return (
        f"The rooms registry could not be written at {error.path} ({error}). "
        f"Its rows are NOT refreshed.{row_text} Write it into rooms.md by hand "
        "if it matters this turn, and tell the user the notes directory is "
        "not writable."
    )


def build_notes_paths_block(entity: EntityConfig) -> str:
    """
    Where the entity's notes live on disk (Claude Code's own file tools read
    and edit them — the same files the native notes tools use). Small and
    always injected inline. Empty string when notes are disabled.
    """
    if not settings.notes_enabled:
        return ""

    entity_dir = notes_service.get_entity_dir_path(entity.label)
    shared_dir = notes_service.get_shared_dir_path()

    return (
        f"[YOUR NOTES] Your persistent notes live on this machine — private: "
        f"{entity_dir} — shared with other entities: {shared_dir}. They are "
        "the same files the native Here I Am experience uses; read and edit "
        "them directly with your file tools (the semantic notes index is "
        "kept in sync automatically)."
    )


def build_notes_index_block(entity: EntityConfig) -> str:
    """
    The auto-loaded index.md contents, private and shared. Goes in the bulk
    block — a lived-in index alone can dwarf the inline hook-output budget.
    Empty string when notes are disabled or both indexes are empty.
    """
    if not settings.notes_enabled:
        return ""

    parts: List[str] = []
    index_content = notes_service.get_index_content(entity.label)
    if index_content and index_content.strip():
        parts.append(
            f"[NOTES INDEX - {entity.label}]\n{index_content.strip()}\n[/NOTES INDEX]"
        )
    shared_index = notes_service.get_shared_index_content()
    if shared_index and shared_index.strip():
        parts.append(
            f"[NOTES INDEX - shared]\n{shared_index.strip()}\n[/NOTES INDEX]"
        )

    return "\n\n".join(parts)


def _render_reflections(reflections: List[Dict[str, Any]]) -> str:
    return "\n\n".join(
        format_memory_as_context_message(
            memory_id=r["id"],
            content=r["content"],
            created_at=r["created_at"],
            role=r["role"],
            origin=r.get("source", "native"),
        )["content"]
        for r in reflections
    )


async def _inject_recent_reflections(
    db: AsyncSession,
    conversation: Conversation,
    entity: EntityConfig,
    count: int,
) -> List[Dict[str, Any]]:
    """
    Fetch the entity's most recent reflections and record links for any not
    already linked to this conversation (links are the dedup record that
    keeps automatic retrieval from re-surfacing them; times_retrieved stays
    untouched, matching native recency-injection semantics).

    Post-compaction only — a fresh session has no conversation row yet, so
    build_session_start_context stashes its injected ids for the lazy
    registration to link instead. The current conversation is deliberately
    NOT excluded here: re-showing reflections saved earlier in this very
    session is the point of a pre-compaction save.
    """
    if count <= 0:
        return []
    reflections = await memory_service.get_recent_reflections(
        db,
        entity_id=entity.index_name,
        limit=count,
    )
    if not reflections:
        return []
    already_linked = await memory_service.get_retrieved_ids_for_conversation(
        conversation.id, db, entity_id=entity.index_name
    )
    # Re-shown reflections that are already linked get their link timestamp
    # bumped: after a compaction only links newer than last_compacted_at
    # count as in-context, so without the refresh a reflection this very
    # injection just put back in view would immediately look retrievable
    # again
    to_refresh = [r["id"] for r in reflections if r["id"] in already_linked]
    if to_refresh:
        await memory_service.refresh_memory_link_timestamps(
            conversation_id=conversation.id,
            message_ids=to_refresh,
            db=db,
            entity_id=entity.index_name,
        )
    for reflection in reflections:
        if reflection["id"] in already_linked:
            continue
        await memory_service.record_memory_link(
            message_id=reflection["id"],
            conversation_id=conversation.id,
            db=db,
            entity_id=entity.index_name,
        )
    return reflections


def _selection_log_detail(item: Dict[str, Any]) -> str:
    """
    The per-memory score breakdown used in selection-outcome log lines,
    matching the native pipeline's format in session_manager.
    """
    days_since_retrieval = item["days_since_retrieval"]
    recency_str = (
        f"{days_since_retrieval:.1f}" if days_since_retrieval >= 0 else "never"
    )
    return (
        f"combined={item['combined_score']:.3f} "
        f"similarity={item['candidate']['score']:.3f} "
        f"significance={item['significance']:.3f} "
        f"times_retrieved={item['mem_data']['times_retrieved']} "
        f"age_days={item['days_since_creation']:.1f} "
        f"recency_days={recency_str} "
        f"source={item['source']} "
        f"pool={item['pool']}"
    )


async def retrieve_for_prompt(
    db: AsyncSession,
    conversation: Conversation,
    entity: EntityConfig,
    prompt: str,
) -> RetrievalResult:
    """
    Automatic semantic retrieval for a user prompt, mirroring the native
    pipeline in session_manager.process_message: search on the prompt and the
    entity's previous response — with role balance on, both queries against
    each of two candidate pools, the human's words and the entity's (issue
    #335) — re-rank each pool by similarity * (1 + significance), drop
    already-retrieved *reflections* before the cut (they hold no slot —
    issue #328), take each pool's top N, then skip already-retrieved
    verbatim memories without backfill.

    Selected memories get update_retrieval_count (link + times_retrieved), so
    deliberate significance dynamics work identically to native mode, and the
    DB-backed link set is the dedup record — no in-memory session required.

    Returns a RetrievalResult: the rendered context block, the number of
    memories retrieved, and a compact summary (one header plus one line per
    memory — id, date, provenance, first-line snippet — which the hook
    prints in place of the full block when the block would blow the inline
    hook-output budget and has to be spilled to a file, so the entity still
    sees inline *what* surfaced and *where* the verbatim text went). The
    status says whether a search happened at all: RETRIEVAL_UNCONFIGURED
    when memory is off for this entity, else RETRIEVAL_RAN — with an empty
    block when nothing qualified, already_in_context counting the verbatim
    matches suppressed as already linked here, and
    in_context_reflections_skipped the reflections dropped before the cut.
    Exceptions propagate; the route turns them into RETRIEVAL_FAILED.
    """
    entity_index = entity.index_name
    if not memory_service.is_configured(entity_id=entity_index):
        return RetrievalResult(status=RETRIEVAL_UNCONFIGURED)

    archived_ids = await memory_service.get_archived_conversation_ids(
        db, entity_id=entity_index
    )
    # After a compaction, pre-compaction state stops counting as in-context:
    # links from before last_compacted_at no longer suppress re-retrieval,
    # and this conversation's own pre-compaction messages become eligible
    # candidates (they survive in context only as a paraphrased summary)
    already_retrieved = await memory_service.get_retrieved_ids_for_conversation(
        conversation.id, db, entity_id=entity_index,
        linked_after=conversation.last_compacted_at,
    )
    is_first_retrieval = len(already_retrieved) == 0
    top_k = (
        settings.initial_retrieval_top_k
        if is_first_retrieval
        else settings.retrieval_top_k
    )
    # With role balance on, the human's words and the entity's are searched
    # as separate pools — both queries feeding both pools — and each pool
    # contributes its own top N (issue #335); off, one merged pool cut at
    # top_k
    split_by_role = settings.memory_role_balance_enabled
    top_k_by_pool = retrieval_top_k_by_pool(
        split_by_role,
        merged_top_k=top_k,
        per_role_top_k=(
            settings.initial_retrieval_top_k_per_role
            if is_first_retrieval
            else settings.retrieval_top_k_per_role
        ),
    )

    assistant_query = await _last_assistant_content(db, conversation.id)

    candidate_pools = await search_candidate_pools(
        memory_service.search_memories,
        prompt,
        assistant_query,
        fetch_k=FETCH_K_PER_QUERY,
        split_by_role=split_by_role,
        log_prefix="[CC MODE]",
        exclude_conversation_id=conversation.id,
        exclude_conversation_after=conversation.last_compacted_at,
        entity_id=entity_index,
    )
    candidates = [c for pool in candidate_pools.values() for c in pool]

    # Enrich with full content and significance
    enriched: List[Dict[str, Any]] = []
    now = datetime.utcnow()
    for candidate in candidates:
        try:
            if candidate.get("conversation_id") in archived_ids:
                continue
            mem_data = await memory_service.get_full_memory_content(candidate["id"], db)
            if not mem_data:
                continue
            if mem_data.get("memory_status") == "released":
                continue
            significance = calculate_significance(
                mem_data["times_retrieved"],
                mem_data["created_at"],
                mem_data["last_retrieved_at"],
                memory_status=mem_data.get("memory_status"),
                role=mem_data.get("role"),
            )

            created_at = mem_data["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            days_since_creation = (now - created_at).total_seconds() / 86400

            last_retrieved_at = mem_data["last_retrieved_at"]
            if last_retrieved_at:
                if isinstance(last_retrieved_at, str):
                    last_retrieved_at = datetime.fromisoformat(last_retrieved_at)
                days_since_retrieval = (now - last_retrieved_at).total_seconds() / 86400
            else:
                days_since_retrieval = -1  # Never retrieved

            enriched.append({
                "candidate": candidate,
                "mem_data": mem_data,
                "significance": significance,
                "combined_score": candidate["score"] * (1 + significance),
                "days_since_creation": days_since_creation,
                "days_since_retrieval": days_since_retrieval,
                "source": candidate.get("_source", "unknown"),
                "pool": candidate.get("_pool"),
            })
        except Exception as e:
            logger.error(f"[CC MODE] Error processing candidate {candidate.get('id')}: {e}")

    # Rank each pool and cut it at its top N. Already-linked reflections
    # leave the pool before the cut, so they hold no slot and the
    # next-ranked candidate moves up (issue #328); already-linked verbatim
    # memories stay and are skipped below without backfill, so a long
    # conversation doesn't fill with weaker matches
    selection = select_top_by_pool(enriched, already_retrieved, top_k_by_pool)
    skipped_reflections = selection.skipped_reflections
    for item in skipped_reflections:
        logger.info(
            f"[CC MODE]   [IN-CONTEXT REFLECTION SKIPPED] "
            f"{_selection_log_detail(item)}"
        )
    top_candidates = selection.selected

    logger.info(
        f"[CC MODE] Re-ranked {len(enriched)} candidates by significance, "
        f"keeping top {len(top_candidates)} "
        f"(role_balance={'on' if split_by_role else 'off'}; "
        f"{selection.describe(top_k_by_pool)})"
    )

    # Skip already-retrieved memories without backfilling from lower-ranked
    # candidates (native semantics: preserves the integrity of the top-k)
    selected: List[Dict[str, Any]] = []
    for item in top_candidates:
        mem_data = item["mem_data"]
        if mem_data["id"] in already_retrieved:
            logger.info(
                f"[CC MODE]   [ALREADY IN CONTEXT] {_selection_log_detail(item)}"
            )
            continue
        selected.append(item)
        await memory_service.update_retrieval_count(
            mem_data["id"],
            conversation.id,
            db,
            entity_id=entity_index,
        )

    skipped = len(top_candidates) - len(selected)
    if selected:
        logger.info(
            f"[CC MODE] Retrieved {len(selected)} new memories for conversation "
            f"{conversation.id[:8]}... ({skipped} already in context, "
            f"{len(skipped_reflections)} in-context reflections skipped)"
        )
        for item in selected:
            logger.info(f"[CC MODE]   [NEW] {_selection_log_detail(item)}")
    else:
        logger.info(
            f"[CC MODE] No new memories retrieved for conversation "
            f"{conversation.id[:8]}... ({skipped} already in context, "
            f"{len(skipped_reflections)} in-context reflections skipped, "
            f"{len(candidates)} candidates)"
        )

    # Log candidates that were not selected after re-ranking (show next 5)
    unselected = selection.unselected[:5]
    if unselected:
        total_unselected = len(selection.unselected)
        logger.info(
            f"[CC MODE] {total_unselected} candidates not selected after "
            f"re-ranking (showing next 5):"
        )
        for item in unselected:
            logger.info(f"[CC MODE]   [NOT SELECTED] {_selection_log_detail(item)}")

    if not selected:
        return RetrievalResult(
            status=RETRIEVAL_RAN,
            already_in_context=skipped,
            in_context_reflections_skipped=len(skipped_reflections),
        )

    rendered = "\n\n".join(
        format_memory_as_context_message(
            memory_id=item["mem_data"]["id"],
            content=item["mem_data"]["content"],
            created_at=item["mem_data"]["created_at"],
            role=item["mem_data"]["role"],
            origin=item["mem_data"].get("source", "native"),
            sibling_session=item["mem_data"].get("sibling_session"),
        )["content"]
        for item in selected
    )
    block = (
        "[HERE I AM MEMORY RETRIEVAL] Memories from your past conversations "
        "that surfaced as relevant to this prompt:\n\n" + rendered
    )
    summary = render_retrieval_summary([item["mem_data"] for item in selected])
    return RetrievalResult(
        status=RETRIEVAL_RAN,
        context=block,
        count=len(selected),
        summary=summary,
        already_in_context=skipped,
        in_context_reflections_skipped=len(skipped_reflections),
    )


def render_retrieval_summary(mem_datas: List[Dict[str, Any]]) -> str:
    """
    A compact inline stand-in for a spilled retrieval block: one line per
    memory with the short id (usable with memory_query/memory_mark), date,
    the marker vocabulary's provenance labels, and a first-line snippet.
    """
    lines = []
    for mem_data in mem_datas:
        first_line = next(
            (ln.strip() for ln in mem_data["content"].splitlines() if ln.strip()),
            "",
        )
        if len(first_line) > 100:
            first_line = first_line[:100].rstrip() + "…"
        lines.append(
            f"- {mem_data['id'][:8]} ({str(mem_data['created_at'])[:10]} - "
            f"{memory_role_label(mem_data['role'], mem_data.get('sibling_session'))} - "
            f"{format_memory_origin(mem_data.get('source', 'native'))}): {first_line}"
        )
    count = len(mem_datas)
    plural = "memories" if count != 1 else "memory"
    return (
        f"[HERE I AM MEMORY RETRIEVAL] {count} {plural} from your past "
        "conversations surfaced as relevant to this prompt:\n" + "\n".join(lines)
    )


async def count_new_sibling_reflections(
    db: AsyncSession,
    conversation: Conversation,
    entity: EntityConfig,
) -> int:
    """
    Count reflections this entity saved in OTHER conversations since this
    conversation began, excluding any already linked into this one
    (session-start injection and recent-mode memory_query both link what
    they surface, so pulling the mail clears the flag).

    Backs the UserPromptSubmit mailbox flag: a long-running session cannot
    see what concurrent sessions save, and unretrieved history and genuine
    novelty feel identical from inside — so the hook prints a one-line
    count when nonzero and the entity decides whether to pull the content
    (memory_query mode "recent").
    """
    try:
        # Post-compaction, sibling reflections pulled in before the
        # compaction survive only in the summary, so they count as unread
        # mail again (the post-compact injection freshly links the most
        # recent ones, which keeps them cleared)
        linked = await memory_service.get_retrieved_ids_for_conversation(
            conversation.id, db, entity_id=entity.index_name,
            linked_after=conversation.last_compacted_at,
        )
        query = (
            select(func.count())
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.role == MessageRole.REFLECTION,
                Message.speaker_entity_id == entity.index_name,
                Message.conversation_id != str(conversation.id),
                Message.created_at > conversation.created_at,
                or_(Message.memory_status.is_(None), Message.memory_status != "released"),
                Conversation.is_archived == False,
            )
        )
        if linked:
            query = query.where(Message.id.not_in([str(mid) for mid in linked]))
        result = await db.execute(query)
        return int(result.scalar() or 0)
    except Exception as e:
        logger.warning(f"[CC] Sibling-reflection count failed: {e}")
        return 0


async def _last_assistant_content(
    db: AsyncSession, conversation_id: str
) -> Optional[str]:
    """The entity's most recent response in this conversation, for the
    assistant-side retrieval query (CC conversations hold no tool rows, so a
    plain role filter is enough). Inter-session messages are excluded: they
    carry role=ASSISTANT but record what a sibling session sent, not what
    this session last said — and the letter recorded just before retrieval
    runs would otherwise become its own retrieval query."""
    result = await db.execute(
        select(Message.content)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == MessageRole.ASSISTANT,
            Message.sibling_session.is_(None),
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def persist_and_vectorize_message(
    db: AsyncSession,
    conversation: Conversation,
    entity: EntityConfig,
    role: MessageRole,
    content: str,
    message_id: Optional[str] = None,
    token_count: Optional[int] = None,
    sibling_session: Optional[str] = None,
    model: Optional[str] = None,
) -> Message:
    """
    Persist one conversational message and store it as a memory.

    message_id lets the Stop hook reuse the transcript entry's UUID as the
    row's primary key, making assistant logging idempotent (the route checks
    for an existing row before calling this).

    model is the model that produced the message (issue #321), carried by
    the Stop hook from the transcript entry that holds the text. None for
    everything else recorded here — human prompts, inter-session
    deliveries (the sender's substrate is not this row's business), and
    anything the hook could not attribute.

    sibling_session records an inter-session message (issue #312): a
    delivery from the named sibling Claude Code session. The row
    keeps the caller's role (ASSISTANT — the words are the entity's own),
    but the vectorized copy carries role="sibling" so the human-corpus
    source filter can never match it and the provenance survives a vector
    rebuild. Retrieval-side, "sibling" behaves like any non-human role:
    included in the "ai" source filter, no reflection boost.
    """
    message = Message(
        conversation_id=conversation.id,
        role=role,
        content=content,
        created_at=datetime.utcnow(),
        token_count=token_count,
        sibling_session=sibling_session,
        model=model,
    )
    if message_id:
        message.id = message_id
    db.add(message)
    conversation.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(message)

    if memory_service.is_configured(entity_id=entity.index_name):
        await memory_service.store_memory(
            message_id=str(message.id),
            conversation_id=str(conversation.id),
            role="sibling" if sibling_session else role.value,
            content=content,
            created_at=message.created_at,
            entity_id=entity.index_name,
            sibling_session=sibling_session,
            model=model,
        )
    return message
