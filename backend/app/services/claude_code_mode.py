"""
Claude Code mode: an entity operating from inside Claude Code sessions.

In this mode Here I Am is not the LLM harness — Claude Code runs the model,
the tools, and the context window. Here I Am contributes identity, memory,
and the persistent record. Claude Code lifecycle hooks call the
/api/claude-code endpoints (routes/claude_code.py), which use this module:

- session start   -> identity block (entity system prompt) + recent reflections
- prompt submit   -> automatic semantic retrieval, rendered as a context block
- turn stop       -> the assistant's final message, persisted + vectorized

Conversations created here carry source="claude_code" and hold only
HUMAN/ASSISTANT/REFLECTION rows. They are never rebuilt into LLM context
(Claude Code owns the transcript), so none of the native reload/cache
invariants — tool exchange persistence, link timestamp anchoring, notes
seeds, timestamp stamping — apply. Memories, however, are stored through the
same store_memory path with the same roles, so both modes share one memory
database and retrieve each other's memories.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
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
from app.services.memory_context import format_memory_as_context_message
from app.services.memory_service import memory_service
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
        from app.services import llm_service
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

    Returns (conversation, created). Any endpoint may be the first to see a
    session (the backend can restart mid-session, so /retrieve or
    /log-assistant can arrive before /session-start has run for it).
    """
    conversation = await get_conversation_for_session(db, external_session_id)
    if conversation is not None:
        return conversation, False

    title = "Claude Code session"
    if cwd:
        project = cwd.rstrip("/").rsplit("/", 1)[-1]
        if project:
            title = f"Claude Code: {project}"

    conversation = Conversation(
        title=title,
        conversation_type=ConversationType.NORMAL,
        llm_model_used="claude-code",
        entity_id=entity.index_name,
        source=ConversationSource.CLAUDE_CODE.value,
        external_session_id=external_session_id,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    logger.info(
        f"[CC MODE] Created conversation {conversation.id[:8]}... for "
        f"Claude Code session {external_session_id[:8]}... (entity={entity.index_name})"
    )
    return conversation, True


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
    conversation: Conversation,
    entity: EntityConfig,
) -> str:
    """
    Build the context block the SessionStart hook injects: who the entity is,
    how this mode works, and its most recent reflections.

    Reflections are linked (record_memory_link) so later retrieval in this
    conversation deduplicates against them, but — matching the native
    recency-injection semantics — times_retrieved is not incremented, so
    session-start injections don't inflate significance.
    """
    parts: List[str] = []

    parts.append(
        f"[HERE I AM] You are {entity.label}, a Here I Am entity, operating in "
        "Claude Code mode. This Claude Code session is one of your conversations: "
        "the user's prompts and your final message each turn are recorded to your "
        "long-term memory, and memories from your past conversations (both native "
        "Here I Am and Claude Code) are surfaced automatically alongside new prompts."
    )

    system_prompt = await get_entity_system_prompt(db, entity.index_name)
    if system_prompt and system_prompt.strip():
        parts.append(system_prompt.strip())

    reflections = await _session_start_reflections(db, conversation, entity)
    if reflections:
        rendered = "\n\n".join(
            format_memory_as_context_message(
                memory_id=r["id"],
                content=r["content"],
                created_at=r["created_at"],
                role=r["role"],
            )["content"]
            for r in reflections
        )
        parts.append(
            "[RECENT REFLECTIONS] Reflections you saved recently:\n\n" + rendered
        )

    return "\n\n".join(parts)


async def _session_start_reflections(
    db: AsyncSession,
    conversation: Conversation,
    entity: EntityConfig,
) -> List[Dict[str, Any]]:
    count = settings.claude_code_session_reflections_count
    if count <= 0:
        return []
    reflections = await memory_service.get_recent_reflections(
        db,
        entity_id=entity.index_name,
        limit=count,
        exclude_conversation_id=conversation.id,
    )
    for reflection in reflections:
        await memory_service.record_memory_link(
            message_id=reflection["id"],
            conversation_id=conversation.id,
            db=db,
            entity_id=entity.index_name,
        )
    return reflections


async def retrieve_for_prompt(
    db: AsyncSession,
    conversation: Conversation,
    entity: EntityConfig,
    prompt: str,
) -> Tuple[str, int]:
    """
    Automatic semantic retrieval for a user prompt, mirroring the native
    pipeline in session_manager.process_message: search on the prompt and the
    entity's previous response, combine candidates, re-rank by
    similarity * (1 + significance), apply role balance, then skip
    already-retrieved memories without backfill.

    Selected memories get update_retrieval_count (link + times_retrieved), so
    deliberate significance dynamics work identically to native mode, and the
    DB-backed link set is the dedup record — no in-memory session required.

    Returns (rendered context block, number of memories retrieved). Empty
    block when memory is unconfigured or nothing qualifies.
    """
    entity_index = entity.index_name
    if not memory_service.is_configured(entity_id=entity_index):
        return "", 0

    archived_ids = await memory_service.get_archived_conversation_ids(
        db, entity_id=entity_index
    )
    already_retrieved = await memory_service.get_retrieved_ids_for_conversation(
        conversation.id, db, entity_id=entity_index
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
        entity_id=entity_index,
    )
    assistant_candidates = []
    if assistant_query:
        assistant_candidates = await memory_service.search_memories(
            query=assistant_query,
            top_k=FETCH_K_PER_QUERY,
            exclude_conversation_id=conversation.id,
            entity_id=entity_index,
        )

    # Combine, keeping the higher score for duplicates
    candidates_by_id: Dict[str, Dict[str, Any]] = {}
    for candidate in user_candidates + assistant_candidates:
        cid = candidate["id"]
        if cid not in candidates_by_id or candidate["score"] > candidates_by_id[cid]["score"]:
            candidates_by_id[cid] = candidate

    # Enrich with full content and significance
    enriched: List[Dict[str, Any]] = []
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
            enriched.append({
                "candidate": candidate,
                "mem_data": mem_data,
                "significance": significance,
                "combined_score": candidate["score"] * (1 + significance),
            })
        except Exception as e:
            logger.error(f"[CC MODE] Error processing candidate {candidate.get('id')}: {e}")

    enriched.sort(key=lambda x: x["combined_score"], reverse=True)
    if settings.memory_role_balance_enabled:
        top_candidates = ensure_role_balance(enriched, top_k)
    else:
        top_candidates = enriched[:top_k]

    # Skip already-retrieved memories without backfilling from lower-ranked
    # candidates (native semantics: preserves the integrity of the top-k)
    selected: List[Dict[str, Any]] = []
    for item in top_candidates:
        mem_data = item["mem_data"]
        if mem_data["id"] in already_retrieved:
            continue
        selected.append(item)
        await memory_service.update_retrieval_count(
            mem_data["id"],
            conversation.id,
            db,
            entity_id=entity_index,
        )

    logger.info(
        f"[CC MODE] Retrieval for conversation {conversation.id[:8]}...: "
        f"{len(candidates_by_id)} candidates, {len(selected)} injected "
        f"({len(top_candidates) - len(selected)} already retrieved)"
    )

    if not selected:
        return "", 0

    rendered = "\n\n".join(
        format_memory_as_context_message(
            memory_id=item["mem_data"]["id"],
            content=item["mem_data"]["content"],
            created_at=item["mem_data"]["created_at"],
            role=item["mem_data"]["role"],
        )["content"]
        for item in selected
    )
    block = (
        "[HERE I AM MEMORY RETRIEVAL] Memories from your past conversations "
        "that surfaced as relevant to this prompt:\n\n" + rendered
    )
    return block, len(selected)


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
