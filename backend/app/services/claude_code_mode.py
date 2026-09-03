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
HUMAN/ASSISTANT/REFLECTION rows. They are never rebuilt into LLM context
(Claude Code owns the transcript), so none of the native reload/cache
invariants — tool exchange persistence, link timestamp anchoring, notes
seeds, timestamp stamping — apply. Memories, however, are stored through the
same store_memory path with the same roles, so both modes share one memory
database and retrieve each other's memories.
"""

import logging
import uuid
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
from app.services.session_helpers import calculate_significance, ensure_role_balance

logger = logging.getLogger(__name__)

# Candidates fetched per query before significance re-ranking (matches the
# native pipeline in session_manager)
FETCH_K_PER_QUERY = 10


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
        f"source={item['source']}"
    )


async def retrieve_for_prompt(
    db: AsyncSession,
    conversation: Conversation,
    entity: EntityConfig,
    prompt: str,
) -> Tuple[str, int, str]:
    """
    Automatic semantic retrieval for a user prompt, mirroring the native
    pipeline in session_manager.process_message: search on the prompt and the
    entity's previous response, combine candidates, re-rank by
    similarity * (1 + significance), apply role balance, then skip
    already-retrieved memories without backfill.

    Selected memories get update_retrieval_count (link + times_retrieved), so
    deliberate significance dynamics work identically to native mode, and the
    DB-backed link set is the dedup record — no in-memory session required.

    Returns (rendered context block, number of memories retrieved, compact
    summary). The summary is one header plus one line per memory (id, date,
    provenance, first-line snippet); the hook prints it in place of the full
    block when the block would blow the inline hook-output budget and has to
    be spilled to a file — so the entity still sees inline *what* surfaced
    and *where* the verbatim text went. Empty strings when memory is
    unconfigured or nothing qualifies.
    """
    entity_index = entity.index_name
    if not memory_service.is_configured(entity_id=entity_index):
        return "", 0, ""

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

    assistant_query = await _last_assistant_content(db, conversation.id)

    user_candidates = await memory_service.search_memories(
        query=prompt,
        top_k=FETCH_K_PER_QUERY,
        exclude_conversation_id=conversation.id,
        exclude_conversation_after=conversation.last_compacted_at,
        entity_id=entity_index,
    )
    logger.info(f"[CC MODE] User query retrieved {len(user_candidates)} candidates")
    assistant_candidates = []
    if assistant_query:
        assistant_candidates = await memory_service.search_memories(
            query=assistant_query,
            top_k=FETCH_K_PER_QUERY,
            exclude_conversation_id=conversation.id,
            exclude_conversation_after=conversation.last_compacted_at,
            entity_id=entity_index,
        )
        logger.info(f"[CC MODE] Assistant query retrieved {len(assistant_candidates)} candidates")

    # Combine, keeping the higher score for duplicates
    candidates_by_id: Dict[str, Dict[str, Any]] = {}
    user_candidate_ids = set(c["id"] for c in user_candidates)
    assistant_candidate_ids = set(c["id"] for c in assistant_candidates)
    for candidate in user_candidates + assistant_candidates:
        cid = candidate["id"]
        if cid not in candidates_by_id or candidate["score"] > candidates_by_id[cid]["score"]:
            candidates_by_id[cid] = candidate
    for cid, candidate in candidates_by_id.items():
        if cid in user_candidate_ids and cid in assistant_candidate_ids:
            candidate["_source"] = "both"
        elif cid in user_candidate_ids:
            candidate["_source"] = "user"
        else:
            candidate["_source"] = "assistant"
    logger.info(f"[CC MODE] Combined {len(candidates_by_id)} unique candidates from both queries")

    # Enrich with full content and significance
    enriched: List[Dict[str, Any]] = []
    now = datetime.utcnow()
    for candidate in candidates_by_id.values():
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
            })
        except Exception as e:
            logger.error(f"[CC MODE] Error processing candidate {candidate.get('id')}: {e}")

    enriched.sort(key=lambda x: x["combined_score"], reverse=True)
    if settings.memory_role_balance_enabled:
        top_candidates = ensure_role_balance(enriched, top_k)
    else:
        top_candidates = enriched[:top_k]

    logger.info(
        f"[CC MODE] Re-ranked {len(enriched)} candidates by significance, "
        f"keeping top {len(top_candidates)} "
        f"(role_balance={'on' if settings.memory_role_balance_enabled else 'off'})"
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
            f"{conversation.id[:8]}... ({skipped} already in context)"
        )
        for item in selected:
            logger.info(f"[CC MODE]   [NEW] {_selection_log_detail(item)}")
    else:
        logger.info(
            f"[CC MODE] No new memories retrieved for conversation "
            f"{conversation.id[:8]}... ({skipped} already in context, "
            f"{len(candidates_by_id)} candidates)"
        )

    # Log candidates that were not selected after re-ranking (show next 5)
    unselected = enriched[top_k:top_k + 5]
    if unselected:
        total_unselected = len(enriched) - top_k
        logger.info(
            f"[CC MODE] {total_unselected} candidates not selected after "
            f"re-ranking (showing next 5):"
        )
        for item in unselected:
            logger.info(f"[CC MODE]   [NOT SELECTED] {_selection_log_detail(item)}")

    if not selected:
        return "", 0, ""

    rendered = "\n\n".join(
        format_memory_as_context_message(
            memory_id=item["mem_data"]["id"],
            content=item["mem_data"]["content"],
            created_at=item["mem_data"]["created_at"],
            role=item["mem_data"]["role"],
            origin=item["mem_data"].get("source", "native"),
        )["content"]
        for item in selected
    )
    block = (
        "[HERE I AM MEMORY RETRIEVAL] Memories from your past conversations "
        "that surfaced as relevant to this prompt:\n\n" + rendered
    )
    summary = render_retrieval_summary([item["mem_data"] for item in selected])
    return block, len(selected), summary


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
            f"{memory_role_label(mem_data['role'])} - "
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
    plain role filter is enough)."""
    result = await db.execute(
        select(Message.content)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == MessageRole.ASSISTANT,
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
) -> Message:
    """
    Persist one conversational message and store it as a memory.

    message_id lets the Stop hook reuse the transcript entry's UUID as the
    row's primary key, making assistant logging idempotent (the route checks
    for an existing row before calling this).
    """
    message = Message(
        conversation_id=conversation.id,
        role=role,
        content=content,
        created_at=datetime.utcnow(),
        token_count=token_count,
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
            role=role.value,
            content=content,
            created_at=message.created_at,
            entity_id=entity.index_name,
        )
    return message
