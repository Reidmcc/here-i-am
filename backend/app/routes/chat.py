from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models import Conversation, Message, MessageRole
from app.services.session_manager import session_manager
from app.services.memory_service import memory_service
from app.services.llm_service import llm_service
from app.config import settings

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    # Optional overrides
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None  # None means use conversation default


class MemoryInfo(BaseModel):
    id: str
    content_preview: str
    created_at: str
    times_retrieved: int
    score: float


class ChatResponse(BaseModel):
    content: str
    model: str
    usage: dict
    stop_reason: str
    new_memories_retrieved: List[MemoryInfo]
    total_memories_in_context: int
    message_id: str


class QuickChatRequest(BaseModel):
    """For quick chats without persistent conversation."""
    message: str
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None


@router.post("/send", response_model=ChatResponse)
async def send_message(
    data: ChatRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Send a message in a conversation.

    This follows the full pipeline:
    1. Retrieve relevant memories
    2. Deduplicate against session memories
    3. Update retrieval counts
    4. Build context with memories
    5. Call Claude API
    6. Store messages as new memories
    """
    # Get or create session
    session = session_manager.get_session(data.conversation_id)

    if not session:
        # Try to load from database
        session = await session_manager.load_session_from_db(data.conversation_id, db)

        if not session:
            # Conversation doesn't exist
            raise HTTPException(status_code=404, detail="Conversation not found")

    # Apply any overrides
    if data.model:
        session.model = data.model
    if data.temperature is not None:
        session.temperature = data.temperature
    if data.max_tokens:
        session.max_tokens = data.max_tokens
    if data.system_prompt is not None:
        session.system_prompt = data.system_prompt

    # Process the message through the full pipeline
    response = await session_manager.process_message(
        session=session,
        user_message=data.message,
        db=db,
    )

    # Store new messages in database
    # Human message
    human_msg = Message(
        conversation_id=data.conversation_id,
        role=MessageRole.HUMAN,
        content=data.message,
        token_count=llm_service.count_tokens(data.message),
    )
    db.add(human_msg)

    # Assistant message
    assistant_msg = Message(
        conversation_id=data.conversation_id,
        role=MessageRole.ASSISTANT,
        content=response["content"],
        token_count=llm_service.count_tokens(response["content"]),
    )
    db.add(assistant_msg)

    # Update conversation timestamp
    result = await db.execute(
        select(Conversation).where(Conversation.id == data.conversation_id)
    )
    conversation = result.scalar_one()
    conversation.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(human_msg)
    await db.refresh(assistant_msg)

    # Store messages as memories in vector database for the session's entity
    if memory_service.is_configured():
        await memory_service.store_memory(
            message_id=human_msg.id,
            conversation_id=data.conversation_id,
            role="human",
            content=data.message,
            created_at=human_msg.created_at,
            entity_id=session.entity_id,
        )
        await memory_service.store_memory(
            message_id=assistant_msg.id,
            conversation_id=data.conversation_id,
            role="assistant",
            content=response["content"],
            created_at=assistant_msg.created_at,
            entity_id=session.entity_id,
        )

    return ChatResponse(
        content=response["content"],
        model=response["model"],
        usage=response["usage"],
        stop_reason=response["stop_reason"],
        new_memories_retrieved=[
            MemoryInfo(**m) for m in response["new_memories_retrieved"]
        ],
        total_memories_in_context=response["total_memories_in_context"],
        message_id=assistant_msg.id,
    )


@router.post("/quick")
async def quick_chat(data: QuickChatRequest):
    """
    Quick chat without conversation persistence.

    Useful for one-off queries. Does not store messages or retrieve memories.
    """
    messages = [{"role": "user", "content": data.message}]

    # Use default model if not specified
    model = data.model or settings.default_model

    response = await llm_service.send_message(
        messages=messages,
        model=model,
        system_prompt=data.system_prompt,
        temperature=data.temperature,
        max_tokens=data.max_tokens,
    )

    return {
        "content": response["content"],
        "model": response["model"],
        "usage": response["usage"],
        "stop_reason": response["stop_reason"],
    }


@router.get("/session/{conversation_id}")
async def get_session_info(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get current session information including retrieved memories.
    """
    session = session_manager.get_session(conversation_id)

    if not session:
        session = await session_manager.load_session_from_db(conversation_id, db)

    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "conversation_id": session.conversation_id,
        "model": session.model,
        "temperature": session.temperature,
        "max_tokens": session.max_tokens,
        "system_prompt": session.system_prompt,
        "entity_id": session.entity_id,
        "message_count": len(session.conversation_context),
        "memories_in_context": len(session.session_memories),
        "memories": [
            {
                "id": m.id,
                "content_preview": m.content[:200] if len(m.content) > 200 else m.content,
                "created_at": m.created_at,
                "times_retrieved": m.times_retrieved,
                "role": m.role,
            }
            for m in session.session_memories.values()
        ],
    }


@router.delete("/session/{conversation_id}")
async def close_session(conversation_id: str):
    """
    Close an active session.

    This removes the session from memory but does not delete the conversation.
    """
    session_manager.close_session(conversation_id)
    return {"status": "closed", "conversation_id": conversation_id}


@router.get("/config")
async def get_chat_config():
    """Get default chat configuration including available entities and providers."""
    entities = settings.get_entities()
    default_entity = settings.get_default_entity()

    return {
        "default_model": settings.default_model,
        "default_openai_model": settings.default_openai_model,
        "default_temperature": settings.default_temperature,
        "default_max_tokens": settings.default_max_tokens,
        "providers": llm_service.get_available_providers(),
        "available_models": llm_service.get_all_available_models(),
        "memory_enabled": memory_service.is_configured(),
        "retrieval_top_k": settings.retrieval_top_k,
        "similarity_threshold": settings.similarity_threshold,
        "entities": [entity.to_dict() for entity in entities],
        "default_entity": default_entity.index_name,
    }
