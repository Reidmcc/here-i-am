"""
Conversation Session Module

Contains the ConversationSession and MemoryEntry dataclasses that represent
the runtime state of an active conversation.

Split from session_manager.py to reduce file size and improve maintainability.
"""

from typing import Dict, List, Set, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import logging

from app.config import settings
from app.services.session_helpers import (
    get_message_content_text,
    build_memory_block_text,
)
from app.services.memory_context import (
    MemoryContextTracker,
    format_memory_as_context_message,
    find_memory_insertion_point,
)

logger = logging.getLogger(__name__)


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

    Maintains the conversation context (message history) and tracks memories
    that have been retrieved during this conversation.

    Memory System (Transitioning):
    The memory system is being reworked from a "memory block" approach to
    "memory-in-context" approach. During transition, both systems coexist:
    
    Legacy (memory block):
    - in_context_ids: Set of memory IDs in the memory block
    - get_memories_for_injection(): Returns memories for block construction
    - trim_memories_to_limit(): Trims memory block to token limit
    
    New (memory-in-context):
    - memory_tracker: MemoryContextTracker for position-based tracking
    - insert_memory_into_context(): Inserts memory as context message
    - Memory messages have is_memory=True flag
    """
    conversation_id: str
    model: str = field(default_factory=lambda: settings.default_model)
    temperature: float = field(default_factory=lambda: settings.default_temperature)
    max_tokens: int = field(default_factory=lambda: settings.default_max_tokens)
    system_prompt: Optional[str] = None
    entity_id: Optional[str] = None  # Pinecone index name for this conversation's entity
    conversation_start_date: Optional[datetime] = None  # When the conversation was created
    verbosity: Optional[str] = None  # Verbosity level for gpt-5.1 models (low, medium, high)
    provider_hint: Optional[str] = None  # LLM provider from entity config (e.g., "minimax")

    # Multi-entity conversation support
    is_multi_entity: bool = False  # True if this is a multi-entity conversation
    entity_labels: Dict[str, str] = field(default_factory=dict)  # entity_id -> label mapping
    responding_entity_label: Optional[str] = None  # Label of the entity receiving this context

    # Custom display name for the user/researcher (used in role labels)
    user_display_name: Optional[str] = None

    # The actual back-and-forth (includes memory messages when using new system)
    conversation_context: List[Dict[str, Any]] = field(default_factory=list)

    # Retrieved memories, keyed by ID
    session_memories: Dict[str, MemoryEntry] = field(default_factory=dict)

    # All IDs that have had retrieval count updated in this conversation (never remove)
    retrieved_ids: Set[str] = field(default_factory=set)

    # ===== Legacy memory block tracking (to be deprecated) =====
    # IDs currently in the memory block (can be trimmed and restored)
    in_context_ids: Set[str] = field(default_factory=set)

    # ===== New memory-in-context tracking =====
    # Position-based memory tracker for the new system
    memory_tracker: MemoryContextTracker = field(default_factory=MemoryContextTracker)
    
    # Flag to enable new memory system (set to True to use memory-in-context)
    use_memory_in_context: bool = False

    # Cache tracking for conversation history (single breakpoint)
    last_cached_context_length: int = 0  # Frozen history length for cache stability

    # ===== Legacy memory block methods (to be deprecated) =====
    
    def add_memory(self, memory: MemoryEntry) -> Tuple[bool, bool]:
        """
        Add a memory to the session (legacy memory block system).

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
        """Get memories formatted for API injection (legacy memory block system)."""
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

    def trim_memories_to_limit(
        self,
        max_tokens: int,
        count_tokens_fn: Callable[[str], int],
    ) -> List[str]:
        """
        Trim oldest-retrieved memories until the memory block fits within token limit.
        (Legacy memory block system - not needed when use_memory_in_context=True)

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
            memory_block_text = build_memory_block_text(
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

    # ===== New memory-in-context methods =====
    
    def insert_memory_into_context(self, memory: MemoryEntry) -> Tuple[bool, bool]:
        """
        Insert a memory into the conversation context (new memory-in-context system).
        
        The memory is formatted as a user message and inserted at the current end
        of the conversation context. Its position is tracked so we know if it gets
        rolled out when context is trimmed.
        
        Args:
            memory: The MemoryEntry to insert
            
        Returns:
            Tuple of (was_inserted, is_new_retrieval):
            - (True, True): New memory inserted, retrieval count should be updated
            - (True, False): Previously retrieved memory re-inserted (was rolled out)
            - (False, False): Memory already in context, no action needed
        """
        context_length = len(self.conversation_context)
        already_retrieved, currently_in_context = self.memory_tracker.check_memory_status(
            memory.id, context_length
        )
        
        if currently_in_context:
            # Already in context, nothing to do
            return (False, False)
        
        # Format and insert the memory
        memory_message = format_memory_as_context_message(
            memory_id=memory.id,
            content=memory.content,
            created_at=memory.created_at,
            role=memory.role,
        )
        
        insertion_point = find_memory_insertion_point(self.conversation_context)
        self.conversation_context.insert(insertion_point, memory_message)
        
        # Record the insertion
        is_new_retrieval = not already_retrieved
        self.memory_tracker.record_memory_insertion(
            memory_id=memory.id,
            position=insertion_point,
            is_new_retrieval=is_new_retrieval,
        )
        
        # Also store in session_memories for reference
        self.session_memories[memory.id] = memory
        
        # Keep retrieved_ids in sync (for compatibility)
        if is_new_retrieval:
            self.retrieved_ids.add(memory.id)
        
        logger.debug(f"[MEMORY] Inserted memory {memory.id[:8]}... at position {insertion_point} (new={is_new_retrieval})")
        
        return (True, is_new_retrieval)
    
    def get_in_context_memory_count(self) -> int:
        """
        Get the count of memories currently in context.
        
        Works with both legacy and new memory systems.
        """
        if self.use_memory_in_context:
            return len(self.memory_tracker.get_in_context_memory_ids(len(self.conversation_context)))
        else:
            return len(self.in_context_ids)

    # ===== Shared methods (work with both systems) =====

    def add_exchange(
        self,
        human_message: Optional[str],
        assistant_response: str,
        tool_exchanges: Optional[List[Dict[str, Any]]] = None,
    ):
        """Add a human/assistant exchange to the conversation context.

        If human_message is None (continuation), only the assistant response is added.
        For multi-entity conversations, messages are labeled with participant names.

        Args:
            human_message: The human's message (None for continuations)
            assistant_response: The final text response from the assistant
            tool_exchanges: Optional list of tool exchanges that occurred during this response.
                Each exchange is a dict with "assistant" and "user" keys containing
                the tool_use and tool_result messages respectively.
        """
        if human_message:
            self.conversation_context.append({"role": "user", "content": human_message})

        # Add tool exchanges if any occurred during this response
        # These go between the user message and the final assistant response
        if tool_exchanges:
            for exchange in tool_exchanges:
                # Assistant's tool_use message (content is a list of content blocks)
                self.conversation_context.append({
                    "role": "assistant",
                    "content": exchange["assistant"]["content"],
                    "is_tool_use": True,
                })
                # User's tool_result message (content is a list of tool_result blocks)
                self.conversation_context.append({
                    "role": "user",
                    "content": exchange["user"]["content"],
                    "is_tool_result": True,
                })

        # Add the final assistant response (text only)
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
        - With memory-in-context, memories are part of the cached history

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

    def should_consolidate_cache(self, count_tokens_fn: Callable[[str], int]) -> bool:
        """
        Determine if we should consolidate (grow) the cached conversation history.

        Consolidation causes a cache MISS but creates a larger cache for future hits.

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
            cached_text = "\n".join(
                f"{m['role']}: {get_message_content_text(m['content'])}"
                for m in cached_context
            )
            cached_tokens = count_tokens_fn(cached_text)

            # If cached context is too small to be cached (< 1024 tokens), grow it
            if cached_tokens < 1024:
                logger.info(f"[CACHE] Consolidation check: cached_tokens={cached_tokens} < 1024, will consolidate")
                return True

        # Calculate tokens in new context
        new_text = "\n".join(
            f"{m['role']}: {get_message_content_text(m['content'])}"
            for m in new_context
        )
        new_tokens = count_tokens_fn(new_text)

        # Consolidate when new history reaches threshold (balance cache hits vs prefix growth)
        will_consolidate = new_tokens >= 2048
        logger.info(f"[CACHE] Consolidation check: cached_history={len(cached_context)} msgs/{cached_tokens} tokens, new_history={len(new_context)} msgs/{new_tokens} tokens, threshold=2048, will_consolidate={will_consolidate}")

        return will_consolidate

    def update_cache_state(self, cached_context_length: int):
        """
        Update cache tracking after an API call.

        Args:
            cached_context_length: Number of messages in the cached history block
        """
        old_ctx_len = self.last_cached_context_length
        self.last_cached_context_length = cached_context_length

        # Log if cache state changed
        if cached_context_length != old_ctx_len:
            logger.info(f"[CACHE] Cache state updated: history {old_ctx_len}->{cached_context_length} msgs")

    def trim_context_to_limit(
        self,
        max_tokens: int,
        count_tokens_fn: Callable[[str], int],
        current_message: str = "",
    ) -> int:
        """
        Trim oldest messages from conversation context until it fits within token limit.

        Messages are removed in FIFO order (oldest = first removed).
        When using memory-in-context, this also updates memory tracking for rolled-out memories.

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
                f"{msg['role']}: {get_message_content_text(msg.get('content', ''))}"
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

            # Remove the oldest message
            self.conversation_context.pop(0)
            removed_count += 1
            
            # If next message is assistant, remove it too to maintain pairs
            if self.conversation_context and self.conversation_context[0]["role"] == "assistant":
                self.conversation_context.pop(0)
                removed_count += 1

        # If using memory-in-context and we removed messages, update memory tracking
        if self.use_memory_in_context and removed_count > 0:
            rolled_out = self.memory_tracker.handle_context_rollout(
                num_messages_removed=removed_count,
                conversation_context=self.conversation_context,
            )
            if rolled_out:
                logger.info(f"[MEMORY] Context trimming rolled out {len(rolled_out)} memories")

        return removed_count
