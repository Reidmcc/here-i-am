from typing import Dict, List, Set, Optional, Any, AsyncIterator, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Conversation, Message, MessageRole, ConversationType, ConversationEntity
from app.services import memory_service, llm_service
from app.config import settings

logger = logging.getLogger(__name__)


def _build_memory_query(
    conversation_context: List[Dict[str, str]],
    current_message: Optional[str],
) -> str:
    """
    Build the query text for memory similarity search.

    Combines the most recent AI response (if any) with the current human message
    to provide better context for memory retrieval.

    Args:
        conversation_context: The conversation history
        current_message: The current human message (can be None for continuations)

    Returns:
        Combined query string for memory search
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
            return last_assistant_content
        # Fallback to last user message if no assistant message
        for msg in reversed(conversation_context):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""

    # Combine with current message for better semantic matching
    if last_assistant_content:
        return f"{last_assistant_content}\n\n{current_message}"
    else:
        return current_message


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

        # Determine entity_id and model for the session
        entity_id = responding_entity_id if responding_entity_id else conversation.entity_id
        model = conversation.llm_model_used

        # For multi-entity conversations with a responding entity, use that entity's model
        if responding_entity_id:
            entity = settings.get_entity_by_index(responding_entity_id)
            if entity:
                model = entity.default_model or settings.get_default_model_for_provider(entity.llm_provider)

        # Create session with conversation settings
        session = self.create_session(
            conversation_id=conversation_id,
            model=model,
            system_prompt=conversation.system_prompt_used,
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

            # Build query using both recent AI response and current human message
            memory_query = _build_memory_query(
                session.conversation_context,
                user_message,
            )

            # Use higher limit for first retrieval in a conversation
            is_first_retrieval = len(session.retrieved_ids) == 0
            top_k = settings.initial_retrieval_top_k if is_first_retrieval else settings.retrieval_top_k

            # Fetch more candidates than needed for significance-based re-ranking
            fetch_k = top_k * settings.retrieval_candidate_multiplier

            # Exclude memories already in context (not all retrieved - allows trimmed ones to return)
            candidates = await memory_service.search_memories(
                query=memory_query,
                top_k=fetch_k,
                exclude_conversation_id=session.conversation_id,
                exclude_ids=session.in_context_ids,
                entity_id=session.entity_id,
            )

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
                    })
                except Exception as e:
                    logger.error(f"[MEMORY] Error processing candidate {candidate.get('id', 'unknown')}: {e}")
                    continue

            # Re-rank by combined score and keep top_k
            enriched_candidates.sort(key=lambda x: x["combined_score"], reverse=True)
            top_candidates = enriched_candidates[:top_k]

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
                    logger.info(f"[MEMORY]   [{retrieval_type}] combined={mem.combined_score:.3f} similarity={mem.score:.3f} significance={mem.significance:.3f} times_retrieved={mem.times_retrieved} age_days={mem.days_since_creation:.1f} recency_days={recency_str}")
            else:
                logger.info(f"[MEMORY] No new memories retrieved (total in context: {len(session.in_context_ids)})")

            # Log candidates that were not selected after re-ranking
            unselected_candidates = enriched_candidates[top_k:]
            if unselected_candidates:
                logger.info(f"[MEMORY] {len(unselected_candidates)} candidates not selected after re-ranking:")
                for item in unselected_candidates:
                    recency_str = f"{item['days_since_retrieval']:.1f}" if item['days_since_retrieval'] >= 0 else "never"
                    logger.info(f"[MEMORY]   [NOT SELECTED] combined={item['combined_score']:.3f} similarity={item['candidate']['score']:.3f} significance={item['significance']:.3f} times_retrieved={item['mem_data']['times_retrieved']} age_days={item['days_since_creation']:.1f} recency_days={recency_str}")
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
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Process a user message through the full pipeline with streaming response.

        This performs memory retrieval first, then streams the LLM response.

        If user_message is None (multi-entity continuation), the entity responds
        based on existing conversation context without a new human message.

        Yields events:
        - {"type": "memories", "new_memories": [...], "total_in_context": int}
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "done", "content": str, "model": str, "usage": dict, "stop_reason": str}
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

            # Build query using both recent AI response and current human message
            memory_query = _build_memory_query(
                session.conversation_context,
                user_message,
            )

            # Use higher limit for first retrieval in a conversation
            is_first_retrieval = len(session.retrieved_ids) == 0
            top_k = settings.initial_retrieval_top_k if is_first_retrieval else settings.retrieval_top_k

            # Fetch more candidates than needed for significance-based re-ranking
            fetch_k = top_k * settings.retrieval_candidate_multiplier

            # Exclude memories already in context (not all retrieved - allows trimmed ones to return)
            candidates = await memory_service.search_memories(
                query=memory_query,
                top_k=fetch_k,
                exclude_conversation_id=session.conversation_id,
                exclude_ids=session.in_context_ids,
                entity_id=session.entity_id,
            )

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
                    })
                except Exception as e:
                    logger.error(f"[MEMORY] Error processing candidate {candidate.get('id', 'unknown')}: {e}")
                    continue

            # Re-rank by combined score and keep top_k
            enriched_candidates.sort(key=lambda x: x["combined_score"], reverse=True)
            top_candidates = enriched_candidates[:top_k]

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
                    logger.info(f"[MEMORY]   [{retrieval_type}] combined={mem.combined_score:.3f} similarity={mem.score:.3f} significance={mem.significance:.3f} times_retrieved={mem.times_retrieved} age_days={mem.days_since_creation:.1f} recency_days={recency_str}")
            else:
                logger.info(f"[MEMORY] No new memories retrieved (total in context: {len(session.in_context_ids)})")

            # Log candidates that were not selected after re-ranking
            unselected_candidates = enriched_candidates[top_k:]
            if unselected_candidates:
                logger.info(f"[MEMORY] {len(unselected_candidates)} candidates not selected after re-ranking:")
                for item in unselected_candidates:
                    recency_str = f"{item['days_since_retrieval']:.1f}" if item['days_since_retrieval'] >= 0 else "never"
                    logger.info(f"[MEMORY]   [NOT SELECTED] combined={item['combined_score']:.3f} similarity={item['candidate']['score']:.3f} significance={item['significance']:.3f} times_retrieved={item['mem_data']['times_retrieved']} age_days={item['days_since_creation']:.1f} recency_days={recency_str}")
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
        )

        # Step 6: Stream LLM response with caching enabled
        full_content = ""
        async for event in llm_service.send_message_stream(
            messages=messages,
            model=session.model,
            system_prompt=session.system_prompt,
            temperature=session.temperature,
            max_tokens=session.max_tokens,
            enable_caching=True,
            verbosity=session.verbosity,
        ):
            if event["type"] == "token":
                full_content += event["content"]
            elif event["type"] == "done":
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
            yield event

    def close_session(self, conversation_id: str):
        """Remove a session from active sessions."""
        if conversation_id in self._sessions:
            del self._sessions[conversation_id]


# Singleton instance
session_manager = SessionManager()
