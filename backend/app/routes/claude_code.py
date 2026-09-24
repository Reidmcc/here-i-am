"""
Claude Code mode endpoints.

Called by the Claude Code lifecycle hooks shipped in claude-code-mode/ (not
by the frontend). Each endpoint is keyed on the Claude Code session ID and
is safe to call out of order: any endpoint that records content will create
the session's conversation if the backend hasn't seen it yet (e.g. after a
mid-session backend restart). /session-start deliberately does NOT create
it — registration is lazy, deferred to the first recorded prompt, because
Claude Desktop fires SessionStart for background/utility sessions that
never speak (see services/claude_code_mode.py).

Gated by CLAUDE_CODE_MODE_ENABLED (default off) — the hooks are written to
fail soft, so a disabled or unreachable backend degrades a Claude Code
session to a plain one rather than breaking it.

See docs/claude-code-mode.md for the full design.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import EntityConfig, settings
from app.database import get_db
from app.models import Message, MessageRole
from app.services import claude_code_mcp
from app.services import claude_code_mode as cc
from app.services.harness_limits import (
    AUTO_COMPACT_DEFAULT_WINDOW_TOKENS,
    AUTO_COMPACT_RESERVE_TOKENS,
    HOOK_INLINE_BUDGET_CHARS,
)
from app.services.memory_service import memory_service
from app.services.notes_vector_service import notes_vector_service

router = APIRouter(prefix="/api/claude-code", tags=["claude-code"])

# The MCP transport lives at /mcp (no /api prefix — it is the URL Claude
# Code's .mcp.json points at, not a frontend API)
mcp_router = APIRouter(tags=["claude-code-mcp"])

logger = logging.getLogger(__name__)

# A bare slash command ("/compact", "/clear") — harness input, not
# conversation. Not persisted, not vectorized, no retrieval run against it.
BARE_SLASH_COMMAND = re.compile(r"^/\S*$")


class SessionObservationIn(BaseModel):
    """
    One live session as a hook could see it — from Claude Code's per-process
    registry, best effort (issue #323). Every field but session_id is
    optional: absent means the harness didn't expose it, and the rooms
    registry records exactly that rather than guessing.
    """
    session_id: str
    name: Optional[str] = None
    name_source: Optional[str] = None  # "user" | "derived"
    name_since: Optional[str] = None
    messaging_socket: Optional[str] = None
    cwd: Optional[str] = None
    transcript_path: Optional[str] = None
    started_at: Optional[str] = None
    # From the desktop app's own session record (issue #339): the `local_…`
    # id its session-management MCP addresses, and the sidebar title
    desktop_session_id: Optional[str] = None
    desktop_title: Optional[str] = None


class SessionStartRequest(BaseModel):
    session_id: str
    entity: Optional[str] = None  # index name or label; None = default entity
    cwd: Optional[str] = None
    source: Optional[str] = None  # Claude Code's startup|resume|clear|compact
    transcript_path: Optional[str] = None
    # Snapshot of the live sessions the hook could see (this one and its
    # siblings), for the rooms registry
    sessions: List[SessionObservationIn] = []
    # Lineage hints for fork adoption (issue #357): the desktop app forks a
    # session under a new id on restart/continue/rewind, so these let the
    # backend resolve this id to the conversation it continues rather than
    # start an empty one. prior_session_ids = the desktop record's
    # priorCliSessionIds; transcript_message_ids = end-of-turn assistant
    # entry uuids from the transcript tail (which are Message row ids).
    prior_session_ids: List[str] = []
    transcript_message_ids: List[str] = []


class BulkPart(BaseModel):
    """One named part of the session-start bulk (notes-index, reflections),
    which the hook spills to its own file when the whole doesn't fit."""
    name: str
    text: str


class ContextItem(BaseModel):
    """One retrieved memory as the hook fits it: the rendered [MEMORY]
    marker and the one-line summary that stands in for it when it
    doesn't fit."""
    id: str
    text: str
    summary: str


class GitIdentity(BaseModel):
    """The entity's own GitHub identity for this session (issue #362):
    what the SessionStart hook exports into the session's shell
    environment. A path and two strings — never a token."""
    author_name: Optional[str] = None
    author_email: Optional[str] = None
    gh_config_dir: Optional[str] = None


class SessionStartResponse(BaseModel):
    # The session's conversation id — deterministic, and handed out before
    # the row exists (lazy registration; see services/claude_code_mode.py)
    conversation_id: str
    entity_id: str
    entity_label: str
    # True when this response carries the fresh-session context (no row
    # yet). The hook uses it only to name the bulk spill file.
    created: bool
    context: str
    # Notes indexes + recent reflections. Separate from `context` because the
    # hook must keep its stdout under Claude Code's hook-stdout line
    # (harness_limits: oversized hook output is persisted to a file with a
    # 2 KB preview): when the combined blocks don't fit, the hook writes the
    # bulk to files and prints a loud pointer instead. bulk_parts is the
    # same content as named parts, one file each (notes-index, reflections),
    # so each lands in one Read call; bulk_context is the parts joined, for
    # a hook that predates the split.
    bulk_context: str = ""
    bulk_parts: List[BulkPart] = []
    # The hook's stdout budget in characters (harness_limits), so hooks and
    # backend can't disagree about the line; HIM_INLINE_BUDGET overrides it
    inline_budget: int = HOOK_INLINE_BUDGET_CHARS
    # Rooms registry (issue #323): a one-line notice about this session's
    # registry row, and — never silent — a write failure carrying the row
    # the entity can write by hand
    rooms_notice: str = ""
    rooms_error: str = ""
    # The entity's GitHub identity, on every firing (startup, resume,
    # compact): the environment file is per session process, so a resume
    # needs it as much as a fresh start. None = entity has none configured.
    git_identity: Optional[GitIdentity] = None


class PeerMessage(BaseModel):
    """
    One inter-session message (a delivery from a sibling Claude Code
    session over the desktop app's session-management MCP), extracted from
    the prompt by the UserPromptSubmit hook. Another session of this entity
    speaking — recorded under the entity's own name with the sending
    session marked (issue #312), never as the human's words.
    """
    content: str
    # The sending session's display name (the wrapper's name= attribute;
    # from-name= in the removed SendMessage tool's wrapper — issue #331)
    sender: Optional[str] = None
    # The wrapper's from= attribute: the sender's messaging address (its
    # desktop-app session id). Not persisted on the row; it confirms the
    # rooms registry's address for the sender (issue #339)
    sender_session: Optional[str] = None
    # Row id chosen by the hook (a UUID), so it can verify recording after a
    # failed call and so a retry is idempotent; see RetrieveRequest.message_id
    message_id: Optional[str] = None


class RetrieveRequest(BaseModel):
    session_id: str
    prompt: str
    entity: Optional[str] = None
    cwd: Optional[str] = None
    # Row id for the prompt's human message, chosen by the hook (a UUID).
    # The hook needs to know, after a failed call, whether the row it sent
    # was committed before the failure — a 500 or a timeout can land after
    # the commit, and "NOT recorded" would then be false. With the id in
    # hand it asks /recorded instead of inferring. The same id makes a
    # retried call idempotent (an existing row is reused, never
    # re-recorded). Invalid or absent: the row gets a generated id.
    message_id: Optional[str] = None
    # Inter-session messages that rode in with (or stood in for) the prompt
    peer_messages: List[PeerMessage] = []
    # Live-session snapshot for the rooms registry (see SessionStartRequest)
    sessions: List[SessionObservationIn] = []
    # Fork-adoption lineage hints (issue #357; see SessionStartRequest)
    prior_session_ids: List[str] = []
    transcript_message_ids: List[str] = []


class RetrieveResponse(BaseModel):
    conversation_id: str
    human_message_id: Optional[str]
    context: str
    memories_retrieved: int
    # One line per retrieved memory; a hook that predates per-memory fitting
    # prints it in place of an oversized `context` it had to spill to a file
    context_summary: str = ""
    # The block's header sentence and one entry per memory in rank order
    # (rendered marker + summary line): the hook renders whole markers while
    # they fit its stdout budget and summary lines for the rest, pointing at
    # the spilled full block (fit, then point)
    context_header: str = ""
    context_items: List[ContextItem] = []
    inline_budget: int = HOOK_INLINE_BUDGET_CHARS
    # Reflections the entity saved in other sessions since this conversation
    # began, not yet surfaced here. The hook prints a one-line mailbox flag
    # when nonzero; the entity pulls the content with memory_query
    # mode="recent" if it wants it.
    new_sibling_reflections: int = 0
    # Rows created for peer_messages, in input order
    peer_message_ids: List[str] = []
    # Whether automatic retrieval happened (issue #326): "ran" (context and
    # memories_retrieved say what it found), "skipped" (nothing to query —
    # a wakeup tick's empty prompt or a bare slash command), "unconfigured"
    # (memory is off for this entity), or "failed" (the search raised;
    # retrieval_error says why — the prompt was still recorded). The hook
    # prints a distinct one-liner for each empty outcome, so its silence
    # never has to be read as "nothing matched".
    retrieval_status: str = cc.RETRIEVAL_RAN
    retrieval_error: str = ""
    # Verbatim matches that made the re-ranked top-k but were already linked
    # into this conversation (suppressed without backfill, like native mode)
    already_in_context: int = 0
    # Already-linked reflections the pull ranked highly and dropped from the
    # pool before the top-k cut, so they held no slot (issue #328)
    in_context_reflections_skipped: int = 0
    # Rooms registry: renames observed this turn / a loud write failure
    rooms_notice: str = ""
    rooms_error: str = ""
    # Set when this prompt's session turned out to be a fork and its parent
    # conversation was adopted (issue #357). The hook prints it: a rewind
    # usually fires no SessionStart, so /retrieve is where the entity hears
    # that this session is a continuation and which id is recording it.
    adoption_notice: str = ""


class LogAssistantRequest(BaseModel):
    session_id: str
    content: str
    entity: Optional[str] = None
    cwd: Optional[str] = None
    message_uuid: Optional[str] = None  # transcript entry UUID, for idempotency
    # The model that wrote the message, as the transcript entry reports it
    # (issue #321). Optional: an older hook, or an entry without one,
    # records NULL — never a guess.
    model: Optional[str] = None
    # Fork-adoption lineage hints (issue #357; see SessionStartRequest)
    prior_session_ids: List[str] = []
    transcript_message_ids: List[str] = []


class LogAssistantResponse(BaseModel):
    conversation_id: str
    message_id: Optional[str]
    deduplicated: bool
    # The context gauge (issue #365): the Stop hook measures the turn's
    # prompt size against the auto-compaction line, which it computes as
    # window − reserve. The window here is the default; the hook prefers
    # the one the harness is actually configured with (its environment and
    # settings files), which only the hook can see
    compact_window: int = AUTO_COMPACT_DEFAULT_WINDOW_TOKENS
    compact_reserve: int = AUTO_COMPACT_RESERVE_TOKENS


class SessionEndRequest(BaseModel):
    session_id: str
    entity: Optional[str] = None
    reason: Optional[str] = None  # Claude Code's clear|logout|prompt_input_exit|other


class SessionEndResponse(BaseModel):
    conversation_id: Optional[str]
    notes_sync_started: bool


def _require_enabled() -> None:
    if not settings.claude_code_mode_enabled:
        raise HTTPException(
            status_code=404,
            detail="Claude Code mode is not enabled (set CLAUDE_CODE_MODE_ENABLED=true)",
        )


def _resolve_entity_or_400(identifier: Optional[str]) -> EntityConfig:
    entity = cc.resolve_entity(identifier)
    if entity is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown entity '{identifier}' (use a configured index name or label)",
        )
    return entity


@router.post("/session-start", response_model=SessionStartResponse)
async def session_start(
    data: SessionStartRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the entity's context block for a starting Claude Code session.

    No conversation row is created here: registration is lazy, deferred to
    the first endpoint that records something (/retrieve, /log-assistant).
    The conversation id named in the context is deterministic (uuid5 of the
    session id), so the row those endpoints create matches what the entity
    was already told to pass to the memory tools.

    An unrecorded session gets the full block (identity + system prompt +
    notes index + recent reflections). An existing conversation whose
    context was just compacted (source "compact") gets the post-compaction
    block: notes index reloaded plus the most recent reflections restored
    verbatim (compaction paraphrases everything else); a compact for a
    session with no row registers it first — the post-compact block records
    reflection links, which need a home. A session-start for a session that
    already has a conversation (a resume) gets an empty block — its
    transcript already carries the injections, so re-sending them would
    duplicate context.

    Fork adoption (issue #357): the desktop app forks a session under a new
    id on restart/continue/rewind, copying the transcript. Lineage hints
    resolve the new id to the conversation it continues and re-key it onto
    that row, so the forked session records into the same conversation and
    the id already in its copied context stays valid — instead of a new,
    empty conversation whose post-compaction recovery finds nothing.
    """
    _require_enabled()
    entity = _resolve_entity_or_400(data.entity)

    # Resolve without creating: an existing row, or a fork's parent adopted
    # onto this id. A truly new session stays unregistered (lazy) unless it
    # is a compaction, which implies recorded history worth a row.
    resolution = await cc.resolve_session(
        db,
        data.session_id,
        entity,
        cwd=data.cwd,
        create=data.source == "compact",
        prior_session_ids=data.prior_session_ids,
        transcript_message_ids=data.transcript_message_ids,
    )
    conversation = resolution.conversation if resolution else None
    # An adoption that happened on this call speaks for itself; one that
    # landed on an earlier hook with no channel back (the Stop hook's
    # stdout is not injected) left its line for whoever speaks next
    adoption_text = cc.resolution_notice(resolution) or cc.take_adoption_notice(
        data.session_id
    )

    context = ""
    bulk_parts = []
    fresh = conversation is None
    if fresh:
        conversation_id = cc.conversation_id_for_session(data.session_id)
        context, bulk_parts = await cc.build_session_start_context(
            db, conversation_id, entity
        )
    else:
        conversation_id = str(conversation.id)
        if data.source == "compact":
            # Stamp the eligibility boundary first: links recorded/refreshed
            # by the post-compact injection must land after it (see
            # mark_conversation_compacted)
            await cc.mark_conversation_compacted(db, conversation)
            context, bulk_parts = await cc.build_post_compact_context(
                db, conversation, entity
            )
            if adoption_text:
                context = f"{adoption_text}\n\n{context}"
        elif adoption_text:
            # A resume that turned out to be a fork: the transcript already
            # carries the injections (so no bulk), but say once that the
            # session was picked up as a continuation — the recording is
            # landing on the parent, and under which conversation_id.
            context = adoption_text

    # Rooms registry: refresh this session's row (if it declared a room) and
    # any sibling rows the hook's snapshot covers — every SessionStart,
    # including resumes and post-compaction restarts, is a liveness signal
    rooms_notice, rooms_error = cc.observe_rooms_for_hook(
        entity,
        data.session_id,
        cwd=data.cwd,
        transcript_path=data.transcript_path,
        sessions=[s.model_dump() for s in data.sessions],
        session_start=True,
        resolution=resolution,
    )

    # Catch note edits made while the backend wasn't watching (e.g. before
    # this backend start)
    _spawn_notes_sync(entity)

    return SessionStartResponse(
        conversation_id=conversation_id,
        entity_id=entity.index_name,
        entity_label=entity.label,
        created=fresh,
        context=context,
        bulk_context=cc.join_bulk_parts(bulk_parts),
        bulk_parts=[BulkPart(name=name, text=text) for name, text in bulk_parts],
        rooms_notice=rooms_notice,
        rooms_error=rooms_error,
        git_identity=cc.git_identity_for(entity),
    )


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    data: RetrieveRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Record a user turn and run automatic memory retrieval against it.

    The prompt is persisted (role=human) and vectorized like a native human
    message. Inter-session messages the hook extracted from the prompt
    channel (peer_messages) are recorded separately, under the entity's own
    name: role=assistant with sibling_session marking the sending session,
    vectorized as role="sibling" (issue #312 — the archive holds the
    correspondence with honest provenance instead of omitting it). The
    returned context block holds the retrieved memories for the
    UserPromptSubmit hook to inject.
    """
    _require_enabled()
    entity = _resolve_entity_or_400(data.entity)

    # A fork's first recorded event is almost always this prompt — the
    # harness fires no SessionStart at a rewind boundary — so this is the
    # endpoint that usually does the adopting (issue #357).
    resolution = await cc.resolve_session(
        db,
        data.session_id,
        entity,
        cwd=data.cwd,
        create=True,
        prior_session_ids=data.prior_session_ids,
        transcript_message_ids=data.transcript_message_ids,
    )
    conversation = resolution.conversation

    # Rooms registry: a prompt in any room is a chance to catch a rename
    # anywhere (the snapshot covers every live session the hook could see),
    # and a letter that arrived with it confirms its sender's address.
    # adopted_from re-keys this room's row onto the forked session id, which
    # matters most here: this is the path an ordinary rewind takes.
    rooms_notice, rooms_error = cc.observe_rooms_for_hook(
        entity,
        data.session_id,
        cwd=data.cwd,
        transcript_path=None,
        sessions=[s.model_dump() for s in data.sessions],
        session_start=False,
        delivered_from=[
            peer.sender_session
            for peer in (data.peer_messages or [])
            if peer.sender_session
        ],
        resolution=resolution,
    )

    # As in session-start: this call's own adoption, or one stashed by an
    # endpoint that had no line back to the entity (issue #359)
    adoption_text = cc.resolution_notice(resolution) or cc.take_adoption_notice(
        data.session_id
    )

    prompt = data.prompt or ""
    # The bare-slash-command skip applies to the human's words only: a
    # sibling's letter is prose from the entity, not harness input
    record_prompt = bool(prompt.strip()) and not BARE_SLASH_COMMAND.match(
        prompt.strip()
    )
    peer_messages = [
        peer for peer in (data.peer_messages or []) if (peer.content or "").strip()
    ]
    if not record_prompt and not peer_messages:
        # Nothing to record — a bare slash command, or a self-scheduled
        # wakeup tick the hook dropped (issue #318: the entity's own timer
        # firing is not talk, so it never enters the archive). Wakeup-driven
        # loop sessions can run for hours on ticks alone, so the mailbox
        # count and the incremental notes sync still run here.
        sibling_reflections = await cc.count_new_sibling_reflections(
            db, conversation, entity
        )
        _spawn_notes_sync(entity)
        return RetrieveResponse(
            conversation_id=str(conversation.id),
            human_message_id=None,
            context="",
            memories_retrieved=0,
            new_sibling_reflections=sibling_reflections,
            retrieval_status=cc.RETRIEVAL_SKIPPED,
            rooms_notice=rooms_notice,
            rooms_error=rooms_error,
            adoption_notice=adoption_text,
        )

    # Rows are persisted under the ids the hook chose (when valid), and a
    # row that already exists under that id is reused: a hook retrying
    # after a timeout must not record the turn twice. The route can still
    # fail after a commit (vectorization, a later peer row, the search); the
    # hook then verifies what landed through /recorded instead of guessing.
    human_msg = None
    if record_prompt:
        human_msg = await _existing_message(db, conversation, _valid_uuid(data.message_id))
        if human_msg is None:
            human_msg = await cc.persist_and_vectorize_message(
                db,
                conversation,
                entity,
                role=MessageRole.HUMAN,
                content=prompt,
                message_id=_valid_uuid(data.message_id),
                token_count=cc.safe_token_count(prompt),
            )

    peer_message_ids: List[str] = []
    for peer in peer_messages:
        peer_msg = await _existing_message(db, conversation, _valid_uuid(peer.message_id))
        if peer_msg is None:
            peer_msg = await cc.persist_and_vectorize_message(
                db,
                conversation,
                entity,
                role=MessageRole.ASSISTANT,
                content=peer.content,
                message_id=_valid_uuid(peer.message_id),
                token_count=cc.safe_token_count(peer.content),
                sibling_session=(peer.sender or "").strip() or "unknown session",
            )
        peer_message_ids.append(str(peer_msg.id))

    # Retrieval runs against the whole turn's new content — the human's
    # words and/or the sibling's letter (retrieve_for_prompt excludes this
    # conversation, so the rows just recorded can't surface as results)
    query_parts = [prompt] if record_prompt else []
    query_parts.extend(peer.content for peer in peer_messages)
    try:
        retrieval = await cc.retrieve_for_prompt(
            db, conversation, entity, "\n\n".join(query_parts)
        )
    except Exception as e:
        # The turn's rows are already committed above, so a 500 here would
        # make the hook's unreachable-backend notice ("NOT recorded") lie.
        # Report the failure in the response instead: the hook prints it as
        # its own distinct line, and the mailbox count and notes sync below
        # still run.
        logger.exception(
            f"[CC MODE] Automatic retrieval failed for conversation "
            f"{str(conversation.id)[:8]}...: {e}"
        )
        retrieval = cc.RetrievalResult(
            status=cc.RETRIEVAL_FAILED,
            error=f"{e.__class__.__name__}: {e}"[:300],
        )

    sibling_reflections = await cc.count_new_sibling_reflections(
        db, conversation, entity
    )

    # Keep the semantic notes mirror fresh continuously: sessions edit note
    # files with Claude Code's own tools and may never formally end, so each
    # recorded prompt triggers an incremental background sync (hash-compare,
    # only diffs touch Pinecone)
    _spawn_notes_sync(entity)

    return RetrieveResponse(
        conversation_id=str(conversation.id),
        human_message_id=str(human_msg.id) if human_msg else None,
        context=retrieval.context,
        memories_retrieved=retrieval.count,
        context_summary=retrieval.summary,
        context_header=retrieval.header,
        context_items=[ContextItem(**item) for item in retrieval.items],
        new_sibling_reflections=sibling_reflections,
        peer_message_ids=peer_message_ids,
        retrieval_status=retrieval.status,
        retrieval_error=retrieval.error,
        already_in_context=retrieval.already_in_context,
        in_context_reflections_skipped=retrieval.in_context_reflections_skipped,
        rooms_notice=rooms_notice,
        rooms_error=rooms_error,
        adoption_notice=adoption_text,
    )


def _valid_uuid(value: Optional[str]) -> Optional[str]:
    """The value as a canonical UUID string, or None if it isn't one — a
    hook-chosen row id is honored only when it is well-formed."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return None


async def _existing_message(
    db: AsyncSession, conversation, message_id: Optional[str]
) -> Optional[Message]:
    """A row already recorded under message_id in this conversation (a
    retried hook call), or None."""
    if not message_id:
        return None
    result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == str(conversation.id),
        )
    )
    return result.scalar_one_or_none()


class RecordedRequest(BaseModel):
    session_id: str
    message_ids: List[str] = []


class RecordedResponse(BaseModel):
    # Of the ids asked about, those that exist as rows of this session's
    # conversation, and those that don't
    recorded: List[str] = []
    missing: List[str] = []


@router.post("/recorded", response_model=RecordedResponse)
async def recorded(
    data: RecordedRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Which of these message ids were recorded for this session.

    The UserPromptSubmit hook's verification step: when /retrieve fails
    (a 500, a timeout, a dropped connection), the hook cannot tell from the
    error whether the rows it sent were committed first — and telling the
    entity its words were NOT recorded when they were is exactly the false
    information the hooks exist to prevent. So the hook chooses the row ids
    up front and asks here. SQL only, no side effects, creates nothing;
    ids are scoped to the session's conversation so a stray id from
    elsewhere reads as missing.
    """
    _require_enabled()
    wanted = [mid for mid in (_valid_uuid(m) for m in data.message_ids) if mid]
    if not wanted:
        return RecordedResponse(recorded=[], missing=list(data.message_ids))
    # Resolve the session's conversation (alias-aware): after a fork the
    # rows live under the adopted parent, not uuid5(this session id), so
    # recomputing the deterministic id would report a landed row as missing
    # (issue #357). No row means nothing was recorded — everything missing.
    conversation = await cc.get_conversation_for_session(db, data.session_id)
    if conversation is None:
        return RecordedResponse(recorded=[], missing=list(data.message_ids))
    result = await db.execute(
        select(Message.id).where(
            Message.id.in_(wanted),
            Message.conversation_id == str(conversation.id),
        )
    )
    found = {row[0] for row in result.all()}
    return RecordedResponse(
        recorded=[mid for mid in wanted if mid in found],
        missing=[mid for mid in data.message_ids if _valid_uuid(mid) not in found],
    )


@router.post("/log-assistant", response_model=LogAssistantResponse)
async def log_assistant(
    data: LogAssistantRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Record the entity's final message of a turn (from the Stop hook).

    Idempotent on message_uuid: the transcript entry's UUID becomes the
    Message row's primary key, so a re-fired hook is a no-op.
    """
    _require_enabled()
    entity = _resolve_entity_or_400(data.entity)

    resolution = await cc.resolve_session(
        db,
        data.session_id,
        entity,
        cwd=data.cwd,
        create=True,
        prior_session_ids=data.prior_session_ids,
        transcript_message_ids=data.transcript_message_ids,
    )
    conversation = resolution.conversation

    # The Stop hook is often the first call after the harness has written
    # a fork's files, so it is a real adopter (issue #359) — but its own
    # stdout never reaches the entity, so the registry is corrected here
    # and the line is left for the next prompt to print.
    notice = cc.resolution_notice(resolution)
    if notice:
        cc.stash_adoption_notice(data.session_id, notice)
        cc.apply_adoption_to_rooms(entity, data.session_id, resolution)

    content = data.content or ""
    if not content.strip():
        # Mirror native behavior: empty responses are never persisted
        return LogAssistantResponse(
            conversation_id=str(conversation.id),
            message_id=None,
            deduplicated=False,
        )

    if data.message_uuid:
        result = await db.execute(
            select(Message.id).where(Message.id == data.message_uuid)
        )
        if result.scalar_one_or_none() is not None:
            return LogAssistantResponse(
                conversation_id=str(conversation.id),
                message_id=data.message_uuid,
                deduplicated=True,
            )

    assistant_msg = await cc.persist_and_vectorize_message(
        db,
        conversation,
        entity,
        role=MessageRole.ASSISTANT,
        content=content,
        message_id=data.message_uuid,
        token_count=cc.safe_token_count(content),
        model=(data.model or "").strip()[:100] or None,
    )

    return LogAssistantResponse(
        conversation_id=str(conversation.id),
        message_id=str(assistant_msg.id),
        deduplicated=False,
    )


@router.post("/session-end", response_model=SessionEndResponse)
async def session_end(
    data: SessionEndRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    A Claude Code session ended. Runs a final background notes sync.

    This is a catch, not the mechanism: SessionEnd only fires on /clear,
    logout, or exiting the CLI, and a session can simply idle out without
    ever ending — so the same sync also runs on every recorded prompt
    (see /retrieve). Deliberately does not create a conversation for an
    unseen session (nothing to record for a session that never spoke).
    """
    _require_enabled()
    entity = _resolve_entity_or_400(data.entity)

    conversation = await cc.get_conversation_for_session(db, data.session_id)
    sync_started = _spawn_notes_sync(entity)

    return SessionEndResponse(
        conversation_id=str(conversation.id) if conversation else None,
        notes_sync_started=sync_started,
    )


# Keep references so background sync tasks aren't garbage-collected mid-run
_background_tasks: set = set()


def _spawn_notes_sync(entity: EntityConfig) -> bool:
    """
    Fire-and-forget incremental notes sync for an entity (see
    notes_vector_service.sync_entity_notes). Claude Code sessions edit note
    files with their own file tools, so the semantic mirror is refreshed in
    the background on every backend contact rather than waiting for a
    session-end that may never come. Returns whether a sync was started.
    """
    if not settings.notes_enabled:
        return False
    if not memory_service.is_configured(entity_id=entity.index_name):
        return False
    task = asyncio.create_task(_sync_notes_for_entity(entity.label))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return True


async def _sync_notes_for_entity(entity_label: str) -> None:
    try:
        await notes_vector_service.sync_entity_notes(entity_label)
    except Exception as e:
        logger.error(f"[CC MODE] Notes sync failed for '{entity_label}': {e}")


@mcp_router.post("/mcp")
async def mcp_endpoint(request: Request):
    """
    MCP streamable-HTTP transport (stateless, JSON responses).

    Claude Code connects here via the plugin's .mcp.json to reach the
    entity's deliberate memory tools. Each POST carries one JSON-RPC
    message (or, on pre-2025-06-18 protocol versions, a batch);
    notifications are acknowledged with 202 and no body. See
    services/claude_code_mcp.py for the protocol handling.
    """
    _require_enabled()

    try:
        body = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": claude_code_mcp.PARSE_ERROR, "message": "Parse error"},
            },
        )

    if isinstance(body, list):
        # JSON-RPC batch (allowed before protocol 2025-06-18)
        responses = []
        for message in body:
            response = await claude_code_mcp.handle_jsonrpc_message(message)
            if response is not None:
                responses.append(response)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(content=responses)

    response = await claude_code_mcp.handle_jsonrpc_message(body)
    if response is None:
        # Notification: acknowledged, no body
        return Response(status_code=202)
    return JSONResponse(content=response)


@mcp_router.get("/mcp")
async def mcp_get():
    """No server-initiated stream is offered (stateless server)."""
    _require_enabled()
    return Response(status_code=405, headers={"Allow": "POST"})


@mcp_router.delete("/mcp")
async def mcp_delete():
    """No sessions to terminate (stateless server)."""
    _require_enabled()
    return Response(status_code=405, headers={"Allow": "POST"})
