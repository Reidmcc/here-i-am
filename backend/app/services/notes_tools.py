"""
Notes Tools - Tool definitions for entity notes management.

These tools allow AI entities to read, write, and manage their own notes,
as well as access shared notes that all entities can see.

Tools are registered at module load time via register_notes_tools().
"""

import logging
from typing import Optional

from app.services.tool_service import tool_service, ToolCategory
from app.services.notes_service import notes_service

logger = logging.getLogger(__name__)


# Track the entity label for the current context
# This gets set by the chat route before tool execution
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
    entity_label = get_current_entity_label()
    if not entity_label and not shared:
        return "Error: No entity context available for deleting private notes"
    
    result = notes_service.delete_note(
        entity_label=entity_label or "",
        filename=filename,
        shared=shared,
    )
    
    if result['success']:
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


def register_notes_tools() -> None:
    """Register all notes tools with the tool service."""
    
    # notes_read
    tool_service.register_tool(
        name="notes_read",
        description=(
            "Read a note file from your private notes or the shared notes folder. "
            "Your private notes are only visible to you. Shared notes are visible to all entities. "
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
    
    logger.info("Notes tools registered: notes_read, notes_write, notes_delete, notes_list")


# Register tools when module is imported
register_notes_tools()
