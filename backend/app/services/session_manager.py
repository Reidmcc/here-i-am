from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Conversation, Message, MessageRole
from app.services.memory_service import memory_service
from app.services.anthropic_service import anthropic_service
from app.config import settings


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
    """
    conversation_id: str
    model: str = field(default_factory=lambda: settings.default_model)
    temperature: float = field(default_factory=lambda: settings.default_temperature)
    max_tokens: int = field(default_factory=lambda: settings.default_max_tokens)
    system_prompt: Optional[str] = None

    # The actual back-and-forth
    conversation_context: List[Dict[str, str]] = field(default_factory=list)

    # Retrieved memories, keyed by ID
    session_memories: Dict[str, MemoryEntry] = field(default_factory=dict)

    # Quick lookup for deduplication
    retrieved_ids: Set[str] = field(default_factory=set)

    def add_memory(self, memory: MemoryEntry) -> bool:
        """
        Add a memory to the session if not already present.

        Returns True if added, False if already exists.
        """
        if memory.id in self.retrieved_ids:
            return False

        self.retrieved_ids.add(memory.id)
        self.session_memories[memory.id] = memory
        return True

    def get_memories_for_injection(self) -> List[Dict[str, Any]]:
        """Get memories formatted for API injection."""
        memories = list(self.session_memories.values())
        # Sort by relevance score (most relevant first)
        memories.sort(key=lambda m: m.score, reverse=True)

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
    ) -> ConversationSession:
        """Create a new session for a conversation."""
        session = ConversationSession(
            conversation_id=conversation_id,
            model=model or settings.default_model,
            temperature=temperature if temperature is not None else settings.default_temperature,
            max_tokens=max_tokens or settings.default_max_tokens,
            system_prompt=system_prompt,
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
            model=conversation.model_used,
            system_prompt=conversation.system_prompt_used,
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
        2. Filter and deduplicate
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
            candidates = await memory_service.search_memories(
                query=user_message,
                exclude_conversation_id=session.conversation_id,
                exclude_ids=session.retrieved_ids,
            )

            for candidate in candidates:
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

                    if session.add_memory(memory):
                        new_memories.append(memory)
                        # Step 3: Update retrieval tracking
                        await memory_service.update_retrieval_count(
                            memory.id,
                            session.conversation_id,
                            db
                        )

        # Step 4: Build API messages
        memories_for_injection = session.get_memories_for_injection()
        messages = anthropic_service.build_messages_with_memories(
            memories=memories_for_injection,
            conversation_context=session.conversation_context,
            current_message=user_message,
        )

        # Step 5: Call Claude API
        response = await anthropic_service.send_message(
            messages=messages,
            system_prompt=session.system_prompt,
            model=session.model,
            temperature=session.temperature,
            max_tokens=session.max_tokens,
        )

        # Step 6: Update conversation context
        session.add_exchange(user_message, response["content"])

        # Step 7: Store new messages as memories (happens in route layer with DB)
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
                }
                for m in new_memories
            ],
            "total_memories_in_context": len(session.session_memories),
        }

    def close_session(self, conversation_id: str):
        """Remove a session from active sessions."""
        if conversation_id in self._sessions:
            del self._sessions[conversation_id]


# Singleton instance
session_manager = SessionManager()
