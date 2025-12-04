from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pydantic import BaseModel

from app.database import get_db
from app.models import Conversation, Message, ConversationType, MessageRole
from app.config import settings

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    conversation_type: str = "normal"
    system_prompt: Optional[str] = None
    model: str = "claude-sonnet-4-5-20250929"
    entity_id: Optional[str] = None  # Pinecone index name for the AI entity


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None


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

    class Config:
        from_attributes = True


def check_entity_exists(entity_id: Optional[str]) -> bool:
    """Check if an entity_id references a configured entity."""
    if entity_id is None:
        return True  # NULL means default entity, always valid
    return settings.get_entity_by_index(entity_id) is not None


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    token_count: Optional[int]
    times_retrieved: int
    last_retrieved_at: Optional[datetime]

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
    messages: List[dict]  # List of {role: str, content: str, times_retrieved?: int}


@router.post("/", response_model=ConversationResponse)
async def create_conversation(
    data: ConversationCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new conversation."""
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

    query = query.order_by(
        Conversation.updated_at.desc().nullsfirst(),
        Conversation.created_at.desc()
    ).limit(limit).offset(offset)

    result = await db.execute(query)
    conversations = result.scalars().all()

    response = []
    for conv in conversations:
        # Get message count
        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
        )
        messages = msg_result.scalars().all()
        message_count = len(messages)

        # Get preview from first human message
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
        ))

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
        message_count=len(messages),
        preview=preview,
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

    conv_type = ConversationType.REFLECTION if data.conversation_type == "reflection" else ConversationType.NORMAL

    conversation = Conversation(
        title=data.title,
        tags=data.tags,
        conversation_type=conv_type,
        system_prompt_used=data.system_prompt_used,
        llm_model_used=data.llm_model_used,
        notes=data.notes,
        entity_id=data.entity_id,
    )

    db.add(conversation)
    await db.flush()  # Get the ID

    stored_count = 0
    for msg_data in data.messages:
        role = MessageRole.HUMAN if msg_data["role"] == "human" else MessageRole.ASSISTANT
        times_retrieved = msg_data.get("times_retrieved", 0)

        message = Message(
            conversation_id=conversation.id,
            role=role,
            content=msg_data["content"],
            times_retrieved=times_retrieved,
        )
        db.add(message)
        await db.flush()

        # Store in vector database for the specified entity
        if memory_service.is_configured():
            success = await memory_service.store_memory(
                message_id=message.id,
                conversation_id=conversation.id,
                role=role.value,
                content=message.content,
                created_at=message.created_at,
                entity_id=data.entity_id,
            )
            if success:
                stored_count += 1

    await db.commit()

    return {
        "status": "imported",
        "conversation_id": conversation.id,
        "message_count": len(data.messages),
        "memories_stored": stored_count,
        "entity_id": conversation.entity_id,
    }
