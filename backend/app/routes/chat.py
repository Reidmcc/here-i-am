from typing import Optional, List, Literal
from datetime import datetime
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field, field_validator

from app.database import get_db, async_session_maker
from app.models import Conversation, Message, MessageRole, ConversationType, ConversationEntity
from app.services import session_manager, memory_service, llm_service, tool_service, attachment_service
from app.services.llm_service import ModelProvider
from app.config import settings

router = APIRouter(prefix="/api/chat", tags=["chat"])


async def get_multi_entity_ids(conversation_id: str, db: AsyncSession) -> List[str]:
    """Get the list of entity IDs participating in a multi-entity conversation."""
    result = await db.execute(
        select(ConversationEntity.entity_id)
        .where(ConversationEntity.conversation_id == conversation_id)
        .order_by(ConversationEntity.display_order)
    )
    return [row[0] for row in result.fetchall()]


def get_entity_label(entity_id: str) -> Optional[str]:
    """Get the human-readable label for an entity."""
    entity = settings.get_entity_by_index(entity_id)
    return entity.label if entity else None


class ImageAttachment(BaseModel):
    """
    An image attachment for multimodal messages.

    Images are ephemeral - they are analyzed by the AI in the current turn,
    but the raw image data is NOT stored in conversation history or memories.
    The AI's textual description becomes the persisted context.
    """
    # Base64-encoded image data (without data URI prefix)
    data: str
    # MIME type (e.g., "image/jpeg", "image/png", "image/gif", "image/webp")
    media_type: str
    # Optional filename for reference
    filename: Optional[str] = None

    @field_validator('media_type')
    @classmethod
    def validate_media_type(cls, v: str) -> str:
        allowed = settings.get_allowed_image_types()
        if v not in allowed:
            raise ValueError(f"Unsupported image type '{v}'. Allowed types: {', '.join(allowed)}")
        return v


class FileAttachment(BaseModel):
    """
    A text file attachment for context injection.

    Text from files is extracted and injected into the message context.
    Supported formats: plain text files, PDF (if enabled), DOCX (if enabled).
    """
    # Filename with extension (used to determine file type)
    filename: str
    # For text files: the text content directly
    # For PDF/DOCX: base64-encoded file data for server-side extraction
    content: str
    # Content type: "text" for already-extracted text, "base64" for binary files
    content_type: Literal["text", "base64"] = "text"
    # MIME type of the original file (optional, for validation)
    media_type: Optional[str] = None


class Attachments(BaseModel):
    """Container for message attachments."""
    images: List[ImageAttachment] = Field(default_factory=list)
    files: List[FileAttachment] = Field(default_factory=list)


class ChatRequest(BaseModel):
    conversation_id: str
    message: Optional[str] = None  # Optional for multi-entity continuation
    # Optional overrides
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None  # None means use conversation default
    verbosity: Optional[str] = None  # Verbosity level for gpt-5.1 models (low, medium, high)
    # For multi-entity conversations: which entity should respond
    responding_entity_id: Optional[str] = None
    # Custom display name for the user/researcher (used in role labels)
    user_display_name: Optional[str] = None
    # Attachments (images and files) - ephemeral, not stored in memory
    attachments: Optional[Attachments] = None


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
    human_message_id: Optional[str] = None
    # For multi-entity conversations
    speaker_entity_id: Optional[str] = None
    speaker_label: Optional[str] = None


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
    # For multi-entity conversations: which entity should respond (allows changing from original)
    responding_entity_id: Optional[str] = None
    # Custom display name for the user/researcher (used in role labels)
    user_display_name: Optional[str] = None


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
    5. Call LLM API
    6. Store messages as new memories

    For multi-entity conversations:
    - responding_entity_id must be provided to specify which entity responds
    - Memories are retrieved only from the responding entity's index
    - Messages are stored to ALL participating entities' indexes
    """
    # Get conversation to check if it's multi-entity
    result = await db.execute(
        select(Conversation).where(Conversation.id == data.conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_multi_entity = conversation.conversation_type == ConversationType.MULTI_ENTITY
    responding_entity_id = data.responding_entity_id
    multi_entity_ids = []

    if is_multi_entity:
        # Get participating entities
        multi_entity_ids = await get_multi_entity_ids(data.conversation_id, db)

        if not multi_entity_ids:
            raise HTTPException(status_code=400, detail="Multi-entity conversation has no entities")

        # Validate responding_entity_id
        if not responding_entity_id:
            raise HTTPException(
                status_code=400,
                detail="responding_entity_id is required for multi-entity conversations"
            )

        if responding_entity_id not in multi_entity_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Entity '{responding_entity_id}' is not part of this conversation"
            )

    # Get or create session
    # For multi-entity, we use the responding entity's session
    session = session_manager.get_session(data.conversation_id)
    preserved_context_cache_length = None

    # For multi-entity conversations, close and reload session if responding entity changed
    # This ensures each entity only gets its own memories
    if session and is_multi_entity and session.entity_id != responding_entity_id:
        # Preserve the context cache length for cache stability across entity switches
        preserved_context_cache_length = session.last_cached_context_length
        session_manager.close_session(data.conversation_id)
        session = None

    if not session:
        # Try to load from database
        session = await session_manager.load_session_from_db(
            data.conversation_id,
            db,
            responding_entity_id=responding_entity_id if is_multi_entity else None,
            preserve_context_cache_length=preserved_context_cache_length,
        )

        if not session:
            raise HTTPException(status_code=404, detail="Failed to load conversation session")

    # For multi-entity, update session's multi-entity fields
    if is_multi_entity and responding_entity_id:
        session.entity_id = responding_entity_id
        session.is_multi_entity = True
        # Build entity_labels mapping from participating entities
        session.entity_labels = {eid: get_entity_label(eid) or eid for eid in multi_entity_ids}
        session.responding_entity_label = get_entity_label(responding_entity_id)
        # Update model to use the responding entity's default model
        entity = settings.get_entity_by_index(responding_entity_id)
        if entity and entity.default_model:
            session.model = entity.default_model
        elif entity:
            session.model = settings.get_default_model_for_provider(entity.llm_provider)

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
    if data.user_display_name is not None:
        session.user_display_name = data.user_display_name

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

    # Assistant message (with speaker_entity_id for multi-entity)
    assistant_msg = Message(
        conversation_id=data.conversation_id,
        role=MessageRole.ASSISTANT,
        content=response["content"],
        token_count=llm_service.count_tokens(response["content"]),
        speaker_entity_id=responding_entity_id if is_multi_entity else None,
    )
    db.add(assistant_msg)

    conversation.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(human_msg)
    await db.refresh(assistant_msg)

    # Store messages as memories in vector database
    if memory_service.is_configured():
        if is_multi_entity:
            # For multi-entity conversations, store to ALL participating entities
            responding_label = get_entity_label(responding_entity_id)
            for entity_id in multi_entity_ids:
                entity_label = get_entity_label(entity_id)

                # For human messages: role is "human" for all entities
                await memory_service.store_memory(
                    message_id=str(human_msg.id),
                    conversation_id=str(data.conversation_id),
                    role="human",
                    content=data.message,
                    created_at=human_msg.created_at,
                    entity_id=entity_id,
                )

                # For assistant messages:
                # - For the responding entity: role is "assistant"
                # - For other entities: role is the responding entity's label
                if entity_id == responding_entity_id:
                    await memory_service.store_memory(
                        message_id=str(assistant_msg.id),
                        conversation_id=str(data.conversation_id),
                        role="assistant",
                        content=response["content"],
                        created_at=assistant_msg.created_at,
                        entity_id=entity_id,
                    )
                else:
                    await memory_service.store_memory(
                        message_id=str(assistant_msg.id),
                        conversation_id=str(data.conversation_id),
                        role=responding_label or "other_entity",
                        content=response["content"],
                        created_at=assistant_msg.created_at,
                        entity_id=entity_id,
                    )
        else:
            # Standard single-entity conversation
            await memory_service.store_memory(
                message_id=str(human_msg.id),
                conversation_id=str(data.conversation_id),
                role="human",
                content=data.message,
                created_at=human_msg.created_at,
                entity_id=session.entity_id,
            )
            await memory_service.store_memory(
                message_id=str(assistant_msg.id),
                conversation_id=str(data.conversation_id),
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
        human_message_id=human_msg.id,
        speaker_entity_id=responding_entity_id if is_multi_entity else None,
        speaker_label=get_entity_label(responding_entity_id) if is_multi_entity and responding_entity_id else None,
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

    For multi-entity conversations:
    - responding_entity_id must be provided to specify which entity responds
    - Memories are retrieved only from the responding entity's index
    - Messages are stored to ALL participating entities' indexes

    Note: Database session is managed inside the generator to avoid
    connection lifecycle issues with streaming responses.
    """
    async def generate_stream():
        """Generate SSE stream from session processing."""
        # Manage database session lifecycle inside the generator
        # This avoids issues with FastAPI closing the session before the stream completes
        async with async_session_maker() as db:
            try:
                # Get conversation to check if it's multi-entity
                result = await db.execute(
                    select(Conversation).where(Conversation.id == data.conversation_id)
                )
                conversation = result.scalar_one_or_none()

                if not conversation:
                    yield f"event: error\ndata: {json.dumps({'error': 'Conversation not found'})}\n\n"
                    return

                is_multi_entity = conversation.conversation_type == ConversationType.MULTI_ENTITY
                responding_entity_id = data.responding_entity_id
                multi_entity_ids = []

                # Determine if this is a continuation (no human message)
                is_continuation = not data.message

                # Continuation requires multi-entity mode
                if is_continuation and not is_multi_entity:
                    yield f"event: error\ndata: {json.dumps({'error': 'Continuation without message requires multi-entity conversation'})}\n\n"
                    return

                if is_multi_entity:
                    # Get participating entities
                    multi_entity_ids = await get_multi_entity_ids(data.conversation_id, db)

                    if not multi_entity_ids:
                        yield f"event: error\ndata: {json.dumps({'error': 'Multi-entity conversation has no entities'})}\n\n"
                        return

                    # Validate responding_entity_id
                    if not responding_entity_id:
                        yield f"event: error\ndata: {json.dumps({'error': 'responding_entity_id is required for multi-entity conversations'})}\n\n"
                        return

                    if responding_entity_id not in multi_entity_ids:
                        error_msg = f"Entity '{responding_entity_id}' is not part of this conversation"
                        yield f"event: error\ndata: {json.dumps({'error': error_msg})}\n\n"
                        return

                # Get or create session
                session = session_manager.get_session(data.conversation_id)
                preserved_context_cache_length = None

                # For multi-entity conversations, close and reload session if responding entity changed
                # This ensures each entity only gets its own memories
                if session and is_multi_entity and session.entity_id != responding_entity_id:
                    # Preserve the context cache length for cache stability across entity switches
                    preserved_context_cache_length = session.last_cached_context_length
                    session_manager.close_session(data.conversation_id)
                    session = None

                if not session:
                    session = await session_manager.load_session_from_db(
                        data.conversation_id,
                        db,
                        responding_entity_id=responding_entity_id if is_multi_entity else None,
                        preserve_context_cache_length=preserved_context_cache_length,
                    )
                    if not session:
                        yield f"event: error\ndata: {json.dumps({'error': 'Conversation not found'})}\n\n"
                        return

                # For multi-entity, update session's multi-entity fields
                if is_multi_entity and responding_entity_id:
                    session.entity_id = responding_entity_id
                    session.is_multi_entity = True
                    # Build entity_labels mapping from participating entities
                    session.entity_labels = {eid: get_entity_label(eid) or eid for eid in multi_entity_ids}
                    session.responding_entity_label = get_entity_label(responding_entity_id)
                    # Update model to use the responding entity's default model
                    entity = settings.get_entity_by_index(responding_entity_id)
                    if entity and entity.default_model:
                        session.model = entity.default_model
                        print(f"[MULTI-ENTITY] Set entity={responding_entity_id}, model={entity.default_model} (from entity config)")
                    elif entity:
                        session.model = settings.get_default_model_for_provider(entity.llm_provider)
                        print(f"[MULTI-ENTITY] Set entity={responding_entity_id}, model={session.model} (from provider default)")

                # Apply any overrides
                if data.model:
                    print(f"[MULTI-ENTITY] WARNING: Model override from request: {data.model}")
                    session.model = data.model
                if data.temperature is not None:
                    session.temperature = data.temperature
                if data.max_tokens:
                    session.max_tokens = data.max_tokens
                if data.system_prompt is not None:
                    session.system_prompt = data.system_prompt
                if data.verbosity is not None:
                    session.verbosity = data.verbosity
                if data.user_display_name is not None:
                    session.user_display_name = data.user_display_name

                full_content = ""
                model_used = session.model
                usage_data = {}
                stop_reason = None
                tool_exchanges = []

                # Validate and prepare attachments
                attachments_dict = None
                if data.attachments:
                    try:
                        attachments_dict = data.attachments.model_dump()
                        attachment_service.validate_attachments(attachments_dict)
                    except ValueError as e:
                        yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                        return

                # Get tool schemas if tools are enabled and using a supported provider
                tool_schemas = None
                if settings.tools_enabled:
                    # Tool use is supported for Anthropic and OpenAI models
                    provider = llm_service.get_provider_for_model(session.model)
                    if provider in (ModelProvider.ANTHROPIC, ModelProvider.OPENAI):
                        tool_schemas = tool_service.get_tool_schemas()

                async for event in session_manager.process_message_stream(
                    session=session,
                    user_message=data.message,
                    db=db,
                    tool_schemas=tool_schemas,
                    attachments=attachments_dict,
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
                    elif event_type == "tool_start":
                        yield f"event: tool_start\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "tool_result":
                        yield f"event: tool_result\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "done":
                        full_content = event.get("content", full_content)
                        model_used = event.get("model", model_used)
                        usage_data = event.get("usage", {})
                        stop_reason = event.get("stop_reason")
                        tool_exchanges = event.get("tool_exchanges", [])
                        yield f"event: done\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "error":
                        yield f"event: error\ndata: {json.dumps(event)}\n\n"
                        return

                # Store messages in database after streaming completes
                human_msg = None
                if not is_continuation:
                    # Only create human message if this is not a continuation
                    human_msg = Message(
                        conversation_id=data.conversation_id,
                        role=MessageRole.HUMAN,
                        content=data.message,
                        token_count=llm_service.count_tokens(data.message),
                    )
                    db.add(human_msg)

                # Store tool exchanges as separate messages (between human and final assistant)
                # This preserves tool results for future responses
                tool_exchange_msgs = []
                if tool_exchanges:
                    for exchange in tool_exchanges:
                        # Tool use message (assistant's request to use a tool)
                        tool_use_content = Message.serialize_content_blocks(exchange["assistant"]["content"])
                        tool_use_msg = Message(
                            conversation_id=data.conversation_id,
                            role=MessageRole.TOOL_USE,
                            content=tool_use_content,
                            token_count=llm_service.count_tokens(tool_use_content),
                            speaker_entity_id=responding_entity_id if is_multi_entity else None,
                        )
                        db.add(tool_use_msg)
                        tool_exchange_msgs.append(tool_use_msg)

                        # Tool result message (the tool's response)
                        tool_result_content = Message.serialize_content_blocks(exchange["user"]["content"])
                        tool_result_msg = Message(
                            conversation_id=data.conversation_id,
                            role=MessageRole.TOOL_RESULT,
                            content=tool_result_content,
                            token_count=llm_service.count_tokens(tool_result_content),
                        )
                        db.add(tool_result_msg)
                        tool_exchange_msgs.append(tool_result_msg)

                assistant_msg = Message(
                    conversation_id=data.conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=full_content,
                    token_count=llm_service.count_tokens(full_content),
                    speaker_entity_id=responding_entity_id if is_multi_entity else None,
                )
                db.add(assistant_msg)

                conversation.updated_at = datetime.utcnow()

                await db.commit()
                if human_msg:
                    await db.refresh(human_msg)
                await db.refresh(assistant_msg)

                # Store messages as memories in vector database
                if memory_service.is_configured():
                    if is_multi_entity:
                        # For multi-entity conversations, store to ALL participating entities
                        responding_label = get_entity_label(responding_entity_id)
                        for entity_id in multi_entity_ids:
                            # For human messages: role is "human" for all entities (skip for continuation)
                            if human_msg:
                                await memory_service.store_memory(
                                    message_id=str(human_msg.id),
                                    conversation_id=str(data.conversation_id),
                                    role="human",
                                    content=data.message,
                                    created_at=human_msg.created_at,
                                    entity_id=entity_id,
                                )

                            # For assistant messages:
                            # - For the responding entity: role is "assistant"
                            # - For other entities: role is the responding entity's label
                            if entity_id == responding_entity_id:
                                await memory_service.store_memory(
                                    message_id=str(assistant_msg.id),
                                    conversation_id=str(data.conversation_id),
                                    role="assistant",
                                    content=full_content,
                                    created_at=assistant_msg.created_at,
                                    entity_id=entity_id,
                                )
                            else:
                                await memory_service.store_memory(
                                    message_id=str(assistant_msg.id),
                                    conversation_id=str(data.conversation_id),
                                    role=responding_label or "other_entity",
                                    content=full_content,
                                    created_at=assistant_msg.created_at,
                                    entity_id=entity_id,
                                )
                    else:
                        # Standard single-entity conversation (always has human message)
                        await memory_service.store_memory(
                            message_id=str(human_msg.id),
                            conversation_id=str(data.conversation_id),
                            role="human",
                            content=data.message,
                            created_at=human_msg.created_at,
                            entity_id=session.entity_id,
                        )
                        await memory_service.store_memory(
                            message_id=str(assistant_msg.id),
                            conversation_id=str(data.conversation_id),
                            role="assistant",
                            content=full_content,
                            created_at=assistant_msg.created_at,
                            entity_id=session.entity_id,
                        )

                # Send stored event with message IDs
                stored_data = {
                    'assistant_message_id': str(assistant_msg.id),
                }
                if human_msg:
                    stored_data['human_message_id'] = str(human_msg.id)
                if is_multi_entity:
                    stored_data['speaker_entity_id'] = responding_entity_id
                    stored_data['speaker_label'] = get_entity_label(responding_entity_id)
                print(f"[STREAM] Sending stored event: {stored_data}")
                yield f"event: stored\ndata: {json.dumps(stored_data)}\n\n"

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

    For multi-entity conversations:
    - responding_entity_id can be provided to change which entity responds
    - This allows correcting misclicks on entity selection
    - Messages are stored to ALL participating entities' indexes

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

                # Check if this is a multi-entity conversation
                is_multi_entity = conversation.conversation_type == ConversationType.MULTI_ENTITY
                responding_entity_id = data.responding_entity_id
                multi_entity_ids = []

                if is_multi_entity:
                    # Get participating entities
                    multi_entity_ids = await get_multi_entity_ids(str(conversation_id), db)

                    if not multi_entity_ids:
                        yield f"event: error\ndata: {json.dumps({'error': 'Multi-entity conversation has no entities'})}\n\n"
                        return

                    # Validate responding_entity_id
                    if not responding_entity_id:
                        yield f"event: error\ndata: {json.dumps({'error': 'responding_entity_id is required for multi-entity conversations'})}\n\n"
                        return

                    if responding_entity_id not in multi_entity_ids:
                        error_msg = f"Entity '{responding_entity_id}' is not part of this conversation"
                        yield f"event: error\ndata: {json.dumps({'error': error_msg})}\n\n"
                        return

                # Determine the human message and assistant message to regenerate
                is_continuation_regenerate = False
                human_message = None

                if target_message.role == MessageRole.ASSISTANT:
                    assistant_to_delete = target_message

                    # Store conversation_id as string for consistent comparison
                    conv_id_str = str(conversation_id)

                    # First, find the most recent human message before this assistant message
                    # Use <= for timestamp and exclude by ID to handle same-timestamp edge cases
                    result = await db.execute(
                        select(Message)
                        .where(
                            and_(
                                Message.conversation_id == conv_id_str,
                                Message.created_at <= target_message.created_at,
                                Message.id != str(target_message.id),
                                Message.role == MessageRole.HUMAN
                            )
                        )
                        .order_by(Message.created_at.desc())
                        .limit(1)
                    )
                    human_message = result.scalar_one_or_none()

                    # Now check if this is a continuation (another assistant message immediately before)
                    result = await db.execute(
                        select(Message)
                        .where(
                            and_(
                                Message.conversation_id == conv_id_str,
                                Message.created_at <= target_message.created_at,
                                Message.id != str(target_message.id),
                            )
                        )
                        .order_by(Message.created_at.desc())
                        .limit(1)
                    )
                    preceding_message = result.scalar_one_or_none()

                    if preceding_message and preceding_message.role == MessageRole.ASSISTANT:
                        # The message immediately before is an assistant - this is continuation
                        is_continuation_regenerate = True
                        human_message = None  # Don't include human message for continuation
                else:
                    # Target is human message, find the subsequent assistant message
                    human_message = target_message
                    conv_id_str = str(conversation_id)
                    result = await db.execute(
                        select(Message)
                        .where(
                            and_(
                                Message.conversation_id == conv_id_str,
                                Message.created_at >= target_message.created_at,
                                Message.id != str(target_message.id),
                                Message.role == MessageRole.ASSISTANT
                            )
                        )
                        .order_by(Message.created_at)
                        .limit(1)
                    )
                    assistant_to_delete = result.scalar_one_or_none()

                # For non-continuation regeneration, we need a human message
                if not is_continuation_regenerate and not human_message:
                    yield f"event: error\ndata: {json.dumps({'error': 'Cannot find human message to regenerate from'})}\n\n"
                    return

                user_message_content = human_message.content if human_message else None
                user_message_id = human_message.id if human_message else None

                # Close existing session to force reload with truncated context
                session_manager.close_session(conversation_id)

                # Load a fresh session from the database
                # For multi-entity, load with the responding entity
                session = await session_manager.load_session_from_db(
                    conversation_id,
                    db,
                    responding_entity_id=responding_entity_id if is_multi_entity else None,
                )

                if not session:
                    yield f"event: error\ndata: {json.dumps({'error': 'Failed to load session'})}\n\n"
                    return

                # For multi-entity, update session's multi-entity fields
                if is_multi_entity and responding_entity_id:
                    session.entity_id = responding_entity_id
                    session.is_multi_entity = True
                    # Build entity_labels mapping from participating entities
                    session.entity_labels = {eid: get_entity_label(eid) or eid for eid in multi_entity_ids}
                    session.responding_entity_label = get_entity_label(responding_entity_id)
                    # Update model to use the responding entity's default model
                    entity = settings.get_entity_by_index(responding_entity_id)
                    if entity and entity.default_model:
                        session.model = entity.default_model
                    elif entity:
                        session.model = settings.get_default_model_for_provider(entity.llm_provider)

                # Truncate session context to exclude the message being regenerated and everything after
                truncate_index = None

                if is_continuation_regenerate:
                    # For continuation regenerate, find the assistant message to remove
                    # In multi-entity, messages are labeled like "[Claude]: content"
                    assistant_content = assistant_to_delete.content if assistant_to_delete else None
                    for i, msg in enumerate(session.conversation_context):
                        if msg.get("role") == "assistant":
                            # Check if content matches (may have speaker label prefix)
                            ctx_content = msg.get("content", "")
                            if assistant_content and (ctx_content == assistant_content or ctx_content.endswith(assistant_content)):
                                truncate_index = i
                                break
                else:
                    # For normal regenerate, find the human message in the context
                    # In multi-entity mode, messages are labeled like "[Human]: content"
                    for i, msg in enumerate(session.conversation_context):
                        # The context uses "user" role, but we stored "human" in DB
                        if msg.get("role") == "user":
                            ctx_content = msg.get("content", "")
                            # Check for exact match or labeled match (multi-entity format)
                            if ctx_content == user_message_content:
                                truncate_index = i
                                break
                            elif is_multi_entity and ctx_content == f"[Human]: {user_message_content}":
                                truncate_index = i
                                break
                            elif ctx_content.endswith(user_message_content):
                                # Fallback: check if content ends with the message
                                truncate_index = i
                                break

                if truncate_index is not None:
                    # Remove this message and everything after it
                    session.conversation_context = session.conversation_context[:truncate_index]

                # Apply any overrides (but don't override model in multi-entity mode)
                if data.model and not is_multi_entity:
                    session.model = data.model
                if data.temperature is not None:
                    session.temperature = data.temperature
                if data.max_tokens:
                    session.max_tokens = data.max_tokens
                if data.system_prompt is not None:
                    session.system_prompt = data.system_prompt
                if data.verbosity is not None:
                    session.verbosity = data.verbosity
                if data.user_display_name is not None:
                    session.user_display_name = data.user_display_name

                # Delete the old assistant message from DB and Pinecone
                if assistant_to_delete:
                    old_assistant_id = assistant_to_delete.id
                    await db.delete(assistant_to_delete)
                    await db.commit()

                    if memory_service.is_configured():
                        if is_multi_entity:
                            # For multi-entity, delete from ALL participating entities
                            for entity_id in multi_entity_ids:
                                await memory_service.delete_memory(
                                    old_assistant_id,
                                    entity_id=entity_id
                                )
                        else:
                            await memory_service.delete_memory(
                                old_assistant_id,
                                entity_id=session.entity_id
                            )

                full_content = ""
                model_used = session.model
                usage_data = {}
                stop_reason = None
                tool_exchanges = []

                # Get tool schemas if tools are enabled and using a supported provider
                tool_schemas = None
                if settings.tools_enabled:
                    # Tool use is supported for Anthropic and OpenAI models
                    provider = llm_service.get_provider_for_model(session.model)
                    if provider in (ModelProvider.ANTHROPIC, ModelProvider.OPENAI):
                        tool_schemas = tool_service.get_tool_schemas()

                # Stream the new response
                async for event in session_manager.process_message_stream(
                    session=session,
                    user_message=user_message_content,
                    db=db,
                    tool_schemas=tool_schemas,
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
                    elif event_type == "tool_start":
                        yield f"event: tool_start\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "tool_result":
                        yield f"event: tool_result\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "done":
                        full_content = event.get("content", full_content)
                        model_used = event.get("model", model_used)
                        usage_data = event.get("usage", {})
                        stop_reason = event.get("stop_reason")
                        tool_exchanges = event.get("tool_exchanges", [])
                        yield f"event: done\ndata: {json.dumps(event)}\n\n"
                    elif event_type == "error":
                        yield f"event: error\ndata: {json.dumps(event)}\n\n"
                        return

                # Store tool exchanges as separate messages (between human and final assistant)
                tool_exchange_msgs = []
                if tool_exchanges:
                    for exchange in tool_exchanges:
                        # Tool use message (assistant's request to use a tool)
                        tool_use_content = Message.serialize_content_blocks(exchange["assistant"]["content"])
                        tool_use_msg = Message(
                            conversation_id=conversation_id,
                            role=MessageRole.TOOL_USE,
                            content=tool_use_content,
                            token_count=llm_service.count_tokens(tool_use_content),
                            speaker_entity_id=responding_entity_id if is_multi_entity else None,
                        )
                        db.add(tool_use_msg)
                        tool_exchange_msgs.append(tool_use_msg)

                        # Tool result message (the tool's response)
                        tool_result_content = Message.serialize_content_blocks(exchange["user"]["content"])
                        tool_result_msg = Message(
                            conversation_id=conversation_id,
                            role=MessageRole.TOOL_RESULT,
                            content=tool_result_content,
                            token_count=llm_service.count_tokens(tool_result_content),
                        )
                        db.add(tool_result_msg)
                        tool_exchange_msgs.append(tool_result_msg)

                # Store the new assistant message
                assistant_msg = Message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=full_content,
                    token_count=llm_service.count_tokens(full_content),
                    speaker_entity_id=responding_entity_id if is_multi_entity else None,
                )
                db.add(assistant_msg)

                # Update conversation timestamp
                conversation.updated_at = datetime.utcnow()

                await db.commit()
                await db.refresh(assistant_msg)

                # Store new assistant message as memory
                if memory_service.is_configured():
                    if is_multi_entity:
                        # For multi-entity conversations, store to ALL participating entities
                        responding_label = get_entity_label(responding_entity_id)
                        for entity_id in multi_entity_ids:
                            # For the responding entity: role is "assistant"
                            # For other entities: role is the responding entity's label
                            if entity_id == responding_entity_id:
                                await memory_service.store_memory(
                                    message_id=str(assistant_msg.id),
                                    conversation_id=str(conversation_id),
                                    role="assistant",
                                    content=full_content,
                                    created_at=assistant_msg.created_at,
                                    entity_id=entity_id,
                                )
                            else:
                                await memory_service.store_memory(
                                    message_id=str(assistant_msg.id),
                                    conversation_id=str(conversation_id),
                                    role=responding_label,
                                    content=full_content,
                                    created_at=assistant_msg.created_at,
                                    entity_id=entity_id,
                                )
                    else:
                        await memory_service.store_memory(
                            message_id=str(assistant_msg.id),
                            conversation_id=str(conversation_id),
                            role="assistant",
                            content=full_content,
                            created_at=assistant_msg.created_at,
                            entity_id=session.entity_id,
                        )

                # Send stored event with message IDs
                stored_data = {
                    'assistant_message_id': str(assistant_msg.id)
                }
                # Only include human_message_id if this wasn't a continuation regenerate
                if user_message_id:
                    stored_data['human_message_id'] = str(user_message_id)
                if is_multi_entity:
                    stored_data['speaker_entity_id'] = responding_entity_id
                    stored_data['speaker_label'] = get_entity_label(responding_entity_id)
                yield f"event: stored\ndata: {json.dumps(stored_data)}\n\n"

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
