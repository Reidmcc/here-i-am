"""
Routes for individual message operations (edit, delete).
"""
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel

from app.database import get_db
from app.models import Message, MessageRole, Conversation, ConversationType, ConversationEntity
from app.services import memory_service, session_manager, llm_service
from app.config import settings

router = APIRouter(prefix="/api/messages", tags=["messages"])


class MessageUpdate(BaseModel):
    """Request body for updating a message."""
    content: str


class MessageResponse(BaseModel):
    """Response for a message."""
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: str
    token_count: Optional[int] = None
    times_retrieved: int = 0


async def get_entity_ids_for_conversation(
    conversation: Conversation,
    db: AsyncSession
) -> List[str]:
    """
    Get the entity IDs to use for memory operations.

    For single-entity conversations: returns [conversation.entity_id]
    For multi-entity conversations: returns all participating entity IDs
    """
    if conversation.conversation_type == ConversationType.MULTI_ENTITY:
        result = await db.execute(
            select(ConversationEntity.entity_id)
            .where(ConversationEntity.conversation_id == str(conversation.id))
            .order_by(ConversationEntity.display_order)
        )
        entity_ids = [row[0] for row in result.fetchall()]
        return entity_ids if entity_ids else []
    else:
        return [conversation.entity_id] if conversation.entity_id else []


def get_entity_label(entity_id: str) -> Optional[str]:
    """Get the label for an entity from settings."""
    entity = settings.get_entity_by_index(entity_id)
    return entity.label if entity else None


@router.put("/{message_id}")
async def update_message(
    message_id: str,
    data: MessageUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update a human message's content.

    This will:
    1. Update the message content in the database
    2. Update the message embedding in Pinecone
    3. Delete any subsequent assistant message (to be regenerated)
    4. Invalidate the session cache so context is rebuilt

    Only human messages can be edited.
    """
    # Get the message
    result = await db.execute(
        select(Message).where(Message.id == message_id)
    )
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    if message.role != MessageRole.HUMAN:
        raise HTTPException(
            status_code=400,
            detail="Only human messages can be edited"
        )

    # Get the conversation to find the entity_id
    result = await db.execute(
        select(Conversation).where(Conversation.id == message.conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Find the subsequent assistant message (if any)
    result = await db.execute(
        select(Message)
        .where(
            and_(
                Message.conversation_id == message.conversation_id,
                Message.created_at > message.created_at,
                Message.role == MessageRole.ASSISTANT
            )
        )
        .order_by(Message.created_at)
        .limit(1)
    )
    subsequent_assistant_msg = result.scalar_one_or_none()

    # Update the message content
    old_content = message.content
    message.content = data.content
    message.token_count = llm_service.count_tokens(data.content)

    # Delete the subsequent assistant message if it exists
    deleted_assistant_id = None
    if subsequent_assistant_msg:
        deleted_assistant_id = subsequent_assistant_msg.id
        await db.delete(subsequent_assistant_msg)

    # Update conversation timestamp
    conversation.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(message)

    # Update Pinecone embedding for the edited message
    if memory_service.is_configured():
        # Get entity IDs for memory operations (handles multi-entity)
        entity_ids = await get_entity_ids_for_conversation(conversation, db)

        for entity_id in entity_ids:
            # Delete old embedding and store new one
            await memory_service.delete_memory(message_id, entity_id=entity_id)
            await memory_service.store_memory(
                message_id=message.id,
                conversation_id=message.conversation_id,
                role="human",
                content=data.content,
                created_at=message.created_at,
                entity_id=entity_id,
            )

            # Also delete the subsequent assistant message from Pinecone
            if deleted_assistant_id:
                await memory_service.delete_memory(
                    deleted_assistant_id,
                    entity_id=entity_id
                )

    # Invalidate the session cache so it gets rebuilt
    session_manager.close_session(message.conversation_id)

    return {
        "message": MessageResponse(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role.value,
            content=message.content,
            created_at=message.created_at.isoformat(),
            token_count=message.token_count,
            times_retrieved=message.times_retrieved,
        ),
        "deleted_assistant_message_id": deleted_assistant_id,
    }


@router.delete("/{message_id}")
async def delete_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a message and optionally its paired response.

    If deleting a human message, also deletes the subsequent assistant message.
    If deleting an assistant message, only deletes that message.
    """
    # Get the message
    result = await db.execute(
        select(Message).where(Message.id == message_id)
    )
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Get the conversation to find the entity_id
    result = await db.execute(
        select(Conversation).where(Conversation.id == message.conversation_id)
    )
    conversation = result.scalar_one_or_none()

    deleted_ids = [message_id]

    # If deleting a human message, also delete the subsequent assistant message
    if message.role == MessageRole.HUMAN:
        result = await db.execute(
            select(Message)
            .where(
                and_(
                    Message.conversation_id == message.conversation_id,
                    Message.created_at > message.created_at,
                    Message.role == MessageRole.ASSISTANT
                )
            )
            .order_by(Message.created_at)
            .limit(1)
        )
        subsequent_msg = result.scalar_one_or_none()
        if subsequent_msg:
            deleted_ids.append(subsequent_msg.id)
            await db.delete(subsequent_msg)

    await db.delete(message)

    # Update conversation timestamp
    if conversation:
        conversation.updated_at = datetime.utcnow()

    await db.commit()

    # Delete from Pinecone
    if memory_service.is_configured() and conversation:
        # Get entity IDs for memory operations (handles multi-entity)
        entity_ids = await get_entity_ids_for_conversation(conversation, db)

        for entity_id in entity_ids:
            for del_id in deleted_ids:
                await memory_service.delete_memory(del_id, entity_id=entity_id)

    # Invalidate the session cache
    session_manager.close_session(message.conversation_id)

    return {
        "deleted_message_ids": deleted_ids,
    }
