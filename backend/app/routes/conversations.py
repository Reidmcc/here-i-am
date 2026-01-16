import logging
import json
import asyncio
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pydantic import BaseModel

from app.database import get_db, async_session_maker
from app.models import Conversation, Message, ConversationType, MessageRole, ConversationEntity
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

# Batch size for committing during imports to prevent memory exhaustion
IMPORT_BATCH_SIZE = 50


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    conversation_type: str = "normal"
    system_prompt: Optional[str] = None  # Legacy: single system prompt (fallback)
    model: str = "claude-sonnet-4-5-20250929"
    entity_id: Optional[str] = None  # Pinecone index name for the AI entity
    # For multi-entity conversations: list of entity IDs to include
    entity_ids: Optional[List[str]] = None
    # Per-entity system prompts: { entity_id: system_prompt, ... }
    entity_system_prompts: Optional[dict] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None


class EntityInfo(BaseModel):
    """Information about an entity in a multi-entity conversation."""
    entity_id: str
    label: str
    description: Optional[str] = None
    llm_provider: str = "anthropic"
    default_model: Optional[str] = None


class ConversationResponse(BaseModel):
    id: str
    created_at: datetime
    updated_at: Optional[datetime]
    title: Optional[str]
    tags: Optional[List[str]]
    conversation_type: str
    system_prompt_used: Optional[str]
    llm_model_used: str
    notes: Optional[str]
    entity_id: Optional[str] = None  # Pinecone index name for the AI entity
    is_archived: bool = False
    entity_missing: bool = False  # True if entity_id references a non-existent entity
    message_count: int = 0
    preview: Optional[str] = None
    # For multi-entity conversations: list of participating entities
    entities: Optional[List[EntityInfo]] = None
    # Per-entity system prompts: { entity_id: system_prompt, ... }
    entity_system_prompts: Optional[dict] = None
    # External link fields (for OGS game integration, etc.)
    external_link_type: Optional[str] = None
    external_link_id: Optional[str] = None
    external_link_metadata: Optional[dict] = None

    class Config:
        from_attributes = True


def check_entity_exists(entity_id: Optional[str]) -> bool:
    """Check if an entity_id references a configured entity."""
    if entity_id is None:
        return True  # NULL means default entity, always valid
    return settings.get_entity_by_index(entity_id) is not None


def get_entity_info(entity_id: str) -> Optional[EntityInfo]:
    """Get EntityInfo for a given entity_id."""
    entity = settings.get_entity_by_index(entity_id)
    if not entity:
        return None
    return EntityInfo(
        entity_id=entity.index_name,
        label=entity.label,
        description=entity.description,
        llm_provider=entity.llm_provider,
        default_model=entity.default_model,
    )


def get_entity_label(entity_id: str) -> Optional[str]:
    """Get the human-readable label for an entity."""
    entity = settings.get_entity_by_index(entity_id)
    return entity.label if entity else None


async def get_conversation_entities(conversation_id: str, db: AsyncSession) -> List[EntityInfo]:
    """Get the list of entities participating in a multi-entity conversation."""
    result = await db.execute(
        select(ConversationEntity)
        .where(ConversationEntity.conversation_id == conversation_id)
        .order_by(ConversationEntity.display_order)
    )
    conv_entities = result.scalars().all()

    entities_info = []
    for ce in conv_entities:
        info = get_entity_info(ce.entity_id)
        if info:
            entities_info.append(info)
    return entities_info


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    token_count: Optional[int]
    times_retrieved: int
    last_retrieved_at: Optional[datetime]
    # For multi-entity conversations: which entity spoke this message
    speaker_entity_id: Optional[str] = None
    speaker_label: Optional[str] = None  # Human-readable label for the speaker

    class Config:
        from_attributes = True


class ConversationExport(BaseModel):
    id: str
    created_at: str
    title: Optional[str]
    tags: Optional[List[str]]
    conversation_type: str
    system_prompt_used: Optional[str]
    llm_model_used: str
    notes: Optional[str]
    entity_id: Optional[str] = None
    messages: List[dict]


class SeedConversationImport(BaseModel):
    """Import format for seed conversations."""
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    conversation_type: str = "normal"
    system_prompt_used: Optional[str] = None
    llm_model_used: str = "claude-sonnet-4-5-20250929"
    notes: Optional[str] = None
    entity_id: Optional[str] = None  # Pinecone index name for the AI entity
    # Messages format: {role: str, content: str, id?: str, times_retrieved?: int, created_at?: str (ISO format)}
    # If 'id' is provided, it will be used for deduplication - messages with existing IDs will be skipped
    messages: List[dict]


@router.post("/", response_model=ConversationResponse)
async def create_conversation(
    data: ConversationCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new conversation."""
    # Check if this is a multi-entity conversation
    is_multi_entity = data.conversation_type == "multi_entity" or (data.entity_ids and len(data.entity_ids) > 1)

    if is_multi_entity:
        # Validate all entity_ids
        if not data.entity_ids or len(data.entity_ids) < 2:
            raise HTTPException(
                status_code=400,
                detail="Multi-entity conversations require at least 2 entities."
            )

        for eid in data.entity_ids:
            entity = settings.get_entity_by_index(eid)
            if not entity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Entity '{eid}' is not configured. Check your PINECONE_INDEXES environment variable."
                )

        conv_type = ConversationType.MULTI_ENTITY

        # For multi-entity, entity_id is set to a special marker
        conversation = Conversation(
            title=data.title,
            tags=data.tags,
            conversation_type=conv_type,
            system_prompt_used=data.system_prompt,
            llm_model_used=data.model,
            entity_id="multi-entity",  # Special marker for multi-entity conversations
            entity_system_prompts=data.entity_system_prompts,
        )

        db.add(conversation)
        await db.flush()  # Get the conversation ID

        # Add the participating entities
        for order, eid in enumerate(data.entity_ids):
            conv_entity = ConversationEntity(
                conversation_id=conversation.id,
                entity_id=eid,
                display_order=order,
            )
            db.add(conv_entity)

        await db.commit()
        await db.refresh(conversation)

        # Get entity info for response
        entities_info = [get_entity_info(eid) for eid in data.entity_ids]
        entities_info = [e for e in entities_info if e is not None]

        return ConversationResponse(
            id=conversation.id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            title=conversation.title,
            tags=conversation.tags,
            conversation_type=conversation.conversation_type.value,
            system_prompt_used=conversation.system_prompt_used,
            llm_model_used=conversation.llm_model_used,
            notes=conversation.notes,
            entity_id=conversation.entity_id,
            is_archived=conversation.is_archived,
            entity_missing=False,
            message_count=0,
            entities=entities_info,
            entity_system_prompts=conversation.entity_system_prompts,
        )
    else:
        # Standard single-entity conversation
        # Validate entity_id if provided
        if data.entity_id:
            entity = settings.get_entity_by_index(data.entity_id)
            if not entity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Entity '{data.entity_id}' is not configured. Check your PINECONE_INDEXES environment variable."
                )

        conv_type = ConversationType.REFLECTION if data.conversation_type == "reflection" else ConversationType.NORMAL

        conversation = Conversation(
            title=data.title,
            tags=data.tags,
            conversation_type=conv_type,
            system_prompt_used=data.system_prompt,
            llm_model_used=data.model,
            entity_id=data.entity_id,
            entity_system_prompts=data.entity_system_prompts,
        )

        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

        return ConversationResponse(
            id=conversation.id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            title=conversation.title,
            tags=conversation.tags,
            conversation_type=conversation.conversation_type.value,
            system_prompt_used=conversation.system_prompt_used,
            llm_model_used=conversation.llm_model_used,
            notes=conversation.notes,
            entity_id=conversation.entity_id,
            is_archived=conversation.is_archived,
            entity_missing=not check_entity_exists(conversation.entity_id),
            message_count=0,
            entity_system_prompts=conversation.entity_system_prompts,
        )


@router.get("/", response_model=List[ConversationResponse])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    entity_id: Optional[str] = None,
    include_archived: bool = False,
):
    """
    List all conversations with message counts and previews.

    Args:
        entity_id: Optional filter by AI entity (Pinecone index name).
                   Use "multi-entity" to list multi-entity conversations.
                   If not provided, returns all conversations.
        include_archived: If True, include archived conversations. Default False.
    """
    query = select(Conversation)

    # Filter by entity_id if provided
    if entity_id is not None:
        query = query.where(Conversation.entity_id == entity_id)

    # Exclude archived by default
    if not include_archived:
        query = query.where(Conversation.is_archived == False)

    # Always exclude imported conversations from the list (they only serve as memory sources)
    query = query.where(Conversation.is_imported == False)

    query = query.order_by(
        Conversation.updated_at.desc().nullsfirst(),
        Conversation.created_at.desc()
    ).limit(limit).offset(offset)

    result = await db.execute(query)
    conversations = result.scalars().all()

    response = []
    deleted_empty = False
    for conv in conversations:
        # Get message count
        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
        )
        messages = msg_result.scalars().all()
        message_count = len(messages)

        # Clean up empty conversations (no messages ever sent)
        if message_count == 0:
            await db.delete(conv)
            deleted_empty = True
            continue

        # Get preview from first human message
        preview = None
        for msg in messages:
            if msg.role == MessageRole.HUMAN:
                preview = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
                break

        # Get entity info for multi-entity conversations
        entities_info = None
        if conv.conversation_type == ConversationType.MULTI_ENTITY:
            entities_info = await get_conversation_entities(conv.id, db)

        response.append(ConversationResponse(
            id=conv.id,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            title=conv.title,
            tags=conv.tags,
            conversation_type=conv.conversation_type.value,
            system_prompt_used=conv.system_prompt_used,
            llm_model_used=conv.llm_model_used,
            notes=conv.notes,
            entity_id=conv.entity_id,
            is_archived=conv.is_archived,
            entity_missing=not check_entity_exists(conv.entity_id) if conv.entity_id != "multi-entity" else False,
            message_count=message_count,
            preview=preview,
            entities=entities_info,
            entity_system_prompts=conv.entity_system_prompts,
        ))

    # Commit any deleted empty conversations
    if deleted_empty:
        await db.commit()

    return response


@router.get("/archived", response_model=List[ConversationResponse])
async def list_archived_conversations(
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    entity_id: Optional[str] = None,
):
    """
    List archived conversations.

    Args:
        entity_id: Optional filter by AI entity (Pinecone index name).
    """
    query = select(Conversation).where(Conversation.is_archived == True)

    # Exclude imported conversations from archived list
    query = query.where(Conversation.is_imported == False)

    if entity_id is not None:
        query = query.where(Conversation.entity_id == entity_id)

    query = query.order_by(
        Conversation.updated_at.desc().nullsfirst(),
        Conversation.created_at.desc()
    ).limit(limit).offset(offset)

    result = await db.execute(query)
    conversations = result.scalars().all()

    response = []
    for conv in conversations:
        msg_result = await db.execute(
            select(Message).where(Message.conversation_id == conv.id)
        )
        messages = msg_result.scalars().all()
        message_count = len(messages)

        preview = None
        for msg in messages:
            if msg.role == MessageRole.HUMAN:
                preview = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
                break

        response.append(ConversationResponse(
            id=conv.id,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            title=conv.title,
            tags=conv.tags,
            conversation_type=conv.conversation_type.value,
            system_prompt_used=conv.system_prompt_used,
            llm_model_used=conv.llm_model_used,
            notes=conv.notes,
            entity_id=conv.entity_id,
            is_archived=conv.is_archived,
            entity_missing=not check_entity_exists(conv.entity_id),
            message_count=message_count,
            preview=preview,
            entity_system_prompts=conv.entity_system_prompts,
        ))

    return response


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific conversation."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get message count
    msg_result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id)
    )
    messages = msg_result.scalars().all()

    preview = None
    for msg in messages:
        if msg.role == MessageRole.HUMAN:
            preview = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
            break

    # Get entity info for multi-entity conversations
    entities_info = None
    if conversation.conversation_type == ConversationType.MULTI_ENTITY:
        entities_info = await get_conversation_entities(conversation.id, db)

    return ConversationResponse(
        id=conversation.id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        title=conversation.title,
        tags=conversation.tags,
        conversation_type=conversation.conversation_type.value,
        system_prompt_used=conversation.system_prompt_used,
        llm_model_used=conversation.llm_model_used,
        notes=conversation.notes,
        entity_id=conversation.entity_id,
        is_archived=conversation.is_archived,
        entity_missing=not check_entity_exists(conversation.entity_id) if conversation.entity_id != "multi-entity" else False,
        message_count=len(messages),
        preview=preview,
        entities=entities_info,
        entity_system_prompts=conversation.entity_system_prompts,
    )


@router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_conversation_messages(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get all messages in a conversation."""
    # Verify conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get messages
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    messages = result.scalars().all()

    return [
        MessageResponse(
            id=msg.id,
            role=msg.role.value,
            content=msg.content,
            created_at=msg.created_at,
            token_count=msg.token_count,
            times_retrieved=msg.times_retrieved,
            last_retrieved_at=msg.last_retrieved_at,
            speaker_entity_id=msg.speaker_entity_id,
            speaker_label=get_entity_label(msg.speaker_entity_id) if msg.speaker_entity_id else None,
        )
        for msg in messages
    ]


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update conversation metadata."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if data.title is not None:
        conversation.title = data.title
    if data.tags is not None:
        conversation.tags = data.tags
    if data.notes is not None:
        conversation.notes = data.notes

    conversation.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(conversation)

    # Get message count
    msg_result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id)
    )
    message_count = len(msg_result.scalars().all())

    return ConversationResponse(
        id=conversation.id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        title=conversation.title,
        tags=conversation.tags,
        conversation_type=conversation.conversation_type.value,
        system_prompt_used=conversation.system_prompt_used,
        llm_model_used=conversation.llm_model_used,
        notes=conversation.notes,
        entity_id=conversation.entity_id,
        is_archived=conversation.is_archived,
        entity_missing=not check_entity_exists(conversation.entity_id),
        message_count=message_count,
        entity_system_prompts=conversation.entity_system_prompts,
    )


@router.post("/{conversation_id}/archive")
async def archive_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Archive a conversation.

    Archived conversations are hidden from the main list and their messages
    are excluded from memory retrieval. All data is preserved.
    """
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.is_archived:
        raise HTTPException(status_code=400, detail="Conversation is already archived")

    conversation.is_archived = True
    conversation.updated_at = datetime.utcnow()
    await db.commit()

    return {"status": "archived", "id": conversation_id}


@router.post("/{conversation_id}/unarchive")
async def unarchive_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Unarchive a conversation.

    Restores the conversation to the main list and re-enables memory retrieval
    for its messages.
    """
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not conversation.is_archived:
        raise HTTPException(status_code=400, detail="Conversation is not archived")

    conversation.is_archived = False
    conversation.updated_at = datetime.utcnow()
    await db.commit()

    return {"status": "unarchived", "id": conversation_id}


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Permanently delete a conversation and all its messages.

    This action cannot be undone. The conversation must be archived first
    to prevent accidental deletion of active conversations.

    Also deletes associated memories from the vector database.
    """
    from app.services import memory_service

    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not conversation.is_archived:
        raise HTTPException(
            status_code=400,
            detail="Conversation must be archived before deletion. Archive it first."
        )

    entity_id = conversation.entity_id

    # Get all message IDs for this conversation (to delete from vector store)
    msg_result = await db.execute(
        select(Message.id).where(Message.conversation_id == conversation_id)
    )
    message_ids = [row[0] for row in msg_result.fetchall()]

    # Delete from vector database first
    deleted_memories = 0
    if memory_service.is_configured() and message_ids:
        for msg_id in message_ids:
            success = await memory_service.delete_memory(msg_id, entity_id)
            if success:
                deleted_memories += 1

    # Delete messages from SQL (cascade would handle this, but let's be explicit)
    await db.execute(
        delete(Message).where(Message.conversation_id == conversation_id)
    )

    # Delete the conversation
    await db.delete(conversation)
    await db.commit()

    logger.info(
        f"Deleted conversation {conversation_id}: "
        f"{len(message_ids)} messages, {deleted_memories} memories removed from vector store"
    )

    return {
        "status": "deleted",
        "id": conversation_id,
        "messages_deleted": len(message_ids),
        "memories_deleted": deleted_memories,
    }


@router.get("/{conversation_id}/export", response_model=ConversationExport)
async def export_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Export a conversation as JSON."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get messages
    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()

    return ConversationExport(
        id=conversation.id,
        created_at=conversation.created_at.isoformat(),
        title=conversation.title,
        tags=conversation.tags,
        conversation_type=conversation.conversation_type.value,
        system_prompt_used=conversation.system_prompt_used,
        llm_model_used=conversation.llm_model_used,
        notes=conversation.notes,
        entity_id=conversation.entity_id,
        messages=[
            {
                "id": msg.id,
                "role": msg.role.value,
                "content": msg.content,
                "created_at": msg.created_at.isoformat(),
                "times_retrieved": msg.times_retrieved,
            }
            for msg in messages
        ],
    )


@router.post("/import-seed")
async def import_seed_conversation(
    data: SeedConversationImport,
    db: AsyncSession = Depends(get_db)
):
    """
    Import a seed conversation with messages.

    This is used to import the founding conversation or other seed data.
    Messages can include pre-set times_retrieved values for significance seeding.
    Messages with an 'id' field will be deduplicated - if a message with that ID
    already exists for this entity, it will be skipped.
    """
    from app.services import memory_service

    # Validate entity_id if provided
    if data.entity_id:
        entity = settings.get_entity_by_index(data.entity_id)
        if not entity:
            raise HTTPException(
                status_code=400,
                detail=f"Entity '{data.entity_id}' is not configured. Check your PINECONE_INDEXES environment variable."
            )

    # Collect message IDs for deduplication
    all_message_ids = []
    for msg_data in data.messages:
        if msg_data.get("id"):
            all_message_ids.append(msg_data["id"])

    # Query existing message IDs for THIS entity only (for deduplication)
    existing_ids = set()
    if all_message_ids:
        result = await db.execute(
            select(Message.id)
            .join(Conversation)
            .where(
                Message.id.in_(all_message_ids),
                Conversation.entity_id == data.entity_id
            )
        )
        existing_ids = {row[0] for row in result.fetchall()}

    conv_type = ConversationType.REFLECTION if data.conversation_type == "reflection" else ConversationType.NORMAL

    conversation = Conversation(
        title=data.title,
        tags=data.tags,
        conversation_type=conv_type,
        system_prompt_used=data.system_prompt_used,
        llm_model_used=data.llm_model_used,
        notes=data.notes,
        entity_id=data.entity_id,
        is_imported=True,  # Mark as imported so it doesn't show in conversation list
    )

    db.add(conversation)
    await db.flush()  # Get the ID

    # Store conversation ID before loop - we may expunge the conversation object during batch commits
    conversation_id = conversation.id

    logger.info(f"Importing seed conversation: {data.title or 'Untitled'} (id={conversation_id}, entity={data.entity_id})")

    stored_count = 0
    skipped_count = 0
    imported_count = 0
    batch_counter = 0  # Track messages for batch commits

    for idx, msg_data in enumerate(data.messages):
        msg_id = msg_data.get("id")

        # Skip if message already exists for this entity (deduplication)
        if msg_id and msg_id in existing_ids:
            skipped_count += 1
            logger.debug(f"  Message {idx+1}/{len(data.messages)}: Skipped (duplicate id={msg_id})")
            continue

        role = MessageRole.HUMAN if msg_data["role"] == "human" else MessageRole.ASSISTANT
        times_retrieved = msg_data.get("times_retrieved", 0)

        # Parse created_at if provided (ISO format string)
        created_at = None
        original_timestamp = msg_data.get("created_at")
        if original_timestamp:
            try:
                created_at = datetime.fromisoformat(original_timestamp.replace("Z", "+00:00"))
                # Convert to UTC naive datetime for consistency
                if created_at.tzinfo is not None:
                    created_at = created_at.replace(tzinfo=None)
            except (ValueError, TypeError):
                logger.warning(f"  Message {idx+1}: Failed to parse timestamp '{original_timestamp}', using current time")

        # Build message kwargs, only include created_at if we have a valid timestamp
        # Use provided ID if available, otherwise auto-generate
        message_kwargs = {
            "conversation_id": conversation_id,
            "role": role,
            "content": msg_data["content"],
            "times_retrieved": times_retrieved,
        }
        if msg_id:
            message_kwargs["id"] = msg_id
        if created_at is not None:
            message_kwargs["created_at"] = created_at

        message = Message(**message_kwargs)
        db.add(message)
        await db.flush()
        imported_count += 1

        # Extract values we need before potential expunge
        message_id = message.id
        message_created_at = message.created_at
        message_content = message.content

        # Log the message with its timestamp
        timestamp_source = "original" if created_at is not None else "default (now)"
        logger.info(f"  Message {idx+1}/{len(data.messages)}: {role.value} | timestamp={message_created_at.isoformat()} ({timestamp_source})")

        # Store in vector database for the specified entity
        if memory_service.is_configured():
            success = await memory_service.store_memory(
                message_id=message_id,
                conversation_id=conversation_id,
                role=role.value,
                content=message_content,
                created_at=message_created_at,
                entity_id=data.entity_id,
            )
            if success:
                stored_count += 1

        # Batch commit to prevent memory exhaustion
        batch_counter += 1
        if batch_counter >= IMPORT_BATCH_SIZE:
            await db.commit()
            # Expunge all to release memory after commit (use run_sync for async compatibility)
            await db.run_sync(lambda session: session.expunge_all())
            batch_counter = 0
            logger.debug(f"Batch commit: {imported_count} messages imported so far")

    # If no messages were imported (all duplicates), remove the empty conversation
    if imported_count == 0:
        # Re-fetch conversation since it may have been expunged
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv_to_delete = result.scalar_one_or_none()
        if conv_to_delete:
            await db.delete(conv_to_delete)
        await db.commit()
        return {
            "status": "skipped",
            "conversation_id": None,
            "message_count": 0,
            "messages_skipped": skipped_count,
            "memories_stored": 0,
            "entity_id": data.entity_id,
        }

    await db.commit()

    return {
        "status": "imported",
        "conversation_id": conversation_id,
        "message_count": imported_count,
        "messages_skipped": skipped_count,
        "memories_stored": stored_count,
        "entity_id": data.entity_id,
    }


class ExternalConversationImport(BaseModel):
    """Import format for external conversations (OpenAI/Anthropic exports)."""
    content: str  # JSON string content of the export file
    entity_id: str  # Target entity (required)
    source: Optional[str] = None  # Optional source hint: "openai", "anthropic", or auto-detect
    selected_conversations: Optional[List[dict]] = None  # List of {index, import_as_memory, import_to_history}
    allow_reimport: bool = False  # If True, allow selecting already-imported conversations (per-message dedup still applies)


class ExternalConversationPreview(BaseModel):
    """Request to preview an export file."""
    content: str  # JSON string content of the export file
    source: Optional[str] = None  # Optional source hint
    entity_id: str  # Target entity for deduplication check
    allow_reimport: bool = False  # If True, don't mark conversations as already imported


def _parse_openai_export(data: list, include_ids: bool = False) -> List[dict]:
    """
    Parse OpenAI ChatGPT export format.

    OpenAI exports conversations as a list where each conversation has a 'mapping'
    dict containing messages in a tree structure, and 'title' field.
    """
    all_conversations = []

    for idx, conv in enumerate(data):
        if not isinstance(conv, dict):
            continue

        conv_id = conv.get("id", f"openai-{idx}")
        title = conv.get("title", "Imported from ChatGPT")
        mapping = conv.get("mapping", {})

        # Build message chain from the tree structure
        message_nodes = []
        for node_id, node in mapping.items():
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not message:
                continue

            msg_id = message.get("id", node_id)
            author = message.get("author", {})
            role = author.get("role", "")

            # Skip system messages
            if role not in ("user", "assistant"):
                continue

            content = message.get("content", {})
            parts = content.get("parts", [])

            # Combine text parts
            text = ""
            for part in parts:
                if isinstance(part, str):
                    text += part
                elif isinstance(part, dict) and "text" in part:
                    text += part["text"]

            if text.strip():
                create_time = message.get("create_time") or 0
                msg_entry = {
                    "role": "human" if role == "user" else "assistant",
                    "content": text.strip(),
                    "timestamp": create_time,
                }
                if include_ids:
                    msg_entry["id"] = msg_id
                message_nodes.append(msg_entry)

        # Sort by timestamp
        message_nodes.sort(key=lambda x: x.get("timestamp", 0))

        if include_ids:
            messages = [{"id": m.get("id"), "role": m["role"], "content": m["content"], "timestamp": m.get("timestamp")} for m in message_nodes]
        else:
            messages = [{"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp")} for m in message_nodes]

        if messages:
            conv_entry = {
                "title": title,
                "messages": messages,
                "message_count": len(messages),
            }
            if include_ids:
                conv_entry["id"] = conv_id
                conv_entry["index"] = idx
            all_conversations.append(conv_entry)

    return all_conversations


def _parse_anthropic_export(data: list, include_ids: bool = False) -> List[dict]:
    """
    Parse Anthropic Claude export format.

    Anthropic exports as a list of conversations, each with 'chat_messages' array
    containing messages with 'sender' and 'text' fields.
    """
    all_conversations = []

    for idx, conv in enumerate(data):
        if not isinstance(conv, dict):
            continue

        conv_id = conv.get("uuid", f"anthropic-{idx}")
        title = conv.get("name", "Imported from Claude")
        chat_messages = conv.get("chat_messages", [])

        messages = []
        for msg in chat_messages:
            if not isinstance(msg, dict):
                continue

            msg_id = msg.get("uuid")
            sender = msg.get("sender", "")
            text = msg.get("text", "")
            # Anthropic exports use created_at or updated_at in ISO format
            timestamp_str = msg.get("created_at") or msg.get("updated_at")

            if sender in ("human", "user") and text.strip():
                msg_entry = {
                    "role": "human",
                    "content": text.strip(),
                    "timestamp_str": timestamp_str,
                }
                if include_ids and msg_id:
                    msg_entry["id"] = msg_id
                messages.append(msg_entry)
            elif sender == "assistant" and text.strip():
                msg_entry = {
                    "role": "assistant",
                    "content": text.strip(),
                    "timestamp_str": timestamp_str,
                }
                if include_ids and msg_id:
                    msg_entry["id"] = msg_id
                messages.append(msg_entry)

        if messages:
            conv_entry = {
                "title": title,
                "messages": messages,
                "message_count": len(messages),
            }
            if include_ids:
                conv_entry["id"] = conv_id
                conv_entry["index"] = idx
            all_conversations.append(conv_entry)

    return all_conversations


def _detect_and_parse_export(content: str, source_hint: Optional[str] = None, include_ids: bool = False) -> tuple:
    """
    Detect export format and parse conversations.

    Returns: (conversations_list, detected_source)
    """
    import json

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")

    if not isinstance(data, list):
        raise ValueError("Export file must contain a JSON array")

    if len(data) == 0:
        return [], "unknown"

    # Try to detect format from structure
    first_item = data[0]

    if source_hint == "openai" or (source_hint is None and "mapping" in first_item):
        # OpenAI format has 'mapping' field
        return _parse_openai_export(data, include_ids), "openai"
    elif source_hint == "anthropic" or (source_hint is None and "chat_messages" in first_item):
        # Anthropic format has 'chat_messages' field
        return _parse_anthropic_export(data, include_ids), "anthropic"
    else:
        # Try to auto-detect by checking for common patterns
        if any("mapping" in item for item in data if isinstance(item, dict)):
            return _parse_openai_export(data, include_ids), "openai"
        elif any("chat_messages" in item for item in data if isinstance(item, dict)):
            return _parse_anthropic_export(data, include_ids), "anthropic"
        else:
            raise ValueError(
                "Could not detect export format. Supported formats: OpenAI ChatGPT export, Anthropic Claude export"
            )


@router.post("/import-external/preview")
async def preview_external_conversations(
    data: ExternalConversationPreview,
    db: AsyncSession = Depends(get_db)
):
    """
    Preview conversations from an external export file.

    Returns a list of conversations with their titles, message counts, and
    whether they've already been imported (for deduplication).
    """
    # Parse the export file with IDs for deduplication
    try:
        conversations, detected_source = _detect_and_parse_export(data.content, data.source, include_ids=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not conversations:
        raise HTTPException(status_code=400, detail="No conversations found in export file")

    # Check for already imported message IDs
    all_message_ids = []
    for conv in conversations:
        for msg in conv.get("messages", []):
            if msg.get("id"):
                all_message_ids.append(msg["id"])

    # Query existing message IDs for THIS entity only
    # This allows the same file to be imported to different entities
    existing_ids = set()
    if all_message_ids:
        result = await db.execute(
            select(Message.id)
            .join(Conversation)
            .where(
                Message.id.in_(all_message_ids),
                Conversation.entity_id == data.entity_id
            )
        )
        existing_ids = {row[0] for row in result.fetchall()}

    # Build preview response
    preview = []
    for conv in conversations:
        messages = conv.get("messages", [])
        imported_count = sum(1 for m in messages if m.get("id") in existing_ids)
        # When allow_reimport is True, don't mark as already_imported (keeps checkboxes enabled)
        already_imported = (imported_count == len(messages) and len(messages) > 0) and not data.allow_reimport

        preview.append({
            "index": conv.get("index", 0),
            "id": conv.get("id"),
            "title": conv["title"],
            "message_count": conv["message_count"],
            "imported_count": imported_count,
            "already_imported": already_imported,
        })

    return {
        "source_format": detected_source,
        "total_conversations": len(preview),
        "conversations": preview,
        "allow_reimport": data.allow_reimport,
    }


@router.post("/import-external")
async def import_external_conversations(
    data: ExternalConversationImport,
    db: AsyncSession = Depends(get_db)
):
    """
    Import conversations from external services (OpenAI ChatGPT or Anthropic Claude exports).

    If selected_conversations is provided, only import those conversations.
    Each conversation can be imported as:
    - Memory only (is_imported=True, won't show in conversation list)
    - Memory + History (is_imported=False, will show in conversation list)

    Messages with existing IDs are always skipped for deduplication.
    The allow_reimport flag only affects the UI preview (allows selecting conversations
    that appear fully imported), but per-message deduplication is always enforced.
    This is useful for retrying failed imports where some messages succeeded.

    Uses batch processing to prevent memory exhaustion during large imports.
    """
    from app.services import memory_service

    # Validate entity_id
    entity = settings.get_entity_by_index(data.entity_id)
    if not entity:
        raise HTTPException(
            status_code=400,
            detail=f"Entity '{data.entity_id}' is not configured. Check your PINECONE_INDEXES environment variable."
        )

    # Parse the export file with IDs
    try:
        conversations, detected_source = _detect_and_parse_export(data.content, data.source, include_ids=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"Starting external import: source={detected_source}, entity={data.entity_id}, conversations_found={len(conversations)}")

    if not conversations:
        raise HTTPException(status_code=400, detail="No conversations found in export file")

    # Build selection map if provided
    selection_map = {}
    if data.selected_conversations:
        for sel in data.selected_conversations:
            selection_map[sel["index"]] = {
                "import_as_memory": sel.get("import_as_memory", True),
                "import_to_history": sel.get("import_to_history", False),
            }

    # Get existing message IDs for deduplication (per-entity)
    # Only messages already imported for THIS entity are considered duplicates
    all_message_ids = []
    for conv in conversations:
        for msg in conv.get("messages", []):
            if msg.get("id"):
                all_message_ids.append(msg["id"])

    existing_ids = set()  # IDs that exist for THIS entity (skip these)
    global_existing_ids = set()  # IDs that exist for ANY entity (need new IDs)
    if all_message_ids:
        # Check for IDs already imported to THIS entity (for skipping)
        result = await db.execute(
            select(Message.id)
            .join(Conversation)
            .where(
                Message.id.in_(all_message_ids),
                Conversation.entity_id == data.entity_id
            )
        )
        existing_ids = {row[0] for row in result.fetchall()}

        # Check for IDs that exist globally (for ID regeneration)
        # This handles the case where the same file is imported to multiple entities
        result = await db.execute(
            select(Message.id).where(Message.id.in_(all_message_ids))
        )
        global_existing_ids = {row[0] for row in result.fetchall()}

    # Import conversations
    total_messages = 0
    total_memories = 0
    imported_conversations = 0
    skipped_messages = 0
    history_conversations = 0
    batch_counter = 0  # Track messages for batch commits

    for conv in conversations:
        conv_index = conv.get("index", 0)

        # Check if this conversation should be imported
        if data.selected_conversations is not None:
            if conv_index not in selection_map:
                continue  # Not selected, skip
            selection = selection_map[conv_index]
            import_as_memory = selection["import_as_memory"]
            import_to_history = selection["import_to_history"]

            # Skip if neither option is selected
            if not import_as_memory and not import_to_history:
                continue
        else:
            # Legacy behavior: import all as memory only
            import_as_memory = True
            import_to_history = False

        title = conv.get("title", f"Imported from {detected_source}")
        messages = conv.get("messages", [])

        if not messages:
            continue

        # If importing to history, conversation should be visible (is_imported=False)
        # Otherwise, mark as imported (hidden)
        is_imported = not import_to_history

        # Create conversation
        conversation = Conversation(
            title=f"[Imported] {title}" if is_imported else title,
            conversation_type=ConversationType.NORMAL,
            llm_model_used="imported",
            entity_id=data.entity_id,
            is_imported=is_imported,
        )

        db.add(conversation)
        await db.flush()

        # Store conversation ID - we may expunge the conversation object during batch commits
        conv_id = conversation.id

        logger.info(f"Importing conversation: {title} (id={conv_id}, source={detected_source}, entity={data.entity_id})")

        # Track if any messages were added
        messages_added = 0

        # Add messages
        for msg_idx, msg_data in enumerate(messages):
            msg_id = msg_data.get("id")
            role = MessageRole.HUMAN if msg_data["role"] == "human" else MessageRole.ASSISTANT
            content = msg_data["content"]

            # Skip if message already exists for THIS entity (deduplication)
            # This check always runs - allow_reimport only affects conversation-level UI selection,
            # per-message deduplication is always enforced
            if msg_id and msg_id in existing_ids:
                skipped_messages += 1
                logger.debug(f"  Message {msg_idx+1}/{len(messages)}: Skipped (duplicate id={msg_id})")
                continue

            # Determine the message ID to use:
            # - If ID exists globally (another entity), generate new ID
            # - If ID is available and not used, use original
            # - If no ID provided, auto-generate
            if msg_id and msg_id in global_existing_ids:
                # ID exists for another entity, generate new ID for this entity
                use_id = None  # Will auto-generate UUID
            else:
                use_id = msg_id if msg_id else None

            # Extract and parse timestamp from the original message
            # OpenAI uses Unix timestamp (float), Anthropic uses ISO string
            created_at = None
            original_ts = msg_data.get("timestamp") or msg_data.get("timestamp_str")
            if msg_data.get("timestamp"):
                # OpenAI: Unix timestamp (seconds since epoch)
                try:
                    created_at = datetime.utcfromtimestamp(msg_data["timestamp"])
                except (ValueError, TypeError, OSError):
                    logger.warning(f"  Message {msg_idx+1}: Failed to parse Unix timestamp '{msg_data['timestamp']}', using current time")
            elif msg_data.get("timestamp_str"):
                # Anthropic: ISO format string
                try:
                    created_at = datetime.fromisoformat(msg_data["timestamp_str"].replace("Z", "+00:00"))
                    # Convert to UTC naive datetime for consistency
                    if created_at.tzinfo is not None:
                        created_at = created_at.replace(tzinfo=None)
                except (ValueError, TypeError):
                    logger.warning(f"  Message {msg_idx+1}: Failed to parse ISO timestamp '{msg_data['timestamp_str']}', using current time")

            # Build message kwargs, only include created_at if we have a valid timestamp
            message_kwargs = {
                "id": use_id,
                "conversation_id": conv_id,
                "role": role,
                "content": content,
                "times_retrieved": 0,
            }
            if created_at is not None:
                message_kwargs["created_at"] = created_at

            message = Message(**message_kwargs)
            db.add(message)
            await db.flush()
            total_messages += 1
            messages_added += 1

            # Extract values we need before potential expunge
            message_id = message.id
            message_created_at = message.created_at

            # Log the message with its timestamp
            timestamp_source = "original" if created_at is not None else "default (now)"
            content_preview = content[:50] + "..." if len(content) > 50 else content
            logger.info(f"  Message {msg_idx+1}/{len(messages)}: {role.value} | timestamp={message_created_at.isoformat()} ({timestamp_source}) | {content_preview!r}")

            # Store in vector database if importing as memory
            if import_as_memory and memory_service.is_configured():
                success = await memory_service.store_memory(
                    message_id=message_id,
                    conversation_id=conv_id,
                    role=role.value,
                    content=content,
                    created_at=message_created_at,
                    entity_id=data.entity_id,
                )
                if success:
                    total_memories += 1

            # Batch commit to prevent memory exhaustion
            batch_counter += 1
            if batch_counter >= IMPORT_BATCH_SIZE:
                await db.commit()
                # Expunge all to release memory after commit (use run_sync for async compatibility)
                await db.run_sync(lambda session: session.expunge_all())
                batch_counter = 0
                logger.debug(f"Batch commit: {total_messages} messages imported so far")

        # If no messages were added (all duplicates), remove the empty conversation
        if messages_added == 0:
            # Re-fetch conversation since it may have been expunged
            result = await db.execute(
                select(Conversation).where(Conversation.id == conv_id)
            )
            conv_to_delete = result.scalar_one_or_none()
            if conv_to_delete:
                await db.delete(conv_to_delete)
        else:
            imported_conversations += 1
            if import_to_history:
                history_conversations += 1

    await db.commit()

    logger.info(f"Import complete: {imported_conversations} conversations, {total_messages} messages imported, {skipped_messages} skipped, {total_memories} memories stored")

    return {
        "status": "imported",
        "source_format": detected_source,
        "conversations_imported": imported_conversations,
        "conversations_to_history": history_conversations,
        "messages_imported": total_messages,
        "messages_skipped": skipped_messages,
        "memories_stored": total_memories,
        "entity_id": data.entity_id,
    }


@router.post("/import-external/stream")
async def import_external_conversations_stream(data: ExternalConversationImport):
    """
    Import conversations from external services with streaming progress updates.

    Returns SSE stream with events:
    - event: start - Import started with total counts
    - event: progress - Progress update for each message
    - event: done - Import completed with final stats
    - event: error - Error occurred

    The import can be cancelled by closing the connection.
    """

    async def generate_stream():
        """Generate SSE stream from import processing."""
        from app.services import memory_service

        async with async_session_maker() as db:
            try:
                # Validate entity_id
                entity = settings.get_entity_by_index(data.entity_id)
                if not entity:
                    error_msg = f"Entity '{data.entity_id}' is not configured."
                    yield f"event: error\ndata: {json.dumps({'error': error_msg})}\n\n"
                    return

                # Parse the export file with IDs
                try:
                    conversations, detected_source = _detect_and_parse_export(data.content, data.source, include_ids=True)
                except ValueError as e:
                    yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                    return

                if not conversations:
                    yield f"event: error\ndata: {json.dumps({'error': 'No conversations found in export file'})}\n\n"
                    return

                # Build selection map
                selection_map = {}
                if data.selected_conversations:
                    for sel in data.selected_conversations:
                        selection_map[sel["index"]] = {
                            "import_as_memory": sel.get("import_as_memory", True),
                            "import_to_history": sel.get("import_to_history", False),
                        }

                # Count total messages to import for progress calculation
                total_messages_to_process = 0
                conversations_to_import = []
                for conv in conversations:
                    conv_index = conv.get("index", 0)
                    if data.selected_conversations is not None:
                        if conv_index not in selection_map:
                            continue
                        selection = selection_map[conv_index]
                        if not selection["import_as_memory"] and not selection["import_to_history"]:
                            continue
                    conversations_to_import.append(conv)
                    total_messages_to_process += len(conv.get("messages", []))

                # Get existing message IDs for deduplication
                all_message_ids = []
                for conv in conversations_to_import:
                    for msg in conv.get("messages", []):
                        if msg.get("id"):
                            all_message_ids.append(msg["id"])

                existing_ids = set()
                global_existing_ids = set()
                if all_message_ids:
                    result = await db.execute(
                        select(Message.id)
                        .join(Conversation)
                        .where(
                            Message.id.in_(all_message_ids),
                            Conversation.entity_id == data.entity_id
                        )
                    )
                    existing_ids = {row[0] for row in result.fetchall()}

                    result = await db.execute(
                        select(Message.id).where(Message.id.in_(all_message_ids))
                    )
                    global_existing_ids = {row[0] for row in result.fetchall()}

                # Send start event
                yield f"event: start\ndata: {json.dumps({'total_conversations': len(conversations_to_import), 'total_messages': total_messages_to_process, 'source_format': detected_source})}\n\n"

                # Allow the start event to be sent before processing
                await asyncio.sleep(0)

                # Import state
                total_messages = 0
                total_memories = 0
                imported_conversations = 0
                skipped_messages = 0
                history_conversations = 0
                batch_counter = 0
                messages_processed = 0

                for conv_idx, conv in enumerate(conversations_to_import):
                    conv_index = conv.get("index", 0)

                    # Get selection options
                    if data.selected_conversations is not None:
                        selection = selection_map[conv_index]
                        import_as_memory = selection["import_as_memory"]
                        import_to_history = selection["import_to_history"]
                    else:
                        import_as_memory = True
                        import_to_history = False

                    title = conv.get("title", f"Imported from {detected_source}")
                    messages = conv.get("messages", [])

                    if not messages:
                        continue

                    is_imported = not import_to_history

                    # Create conversation
                    conversation = Conversation(
                        title=f"[Imported] {title}" if is_imported else title,
                        conversation_type=ConversationType.NORMAL,
                        llm_model_used="imported",
                        entity_id=data.entity_id,
                        is_imported=is_imported,
                    )
                    db.add(conversation)
                    await db.flush()
                    conv_id = conversation.id

                    messages_added = 0

                    for msg_idx, msg_data in enumerate(messages):
                        msg_id = msg_data.get("id")
                        role = MessageRole.HUMAN if msg_data["role"] == "human" else MessageRole.ASSISTANT
                        content = msg_data["content"]

                        # Skip duplicates
                        if msg_id and msg_id in existing_ids:
                            skipped_messages += 1
                            messages_processed += 1
                            continue

                        # Determine message ID
                        if msg_id and msg_id in global_existing_ids:
                            use_id = None
                        else:
                            use_id = msg_id if msg_id else None

                        # Parse timestamp
                        created_at = None
                        if msg_data.get("timestamp"):
                            try:
                                created_at = datetime.utcfromtimestamp(msg_data["timestamp"])
                            except (ValueError, TypeError, OSError):
                                pass
                        elif msg_data.get("timestamp_str"):
                            try:
                                created_at = datetime.fromisoformat(msg_data["timestamp_str"].replace("Z", "+00:00"))
                                if created_at.tzinfo is not None:
                                    created_at = created_at.replace(tzinfo=None)
                            except (ValueError, TypeError):
                                pass

                        message_kwargs = {
                            "id": use_id,
                            "conversation_id": conv_id,
                            "role": role,
                            "content": content,
                            "times_retrieved": 0,
                        }
                        if created_at is not None:
                            message_kwargs["created_at"] = created_at

                        message = Message(**message_kwargs)
                        db.add(message)
                        await db.flush()
                        total_messages += 1
                        messages_added += 1
                        messages_processed += 1

                        message_id = message.id
                        message_created_at = message.created_at

                        # Store in vector database
                        if import_as_memory and memory_service.is_configured():
                            success = await memory_service.store_memory(
                                message_id=message_id,
                                conversation_id=conv_id,
                                role=role.value,
                                content=content,
                                created_at=message_created_at,
                                entity_id=data.entity_id,
                            )
                            if success:
                                total_memories += 1

                        # Batch commit
                        batch_counter += 1
                        if batch_counter >= IMPORT_BATCH_SIZE:
                            await db.commit()
                            await db.run_sync(lambda session: session.expunge_all())
                            batch_counter = 0

                        # Send progress every few messages
                        if messages_processed % 5 == 0 or messages_processed == total_messages_to_process:
                            progress_pct = round((messages_processed / total_messages_to_process) * 100) if total_messages_to_process > 0 else 100
                            yield f"event: progress\ndata: {json.dumps({'messages_processed': messages_processed, 'total_messages': total_messages_to_process, 'progress_percent': progress_pct, 'current_conversation': title[:50]})}\n\n"
                            await asyncio.sleep(0)

                    # Handle empty conversation
                    if messages_added == 0:
                        result = await db.execute(
                            select(Conversation).where(Conversation.id == conv_id)
                        )
                        conv_to_delete = result.scalar_one_or_none()
                        if conv_to_delete:
                            await db.delete(conv_to_delete)
                    else:
                        imported_conversations += 1
                        if import_to_history:
                            history_conversations += 1

                await db.commit()

                # Send done event
                yield f"event: done\ndata: {json.dumps({'status': 'imported', 'source_format': detected_source, 'conversations_imported': imported_conversations, 'conversations_to_history': history_conversations, 'messages_imported': total_messages, 'messages_skipped': skipped_messages, 'memories_stored': total_memories, 'entity_id': data.entity_id})}\n\n"

            except asyncio.CancelledError:
                # Import was cancelled
                logger.info("Import cancelled by client")
                await db.rollback()
                yield f"event: cancelled\ndata: {json.dumps({'status': 'cancelled', 'messages_imported': total_messages})}\n\n"
            except Exception as e:
                logger.exception("Error during streaming import")
                await db.rollback()
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
