"""
Memory Context Integration Module

This module provides the new memory tracking system where memories are inserted
directly into conversation history rather than being rendered as a separate block.

This approach improves cacheability (memories are paid for once per conversation
instead of re-rendered each turn) and creates a more integrated experience.

Usage:
    The functions and class in this module are designed to work alongside the 
    existing ConversationSession. During migration, ConversationSession will 
    be updated to use these new tracking mechanisms.
"""

from typing import Dict, List, Set, Optional, Any, Tuple
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


def format_memory_as_context_message(
    memory_id: str,
    content: str,
    created_at: str,
    role: str,
) -> Dict[str, Any]:
    """
    Format a memory as a user message for insertion into conversation context.
    
    Memories are inserted as user-role messages with special markers so they
    can be identified and tracked. The format makes it clear to the AI that
    this is remembered content from a previous conversation.
    
    Args:
        memory_id: The unique ID of the memory
        content: The memory content (original message text)
        created_at: ISO format timestamp of when the original message was created
        role: The original role of the message ("human" or "assistant")
    
    Returns:
        Dict formatted as a conversation context message with memory metadata
    """
    # Format content with clear markers
    role_label = "you" if role == "assistant" else "human"
    formatted_content = f"[MEMORY from {created_at} - originally from {role_label}]\n{content}\n[/MEMORY]"
    
    return {
        "role": "user",
        "content": formatted_content,
        "is_memory": True,
        "memory_id": memory_id,
    }


@dataclass
class MemoryContextTracker:
    """
    Tracks memories that have been inserted into conversation context.
    
    This replaces the old in_context_ids/memory block approach with position-based
    tracking. Memories are inserted into conversation_context as regular messages,
    and we track their positions to know which are still present after context rolling.
    
    Attributes:
        retrieved_ids: All memory IDs that have been retrieved this conversation
                      (retrieval count has been incremented). Never cleared.
        memory_positions: Maps memory_id -> index in conversation_context where 
                         the memory message was inserted. Updated when context rolls.
        session_memories: Full memory data for each retrieved memory, keyed by ID.
    """
    # All IDs that have had retrieval count updated this conversation (never remove)
    retrieved_ids: Set[str] = field(default_factory=set)
    
    # Maps memory_id -> position in conversation_context
    # Position of -1 means "was retrieved but has been rolled out of context"
    memory_positions: Dict[str, int] = field(default_factory=dict)
    
    def is_memory_in_context(self, memory_id: str, context_length: int) -> bool:
        """
        Check if a memory is currently in context (hasn't been rolled out).
        
        Args:
            memory_id: The memory ID to check
            context_length: Current length of conversation_context
            
        Returns:
            True if memory is in context, False if not present or rolled out
        """
        if memory_id not in self.memory_positions:
            return False
        position = self.memory_positions[memory_id]
        # Position of -1 means explicitly rolled out
        # Position >= context_length means implicitly rolled out
        return 0 <= position < context_length
    
    def get_in_context_memory_ids(self, context_length: int) -> Set[str]:
        """
        Get the set of memory IDs currently in context.
        
        Args:
            context_length: Current length of conversation_context
            
        Returns:
            Set of memory IDs that are currently in context
        """
        return {
            mid for mid, pos in self.memory_positions.items()
            if 0 <= pos < context_length
        }
    
    def record_memory_insertion(
        self,
        memory_id: str,
        position: int,
        is_new_retrieval: bool,
    ) -> None:
        """
        Record that a memory was inserted into context at a given position.
        
        Args:
            memory_id: The memory ID
            position: Index in conversation_context where it was inserted
            is_new_retrieval: True if this is first retrieval (not restoration)
        """
        self.memory_positions[memory_id] = position
        if is_new_retrieval:
            self.retrieved_ids.add(memory_id)
    
    def handle_context_rollout(
        self,
        num_messages_removed: int,
        conversation_context: List[Dict[str, Any]],
    ) -> Set[str]:
        """
        Update memory tracking after messages are removed from context start.
        
        When context rolls (oldest messages removed), we need to:
        1. Mark memories that were rolled out (set position to -1)
        2. Shift positions of remaining memories down
        
        Args:
            num_messages_removed: How many messages were removed from the start
            conversation_context: The context AFTER removal (for verification)
            
        Returns:
            Set of memory IDs that were rolled out
        """
        rolled_out_ids = set()
        
        for memory_id, position in list(self.memory_positions.items()):
            if position < 0:
                # Already rolled out, skip
                continue
            elif position < num_messages_removed:
                # This memory was in the removed portion
                self.memory_positions[memory_id] = -1
                rolled_out_ids.add(memory_id)
                logger.debug(f"[MEMORY] Memory {memory_id[:8]}... rolled out of context")
            else:
                # This memory is still in context, shift its position
                self.memory_positions[memory_id] = position - num_messages_removed
        
        return rolled_out_ids
    
    def check_memory_status(
        self,
        memory_id: str,
        context_length: int,
    ) -> Tuple[bool, bool]:
        """
        Check the status of a memory for retrieval decisions.
        
        Returns:
            Tuple of (already_retrieved, currently_in_context):
            - (False, False): Never seen before - should insert and increment count
            - (True, False): Previously retrieved but rolled out - should re-insert, no count increment
            - (True, True): Already in context - skip entirely
        """
        already_retrieved = memory_id in self.retrieved_ids
        currently_in_context = self.is_memory_in_context(memory_id, context_length)
        return (already_retrieved, currently_in_context)


def find_memory_insertion_point(
    conversation_context: List[Dict[str, Any]],
) -> int:
    """
    Find the appropriate position to insert memories in conversation context.
    
    Memories should be inserted at the end of the current context, just before
    where the new user message will be added. This ensures they appear in the
    flow of conversation at the point they became relevant.
    
    For tool exchanges, we want to insert after any pending tool results but
    before the position where the next human message would go.
    
    Args:
        conversation_context: Current conversation context
        
    Returns:
        Index where memory messages should be inserted
    """
    # Insert at the end of current context
    # The human message and assistant response will be added after
    return len(conversation_context)
