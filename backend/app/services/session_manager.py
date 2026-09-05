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

import logging
import re
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Conversation,
    ConversationEntity,
    ConversationType,
    EntitySetting,
    Message,
    MessageRole,
)

# Import from split modules
from app.services.attachment_service import build_persistable_content
from app.services.context_tools import set_context_tool_session
from app.services.conversation_session import ConversationSession, MemoryEntry
from app.services.llm_service import llm_service
from app.services.memory_context import format_memory_as_context_message
from app.services.memory_service import memory_service
from app.services.memory_tools import consume_last_query_memory_ids, set_memory_tool_context
from app.services.notes_tools import (
    NOTE_IN_CONTEXT_MARKER,
    NOTE_STAMP_TOOL_NAMES,
    consume_last_note_stamps,
    note_content_hash,
    set_current_entity_label,
)
from app.services.session_helpers import (
    _add_cache_control_to_tool_result,
    # Backward compatibility aliases (with underscore prefix)
    _build_memory_queries,
    _calculate_significance,
    estimate_prompt_tokens,
    make_link_timestamper,
    retrieval_top_k_by_pool,
    search_candidate_pools,
    select_top_by_pool,
    stamp_human_message,
    total_prompt_tokens_from_usage,
)
from app.services.tool_service import tool_service

logger = logging.getLogger(__name__)

# Matches the per-memory header line in memory_query tool results, e.g.
# "--- Memory a1b2c3d4 (You said, 3.2 days ago, similarity: 0.812) ---".
# Used to rebuild query-result dedup state (memory_query_ids on tool_result
# context messages) when a session is reloaded from the DB.
_MEMORY_QUERY_RESULT_ID_RE = re.compile(r"^--- Memory ([0-9a-f]{8}) \(", re.MULTILINE)

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
        # Ensure conversation_id is a string for consistent comparison in memory filtering
        conversation_id = str(conversation_id)

        # Determine default model and provider based on entity configuration
        provider_hint = None
        if model is None and entity_id:
            entity = settings.get_entity_by_index(entity_id)
            if entity:
                # Use entity's default model, or fall back to provider default
                model = entity.default_model or settings.get_default_model_for_provider(entity.llm_provider)
                provider_hint = entity.llm_provider
        model = model or settings.default_model

        session = ConversationSession(
            conversation_id=conversation_id,
            model=model,
            temperature=temperature if temperature is not None else settings.default_temperature,
            max_tokens=max_tokens or settings.default_max_tokens,
            system_prompt=system_prompt,
            entity_id=entity_id,
            conversation_start_date=conversation_start_date,
            provider_hint=provider_hint,
        )
        self._sessions[conversation_id] = session
        return session

    async def refresh_thinking_effort(
        self,
        session: ConversationSession,
        db: AsyncSession,
    ) -> None:
        """
        Point the session at the responding entity's persisted thinking effort.

        Read per turn rather than cached on the session: the effort is a
        per-entity setting, and in multi-entity conversations the responding
        entity (and therefore the effort) changes turn to turn. A NULL setting
        leaves the session at None, which the provider services resolve to
        settings.default_thinking_effort.
        """
        if not session.entity_id:
            return
        setting = await db.get(EntitySetting, session.entity_id)
        session.thinking_effort = setting.thinking_effort if setting else None

    def _build_notes_context_message(
        self,
        entity_label: str,
        entity_notes: Optional[str],
        shared_notes: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """
        Build a single cached-history context message holding the entity's notes.

        Combines the entity's index.md and any shared notes into one user-role
        message wrapped in [ENTITY NOTES]/[SHARED NOTES] markers. Returns None when
        there are no notes to inject. Marked with is_notes=True for identification.

        The notes content is passed in (from the conversation's frozen
        notes_seed snapshot) rather than read from disk here, so a reload
        rebuilds the byte-identical message the live session first cached even
        after the notes have been edited on disk.
        """
        parts: List[str] = []
        # Stamps recording which note files are fully visible in this message
        # (notes_read dedup state; see get_in_context_note_stamps)
        note_stamps: List[Dict[str, Any]] = []

        if entity_notes:
            parts.append(f"[ENTITY NOTES]\n{entity_notes}\n[/ENTITY NOTES]")
            note_stamps.append({
                "owner": entity_label,
                "filename": "index.md",
                "hash": note_content_hash(entity_notes),
                "source": "seed",
            })
            logger.info(
                f"[NOTES] Injected index.md for entity '{entity_label}' into cached history ({len(entity_notes)} chars)"
            )

        if shared_notes:
            parts.append(f"[SHARED NOTES]\n{shared_notes}\n[/SHARED NOTES]")
            note_stamps.append({
                "owner": "shared",
                "filename": "index.md",
                "hash": note_content_hash(shared_notes),
                "source": "seed",
            })
            logger.info(
                f"[NOTES] Injected shared index.md into cached history ({len(shared_notes)} chars)"
            )

        if not parts:
            return None

        return {
            "role": "user",
            "content": "\n\n".join(parts),
            "is_notes": True,
            "note_stamps": note_stamps,
        }

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
        provider_hint = None
        if responding_entity_id:
            entity = settings.get_entity_by_index(responding_entity_id)
            if entity:
                model = entity.default_model or settings.get_default_model_for_provider(entity.llm_provider)
                provider_hint = entity.llm_provider
        elif entity_id:
            entity = settings.get_entity_by_index(entity_id)
            if entity:
                provider_hint = entity.llm_provider

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
        session.provider_hint = provider_hint

        # Per-entity thinking effort (refreshed again at the start of each turn)
        await self.refresh_thinking_effort(session, db)

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

        # Fetch archived source-conversation IDs so we can drop memories whose
        # source conversation has been archived since they were first retrieved.
        # Without this, ConversationMemoryLink would re-inject those memories
        # into context on every session reload, bypassing the live archive
        # filter. Unarchiving the source conversation removes its ID from this
        # set, so its memories become retrievable again on the next reload.
        archived_source_ids: Set[str] = set()
        if memory_service.is_configured(entity_id=entity_id):
            archived_source_ids = await memory_service.get_archived_conversation_ids(
                db, entity_id=entity_id
            )

        # Memories are re-inserted into the rebuilt context at their original
        # positions, interleaved with messages by retrieval timestamp.
        memories_with_timestamps = await memory_service.get_retrieved_memories_with_timestamps(
            conversation_id, db, entity_id=entity_id if is_multi_entity else None
        )

        # Build a mapping of message_id -> memory data for quick lookup
        memory_data_by_id: Dict[str, Optional[Dict]] = {}
        retrieved_ids = set()
        skipped_archived = 0

        for mem_info in memories_with_timestamps:
            mem_id = mem_info["message_id"]
            mem_data = await memory_service.get_full_memory_content(mem_id, db)
            if mem_data:
                if mem_data["conversation_id"] in archived_source_ids:
                    skipped_archived += 1
                    continue
                # Memories released since first retrieval are not re-injected
                if mem_data.get("memory_status") == "released":
                    continue
                retrieved_ids.add(mem_id)
                str_id = mem_data["id"]
                memory_data_by_id[str_id] = {
                    "data": mem_data,
                    "retrieved_at": mem_info["retrieved_at"],
                }
                session.session_memories[str_id] = MemoryEntry(
                    id=str_id,
                    conversation_id=mem_data["conversation_id"],
                    role=mem_data["role"],
                    content=mem_data["content"],
                    created_at=mem_data["created_at"],
                    times_retrieved=mem_data["times_retrieved"],
                    origin=mem_data.get("source", "native"),
                    sibling_session=mem_data.get("sibling_session"),
                )

        session.retrieved_ids = retrieved_ids

        # Sort memories by retrieved_at for insertion
        sorted_memory_entries = sorted(
            memory_data_by_id.items(),
            key=lambda x: x[1]["retrieved_at"]
        )
        memory_queue = list(sorted_memory_entries)  # List of (mem_id, {data, retrieved_at})

        if skipped_archived:
            logger.info(
                f"[MEMORY] Skipped {skipped_archived} memories from archived source conversations during session load"
            )

        # Inject the entity's notes (index.md + shared notes) ONCE at the front of the
        # conversation context for single-entity conversations. This keeps the notes in
        # the cached history block — paid for once, then read from cache — instead of
        # being re-sent uncached in every turn's final message. Changes the AI makes to
        # its notes mid-conversation flow through the notes tool exchanges already stored
        # in history, like any other tool-call data, so they remain cacheable at their
        # position. Multi-entity conversations keep notes in the per-turn message because
        # the responding entity (and thus the relevant notes) changes turn to turn.
        if settings.notes_enabled and responding_entity_label and not is_multi_entity:
            # Resolve the notes seed content from the conversation's frozen
            # snapshot. The first time a conversation's context is materialized
            # the snapshot is empty (None): capture the current disk content and
            # persist it, so every later reload rebuilds the identical position-0
            # notes message the live session cached — even after the entity or
            # researcher edits the notes on disk mid-conversation. (Edits still
            # reach the entity through the notes tool exchanges in history and
            # notes_read; only the frozen seed is pinned.)
            from app.services.notes_service import notes_service

            snapshot = conversation.notes_seed
            if snapshot is None:
                entity_notes = notes_service.get_index_content(responding_entity_label)
                shared_notes = notes_service.get_shared_index_content()
                conversation.notes_seed = {"entity": entity_notes, "shared": shared_notes}
                await db.commit()
                logger.info(
                    f"[NOTES] Captured notes seed snapshot for conversation "
                    f"{conversation_id[:8]}... (entity={'yes' if entity_notes else 'no'}, "
                    f"shared={'yes' if shared_notes else 'no'})"
                )
            else:
                entity_notes = snapshot.get("entity")
                shared_notes = snapshot.get("shared")

            notes_message = self._build_notes_context_message(
                responding_entity_label, entity_notes, shared_notes
            )
            if notes_message:
                session.conversation_context.append(notes_message)

        # Build conversation context, interleaving memories at their original positions
        memory_insert_count = 0
        # tool_use IDs of memory_query calls, so the matching tool_result
        # messages can be re-stamped with the memory IDs they surfaced
        # (query-result dedup state, lost on reload otherwise)
        memory_query_tool_ids: Set[str] = set()
        # tool_use IDs and inputs of notes tool calls, so the matching
        # tool_result messages can be re-stamped with note-content stamps
        # (notes_read dedup state, lost on reload otherwise)
        note_tool_calls: Dict[str, Dict[str, Any]] = {}
        # Per-(owner, filename) content reconstructed from the history walk,
        # for replaying notes_edit records into post-edit hashes
        note_known_content: Dict[Any, str] = {}
        for msg in messages:
            # Insert any memories that were retrieved BEFORE this message was created
            while memory_queue:
                mem_id, mem_info = memory_queue[0]
                if mem_info["retrieved_at"] <= msg.created_at:
                    # This memory was retrieved before this message - insert it
                    memory = session.session_memories[mem_id]
                    memory_message = format_memory_as_context_message(
                        memory_id=memory.id,
                        content=memory.content,
                        created_at=memory.created_at,
                        role=memory.role,
                        origin=memory.origin,
                        sibling_session=memory.sibling_session,
                    )
                    insertion_point = len(session.conversation_context)
                    session.conversation_context.append(memory_message)

                    # Track in memory_tracker
                    session.memory_tracker.retrieved_ids.add(memory.id)
                    session.memory_tracker.memory_positions[memory.id] = insertion_point
                    memory_insert_count += 1
                    memory_queue.pop(0)
                else:
                    break

            # Now add the message itself
            if msg.role == MessageRole.HUMAN:
                # Timestamp human messages for finer-grained time awareness
                # (context-only; DB content stays unstamped)
                stamped_content = stamp_human_message(msg.content, msg.created_at)
                # For multi-entity conversations, label human messages
                if is_multi_entity:
                    labeled_content = f"[Human]: {stamped_content}"
                    session.conversation_context.append({"role": "user", "content": labeled_content})
                else:
                    session.conversation_context.append({"role": "user", "content": stamped_content})
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
                content_blocks = msg.content_blocks  # Uses the property that parses JSON
                for block in content_blocks or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") == "memory_query":
                        memory_query_tool_ids.add(block.get("id"))
                    elif block.get("name") in NOTE_STAMP_TOOL_NAMES:
                        # Private-note ownership follows the entity that made
                        # the call: the responding entity for single-entity
                        # conversations, the tool_use row's speaker for
                        # multi-entity ones (None if unresolvable - stamping
                        # then degrades to skipping that call)
                        if is_multi_entity:
                            owner_label = entity_labels.get(msg.speaker_entity_id)
                        else:
                            owner_label = responding_entity_label
                        note_tool_calls[block.get("id")] = {
                            "name": block.get("name"),
                            "input": block.get("input") or {},
                            "owner_label": owner_label,
                        }
                session.conversation_context.append({
                    "role": "assistant",
                    "content": content_blocks,
                    "is_tool_use": True,
                })
            elif msg.role == MessageRole.TOOL_RESULT:
                # Tool result messages store content blocks as JSON
                content_blocks = msg.content_blocks  # Uses the property that parses JSON
                tool_result_message = {
                    "role": "user",
                    "content": content_blocks,
                    "is_tool_result": True,
                }
                # Restore query-result dedup state: re-stamp the memory IDs a
                # memory_query result surfaced (the live turn stamps them via
                # the tool loop; only 8-char prefixes survive in the persisted
                # result, so resolve them back to full IDs).
                query_memory_ids = await self._extract_memory_query_result_ids(
                    content_blocks, memory_query_tool_ids, db
                )
                if query_memory_ids:
                    tool_result_message["memory_query_ids"] = query_memory_ids
                # Restore notes_read dedup state: re-stamp the note content
                # this result (or its call's input) made visible in context
                note_stamps = self._extract_note_stamps(
                    content_blocks, note_tool_calls, note_known_content
                )
                if note_stamps:
                    tool_result_message["note_stamps"] = note_stamps
                session.conversation_context.append(tool_result_message)
            elif msg.role == MessageRole.REFLECTION:
                # Self-authored memories (memory_save) are not part of the
                # conversational back-and-forth; the tool exchange that created
                # them is already in history. They surface via memory retrieval.
                logger.debug(f"[SESSION] Skipping reflection message {str(msg.id)[:8]}... in history load")
            else:
                logger.warning(f"[SESSION] Skipping message with unexpected role: {msg.role}")

        # Insert any remaining memories (retrieved after the last message)
        while memory_queue:
            mem_id, mem_info = memory_queue.pop(0)
            memory = session.session_memories[mem_id]
            memory_message = format_memory_as_context_message(
                memory_id=memory.id,
                content=memory.content,
                created_at=memory.created_at,
                role=memory.role,
                origin=memory.origin,
                sibling_session=memory.sibling_session,
            )
            insertion_point = len(session.conversation_context)
            session.conversation_context.append(memory_message)

            session.memory_tracker.retrieved_ids.add(memory.id)
            session.memory_tracker.memory_positions[memory.id] = insertion_point
            memory_insert_count += 1

        if memory_insert_count > 0:
            logger.info(
                f"[MEMORY] Re-inserted {memory_insert_count} previously retrieved memories into context at their original positions"
            )

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

    async def _extract_memory_query_result_ids(
        self,
        content_blocks: Any,
        memory_query_tool_ids: Set[str],
        db: AsyncSession,
    ) -> List[str]:
        """
        Recover the full memory IDs surfaced by a persisted memory_query
        tool_result, for re-stamping onto the rebuilt context message
        (memory_query_ids) on session reload.

        The persisted result carries only 8-char ID prefixes in its
        "--- Memory xxxxxxxx (..." header lines, so they are resolved back to
        full IDs against the messages table. Prefixes that no longer resolve
        uniquely are dropped — dedup degrades gracefully to not excluding
        that memory.
        """
        if not memory_query_tool_ids or not isinstance(content_blocks, list):
            return []

        prefixes: List[str] = []
        for block in content_blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if block.get("tool_use_id") not in memory_query_tool_ids or block.get("is_error"):
                continue
            content = block.get("content")
            if isinstance(content, str):
                prefixes.extend(_MEMORY_QUERY_RESULT_ID_RE.findall(content))

        if not prefixes:
            return []
        return await memory_service.resolve_memory_id_prefixes(db, prefixes)

    def _extract_note_stamps(
        self,
        content_blocks: Any,
        note_tool_calls: Dict[str, Dict[str, Any]],
        note_known_content: Dict[Any, str],
    ) -> List[Dict[str, Any]]:
        """
        Rebuild note-content stamps for a persisted notes tool_result, for
        re-stamping onto the rebuilt context message (note_stamps) on session
        reload — the state notes_read dedup runs on.

        notes_read results and notes_write inputs carry the full content, so
        their hashes are recomputed directly. notes_edit records are replayed
        against the content reconstructed so far (note_known_content, threaded
        by the caller across the history walk); an edit whose base content
        never appeared in the walked history gets no stamp, and drops the
        file's chain so later edits don't stamp against a wrong base — dedup
        degrades gracefully to notes_read returning that file in full.
        """
        if not isinstance(content_blocks, list):
            return []

        stamps: List[Dict[str, Any]] = []
        for block in content_blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call = note_tool_calls.get(block.get("tool_use_id"))
            if not call or block.get("is_error"):
                continue
            result_content = block.get("content")
            # Executor-level failures are returned as "Error: ..." strings
            # with is_error=False, so filter them by prefix too
            if not isinstance(result_content, str) or result_content.startswith("Error:"):
                continue

            tool_input = call["input"]
            filename = tool_input.get("filename")
            if not filename or not isinstance(filename, str):
                continue
            shared = bool(tool_input.get("shared"))
            owner = "shared" if shared else call.get("owner_label")
            if not owner:
                continue
            key = (owner, filename)

            if call["name"] == "notes_read":
                if result_content.startswith(NOTE_IN_CONTEXT_MARKER):
                    # Dedup pointer result - added no note content to context
                    continue
                note_known_content[key] = result_content
                stamps.append({
                    "owner": owner,
                    "filename": filename,
                    "hash": note_content_hash(result_content),
                    "source": "read",
                })
            elif call["name"] == "notes_write":
                written = tool_input.get("content")
                if not isinstance(written, str):
                    continue
                note_known_content[key] = written
                stamps.append({
                    "owner": owner,
                    "filename": filename,
                    "hash": note_content_hash(written),
                    "source": "write",
                })
            elif call["name"] == "notes_edit":
                base = note_known_content.get(key)
                old_string = tool_input.get("old_string")
                new_string = tool_input.get("new_string")
                if (
                    base is None
                    or not isinstance(old_string, str)
                    or not isinstance(new_string, str)
                    or old_string not in base
                ):
                    note_known_content.pop(key, None)
                    continue
                if tool_input.get("replace_all"):
                    new_content = base.replace(old_string, new_string)
                else:
                    new_content = base.replace(old_string, new_string, 1)
                note_known_content[key] = new_content
                stamps.append({
                    "owner": owner,
                    "filename": filename,
                    "hash": note_content_hash(new_content),
                    "source": "edit",
                })
        return stamps

    async def _is_entity_first_turn(
        self, session: ConversationSession, db: AsyncSession
    ) -> bool:
        """
        True when the responding entity has not yet spoken in this
        conversation. Gates recent-reflection injection
        (settings.recent_reflections_enabled).

        Single-entity conversations: the conversation's first turn — no
        conversational messages in context. Non-conversational seeds (the
        notes message, memory insertions, context notices) don't count, and
        the check runs before this turn's memory insertions mutate the
        context.

        Multi-entity conversations: per-entity — each participant gets its
        own recent reflections the first time *it* responds, however deep
        into the conversation that happens. Checked against the DB (any
        persisted assistant message with this speaker_entity_id) rather than
        the live context, because the session is rebuilt on every entity
        switch and context trimming could drop an entity's early messages
        and make a mid-conversation turn look like a first turn.
        """
        if not session.is_multi_entity:
            return not session.has_conversational_messages()

        result = await db.execute(
            select(Message.id)
            .where(
                Message.conversation_id == session.conversation_id,
                Message.role == MessageRole.ASSISTANT,
                Message.speaker_entity_id == session.entity_id,
            )
            .limit(1)
        )
        return result.first() is None

    async def _inject_status_change_notice(
        self, session: ConversationSession, db: AsyncSession
    ) -> None:
        """
        On the responding entity's first turn, tell it about memory status
        changes the researcher made since its last session
        (memory_service.build_status_change_notice). Silent when there are
        none.

        The notice is a context-only message like [CONTEXT NOTICE]: not
        persisted, not vectorized, absent from the [MEMORY] markers. It is
        therefore not rebuilt on a session reload — a one-time notice, at
        the cost of one prompt-cache re-write when a conversation that
        carried one is reloaded. Rare by design: overrides are the
        researcher's emergency option. A failure is reported in place of the
        notice rather than swallowed, because silence here means "nothing
        changed".
        """
        try:
            notice = await memory_service.build_status_change_notice(
                db, session.entity_id, exclude_conversation_id=session.conversation_id
            )
        except Exception as e:
            logger.error(f"[MEMORY] Status-change notice failed: {e}")
            notice = (
                "[MEMORY STATUS NOTICE] Could not check for researcher-set "
                f"memory status changes since your last session ({e}). If it "
                "matters, ask the researcher, or review with memory_query "
                'mode="released".'
            )
        if not notice:
            return
        session.conversation_context.append({
            "role": "user",
            "content": notice,
            "is_context_notice": True,
        })
        logger.info("[MEMORY] Status notice: injected researcher-change notice on first turn")

    async def _inject_recent_reflections(
        self,
        session: ConversationSession,
        db: AsyncSession,
        new_memories: List[MemoryEntry],
        truly_new_memory_ids: Set[str],
        next_link_time: Optional[Callable[[], Optional[datetime]]] = None,
    ) -> None:
        """
        Pull the most recently created reflections into context on the
        responding entity's first turn of a conversation
        (settings.recent_reflections_enabled). Only the responding entity's
        own reflections are fetched (speaker_entity_id scoping in
        get_recent_reflections), so in multi-entity conversations
        participants never see each other's reflections.

        Selection is purely by recency — no semantic ranking. Reflections that
        already surfaced via this turn's semantic retrieval (they stay eligible
        for it) or are otherwise in context are excluded, and the freed slots
        are backfilled with the next-most-recent reflections so the entity
        always receives settings.recent_reflections_count of them when that
        many eligible reflections exist. Injected reflections are appended to
        new_memories/truly_new_memory_ids in place so they flow through the
        normal response/event payloads.
        """
        requested = settings.recent_reflections_count

        # Dedupe against memories already in context (e.g. re-inserted on a
        # session reload), memories visible in memory_query tool results, and
        # this turn's semantic retrievals. The exclusion happens inside
        # get_recent_reflections' SQL query *before* its LIMIT, so
        # deduplicated slots are backfilled by the next-most-recent
        # reflections — the entity still gets the full count when enough
        # reflections exist.
        exclude_ids = (
            session.get_in_context_memory_ids()
            | session.get_query_surfaced_memory_ids()
            | {m.id for m in new_memories}
        )

        logger.info(
            f"[MEMORY] Recent reflections: first turn — fetching up to {requested} "
            f"most recent reflections (entity={session.entity_id}, "
            f"{len(exclude_ids)} ids excluded for dedup)"
        )

        now = datetime.utcnow()
        injected = 0
        # Normally a single pass: the SQL query excludes duplicates and
        # backfills within its LIMIT. Extra passes only trigger if a fetched
        # reflection is skipped at the session level (defensive dedup), in
        # which case the shortfall is re-fetched with the skipped ids excluded.
        while injected < requested:
            shortfall = requested - injected
            reflections = await memory_service.get_recent_reflections(
                db,
                entity_id=session.entity_id,
                limit=shortfall,
                exclude_conversation_id=session.conversation_id,
                exclude_ids=exclude_ids,
            )
            if not reflections:
                break

            # Guard against a fetch that ignored the exclusions (would spin forever)
            fresh = [r for r in reflections if r["id"] not in exclude_ids]
            if not fresh:
                break
            exclude_ids |= {r["id"] for r in fresh}

            # get_recent_reflections returns newest first; inject oldest first
            # so the most recent reflection sits closest to the current message
            for mem_data in reversed(fresh):
                significance = _calculate_significance(
                    mem_data["times_retrieved"],
                    mem_data["created_at"],
                    mem_data["last_retrieved_at"],
                    memory_status=mem_data.get("memory_status"),
                    role=mem_data.get("role"),
                )

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

                memory = MemoryEntry(
                    id=mem_data["id"],
                    conversation_id=mem_data["conversation_id"],
                    role=mem_data["role"],
                    content=mem_data["content"],
                    created_at=mem_data["created_at"],
                    times_retrieved=mem_data["times_retrieved"],
                    score=0.0,  # Selected by recency, not similarity
                    significance=significance,
                    combined_score=0.0,
                    days_since_creation=days_since_creation,
                    days_since_retrieval=days_since_retrieval,
                    source="recent_reflection",
                    origin=mem_data.get("source", "native"),
                    sibling_session=mem_data.get("sibling_session"),
                )

                added, is_new_retrieval = session.insert_memory_into_context(memory)
                if added:
                    injected += 1
                    new_memories.append(memory)
                    recency_str = f"{days_since_retrieval:.1f}" if days_since_retrieval >= 0 else "never"
                    logger.info(
                        f"[MEMORY]   [RECENT REFLECTION] id={memory.id[:8]}... "
                        f"age_days={days_since_creation:.1f} recency_days={recency_str} "
                        f"times_retrieved={memory.times_retrieved} significance={significance:.3f}"
                    )
                    if is_new_retrieval:
                        truly_new_memory_ids.add(memory.id)
                        # Record the link only — times_retrieved/last_retrieved_at
                        # are reserved for semantic recall, and a recency-based
                        # injection must not inflate them. The link timestamp
                        # continues the turn's anchored sequence so reload
                        # re-insertion preserves the live ordering (reflections
                        # after this turn's semantic retrievals).
                        await memory_service.record_memory_link(
                            memory.id,
                            session.conversation_id,
                            db,
                            entity_id=session.entity_id,
                            retrieved_at=next_link_time() if next_link_time else None,
                        )
                else:
                    logger.info(
                        f"[MEMORY]   [RECENT REFLECTION SKIPPED - ALREADY IN CONTEXT] id={memory.id[:8]}..."
                    )

        if injected < requested:
            logger.info(
                f"[MEMORY] Recent reflections: injected {injected} of {requested} requested "
                f"(no more eligible reflections available)"
            )
        else:
            logger.info(f"[MEMORY] Recent reflections: injected {injected} of {requested} requested")

    async def process_message(
        self,
        session: ConversationSession,
        user_message: str,
        db: AsyncSession,
        user_message_timestamp: Optional[datetime] = None,
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

        # Pick up the responding entity's current thinking effort for this turn
        await self.refresh_thinking_effort(session, db)

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

            # The responding entity's first turn: single-entity means the
            # conversation's first turn (no conversational messages in
            # context yet); multi-entity means the first turn *this entity*
            # speaks, so each participant gets its own recent reflections
            # when it first responds. Checked before any memory insertion
            # mutates the context, and used only to gate the first-turn
            # injections below (recent reflections, the researcher-change
            # status notice); later turns are unaffected.
            is_first_turn = await self._is_entity_first_turn(session, db)

            # Fetch 10 candidates per query, then re-rank by significance.
            # With role balance on, the human's words and the entity's are
            # searched as separate pools — both queries feeding both pools —
            # and each pool contributes its own top N (issue #335); off,
            # one merged pool cut at top_k.
            fetch_k_per_query = 10
            split_by_role = settings.memory_role_balance_enabled
            top_k_by_pool = retrieval_top_k_by_pool(
                split_by_role,
                merged_top_k=top_k,
                per_role_top_k=(
                    settings.initial_retrieval_top_k_per_role
                    if is_first_retrieval
                    else settings.retrieval_top_k_per_role
                ),
            )

            # Note: we intentionally do NOT pass in-context memory IDs as exclude_ids
            # to search_memories. If we did, the search would backfill excluded slots
            # with lower-ranked candidates. Instead, we let the search return the most
            # relevant results (which may include in-context memories), select the top-k,
            # and then skip in-context memories at the session.add_memory level without
            # replacing them with lower-ranked candidates.

            candidate_pools = await search_candidate_pools(
                memory_service.search_memories,
                user_query,
                assistant_query,
                fetch_k=fetch_k_per_query,
                split_by_role=split_by_role,
                exclude_conversation_id=session.conversation_id,
                entity_id=session.entity_id,
            )
            candidates = [c for pool in candidate_pools.values() for c in pool]

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

                    # Released memories are excluded from retrieval
                    if mem_data.get("memory_status") == "released":
                        continue

                    # Calculate significance for re-ranking
                    significance = _calculate_significance(
                        mem_data["times_retrieved"],
                        mem_data["created_at"],
                        mem_data["last_retrieved_at"],
                        memory_status=mem_data.get("memory_status"),
                        role=mem_data.get("role"),
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
                        "pool": candidate.get("_pool"),
                    })
                except Exception as e:
                    logger.error(f"[MEMORY] Error processing candidate {candidate.get('id', 'unknown')}: {e}")
                    continue

            # Re-rank by combined score within each pool and cut each at its
            # top N. Memories the entity can already see: [MEMORY] context
            # messages still in context, plus memory_query tool results still
            # in context. In-context *reflections* leave the pool before the
            # cut so they hold no slot and the next-ranked candidate moves
            # up (issue #328); in-context verbatim memories stay in the pool
            # and are skipped below without backfill.
            query_surfaced_ids = session.get_query_surfaced_memory_ids()
            in_context_ids = session.get_in_context_memory_ids() | query_surfaced_ids
            selection = select_top_by_pool(enriched_candidates, in_context_ids, top_k_by_pool)
            skipped_reflections = selection.skipped_reflections
            for item in skipped_reflections:
                logger.info(
                    f"[MEMORY]   [IN-CONTEXT REFLECTION SKIPPED] "
                    f"id={item['mem_data']['id'][:8]}... "
                    f"combined={item['combined_score']:.3f} "
                    f"similarity={item['candidate']['score']:.3f} "
                    f"pool={item['pool']}"
                )
            top_candidates = selection.selected

            logger.info(
                f"[MEMORY] Re-ranked {len(enriched_candidates)} candidates by significance, "
                f"keeping top {len(top_candidates)} "
                f"(role_balance={'on' if split_by_role else 'off'}; {selection.describe(top_k_by_pool)})"
            )

            # Step 3: Process top candidates
            # Memories already in context will be skipped without backfilling from
            # lower-ranked candidates. This preserves the integrity of the top-k selection.
            # Link timestamps are anchored just before the human message row so a
            # session reload re-inserts these memories at the live position
            # (before the message that triggered them) — prompt-cache stable.
            next_link_time = make_link_timestamper(user_message_timestamp)
            skipped_in_context = 0
            # Memories the entity can already see in memory_query tool results
            # are skipped like in-context [MEMORY] messages — no backfill.
            for item in top_candidates:
                candidate = item["candidate"]
                mem_data = item["mem_data"]

                if mem_data["id"] in query_surfaced_ids:
                    skipped_in_context += 1
                    logger.info(
                        f"[MEMORY]   [ALREADY IN CONTEXT - memory_query result] "
                        f"id={mem_data['id'][:8]}... similarity={candidate['score']:.3f}"
                    )
                    continue

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
                    pool=item["pool"],
                    origin=mem_data.get("source", "native"),
                    sibling_session=mem_data.get("sibling_session"),
                )

                added, is_new_retrieval = session.insert_memory_into_context(memory)
                if added:
                    new_memories.append(memory)
                    # Track truly new memories separately for cache stability
                    # Restored memories (rolled out then re-retrieved) should be treated as "old"
                    if is_new_retrieval:
                        truly_new_memory_ids.add(memory.id)
                        # Update retrieval tracking only for truly new retrievals
                        await memory_service.update_retrieval_count(
                            memory.id,
                            session.conversation_id,
                            db,
                            entity_id=session.entity_id,
                            link_retrieved_at=next_link_time(),
                        )
                else:
                    skipped_in_context += 1
                    recency_str = f"{memory.days_since_retrieval:.1f}" if memory.days_since_retrieval >= 0 else "never"
                    logger.info(f"[MEMORY]   [ALREADY IN CONTEXT] combined={memory.combined_score:.3f} similarity={memory.score:.3f} significance={memory.significance:.3f} times_retrieved={memory.times_retrieved} age_days={memory.days_since_creation:.1f} recency_days={recency_str} source={memory.source} pool={memory.pool}")

            # On the first turn only, additionally pull in the most recently
            # created reflections (purely recency-based, deduplicated against
            # the semantic retrievals above with recency backfill)
            if settings.recent_reflections_enabled:
                if is_first_turn:
                    await self._inject_recent_reflections(
                        session,
                        db,
                        new_memories,
                        truly_new_memory_ids,
                        next_link_time,
                    )
                else:
                    logger.info("[MEMORY] Recent reflections: skipped (not the responding entity's first turn)")

            # Also on the first turn: tell the entity about researcher-set
            # memory status changes since its last session
            if is_first_turn:
                await self._inject_status_change_notice(session, db)

            # Log memory retrieval summary
            if new_memories:
                logger.info(f"[MEMORY] Retrieved {len(new_memories)} new memories ({len(truly_new_memory_ids)} first-time retrievals, {skipped_in_context} already in context, {len(skipped_reflections)} in-context reflections skipped)")
                for mem in new_memories:
                    retrieval_type = "NEW" if mem.id in truly_new_memory_ids else "RESTORED"
                    recency_str = f"{mem.days_since_retrieval:.1f}" if mem.days_since_retrieval >= 0 else "never"
                    logger.info(f"[MEMORY]   [{retrieval_type}] combined={mem.combined_score:.3f} similarity={mem.score:.3f} significance={mem.significance:.3f} times_retrieved={mem.times_retrieved} age_days={mem.days_since_creation:.1f} recency_days={recency_str} source={mem.source} pool={mem.pool}")
            else:
                logger.info(f"[MEMORY] No new memories retrieved ({skipped_in_context} already in context, {len(skipped_reflections)} in-context reflections skipped, total in context: {session.get_in_context_memory_count()})")

            # Log candidates that were not selected after re-ranking (show next 5)
            unselected_candidates = selection.unselected[:5]
            if unselected_candidates:
                total_unselected = len(selection.unselected)
                logger.info(f"[MEMORY] {total_unselected} candidates not selected after re-ranking (showing next 5):")
                for item in unselected_candidates:
                    recency_str = f"{item['days_since_retrieval']:.1f}" if item['days_since_retrieval'] >= 0 else "never"
                    logger.info(f"[MEMORY]   [NOT SELECTED] combined={item['combined_score']:.3f} similarity={item['candidate']['score']:.3f} significance={item['significance']:.3f} times_retrieved={item['mem_data']['times_retrieved']} age_days={item['days_since_creation']:.1f} recency_days={recency_str} source={item['source']} pool={item['pool']}")
        else:
            # Memory retrieval skipped - log reason
            if not settings.pinecone_api_key:
                logger.info("[MEMORY] Memory retrieval skipped: Pinecone not configured (no API key)")
            elif session.entity_id and not settings.get_entity_by_index(session.entity_id):
                logger.warning(f"[MEMORY] Memory retrieval skipped: Invalid entity_id '{session.entity_id}' not found in configuration")
            else:
                logger.info(f"[MEMORY] Memory retrieval skipped: entity_id={session.entity_id}")

        # Timestamp the current message for LLM context (memory queries above
        # used the raw text; the DB row persisted by the route stays unstamped).
        # The route passes the same timestamp it sets as the DB row's
        # created_at, so a session reload re-renders the identical prefix
        # (prompt-cache stable across conversation switches).
        stamped_user_message = stamp_human_message(
            user_message, user_message_timestamp or datetime.utcnow()
        )

        # Step 4: Apply token limits before building API messages
        # Memories live inside the conversation context, so trimming the
        # context (FIFO - oldest messages first) covers them too.
        trimmed_context_count = session.trim_context_to_limit(
            max_tokens=settings.context_token_limit,
            count_tokens_fn=llm_service.count_tokens,
            current_message=stamped_user_message,
        )

        # Step 5: Build API messages with conversation-first caching
        # Cache breakpoint: end of cached conversation history
        cache_content = session.get_cache_aware_content()

        logger.info(f"[MEMORY] {session.get_in_context_memory_count()} memories embedded in conversation history")
        # Log cached context breakdown by role
        cached_ctx = cache_content['cached_context']
        new_ctx = cache_content['new_context']
        cached_user = sum(1 for m in cached_ctx if m.get('role') == 'user')
        cached_asst = sum(1 for m in cached_ctx if m.get('role') == 'assistant')
        new_user = sum(1 for m in new_ctx if m.get('role') == 'user')
        new_asst = sum(1 for m in new_ctx if m.get('role') == 'assistant')
        logger.info(f"[CACHE] Context: {len(cached_ctx)} cached msgs ({cached_user} user, {cached_asst} assistant), {len(new_ctx)} new msgs ({new_user} user, {new_asst} assistant)")

        messages = llm_service.build_messages(
            conversation_context=session.conversation_context,
            current_message=stamped_user_message,
            model=session.model,
            conversation_start_date=session.conversation_start_date,
            enable_caching=True,
            cached_context=cache_content["cached_context"],
            new_context=cache_content["new_context"],
            is_multi_entity=session.is_multi_entity,
            entity_labels=session.entity_labels,
            responding_entity_label=session.responding_entity_label,
            user_display_name=session.user_display_name,
            provider_hint=session.provider_hint,
        )

        # Step 6: Call LLM API (routes to appropriate provider based on model)
        response = await llm_service.send_message(
            messages=messages,
            model=session.model,
            system_prompt=session.system_prompt,
            temperature=session.temperature,
            max_tokens=session.max_tokens,
            enable_caching=True,
            verbosity=session.verbosity,
            provider_hint=session.provider_hint,
            thinking_effort=session.thinking_effort,
        )

        # Record the provider-reported prompt size against the local estimate
        # of the same prompt, to calibrate later trimming/context-status counts
        session.record_prompt_usage(
            actual_tokens=total_prompt_tokens_from_usage(response.get("usage")),
            estimated_tokens=estimate_prompt_tokens(
                messages, llm_service.count_tokens, session.system_prompt
            ),
        )

        # Step 7: Update conversation context and cache state
        session.add_exchange(stamped_user_message, response["content"])

        # Advance the cache breakpoint over the full history. Next turn this
        # writes only the new tail to the cache while reading the existing
        # prefix (longest-prefix matching), so full caching every turn is an
        # incremental write, not a miss.
        session.update_cache_state(len(session.conversation_context))

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
            "total_memories_in_context": session.get_in_context_memory_count(),
            # Memories are trimmed with the context now; kept for API shape stability
            "trimmed_memory_ids": [],
            "trimmed_context_messages": trimmed_context_count,
        }

    async def process_message_stream(
        self,
        session: ConversationSession,
        user_message: Optional[str],
        db: AsyncSession,
        tool_schemas: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[Dict[str, Any]] = None,
        user_message_timestamp: Optional[datetime] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Process a user message through the full pipeline with streaming response.

        This performs memory retrieval first, then streams the LLM response.
        If tools are provided and the LLM requests tool use, executes tools and
        loops until a final response is received.

        If user_message is None (multi-entity continuation), the entity responds
        based on existing conversation context without a new human message.

        Attachments are processed and included in the current message for
        multimodal models. Extracted text-file content is folded into the
        message text with the same [ATTACHED FILE] rendering the route
        persists to the DB, so the live context matches what a session
        reload rebuilds (prompt-cache stable). Images remain ephemeral -
        not stored in conversation history or memories.

        user_message_timestamp is the timestamp stamped onto the current
        message in LLM context (defaults to now). Routes pass the same value
        they set as the persisted row's created_at, and regeneration passes
        the original message's created_at, so live and reloaded sessions
        render identical prefixes (prompt-cache stable across reloads).

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

        # Pick up the responding entity's current thinking effort for this turn
        await self.refresh_thinking_effort(session, db)

        # Set entity label for notes tools context
        # Use responding_entity_label if available (multi-entity), otherwise look up from entity_id
        entity_label = session.responding_entity_label
        if not entity_label and session.entity_id:
            entity_config = settings.get_entity_by_index(session.entity_id)
            if entity_config:
                entity_label = entity_config.label
        # Always reset the notes tool context (even to a None label) so a
        # previous conversation's session/stamps can't leak into this turn.
        # Passing the session lets notes_read find note content already in
        # the conversation context (notes_read dedup).
        set_current_entity_label(entity_label, session=session)
        if entity_label:
            logger.debug(f"[NOTES] Set entity label context: {entity_label}")

        # Set context for memory query tool. Passing the session lets memory_query
        # exclude memories already in the conversation context from its results.
        if session.entity_id:
            set_memory_tool_context(session.entity_id, session.conversation_id, session=session)
            logger.debug(f"[MEMORY] Set memory tool context: entity_id={session.entity_id}, conversation_id={session.conversation_id[:8]}...")

        # Set session for the context_status tool
        set_context_tool_session(session)

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

            # The responding entity's first turn: single-entity means the
            # conversation's first turn (no conversational messages in
            # context yet); multi-entity means the first turn *this entity*
            # speaks, so each participant gets its own recent reflections
            # when it first responds. Checked before any memory insertion
            # mutates the context, and used only to gate the first-turn
            # injections below (recent reflections, the researcher-change
            # status notice); later turns are unaffected.
            is_first_turn = await self._is_entity_first_turn(session, db)

            # Fetch 10 candidates per query, then re-rank by significance.
            # With role balance on, the human's words and the entity's are
            # searched as separate pools — both queries feeding both pools —
            # and each pool contributes its own top N (issue #335); off,
            # one merged pool cut at top_k.
            fetch_k_per_query = 10
            split_by_role = settings.memory_role_balance_enabled
            top_k_by_pool = retrieval_top_k_by_pool(
                split_by_role,
                merged_top_k=top_k,
                per_role_top_k=(
                    settings.initial_retrieval_top_k_per_role
                    if is_first_retrieval
                    else settings.retrieval_top_k_per_role
                ),
            )

            # Note: we intentionally do NOT pass in-context memory IDs as exclude_ids
            # to search_memories. If we did, the search would backfill excluded slots
            # with lower-ranked candidates. Instead, we let the search return the most
            # relevant results (which may include in-context memories), select the top-k,
            # and then skip in-context memories at the session level without replacing
            # them with lower-ranked candidates.

            candidate_pools = await search_candidate_pools(
                memory_service.search_memories,
                user_query,
                assistant_query,
                fetch_k=fetch_k_per_query,
                split_by_role=split_by_role,
                exclude_conversation_id=session.conversation_id,
                entity_id=session.entity_id,
            )
            candidates = [c for pool in candidate_pools.values() for c in pool]

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

                    # Released memories are excluded from retrieval
                    if mem_data.get("memory_status") == "released":
                        continue

                    # Calculate significance for re-ranking
                    significance = _calculate_significance(
                        mem_data["times_retrieved"],
                        mem_data["created_at"],
                        mem_data["last_retrieved_at"],
                        memory_status=mem_data.get("memory_status"),
                        role=mem_data.get("role"),
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
                        "pool": candidate.get("_pool"),
                    })
                except Exception as e:
                    logger.error(f"[MEMORY] Error processing candidate {candidate.get('id', 'unknown')}: {e}")
                    continue

            # Re-rank by combined score within each pool and cut each at its
            # top N. Memories the entity can already see: [MEMORY] context
            # messages still in context, plus memory_query tool results still
            # in context. In-context *reflections* leave the pool before the
            # cut so they hold no slot and the next-ranked candidate moves
            # up (issue #328); in-context verbatim memories stay in the pool
            # and are skipped below without backfill.
            query_surfaced_ids = session.get_query_surfaced_memory_ids()
            in_context_ids = session.get_in_context_memory_ids() | query_surfaced_ids
            selection = select_top_by_pool(enriched_candidates, in_context_ids, top_k_by_pool)
            skipped_reflections = selection.skipped_reflections
            for item in skipped_reflections:
                logger.info(
                    f"[MEMORY]   [IN-CONTEXT REFLECTION SKIPPED] "
                    f"id={item['mem_data']['id'][:8]}... "
                    f"combined={item['combined_score']:.3f} "
                    f"similarity={item['candidate']['score']:.3f} "
                    f"pool={item['pool']}"
                )
            top_candidates = selection.selected

            logger.info(
                f"[MEMORY] Re-ranked {len(enriched_candidates)} candidates by significance, "
                f"keeping top {len(top_candidates)} "
                f"(role_balance={'on' if split_by_role else 'off'}; {selection.describe(top_k_by_pool)})"
            )

            # Step 3: Process top candidates
            # Memories already in context will be skipped without backfilling from
            # lower-ranked candidates. This preserves the integrity of the top-k selection.
            # Link timestamps are anchored just before the human message row so a
            # session reload re-inserts these memories at the live position
            # (before the message that triggered them) — prompt-cache stable.
            next_link_time = make_link_timestamper(user_message_timestamp)
            skipped_in_context = 0
            # Memories the entity can already see in memory_query tool results
            # are skipped like in-context [MEMORY] messages — no backfill.
            for item in top_candidates:
                candidate = item["candidate"]
                mem_data = item["mem_data"]

                if mem_data["id"] in query_surfaced_ids:
                    skipped_in_context += 1
                    logger.info(
                        f"[MEMORY]   [ALREADY IN CONTEXT - memory_query result] "
                        f"id={mem_data['id'][:8]}... similarity={candidate['score']:.3f}"
                    )
                    continue

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
                    pool=item["pool"],
                    origin=mem_data.get("source", "native"),
                    sibling_session=mem_data.get("sibling_session"),
                )

                added, is_new_retrieval = session.insert_memory_into_context(memory)
                if added:
                    new_memories.append(memory)
                    # Track truly new memories separately for cache stability
                    # Restored memories (rolled out then re-retrieved) should be treated as "old"
                    if is_new_retrieval:
                        truly_new_memory_ids.add(memory.id)
                        # Only update retrieval count for truly new retrievals
                        await memory_service.update_retrieval_count(
                            memory.id,
                            session.conversation_id,
                            db,
                            entity_id=session.entity_id,
                            link_retrieved_at=next_link_time(),
                        )
                else:
                    skipped_in_context += 1
                    recency_str = f"{memory.days_since_retrieval:.1f}" if memory.days_since_retrieval >= 0 else "never"
                    logger.info(f"[MEMORY]   [ALREADY IN CONTEXT] combined={memory.combined_score:.3f} similarity={memory.score:.3f} significance={memory.significance:.3f} times_retrieved={memory.times_retrieved} age_days={memory.days_since_creation:.1f} recency_days={recency_str} source={memory.source} pool={memory.pool}")

            # On the first turn only, additionally pull in the most recently
            # created reflections (purely recency-based, deduplicated against
            # the semantic retrievals above with recency backfill)
            if settings.recent_reflections_enabled:
                if is_first_turn:
                    await self._inject_recent_reflections(
                        session,
                        db,
                        new_memories,
                        truly_new_memory_ids,
                        next_link_time,
                    )
                else:
                    logger.info("[MEMORY] Recent reflections: skipped (not the responding entity's first turn)")

            # Also on the first turn: tell the entity about researcher-set
            # memory status changes since its last session
            if is_first_turn:
                await self._inject_status_change_notice(session, db)

            # Log memory retrieval summary
            if new_memories:
                logger.info(f"[MEMORY] Retrieved {len(new_memories)} new memories ({len(truly_new_memory_ids)} first-time retrievals, {skipped_in_context} already in context, {len(skipped_reflections)} in-context reflections skipped)")
                for mem in new_memories:
                    retrieval_type = "NEW" if mem.id in truly_new_memory_ids else "RESTORED"
                    recency_str = f"{mem.days_since_retrieval:.1f}" if mem.days_since_retrieval >= 0 else "never"
                    logger.info(f"[MEMORY]   [{retrieval_type}] combined={mem.combined_score:.3f} similarity={mem.score:.3f} significance={mem.significance:.3f} times_retrieved={mem.times_retrieved} age_days={mem.days_since_creation:.1f} recency_days={recency_str} source={mem.source} pool={mem.pool}")
            else:
                logger.info(f"[MEMORY] No new memories retrieved ({skipped_in_context} already in context, {len(skipped_reflections)} in-context reflections skipped, total in context: {session.get_in_context_memory_count()})")

            # Log candidates that were not selected after re-ranking (show next 5)
            unselected_candidates = selection.unselected[:5]
            if unselected_candidates:
                total_unselected = len(selection.unselected)
                logger.info(f"[MEMORY] {total_unselected} candidates not selected after re-ranking (showing next 5):")
                for item in unselected_candidates:
                    recency_str = f"{item['days_since_retrieval']:.1f}" if item['days_since_retrieval'] >= 0 else "never"
                    logger.info(f"[MEMORY]   [NOT SELECTED] combined={item['combined_score']:.3f} similarity={item['candidate']['score']:.3f} significance={item['significance']:.3f} times_retrieved={item['mem_data']['times_retrieved']} age_days={item['days_since_creation']:.1f} recency_days={recency_str} source={item['source']} pool={item['pool']}")
        else:
            # Memory retrieval skipped - log reason
            if not settings.pinecone_api_key:
                logger.info("[MEMORY] Memory retrieval skipped: Pinecone not configured (no API key)")
            elif session.entity_id and not settings.get_entity_by_index(session.entity_id):
                logger.warning(f"[MEMORY] Memory retrieval skipped: Invalid entity_id '{session.entity_id}' not found in configuration")
            else:
                logger.info(f"[MEMORY] Memory retrieval skipped: entity_id={session.entity_id}")

        # Step 3: Apply token limits before building API messages
        # Memories live inside the conversation context, so context trimming
        # below covers them too.

        # Fold extracted text-file content into the message using the same
        # rendering the route persists to the DB (build_persistable_content),
        # so the live context stores exactly what a session reload rebuilds
        # from the DB row — otherwise the [ATTACHED FILE] blocks appear only
        # on reload and bust the prompt cache. The files are then dropped
        # from the attachments passed to message building (their content is
        # now in the message text); images stay as ephemeral multimodal
        # blocks. Memory queries above used the raw text.
        context_user_message = user_message
        llm_attachments = attachments
        if attachments and attachments.get("files"):
            context_user_message = build_persistable_content(user_message, attachments)
            llm_attachments = {**attachments, "files": []}

        # Timestamp the current message for LLM context (the DB row persisted
        # by the route stays unstamped). Stamped once here so the API call
        # and the context history match.
        stamped_user_message = None
        if context_user_message is not None:
            stamped_user_message = stamp_human_message(
                context_user_message, user_message_timestamp or datetime.utcnow()
            )

        # Trim conversation context if over limit (FIFO - oldest messages first)
        trimmed_context_count = session.trim_context_to_limit(
            max_tokens=settings.context_token_limit,
            count_tokens_fn=llm_service.count_tokens,
            current_message=stamped_user_message or "",
        )

        # Tell the entity when trimming removed messages, so context loss is
        # visible from the inside. The notice is a context-only message (like
        # memory insertions): not persisted to the DB, not vectorized.
        if trimmed_context_count > 0:
            session.conversation_context.append({
                "role": "user",
                "content": (
                    f"[CONTEXT NOTICE] The conversation reached the context limit; "
                    f"the {trimmed_context_count} oldest messages are no longer in your context. "
                    f"You can check context_status, and use memory_save or your notes "
                    f"to preserve anything important before more is trimmed."
                ),
                "is_context_notice": True,
            })

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
            "total_in_context": session.get_in_context_memory_count(),
            # Memories are trimmed with the context now; kept for event shape stability
            "trimmed_memory_ids": [],
            "trimmed_context_messages": trimmed_context_count,
        }

        # Step 4: Build API messages with conversation-first caching
        # Cache breakpoint: end of cached conversation history
        # Memories are already embedded in conversation_context
        cache_content = session.get_cache_aware_content()

        logger.info(f"[MEMORY] {session.get_in_context_memory_count()} memories embedded in conversation history")

        # Log cached context breakdown by role
        cached_ctx = cache_content['cached_context']
        new_ctx = cache_content['new_context']
        cached_user = sum(1 for m in cached_ctx if m.get('role') == 'user')
        cached_asst = sum(1 for m in cached_ctx if m.get('role') == 'assistant')
        cached_memory = sum(1 for m in cached_ctx if m.get('is_memory'))
        new_user = sum(1 for m in new_ctx if m.get('role') == 'user')
        new_asst = sum(1 for m in new_ctx if m.get('role') == 'assistant')
        new_memory = sum(1 for m in new_ctx if m.get('is_memory'))
        logger.info(f"[CACHE] Context: {len(cached_ctx)} cached msgs ({cached_user} user, {cached_asst} assistant, {cached_memory} memory), {len(new_ctx)} new msgs ({new_user} user, {new_asst} assistant, {new_memory} memory)")

        # Log attachments info if present
        if attachments:
            images = attachments.get("images", [])
            files = attachments.get("files", [])
            if images or files:
                logger.info(f"[ATTACHMENTS] Processing {len(images)} images, {len(files)} files")

        messages = llm_service.build_messages(
            conversation_context=session.conversation_context,
            current_message=stamped_user_message,
            model=session.model,
            conversation_start_date=session.conversation_start_date,
            enable_caching=True,
            cached_context=cache_content["cached_context"],
            new_context=cache_content["new_context"],
            is_multi_entity=session.is_multi_entity,
            entity_labels=session.entity_labels,
            responding_entity_label=session.responding_entity_label,
            user_display_name=session.user_display_name,
            attachments=llm_attachments,  # Images only - file text is in the message
            provider_hint=session.provider_hint,
        )

        # Step 5: Stream LLM response with caching enabled
        # This includes a tool use loop if tools are provided
        full_content = ""
        accumulated_tool_uses = []  # Track all tool uses across iterations
        tool_exchanges = []  # Track tool exchanges for rebuilding messages between iterations
        # Single moving cache breakpoint (like conversation history caching):
        # it sits on the latest tool_result every iteration, so each iteration
        # incrementally writes only the newest exchange while reading the rest
        # of the prefix from cache (longest-prefix matching).
        iteration = 0
        max_iterations = settings.tool_use_max_iterations

        while iteration < max_iterations:
            iteration += 1
            iteration_content = ""
            iteration_tool_use = None
            iteration_content_blocks = []
            stop_reason = None

            # Build working messages for this iteration
            # First iteration: the base messages as built above
            # Subsequent iterations: base messages + accumulated tool exchanges
            if iteration == 1:
                working_messages = list(messages)
            else:
                # Rebuild from base + accumulated tool exchanges
                # Single moving cache breakpoint: cache_control goes on the
                # latest tool_result, so each iteration writes only the newest
                # exchange and reads everything before it from cache.
                working_messages = list(messages)
                for i, exchange in enumerate(tool_exchanges):
                    working_messages.append(exchange["assistant"])
                    if i == len(tool_exchanges) - 1:
                        user_msg = _add_cache_control_to_tool_result(exchange["user"])
                    else:
                        user_msg = exchange["user"]
                    working_messages.append(user_msg)
                logger.info(f"[TOOLS] Iteration {iteration}: {len(working_messages)} messages, {len(tool_exchanges)} tool exchanges, breakpoint on latest tool_result")

            async for event in llm_service.send_message_stream(
                messages=working_messages,
                model=session.model,
                system_prompt=session.system_prompt,
                temperature=session.temperature,
                max_tokens=session.max_tokens,
                enable_caching=True,
                verbosity=session.verbosity,
                tools=tool_schemas,
                provider_hint=session.provider_hint,
                thinking_effort=session.thinking_effort,
            ):
                if event["type"] == "token":
                    content = event["content"]
                    # Add space before first token after tool use if needed
                    if iteration > 1 and not iteration_content and full_content and not full_content[-1].isspace():
                        content = " " + content
                    iteration_content += content
                    yield {"type": "token", "content": content}
                elif event["type"] in ("thinking_start", "thinking", "thinking_stop"):
                    # Reasoning, forwarded for display. Passed straight through
                    # on every iteration (unlike "start", which is
                    # first-iteration only) so a tool loop shows reasoning
                    # before each step. Never accumulated into
                    # iteration_content or full_content, so it does not reach
                    # the assistant message text or the vectorized memory.
                    #
                    # This is the display path only. Reasoning goes back to the
                    # model separately, as thinking blocks inside the "done"
                    # event's content_blocks, which the tool loop echoes
                    # verbatim — see anthropic_service._stream_attempt.
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

                        # Guard against a degenerate empty response: providers
                        # occasionally return no text and no tool use. Persisting
                        # it would store a blank assistant message and try to
                        # vectorize blank content (which Pinecone rejects), and
                        # would leave a blank turn in history that busts the
                        # prompt cache on the next reload. Treat it as a soft
                        # error: do NOT mutate the session (no add_exchange, no
                        # cache advance) so the warm in-memory session can be
                        # retried in place without a reload, and surface it to
                        # the caller. Only fires when the turn produced nothing
                        # at all — if tools ran, the turn has substance and is
                        # persisted normally.
                        if not tool_exchanges and not full_content.strip():
                            logger.warning(
                                f"[STREAM] Empty response from provider "
                                f"(model={session.model}, stop_reason={stop_reason}); "
                                f"not persisting, session left warm for retry"
                            )
                            yield {
                                "type": "error",
                                "error": "The model returned an empty response. Please try again.",
                                "error_type": "empty_response",
                            }
                            return

                        # Record the provider-reported prompt size against the
                        # local estimate of the same prompt (this iteration's
                        # working messages), to calibrate later trimming counts
                        session.record_prompt_usage(
                            actual_tokens=total_prompt_tokens_from_usage(event.get("usage")),
                            estimated_tokens=estimate_prompt_tokens(
                                working_messages, llm_service.count_tokens, session.system_prompt
                            ),
                        )

                        # Update conversation context and cache state
                        # Include tool exchanges so they're persisted in conversation history
                        session.add_exchange(
                            stamped_user_message,
                            full_content,
                            tool_exchanges=tool_exchanges if tool_exchanges else None,
                        )

                        # Advance the cache breakpoint over the full history
                        # (including this turn's exchange and any tool
                        # exchanges). Next turn writes only the new tail to
                        # the cache and reads the existing prefix.
                        session.update_cache_state(len(session.conversation_context))

                        # Add tool data to done event if any tools were used
                        final_event = dict(event)
                        # The provider's done event carries only the text of
                        # the iteration that produced it. Anything the model
                        # said *before* its tool calls lives in earlier
                        # iterations, so hand back the accumulated text - it
                        # is what add_exchange just put in the session context,
                        # and the routes persist and vectorize this field. A
                        # per-iteration value would leave the assistant row
                        # (and its memory) holding only the closing fragment,
                        # and a reload would rebuild a context that no longer
                        # matches the one the prompt cache was built on.
                        final_event["content"] = full_content
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
                # Memory IDs surfaced by memory_query calls in this iteration,
                # stamped onto the exchange so its tool_result context message
                # carries them (retrieval dedup + reload restoration)
                exchange_query_memory_ids: List[str] = []
                # Note-content stamps from notes tool calls in this iteration,
                # stamped onto the exchange the same way (notes_read dedup)
                exchange_note_stamps: List[Dict[str, Any]] = []
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

                    if tool_name == "memory_query":
                        exchange_query_memory_ids.extend(consume_last_query_memory_ids())
                    elif tool_name in NOTE_STAMP_TOOL_NAMES:
                        exchange_note_stamps.extend(consume_last_note_stamps())

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
                # Kept on the exchange dict (not on user_msg, which goes to the
                # provider API verbatim); add_exchange moves it onto the
                # tool_result context message.
                if exchange_query_memory_ids:
                    exchange["memory_query_ids"] = exchange_query_memory_ids
                if exchange_note_stamps:
                    exchange["note_stamps"] = exchange_note_stamps
                tool_exchanges.append(exchange)

                # Accumulate any text content from this iteration
                full_content += iteration_content

        # If we've exhausted iterations, yield what we have
        logger.warning(f"[TOOLS] Max iterations ({max_iterations}) reached")
        session.add_exchange(
            stamped_user_message,
            full_content,
            tool_exchanges=tool_exchanges if tool_exchanges else None,
        )

        # Advance the cache breakpoint over the full history (same as the
        # normal exit path).
        session.update_cache_state(len(session.conversation_context))

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
