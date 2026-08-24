"""
Claude Code mode endpoints.

Called by the Claude Code lifecycle hooks shipped in claude-code-mode/ (not
by the frontend). Each endpoint is keyed on the Claude Code session ID and
is safe to call out of order: any of them will create the session's
conversation if the backend hasn't seen it yet (e.g. after a mid-session
backend restart).

Gated by CLAUDE_CODE_MODE_ENABLED (default off) — the hooks are written to
fail soft, so a disabled or unreachable backend degrades a Claude Code
session to a plain one rather than breaking it.

See docs/claude-code-mode.md for the full design.
"""

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import EntityConfig, settings
from app.database import get_db
from app.models import Message, MessageRole
from app.services import claude_code_mode as cc

router = APIRouter(prefix="/api/claude-code", tags=["claude-code"])

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
    conversation_id: str
    entity_id: str
    entity_label: str
    created: bool
    context: str


class RetrieveRequest(BaseModel):
    session_id: str
    prompt: str
    entity: Optional[str] = None
    cwd: Optional[str] = None


class RetrieveResponse(BaseModel):
    conversation_id: str
    human_message_id: Optional[str]
    context: str
    memories_retrieved: int


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
    Register a Claude Code session and return the entity's context block.

    A new conversation gets the full block (identity + system prompt +
    recent reflections). An existing one — a resumed or compacting session —
    gets an empty block: its transcript already carries the identity
    injection, so re-sending it would duplicate context.
    """
    _require_enabled()
    entity = _resolve_entity_or_400(data.entity)

    conversation, created = await cc.ensure_conversation(
        db, data.session_id, entity, cwd=data.cwd
    )

    context = ""
    if created:
        context = await cc.build_session_start_context(db, conversation, entity)

    return SessionStartResponse(
        conversation_id=str(conversation.id),
        entity_id=entity.index_name,
        entity_label=entity.label,
        created=created,
        context=context,
    )


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    data: RetrieveRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Record a user prompt and run automatic memory retrieval against it.

    The prompt is persisted (role=human) and vectorized like a native human
    message; the returned context block holds the retrieved memories for the
    UserPromptSubmit hook to inject.
    """
    _require_enabled()
    entity = _resolve_entity_or_400(data.entity)

    conversation, _ = await cc.ensure_conversation(
        db, data.session_id, entity, cwd=data.cwd
    )

    prompt = data.prompt or ""
    if not prompt.strip() or BARE_SLASH_COMMAND.match(prompt.strip()):
        return RetrieveResponse(
            conversation_id=str(conversation.id),
            human_message_id=None,
            context="",
            memories_retrieved=0,
        )

    human_msg = await cc.persist_and_vectorize_message(
        db,
        conversation,
        entity,
        role=MessageRole.HUMAN,
        content=prompt,
        token_count=cc.safe_token_count(prompt),
    )

    context, count = await cc.retrieve_for_prompt(db, conversation, entity, prompt)

    return RetrieveResponse(
        conversation_id=str(conversation.id),
        human_message_id=str(human_msg.id),
        context=context,
        memories_retrieved=count,
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
