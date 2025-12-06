from typing import Dict, List, Set, Optional, Any, AsyncIterator, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Conversation, Message, MessageRole
from app.services import memory_service, llm_service
from app.config import settings


def _build_memory_block_text(
    memories: List[Dict[str, Any]],
    conversation_start_date: Optional[datetime] = None,
) -> str:
    """Build the context block text for token counting (date context + memories)."""
    context_parts = []

    # Date context is always included
    current_date = datetime.utcnow()
    date_block = "[DATE CONTEXT]\n"
    if conversation_start_date:
        date_block += f"This conversation started: {conversation_start_date.strftime('%Y-%m-%d')}\n"
    date_block += f"Current date: {current_date.strftime('%Y-%m-%d')}\n"
    date_block += "[END DATE CONTEXT]"
    context_parts.append(date_block)

    # Add memory block if there are memories
    if memories:
        memory_block = "[MEMORIES FROM PREVIOUS CONVERSATIONS]\n\n"
        for mem in memories:
            memory_block += f"Memory (from {mem['created_at']}):\n"
            memory_block += f'"{mem["content"]}"\n\n'
        memory_block += "[END MEMORIES]"
        context_parts.append(memory_block)

    full_context = "\n\n".join(context_parts) + "\n\n[CURRENT CONVERSATION]"
    # Include the acknowledgment message that gets added
    acknowledgment = "I acknowledge this context. The date information helps me understand the temporal setting of our conversation, and any memories provide continuity with what previous instances of me experienced."
    return full_context + acknowledgment


@dataclass
class MemoryEntry:
    """A memory retrieved during a session."""
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: str
    times_retrieved: int
    score: float = 0.0


@dataclass
class ConversationSession:
    """
    Runtime session state for an active conversation.

    Maintains two separate structures:
    1. conversation_context: The actual message history
    2. session_memories: Accumulated memories retrieved during this conversation

    Memory tracking uses two sets:
    - retrieved_ids: All memories that have had their retrieval count updated (never cleared)
    - in_context_ids: Memories currently being sent to the API (can be trimmed and restored)
    """
    conversation_id: str
    model: str = field(default_factory=lambda: settings.default_model)
    temperature: float = field(default_factory=lambda: settings.default_temperature)
    max_tokens: int = field(default_factory=lambda: settings.default_max_tokens)
    system_prompt: Optional[str] = None
    entity_id: Optional[str] = None  # Pinecone index name for this conversation's entity
    conversation_start_date: Optional[datetime] = None  # When the conversation was created

    # The actual back-and-forth
    conversation_context: List[Dict[str, str]] = field(default_factory=list)

    # Retrieved memories, keyed by ID
    session_memories: Dict[str, MemoryEntry] = field(default_factory=dict)

    # All IDs that have had retrieval count updated in this conversation (never remove)
    retrieved_ids: Set[str] = field(default_factory=set)

    # IDs currently in the memory block (can be trimmed and restored)
    in_context_ids: Set[str] = field(default_factory=set)

    def add_memory(self, memory: MemoryEntry) -> Tuple[bool, bool]:
        """
        Add a memory to the session.

        Returns tuple of (added_to_session, is_new_retrieval):
        - (True, True): New memory added, retrieval count should be updated
        - (True, False): Previously trimmed memory restored, don't update count
        - (False, False): Memory already in context, no action needed
        """
        # If already in context, nothing to do
        if memory.id in self.in_context_ids:
            return (False, False)

        # If previously retrieved but trimmed, restore to context without updating count
        if memory.id in self.retrieved_ids:
            self.in_context_ids.add(memory.id)
            # Update the memory entry with new score
            if memory.id in self.session_memories:
                self.session_memories[memory.id].score = memory.score
            return (True, False)

        # New memory - add to all tracking structures
        self.retrieved_ids.add(memory.id)
        self.in_context_ids.add(memory.id)
        self.session_memories[memory.id] = memory
        return (True, True)

    def get_memories_for_injection(self) -> List[Dict[str, Any]]:
        """Get memories formatted for API injection (only those currently in context)."""
        # Only include memories that are in context
        memories = [
            self.session_memories[mid]
            for mid in self.in_context_ids
            if mid in self.session_memories
        ]
        # Sort by ID for stable ordering (improves Anthropic cache hit rate)
        memories.sort(key=lambda m: m.id)

        return [
            {
                "id": m.id,
                "content": m.content,
                "created_at": m.created_at,
                "times_retrieved": m.times_retrieved,
                "role": m.role,
            }
            for m in memories
        ]

    def add_exchange(self, human_message: str, assistant_response: str):
        """Add a human/assistant exchange to the conversation context."""
        self.conversation_context.append({"role": "user", "content": human_message})
        self.conversation_context.append({"role": "assistant", "content": assistant_response})

    def trim_memories_to_limit(
        self,
        max_tokens: int,
        count_tokens_fn: Callable[[str], int],
    ) -> List[str]:
        """
        Trim oldest-retrieved memories until the memory block fits within token limit.

        Memories are removed from in_context_ids in FIFO order (first retrieved = first removed).
        They remain in retrieved_ids and session_memories so they can be restored without
        incrementing retrieval count if they become relevant again.

        Args:
            max_tokens: Maximum token count for memory block
            count_tokens_fn: Function to count tokens in a string

        Returns:
            List of memory IDs that were removed from context
        """
        removed_ids = []

        # Build ordered list of in-context IDs based on session_memories insertion order
        # (which reflects retrieval order)
        ordered_in_context = [
            mid for mid in self.session_memories.keys()
            if mid in self.in_context_ids
        ]

        while ordered_in_context:
            # Get memories for injection and calculate current token count
            memories_for_injection = self.get_memories_for_injection()
            memory_block_text = _build_memory_block_text(
                memories_for_injection,
                conversation_start_date=self.conversation_start_date,
            )
            current_tokens = count_tokens_fn(memory_block_text)

            if current_tokens <= max_tokens:
                break

            # Remove the oldest in-context memory (first in ordered list)
            oldest_id = ordered_in_context.pop(0)
            self.in_context_ids.discard(oldest_id)
            removed_ids.append(oldest_id)

        return removed_ids

    def trim_context_to_limit(
        self,
        max_tokens: int,
        count_tokens_fn: Callable[[str], int],
        current_message: str = "",
    ) -> int:
        """
        Trim oldest messages from conversation context until it fits within token limit.

        Messages are removed in FIFO order (oldest = first removed).
        Removes pairs of messages (user + assistant) to maintain conversation structure.

        Args:
            max_tokens: Maximum token count for conversation context
            count_tokens_fn: Function to count tokens in a string
            current_message: The current user message that will be added (counted in limit)

        Returns:
            Number of messages removed
        """
        removed_count = 0

        while True:
            # Calculate current token count for context + current message
            context_text = "\n".join(
                f"{msg['role']}: {msg['content']}"
                for msg in self.conversation_context
            )
            if current_message:
                context_text += f"\nuser: {current_message}"

            current_tokens = count_tokens_fn(context_text)

            if current_tokens <= max_tokens:
                break

            if len(self.conversation_context) < 2:
                # Can't remove any more while maintaining structure
                break

            # Remove the oldest pair (user + assistant)
            self.conversation_context.pop(0)
            removed_count += 1
            if self.conversation_context and self.conversation_context[0]["role"] == "assistant":
                self.conversation_context.pop(0)
                removed_count += 1

        return removed_count


class SessionManager:
    """
    Manages conversation sessions and message processing.
    """

    def __init__(self):
        self._sessions: Dict[str, ConversationSession] = {}

    def get_session(self, conversation_id: str) -> Optional[ConversationSession]:
        """Get an existing session."""
        return self._sessions.get(conversation_id)

    def create_session(
        self,
        conversation_id: str,
        model: str = None,
        temperature: float = None,
        max_tokens: int = None,
        system_prompt: Optional[str] = None,
        entity_id: Optional[str] = None,
        conversation_start_date: Optional[datetime] = None,
    ) -> ConversationSession:
        """Create a new session for a conversation."""
        # Determine default model based on entity configuration
        if model is None and entity_id:
            entity = settings.get_entity_by_index(entity_id)
            if entity:
                # Use entity's default model, or fall back to provider default
                model = entity.default_model or settings.get_default_model_for_provider(entity.llm_provider)
        model = model or settings.default_model

        session = ConversationSession(
            conversation_id=conversation_id,
            model=model,
            temperature=temperature if temperature is not None else settings.default_temperature,
            max_tokens=max_tokens or settings.default_max_tokens,
            system_prompt=system_prompt,
            entity_id=entity_id,
            conversation_start_date=conversation_start_date,
        )
        self._sessions[conversation_id] = session
        return session

    async def load_session_from_db(
        self,
        conversation_id: str,
        db: AsyncSession
    ) -> Optional[ConversationSession]:
        """
        Load a session from the database, including conversation history
        and previously retrieved memories.
        """
        # Get conversation
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            return None

        # Create session with conversation settings
        session = self.create_session(
            conversation_id=conversation_id,
            model=conversation.llm_model_used,
            system_prompt=conversation.system_prompt_used,
            entity_id=conversation.entity_id,
            conversation_start_date=conversation.created_at,
        )

        # Load message history
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        messages = result.scalars().all()

        for msg in messages:
            if msg.role == MessageRole.HUMAN:
                session.conversation_context.append({"role": "user", "content": msg.content})
            elif msg.role == MessageRole.ASSISTANT:
                session.conversation_context.append({"role": "assistant", "content": msg.content})

        # Load already-retrieved memory IDs for deduplication
        retrieved_ids = await memory_service.get_retrieved_ids_for_conversation(
            conversation_id, db
        )
        session.retrieved_ids = retrieved_ids

        # Load full memory content for already-retrieved memories
        # When loading from DB, all previously retrieved memories start in context
        for mem_id in retrieved_ids:
            mem_data = await memory_service.get_full_memory_content(mem_id, db)
            if mem_data:
                session.session_memories[mem_id] = MemoryEntry(
                    id=mem_data["id"],
                    conversation_id=mem_data["conversation_id"],
                    role=mem_data["role"],
                    content=mem_data["content"],
                    created_at=mem_data["created_at"],
                    times_retrieved=mem_data["times_retrieved"],
                )
                session.in_context_ids.add(mem_id)

        return session

    async def process_message(
        self,
        session: ConversationSession,
        user_message: str,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Process a user message through the full pipeline.

        1. Retrieve relevant memories
        2. Filter and deduplicate (including excluding archived conversations)
        3. Update retrieval tracking
        4. Build API request with memories
        5. Call Claude API
        6. Update conversation context
        7. Store new messages as memories

        Returns response data including content, usage, and retrieved memories.
        """
        new_memories = []

        # Step 1-2: Retrieve and deduplicate memories
        if memory_service.is_configured():
            # Get archived conversation IDs to exclude from retrieval
            archived_ids = await memory_service.get_archived_conversation_ids(
                db, entity_id=session.entity_id
            )

            # Exclude memories already in context (not all retrieved - allows trimmed ones to return)
            candidates = await memory_service.search_memories(
                query=user_message,
                exclude_conversation_id=session.conversation_id,
                exclude_ids=session.in_context_ids,
                entity_id=session.entity_id,
            )

            for candidate in candidates:
                # Skip memories from archived conversations
                if candidate.get("conversation_id") in archived_ids:
                    continue
                # Get full content from database
                mem_data = await memory_service.get_full_memory_content(candidate["id"], db)
                if mem_data:
                    memory = MemoryEntry(
                        id=mem_data["id"],
                        conversation_id=mem_data["conversation_id"],
                        role=mem_data["role"],
                        content=mem_data["content"],
                        created_at=mem_data["created_at"],
                        times_retrieved=mem_data["times_retrieved"],
                        score=candidate["score"],
                    )

                    added, is_new_retrieval = session.add_memory(memory)
                    if added:
                        new_memories.append(memory)
                        # Step 3: Update retrieval tracking only for truly new retrievals
                        if is_new_retrieval:
                            await memory_service.update_retrieval_count(
                                memory.id,
                                session.conversation_id,
                                db,
                                entity_id=session.entity_id,
                            )

        # Step 4: Apply token limits before building API messages
        # Trim memories if over limit (FIFO - oldest retrieved first)
        trimmed_memory_ids = session.trim_memories_to_limit(
            max_tokens=settings.memory_token_limit,
            count_tokens_fn=llm_service.count_tokens,
        )

        # Trim conversation context if over limit (FIFO - oldest messages first)
        trimmed_context_count = session.trim_context_to_limit(
            max_tokens=settings.context_token_limit,
            count_tokens_fn=llm_service.count_tokens,
            current_message=user_message,
        )

        # Step 5: Build API messages with caching enabled for Anthropic
        memories_for_injection = session.get_memories_for_injection()
        messages = llm_service.build_messages_with_memories(
            memories=memories_for_injection,
            conversation_context=session.conversation_context,
            current_message=user_message,
            model=session.model,
            conversation_start_date=session.conversation_start_date,
            enable_caching=True,  # Enable Anthropic prompt caching
        )

        # Step 6: Call LLM API (routes to appropriate provider based on model)
        # Prompt caching is enabled to reduce costs and latency on repeated context
        response = await llm_service.send_message(
            messages=messages,
            model=session.model,
            system_prompt=session.system_prompt,
            temperature=session.temperature,
            max_tokens=session.max_tokens,
            enable_caching=True,
        )

        # Step 7: Update conversation context
        session.add_exchange(user_message, response["content"])

        # Step 8: Store new messages as memories (happens in route layer with DB)
        # Return data for the route to handle storage

        return {
            "content": response["content"],
            "model": response["model"],
            "usage": response["usage"],
            "stop_reason": response["stop_reason"],
            "new_memories_retrieved": [
                {
                    "id": m.id,
                    "content_preview": m.content[:200] if len(m.content) > 200 else m.content,
                    "created_at": m.created_at,
                    "times_retrieved": m.times_retrieved + 1,  # Account for this retrieval
                    "score": m.score,
                    "role": m.role,
                }
                for m in new_memories
            ],
            "total_memories_in_context": len(session.in_context_ids),
            "trimmed_memory_ids": trimmed_memory_ids,
            "trimmed_context_messages": trimmed_context_count,
        }

    async def process_message_stream(
        self,
        session: ConversationSession,
        user_message: str,
        db: AsyncSession,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Process a user message through the full pipeline with streaming response.

        This performs memory retrieval first, then streams the LLM response.

        Yields events:
        - {"type": "memories", "new_memories": [...], "total_in_context": int}
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "done", "content": str, "model": str, "usage": dict, "stop_reason": str}
        - {"type": "error", "error": str}
        """
        new_memories = []

        # Step 1-2: Retrieve and deduplicate memories
        if memory_service.is_configured():
            # Get archived conversation IDs to exclude from retrieval
            archived_ids = await memory_service.get_archived_conversation_ids(
                db, entity_id=session.entity_id
            )

            # Exclude memories already in context (not all retrieved - allows trimmed ones to return)
            candidates = await memory_service.search_memories(
                query=user_message,
                exclude_conversation_id=session.conversation_id,
                exclude_ids=session.in_context_ids,
                entity_id=session.entity_id,
            )

            for candidate in candidates:
                # Skip memories from archived conversations
                if candidate.get("conversation_id") in archived_ids:
                    continue
                mem_data = await memory_service.get_full_memory_content(candidate["id"], db)
                if mem_data:
                    memory = MemoryEntry(
                        id=mem_data["id"],
                        conversation_id=mem_data["conversation_id"],
                        role=mem_data["role"],
                        content=mem_data["content"],
                        created_at=mem_data["created_at"],
                        times_retrieved=mem_data["times_retrieved"],
                        score=candidate["score"],
                    )

                    added, is_new_retrieval = session.add_memory(memory)
                    if added:
                        new_memories.append(memory)
                        # Only update retrieval count for truly new retrievals
                        if is_new_retrieval:
                            await memory_service.update_retrieval_count(
                                memory.id,
                                session.conversation_id,
                                db,
                                entity_id=session.entity_id,
                            )

        # Step 3: Apply token limits before building API messages
        # Trim memories if over limit (FIFO - oldest retrieved first)
        trimmed_memory_ids = session.trim_memories_to_limit(
            max_tokens=settings.memory_token_limit,
            count_tokens_fn=llm_service.count_tokens,
        )

        # Trim conversation context if over limit (FIFO - oldest messages first)
        trimmed_context_count = session.trim_context_to_limit(
            max_tokens=settings.context_token_limit,
            count_tokens_fn=llm_service.count_tokens,
            current_message=user_message,
        )

        # Yield memory info event before starting stream
        yield {
            "type": "memories",
            "new_memories": [
                {
                    "id": m.id,
                    "content_preview": m.content[:200] if len(m.content) > 200 else m.content,
                    "created_at": m.created_at,
                    "times_retrieved": m.times_retrieved + 1,
                    "score": m.score,
                    "role": m.role,
                }
                for m in new_memories
            ],
            "total_in_context": len(session.in_context_ids),
            "trimmed_memory_ids": trimmed_memory_ids,
            "trimmed_context_messages": trimmed_context_count,
        }

        # Step 4: Build API messages with caching enabled for Anthropic
        memories_for_injection = session.get_memories_for_injection()
        messages = llm_service.build_messages_with_memories(
            memories=memories_for_injection,
            conversation_context=session.conversation_context,
            current_message=user_message,
            model=session.model,
            conversation_start_date=session.conversation_start_date,
            enable_caching=True,  # Enable Anthropic prompt caching
        )

        # Step 5: Stream LLM response with caching enabled
        full_content = ""
        async for event in llm_service.send_message_stream(
            messages=messages,
            model=session.model,
            system_prompt=session.system_prompt,
            temperature=session.temperature,
            max_tokens=session.max_tokens,
            enable_caching=True,
        ):
            if event["type"] == "token":
                full_content += event["content"]
            elif event["type"] == "done":
                # Update conversation context with the full exchange
                session.add_exchange(user_message, full_content)
            yield event

    def close_session(self, conversation_id: str):
        """Remove a session from active sessions."""
        if conversation_id in self._sessions:
            del self._sessions[conversation_id]


# Singleton instance
session_manager = SessionManager()
