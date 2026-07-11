import asyncio
import logging
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel

from app.database import get_db
from app.models import Message, MessageRole, Conversation
from app.services import memory_service
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memories"])

# The post-search SQL enrichment is a few primary-key lookups; if it takes this
# long the database is stuck and we fail the request instead of hanging it
SEARCH_ENRICHMENT_TIMEOUT_SECONDS = 15

# Only these roles are vectorized into Pinecone; tool exchanges and system
# messages live in SQL for conversation replay but are never memories, so the
# memory browser must not show them.
MEMORY_ROLES = (MessageRole.HUMAN, MessageRole.ASSISTANT, MessageRole.REFLECTION)


class MemoryResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    content_preview: str
    created_at: datetime
    times_retrieved: int
    last_retrieved_at: Optional[datetime]
    significance: float
    memory_status: Optional[str] = None

    class Config:
        from_attributes = True


class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = 10
    include_content: bool = True
    entity_id: Optional[str] = None  # Filter by entity (Pinecone index name)


class MemoryStats(BaseModel):
    total_count: int
    human_count: int
    assistant_count: int
    avg_times_retrieved: float
    max_times_retrieved: int
    most_significant: List[dict]
    retrieval_distribution: dict


def calculate_significance(
    times_retrieved: int,
    created_at: datetime,
    last_retrieved_at: Optional[datetime],
    memory_status: Optional[str] = None,
    role: Optional[str] = None,
) -> float:
    """
    Calculate dynamic significance based on retrieval patterns.

    significance = (1 + 0.1 * times_retrieved) * recency_factor * half_life_modifier

    Where:
    - times_retrieved: How many times this memory has been retrieved (weighted at 10%)
    - recency_factor: Boost based on how recently retrieved (decays over time)
    - half_life_modifier: Decay based on memory age (halves every N days);
      pinned memories (memory_status == "pinned") are exempt from age decay
    - role: memories saved via memory_save (role == "reflection") are multiplied
      by settings.reflection_significance_multiplier
    """
    now = datetime.utcnow()

    # Half-life modifier - older memories decay in significance
    # Starts at 1.0 and halves every significance_half_life_days
    half_life_modifier = 1.0
    if memory_status != "pinned":
        days_since_creation = (now - created_at).days
        half_life_modifier = 0.5 ** (days_since_creation / settings.significance_half_life_days)

    # Recency factor - boosts recently retrieved memories
    # Cap at 1 day minimum to prevent very recent retrievals from dominating
    recency_factor = 1.0
    if last_retrieved_at:
        days_since_retrieval = max((now - last_retrieved_at).days, 1)
        recency_factor = 1.0 + min(1.0 / days_since_retrieval, settings.recency_boost_strength)

    # Calculate significance (0.1 weight on times_retrieved to prevent retrieval
    # count from dominating; +1 base so never-retrieved memories aren't zeroed out)
    significance = (1 + 0.1 * times_retrieved) * recency_factor * half_life_modifier

    # Boost self-authored memories (saved via the memory_save tool)
    if role == "reflection":
        significance *= settings.reflection_significance_multiplier

    # Apply floor
    return max(significance, settings.significance_floor)


@router.get("/", response_model=List[MemoryResponse])
async def list_memories(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = 0,
    role: Optional[str] = None,
    entity_id: Optional[str] = None,
    sort_by: str = Query("significance", enum=["significance", "created_at", "times_retrieved"]),
):
    """
    List all memories with significance calculation.

    Args:
        entity_id: Optional filter by AI entity (Pinecone index name).
    """
    query = select(Message).where(Message.role.in_(MEMORY_ROLES))

    if role:
        try:
            role_enum = MessageRole(role)
        except ValueError:
            role_enum = None
        if role_enum not in MEMORY_ROLES:
            valid = ", ".join(r.value for r in MEMORY_ROLES)
            raise HTTPException(status_code=400, detail=f"role must be one of: {valid}")
        query = query.where(Message.role == role_enum)

    # Filter by entity by joining with Conversation
    if entity_id is not None:
        query = query.join(Conversation, Message.conversation_id == Conversation.id)
        query = query.where(Conversation.entity_id == entity_id)

    result = await db.execute(query)
    messages = result.scalars().all()

    # Calculate significance for each
    memories = []
    for msg in messages:
        significance = calculate_significance(
            msg.times_retrieved,
            msg.created_at,
            msg.last_retrieved_at,
            msg.memory_status,
            msg.role.value,
        )
        memories.append({
            "id": msg.id,
            "conversation_id": msg.conversation_id,
            "role": msg.role.value,
            "content": msg.content,
            "content_preview": msg.content[:200] if len(msg.content) > 200 else msg.content,
            "created_at": msg.created_at,
            "times_retrieved": msg.times_retrieved,
            "last_retrieved_at": msg.last_retrieved_at,
            "significance": significance,
            "memory_status": msg.memory_status,
        })

    # Sort
    if sort_by == "significance":
        memories.sort(key=lambda m: m["significance"], reverse=True)
    elif sort_by == "created_at":
        memories.sort(key=lambda m: m["created_at"], reverse=True)
    elif sort_by == "times_retrieved":
        memories.sort(key=lambda m: m["times_retrieved"], reverse=True)

    # Paginate
    memories = memories[offset:offset + limit]

    return [MemoryResponse(**m) for m in memories]


@router.post("/search")
async def search_memories(
    data: MemorySearchRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Semantic search over memories, mirroring the memory_query tool's retrieval:
    results are ranked purely by similarity (no significance re-ranking),
    memories from archived conversations and released memories are excluded,
    and extra candidates are fetched so that filtering doesn't shrink the
    result set. Unlike memory_query, browsing does NOT update retrieval
    tracking — researcher searches shouldn't feed back into significance.

    Args:
        data.entity_id: Optional filter by AI entity (Pinecone index name).
    """
    if not memory_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Memory system not configured. Set PINECONE_API_KEY in environment."
        )

    if data.entity_id == "multi-entity":
        raise HTTPException(
            status_code=400,
            detail=(
                "Memory search requires a specific entity: multi-entity mode "
                "has no memory index of its own. Select an entity and retry."
            ),
        )

    # Fetch more candidates than requested so archived-conversation,
    # released-memory, and orphan filtering doesn't shrink the result set.
    # Deliberate queries are short, semantically sparse strings, so they use
    # a lower similarity floor than automatic chat-context retrieval.
    candidates = await memory_service.search_memories(
        query=data.query,
        top_k=data.top_k * settings.retrieval_candidate_multiplier,
        entity_id=data.entity_id,
        similarity_threshold=settings.query_similarity_threshold,
    )

    if not candidates:
        return []

    logger.info(f"[MEMORY] Browser search: enriching {len(candidates)} candidates from SQL")

    async def _enrich() -> list:
        archived_ids = await memory_service.get_archived_conversation_ids(
            db, entity_id=data.entity_id
        )
        logger.info(
            f"[MEMORY] Browser search: filtering against {len(archived_ids)} archived conversation(s)"
        )

        results = []
        for candidate in candidates:
            if len(results) >= data.top_k:
                break

            if candidate.get("conversation_id") in archived_ids:
                continue

            full_data = await memory_service.get_full_memory_content(candidate["id"], db)
            if not full_data:
                # Orphaned in Pinecone (no SQL row) — nothing to show
                continue

            if full_data.get("memory_status") == "released":
                continue

            significance = calculate_significance(
                full_data["times_retrieved"],
                datetime.fromisoformat(full_data["created_at"]),
                datetime.fromisoformat(full_data["last_retrieved_at"]) if full_data["last_retrieved_at"] else None,
                full_data.get("memory_status"),
                full_data.get("role"),
            )
            item = {
                **full_data,
                "content_preview": full_data["content"][:200],
                "score": candidate["score"],
                "significance": significance,
            }
            if not data.include_content:
                item.pop("content")
            results.append(item)

        return results

    # The SQL enrichment is a handful of primary-key lookups and should be
    # near-instant; if the database blocks (e.g., connection pool exhausted,
    # locked file), surface a clear error instead of hanging the request forever.
    try:
        results = await asyncio.wait_for(_enrich(), timeout=SEARCH_ENRICHMENT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error(
            f"[MEMORY] Browser search timed out after {SEARCH_ENRICHMENT_TIMEOUT_SECONDS}s "
            "while loading memory content from SQL. The vector search succeeded, so the "
            "database is not responding (locked file or exhausted connection pool?)."
        )
        raise HTTPException(
            status_code=504,
            detail=(
                "Memory search timed out while loading memory content from the "
                "database (vector search succeeded). See server logs."
            ),
        )

    logger.info(f"[MEMORY] Browser search: returning {len(results)} memories")
    return results


@router.get("/stats", response_model=MemoryStats)
async def get_memory_stats(
    db: AsyncSession = Depends(get_db),
    entity_id: Optional[str] = None,
):
    """
    Get statistics about stored memories.

    Args:
        entity_id: Optional filter by AI entity (Pinecone index name).
    """
    # Build base query with optional entity filter; always restricted to
    # roles that are actually vectorized as memories
    def apply_entity_filter(query):
        query = query.where(Message.role.in_(MEMORY_ROLES))
        if entity_id is not None:
            return query.join(Conversation, Message.conversation_id == Conversation.id).where(
                Conversation.entity_id == entity_id
            )
        return query

    # Total counts
    total_query = apply_entity_filter(select(func.count(Message.id)))
    total_result = await db.execute(total_query)
    total_count = total_result.scalar()

    human_query = apply_entity_filter(
        select(func.count(Message.id)).where(Message.role == MessageRole.HUMAN)
    )
    human_result = await db.execute(human_query)
    human_count = human_result.scalar()

    assistant_query = apply_entity_filter(
        select(func.count(Message.id)).where(Message.role == MessageRole.ASSISTANT)
    )
    assistant_result = await db.execute(assistant_query)
    assistant_count = assistant_result.scalar()

    # Retrieval stats
    avg_query = apply_entity_filter(select(func.avg(Message.times_retrieved)))
    avg_result = await db.execute(avg_query)
    avg_times_retrieved = avg_result.scalar() or 0

    max_query = apply_entity_filter(select(func.max(Message.times_retrieved)))
    max_result = await db.execute(max_query)
    max_times_retrieved = max_result.scalar() or 0

    # Most significant memories
    messages_query = apply_entity_filter(select(Message))
    result = await db.execute(messages_query)
    messages = result.scalars().all()

    memories_with_sig = []
    for msg in messages:
        sig = calculate_significance(
            msg.times_retrieved, msg.created_at, msg.last_retrieved_at, role=msg.role.value
        )
        memories_with_sig.append({
            "id": msg.id,
            "content_preview": msg.content[:100],
            "times_retrieved": msg.times_retrieved,
            "significance": sig,
        })

    memories_with_sig.sort(key=lambda m: m["significance"], reverse=True)
    most_significant = memories_with_sig[:10]

    # Retrieval distribution (buckets)
    distribution = {"0": 0, "1-5": 0, "6-10": 0, "11-20": 0, "21+": 0}
    for msg in messages:
        count = msg.times_retrieved
        if count == 0:
            distribution["0"] += 1
        elif count <= 5:
            distribution["1-5"] += 1
        elif count <= 10:
            distribution["6-10"] += 1
        elif count <= 20:
            distribution["11-20"] += 1
        else:
            distribution["21+"] += 1

    return MemoryStats(
        total_count=total_count,
        human_count=human_count,
        assistant_count=assistant_count,
        avg_times_retrieved=round(avg_times_retrieved, 2),
        max_times_retrieved=max_times_retrieved,
        most_significant=most_significant,
        retrieval_distribution=distribution,
    )


@router.get("/status/health")
async def memory_health():
    """Check memory system health including entity information."""
    entities = settings.get_entities()
    default_entity = settings.get_default_entity()

    return {
        "configured": memory_service.is_configured(),
        "default_index": default_entity.index_name if memory_service.is_configured() else None,
        "entities": [entity.to_dict() for entity in entities],
        "retrieval_top_k": settings.retrieval_top_k,
        "similarity_threshold": settings.similarity_threshold,
        "query_similarity_threshold": settings.query_similarity_threshold,
        "recency_boost_strength": settings.recency_boost_strength,
    }


class OrphanedRecord(BaseModel):
    id: str
    metadata: Optional[dict] = None


class OrphanedRecordsResponse(BaseModel):
    entity_id: Optional[str]
    orphans_found: int
    orphans: List[OrphanedRecord]


class CleanupRequest(BaseModel):
    entity_id: Optional[str] = None
    dry_run: bool = True  # Default to dry run for safety


class CleanupResponse(BaseModel):
    entity_id: Optional[str]
    dry_run: bool
    orphans_found: int
    orphans_deleted: int
    errors: List[str]
    orphan_ids: List[str]


@router.get("/orphans", response_model=OrphanedRecordsResponse)
async def list_orphaned_records(
    db: AsyncSession = Depends(get_db),
    entity_id: Optional[str] = None,
):
    """
    List orphaned Pinecone records that don't exist in SQL database.

    Orphans typically occur when:
    - A conversation or message was deleted but Pinecone deletion failed
    - Database was restored from an older backup
    - Records were created during development/testing

    Args:
        entity_id: Optional filter by AI entity (Pinecone index name).
                   If not specified, uses the default entity.
    """
    if not memory_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Memory system not configured. Set PINECONE_API_KEY in environment."
        )

    orphans = await memory_service.find_orphaned_records(db, entity_id)

    return OrphanedRecordsResponse(
        entity_id=entity_id,
        orphans_found=len(orphans),
        orphans=[OrphanedRecord(id=o["id"], metadata=o["metadata"]) for o in orphans],
    )


@router.post("/orphans/cleanup", response_model=CleanupResponse)
async def cleanup_orphaned_records(
    data: CleanupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Clean up orphaned Pinecone records that don't exist in SQL database.

    By default runs in dry_run mode which only reports what would be deleted.
    Set dry_run=false to actually delete the orphaned records.

    Args:
        entity_id: Optional filter by AI entity (Pinecone index name).
                   If not specified, uses the default entity.
        dry_run: If true (default), only report what would be deleted.
                 If false, actually delete the orphaned records.
    """
    if not memory_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Memory system not configured. Set PINECONE_API_KEY in environment."
        )

    result = await memory_service.cleanup_orphaned_records(
        db=db,
        entity_id=data.entity_id,
        dry_run=data.dry_run,
    )

    return CleanupResponse(**result)


class QueryLinkCleanupRequest(BaseModel):
    dry_run: bool = True


class QueryLinkCleanupResponse(BaseModel):
    dry_run: bool
    conversations_with_query_results: int
    links_matched: int
    links_deleted: int


@router.post("/query-links/cleanup", response_model=QueryLinkCleanupResponse)
async def cleanup_query_links(
    data: Optional[QueryLinkCleanupRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    One-time cleanup of stale ConversationMemoryLinks created by memory_query.

    memory_query no longer records links (its results are not context
    memories), but links from before that fix make session reload inject the
    query results into the rebuilt context mid-history, duplicating them and
    busting the prompt cache. This scans persisted memory_query tool results
    for memory-ID markers and deletes the matching links.

    The body is optional: a bare POST (or {}) runs in dry_run mode, which
    only reports what would be deleted. Send {"dry_run": false} to actually
    delete the links. SQL-only — works without Pinecone configured.
    """
    result = await memory_service.cleanup_memory_query_links(
        db=db,
        dry_run=data.dry_run if data is not None else True,
    )
    return QueryLinkCleanupResponse(**result)


class MemoryStatusUpdate(BaseModel):
    # "pinned", "released", or null to clear the override
    status: Optional[str] = None


@router.get("/overrides", response_model=List[MemoryResponse])
async def list_memory_overrides(
    db: AsyncSession = Depends(get_db),
    entity_id: Optional[str] = None,
):
    """
    List memories with an entity-set status override (pinned or released).

    Pinned memories are exempt from age-based significance decay.
    Released memories are excluded from retrieval.

    These are normally set by the entity itself (memory_mark/memory_release
    tools); this endpoint gives the researcher visibility, and PUT
    /{memory_id}/status allows changing them. Treat overriding the entity's
    own choices as an emergency option.
    """
    query = select(Message).where(Message.memory_status.isnot(None))

    if entity_id is not None:
        query = query.join(Conversation, Message.conversation_id == Conversation.id)
        query = query.where(Conversation.entity_id == entity_id)

    result = await db.execute(query.order_by(Message.created_at.desc()))
    messages = result.scalars().all()

    return [
        MemoryResponse(
            id=msg.id,
            conversation_id=msg.conversation_id,
            role=msg.role.value,
            content=msg.content,
            content_preview=msg.content[:200] if len(msg.content) > 200 else msg.content,
            created_at=msg.created_at,
            times_retrieved=msg.times_retrieved,
            last_retrieved_at=msg.last_retrieved_at,
            significance=calculate_significance(
                msg.times_retrieved, msg.created_at, msg.last_retrieved_at, msg.memory_status, msg.role.value
            ),
            memory_status=msg.memory_status,
        )
        for msg in messages
    ]


@router.put("/{memory_id}/status")
async def set_memory_status(
    memory_id: str,
    data: MemoryStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Set or clear a memory's status: "pinned", "released", or null (normal).

    This can override the entity's own memory_mark/memory_release choices;
    intended as an emergency/maintenance option.
    """
    if data.status not in (None, "pinned", "released"):
        raise HTTPException(status_code=400, detail="status must be 'pinned', 'released', or null")

    result = await db.execute(select(Message).where(Message.id == memory_id))
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail="Memory not found")

    success = await memory_service.set_memory_status(memory_id, data.status, db)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update memory status")

    return {"id": memory_id, "memory_status": data.status}


# NOTE: Parameterized routes must come AFTER static routes to avoid matching
# e.g., /orphans being interpreted as /{memory_id}

@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific memory by ID."""
    result = await db.execute(
        select(Message).where(Message.id == memory_id)
    )
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Memory not found")

    significance = calculate_significance(
        message.times_retrieved,
        message.created_at,
        message.last_retrieved_at,
        message.memory_status,
        message.role.value,
    )

    return MemoryResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role.value,
        content=message.content,
        content_preview=message.content[:200] if len(message.content) > 200 else message.content,
        created_at=message.created_at,
        times_retrieved=message.times_retrieved,
        last_retrieved_at=message.last_retrieved_at,
        significance=significance,
        memory_status=message.memory_status,
    )


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a specific memory.

    This removes the memory from both the SQL database and vector store.
    """
    # Get message with its conversation to determine entity_id
    result = await db.execute(
        select(Message, Conversation)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Message.id == memory_id)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Memory not found")

    message, conversation = row

    # Delete from vector store for the correct entity
    if memory_service.is_configured():
        await memory_service.delete_memory(memory_id, entity_id=conversation.entity_id)

    # Delete from SQL
    await db.delete(message)
    await db.commit()

    return {"status": "deleted", "id": memory_id}
