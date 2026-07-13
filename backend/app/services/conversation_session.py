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
    source: str = "unknown"  # What retrieved this memory: "user"/"assistant"/"both" (semantic queries) or "recent_reflection" (first-turn recency injection)


@dataclass
class ConversationSession:
    """
    Runtime session state for an active conversation.

    Maintains the conversation context (message history) and tracks memories
    that have been retrieved during this conversation.

    Memory system (memory-in-context):
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

    # Position-based memory tracker (memories live inside conversation_context)
    memory_tracker: MemoryContextTracker = field(default_factory=MemoryContextTracker)

    # Cache tracking for conversation history (single breakpoint).
    # Advanced to the full context length after every exchange, so each turn's
    # new messages are written to the cache once and read on later turns.
    last_cached_context_length: int = 0

    # ===== Provider-usage token calibration =====
    # After each API response, the provider-reported prompt-side total
    # (input + cache_creation + cache_read) is recorded alongside the local
    # tiktoken estimate of the same prompt. Their ratio calibrates the local
    # counter, which undercounts Claude tokens by roughly 15-20%.
    last_prompt_actual_tokens: Optional[int] = None
    last_prompt_estimated_tokens: Optional[int] = None

    def record_prompt_usage(self, actual_tokens: int, estimated_tokens: int) -> None:
        """
        Record the provider-reported prompt size and the local estimate of the
        same prompt, for calibrating later local counts. Ignores requests where
        either side is unavailable (e.g. providers that report zero usage).
        """
        if actual_tokens > 0 and estimated_tokens > 0:
            self.last_prompt_actual_tokens = actual_tokens
            self.last_prompt_estimated_tokens = estimated_tokens

    @property
    def token_calibration_ratio(self) -> float:
        """
        Ratio of provider-reported to locally-estimated prompt tokens for the
        last request (1.0 until usage has been recorded). Clamped to [0.5, 2.0]
        so a pathological reading can't wildly distort trimming.
        """
        if not self.last_prompt_actual_tokens or not self.last_prompt_estimated_tokens:
            return 1.0
        ratio = self.last_prompt_actual_tokens / self.last_prompt_estimated_tokens
        return max(0.5, min(2.0, ratio))

    def insert_memory_into_context(self, memory: MemoryEntry) -> Tuple[bool, bool]:
        """
        Insert a memory into the conversation context.

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

        if is_new_retrieval:
            self.retrieved_ids.add(memory.id)
        
        logger.debug(f"[MEMORY] Inserted memory {memory.id[:8]}... at position {insertion_point} (new={is_new_retrieval})")
        
        return (True, is_new_retrieval)
    
    def get_in_context_memory_ids(self) -> Set[str]:
        """Get the set of memory IDs currently in context (not rolled out)."""
        return self.memory_tracker.get_in_context_memory_ids(len(self.conversation_context))

    def get_query_surfaced_memory_ids(self) -> Set[str]:
        """
        Get the memory IDs surfaced by memory_query tool results that are
        still in the conversation context.

        The IDs are stamped onto each memory_query tool_result context
        message (memory_query_ids) — by the tool loop live, and by parsing
        the persisted result on session reload. Deriving the set by scanning
        the context means it shrinks naturally when trimming removes the
        tool_result message, mirroring how memory_tracker positions roll out.
        """
        ids: Set[str] = set()
        for msg in self.conversation_context:
            ids.update(msg.get("memory_query_ids") or ())
        return ids

    def get_in_context_memory_count(self) -> int:
        """Get the count of memories currently in context."""
        return len(self.get_in_context_memory_ids())

    def has_conversational_messages(self) -> bool:
        """
        True if the context contains any actual conversational message
        (human/assistant exchange or tool exchange).

        Context seeds that are not part of the back-and-forth — the entity's
        notes message (is_notes), memory insertions (is_memory), and context
        notices (is_context_notice) — don't count. A session freshly loaded
        for a brand-new conversation contains the notes message, so a plain
        length check cannot detect the first turn.
        """
        return any(
            not (msg.get("is_notes") or msg.get("is_memory") or msg.get("is_context_notice"))
            for msg in self.conversation_context
        )

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
            # Label with [Human] in multi-entity mode, matching how the message
            # is rendered on session reload (load_session_from_db) — the live
            # and reloaded context must be identical for prompt-cache stability.
            if self.is_multi_entity:
                self.conversation_context.append({"role": "user", "content": f"[Human]: {human_message}"})
            else:
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
                tool_result_message = {
                    "role": "user",
                    "content": exchange["user"]["content"],
                    "is_tool_result": True,
                }
                # Memory IDs surfaced by memory_query calls in this exchange,
                # stamped on the context message so retrieval dedup can see
                # them for as long as the tool result remains in context.
                if exchange.get("memory_query_ids"):
                    tool_result_message["memory_query_ids"] = exchange["memory_query_ids"]
                self.conversation_context.append(tool_result_message)

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
        - cached_context is everything up to the breakpoint set after the last
          API call; the cache_control marker goes on its final message.
        - new_context is anything appended since then (memory-in-context
          insertions, context notices). It is sent uncached this turn and
          absorbed into cached_context when the breakpoint advances after
          this turn's API call.

        Anthropic caching is longest-prefix matching, so advancing the
        breakpoint every turn is an incremental cache write of the new tail,
        not a miss: the previously cached prefix is still read from cache.
        """
        # Split context into cached (frozen) vs new
        cached_context = self.conversation_context[:self.last_cached_context_length]
        new_context = self.conversation_context[self.last_cached_context_length:]

        return {
            "cached_context": cached_context,
            "new_context": new_context,
        }

    def update_cache_state(self, cached_context_length: int):
        """
        Update cache tracking after an API call.

        Args:
            cached_context_length: Number of messages in the cached history block
        """
        self.last_cached_context_length = cached_context_length

    def trim_context_to_limit(
        self,
        max_tokens: int,
        count_tokens_fn: Callable[[str], int],
        current_message: str = "",
    ) -> int:
        """
        Trim oldest messages from conversation context until it fits within token limit.

        Messages are removed in FIFO order (oldest = first removed).
        Memory tracking is updated for any memories that roll out with them.

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

            # Calibrate the local estimate against the provider-reported size
            # of the last prompt (ratio is 1.0 until usage has been recorded)
            current_tokens = int(count_tokens_fn(context_text) * self.token_calibration_ratio)

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

        if removed_count > 0:
            # Keep the cache breakpoint aligned with the messages that remain.
            # Front-trimming shifts every index down by removed_count, so the
            # breakpoint must shift with it; if the breakpoint was inside the
            # trimmed region it collapses to 0. Without this, cached_context
            # would point at the wrong messages, the [CONVERSATION HISTORY]
            # header would land on a different message, and every subsequent
            # turn would be a full cache miss.
            old_cache_len = self.last_cached_context_length
            self.last_cached_context_length = max(0, old_cache_len - removed_count)
            if self.last_cached_context_length != old_cache_len:
                logger.info(
                    f"[CACHE] Context trim removed {removed_count} msgs; "
                    f"cache breakpoint {old_cache_len}->{self.last_cached_context_length}"
                )

            # Update memory tracking for rolled-out memories
            rolled_out = self.memory_tracker.handle_context_rollout(
                num_messages_removed=removed_count,
                conversation_context=self.conversation_context,
            )
            if rolled_out:
                logger.info(f"[MEMORY] Context trimming rolled out {len(rolled_out)} memories")

        return removed_count
