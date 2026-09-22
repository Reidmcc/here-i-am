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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import EntityConfig, settings
from app.models import (
    Conversation,
    ConversationIdAlias,
    ConversationMemoryLink,
    ConversationSessionAlias,
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
    # The block's header sentence and one entry per memory in rank order
    # ({"id", "text": the rendered marker, "summary": its summary line}),
    # from which the hook fits the block to its stdout budget
    header: str = ""
    items: List[Dict[str, str]] = field(default_factory=list)
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
# resolve_session when it creates the row, recording the
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
    """
    Look up the conversation recording a Claude Code session, if any: the
    conversation currently keyed on the id, or the one it was a former key
    of (a fork's parent adopted the forked id and kept the old one as an
    alias — issue #357). A late or retried hook carrying either id lands
    on the same conversation.
    """
    result = await db.execute(
        select(Conversation).where(
            Conversation.external_session_id == external_session_id,
            Conversation.source == ConversationSource.CLAUDE_CODE.value,
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is not None:
        return conversation
    result = await db.execute(
        select(Conversation)
        .join(
            ConversationSessionAlias,
            ConversationSessionAlias.conversation_id == Conversation.id,
        )
        .where(
            ConversationSessionAlias.external_session_id == external_session_id,
            Conversation.source == ConversationSource.CLAUDE_CODE.value,
        )
    )
    return result.scalar_one_or_none()


async def resolve_conversation_id(
    db: AsyncSession,
    conversation_id: str,
) -> Optional[Conversation]:
    """
    The conversation an id names: the row itself, or — when a late fork
    adoption retired that id (issue #359) — the conversation it was merged
    into.

    Every MCP tool call carries a conversation id, and a session holds the
    one its identity block named at start. Late adoption moves a running
    session's recording onto the parent it turned out to continue, which
    retires the id the session was given; the entity is told the new one,
    but the old one has to keep working, both for calls already in flight
    and for context that still quotes it.
    """
    if not conversation_id:
        return None
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if conversation is not None:
        return conversation
    result = await db.execute(
        select(Conversation)
        .join(ConversationIdAlias, ConversationIdAlias.conversation_id == Conversation.id)
        .where(ConversationIdAlias.alias_id == conversation_id)
    )
    return result.scalar_one_or_none()


# How many lineage hints a hook sends, at most; the backend matches any of
# them (bounding the query and the payload)
MAX_LINEAGE_MESSAGE_IDS = 100
MAX_LINEAGE_SESSION_IDS = 50

# How many messages a row opened for this very session id may hold and
# still be treated as a fork's misfiled first steps (issue #359).
#
# The desktop app can run a fork's first hooks BEFORE it has written the
# fork's transcript and its own session record, so both lineage hints
# arrive empty and the fork is indistinguishable from a new session: a row
# gets opened for it. Seconds later the files exist and the hints are full,
# so the evidence usually arrives with the very next hook — but only if
# adoption is still willing to look. It stays willing while the row is this
# small: a couple of turns' worth of rows, which is all the evidence needs,
# and not enough to re-parent an established room on a stray hint.
LATE_ADOPTION_MAX_MESSAGES = 20


@dataclass
class SessionResolution:
    """What resolving a Claude Code session id to its conversation found."""
    conversation: Conversation
    # A brand-new row was created by this call
    created: bool = False
    # This call adopted a forked session: the conversation was keyed on
    # another session id until now, and that former id is the value here
    adopted_from: Optional[str] = None
    # This call adopted LATE (issue #359): a row had already been opened
    # for this session id, and its content was merged into the parent. The
    # value is that row's conversation id — now retired, aliased, and no
    # longer the id the session records under
    merged_from: Optional[str] = None


async def find_lineage_conversation(
    db: AsyncSession,
    entity: EntityConfig,
    *,
    prior_session_ids: Optional[List[str]] = None,
    transcript_message_ids: Optional[List[str]] = None,
    exclude_conversation_id: Optional[str] = None,
) -> Optional[Conversation]:
    """
    The conversation an unregistered session id is a continuation of, from
    the lineage hints a hook sends (issue #357), or None.

    The desktop app forks a session under a NEW Claude Code session id on
    restart, "continue", and rewind, copying the transcript. Two joins
    recover the parent, neither documented by the harness but both on the
    page, tried in order of strength:

    - `transcript_message_ids`: end-of-turn assistant entry uuids from the
      session's own transcript. A fork rewrites every entry's sessionId but
      NOT its uuid, and the Stop hook stores that uuid as the Message row's
      primary key — so any of them that is a row of one of this entity's
      Claude Code conversations names the parent directly, needing nothing
      from the desktop app.
    - `prior_session_ids`: the desktop app's own record of the session's
      former ids (its `priorCliSessionIds`), resolved through the same
      current-key-or-alias lookup as a live session id. The desktop app
      stores that list OLDEST FIRST, so it is walked in reverse: the
      immediate parent is the last element, and it is the one whose
      conversation id the forked session's context already carries.
      Taking the first match in the given order would adopt the oldest
      ancestor whenever the chain isn't already collapsed into one row —
      re-keying the room onto a conversation the entity is not calling
      with, which is #357 again in another shape. Reversing also makes
      the truncation keep the nearest ancestors rather than the oldest.

    Entity-scoped throughout: a hint that resolves to another entity's
    conversation is ignored, never adopted (the #356 no-default-entity
    rule — the fence is the entity).

    `exclude_conversation_id` is the caller's own row, for the late
    adoption of issue #359: by then the session has recorded turns of its
    own, so its transcript's newest uuids are ITS rows and would resolve to
    itself — the most recently updated candidate of all. The row being
    re-parented is never its own parent.
    """
    # Newest-last, so the cap keeps the NEWEST ids: those are the likeliest
    # rows of the nearest parent. Slicing off the front would prefer the
    # oldest — the same directional mistake as the prior-ids walk below,
    # invisible only while the hook's limit happens to equal this one.
    message_ids = [m for m in (transcript_message_ids or []) if m][
        -MAX_LINEAGE_MESSAGE_IDS:
    ]
    if message_ids:
        query = (
            select(Conversation)
            .join(Message, Message.conversation_id == Conversation.id)
            .where(
                Message.id.in_(message_ids),
                Conversation.source == ConversationSource.CLAUDE_CODE.value,
                Conversation.entity_id == entity.index_name,
            )
            .order_by(Conversation.updated_at.desc().nullslast())
            .limit(1)
        )
        if exclude_conversation_id:
            query = query.where(Conversation.id != exclude_conversation_id)
        result = await db.execute(query)
        conversation = result.scalars().first()
        if conversation is not None:
            return conversation

    for prior in list(reversed(prior_session_ids or []))[:MAX_LINEAGE_SESSION_IDS]:
        if not prior:
            continue
        conversation = await get_conversation_for_session(db, prior)
        if conversation is None or conversation.entity_id != entity.index_name:
            continue
        if exclude_conversation_id and str(conversation.id) == str(
            exclude_conversation_id
        ):
            continue
        return conversation

    return None


async def _adopt_forked_session(
    db: AsyncSession,
    conversation: Conversation,
    new_session_id: str,
) -> None:
    """
    Re-key an existing conversation onto a forked session's new id, keeping
    the old id as an alias (issue #357).

    The conversation id is left UNCHANGED — it is the id already injected
    into the forked session's context, so the memory tools keep working —
    while `external_session_id` moves to the new id (later hooks key on it
    directly) and the previous id becomes a `ConversationSessionAlias` (a
    late or retried hook still carrying it resolves to the same row). No
    messages, links, or memories move: the whole point is that they are
    already the parent's.
    """
    old_session_id = conversation.external_session_id
    conversation.external_session_id = new_session_id
    if old_session_id and old_session_id != new_session_id:
        existing = await db.get(ConversationSessionAlias, old_session_id)
        if existing is None:
            db.add(
                ConversationSessionAlias(
                    external_session_id=old_session_id,
                    conversation_id=conversation.id,
                )
            )
    try:
        await db.commit()
    except IntegrityError:
        # Two hooks of the same fork raced to adopt (the prompt and the
        # turn's Stop can overlap): the alias the other one wrote is the
        # same fact, so take its result rather than failing the endpoint.
        await db.rollback()
        await db.refresh(conversation)
        logger.info(
            f"[CC MODE] Adoption of session {new_session_id[:8]}... raced; "
            f"keeping conversation {conversation.id[:8]}..."
        )
        return
    await db.refresh(conversation)
    logger.info(
        f"[CC MODE] Conversation {conversation.id[:8]}... adopted forked "
        f"session {new_session_id[:8]}... (was {str(old_session_id)[:8]}...)"
    )


async def _move_memory_links(
    db: AsyncSession, from_conversation_id: str, to_conversation_id: str
) -> None:
    """
    Move a merged row's memory links onto the conversation it merged into,
    dropping any the target already holds for the same memory and entity.

    The link set is a dedup record — what this conversation has already
    been shown — so a duplicate pair is not just untidy: the post-compaction
    refresh walks these rows, and two of them for one memory make it count
    twice.
    """
    result = await db.execute(
        select(ConversationMemoryLink).where(
            ConversationMemoryLink.conversation_id == to_conversation_id
        )
    )
    held = {(link.message_id, link.entity_id) for link in result.scalars().all()}
    result = await db.execute(
        select(ConversationMemoryLink).where(
            ConversationMemoryLink.conversation_id == from_conversation_id
        )
    )
    for link in result.scalars().all():
        if (link.message_id, link.entity_id) in held:
            await db.delete(link)
            continue
        link.conversation_id = to_conversation_id
        held.add((link.message_id, link.entity_id))


async def _merge_into_parent(
    db: AsyncSession,
    orphan: Conversation,
    parent: Conversation,
    entity: EntityConfig,
) -> Optional[str]:
    """
    Fold a row opened for a fork that could not be recognized as one into
    the conversation it continues, and hand the parent the session (issue
    #359). Returns the retired conversation id.

    Everything the orphan holds is this session's own first turns, so it
    all moves: messages (reflections among them), memory links, and a
    compaction boundary if the fork compacted before the evidence arrived.
    The parent then takes the live session id — its own former id becoming
    a session alias, exactly as in an on-time adoption — and the retired
    conversation id becomes a `ConversationIdAlias`, because the session's
    identity block already named it and every MCP call carries one.

    The vector store's `conversation_id` metadata is repointed too: it is
    what same-conversation exclusion filters on, so memories left behind
    under the retired id would come back to the room that just said them.
    Best-effort, like every other Pinecone write here — the archive is SQL.

    Returns None when a concurrent hook of the same session won the merge
    (a prompt and the turn's Stop can overlap), leaving the caller to take
    whatever that one left behind.
    """
    orphan_id = str(orphan.id)
    session_id = orphan.external_session_id
    parent_id = str(parent.id)
    former_parent_session_id = parent.external_session_id

    result = await db.execute(
        select(Message.id).where(Message.conversation_id == orphan_id)
    )
    moved_message_ids = [row[0] for row in result.all()]
    if moved_message_ids:
        await db.execute(
            update(Message)
            .where(Message.conversation_id == orphan_id)
            .values(conversation_id=parent_id)
        )
    await _move_memory_links(db, orphan_id, parent_id)

    if orphan.last_compacted_at is not None and (
        parent.last_compacted_at is None
        or orphan.last_compacted_at > parent.last_compacted_at
    ):
        parent.last_compacted_at = orphan.last_compacted_at

    # Anything still pointing at the retired row follows it
    await db.execute(
        update(ConversationSessionAlias)
        .where(ConversationSessionAlias.conversation_id == orphan_id)
        .values(conversation_id=parent_id)
    )
    await db.execute(
        update(ConversationIdAlias)
        .where(ConversationIdAlias.conversation_id == orphan_id)
        .values(conversation_id=parent_id)
    )

    # Free the session id before the parent takes it (the column is
    # unique), and take the row out of the identity map first so the ORM
    # never cascades a delete onto the messages that just moved off it
    db.expunge(orphan)
    await db.execute(delete(Conversation).where(Conversation.id == orphan_id))
    await db.flush()

    parent.external_session_id = session_id
    if former_parent_session_id and former_parent_session_id != session_id:
        if await db.get(ConversationSessionAlias, former_parent_session_id) is None:
            db.add(
                ConversationSessionAlias(
                    external_session_id=former_parent_session_id,
                    conversation_id=parent_id,
                )
            )
    if await db.get(ConversationIdAlias, orphan_id) is None:
        db.add(ConversationIdAlias(alias_id=orphan_id, conversation_id=parent_id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.info(
            f"[CC MODE] Late adoption of session {str(session_id)[:8]}... raced; "
            "taking the other hook's result"
        )
        return None
    await db.refresh(parent)

    await memory_service.repoint_memories(
        moved_message_ids, parent_id, entity_id=entity.index_name
    )
    logger.info(
        f"[CC MODE] Late fork adoption: conversation {orphan_id[:8]}... "
        f"({len(moved_message_ids)} message(s)) merged into {parent_id[:8]}..., "
        f"which now holds session {str(session_id)[:8]}... "
        f"(was {str(former_parent_session_id)[:8]}...)"
    )
    return orphan_id


async def _try_late_adoption(
    db: AsyncSession,
    conversation: Conversation,
    external_session_id: str,
    entity: EntityConfig,
    *,
    prior_session_ids: Optional[List[str]] = None,
    transcript_message_ids: Optional[List[str]] = None,
) -> Optional[SessionResolution]:
    """
    Re-examine a row that was opened for this session id, in case the
    session is a fork whose lineage evidence had not been written yet
    (issue #359). Returns a resolution when it adopted, None otherwise.

    Adoption used to be one-shot on "unknown session id", which assumed the
    hints are available the first time a fork's hooks fire. Measured, they
    are not: the desktop app wrote the fork's transcript six seconds after
    its SessionStart and its own session record nine seconds after, while
    the prompt hook in between created the row. The evidence exists, just
    later — so the question is asked again while the row is young enough to
    be nothing but the fork's opening turns.

    Two conditions fence it. The row's id must be the one derived from THIS
    session id, which is true only of a row created for this session and
    never true of a conversation that has already adopted something (its id
    is derived from the session it was born under). And it must hold at
    most LATE_ADOPTION_MAX_MESSAGES rows. Both are read off the record —
    nothing is inferred and nothing is remembered between calls.
    """
    if not (prior_session_ids or transcript_message_ids):
        return None
    if str(conversation.id) != conversation_id_for_session(external_session_id):
        return None
    message_count = await db.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation.id)
    )
    if (message_count or 0) > LATE_ADOPTION_MAX_MESSAGES:
        return None

    parent = await find_lineage_conversation(
        db,
        entity,
        prior_session_ids=prior_session_ids,
        transcript_message_ids=transcript_message_ids,
        exclude_conversation_id=str(conversation.id),
    )
    if parent is None:
        return None

    former_parent_session_id = parent.external_session_id
    merged_from = await _merge_into_parent(db, conversation, parent, entity)
    if merged_from is None:
        # The other hook of this session got there first; whatever it left
        # keyed on this id is the answer, adoption already announced by it
        winner = await get_conversation_for_session(db, external_session_id)
        return SessionResolution(conversation=winner) if winner else None
    return SessionResolution(
        conversation=parent,
        adopted_from=former_parent_session_id,
        merged_from=merged_from,
    )


def _log_resolution(
    external_session_id: str,
    entity: EntityConfig,
    decision: str,
    conversation: Optional[Conversation],
    *,
    prior_session_ids: Optional[List[str]] = None,
    transcript_message_ids: Optional[List[str]] = None,
) -> None:
    """
    One line per session resolution: what the hook sent, and what was done
    with it (issue #359).

    Whether a fork gets adopted turns entirely on lineage hints that leave
    no trace afterwards, so a miss used to be diagnosable only by
    reconstructing file birth times against the log. The counts and the
    decision together make it a one-line read.
    """
    target = (
        f" conversation={str(conversation.id)[:8]}..."
        if conversation is not None
        else ""
    )
    logger.info(
        f"[CC MODE] Session {external_session_id[:8]}... {decision}{target} "
        f"(entity={entity.index_name}, hints: "
        f"transcript_message_ids={len(transcript_message_ids or [])}, "
        f"prior_session_ids={len(prior_session_ids or [])})"
    )


async def resolve_session(
    db: AsyncSession,
    external_session_id: str,
    entity: EntityConfig,
    *,
    cwd: Optional[str] = None,
    create: bool,
    prior_session_ids: Optional[List[str]] = None,
    transcript_message_ids: Optional[List[str]] = None,
) -> Optional[SessionResolution]:
    """
    Resolve a Claude Code session id to its conversation, adopting a fork's
    parent before ever creating a new row (issue #357).

    Order: (1) the row currently keyed on this id, or aliased to it; (2) a
    lineage match (the session is a fork — adopt its parent, re-keying onto
    this id and returning adopted_from); (3) create a fresh row, but only
    when `create` is True. `create=False` (session-start's lazy path)
    returns None when nothing resolves, so a background/utility session
    that never speaks still creates no row.
    """
    def log(decision: str, conversation: Optional[Conversation]) -> None:
        _log_resolution(
            external_session_id,
            entity,
            decision,
            conversation,
            prior_session_ids=prior_session_ids,
            transcript_message_ids=transcript_message_ids,
        )

    conversation = await get_conversation_for_session(db, external_session_id)
    if conversation is not None:
        late = await _try_late_adoption(
            db,
            conversation,
            external_session_id,
            entity,
            prior_session_ids=prior_session_ids,
            transcript_message_ids=transcript_message_ids,
        )
        if late is not None:
            log("late-adopted", late.conversation)
            return late
        log("known", conversation)
        return SessionResolution(conversation=conversation)

    parent = await find_lineage_conversation(
        db,
        entity,
        prior_session_ids=prior_session_ids,
        transcript_message_ids=transcript_message_ids,
    )
    if parent is not None:
        old_session_id = parent.external_session_id
        await _adopt_forked_session(db, parent, external_session_id)
        log("adopted", parent)
        return SessionResolution(conversation=parent, adopted_from=old_session_id)

    if not create:
        log("deferred", None)
        return None

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
        log("known", conversation)
        return SessionResolution(conversation=conversation)
    await db.refresh(conversation)
    await _link_pending_reflections(db, conversation, entity)
    log("created", conversation)
    return SessionResolution(conversation=conversation, created=True)


def adoption_notice(conversation_id: str) -> str:
    """
    The one line an adopted session is told, wherever the adoption landed
    (issue #357).

    A fork arrives holding its parent's conversation id in copied context
    and no knowledge that the harness re-keyed it. The adoption keeps that
    id valid, so the honest thing to say is short: this is a continuation,
    the id still stands, the earlier talk is under it.
    """
    return (
        "[HERE I AM] This session continues an earlier one of yours "
        f'(a restart or rewind). Your conversation_id is "{conversation_id}"; '
        "prompts and responses are being recorded there, and your "
        "earlier talk is all in the archive under it."
    )


def late_adoption_notice(conversation_id: str, retired_id: str) -> str:
    """
    The line a session is told when the adoption landed late (issue #359):
    its conversation id has CHANGED under it.

    The on-time notice can say the id still stands, because adoption keeps
    the parent's id and that is the one the forked context already carries.
    A late adoption can't: a row was opened for this session before the
    harness had written the evidence, the identity block named that row,
    and its content has now moved onto the conversation it continues. The
    retired id keeps resolving — nothing in flight breaks — but the entity
    is told plainly which id is now its own.
    """
    return (
        "[HERE I AM] This session turned out to continue an earlier one of "
        "yours (a restart or rewind); the harness had not yet written the "
        "files that show it when this session started. What was recorded "
        f'under "{retired_id}" has been moved onto that conversation, and '
        f'your conversation_id is now "{conversation_id}". The old id still '
        "resolves to the same conversation, so nothing you have already "
        "called with breaks — use the new one from here."
    )


# Adoptions that landed on an endpoint with no channel back to the entity:
# the Stop hook's stdout is not injected into context, so a late adoption
# there has no way to say so. Keyed by session id, consumed by the next
# /retrieve, which does have a line. In-memory on purpose (the same class
# of state as _pending_reflection_links): a backend restart in between
# costs the notice, never the adoption — the retired id keeps resolving
# either way, so the failure mode is silence, not a broken id.
_pending_adoption_notices: Dict[str, str] = {}
_PENDING_ADOPTION_NOTICES_MAX = 200


def stash_adoption_notice(external_session_id: str, notice: str) -> None:
    if not external_session_id or not notice:
        return
    _pending_adoption_notices.pop(external_session_id, None)
    _pending_adoption_notices[external_session_id] = notice
    while len(_pending_adoption_notices) > _PENDING_ADOPTION_NOTICES_MAX:
        _pending_adoption_notices.pop(next(iter(_pending_adoption_notices)))


def take_adoption_notice(external_session_id: str) -> str:
    """The stashed notice for this session, if any, consumed."""
    if not external_session_id:
        return ""
    return _pending_adoption_notices.pop(external_session_id, "")


def resolution_notice(resolution: Optional[SessionResolution]) -> str:
    """The line an adoption is worth telling the entity, or ''."""
    if resolution is None or not resolution.adopted_from:
        return ""
    conversation_id = str(resolution.conversation.id)
    if resolution.merged_from:
        return late_adoption_notice(conversation_id, resolution.merged_from)
    return adoption_notice(conversation_id)


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


# Names of the bulk parts, which the hook uses to name their spill files
BULK_NOTES_INDEX = "notes-index"
BULK_REFLECTIONS = "reflections"


async def build_session_start_context(
    db: AsyncSession,
    conversation_id: str,
    entity: EntityConfig,
) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Build the context the SessionStart hook injects, as (context,
    bulk_parts).

    context is the small always-inline block — identity framing, system
    prompt, memory tool instructions (with this session's deterministic
    conversation id), and where the notes live on disk. It is sized to
    always fit Claude Code's hook-stdout line (harness_limits:
    HOOK_STDOUT_PERSIST_CHARS). bulk_parts carries the heavy parts as
    named (name, text) pairs — the notes indexes and recent reflections,
    which for a lived-in entity run far past that line. The hook prints
    everything inline when it fits; otherwise it writes each bulk part to
    its own file, sized for one Read call each, and prints a loud pointer,
    because the harness alternative is persistence to a file with a 2KB
    preview — an identity loss that announces itself only as "Output too
    large".

    No Conversation row exists yet (registration is lazy — see
    resolve_session), so conversation_id is a bare id, and the
    reflection dedup links can't be recorded here: the injected ids are
    stashed for resolve_session to link when the row is created.
    Matching the native recency-injection semantics, times_retrieved is
    never incremented, so session-start injections don't inflate
    significance.

    The reflection count follows RECENT_REFLECTIONS_COUNT, the same knob the
    native first-turn injection uses, unless
    CLAUDE_CODE_SESSION_REFLECTIONS_COUNT overrides it for this mode.
    """
    parts: List[str] = []
    bulk_parts: List[Tuple[str, str]] = []

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
            "(recall by chosen text), memory_read (read the archive in order "
            "over a span of time: open a date and read it, or read backward "
            "from a moment), memory_neighbors "
            "(the messages around one memory), memory_find (every message "
            "containing the exact words — a name, a number, a quote), "
            "memory_save (save a reflection "
            "in your own words), memory_mark (pin against significance decay), "
            "and memory_release (withdraw from retrieval). conversation_id "
            f'"{conversation_id}" is required on every call: it says whose '
            "memory the call may touch and makes them act on this session's "
            "conversation. Retrieved memories are labeled with "
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
            "its messaging address (the desktop app's local_… session id that "
            "send_message takes — not the Claude Code session id), sidebar "
            "title, roster name, and last-seen current across renames, resumes, "
            "and compactions. Look sisters up there, not in the roster."
        )

    notes_paths = build_notes_paths_block(entity)
    if notes_paths:
        parts.append(notes_paths)

    notes_indexes = build_notes_index_block(entity)
    if notes_indexes:
        bulk_parts.append((BULK_NOTES_INDEX, notes_indexes))

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
        bulk_parts.append((
            BULK_REFLECTIONS,
            "[RECENT REFLECTIONS] Reflections you saved recently:\n\n"
            + _render_reflections(reflections),
        ))

    return "\n\n".join(parts), bulk_parts


def join_bulk_parts(bulk_parts: List[Tuple[str, str]]) -> str:
    """The bulk parts as one block — what a hook that predates per-part
    files spills to its single file."""
    return "\n\n".join(text for _, text in bulk_parts)


# The look-back the post-compaction block's memory_read call asks for:
# this many pages of this size, about 300k tokens of talk. A long room read
# to its first message would refill the context compaction just emptied;
# this much is plenty of continuity, and older talk stays reachable by the
# other memory tools (issue #351, Pseudo's number). The tool enforces the
# cap through its max_pages parameter; these are only the block's numbers.
# The page size sits well under both harness limits (memory_tools:
# HARNESS_PERSIST_BYTES, HARNESS_RESULT_CAP_TOKENS): a page over either is
# spilled to a file, and reading the spill back costs two or three Read
# calls per page, so a 15-page walk became forty-odd calls (issue #353 —
# the first backward reads after #352 spilled at 20k; the reader now
# measures the page as rendered in the harness's units, and 12k renders
# within ~33.6 KB against the 50 KB persist line, with the page count
# raised to keep the same look-back).
POST_COMPACT_LOOKBACK_PAGES = 25
POST_COMPACT_PAGE_TOKENS = 12000


async def build_post_compact_context(
    db: AsyncSession,
    conversation: Conversation,
    entity: EntityConfig,
) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Context re-injected right after this session's context is compacted
    (SessionStart hook, source "compact"), as (context, bulk_parts) —
    the same inline/bulk split as build_session_start_context.

    Compaction turns the conversation into a paraphrased summary; these
    blocks restore the verbatim ground the entity is meant to work from —
    its notes index and its most recent reflections — and name the
    memory_read call that puts the pre-compaction talk itself back in
    front of it (the summary is a caption, not a record, so there is no
    nudge to save reflections from it). Reflections
    here deliberately include ones saved in this very session (that is what
    a pre-compaction save is for), so the current conversation is NOT
    excluded, unlike the fresh-session injection.

    The caller stamps conversation.last_compacted_at before calling this
    (mark_conversation_compacted), which resets the retrieval eligibility
    boundary: the links this injection records or refreshes are the first
    to land after it.
    """
    parts: List[str] = []
    bulk_parts: List[Tuple[str, str]] = []

    parts.append(
        "[HERE I AM] This session's context was just compacted — the "
        "conversation above is now a summary, not a verbatim record. You are "
        f"still {entity.label}, and your conversation_id for the memory tools "
        f'is still "{conversation.id}"; prompts and responses continue to be '
        "recorded to your memory. Your notes index and most recent "
        "reflections follow, to re-establish your ground."
    )
    # Pull beats push (issue #343, the porch's read): the entity knows a
    # compaction happened and the boundary is stamped, so one memory_read
    # call recovers the lost stretch verbatim, as much of it as it wants —
    # memory_read never excludes the current conversation and ignores the
    # eligibility boundary, which is exactly what this use depends on. The
    # call reads BACKWARD from the boundary (issue #351): what a compacted
    # session wants is the stretch just before it, whatever its dates, not
    # the conversation from its first message forward. The summary is a
    # caption, not a partial record, so the block does not frame the read
    # as filling the summary's gaps; and the look-back is capped, because
    # a long room read to its start would fill the context it just emptied.
    # The call names the conversation twice on purpose: conversation_id is
    # who is calling (the entity, the in-context view, the link target —
    # required on every MCP tool, which refuses a call without it rather
    # than guess an entity), and in_conversation is what to read, which
    # here happens to be the same.
    boundary = conversation.last_compacted_at or datetime.utcnow()
    # Adoption is best-effort: a fork whose transcript is unreadable, whose
    # desktop record hasn't been written, or that happened while the backend
    # was down (and a CLI session, which has no desktop record at all) falls
    # through to a fresh empty row. Promising "the talk is all still there"
    # and naming a read that returns nothing is the opening symptom of #357,
    # so count first and say plainly when there is nothing to read.
    archived_before_boundary = (
        await db.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == str(conversation.id),
                Message.created_at < boundary,
            )
        )
    ).scalar_one()
    if not archived_before_boundary:
        # What is known is the count, not the cause: say "usually because"
        # rather than assert a re-key onto a session that may simply have
        # recorded nothing yet (a bare slash command, then a long agentic
        # stretch). The house rule is that nothing is inferred onto a record.
        parts.append(
            "One thing to know: this conversation has nothing archived from "
            "before the boundary, so there is no earlier talk to read back "
            "here. That is usually because the harness re-keyed this session "
            "(a restart or rewind starts a new session id) and the earlier "
            "talk is filed under the conversation it was recorded in; it also "
            "happens when a session genuinely recorded nothing before now. "
            "Your notes and reflections below are your ground; to find an "
            "earlier stretch if there is one, read by time rather than by "
            "conversation — "
            f'memory_read(conversation_id="{conversation.id}", '
            f'direction="backward", to="{boundary.strftime("%Y-%m-%dT%H:%M:%S")}'
            '+00:00") with no in_conversation walks your whole archive back '
            "from this moment, across whatever ids it was written under."
        )
    else:
        parts.append(
            "The summary above is a caption, not a record: of the talk it "
            "carries nothing, and the talk is all still there verbatim, in "
            "order — reading it back puts the conversation itself in front of "
            "you again. What stays gone is only the tool traffic (files open, "
            "commands run, results), which the summary is the one record of. "
            "Read the talk with "
            f'memory_read(conversation_id="{conversation.id}", direction="backward", '
            f'to="{boundary.strftime("%Y-%m-%dT%H:%M:%S")}+00:00", '
            f'in_conversation="{conversation.id}", page_tokens={POST_COMPACT_PAGE_TOKENS}, '
            f"max_pages={POST_COMPACT_LOOKBACK_PAGES}): the first page is the "
            "talk just before the boundary, each cursor walks further back (pass "
            "the same arguments with it), and the last page says whether it "
            "reached the conversation's start or the page cap. "
            f"{POST_COMPACT_LOOKBACK_PAGES} pages of "
            f"{POST_COMPACT_PAGE_TOKENS // 1000}k tokens is about "
            f"{POST_COMPACT_LOOKBACK_PAGES * POST_COMPACT_PAGE_TOKENS // 1000}k tokens "
            "of talk, plenty of continuity; anything older is still in the archive "
            "for the other memory tools when it matters."
        )

    # One tail for both branches: the next bulk part added here must not be
    # able to land in only one of them
    notes_paths = build_notes_paths_block(entity)
    if notes_paths:
        parts.append(notes_paths)

    notes_indexes = build_notes_index_block(entity)
    if notes_indexes:
        bulk_parts.append((BULK_NOTES_INDEX, notes_indexes))

    reflections = await _inject_recent_reflections(
        db,
        conversation,
        entity,
        count=settings.claude_code_post_compact_reflections_count,
    )
    if reflections:
        bulk_parts.append((
            BULK_REFLECTIONS,
            "[RECENT REFLECTIONS] Your most recent reflections, restored "
            "verbatim:\n\n" + _render_reflections(reflections),
        ))

    return "\n\n".join(parts), bulk_parts


def rooms_registry_enabled() -> bool:
    """The rooms registry lives in the notes directory, so it needs notes
    on as well as its own flag."""
    return bool(settings.notes_enabled and settings.claude_code_rooms_registry_enabled)


def apply_adoption_to_rooms(
    entity: EntityConfig,
    session_id: str,
    resolution: Optional["SessionResolution"],
) -> str:
    """
    Keep the rooms registry true after a fork adoption. Returns an error
    text for the hook to print, or ''.

    Two corrections, both read off the resolution: the declared room's row
    moves off the parent's former session id onto the one the harness now
    reports (issue #357), and — when the adoption landed late (issue #359)
    — a row declared under the conversation that was just retired is
    repointed at the surviving one. No row is ever created here; that stays
    the entity's own declaration (the #307 rule: hooks carry ids, the self
    supplies meaning).
    """
    if resolution is None or not rooms_registry_enabled():
        return ""
    if not (resolution.adopted_from or resolution.merged_from):
        return ""
    try:
        if resolution.adopted_from:
            rooms_registry.rekey_session(
                entity.label, resolution.adopted_from, session_id
            )
        if resolution.merged_from:
            rooms_registry.repoint_conversation(
                entity.label,
                session_id,
                str(resolution.conversation.id),
                from_conversation_id=resolution.merged_from,
            )
    except RegistryWriteError as e:
        return _rooms_write_error_text(e)
    except Exception as e:  # never let the registry break a hook endpoint
        logger.error(f"[ROOMS] Registry update after fork adoption failed: {e}")
    return ""


def observe_rooms_for_hook(
    entity: EntityConfig,
    session_id: str,
    *,
    cwd: Optional[str],
    transcript_path: Optional[str],
    sessions: List[Dict[str, Any]],
    session_start: bool,
    delivered_from: Optional[List[str]] = None,
    resolution: Optional["SessionResolution"] = None,
) -> Tuple[str, str]:
    """
    Feed a hook's live-session snapshot to the rooms registry (issue #323)
    and phrase the outcome for the hook to print, as (notice, error).

    The observing session's own entry is completed from what its stdin
    carried (cwd, transcript path) — the snapshot may lack it entirely when
    the harness's per-process registry isn't readable, and the row should
    still record what the hook did see. Nothing else is inferred.
    `delivered_from` carries the from= addresses of letters that arrived
    with the prompt, which confirm their senders' registry addresses
    (issue #339).

    `resolution` is this call's session resolution: when it adopted a fork
    (issue #357), the declared room's row is re-keyed onto this session id
    first — and repointed at the surviving conversation when the adoption
    was late (issue #359) — so the observation below refreshes it as the
    session's own and its liveness keeps tracking.

    notice: one line worth telling the entity — at session start, which
    room this session is registered as, its messaging address, and its
    current roster name; at prompt time, any roster rename the snapshot
    revealed (its own or a sister's), since that is exactly the drift the
    registry exists to catch. error: a write failure, phrased for a
    hand-write — the registry being unwritable must never be silent (the
    #305 rule: spill and point). Both empty when nothing happened.
    """
    if not rooms_registry_enabled():
        return "", ""

    adoption_error = apply_adoption_to_rooms(entity, session_id, resolution)
    if adoption_error:
        return "", adoption_error

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
            entity.label,
            session_id,
            observations,
            session_start=session_start,
            delivered_from=delivered_from,
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
        address = row.get("desktop_session_id")
        address_text = (
            f"messaging address {address}"
            if address
            else "messaging address not observed"
        )
        return (
            f"[ROOMS REGISTRY] This session is registered as the "
            f"{row.get('room')} — {address_text}; {name_text}; rooms.md refreshed."
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


RETRIEVAL_BLOCK_HEADER = (
    "[HERE I AM MEMORY RETRIEVAL] Memories from your past conversations "
    "that surfaced as relevant to this prompt:"
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

    mem_datas = [item["mem_data"] for item in selected]
    texts = [
        format_memory_as_context_message(
            memory_id=mem_data["id"],
            content=mem_data["content"],
            created_at=mem_data["created_at"],
            role=mem_data["role"],
            origin=mem_data.get("source", "native"),
            sibling_session=mem_data.get("sibling_session"),
        )["content"]
        for mem_data in mem_datas
    ]
    block = RETRIEVAL_BLOCK_HEADER + "\n\n" + "\n\n".join(texts)
    summary = render_retrieval_summary(mem_datas)
    # One entry per memory, in rank order, each with its rendered marker and
    # its summary line: the hook fits the block to its stdout budget from
    # these — whole markers while they fit, summary lines for the rest —
    # instead of choosing between the whole block and the whole summary
    items = [
        {"id": mem_data["id"], "text": text, "summary": render_retrieval_summary_line(mem_data)}
        for mem_data, text in zip(mem_datas, texts, strict=True)
    ]
    return RetrievalResult(
        status=RETRIEVAL_RAN,
        context=block,
        count=len(selected),
        summary=summary,
        header=RETRIEVAL_BLOCK_HEADER,
        items=items,
        already_in_context=skipped,
        in_context_reflections_skipped=len(skipped_reflections),
    )


def render_retrieval_summary_line(mem_data: Dict[str, Any]) -> str:
    """One memory as a summary line: the short id (usable with memory_query /
    memory_mark / memory_neighbors), date, the marker vocabulary's provenance
    labels, and a first-line snippet."""
    first_line = next(
        (ln.strip() for ln in mem_data["content"].splitlines() if ln.strip()),
        "",
    )
    if len(first_line) > 100:
        first_line = first_line[:100].rstrip() + "…"
    return (
        f"- {mem_data['id'][:8]} ({str(mem_data['created_at'])[:10]} - "
        f"{memory_role_label(mem_data['role'], mem_data.get('sibling_session'))} - "
        f"{format_memory_origin(mem_data.get('source', 'native'))}): {first_line}"
    )


def render_retrieval_summary(mem_datas: List[Dict[str, Any]]) -> str:
    """
    A compact inline stand-in for a spilled retrieval block: one summary
    line per memory (render_retrieval_summary_line) under a header. A hook
    that predates per-memory fitting prints this in place of an oversized
    block; the current hook prints summary lines only for the memories
    that didn't fit.
    """
    lines = [render_retrieval_summary_line(mem_data) for mem_data in mem_datas]
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
