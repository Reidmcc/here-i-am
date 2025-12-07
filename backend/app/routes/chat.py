from typing import Optional, List
from datetime import datetime
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db, async_session_maker
from app.models import Conversation, Message, MessageRole
from app.services import session_manager, memory_service, llm_service
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
    verbosity: Optional[str] = None  # Verbosity level for gpt-5.1 models (low, medium, high)


class MemoryInfo(BaseModel):
    id: str
    content: Optional[str] = None
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
    verbosity: Optional[str] = None  # Verbosity level for gpt-5.1 models (low, medium, high)


class RegenerateRequest(BaseModel):
    """Request to regenerate an AI response."""
    message_id: str  # ID of the assistant message to regenerate OR the human message to regenerate from
    # Optional overrides
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None
    verbosity: Optional[str] = None


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
    if data.verbosity is not None:
        session.verbosity = data.verbosity

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


@router.post("/stream")
async def stream_message(data: ChatRequest):
    """
    Send a message with streaming response via Server-Sent Events.

    Returns SSE stream with events:
    - event: memories - Memory retrieval info
    - event: start - Stream starting with model info
    - event: token - Individual token
    - event: done - Complete response with usage stats
    - event: stored - Message IDs after storage
    - event: error - Error occurred

    Note: Database session is managed inside the generator to avoid
    connection lifecycle issues with streaming responses.
    """
    async def generate_stream():
        """Generate SSE stream from session processing."""
        # Manage database session lifecycle inside the generator
        # This avoids issues with FastAPI closing the session before the stream completes
        async with async_session_maker() as db:
            try:
                # Get or create session
                session = session_manager.get_session(data.conversation_id)

                if not session:
                    session = await session_manager.load_session_from_db(data.conversation_id, db)
                    if not session:
                        yield f"event: error\ndata: {json.dumps({'error': 'Conversation not found'})}\n\n"
                        return

                # Apply any overrides
                if data.model:
                    session.model = data.model
                if data.temperature is not None:
                    session.temperature = data.temperature
                if data.max_tokens:
                    session.max_tokens = data.max_tokens
                if data.system_prompt is not None:
                    session.system_prompt = data.system_prompt
                if data.verbosity is not None:
                    session.verbosity = data.verbosity

                full_content = ""
                model_used = session.model
                usage_data = {}
                stop_reason = None

                async for event in session_manager.process_message_stream(
                    session=session,
                    user_message=data.message,
                    db=db,
                ):
                    event_type = event.get("type")

                    if event_type == "memories":
                        yield f"event: memories\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "start":
                        model_used = event.get("model", model_used)
                        yield f"event: start\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "token":
                        full_content += event.get("content", "")
                        yield f"event: token\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "done":
                        full_content = event.get("content", full_content)
                        model_used = event.get("model", model_used)
                        usage_data = event.get("usage", {})
                        stop_reason = event.get("stop_reason")
                        yield f"event: done\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "error":
                        yield f"event: error\ndata: {json.dumps(event)}\n\n"
                        return

                # Store messages in database after streaming completes
                human_msg = Message(
                    conversation_id=data.conversation_id,
                    role=MessageRole.HUMAN,
                    content=data.message,
                    token_count=llm_service.count_tokens(data.message),
                )
                db.add(human_msg)

                assistant_msg = Message(
                    conversation_id=data.conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=full_content,
                    token_count=llm_service.count_tokens(full_content),
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

                # Store messages as memories in vector database
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
                        content=full_content,
                        created_at=assistant_msg.created_at,
                        entity_id=session.entity_id,
                    )

                # Send stored event with message IDs
                yield f"event: stored\ndata: {json.dumps({'human_message_id': str(human_msg.id), 'assistant_message_id': str(assistant_msg.id)})}\n\n"

            except Exception as e:
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


@router.post("/regenerate")
async def regenerate_response(data: RegenerateRequest):
    """
    Regenerate an AI response via Server-Sent Events.

    Takes a message_id which can be:
    - An assistant message ID: Regenerates that specific response
    - A human message ID: Generates a new response for that message

    The old assistant message is deleted and replaced with the new one.

    Returns SSE stream with same events as /stream endpoint.
    """
    from sqlalchemy import and_

    async def generate_stream():
        """Generate SSE stream for regeneration."""
        async with async_session_maker() as db:
            try:
                # Get the specified message
                result = await db.execute(
                    select(Message).where(Message.id == data.message_id)
                )
                target_message = result.scalar_one_or_none()

                if not target_message:
                    yield f"event: error\ndata: {json.dumps({'error': 'Message not found'})}\n\n"
                    return

                conversation_id = target_message.conversation_id

                # Get the conversation
                result = await db.execute(
                    select(Conversation).where(Conversation.id == conversation_id)
                )
                conversation = result.scalar_one_or_none()

                if not conversation:
                    yield f"event: error\ndata: {json.dumps({'error': 'Conversation not found'})}\n\n"
                    return

                # Determine the human message and assistant message to regenerate
                if target_message.role == MessageRole.ASSISTANT:
                    # Find the human message before this assistant message
                    result = await db.execute(
                        select(Message)
                        .where(
                            and_(
                                Message.conversation_id == conversation_id,
                                Message.created_at < target_message.created_at,
                                Message.role == MessageRole.HUMAN
                            )
                        )
                        .order_by(Message.created_at.desc())
                        .limit(1)
                    )
                    human_message = result.scalar_one_or_none()
                    assistant_to_delete = target_message
                else:
                    # Target is human message, find the subsequent assistant message
                    human_message = target_message
                    result = await db.execute(
                        select(Message)
                        .where(
                            and_(
                                Message.conversation_id == conversation_id,
                                Message.created_at > target_message.created_at,
                                Message.role == MessageRole.ASSISTANT
                            )
                        )
                        .order_by(Message.created_at)
                        .limit(1)
                    )
                    assistant_to_delete = result.scalar_one_or_none()

                if not human_message:
                    yield f"event: error\ndata: {json.dumps({'error': 'Cannot find human message to regenerate from'})}\n\n"
                    return

                user_message_content = human_message.content
                user_message_id = human_message.id

                # Close existing session to force reload with truncated context
                session_manager.close_session(conversation_id)

                # Load a fresh session from the database
                session = await session_manager.load_session_from_db(conversation_id, db)

                if not session:
                    yield f"event: error\ndata: {json.dumps({'error': 'Failed to load session'})}\n\n"
                    return

                # Truncate session context to exclude the message being regenerated and everything after
                # Find the index of the human message in the context
                truncate_index = None
                for i, msg in enumerate(session.conversation_context):
                    # The context uses "user" role, but we stored "human" in DB
                    if msg.get("role") == "user" and msg.get("content") == user_message_content:
                        truncate_index = i
                        break

                if truncate_index is not None:
                    # Remove this message and everything after it
                    session.conversation_context = session.conversation_context[:truncate_index]

                # Apply any overrides
                if data.model:
                    session.model = data.model
                if data.temperature is not None:
                    session.temperature = data.temperature
                if data.max_tokens:
                    session.max_tokens = data.max_tokens
                if data.system_prompt is not None:
                    session.system_prompt = data.system_prompt
                if data.verbosity is not None:
                    session.verbosity = data.verbosity

                # Delete the old assistant message from DB and Pinecone
                if assistant_to_delete:
                    old_assistant_id = assistant_to_delete.id
                    await db.delete(assistant_to_delete)
                    await db.commit()

                    if memory_service.is_configured():
                        await memory_service.delete_memory(
                            old_assistant_id,
                            entity_id=session.entity_id
                        )

                full_content = ""
                model_used = session.model
                usage_data = {}
                stop_reason = None

                # Stream the new response
                async for event in session_manager.process_message_stream(
                    session=session,
                    user_message=user_message_content,
                    db=db,
                ):
                    event_type = event.get("type")

                    if event_type == "memories":
                        yield f"event: memories\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "start":
                        model_used = event.get("model", model_used)
                        yield f"event: start\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "token":
                        full_content += event.get("content", "")
                        yield f"event: token\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "done":
                        full_content = event.get("content", full_content)
                        model_used = event.get("model", model_used)
                        usage_data = event.get("usage", {})
                        stop_reason = event.get("stop_reason")
                        yield f"event: done\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "error":
                        yield f"event: error\ndata: {json.dumps(event)}\n\n"
                        return

                # Store only the new assistant message (human message already exists)
                assistant_msg = Message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=full_content,
                    token_count=llm_service.count_tokens(full_content),
                )
                db.add(assistant_msg)

                # Update conversation timestamp
                conversation.updated_at = datetime.utcnow()

                await db.commit()
                await db.refresh(assistant_msg)

                # Store new assistant message as memory
                if memory_service.is_configured():
                    await memory_service.store_memory(
                        message_id=assistant_msg.id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=full_content,
                        created_at=assistant_msg.created_at,
                        entity_id=session.entity_id,
                    )

                # Send stored event with message IDs
                yield f"event: stored\ndata: {json.dumps({'human_message_id': str(user_message_id), 'assistant_message_id': str(assistant_msg.id)})}\n\n"

            except Exception as e:
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
        verbosity=data.verbosity,
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

    # Get memories currently in context (not all retrieved, just those in context)
    in_context_memories = [
        session.session_memories[mid]
        for mid in session.in_context_ids
        if mid in session.session_memories
    ]

    return {
        "conversation_id": session.conversation_id,
        "model": session.model,
        "temperature": session.temperature,
        "max_tokens": session.max_tokens,
        "system_prompt": session.system_prompt,
        "entity_id": session.entity_id,
        "message_count": len(session.conversation_context),
        "memories_in_context": len(in_context_memories),
        "memories": [
            {
                "id": m.id,
                "content": m.content[:3000] if len(m.content) > 3000 else m.content,
                "content_preview": m.content[:200] if len(m.content) > 200 else m.content,
                "created_at": m.created_at,
                "times_retrieved": m.times_retrieved,
                "role": m.role,
                "score": m.score,
            }
            for m in in_context_memories
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
