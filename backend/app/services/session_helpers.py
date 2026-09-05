"""
Session Helper Functions

Utility functions used by the session management system for memory retrieval,
significance calculation, caching, and token estimation.

Split from session_manager.py to reduce file size and improve maintainability.
"""

import asyncio
import itertools
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from app.config import settings
from app.services.memory_service import (
    ROLE_FILTER_AI,
    ROLE_FILTER_HUMAN,
    role_matches_filter,
)

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


def drop_in_context_reflections(
    enriched_candidates: List[Dict[str, Any]],
    in_context_ids: Set[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Remove already-in-context reflections from the ranked candidate pool
    before the top-k cut (issue #328).

    Automatic retrieval skips memories that are already in context without
    backfilling their slots from lower-ranked candidates — otherwise a long
    conversation fills with ever-weaker matches. Reflections are the
    exception: they reach the entity by guaranteed channels (the most
    recent ones on waking, the sibling mailbox for the rest), so when the
    semantic pull ranks one of them highly it would only block the verbatim
    memory ranked just below it. Dropping them from the pool *before* the
    cut lets the next-ranked candidate move up; an in-context verbatim
    memory stays in the pool and still consumes its slot.

    Args:
        enriched_candidates: Candidates sorted by combined_score descending,
            each carrying {"mem_data": {"id": ..., "role": ...}, ...}
        in_context_ids: Memory IDs the entity can already see

    Returns:
        (remaining candidates in rank order, the reflections dropped)
    """
    remaining: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for item in enriched_candidates:
        mem_data = item["mem_data"]
        if mem_data.get("role") == "reflection" and mem_data["id"] in in_context_ids:
            dropped.append(item)
        else:
            remaining.append(item)
    return remaining, dropped


# Candidate pools for automatic retrieval (issue #335). With role balance
# on, the human's words and the entity's words are searched and ranked as
# two separate pools and each contributes its own top N; off, one merged
# pool cut at top_k. Pool names double as search_memories role filters.
POOL_ALL = "all"
POOL_HUMAN = ROLE_FILTER_HUMAN
POOL_AI = ROLE_FILTER_AI
_POOL_ROLE_FILTERS = {POOL_ALL: None, POOL_HUMAN: ROLE_FILTER_HUMAN, POOL_AI: ROLE_FILTER_AI}


def retrieval_top_k_by_pool(
    split_by_role: bool,
    *,
    merged_top_k: int,
    per_role_top_k: int,
) -> Dict[str, int]:
    """
    How many memories each candidate pool contributes to a retrieval.

    Role balance on: the human pool and the entity pool each give
    per_role_top_k (the *_retrieval_top_k_per_role settings). Off: the
    single merged pool gives merged_top_k (the *_retrieval_top_k settings).
    """
    if split_by_role:
        return {POOL_HUMAN: per_role_top_k, POOL_AI: per_role_top_k}
    return {POOL_ALL: merged_top_k}


async def search_candidate_pools(
    search_memories: Callable[..., Awaitable[List[Dict[str, Any]]]],
    user_query: Optional[str],
    assistant_query: Optional[str],
    *,
    fetch_k: int,
    split_by_role: bool,
    log_prefix: str = "[MEMORY]",
    **search_kwargs: Any,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Run the automatic-retrieval searches and group the hits into candidate
    pools, keyed by pool name.

    Two query texts drive retrieval: the human's current message and the
    entity's previous response. With split_by_role (role balance, issue
    #335) there are two pools — the human's words (role filter "human")
    and the entity's (role filter "ai": its own messages, reflections, and
    sibling letters) — and *both* queries run against *both* pools, four
    searches in all. The cross-feed matters in both directions: when the
    human opens a new subject, the entity's last message is still about the
    old one, so the prompt query is what keeps the entity pool current; and
    when the human's message is "okay, go ahead", the entity's last message
    is what keeps the human pool topical instead of returning the human's
    other terse turns. Without the split there is one merged pool queried
    by both texts (two searches, the pre-#335 shape minus the role swap).

    Within a pool a hit returned by both queries keeps its higher score;
    every hit is tagged "_source" ("user" / "assistant" / "both") and
    "_pool". A hit is admitted to a pool only if its role satisfies the
    pool's filter — the Pinecone-side filter already guarantees this, so in
    production the check is a no-op backstop. The searches run concurrently.
    """
    queries = [
        (source, query)
        for source, query in (("user", user_query), ("assistant", assistant_query))
        if query
    ]
    pools = [POOL_HUMAN, POOL_AI] if split_by_role else [POOL_ALL]
    plan = [(pool, source, query) for pool in pools for source, query in queries]

    results = await asyncio.gather(*[
        search_memories(
            query=query,
            top_k=fetch_k,
            role_filter=_POOL_ROLE_FILTERS[pool],
            **search_kwargs,
        )
        for pool, _source, query in plan
    ])

    best_by_pool: Dict[str, Dict[str, Dict[str, Any]]] = {pool: {} for pool in pools}
    sources_by_pool: Dict[str, Dict[str, Set[str]]] = {pool: {} for pool in pools}
    for (pool, source, _query), hits in zip(plan, results, strict=True):
        logger.info(
            f"{log_prefix} {source.capitalize()} query retrieved {len(hits)} candidates "
            f"(pool={pool})"
        )
        role_filter = _POOL_ROLE_FILTERS[pool]
        for hit in hits:
            if not role_matches_filter(hit.get("role"), role_filter):
                continue
            cid = hit["id"]
            sources_by_pool[pool].setdefault(cid, set()).add(source)
            current = best_by_pool[pool].get(cid)
            if current is None or hit["score"] > current["score"]:
                # Copy so tags never leak into the search cache's dicts
                best_by_pool[pool][cid] = dict(hit)

    candidate_pools: Dict[str, List[Dict[str, Any]]] = {}
    for pool in pools:
        candidates = []
        for cid, hit in best_by_pool[pool].items():
            sources = sources_by_pool[pool][cid]
            hit["_source"] = "both" if len(sources) > 1 else next(iter(sources))
            hit["_pool"] = pool
            candidates.append(hit)
        candidate_pools[pool] = candidates
        logger.info(
            f"{log_prefix} Combined {len(candidates)} unique candidates "
            f"(pool={pool}, {len(queries)} queries)"
        )
    return candidate_pools


@dataclass
class PoolSelection:
    """The outcome of select_top_by_pool: what the cut kept and what it didn't."""
    selected: List[Dict[str, Any]] = field(default_factory=list)  # all pools, best combined score first
    skipped_reflections: List[Dict[str, Any]] = field(default_factory=list)  # in-context reflections dropped before the cut
    unselected: List[Dict[str, Any]] = field(default_factory=list)  # below the cut, all pools, best first
    pool_sizes: Dict[str, int] = field(default_factory=dict)  # candidates per pool after the reflection drop
    pool_selected: Dict[str, int] = field(default_factory=dict)  # candidates the cut took per pool

    def describe(self, top_k_by_pool: Dict[str, int]) -> str:
        """One-line summary for the retrieval log, e.g. 'human 3/7 (top 3), ai 3/12 (top 3)'."""
        return ", ".join(
            f"{pool} {self.pool_selected.get(pool, 0)}/{self.pool_sizes.get(pool, 0)} (top {top_k})"
            for pool, top_k in top_k_by_pool.items()
        )


def select_top_by_pool(
    enriched_candidates: List[Dict[str, Any]],
    in_context_ids: Set[str],
    top_k_by_pool: Dict[str, int],
) -> PoolSelection:
    """
    Rank each candidate pool by combined score and take its top N.

    Each enriched candidate carries the pool it was searched from
    ("pool"; missing means the merged pool). Per pool: sort by combined
    score, drop in-context reflections before the cut (issue #328 — they
    hold no slot, the next-ranked candidate moves up), then keep the top N
    from top_k_by_pool. In-context verbatim memories stay in and are
    skipped by the caller without backfill. The returned lists are merged
    across pools in combined-score order, so the context receives the
    strongest memory first regardless of who authored it.
    """
    selection = PoolSelection()
    by_score = lambda item: item["combined_score"]  # noqa: E731
    for pool, top_k in top_k_by_pool.items():
        members = [
            item for item in enriched_candidates
            if item.get("pool", POOL_ALL) == pool
        ]
        members.sort(key=by_score, reverse=True)
        members, dropped = drop_in_context_reflections(members, in_context_ids)
        cut = members[:max(top_k, 0)]
        selection.skipped_reflections.extend(dropped)
        selection.selected.extend(cut)
        selection.unselected.extend(members[len(cut):])
        selection.pool_sizes[pool] = len(members)
        selection.pool_selected[pool] = len(cut)
    selection.selected.sort(key=by_score, reverse=True)
    selection.unselected.sort(key=by_score, reverse=True)
    selection.skipped_reflections.sort(key=by_score, reverse=True)
    return selection


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


def add_cache_control_to_tool_result(user_msg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add cache_control to the last tool_result block in a user message.

    This enables Anthropic's prompt caching between tool iterations: the
    breakpoint sits on the latest tool_result every iteration, so each API
    call writes only the newest exchange and reads the rest of the prefix
    from cache. The 1h TTL matters because tool exchanges survive into the
    next turn, which is human-paced and may exceed the 5-minute TTL.

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


def make_link_timestamper(
    user_message_timestamp: Optional[datetime],
) -> Callable[[], Optional[datetime]]:
    """
    Build a callable producing ConversationMemoryLink.retrieved_at values for
    one turn's memory insertions.

    Session reload (load_session_from_db) interleaves memories with messages
    by comparing link.retrieved_at against Message.created_at, so the link
    timestamp determines where a memory lands in the rebuilt context. Live,
    memories are inserted *before* the turn's human message, but retrieval
    runs after the route captures the send timestamp used as the human row's
    created_at — a wall-clock retrieved_at would place them *after* it on
    reload, diverging from the context the prompt cache was built on.

    Anchoring the links 1ms before the send timestamp (with a strictly
    increasing microsecond per link to preserve insertion order) makes the
    rebuilt context put these memories exactly where the live context had
    them. The 1ms offset cannot reach back past the previous turn's rows:
    those were committed at least a human round trip earlier.

    When no send timestamp is available, returns None values so callers fall
    back to wall-clock defaults (previous behavior).
    """
    if user_message_timestamp is None:
        return lambda: None

    anchor = user_message_timestamp - timedelta(milliseconds=1)
    counter = itertools.count()

    def next_link_time() -> Optional[datetime]:
        return anchor + timedelta(microseconds=next(counter))

    return next_link_time


# Backward compatibility aliases (with underscore prefix matching old names)
# These allow existing code to import from here without changes
_build_memory_queries = build_memory_queries
_calculate_significance = calculate_significance
_get_message_content_text = get_message_content_text
_add_cache_control_to_tool_result = add_cache_control_to_tool_result
