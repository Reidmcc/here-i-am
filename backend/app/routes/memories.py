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

router = APIRouter(prefix="/api/memories", tags=["memories"])


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
    last_retrieved_at: Optional[datetime]
) -> float:
    """
    Calculate dynamic significance based on retrieval patterns.

    significance = times_retrieved * recency_factor * half_life_modifier

    Where:
    - times_retrieved: How many times this memory has been retrieved
    - recency_factor: Boost based on how recently retrieved (decays over time)
    - half_life_modifier: Decay based on memory age (halves every N days)
    """
    now = datetime.utcnow()

    # Half-life modifier - older memories decay in significance
    # Starts at 1.0 and halves every significance_half_life_days
    days_since_creation = (now - created_at).days
    half_life_modifier = 0.5 ** (days_since_creation / settings.significance_half_life_days)

    # Recency factor - boosts recently retrieved memories
    # Cap at 1 day minimum to prevent very recent retrievals from dominating
    recency_factor = 1.0
    if last_retrieved_at:
        days_since_retrieval = max((now - last_retrieved_at).days, 1)
        recency_factor = 1.0 + min(1.0 / days_since_retrieval, settings.recency_boost_strength)

    # Calculate significance
    significance = times_retrieved * recency_factor * half_life_modifier

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
    query = select(Message)

    if role:
        role_enum = MessageRole.HUMAN if role == "human" else MessageRole.ASSISTANT
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
            msg.last_retrieved_at
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
    Semantic search over memories.

    Args:
        data.entity_id: Optional filter by AI entity (Pinecone index name).
    """
    if not memory_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Memory system not configured. Set PINECONE_API_KEY in environment."
        )

    # Search vector database for the specified entity
    results = await memory_service.search_memories(
        query=data.query,
        top_k=data.top_k,
        entity_id=data.entity_id,
    )

    # Enrich with full content if requested
    if data.include_content:
        enriched = []
        for result in results:
            full_data = await memory_service.get_full_memory_content(result["id"], db)
            if full_data:
                significance = calculate_significance(
                    full_data["times_retrieved"],
                    datetime.fromisoformat(full_data["created_at"]),
                    datetime.fromisoformat(full_data["last_retrieved_at"]) if full_data["last_retrieved_at"] else None
                )
                enriched.append({
                    **full_data,
                    "score": result["score"],
                    "significance": significance,
                })
        return enriched

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
    # Build base query with optional entity filter
    def apply_entity_filter(query):
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
        sig = calculate_significance(msg.times_retrieved, msg.created_at, msg.last_retrieved_at)
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
        message.last_retrieved_at
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
