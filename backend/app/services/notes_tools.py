"""
Notes Tools - Tool definitions for entity notes management.

These tools allow AI entities to read, write, and manage their own notes,
as well as access shared notes that all entities can see.

Tools are registered via register_notes_tools() called from services/__init__.py.
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional

from app.services.tool_service import ToolCategory, ToolService
from app.services.notes_service import notes_service
from app.services.notes_vector_service import notes_vector_service
from app.config import settings

logger = logging.getLogger(__name__)


# Prefix of notes_read results that point at a copy already in context instead
# of repeating the content. Session reload uses it to tell pointer results
# apart from real note content when rebuilding stamps.
NOTE_IN_CONTEXT_MARKER = "[NOTE IN CONTEXT]"

# Tool names whose results carry note-content stamps; the session manager's
# tool loop consumes stamps after executing any of these.
NOTE_STAMP_TOOL_NAMES = ("notes_read", "notes_write", "notes_edit")


# Track the entity label for the current context
# This gets set by the session manager before tool execution
_current_entity_label: Optional[str] = None
# The active ConversationSession, used by notes_read to find note content
# already in the conversation context. Optional so the tools still work
# without a session.
_current_session = None
# Note-content stamps from operations in the current turn. Tool results are
# folded into the conversation context only when the turn's exchange is added
# at the end of the tool loop, so without this a notes_read after a
# notes_write/notes_edit in the same turn couldn't see that operation.
_turn_note_stamps: List[Dict[str, Any]] = []
# Stamps from the most recent notes tool call. The session manager's tool loop
# consumes these to stamp them onto the tool_result context message
# (note_stamps), which is what makes them visible on later turns and after a
# session reload.
_last_note_stamps: List[Dict[str, Any]] = []


def set_current_entity_label(label: Optional[str], session=None) -> None:
    """
    Set the entity label (and optionally the active session) for the current
    tool execution context. Called at the start of each turn; resets the
    turn-level note stamp accumulator.
    """
    global _current_entity_label, _current_session
    global _turn_note_stamps, _last_note_stamps
    _current_entity_label = label
    _current_session = session
    _turn_note_stamps = []
    _last_note_stamps = []
    logger.debug(f"Notes tools: entity label set to '{label}'")


def get_current_entity_label() -> Optional[str]:
    """Get the current entity label for tool execution."""
    return _current_entity_label


def consume_last_note_stamps() -> List[Dict[str, Any]]:
    """
    Return the note-content stamps recorded by the most recent notes tool call
    and clear them. Called by the session manager's tool loop right after
    executing a notes tool, to stamp them onto that call's tool_result context
    message.
    """
    global _last_note_stamps
    stamps = _last_note_stamps
    _last_note_stamps = []
    return stamps


def note_content_hash(content: str) -> str:
    """Short content hash used to compare in-context note copies to disk."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _note_owner(shared: bool) -> str:
    """Stamp owner key: 'shared' for shared notes, else the entity label."""
    return "shared" if shared else (_current_entity_label or "")


def _record_note_stamp(owner: str, filename: str, content_hash: str, source: str) -> None:
    """
    Record that a note file's content (or an edit to it) is visible at the
    current point of the conversation. source: "read" | "write" | "edit"
    ("seed" stamps are created by the session manager on the notes seed
    message). "edit" stamps are deltas — their hash is the post-edit content,
    derivable only while an earlier full copy remains in context.
    """
    stamp = {"owner": owner, "filename": filename, "hash": content_hash, "source": source}
    _turn_note_stamps.append(stamp)
    _last_note_stamps.append(stamp)


def _collect_note_stamps(owner: str, filename: str) -> List[Dict[str, Any]]:
    """
    All stamps for (owner, filename) in order: those on context messages
    (shrinking naturally as trimming rolls messages out), then this turn's
    not-yet-in-context stamps.
    """
    stamps: List[Dict[str, Any]] = []
    if _current_session is not None:
        try:
            stamps.extend(
                _current_session.get_in_context_note_stamps().get((owner, filename), [])
            )
        except Exception as e:
            logger.warning(f"Could not read note stamps from session: {e}")
    stamps.extend(
        s for s in _turn_note_stamps
        if s["owner"] == owner and s["filename"] == filename
    )
    return stamps


def _full_stamp_pointer(stamp: Dict[str, Any]) -> str:
    """Human-readable location of a full-content stamp, for pointer results."""
    source = stamp.get("source")
    if source == "seed":
        block = "[SHARED NOTES]" if stamp.get("owner") == "shared" else "[ENTITY NOTES]"
        return f"the {block} block at the start of this conversation"
    if source == "read":
        return "your earlier notes_read result for this file in this conversation"
    if source == "write":
        return "the content you passed to notes_write for this file earlier in this conversation"
    return "the earlier copy of this file in your context"


def _find_in_context_pointer(
    owner: str, filename: str, shared: bool, current_hash: str
) -> Optional[str]:
    """
    If the file's current content is already visible in the conversation
    context, return a short pointer to it; otherwise None (return the full
    content). Hash comparison against disk is the staleness check, so
    out-of-band edits (e.g. the researcher editing the file directly)
    automatically fall back to returning the content.
    """
    stamps = _collect_note_stamps(owner, filename)
    full_stamps = [s for s in stamps if s.get("source") != "edit"]

    # The exact current content is verbatim in context somewhere
    matching_full = next(
        (s for s in reversed(full_stamps) if s.get("hash") == current_hash), None
    )
    if matching_full is not None:
        return (
            f"{NOTE_IN_CONTEXT_MARKER} The current content of '{filename}' is already "
            f"in your context — see {_full_stamp_pointer(matching_full)}. "
            f"The file has not changed since."
        )

    # Current content is derivable: a full copy plus subsequent notes_edit
    # records. Front-trimming removes oldest messages first, so if the latest
    # full stamp is still in context, every edit record after it is too.
    if (
        stamps
        and full_stamps
        and stamps[-1].get("source") == "edit"
        and stamps[-1].get("hash") == current_hash
    ):
        return (
            f"{NOTE_IN_CONTEXT_MARKER} The current content of '{filename}' is already "
            f"in your context: take {_full_stamp_pointer(full_stamps[-1])} and apply "
            f"your subsequent notes_edit change(s) to it — together they are the "
            f"file's current content."
        )

    # Multi-entity conversations: index files are re-read from disk and
    # injected into every turn's context (per-turn [ENTITY NOTES]/[SHARED
    # NOTES] block), so they are current even with no stamps — unless this
    # entity changed the file earlier this turn, after the block was built.
    if (
        _current_session is not None
        and getattr(_current_session, "is_multi_entity", False)
        and filename.lower() == "index.md"
    ):
        block = "[SHARED NOTES]" if shared else "[ENTITY NOTES]"
        turn_stamps = [
            s for s in _turn_note_stamps
            if s["owner"] == owner and s["filename"] == filename
        ]
        if not turn_stamps:
            return (
                f"{NOTE_IN_CONTEXT_MARKER} The current content of '{filename}' is in "
                f"the {block} block included with this turn's context. It is re-read "
                f"from disk every turn, so that copy is current."
            )
        if turn_stamps[-1].get("hash") == current_hash:
            if turn_stamps[-1].get("source") == "write":
                return (
                    f"{NOTE_IN_CONTEXT_MARKER} The current content of '{filename}' is "
                    f"the content you passed to notes_write earlier this turn."
                )
            return (
                f"{NOTE_IN_CONTEXT_MARKER} The current content of '{filename}' is the "
                f"{block} block from this turn's context with the notes_edit "
                f"change(s) you made this turn applied."
            )

    return None


async def _notes_read(filename: str, shared: bool = False) -> str:
    """
    Read a note file from your private notes or the shared notes folder.
    
    Args:
        filename: Name of the file to read (e.g., "project-ideas.md")
        shared: If True, read from the shared folder instead of your private notes
    
    Returns:
        The file contents, or an error message
    """
    if not settings.notes_enabled:
        return "Error: Notes feature is not enabled"
    
    entity_label = get_current_entity_label()
    if not entity_label and not shared:
        return "Error: No entity context available for reading private notes"
    
    result = notes_service.read_note(
        entity_label=entity_label or "",
        filename=filename,
        shared=shared,
    )

    if not result['success']:
        return f"Error: {result['error']}"

    content = result['content']
    current_hash = note_content_hash(content)
    owner = _note_owner(shared)

    if settings.notes_read_dedup_enabled:
        pointer = _find_in_context_pointer(owner, filename, shared, current_hash)
        if pointer:
            logger.info(
                f"[NOTES] notes_read dedup: '{filename}' (owner={owner}) already in "
                f"context, returning pointer instead of {len(content)} chars"
            )
            # Pointer results add no note content to context - no stamp
            return pointer

    _record_note_stamp(owner, filename, current_hash, "read")
    return content


async def _notes_write(filename: str, content: str, shared: bool = False) -> str:
    """
    Write or update a note file in your private notes or the shared notes folder.
    
    Args:
        filename: Name of the file to write (e.g., "project-ideas.md")
        content: The content to write to the file
        shared: If True, write to the shared folder instead of your private notes
    
    Returns:
        Success message or error
    """
    if not settings.notes_enabled:
        return "Error: Notes feature is not enabled"
    
    entity_label = get_current_entity_label()
    if not entity_label and not shared:
        return "Error: No entity context available for writing private notes"
    
    result = notes_service.write_note(
        entity_label=entity_label or "",
        filename=filename,
        content=content,
        shared=shared,
    )

    if result['success']:
        # Mirror the note into the vector index so notes_search finds it.
        # Best-effort: the filesystem write above is the source of truth.
        try:
            await notes_vector_service.vectorize_note(
                entity_label=entity_label or "",
                filename=filename,
                content=content,
                shared=shared,
            )
        except Exception as e:
            logger.warning(f"Note vectorization failed for '{filename}': {e}")

        # The full content lives in this call's tool_use input, so the file
        # is now fully represented in context at this point
        _record_note_stamp(_note_owner(shared), filename, note_content_hash(content), "write")

        action = "Created" if result.get('created') else "Updated"
        location = "shared notes" if shared else "your notes"
        return f"{action} '{filename}' in {location}"
    else:
        return f"Error: {result['error']}"


async def _notes_edit(
    filename: str,
    old_string: str,
    new_string: str,
    shared: bool = False,
    replace_all: bool = False,
) -> str:
    """
    Edit an existing note file by exact string replacement.

    Args:
        filename: Name of the file to edit
        old_string: Exact text to replace (must match the file content)
        new_string: Replacement text
        shared: If True, edit in the shared folder instead of your private notes
        replace_all: If True, replace every occurrence of old_string

    Returns:
        Success message or error
    """
    if not settings.notes_enabled:
        return "Error: Notes feature is not enabled"

    entity_label = get_current_entity_label()
    if not entity_label and not shared:
        return "Error: No entity context available for editing private notes"

    result = notes_service.edit_note(
        entity_label=entity_label or "",
        filename=filename,
        old_string=old_string,
        new_string=new_string,
        shared=shared,
        replace_all=replace_all,
    )

    if not result['success']:
        return f"Error: {result['error']}"

    new_content = result['new_content']

    # Mirror the edited note into the vector index so notes_search finds it.
    # Best-effort: the filesystem write above is the source of truth.
    try:
        await notes_vector_service.vectorize_note(
            entity_label=entity_label or "",
            filename=filename,
            content=new_content,
            shared=shared,
        )
    except Exception as e:
        logger.warning(f"Note vectorization failed for '{filename}': {e}")

    # Delta stamp: context holds the edit record (this call's tool_use input),
    # not the full content; hash is the post-edit file content
    _record_note_stamp(_note_owner(shared), filename, note_content_hash(new_content), "edit")

    replacements = result['replacements']
    location = "shared notes" if shared else "your notes"
    plural = "s" if replacements != 1 else ""
    return f"Edited '{filename}' in {location} ({replacements} replacement{plural})"


async def _notes_delete(filename: str, shared: bool = False) -> str:
    """
    Delete a note file from your private notes or the shared notes folder.
    
    Note: Cannot delete index.md - use write to clear it instead.
    
    Args:
        filename: Name of the file to delete
        shared: If True, delete from the shared folder instead of your private notes
    
    Returns:
        Success message or error
    """
    if not settings.notes_enabled:
        return "Error: Notes feature is not enabled"
    
    entity_label = get_current_entity_label()
    if not entity_label and not shared:
        return "Error: No entity context available for deleting private notes"
    
    result = notes_service.delete_note(
        entity_label=entity_label or "",
        filename=filename,
        shared=shared,
    )

    if result['success']:
        # Remove the note's chunks from the vector index (best-effort)
        try:
            await notes_vector_service.remove_note_vectors(
                entity_label=entity_label or "",
                filename=filename,
                shared=shared,
            )
        except Exception as e:
            logger.warning(f"Note vector removal failed for '{filename}': {e}")

        location = "shared notes" if shared else "your notes"
        return f"Deleted '{filename}' from {location}"
    else:
        return f"Error: {result['error']}"


async def _notes_list(shared: bool = False) -> str:
    """
    List all note files in your private notes or the shared notes folder.
    
    Args:
        shared: If True, list the shared folder instead of your private notes
    
    Returns:
        A formatted list of files with sizes and modification dates
    """
    if not settings.notes_enabled:
        return "Error: Notes feature is not enabled"
    
    entity_label = get_current_entity_label()
    if not entity_label and not shared:
        return "Error: No entity context available for listing private notes"
    
    result = notes_service.list_notes(
        entity_label=entity_label or "",
        shared=shared,
    )
    
    if not result['success']:
        return f"Error: {result['error']}"
    
    files = result['files']
    if not files:
        location = "shared notes" if shared else "your notes"
        return f"No files in {location}"
    
    # Format the file list
    lines = []
    location = "Shared notes" if shared else "Your notes"
    lines.append(f"{location}:")
    lines.append("")
    
    for f in files:
        size = f['size_bytes']
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"
        
        # Parse ISO date and format nicely
        modified = f['modified'][:10]  # Just the date part
        lines.append(f"  {f['filename']} ({size_str}, modified {modified})")
    
    return "\n".join(lines)


async def _notes_search(query: str, num_results: int = 5) -> str:
    """
    Semantic search across your notes (private and shared).

    Args:
        query: Text to search for
        num_results: Number of matching chunks to return (default 5, max 10)

    Returns:
        Matching note excerpts with filenames, or an error message
    """
    if not settings.notes_enabled:
        return "Error: Notes feature is not enabled"

    entity_label = get_current_entity_label()
    if not entity_label:
        return "Error: No entity context available for searching notes"

    num_results = max(1, min(10, num_results))

    try:
        matches = await notes_vector_service.search_notes(
            entity_label=entity_label,
            query=query,
            num_results=num_results,
        )
    except Exception as e:
        logger.error(f"Notes search failed: {e}")
        return f"Error searching notes: {e}"

    if not matches:
        return (
            f"No note content found matching: \"{query}\". "
            "Notes written before search was added may not be indexed yet."
        )

    lines = [f"Found {len(matches)} note excerpts matching: \"{query}\"", ""]
    for i, match in enumerate(matches, 1):
        location = "shared" if match["shared"] else "private"
        lines.append(
            f"--- {match['filename']} ({location}, chunk {match['chunk_index']}, "
            f"similarity: {match['score']:.3f}) ---"
        )
        lines.append(match["text"])
        lines.append("")

    lines.append("Use notes_read to read any of these files in full.")
    return "\n".join(lines)


def register_notes_tools(tool_service: ToolService) -> None:
    """Register all notes tools with the tool service."""
    
    # Only register if notes are enabled
    if not settings.notes_enabled:
        logger.info("Notes tools not registered (notes_enabled=False)")
        return
    
    # notes_read
    tool_service.register_tool(
        name="notes_read",
        description=(
            "Read a note file from your private notes or the shared notes folder. "
            "Private notes are not visible to other entities; shared notes are visible to all entities. "
            "Notes are plain files on the researcher's machine, so the researcher can also read them. "
            "Your index.md file is automatically loaded into your context at the start of each conversation. "
            "If the file's current content is already visible in your conversation context, "
            "this returns a short pointer to that copy instead of repeating the content."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to read (e.g., 'project-ideas.md')"
                },
                "shared": {
                    "type": "boolean",
                    "description": "If true, read from the shared folder instead of your private notes",
                    "default": False
                }
            },
            "required": ["filename"]
        },
        executor=_notes_read,
        category=ToolCategory.MEMORY,
        enabled=True,
    )
    
    # notes_write
    tool_service.register_tool(
        name="notes_write",
        description=(
            "Write a complete note file in your private notes or the shared notes folder. "
            "Creates the file if it doesn't exist, replaces its entire content if it does. "
            "To change part of an existing note, prefer notes_edit instead of rewriting the whole file. "
            "Your index.md is special - it's automatically loaded into your context each conversation. "
            "Use it for things you want to always have in mind. "
            "Allowed file types: .md, .json, .txt, .html, .xml, .yaml, .yml"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to write (e.g., 'index.md', 'project-ideas.md')"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file"
                },
                "shared": {
                    "type": "boolean",
                    "description": "If true, write to the shared folder instead of your private notes",
                    "default": False
                }
            },
            "required": ["filename", "content"]
        },
        executor=_notes_write,
        category=ToolCategory.MEMORY,
        enabled=True,
    )
    
    # notes_edit
    tool_service.register_tool(
        name="notes_edit",
        description=(
            "Edit an existing note file by replacing an exact string, without rewriting "
            "the whole file. Provide the exact text to replace (old_string) and its "
            "replacement (new_string). old_string must match the file content exactly, "
            "including whitespace and indentation, and must appear exactly once unless "
            "replace_all is true. Prefer this over notes_write when changing part of an "
            "existing note - it saves you from re-outputting unchanged content. "
            "Use notes_write to create new files or fully rewrite one."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the existing file to edit (e.g., 'index.md')"
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to replace (must match the file content exactly)"
                },
                "new_string": {
                    "type": "string",
                    "description": "The text to replace it with"
                },
                "shared": {
                    "type": "boolean",
                    "description": "If true, edit in the shared folder instead of your private notes",
                    "default": False
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "If true, replace every occurrence of old_string",
                    "default": False
                }
            },
            "required": ["filename", "old_string", "new_string"]
        },
        executor=_notes_edit,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    # notes_delete
    tool_service.register_tool(
        name="notes_delete",
        description=(
            "Delete a note file from your private notes or the shared notes folder. "
            "Cannot delete index.md - use notes_write to clear it instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to delete"
                },
                "shared": {
                    "type": "boolean",
                    "description": "If true, delete from the shared folder instead of your private notes",
                    "default": False
                }
            },
            "required": ["filename"]
        },
        executor=_notes_delete,
        category=ToolCategory.MEMORY,
        enabled=True,
    )
    
    # notes_list
    tool_service.register_tool(
        name="notes_list",
        description=(
            "List all note files in your private notes or the shared notes folder. "
            "Shows filename, size, and last modified date for each file."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "shared": {
                    "type": "boolean",
                    "description": "If true, list the shared folder instead of your private notes",
                    "default": False
                }
            },
            "required": []
        },
        executor=_notes_list,
        category=ToolCategory.MEMORY,
        enabled=True,
    )

    # notes_search (requires the vector store; registered only when configured)
    if settings.pinecone_api_key:
        tool_service.register_tool(
            name="notes_search",
            description=(
                "Search your notes (private and shared) by meaning, not just filename. "
                "Returns matching excerpts with their filenames; use notes_read to read "
                "a file in full. Notes are indexed when written, so notes created before "
                "search existed may need reindexing by the researcher."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for: a topic, phrase, or question."
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of matching excerpts to return (default: 5, max: 10).",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 10
                    }
                },
                "required": ["query"]
            },
            executor=_notes_search,
            category=ToolCategory.MEMORY,
            enabled=True,
        )

    logger.info("Notes tools registered: notes_read, notes_write, notes_edit, notes_delete, notes_list, notes_search")
