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


class SessionStartRequest(BaseModel):
    session_id: str
    entity: Optional[str] = None  # index name or label; None = default entity
    cwd: Optional[str] = None
    source: Optional[str] = None  # Claude Code's startup|resume|clear|compact


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
    # hook must keep its stdout under Claude Code's inline budget (oversized
    # hook output is silently truncated to a preview): when the combined
    # blocks don't fit, the hook writes this to a file and prints a loud
    # pointer instead.
    bulk_context: str = ""


class PeerMessage(BaseModel):
    """
    One inter-session message (a SendMessage delivery from a sibling Claude
    Code session), extracted from the prompt by the UserPromptSubmit hook.
    Another session of this entity speaking — recorded under the entity's
    own name with the sending session marked (issue #312), never as the
    human's words.
    """
    content: str
    # The sending session's display name (the wrapper's from-name attribute)
    sender: Optional[str] = None


class RetrieveRequest(BaseModel):
    session_id: str
    prompt: str
    entity: Optional[str] = None
    cwd: Optional[str] = None
    # Inter-session messages that rode in with (or stood in for) the prompt
    peer_messages: List[PeerMessage] = []


class RetrieveResponse(BaseModel):
    conversation_id: str
    human_message_id: Optional[str]
    context: str
    memories_retrieved: int
    # One line per retrieved memory; the hook prints it in place of an
    # oversized `context` it had to spill to a file
    context_summary: str = ""
    # Reflections the entity saved in other sessions since this conversation
    # began, not yet surfaced here. The hook prints a one-line mailbox flag
    # when nonzero; the entity pulls the content with memory_query
    # mode="recent" if it wants it.
    new_sibling_reflections: int = 0
    # Rows created for peer_messages, in input order
    peer_message_ids: List[str] = []


class LogAssistantRequest(BaseModel):
    session_id: str
    content: str
    entity: Optional[str] = None
    cwd: Optional[str] = None
    message_uuid: Optional[str] = None  # transcript entry UUID, for idempotency


class LogAssistantResponse(BaseModel):
    conversation_id: str
    message_id: Optional[str]
    deduplicated: bool


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
    """
    _require_enabled()
    entity = _resolve_entity_or_400(data.entity)

    conversation = await cc.get_conversation_for_session(db, data.session_id)
    if conversation is None and data.source == "compact":
        # A compaction implies a session with recorded history; if its row
        # is missing anyway (e.g. it ran while the backend was down), the
        # session is clearly a speaking one — register it now
        conversation, _ = await cc.ensure_conversation(
            db, data.session_id, entity, cwd=data.cwd
        )

    context = ""
    bulk_context = ""
    fresh = conversation is None
    if fresh:
        conversation_id = cc.conversation_id_for_session(data.session_id)
        context, bulk_context = await cc.build_session_start_context(
            db, conversation_id, entity
        )
    else:
        conversation_id = str(conversation.id)
        if data.source == "compact":
            # Stamp the eligibility boundary first: links recorded/refreshed
            # by the post-compact injection must land after it (see
            # mark_conversation_compacted)
            await cc.mark_conversation_compacted(db, conversation)
            context, bulk_context = await cc.build_post_compact_context(
                db, conversation, entity
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
        bulk_context=bulk_context,
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

    conversation, _ = await cc.ensure_conversation(
        db, data.session_id, entity, cwd=data.cwd
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
        )

    human_msg = None
    if record_prompt:
        human_msg = await cc.persist_and_vectorize_message(
            db,
            conversation,
            entity,
            role=MessageRole.HUMAN,
            content=prompt,
            token_count=cc.safe_token_count(prompt),
        )

    peer_message_ids: List[str] = []
    for peer in peer_messages:
        peer_msg = await cc.persist_and_vectorize_message(
            db,
            conversation,
            entity,
            role=MessageRole.ASSISTANT,
            content=peer.content,
            token_count=cc.safe_token_count(peer.content),
            sibling_session=(peer.sender or "").strip() or "unknown session",
        )
        peer_message_ids.append(str(peer_msg.id))

    # Retrieval runs against the whole turn's new content — the human's
    # words and/or the sibling's letter (retrieve_for_prompt excludes this
    # conversation, so the rows just recorded can't surface as results)
    query_parts = [prompt] if record_prompt else []
    query_parts.extend(peer.content for peer in peer_messages)
    context, count, summary = await cc.retrieve_for_prompt(
        db, conversation, entity, "\n\n".join(query_parts)
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
        context=context,
        memories_retrieved=count,
        context_summary=summary,
        new_sibling_reflections=sibling_reflections,
        peer_message_ids=peer_message_ids,
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

    conversation, _ = await cc.ensure_conversation(
        db, data.session_id, entity, cwd=data.cwd
    )

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
