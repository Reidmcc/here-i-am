"""
Session Helper Functions

Utility functions used by the session management system for memory retrieval,
significance calculation, caching, and token estimation.

Split from session_manager.py to reduce file size and improve maintainability.
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from datetime import datetime
import json
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def build_memory_queries(
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


def calculate_significance(
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

    # Use (1 + times_retrieved) instead of raw times_retrieved to avoid
    # zeroing out the entire calculation for never-retrieved memories.
    # This allows new memories to compete based on recency and age factors
    # rather than being flattened to the significance floor.
    significance = (1 + times_retrieved) * recency_factor * half_life_modifier
    return significance


def ensure_role_balance(
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


def get_message_content_text(content: Any) -> str:
    """
    Extract text representation from message content (string or content blocks).

    For string content, returns the string directly.
    For content blocks (tool_use, tool_result, text), extracts text/content fields.
    This is used for accurate token counting in cache consolidation decisions.
    """
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return str(content)

    # Content blocks - extract text from each block
    text_parts = []
    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue

        block_type = block.get("type", "")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            # Summarize tool use for token counting
            tool_name = block.get("name", "unknown")
            tool_input = json.dumps(block.get("input", {}))
            text_parts.append(f"[Tool use: {tool_name}({tool_input})]")
        elif block_type == "tool_result":
            # Tool result content
            result_content = block.get("content", "")
            if isinstance(result_content, str):
                text_parts.append(f"[Tool result: {result_content}]")
            else:
                text_parts.append(f"[Tool result: {json.dumps(result_content)}]")

    return "\n".join(text_parts)


def build_memory_block_text(
    memories: List[Dict[str, Any]],
    conversation_start_date: Optional[datetime] = None,
) -> str:
    """
    Build the memory block text for token counting purposes.

    This matches the format used in anthropic_service.build_messages_with_memories
    where memories are placed after conversation history.
    
    NOTE: This function is used by the legacy memory block system and will be
    deprecated when memory-context-integration is complete.
    """
    if not memories:
        return ""

    memory_block = "[MEMORIES FROM PREVIOUS CONVERSATIONS]\n\n"
    for mem in memories:
        memory_block += f"Memory (from {mem['created_at']}):\n"
        memory_block += f'"{mem["content"]}"\n\n'
    memory_block += "[/MEMORIES]"

    return memory_block


def add_cache_control_to_tool_result(user_msg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add cache_control to the last tool_result block in a user message.

    This enables Anthropic's prompt caching between tool iterations, so that
    previous tool exchanges are cached when making the next API call.

    Args:
        user_msg: The user message containing tool_result content blocks

    Returns:
        A new message dict with cache_control added to the last content block
    """
    # Make a shallow copy to avoid mutating the original
    result = dict(user_msg)

    content = result.get("content")
    if isinstance(content, list) and content:
        # Copy the content list and its blocks
        content_copy = []
        for i, block in enumerate(content):
            is_last = (i == len(content) - 1)
            if is_last:
                # Add cache_control to the last block
                block_copy = dict(block)
                block_copy["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
                content_copy.append(block_copy)
            else:
                content_copy.append(block)
        result["content"] = content_copy

    return result


def estimate_tool_exchange_tokens(
    exchange: Dict[str, Any],
    count_tokens_fn: Callable[[str], int]
) -> int:
    """
    Estimate the token count for a tool exchange (assistant tool_use + user tool_result).

    Args:
        exchange: Dict with "assistant" and "user" message dicts
        count_tokens_fn: Function to count tokens in text

    Returns:
        Estimated token count for the exchange
    """
    total = 0

    # Count tokens in assistant's tool_use content
    assistant_content = exchange.get("assistant", {}).get("content", [])
    if isinstance(assistant_content, list):
        for block in assistant_content:
            if block.get("type") == "tool_use":
                # Count tool name and input
                total += count_tokens_fn(block.get("name", ""))
                input_json = json.dumps(block.get("input", {}))
                total += count_tokens_fn(input_json)
            elif block.get("type") == "text":
                total += count_tokens_fn(block.get("text", ""))

    # Count tokens in user's tool_result content
    user_content = exchange.get("user", {}).get("content", [])
    if isinstance(user_content, list):
        for block in user_content:
            if block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, str):
                    total += count_tokens_fn(content)
                elif isinstance(content, list):
                    # Content can be a list of content blocks
                    for sub_block in content:
                        if isinstance(sub_block, dict) and sub_block.get("type") == "text":
                            total += count_tokens_fn(sub_block.get("text", ""))

    return total


# Backward compatibility aliases (with underscore prefix matching old names)
# These allow existing code to import from here without changes
_build_memory_queries = build_memory_queries
_calculate_significance = calculate_significance
_ensure_role_balance = ensure_role_balance
_get_message_content_text = get_message_content_text
_build_memory_block_text = build_memory_block_text
_add_cache_control_to_tool_result = add_cache_control_to_tool_result
_estimate_tool_exchange_tokens = estimate_tool_exchange_tokens
