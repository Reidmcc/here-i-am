"""
Session Manager

Manages conversation sessions and the full message processing pipeline.
This is the main orchestrator for chat interactions, handling:
- Session creation and lifecycle
- Memory retrieval and injection
- LLM API calls with streaming
- Tool use handling

The data structures (ConversationSession, MemoryEntry) are now in
conversation_session.py. Helper functions are in session_helpers.py.
"""

from typing import Dict, List, Set, Optional, Any, AsyncIterator
from datetime import datetime
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Conversation, Message, MessageRole, ConversationType, ConversationEntity
from app.services import memory_service, llm_service
from app.services.tool_service import tool_service, ToolResult
from app.services.notes_tools import set_current_entity_label
from app.config import settings

# Import from split modules
from app.services.conversation_session import ConversationSession, MemoryEntry
from app.services.session_helpers import (
    build_memory_queries,
    calculate_significance,
    ensure_role_balance,
    get_message_content_text,
    build_memory_block_text,
    add_cache_control_to_tool_result,
    estimate_tool_exchange_tokens,
    # Backward compatibility aliases (with underscore prefix)
    _build_memory_queries,
    _calculate_significance,
    _ensure_role_balance,
    _get_message_content_text,
    _build_memory_block_text,
    _add_cache_control_to_tool_result,
    _estimate_tool_exchange_tokens,
)

logger = logging.getLogger(__name__)

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
            elif msg.role == MessageRole.TOOL_USE:
                # Tool use messages store content blocks as JSON
                # Reconstruct the proper format for API calls
                session.conversation_context.append({
                    "role": "assistant",
                    "content": msg.content_blocks,  # Uses the property that parses JSON
                    "is_tool_use": True,
                })
            elif msg.role == MessageRole.TOOL_RESULT:
                # Tool result messages store content blocks as JSON
                session.conversation_context.append({
                    "role": "user",
                    "content": msg.content_blocks,  # Uses the property that parses JSON
                    "is_tool_result": True,
                })
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

        # Set entity label for notes tools context
        # Use responding_entity_label if available (multi-entity), otherwise look up from entity_id
        entity_label = session.responding_entity_label
        if not entity_label and session.entity_id:
            entity_config = settings.get_entity_by_index(session.entity_id)
            if entity_config:
                entity_label = entity_config.label
        if entity_label:
            set_current_entity_label(entity_label)
            logger.debug(f"[NOTES] Set entity label context: {entity_label}")

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

        # Build messages WITHOUT memories for subsequent tool iterations (memory optimization)
        # This reduces context size on iterations after the first
        # Lazy initialization - only built when tool use is detected
        base_messages_no_memories = None

        # Step 6: Stream LLM response with caching enabled
        # This includes a tool use loop if tools are provided
        full_content = ""
        accumulated_tool_uses = []  # Track all tool uses across iterations
        tool_exchanges = []  # Track tool exchanges for rebuilding messages without memories
        tool_exchange_tokens = []  # Token count for each exchange (parallel to tool_exchanges)
        # Single moving cache breakpoint (like conversation history caching)
        # Only moves when enough new tokens accumulate, ensuring cache hits on prefix
        tool_cache_breakpoint_index: Optional[int] = None  # Index of exchange with cache_control
        total_tool_tokens = 0  # Total tokens across all tool exchanges
        tokens_at_last_breakpoint = 0  # Total tokens when breakpoint was last set/moved
        TOOL_CACHE_TOKEN_THRESHOLD = 2048  # Move breakpoint after N new tokens
        iteration = 0
        max_iterations = settings.tool_use_max_iterations

        while iteration < max_iterations:
            iteration += 1
            iteration_content = ""
            iteration_tool_use = None
            iteration_content_blocks = []
            stop_reason = None

            # Build working messages for this iteration
            # First iteration: include memories for full context
            # Subsequent iterations: use base messages without memories (memory optimization)
            if iteration == 1:
                working_messages = list(messages)  # Include memories
            else:
                # Lazy build base messages without memories (only when tool use actually happens)
                if base_messages_no_memories is None:
                    base_messages_no_memories = llm_service.build_messages_with_memories(
                        memories=[],  # No memories for subsequent iterations
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

                # Rebuild from base (no memories) + accumulated tool exchanges
                # Use single moving cache breakpoint (like conversation history)
                # Only the exchange at the breakpoint index gets cache_control
                working_messages = list(base_messages_no_memories)
                for i, exchange in enumerate(tool_exchanges):
                    working_messages.append(exchange["assistant"])
                    # Only add cache_control at the single breakpoint position
                    if i == tool_cache_breakpoint_index:
                        user_msg = _add_cache_control_to_tool_result(exchange["user"])
                    else:
                        user_msg = exchange["user"]
                    working_messages.append(user_msg)
                breakpoint_info = f"breakpoint at {tool_cache_breakpoint_index}" if tool_cache_breakpoint_index is not None else "no breakpoint"
                logger.info(f"[TOOLS] Iteration {iteration}: Using messages without memory block ({len(working_messages)} messages, {len(tool_exchanges)} tool exchanges, {breakpoint_info})")

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
                    content = event["content"]
                    # Add space before first token after tool use if needed
                    if iteration > 1 and not iteration_content and full_content and not full_content[-1].isspace():
                        content = " " + content
                    iteration_content += content
                    yield {"type": "token", "content": content}
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
                        # Include tool exchanges so they're persisted in conversation history
                        session.add_exchange(
                            user_message,
                            full_content,
                            tool_exchanges=tool_exchanges if tool_exchanges else None,
                        )

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

                        # Add tool data to done event if any tools were used
                        final_event = dict(event)
                        if accumulated_tool_uses:
                            final_event["tool_uses"] = accumulated_tool_uses
                        if tool_exchanges:
                            # Include full tool exchanges for DB persistence
                            final_event["tool_exchanges"] = tool_exchanges
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

                # Build tool exchange messages for tracking
                assistant_msg = {
                    "role": "assistant",
                    "content": iteration_content_blocks,
                }

                tool_result_content = []
                for result in tool_results:
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": result.tool_use_id,
                        "content": result.content,
                        "is_error": result.is_error,
                    })

                user_msg = {
                    "role": "user",
                    "content": tool_result_content,
                }

                # Store exchange for rebuilding messages without memories on next iteration
                exchange = {
                    "assistant": assistant_msg,
                    "user": user_msg,
                }
                tool_exchanges.append(exchange)

                # Track tokens and determine if breakpoint should move (single moving breakpoint)
                exchange_tokens = _estimate_tool_exchange_tokens(exchange, llm_service.count_tokens)
                tool_exchange_tokens.append(exchange_tokens)
                total_tool_tokens += exchange_tokens

                # Move breakpoint when enough new tokens have accumulated since last breakpoint
                # This uses a single cache breakpoint that moves forward, like conversation history
                tokens_since_breakpoint = total_tool_tokens - tokens_at_last_breakpoint
                if tokens_since_breakpoint >= TOOL_CACHE_TOKEN_THRESHOLD:
                    exchange_index = len(tool_exchanges) - 1
                    old_breakpoint = tool_cache_breakpoint_index
                    tool_cache_breakpoint_index = exchange_index
                    tokens_at_last_breakpoint = total_tool_tokens
                    logger.debug(f"[TOOLS] Moved cache breakpoint: {old_breakpoint} -> {exchange_index} ({total_tool_tokens} total tokens)")

                # Accumulate any text content from this iteration
                full_content += iteration_content

        # If we've exhausted iterations, yield what we have
        logger.warning(f"[TOOLS] Max iterations ({max_iterations}) reached")
        session.add_exchange(
            user_message,
            full_content,
            tool_exchanges=tool_exchanges if tool_exchanges else None,
        )

        # Update cache state for conversation history (same logic as normal exit path)
        if should_consolidate:
            new_cached_ctx_len = len(session.conversation_context) - 2
        elif session.last_cached_context_length == 0 and len(session.conversation_context) > 0:
            # Bootstrap: start caching with all current content
            new_cached_ctx_len = len(session.conversation_context)
        else:
            # Keep stable for cache hits
            new_cached_ctx_len = session.last_cached_context_length

        session.update_cache_state(new_cached_ctx_len)

        yield {
            "type": "done",
            "content": full_content,
            "model": session.model,
            "usage": {},
            "stop_reason": "max_iterations",
            "tool_uses": accumulated_tool_uses if accumulated_tool_uses else None,
            "tool_exchanges": tool_exchanges if tool_exchanges else None,
        }

    def close_session(self, conversation_id: str):
        """Remove a session from active sessions."""
        if conversation_id in self._sessions:
            del self._sessions[conversation_id]


# Singleton instance
session_manager = SessionManager()
