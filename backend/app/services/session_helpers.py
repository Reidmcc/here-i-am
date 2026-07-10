"""
Session Helper Functions

Utility functions used by the session management system for memory retrieval,
significance calculation, caching, and token estimation.

Split from session_manager.py to reduce file size and improve maintainability.
"""

from typing import Callable, Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import json
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def stamp_human_message(content: str, timestamp: Optional[datetime]) -> str:
    """
    Prefix a human message with its timestamp for LLM context.

    Gives the entity finer-grained time awareness than the per-turn
    [DATE CONTEXT] block (which is date-only). Applied ONLY when rendering
    messages into the LLM context — the content persisted to the DB and
    vectorized into memory stays unstamped. The timestamp is a prefix so
    content-suffix matching (e.g. regenerate's endswith fallback) keeps
    working.

    Timestamps are rendered in the server's local timezone (from the OS /
    TZ env var, via datetime.astimezone with no argument — no config knob).
    Naive datetimes are treated as UTC, matching Message.created_at and
    datetime.utcnow().

    Args:
        content: The human message text
        timestamp: When the message was sent (naive UTC or tz-aware)

    Returns:
        The stamped message, or the original content if timestamp is None
    """
    if not timestamp:
        return content
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    local_ts = timestamp.astimezone()
    return f"[{local_ts.strftime('%Y-%m-%d %H:%M %Z')}] {content}"


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
    memory_status: Optional[str] = None,
    role: Optional[str] = None,
) -> float:
    """
    Calculate memory significance based on retrieval patterns.

    significance = times_retrieved * recency_factor * half_life_modifier

    Where:
    - recency_factor boosts recently-retrieved memories
    - half_life_modifier decays significance based on memory age

    Pinned memories (memory_status == "pinned") are exempt from age decay:
    their half_life_modifier stays at 1.0 regardless of age.

    Memories the entity saved via memory_save (role == "reflection") have their
    significance multiplied by settings.reflection_significance_multiplier.
    """
    now = datetime.utcnow()

    # Handle string dates from database
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    if isinstance(last_retrieved_at, str):
        last_retrieved_at = datetime.fromisoformat(last_retrieved_at)

    # Half-life modifier - older memories decay in significance
    # Starts at 1.0 and halves every significance_half_life_days
    # Pinned memories don't decay with age
    half_life_modifier = 1.0
    if created_at and memory_status != "pinned":
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

    # Use (1 + 0.1 * times_retrieved) to keep retrieval count as a signal
    # without letting it dominate. The +1 base ensures never-retrieved memories
    # can still compete based on recency and age factors.
    significance = (1 + 0.1 * times_retrieved) * recency_factor * half_life_modifier

    # Boost self-authored memories (saved via the memory_save tool)
    if role == "reflection":
        significance *= settings.reflection_significance_multiplier

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
    This is used for token counting (context trimming, minimum-cacheable checks).
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


def total_prompt_tokens_from_usage(usage: Optional[Dict[str, Any]]) -> int:
    """
    Total prompt-side tokens the provider actually processed for a request:
    uncached input + cache writes + cache reads.

    Returns 0 when usage is missing or reports nothing (some providers
    return zeros or None for these fields).
    """
    if not usage:
        return 0
    total = 0
    for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        value = usage.get(key)
        if value:
            total += int(value)
    return total


def estimate_prompt_tokens(
    messages: List[Dict[str, Any]],
    count_tokens_fn: Callable[[str], int],
    system_prompt: Optional[str] = None,
) -> int:
    """
    Local estimate of a full API prompt's token size, using the same text
    extraction as context trimming.

    Paired with the provider-reported total for the same request
    (total_prompt_tokens_from_usage), this yields a calibration ratio for
    the local counter, which is approximate for non-OpenAI tokenizers.
    """
    parts = []
    if system_prompt:
        parts.append(system_prompt)
    for msg in messages:
        parts.append(f"{msg.get('role', '')}: {get_message_content_text(msg.get('content', ''))}")
    text = "\n".join(parts)
    return count_tokens_fn(text) if text else 0


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

    This enables Anthropic's prompt caching between tool iterations: the
    breakpoint sits on the latest tool_result every iteration, so each API
    call writes only the newest exchange and reads the rest of the prefix
    from cache. The 1h TTL matters because with memory-in-context
    (USE_MEMORY_IN_CONTEXT=true) tool exchanges can survive into the next
    turn, which is human-paced and may exceed the 5-minute TTL.

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


# Backward compatibility aliases (with underscore prefix matching old names)
# These allow existing code to import from here without changes
_build_memory_queries = build_memory_queries
_calculate_significance = calculate_significance
_ensure_role_balance = ensure_role_balance
_get_message_content_text = get_message_content_text
_build_memory_block_text = build_memory_block_text
_add_cache_control_to_tool_result = add_cache_control_to_tool_result
