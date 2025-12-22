from typing import Dict, List, Set, Optional, Any, AsyncIterator, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Conversation, Message, MessageRole, ConversationType, ConversationEntity
from app.services import memory_service, llm_service
from app.services.tool_service import tool_service, ToolResult
from app.config import settings

logger = logging.getLogger(__name__)


def _build_memory_queries(
    conversation_context: List[Dict[str, str]],
    current_message: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Build separate query texts for memory similarity search.

    Returns separate queries for the user message and the most recent AI response,
    allowing independent retrieval from each that can then be combined.

    Args:
        conversation_context: The conversation history
        current_message: The current human message (can be None for continuations)

    Returns:
        Tuple of (user_query, assistant_query) - either can be None if not available
    """
    # Find the most recent assistant message
    last_assistant_content = None
    for msg in reversed(conversation_context):
        if msg.get("role") == "assistant":
            last_assistant_content = msg.get("content", "")
            break

    # Handle continuation (no current message) - use last assistant message only
    if not current_message:
        if last_assistant_content:
            return (None, last_assistant_content)
        # Fallback to last user message if no assistant message
        for msg in reversed(conversation_context):
            if msg.get("role") == "user":
                return (msg.get("content", ""), None)
        return (None, None)

    # Return both queries separately
    return (current_message, last_assistant_content)


def _calculate_significance(
    times_retrieved: int,
    created_at: Optional[datetime],
    last_retrieved_at: Optional[datetime],
) -> float:
    """
    Calculate memory significance based on retrieval patterns.

    significance = times_retrieved * recency_factor * half_life_modifier

    Where:
    - recency_factor boosts recently-retrieved memories
    - half_life_modifier decays significance based on memory age
    """
    now = datetime.utcnow()

    # Handle string dates from database
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    if isinstance(last_retrieved_at, str):
        last_retrieved_at = datetime.fromisoformat(last_retrieved_at)

    # Half-life modifier - older memories decay in significance
    # Starts at 1.0 and halves every significance_half_life_days
    half_life_modifier = 1.0
    if created_at:
        days_since_creation = (now - created_at).days
        half_life_modifier = 0.5 ** (days_since_creation / settings.significance_half_life_days)

    # Recency factor - boosts recently retrieved memories
    recency_factor = 1.0
    if last_retrieved_at:
        days_since_retrieval = (now - last_retrieved_at).days
        if days_since_retrieval > 0:
            recency_factor = 1.0 + min(1.0 / days_since_retrieval, settings.recency_boost_strength)
        else:
            recency_factor = 1.0 + settings.recency_boost_strength

    significance = times_retrieved * recency_factor * half_life_modifier
    return max(significance, settings.significance_floor)


def _ensure_role_balance(
    enriched_candidates: List[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """
    Ensure the selected memories include at least one assistant and one human message.

    If all selected memories are from one role (all human or all assistant),
    replace the lowest scoring one with the highest scoring message of the
    other role (if any exist in the candidate pool).

    Args:
        enriched_candidates: List of candidates sorted by combined_score descending,
                            each containing {"mem_data": {"role": ...}, ...}
        top_k: Number of memories to select

    Returns:
        List of selected candidates with role balance ensured
    """
    if not enriched_candidates or top_k <= 0:
        return []

    # Start with top candidates
    top_candidates = list(enriched_candidates[:top_k])  # Make a copy

    if len(top_candidates) < 2:
        # Can't balance with less than 2 candidates
        return top_candidates

    # Count human and assistant roles in selection
    human_count = sum(1 for item in top_candidates if item["mem_data"]["role"] == "human")
    assistant_count = sum(1 for item in top_candidates if item["mem_data"]["role"] == "assistant")

    # Check if we need to rebalance
    # Only rebalance if ALL are one role (human or assistant)
    if human_count > 0 and assistant_count > 0:
        # Already have both roles
        return top_candidates

    # Determine which role we need
    if human_count > 0 and assistant_count == 0:
        needed_role = "assistant"
    elif assistant_count > 0 and human_count == 0:
        needed_role = "human"
    else:
        # Neither human nor assistant in selection (edge case - all other roles)
        # Return as-is
        return top_candidates

    # Find highest scoring candidate with the needed role from the FULL pool
    replacement = None
    for item in enriched_candidates:
        if item["mem_data"]["role"] == needed_role:
            replacement = item
            break  # First match is highest scoring since list is sorted

    if replacement is None:
        # No candidates with the needed role exist in the pool
        logger.info(f"[MEMORY] Role balance: needed {needed_role} but none found in candidate pool")
        return top_candidates

    # Check if replacement is already in selection (shouldn't happen given above logic)
    replacement_id = replacement["mem_data"]["id"]
    if any(item["mem_data"]["id"] == replacement_id for item in top_candidates):
        return top_candidates

    # Replace the lowest scoring candidate (last in the sorted list)
    replaced_id = top_candidates[-1]["mem_data"]["id"][:8]
    replacement_score = replacement["combined_score"]
    logger.info(f"[MEMORY] Role balance: replacing {replaced_id}... with {needed_role} message (score={replacement_score:.3f})")
    top_candidates[-1] = replacement

    return top_candidates


def _build_memory_block_text(
    memories: List[Dict[str, Any]],
    conversation_start_date: Optional[datetime] = None,
) -> str:
    """
    Build the memory block text for token counting purposes.

    This matches the format used in anthropic_service.build_messages_with_memories
    where memories are placed after conversation history.
    """
    if not memories:
        return ""

    memory_block = "[MEMORIES FROM PREVIOUS CONVERSATIONS]\n\n"
    for mem in memories:
        memory_block += f"Memory (from {mem['created_at']}):\n"
        memory_block += f'"{mem["content"]}"\n\n'
    memory_block += "[/MEMORIES]"

    return memory_block


@dataclass
class MemoryEntry:
    """A memory retrieved during a session."""
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: str
    times_retrieved: int
    score: float = 0.0  # Similarity score from vector search
    significance: float = 0.0  # Significance score based on retrieval patterns
    combined_score: float = 0.0  # Combined score used for ranking
    days_since_creation: float = 0.0  # Age of the memory in days
    days_since_retrieval: float = 0.0  # Days since last retrieval (None if never retrieved)
    source: str = "unknown"  # Which query retrieved this memory: "user", "assistant", or "both"


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
    verbosity: Optional[str] = None  # Verbosity level for gpt-5.1 models (low, medium, high)

    # Multi-entity conversation support
    is_multi_entity: bool = False  # True if this is a multi-entity conversation
    entity_labels: Dict[str, str] = field(default_factory=dict)  # entity_id -> label mapping
    responding_entity_label: Optional[str] = None  # Label of the entity receiving this context

    # Custom display name for the user/researcher (used in role labels)
    user_display_name: Optional[str] = None

    # The actual back-and-forth
    conversation_context: List[Dict[str, str]] = field(default_factory=list)

    # Retrieved memories, keyed by ID
    session_memories: Dict[str, MemoryEntry] = field(default_factory=dict)

    # All IDs that have had retrieval count updated in this conversation (never remove)
    retrieved_ids: Set[str] = field(default_factory=set)

    # IDs currently in the memory block (can be trimmed and restored)
    in_context_ids: Set[str] = field(default_factory=set)

    # Cache tracking for conversation history (single breakpoint)
    # Memories are placed after the cache breakpoint so they don't invalidate cache
    last_cached_context_length: int = 0  # Frozen history length for cache stability

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

    def add_exchange(self, human_message: Optional[str], assistant_response: str):
        """Add a human/assistant exchange to the conversation context.

        If human_message is None (continuation), only the assistant response is added.
        For multi-entity conversations, messages are labeled with participant names.
        """
        if human_message:
            if self.is_multi_entity:
                labeled_content = f"[Human]: {human_message}"
                self.conversation_context.append({"role": "user", "content": labeled_content})
            else:
                self.conversation_context.append({"role": "user", "content": human_message})

        if self.is_multi_entity and self.responding_entity_label:
            labeled_content = f"[{self.responding_entity_label}]: {assistant_response}"
            self.conversation_context.append({"role": "assistant", "content": labeled_content})
        else:
            self.conversation_context.append({"role": "assistant", "content": assistant_response})

    def get_cache_aware_content(self) -> Dict[str, Any]:
        """
        Get context split into cached vs new portions for cache hit optimization.

        Single-breakpoint caching strategy:
        - Conversation history is cached (frozen portion)
        - Memories are placed after conversation history (don't affect cache hits)

        Cache hits occur when cached conversation history is identical to previous call.
        Periodic consolidation moves new_context into cached_context.
        """
        # Split context into cached (frozen) vs new
        cached_context = self.conversation_context[:self.last_cached_context_length]
        new_context = self.conversation_context[self.last_cached_context_length:]

        return {
            "cached_context": cached_context,
            "new_context": new_context,
        }

    def should_consolidate_cache(self, count_tokens_fn) -> bool:
        """
        Determine if we should consolidate (grow) the cached conversation history.

        Consolidation causes a cache MISS but creates a larger cache for future hits.
        With the new structure, only conversation history is cached (memories are
        placed after the cache breakpoint and don't affect cache hits).

        We consolidate when:
        1. Cached history is too small to actually cache (< 1024 tokens)
        2. New history >= 2048 tokens (balance between cache hits and prefix growth)
        """
        # Check conversation context
        if not self.conversation_context:
            return False

        cached_context = self.conversation_context[:self.last_cached_context_length]
        new_context = self.conversation_context[self.last_cached_context_length:]

        if not new_context:
            return False

        # Calculate tokens in cached context
        cached_tokens = 0
        if cached_context:
            cached_text = "\n".join(f"{m['role']}: {m['content']}" for m in cached_context)
            cached_tokens = count_tokens_fn(cached_text)

            # If cached context is too small to be cached (< 1024 tokens), grow it
            if cached_tokens < 1024:
                logger.info(f"[CACHE] Consolidation check: cached_tokens={cached_tokens} < 1024, will consolidate")
                return True

        # Calculate tokens in new context
        new_text = "\n".join(f"{m['role']}: {m['content']}" for m in new_context)
        new_tokens = count_tokens_fn(new_text)

        # Consolidate when new history reaches threshold (balance cache hits vs prefix growth)
        will_consolidate = new_tokens >= 2048
        logger.info(f"[CACHE] Consolidation check: cached_history={len(cached_context)} msgs/{cached_tokens} tokens, new_history={len(new_context)} msgs/{new_tokens} tokens, threshold=2048, will_consolidate={will_consolidate}")

        return will_consolidate

    def update_cache_state(self, cached_context_length: int):
        """
        Update cache tracking after an API call.

        With the new structure, only conversation history is cached (memories are
        placed after the cache breakpoint).

        Args:
            cached_context_length: Number of messages in the cached history block
        """
        old_ctx_len = self.last_cached_context_length
        self.last_cached_context_length = cached_context_length

        # Log if cache state changed
        if cached_context_length != old_ctx_len:
            logger.info(f"[CACHE] Cache state updated: history {old_ctx_len}->{cached_context_length} msgs")

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
        db: AsyncSession,
        responding_entity_id: Optional[str] = None,
        preserve_context_cache_length: Optional[int] = None,
    ) -> Optional[ConversationSession]:
        """
        Load a session from the database, including conversation history
        and previously retrieved memories.

        Args:
            conversation_id: The conversation to load
            db: Database session
            responding_entity_id: For multi-entity conversations, the entity that will respond.
                                  This determines which entity's model/provider to use.
            preserve_context_cache_length: If provided, use this value for last_cached_context_length
                                           instead of resetting to len(conversation_context).
                                           This preserves cache breakpoint stability across
                                           entity switches in multi-entity conversations.
        """
        # Get conversation
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            return None

        # Check if this is a multi-entity conversation
        is_multi_entity = conversation.conversation_type == ConversationType.MULTI_ENTITY

        # Build entity_labels mapping for multi-entity conversations
        entity_labels: Dict[str, str] = {}
        responding_entity_label: Optional[str] = None

        if is_multi_entity:
            # Load participating entities
            result = await db.execute(
                select(ConversationEntity.entity_id)
                .where(ConversationEntity.conversation_id == conversation_id)
                .order_by(ConversationEntity.display_order)
            )
            entity_ids = [row[0] for row in result.fetchall()]

            # Build entity_id -> label mapping
            for eid in entity_ids:
                entity_config = settings.get_entity_by_index(eid)
                if entity_config:
                    entity_labels[eid] = entity_config.label
                else:
                    entity_labels[eid] = eid  # Fallback to ID if no config

            # Get the responding entity's label
            if responding_entity_id and responding_entity_id in entity_labels:
                responding_entity_label = entity_labels[responding_entity_id]
        else:
            # For single-entity conversations, get the entity label from config
            if conversation.entity_id:
                entity_config = settings.get_entity_by_index(conversation.entity_id)
                if entity_config:
                    responding_entity_label = entity_config.label

        # Determine entity_id and model for the session
        entity_id = responding_entity_id if responding_entity_id else conversation.entity_id
        model = conversation.llm_model_used

        # For multi-entity conversations with a responding entity, use that entity's model
        if responding_entity_id:
            entity = settings.get_entity_by_index(responding_entity_id)
            if entity:
                model = entity.default_model or settings.get_default_model_for_provider(entity.llm_provider)

        # Determine system prompt: use entity-specific prompt if available, else fallback
        system_prompt = conversation.system_prompt_used
        if conversation.entity_system_prompts:
            # Check for entity-specific system prompt
            # For multi-entity: use responding_entity_id
            # For single-entity: use conversation.entity_id
            prompt_entity_id = responding_entity_id or conversation.entity_id
            if prompt_entity_id:
                entity_prompt = conversation.entity_system_prompts.get(prompt_entity_id)
                if entity_prompt is not None:
                    system_prompt = entity_prompt
                    logger.info(f"[SESSION] Using entity-specific system prompt for {prompt_entity_id}")

        # Create session with conversation settings
        session = self.create_session(
            conversation_id=conversation_id,
            model=model,
            system_prompt=system_prompt,
            entity_id=entity_id,
            conversation_start_date=conversation.created_at,
        )

        # Set multi-entity fields
        session.is_multi_entity = is_multi_entity
        session.entity_labels = entity_labels
        session.responding_entity_label = responding_entity_label

        # Load message history
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        messages = result.scalars().all()

        # Count messages by role for debugging
        human_count = sum(1 for m in messages if m.role == MessageRole.HUMAN)
        assistant_count = sum(1 for m in messages if m.role == MessageRole.ASSISTANT)
        other_count = len(messages) - human_count - assistant_count
        logger.info(f"[SESSION] Loading {len(messages)} messages from DB ({human_count} human, {assistant_count} assistant, {other_count} other)")

        for msg in messages:
            if msg.role == MessageRole.HUMAN:
                # For multi-entity conversations, label human messages
                if is_multi_entity:
                    labeled_content = f"[Human]: {msg.content}"
                    session.conversation_context.append({"role": "user", "content": labeled_content})
                else:
                    session.conversation_context.append({"role": "user", "content": msg.content})
            elif msg.role == MessageRole.ASSISTANT:
                # For multi-entity conversations, label assistant messages with speaker entity
                if is_multi_entity and msg.speaker_entity_id:
                    speaker_label = entity_labels.get(msg.speaker_entity_id, msg.speaker_entity_id)
                    labeled_content = f"[{speaker_label}]: {msg.content}"
                    session.conversation_context.append({"role": "assistant", "content": labeled_content})
                else:
                    session.conversation_context.append({"role": "assistant", "content": msg.content})
            else:
                logger.warning(f"[SESSION] Skipping message with unexpected role: {msg.role}")

        # Load already-retrieved memory IDs for deduplication
        # Note: get_retrieved_ids_for_conversation returns string IDs to match Pinecone
        # For multi-entity conversations, filter by the responding entity to maintain isolation
        retrieved_ids = await memory_service.get_retrieved_ids_for_conversation(
            conversation_id, db, entity_id=entity_id if is_multi_entity else None
        )
        session.retrieved_ids = retrieved_ids

        # Load full memory content for already-retrieved memories
        # When loading from DB, all previously retrieved memories start in context
        for mem_id in retrieved_ids:
            mem_data = await memory_service.get_full_memory_content(mem_id, db)
            if mem_data:
                # Use the string ID from mem_data to ensure consistency
                str_id = mem_data["id"]
                session.session_memories[str_id] = MemoryEntry(
                    id=str_id,
                    conversation_id=mem_data["conversation_id"],
                    role=mem_data["role"],
                    content=mem_data["content"],
                    created_at=mem_data["created_at"],
                    times_retrieved=mem_data["times_retrieved"],
                )
                session.in_context_ids.add(str_id)

        # For context cache length: preserve if provided (for multi-entity entity switches),
        # otherwise bootstrap with all existing content
        if preserve_context_cache_length is not None:
            # Preserve the cache breakpoint location for stable cache hits
            # Cap at actual context length to avoid out-of-bounds issues
            session.last_cached_context_length = min(
                preserve_context_cache_length,
                len(session.conversation_context)
            )
            logger.info(f"[CACHE] Preserved context cache length: {session.last_cached_context_length} (requested: {preserve_context_cache_length})")
        else:
            # Bootstrap: treat all existing content as cached
            session.last_cached_context_length = len(session.conversation_context)
            logger.info(f"[CACHE] Bootstrap context cache length: {session.last_cached_context_length}")

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
        2. Filter and deduplicate (also excluding archived conversations)
        3. Update retrieval tracking
        4. Build API request with memories
        5. Call LLM provider API
        6. Update conversation context
        7. Store new messages as memories

        Returns response data including content, usage, and retrieved memories.
        """
        new_memories = []
        truly_new_memory_ids = set()  # Only memories never seen before (for cache stability)

        logger.info(f"[MEMORY] Processing message for conversation {session.conversation_id[:8]}...")

        # Step 1-2: Retrieve, re-rank by significance, and deduplicate memories
        # Validate both that Pinecone is configured AND the entity_id is valid
        if memory_service.is_configured(entity_id=session.entity_id):
            # Get archived conversation IDs to exclude from retrieval
            archived_ids = await memory_service.get_archived_conversation_ids(
                db, entity_id=session.entity_id
            )

            # Build separate queries for user message and AI response
            user_query, assistant_query = _build_memory_queries(
                session.conversation_context,
                user_message,
            )

            # Use higher limit for first retrieval in a conversation
            is_first_retrieval = len(session.retrieved_ids) == 0
            top_k = settings.initial_retrieval_top_k if is_first_retrieval else settings.retrieval_top_k

            # Fetch 10 candidates per query, then combine and re-rank by significance
            fetch_k_per_query = 10

            # Perform separate searches for user message and assistant response
            user_candidates = []
            assistant_candidates = []

            if user_query:
                user_candidates = await memory_service.search_memories(
                    query=user_query,
                    top_k=fetch_k_per_query,
                    exclude_conversation_id=session.conversation_id,
                    exclude_ids=session.in_context_ids,
                    entity_id=session.entity_id,
                )
                logger.info(f"[MEMORY] User query retrieved {len(user_candidates)} candidates")

            if assistant_query:
                assistant_candidates = await memory_service.search_memories(
                    query=assistant_query,
                    top_k=fetch_k_per_query,
                    exclude_conversation_id=session.conversation_id,
                    exclude_ids=session.in_context_ids,
                    entity_id=session.entity_id,
                )
                logger.info(f"[MEMORY] Assistant query retrieved {len(assistant_candidates)} candidates")

            # Combine candidates, tracking source and keeping higher score for duplicates
            candidates_by_id = {}
            user_candidate_ids = set(c["id"] for c in user_candidates)
            assistant_candidate_ids = set(c["id"] for c in assistant_candidates)

            for candidate in user_candidates + assistant_candidates:
                cid = candidate["id"]
                if cid not in candidates_by_id or candidate["score"] > candidates_by_id[cid]["score"]:
                    candidates_by_id[cid] = candidate

            # Determine source for each candidate
            for cid in candidates_by_id:
                in_user = cid in user_candidate_ids
                in_assistant = cid in assistant_candidate_ids
                if in_user and in_assistant:
                    candidates_by_id[cid]["_source"] = "both"
                elif in_user:
                    candidates_by_id[cid]["_source"] = "user"
                else:
                    candidates_by_id[cid]["_source"] = "assistant"

            candidates = list(candidates_by_id.values())
            logger.info(f"[MEMORY] Combined {len(candidates)} unique candidates from both queries")

            # Step 2: Get full content and calculate combined scores for re-ranking
            enriched_candidates = []
            now = datetime.utcnow()
            for candidate in candidates:
                try:
                    # Skip memories from archived conversations
                    if candidate.get("conversation_id") in archived_ids:
                        continue
                    # Get full content from database
                    mem_data = await memory_service.get_full_memory_content(candidate["id"], db)
                    if not mem_data:
                        # Full ID already logged in memory_service, just note we're skipping
                        logger.debug(f"[MEMORY] Skipping orphaned memory {candidate['id'][:8]}...")
                        continue

                    # Calculate significance for re-ranking
                    significance = _calculate_significance(
                        mem_data["times_retrieved"],
                        mem_data["created_at"],
                        mem_data["last_retrieved_at"],
                    )
                    # Combined score: similarity boosted by significance
                    # Memories with higher significance get priority among similar matches
                    combined_score = candidate["score"] * (1 + significance)

                    # Calculate days since creation and last retrieval for logging
                    created_at = mem_data["created_at"]
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at)
                    days_since_creation = (now - created_at).total_seconds() / 86400

                    last_retrieved_at = mem_data["last_retrieved_at"]
                    if last_retrieved_at:
                        if isinstance(last_retrieved_at, str):
                            last_retrieved_at = datetime.fromisoformat(last_retrieved_at)
                        days_since_retrieval = (now - last_retrieved_at).total_seconds() / 86400
                    else:
                        days_since_retrieval = -1  # Never retrieved

                    enriched_candidates.append({
                        "candidate": candidate,
                        "mem_data": mem_data,
                        "significance": significance,
                        "combined_score": combined_score,
                        "days_since_creation": days_since_creation,
                        "days_since_retrieval": days_since_retrieval,
                        "source": candidate.get("_source", "unknown"),
                    })
                except Exception as e:
                    logger.error(f"[MEMORY] Error processing candidate {candidate.get('id', 'unknown')}: {e}")
                    continue

            # Re-rank by combined score and keep top_k with role balance
            enriched_candidates.sort(key=lambda x: x["combined_score"], reverse=True)
            top_candidates = _ensure_role_balance(enriched_candidates, top_k)

            logger.info(f"[MEMORY] Re-ranked {len(enriched_candidates)} candidates by significance, keeping top {len(top_candidates)}")

            # Step 3: Process top candidates
            for item in top_candidates:
                candidate = item["candidate"]
                mem_data = item["mem_data"]

                memory = MemoryEntry(
                    id=mem_data["id"],
                    conversation_id=mem_data["conversation_id"],
                    role=mem_data["role"],
                    content=mem_data["content"],
                    created_at=mem_data["created_at"],
                    times_retrieved=mem_data["times_retrieved"],
                    score=candidate["score"],
                    significance=item["significance"],
                    combined_score=item["combined_score"],
                    days_since_creation=item["days_since_creation"],
                    days_since_retrieval=item["days_since_retrieval"],
                    source=item["source"],
                )

                added, is_new_retrieval = session.add_memory(memory)
                if added:
                    new_memories.append(memory)
                    # Track truly new memories separately for cache stability
                    # Restored memories (trimmed then re-retrieved) should be treated as "old"
                    if is_new_retrieval:
                        truly_new_memory_ids.add(memory.id)
                        # Update retrieval tracking only for truly new retrievals
                        await memory_service.update_retrieval_count(
                            memory.id,
                            session.conversation_id,
                            db,
                            entity_id=session.entity_id,
                        )

            # Log memory retrieval summary
            if new_memories:
                logger.info(f"[MEMORY] Retrieved {len(new_memories)} new memories ({len(truly_new_memory_ids)} first-time retrievals)")
                for mem in new_memories:
                    retrieval_type = "NEW" if mem.id in truly_new_memory_ids else "RESTORED"
                    recency_str = f"{mem.days_since_retrieval:.1f}" if mem.days_since_retrieval >= 0 else "never"
                    logger.info(f"[MEMORY]   [{retrieval_type}] combined={mem.combined_score:.3f} similarity={mem.score:.3f} significance={mem.significance:.3f} times_retrieved={mem.times_retrieved} age_days={mem.days_since_creation:.1f} recency_days={recency_str} source={mem.source}")
            else:
                logger.info(f"[MEMORY] No new memories retrieved (total in context: {len(session.in_context_ids)})")

            # Log candidates that were not selected after re-ranking (show next 5)
            unselected_candidates = enriched_candidates[top_k:top_k + 5]
            if unselected_candidates:
                total_unselected = len(enriched_candidates) - top_k
                logger.info(f"[MEMORY] {total_unselected} candidates not selected after re-ranking (showing next 5):")
                for item in unselected_candidates:
                    recency_str = f"{item['days_since_retrieval']:.1f}" if item['days_since_retrieval'] >= 0 else "never"
                    logger.info(f"[MEMORY]   [NOT SELECTED] combined={item['combined_score']:.3f} similarity={item['candidate']['score']:.3f} significance={item['significance']:.3f} times_retrieved={item['mem_data']['times_retrieved']} age_days={item['days_since_creation']:.1f} recency_days={recency_str} source={item['source']}")
        else:
            # Memory retrieval skipped - log reason
            if not settings.pinecone_api_key:
                logger.info(f"[MEMORY] Memory retrieval skipped: Pinecone not configured (no API key)")
            elif session.entity_id and not settings.get_entity_by_index(session.entity_id):
                logger.warning(f"[MEMORY] Memory retrieval skipped: Invalid entity_id '{session.entity_id}' not found in configuration")
            else:
                logger.info(f"[MEMORY] Memory retrieval skipped: entity_id={session.entity_id}")

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

        # Step 5: Check if we should consolidate (grow) the cached history
        # This causes a cache MISS but creates a larger cache for future hits
        should_consolidate = session.should_consolidate_cache(llm_service.count_tokens)

        # Step 6: Build API messages with conversation-first caching
        # Cache breakpoint: end of cached conversation history
        # Memories are placed after history (don't invalidate cache)
        memories_for_injection = session.get_memories_for_injection()
        cache_content = session.get_cache_aware_content()

        # Debug logging for memory injection
        logger.info(f"[MEMORY] Injecting {len(memories_for_injection)} memories into context (in_context_ids: {len(session.in_context_ids)}, session_memories: {len(session.session_memories)})")
        # Log cached context breakdown by role
        cached_ctx = cache_content['cached_context']
        new_ctx = cache_content['new_context']
        cached_user = sum(1 for m in cached_ctx if m.get('role') == 'user')
        cached_asst = sum(1 for m in cached_ctx if m.get('role') == 'assistant')
        new_user = sum(1 for m in new_ctx if m.get('role') == 'user')
        new_asst = sum(1 for m in new_ctx if m.get('role') == 'assistant')
        logger.info(f"[CACHE] Context: {len(cached_ctx)} cached msgs ({cached_user} user, {cached_asst} assistant), {len(new_ctx)} new msgs ({new_user} user, {new_asst} assistant)")

        messages = llm_service.build_messages_with_memories(
            memories=memories_for_injection,
            conversation_context=session.conversation_context,
            current_message=user_message,
            model=session.model,
            conversation_start_date=session.conversation_start_date,
            enable_caching=True,
            cached_context=cache_content["cached_context"],
            new_context=cache_content["new_context"],
            is_multi_entity=session.is_multi_entity,
            entity_labels=session.entity_labels,
            responding_entity_label=session.responding_entity_label,
            user_display_name=session.user_display_name,
        )

        # Step 7: Call LLM API (routes to appropriate provider based on model)
        response = await llm_service.send_message(
            messages=messages,
            model=session.model,
            system_prompt=session.system_prompt,
            temperature=session.temperature,
            max_tokens=session.max_tokens,
            enable_caching=True,
            verbosity=session.verbosity,
        )

        # Step 8: Update conversation context and cache state
        session.add_exchange(user_message, response["content"])

        # Update cache state for conversation history (memories don't affect cache hits)
        if should_consolidate:
            # Consolidate: grow the cached history (excluding the 2 messages just added)
            new_cached_ctx_len = len(session.conversation_context) - 2
        elif session.last_cached_context_length == 0 and len(session.conversation_context) > 0:
            # Bootstrap: start caching with all current content
            new_cached_ctx_len = len(session.conversation_context)
        else:
            # Keep stable: don't grow the cache (for cache hits)
            new_cached_ctx_len = session.last_cached_context_length

        session.update_cache_state(new_cached_ctx_len)

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
                    "content": m.content[:3000] if len(m.content) > 3000 else m.content,
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
        user_message: Optional[str],
        db: AsyncSession,
        tool_schemas: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Process a user message through the full pipeline with streaming response.

        This performs memory retrieval first, then streams the LLM response.
        If tools are provided and the LLM requests tool use, executes tools and
        loops until a final response is received.

        If user_message is None (multi-entity continuation), the entity responds
        based on existing conversation context without a new human message.

        Yields events:
        - {"type": "memories", "new_memories": [...], "total_in_context": int}
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "tool_start", "tool_name": str, "tool_id": str, "input": dict}
        - {"type": "tool_result", "tool_name": str, "tool_id": str, "content": str, "is_error": bool}
        - {"type": "done", "content": str, "model": str, "usage": dict, "stop_reason": str, "tool_uses": list|None}
        - {"type": "error", "error": str}
        """
        new_memories = []
        truly_new_memory_ids = set()  # Only memories never seen before (for cache stability)

        logger.info(f"[MEMORY] Processing message (stream) for conversation {session.conversation_id[:8]}... entity_id={session.entity_id}, model={session.model}")

        # Step 1-2: Retrieve, re-rank by significance, and deduplicate memories
        # Validate both that Pinecone is configured AND the entity_id is valid
        if memory_service.is_configured(entity_id=session.entity_id):
            # Get archived conversation IDs to exclude from retrieval
            archived_ids = await memory_service.get_archived_conversation_ids(
                db, entity_id=session.entity_id
            )

            # Build separate queries for user message and AI response
            user_query, assistant_query = _build_memory_queries(
                session.conversation_context,
                user_message,
            )

            # Use higher limit for first retrieval in a conversation
            is_first_retrieval = len(session.retrieved_ids) == 0
            top_k = settings.initial_retrieval_top_k if is_first_retrieval else settings.retrieval_top_k

            # Fetch 10 candidates per query, then combine and re-rank by significance
            fetch_k_per_query = 10

            # Perform separate searches for user message and assistant response
            user_candidates = []
            assistant_candidates = []

            if user_query:
                user_candidates = await memory_service.search_memories(
                    query=user_query,
                    top_k=fetch_k_per_query,
                    exclude_conversation_id=session.conversation_id,
                    exclude_ids=session.in_context_ids,
                    entity_id=session.entity_id,
                )
                logger.info(f"[MEMORY] User query retrieved {len(user_candidates)} candidates")

            if assistant_query:
                assistant_candidates = await memory_service.search_memories(
                    query=assistant_query,
                    top_k=fetch_k_per_query,
                    exclude_conversation_id=session.conversation_id,
                    exclude_ids=session.in_context_ids,
                    entity_id=session.entity_id,
                )
                logger.info(f"[MEMORY] Assistant query retrieved {len(assistant_candidates)} candidates")

            # Combine candidates, tracking source and keeping higher score for duplicates
            candidates_by_id = {}
            user_candidate_ids = set(c["id"] for c in user_candidates)
            assistant_candidate_ids = set(c["id"] for c in assistant_candidates)

            for candidate in user_candidates + assistant_candidates:
                cid = candidate["id"]
                if cid not in candidates_by_id or candidate["score"] > candidates_by_id[cid]["score"]:
                    candidates_by_id[cid] = candidate

            # Determine source for each candidate
            for cid in candidates_by_id:
                in_user = cid in user_candidate_ids
                in_assistant = cid in assistant_candidate_ids
                if in_user and in_assistant:
                    candidates_by_id[cid]["_source"] = "both"
                elif in_user:
                    candidates_by_id[cid]["_source"] = "user"
                else:
                    candidates_by_id[cid]["_source"] = "assistant"

            candidates = list(candidates_by_id.values())
            logger.info(f"[MEMORY] Combined {len(candidates)} unique candidates from both queries")

            # Step 2: Get full content and calculate combined scores for re-ranking
            enriched_candidates = []
            now = datetime.utcnow()
            for candidate in candidates:
                try:
                    # Skip memories from archived conversations
                    if candidate.get("conversation_id") in archived_ids:
                        continue
                    mem_data = await memory_service.get_full_memory_content(candidate["id"], db)
                    if not mem_data:
                        # Full ID already logged in memory_service, just note we're skipping
                        logger.debug(f"[MEMORY] Skipping orphaned memory {candidate['id'][:8]}...")
                        continue

                    # Calculate significance for re-ranking
                    significance = _calculate_significance(
                        mem_data["times_retrieved"],
                        mem_data["created_at"],
                        mem_data["last_retrieved_at"],
                    )
                    # Combined score: similarity boosted by significance
                    combined_score = candidate["score"] * (1 + significance)

                    # Calculate days since creation and last retrieval for logging
                    created_at = mem_data["created_at"]
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at)
                    days_since_creation = (now - created_at).total_seconds() / 86400

                    last_retrieved_at = mem_data["last_retrieved_at"]
                    if last_retrieved_at:
                        if isinstance(last_retrieved_at, str):
                            last_retrieved_at = datetime.fromisoformat(last_retrieved_at)
                        days_since_retrieval = (now - last_retrieved_at).total_seconds() / 86400
                    else:
                        days_since_retrieval = -1  # Never retrieved

                    enriched_candidates.append({
                        "candidate": candidate,
                        "mem_data": mem_data,
                        "significance": significance,
                        "combined_score": combined_score,
                        "days_since_creation": days_since_creation,
                        "days_since_retrieval": days_since_retrieval,
                        "source": candidate.get("_source", "unknown"),
                    })
                except Exception as e:
                    logger.error(f"[MEMORY] Error processing candidate {candidate.get('id', 'unknown')}: {e}")
                    continue

            # Re-rank by combined score and keep top_k with role balance
            enriched_candidates.sort(key=lambda x: x["combined_score"], reverse=True)
            top_candidates = _ensure_role_balance(enriched_candidates, top_k)

            logger.info(f"[MEMORY] Re-ranked {len(enriched_candidates)} candidates by significance, keeping top {len(top_candidates)}")

            # Step 3: Process top candidates
            for item in top_candidates:
                candidate = item["candidate"]
                mem_data = item["mem_data"]

                memory = MemoryEntry(
                    id=mem_data["id"],
                    conversation_id=mem_data["conversation_id"],
                    role=mem_data["role"],
                    content=mem_data["content"],
                    created_at=mem_data["created_at"],
                    times_retrieved=mem_data["times_retrieved"],
                    score=candidate["score"],
                    significance=item["significance"],
                    combined_score=item["combined_score"],
                    days_since_creation=item["days_since_creation"],
                    days_since_retrieval=item["days_since_retrieval"],
                    source=item["source"],
                )

                added, is_new_retrieval = session.add_memory(memory)
                if added:
                    new_memories.append(memory)
                    # Track truly new memories separately for cache stability
                    # Restored memories (trimmed then re-retrieved) should be treated as "old"
                    if is_new_retrieval:
                        truly_new_memory_ids.add(memory.id)
                        # Only update retrieval count for truly new retrievals
                        await memory_service.update_retrieval_count(
                            memory.id,
                            session.conversation_id,
                            db,
                            entity_id=session.entity_id,
                        )

            # Log memory retrieval summary
            if new_memories:
                logger.info(f"[MEMORY] Retrieved {len(new_memories)} new memories ({len(truly_new_memory_ids)} first-time retrievals)")
                for mem in new_memories:
                    retrieval_type = "NEW" if mem.id in truly_new_memory_ids else "RESTORED"
                    recency_str = f"{mem.days_since_retrieval:.1f}" if mem.days_since_retrieval >= 0 else "never"
                    logger.info(f"[MEMORY]   [{retrieval_type}] combined={mem.combined_score:.3f} similarity={mem.score:.3f} significance={mem.significance:.3f} times_retrieved={mem.times_retrieved} age_days={mem.days_since_creation:.1f} recency_days={recency_str} source={mem.source}")
            else:
                logger.info(f"[MEMORY] No new memories retrieved (total in context: {len(session.in_context_ids)})")

            # Log candidates that were not selected after re-ranking (show next 5)
            unselected_candidates = enriched_candidates[top_k:top_k + 5]
            if unselected_candidates:
                total_unselected = len(enriched_candidates) - top_k
                logger.info(f"[MEMORY] {total_unselected} candidates not selected after re-ranking (showing next 5):")
                for item in unselected_candidates:
                    recency_str = f"{item['days_since_retrieval']:.1f}" if item['days_since_retrieval'] >= 0 else "never"
                    logger.info(f"[MEMORY]   [NOT SELECTED] combined={item['combined_score']:.3f} similarity={item['candidate']['score']:.3f} significance={item['significance']:.3f} times_retrieved={item['mem_data']['times_retrieved']} age_days={item['days_since_creation']:.1f} recency_days={recency_str} source={item['source']}")
        else:
            # Memory retrieval skipped - log reason
            if not settings.pinecone_api_key:
                logger.info(f"[MEMORY] Memory retrieval skipped: Pinecone not configured (no API key)")
            elif session.entity_id and not settings.get_entity_by_index(session.entity_id):
                logger.warning(f"[MEMORY] Memory retrieval skipped: Invalid entity_id '{session.entity_id}' not found in configuration")
            else:
                logger.info(f"[MEMORY] Memory retrieval skipped: entity_id={session.entity_id}")

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
        # Include entity_id for multi-entity conversations so frontend can show per-entity memories
        yield {
            "type": "memories",
            "entity_id": session.entity_id if session.is_multi_entity else None,
            "entity_label": session.responding_entity_label if session.is_multi_entity else None,
            "new_memories": [
                {
                    "id": m.id,
                    "content": m.content[:3000] if len(m.content) > 3000 else m.content,
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

        # Step 4: Check if we should consolidate (grow) the cached history
        should_consolidate = session.should_consolidate_cache(llm_service.count_tokens)

        # Step 5: Build API messages with conversation-first caching
        # Cache breakpoint: end of cached conversation history
        # Memories are placed after history (don't invalidate cache)
        memories_for_injection = session.get_memories_for_injection()
        cache_content = session.get_cache_aware_content()

        # Debug logging for memory injection
        logger.info(f"[MEMORY] Injecting {len(memories_for_injection)} memories into context (in_context_ids: {len(session.in_context_ids)}, session_memories: {len(session.session_memories)})")
        # Log cached context breakdown by role
        cached_ctx = cache_content['cached_context']
        new_ctx = cache_content['new_context']
        cached_user = sum(1 for m in cached_ctx if m.get('role') == 'user')
        cached_asst = sum(1 for m in cached_ctx if m.get('role') == 'assistant')
        new_user = sum(1 for m in new_ctx if m.get('role') == 'user')
        new_asst = sum(1 for m in new_ctx if m.get('role') == 'assistant')
        logger.info(f"[CACHE] Context: {len(cached_ctx)} cached msgs ({cached_user} user, {cached_asst} assistant), {len(new_ctx)} new msgs ({new_user} user, {new_asst} assistant)")

        messages = llm_service.build_messages_with_memories(
            memories=memories_for_injection,
            conversation_context=session.conversation_context,
            current_message=user_message,
            model=session.model,
            conversation_start_date=session.conversation_start_date,
            enable_caching=True,
            cached_context=cache_content["cached_context"],
            new_context=cache_content["new_context"],
            is_multi_entity=session.is_multi_entity,
            entity_labels=session.entity_labels,
            responding_entity_label=session.responding_entity_label,
            user_display_name=session.user_display_name,
        )

        # Step 6: Stream LLM response with caching enabled
        # This includes a tool use loop if tools are provided
        full_content = ""
        accumulated_tool_uses = []  # Track all tool uses across iterations
        iteration = 0
        max_iterations = settings.tool_use_max_iterations

        # Working copy of messages for tool loop
        working_messages = list(messages)

        while iteration < max_iterations:
            iteration += 1
            iteration_content = ""
            iteration_tool_use = None
            iteration_content_blocks = []
            stop_reason = None

            async for event in llm_service.send_message_stream(
                messages=working_messages,
                model=session.model,
                system_prompt=session.system_prompt,
                temperature=session.temperature,
                max_tokens=session.max_tokens,
                enable_caching=True,
                verbosity=session.verbosity,
                tools=tool_schemas,
            ):
                if event["type"] == "token":
                    iteration_content += event["content"]
                    yield event
                elif event["type"] == "tool_use_start":
                    # Yield tool start event to frontend
                    yield {
                        "type": "tool_start",
                        "tool_name": event["tool_use"]["name"],
                        "tool_id": event["tool_use"]["id"],
                        "input": {},  # Input comes later when block completes
                    }
                elif event["type"] == "done":
                    stop_reason = event.get("stop_reason")
                    iteration_content_blocks = event.get("content_blocks", [])
                    iteration_tool_use = event.get("tool_use")

                    # If no tool use, this is the final response
                    if stop_reason != "tool_use" or not iteration_tool_use:
                        full_content += iteration_content

                        # Update conversation context and cache state
                        session.add_exchange(user_message, full_content)

                        # Update cache state for conversation history (memories don't affect cache hits)
                        if should_consolidate:
                            new_cached_ctx_len = len(session.conversation_context) - 2
                        elif session.last_cached_context_length == 0 and len(session.conversation_context) > 0:
                            # Bootstrap: start caching with all current content
                            new_cached_ctx_len = len(session.conversation_context)
                        else:
                            # Keep stable for cache hits
                            new_cached_ctx_len = session.last_cached_context_length

                        session.update_cache_state(new_cached_ctx_len)

                        # Add tool_uses to done event if any tools were used
                        final_event = dict(event)
                        if accumulated_tool_uses:
                            final_event["tool_uses"] = accumulated_tool_uses
                        yield final_event
                        return
                elif event["type"] == "error":
                    yield event
                    return
                elif event["type"] == "start":
                    # Only yield start on first iteration
                    if iteration == 1:
                        yield event

            # If we get here, we have tool_use to process
            if iteration_tool_use:
                logger.info(f"[TOOLS] Iteration {iteration}: Processing {len(iteration_tool_use)} tool calls")

                # Execute tools and collect results
                tool_results = []
                for tool_call in iteration_tool_use:
                    tool_name = tool_call["name"]
                    tool_id = tool_call["id"]
                    tool_input = tool_call.get("input", {})

                    # Yield updated tool_start with actual input
                    yield {
                        "type": "tool_start",
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "input": tool_input,
                    }

                    # Execute the tool
                    result = await tool_service.execute_tool(
                        tool_use_id=tool_id,
                        tool_name=tool_name,
                        tool_input=tool_input,
                    )

                    # Yield tool result to frontend
                    yield {
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "content": result.content,
                        "is_error": result.is_error,
                    }

                    tool_results.append(result)

                    # Track for final response
                    accumulated_tool_uses.append({
                        "call": {
                            "name": tool_name,
                            "id": tool_id,
                            "input": tool_input,
                        },
                        "result": {
                            "content": result.content,
                            "is_error": result.is_error,
                        },
                    })

                # Append assistant message with tool use content blocks
                working_messages.append({
                    "role": "assistant",
                    "content": iteration_content_blocks,
                })

                # Append user message with tool results
                tool_result_content = []
                for result in tool_results:
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": result.tool_use_id,
                        "content": result.content,
                        "is_error": result.is_error,
                    })

                working_messages.append({
                    "role": "user",
                    "content": tool_result_content,
                })

                # Accumulate any text content from this iteration
                full_content += iteration_content

        # If we've exhausted iterations, yield what we have
        logger.warning(f"[TOOLS] Max iterations ({max_iterations}) reached")
        session.add_exchange(user_message, full_content)

        yield {
            "type": "done",
            "content": full_content,
            "model": session.model,
            "usage": {},
            "stop_reason": "max_iterations",
            "tool_uses": accumulated_tool_uses if accumulated_tool_uses else None,
        }

    def close_session(self, conversation_id: str):
        """Remove a session from active sessions."""
        if conversation_id in self._sessions:
            del self._sessions[conversation_id]


# Singleton instance
session_manager = SessionManager()
