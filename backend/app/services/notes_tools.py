"""
Notes Tools - Tool definitions for entity notes management.

These tools allow AI entities to read, write, and manage their own notes,
as well as access shared notes that all entities can see.

Tools are registered via register_notes_tools() called from services/__init__.py.
"""

import logging
from typing import Optional

from app.services.tool_service import ToolCategory, ToolService
from app.services.notes_service import notes_service
from app.services.notes_vector_service import notes_vector_service
from app.config import settings

logger = logging.getLogger(__name__)


# Track the entity label for the current context
# This gets set by the session manager before tool execution
_current_entity_label: Optional[str] = None


def set_current_entity_label(label: str) -> None:
    """Set the entity label for the current tool execution context."""
    global _current_entity_label
    _current_entity_label = label
    logger.debug(f"Notes tools: entity label set to '{label}'")


def get_current_entity_label() -> Optional[str]:
    """Get the current entity label for tool execution."""
    return _current_entity_label


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
    
    if result['success']:
        return result['content']
    else:
        return f"Error: {result['error']}"


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

        action = "Created" if result.get('created') else "Updated"
        location = "shared notes" if shared else "your notes"
        return f"{action} '{filename}' in {location}"
    else:
        return f"Error: {result['error']}"


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
            "Your index.md file is automatically loaded into your context at the start of each conversation."
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
            "Write or update a note file in your private notes or the shared notes folder. "
            "Creates the file if it doesn't exist, updates it if it does. "
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

    logger.info("Notes tools registered: notes_read, notes_write, notes_delete, notes_list, notes_search")
