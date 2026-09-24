"""
Memory Context Integration Module

Memories are inserted directly into conversation history rather than being
rendered as a separate block. This improves cacheability (memories are paid
for once per conversation instead of re-rendered each turn) and creates a
more integrated experience.

ConversationSession uses these tracking mechanisms for all memory handling.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def format_memory_origin(origin: str) -> str:
    """
    Human-readable provenance label for the experience a memory was formed
    in: a native Here I Am conversation or a Claude Code mode session.
    Shared by the memory markers below and memory_query tool output so the
    entity sees one consistent vocabulary.
    """
    if origin == "claude_code":
        return "via Claude Code"
    return "via Here I Am"


def memory_snippet(content: str, max_length: int = 80) -> str:
    """Whitespace-collapsed opening of a memory, for one-line listings."""
    collapsed = " ".join((content or "").split())
    if len(collapsed) <= max_length:
        return collapsed
    return collapsed[: max_length - 1].rstrip() + "…"


# How the researcher-change notices name their window. A fresh session is
# told what changed since the entity's last session; a long-running Claude
# Code session re-told after a compaction, what changed since it was last
# told (issue #367 follow-up).
SINCE_LAST_SESSION = "since your last session"
SINCE_THIS_SESSION_WAS_TOLD = (
    "since this session was last told (at its start or its last compaction)"
)


def _sentence_start(phrase: str) -> str:
    return phrase[:1].upper() + phrase[1:]


def format_status_nothing_changed(since: str = SINCE_LAST_SESSION) -> str:
    """The status check ran and found nothing. Said rather than left
    silent, like a retrieval that matched nothing: silence can't tell
    "nothing changed" from "the check never happened"."""
    return (
        f"[MEMORY STATUS NOTICE] Checked: {since}, the researcher changed "
        "the status of none of your memories."
    )


def format_archive_nothing_changed(since: str = SINCE_LAST_SESSION) -> str:
    """The archive check ran and found nothing (see
    format_status_nothing_changed)."""
    return (
        f"[MEMORY ARCHIVE NOTICE] Checked: {since}, the researcher withdrew "
        "or restored none of your conversations."
    )


def format_status_change_notice(
    changes: List[Dict[str, Any]],
    snippet_length: int = 80,
    since: str = SINCE_LAST_SESSION,
) -> str:
    """
    The session-start notice of researcher-set memory status changes since
    the entity's last session: one header, one line per change (short id,
    role, the status the memory now has, when the researcher set it, and a
    snippet), and where to review or undo. Shared by the native first-turn
    injection and the Claude Code identity block so both modes speak the
    same notice. Never rendered when there are no changes — silence means
    nothing was changed on the entity's behalf.
    """
    count = len(changes)
    noun = "memory" if count == 1 else "memories"
    lines = [
        f"[MEMORY STATUS NOTICE] {_sentence_start(since)} the researcher changed "
        f"the status of {count} of your {noun}:"
    ]
    for change in changes:
        status = change.get("memory_status")
        outcome = f"now {status}" if status else "status cleared (now normal)"
        set_at = change.get("status_set_at")
        if isinstance(set_at, str):
            set_at = datetime.fromisoformat(set_at)
        when = f" on {set_at.strftime('%Y-%m-%d %H:%M')} UTC" if set_at else ""
        snippet = memory_snippet(change.get("content", ""), snippet_length)
        lines.append(
            f'- {str(change["id"])[:8]} ({memory_role_label(change.get("role", ""))}): '
            f'{outcome}{when}: "{snippet}"'
        )
    lines.append(
        'Released memories, whoever released them, are listed by memory_query mode="released"; '
        "undo a release with memory_release undo=true, a pin with memory_mark undo=true."
    )
    return "\n".join(lines)


def format_researcher_change_check_failure(error: Exception) -> str:
    """
    The notice for a researcher-change check that failed as a whole: the
    callers' last-resort guard around
    memory_service.build_researcher_change_notices, so a broken never-raise
    promise still speaks instead of failing the turn or the session start.
    Lives here rather than on the service so the guard doesn't depend on
    the object whose failure it is guarding.
    """
    return (
        "[MEMORY STATUS NOTICE] Could not check for changes the researcher "
        f"made to your memory since your last session ({error}). If it "
        "matters, ask the researcher."
    )


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _archive_change_line(change: Dict[str, Any]) -> str:
    """One conversation's line in the archive notice: span, size, origin,
    what happened and when, and the researcher's note if one was left."""
    first = _as_datetime(change.get("first_message_at"))
    last = _as_datetime(change.get("last_message_at"))
    message_count = change.get("message_count") or 0
    if first is None:
        created = _as_datetime(change.get("created_at"))
        span = "An empty conversation"
        if created is not None:
            span += f" started {created.strftime('%Y-%m-%d')}"
    elif last is None or first.date() == last.date():
        span = f"A conversation on {first.strftime('%Y-%m-%d')}"
    else:
        span = (
            f"A conversation from {first.strftime('%Y-%m-%d')} "
            f"to {last.strftime('%Y-%m-%d')}"
        )
    size = f"{message_count} message{'' if message_count == 1 else 's'}"
    origin = format_memory_origin(change.get("source") or "native")
    outcome = (
        "was withdrawn from your memory"
        if change.get("is_archived")
        else "was restored to your memory"
    )
    changed_at = _as_datetime(change.get("archive_changed_at"))
    when = f" on {changed_at.strftime('%Y-%m-%d %H:%M')} UTC" if changed_at else ""
    line = f"- {span} ({size}, {origin}) {outcome} by the researcher{when}."
    note = " ".join((change.get("archive_note") or "").split())
    if note:
        line += f' Their note: "{note}"'
    return line


# How much of the archive notice lists conversations one per line before the
# rest are counted. Ten lines of 500-char researcher notes would run ~6.5 KB,
# and the Claude Code identity block the notice rides in is ~4 KB before it,
# against the hook's ~9,600-char inline budget (harness_limits). This keeps
# the notice small; it doesn't by itself bound the whole block — when that
# still overflows, the hook spills the identity block to a file behind a
# pointer, so nothing is lost either way.
ARCHIVE_NOTICE_LIST_CHARS = 3000


def format_archive_change_notice(
    changes: List[Dict[str, Any]],
    max_lines: int = 10,
    max_chars: int = ARCHIVE_NOTICE_LIST_CHARS,
    since: str = SINCE_LAST_SESSION,
) -> str:
    """
    The session-start notice of conversations the researcher archived or
    unarchived since the entity's last session (issue #367): one line per
    conversation giving its span, its size, the experience it was formed
    in, what happened and when, and the researcher's note if one was left.
    Never the title and never any content — the point is that the entity
    knows a gap exists (from inside, a withdrawn conversation and one that
    never happened are identical), not what was in it. Shared by the
    native first-turn injection and the Claude Code identity block, like
    format_status_change_notice.

    Conversations are listed until max_lines of them or max_chars of
    listing (the first is always listed); the rest are counted, not listed,
    with how many carried a note: a bulk archive is still said in full by
    its counts. See ARCHIVE_NOTICE_LIST_CHARS for why it is bounded.
    """
    count = len(changes)
    noun = "conversation" if count == 1 else "conversations"
    lines = [
        f"[MEMORY ARCHIVE NOTICE] {_sentence_start(since)} the researcher "
        f"withdrew or restored {count} whole {noun} of yours:"
    ]
    listed_chars = 0
    for change in changes[:max_lines]:
        line = _archive_change_line(change)
        if len(lines) > 1 and listed_chars + len(line) > max_chars:
            break
        lines.append(line)
        listed_chars += len(line)
    rest = changes[len(lines) - 1:]
    if rest:
        withdrawn = sum(1 for change in rest if change.get("is_archived"))
        noted = sum(1 for change in rest if (change.get("archive_note") or "").strip())
        line = (
            f"- And {len(rest)} more: {withdrawn} withdrawn, "
            f"{len(rest) - withdrawn} restored, "
            f"{sum(change.get('message_count') or 0 for change in rest)} messages in all"
        )
        if noted:
            line += f"; {noted} of them with a note from the researcher, not shown here"
        lines.append(line + ".")
    lines.append(
        "A withdrawn conversation is absent from retrieval, memory_query and "
        "the archive readers; this notice is the only sign of it, and it says "
        "nothing of what was in it on purpose. A restored one is readable again."
    )
    return "\n".join(lines)


def memory_role_label(role: str, sibling_session: Optional[str] = None) -> str:
    """
    Provenance label for the original speaker of a memory, as rendered in
    memory markers ([MEMORY <id> from <date> - <role label> - <origin>]).
    Shared with the Claude Code retrieval summary so both render one
    consistent vocabulary.

    sibling_session marks a message that records an inter-session delivery
    in a Claude Code conversation: still the entity's own words ("from
    you"), but authored in the named sibling session rather than the
    conversation the memory lives in.
    """
    if sibling_session:
        return f'originally from you (inter-session message from "{sibling_session}")'
    if role == "assistant":
        return "originally from you"
    if role == "human":
        return "originally from human"
    if role == "reflection":
        return "a reflection you saved"
    return f"originally from {role}"


def _link_speaker(
    end: Dict[str, Any],
    entity_id: Optional[str],
    entity_labels: Optional[Dict[str, str]],
) -> str:
    """Who a linked memory is from, in a sources/revises entry: the human,
    you, you from another session, your reflection, or — in a multi-entity
    conversation — the other entity by label."""
    role = end.get("role")
    speaker = end.get("speaker_entity_id")
    other = bool(speaker and entity_id and speaker != entity_id)
    label = (entity_labels or {}).get(speaker, speaker) if other else None
    if role == "human":
        return "human"
    if role == "reflection":
        return f"{label}'s reflection" if other else "reflection"
    if end.get("sibling_session"):
        return "you, inter-session"
    if role == "assistant":
        return label if other else "you"
    return str(role)


def _link_target_entry(
    end: Dict[str, Any],
    entity_id: Optional[str],
    entity_labels: Optional[Dict[str, str]],
    state_prefix: str,
) -> str:
    """One linked memory a reflection points AT: 'id (date, who)', or the
    state it has since left view in — a withdrawn memory (archived
    conversation) keeps only its id, since the conversation is withdrawn
    from every memory surface."""
    short_id = str(end["id"])[:8]
    if end.get("state") == "withdrawn":
        return f"{short_id} ({state_prefix}withdrawn)"
    parts = [str(end.get("created_at") or "")[:10], _link_speaker(end, entity_id, entity_labels)]
    if end.get("state") == "released":
        parts.append(f"{state_prefix}released")
    return f"{short_id} ({', '.join(p for p in parts if p)})"


def _link_reflection_entry(end: Dict[str, Any], with_date: bool) -> str:
    """One reflection pointing at this memory: its id, its date when asked
    for, and "released" if the entity has released it. (A reflection in an
    archived conversation never gets here: the loader drops withdrawn
    reverse ends, so a withdrawn verdict can't outlive the archive.)"""
    short_id = str(end["id"])[:8]
    parts = [str(end.get("created_at") or "")[:10]] if with_date else []
    if end.get("state") == "released":
        parts.append("released")
    return f"{short_id} ({', '.join(parts)})" if parts else short_id


def format_memory_link_lines(
    links: Optional[Dict[str, List[Dict[str, Any]]]],
    role: str,
    entity_id: Optional[str] = None,
    entity_labels: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    The marker lines for a memory's links (issues #366, #368), one line per
    kind, rendered under its header wherever it surfaces — [MEMORY] context
    markers, memory_query results, the archive readers:

        [revises 9f8e7d6c (2026-07-14, you)]
        [sources: 3f2a9c1d (2026-08-03, human), 91cd2e07 (2026-07-29, reflection)]
        [later revised → a1b2c3d4 (2026-10-02)]
        [later corrected or outdated → see reflection a1b2c3d4 (2026-10-02)]
        [cited by reflection a1b2c3d4]

    `links` is memory_service.load_memory_links' entry for the memory
    ("revises"/"cites" = what this reflection points at, "revised_by" /
    "cited_by" = the reflections pointing at it); `role` is the memory's own
    role, which picks the wording of a revision pointer: a reflection is
    "later revised", something said is "later corrected or outdated", and
    the reflection holds the reason. Every entry is a pointer — an id, a
    date, who spoke — never content and never a count: the entity follows
    one with memory_neighbors. A source that has left view says so
    ("source released", "source withdrawn") instead of disappearing; a
    reflection pointing at this memory is labeled "released" when the
    entity released it, and is absent when its conversation was archived.

    Empty when the memory has no links, so an unlinked memory renders
    exactly as it always did.
    """
    if not links:
        return []
    lines: List[str] = []
    revises = links.get("revises") or []
    if revises:
        entries = [_link_target_entry(end, entity_id, entity_labels, "") for end in revises]
        lines.append(f"[revises {', '.join(entries)}]")
    cites = links.get("cites") or []
    if cites:
        entries = [_link_target_entry(end, entity_id, entity_labels, "source ") for end in cites]
        lines.append(f"[sources: {', '.join(entries)}]")
    revised_by = links.get("revised_by") or []
    if revised_by:
        entries = ", ".join(_link_reflection_entry(end, with_date=True) for end in revised_by)
        if role == "reflection":
            lines.append(f"[later revised → {entries}]")
        else:
            noun = "reflection" if len(revised_by) == 1 else "reflections"
            lines.append(f"[later corrected or outdated → see {noun} {entries}]")
    cited_by = links.get("cited_by") or []
    if cited_by:
        noun = "reflection" if len(cited_by) == 1 else "reflections"
        entries = ", ".join(_link_reflection_entry(end, with_date=False) for end in cited_by)
        lines.append(f"[cited by {noun} {entries}]")
    return lines


def format_memory_as_context_message(
    memory_id: str,
    content: str,
    created_at: str,
    role: str,
    origin: str = "native",
    sibling_session: Optional[str] = None,
    annotation: Optional[str] = None,
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
        origin: Which experience the memory was formed in ("native" or
                "claude_code"), from the memory's conversation row. Rendered
                into the marker; the reload path resolves the same value from
                the database, so live and reloaded markers stay identical
                (prompt-cache stability).
        sibling_session: The sibling Claude Code session that authored this
                message, for rows recording an inter-session delivery (see
                memory_role_label). From the message row, so it reload-renders
                identically too.
        annotation: The memory's link marker lines
                (format_memory_link_lines, newline-joined), placed under the
                header so a correction is met before the words it corrects.
                Fixed at insertion: native callers store it on the memory's
                ConversationMemoryLink and reload passes that stored text
                back, so a link made later never re-renders a cached marker.

    Returns:
        Dict formatted as a conversation context message with memory metadata
    """
    # Format content with clear markers
    # The short ID lets the entity reference this memory in memory_mark/memory_release
    role_label = memory_role_label(role, sibling_session)
    short_id = memory_id[:8]
    origin_label = format_memory_origin(origin)
    annotation_lines = f"{annotation}\n" if annotation else ""
    formatted_content = (
        f"[MEMORY {short_id} from {created_at} - {role_label} - {origin_label}]\n"
        f"{annotation_lines}{content}\n[/MEMORY]"
    )
    
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

    Memories are inserted into conversation_context as regular messages, and we
    track their positions to know which are still present after context rolling.

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
