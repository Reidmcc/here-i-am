"""
Notes Service for entity-specific persistent notes.

This service provides:
- Entity-scoped notes storage (each entity has their own folder)
- Shared notes folder accessible to all entities
- Index file auto-loading for context injection
- File CRUD operations for notes management

Notes are stored in plain text files (markdown, json, etc.) organized by entity.
Each entity can have an index.md that gets automatically injected into their context.
"""

import os
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)


class NotesService:
    """
    Service for managing entity-specific notes.
    
    Directory structure:
        {notes_base_dir}/
            {entity_label}/          # Private notes for each entity
                index.md             # Auto-loaded into context
                other_notes.md
                ...
            shared/                  # Shared notes accessible to all entities
                index.md
                ...
    """
    
    def __init__(self):
        self._base_dir: Optional[Path] = None
        logger.info("NotesService initialized")
    
    @property
    def base_dir(self) -> Path:
        """Get the base directory for notes storage."""
        if self._base_dir is None:
            # Default to backend/notes if not configured
            self._base_dir = Path(getattr(settings, 'notes_base_dir', './notes'))
        return self._base_dir
    
    def _get_entity_dir(self, entity_label: str) -> Path:
        """Get the directory path for an entity's notes."""
        # Sanitize entity label for filesystem safety
        safe_label = self._sanitize_filename(entity_label)
        return self.base_dir / safe_label
    
    def _get_shared_dir(self) -> Path:
        """Get the directory path for shared notes."""
        return self.base_dir / "shared"
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize a string for safe use as filename/directory name."""
        # Replace problematic characters with underscores
        unsafe_chars = '<>:"/\\|?*'
        result = name
        for char in unsafe_chars:
            result = result.replace(char, '_')
        # Remove leading/trailing whitespace and dots
        result = result.strip('. ')
        # Ensure non-empty
        return result if result else 'unnamed'
    
    def _ensure_directory(self, dir_path: Path) -> bool:
        """Ensure a directory exists, creating it if necessary."""
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            logger.error(f"Failed to create directory {dir_path}: {e}")
            return False
    
    def _validate_file_extension(self, filename: str) -> bool:
        """Validate that the file has an allowed extension."""
        allowed_extensions = {'.md', '.json', '.txt', '.html', '.xml', '.yaml', '.yml'}
        ext = Path(filename).suffix.lower()
        return ext in allowed_extensions

    def _resolve_note_path(self, target_dir: Path, filename: str) -> Optional[Path]:
        """
        Resolve a note filename inside its owning directory.

        Notes filenames come from tool arguments, so they are model-controlled
        and must stay inside the entity's own folder. Two things matter here:

        - The filename must be a bare name. A path separator would let one
          entity address another entity's notes, including the index.md that
          is auto-injected into that entity's context.
        - Containment is checked with is_relative_to, not string prefixing.
          A prefix test treats '/notes/Ada' as containing '/notes/Adam/...',
          so any entity whose label prefixes another's could read, overwrite,
          or delete the other's notes.

        Args:
            target_dir: The entity or shared notes directory
            filename: Filename from a tool argument

        Returns:
            The resolved path, or None if it is not a safe target
        """
        if not filename or filename in ('.', '..'):
            return None
        if '/' in filename or '\\' in filename or '\x00' in filename:
            return None
        if Path(filename).name != filename or Path(filename).is_absolute():
            return None

        candidate = target_dir / filename
        try:
            resolved = candidate.resolve()
            target_resolved = target_dir.resolve()
        except OSError:
            return None

        if not resolved.is_relative_to(target_resolved):
            return None
        if resolved == target_resolved:
            return None
        return candidate
    
    def get_index_content(self, entity_label: str) -> Optional[str]:
        """
        Get the index.md content for an entity.
        
        This is used for automatic context injection.
        Returns None if the index file doesn't exist.
        
        Args:
            entity_label: The entity's display label
            
        Returns:
            The content of index.md, or None if it doesn't exist
        """
        entity_dir = self._get_entity_dir(entity_label)
        index_path = entity_dir / "index.md"
        
        if not index_path.exists():
            logger.debug(f"No index.md found for entity '{entity_label}'")
            return None
        
        try:
            content = index_path.read_text(encoding='utf-8')
            logger.info(f"Loaded index.md for entity '{entity_label}' ({len(content)} chars)")
            return content
        except OSError as e:
            logger.error(f"Failed to read index.md for entity '{entity_label}': {e}")
            return None
    
    def get_shared_index_content(self) -> Optional[str]:
        """
        Get the shared index.md content.
        
        Returns None if the shared index file doesn't exist.
        """
        shared_dir = self._get_shared_dir()
        index_path = shared_dir / "index.md"
        
        if not index_path.exists():
            logger.debug("No shared index.md found")
            return None
        
        try:
            content = index_path.read_text(encoding='utf-8')
            logger.info(f"Loaded shared index.md ({len(content)} chars)")
            return content
        except OSError as e:
            logger.error(f"Failed to read shared index.md: {e}")
            return None
    
    def read_note(
        self,
        entity_label: str,
        filename: str,
        shared: bool = False,
    ) -> Dict[str, Any]:
        """
        Read a note file.
        
        Args:
            entity_label: The entity's display label (ignored if shared=True)
            filename: Name of the file to read
            shared: If True, read from shared folder instead of entity folder
            
        Returns:
            Dict with 'success', 'content' (if success), and 'error' (if failure)
        """
        if not self._validate_file_extension(filename):
            return {
                'success': False,
                'error': f"Invalid file extension. Allowed: .md, .json, .txt, .html, .xml, .yaml, .yml"
            }
        
        if shared:
            target_dir = self._get_shared_dir()
        else:
            target_dir = self._get_entity_dir(entity_label)
        
        file_path = self._resolve_note_path(target_dir, filename)
        if file_path is None:
            return {'success': False, 'error': "Invalid file path"}
        
        if not file_path.exists():
            return {'success': False, 'error': f"File not found: {filename}"}
        
        try:
            content = file_path.read_text(encoding='utf-8')
            logger.info(f"Read note '{filename}' for entity '{entity_label}' ({len(content)} chars)")
            return {'success': True, 'content': content}
        except OSError as e:
            logger.error(f"Failed to read note '{filename}': {e}")
            return {'success': False, 'error': str(e)}
    
    def write_note(
        self,
        entity_label: str,
        filename: str,
        content: str,
        shared: bool = False,
    ) -> Dict[str, Any]:
        """
        Write or update a note file.
        
        Args:
            entity_label: The entity's display label (ignored if shared=True)
            filename: Name of the file to write
            content: Content to write
            shared: If True, write to shared folder instead of entity folder
            
        Returns:
            Dict with 'success', 'created' (bool), and 'error' (if failure)
        """
        if not self._validate_file_extension(filename):
            return {
                'success': False,
                'error': f"Invalid file extension. Allowed: .md, .json, .txt, .html, .xml, .yaml, .yml"
            }
        
        if shared:
            target_dir = self._get_shared_dir()
        else:
            target_dir = self._get_entity_dir(entity_label)
        
        # Ensure directory exists
        if not self._ensure_directory(target_dir):
            return {'success': False, 'error': "Failed to create notes directory"}
        
        file_path = self._resolve_note_path(target_dir, filename)
        if file_path is None:
            return {'success': False, 'error': "Invalid file path"}
        
        is_new = not file_path.exists()
        
        try:
            file_path.write_text(content, encoding='utf-8')
            action = "Created" if is_new else "Updated"
            logger.info(f"{action} note '{filename}' for entity '{entity_label}' ({len(content)} chars)")
            return {'success': True, 'created': is_new}
        except OSError as e:
            logger.error(f"Failed to write note '{filename}': {e}")
            return {'success': False, 'error': str(e)}
    
    def edit_note(
        self,
        entity_label: str,
        filename: str,
        old_string: str,
        new_string: str,
        shared: bool = False,
        replace_all: bool = False,
    ) -> Dict[str, Any]:
        """
        Edit an existing note file by exact string replacement.

        Unlike write_note, this does not require the full file content —
        only the region being changed. The file must already exist and
        old_string must match exactly once (or replace_all=True).

        Args:
            entity_label: The entity's display label (ignored if shared=True)
            filename: Name of the file to edit
            old_string: Exact text to replace (must be present in the file)
            new_string: Replacement text
            shared: If True, edit in shared folder instead of entity folder
            replace_all: If True, replace every occurrence of old_string

        Returns:
            Dict with 'success', 'replacements' (int), 'new_content' (the
            full post-edit content, for vectorization), and 'error' (if failure)
        """
        if not self._validate_file_extension(filename):
            return {
                'success': False,
                'error': f"Invalid file extension. Allowed: .md, .json, .txt, .html, .xml, .yaml, .yml"
            }

        if not old_string:
            return {'success': False, 'error': "old_string must not be empty"}

        if old_string == new_string:
            return {'success': False, 'error': "old_string and new_string are identical - nothing to change"}

        if shared:
            target_dir = self._get_shared_dir()
        else:
            target_dir = self._get_entity_dir(entity_label)

        file_path = self._resolve_note_path(target_dir, filename)
        if file_path is None:
            return {'success': False, 'error': "Invalid file path"}

        if not file_path.exists():
            return {
                'success': False,
                'error': f"File not found: {filename}. Use notes_write to create a new file."
            }

        try:
            content = file_path.read_text(encoding='utf-8')
        except OSError as e:
            logger.error(f"Failed to read note '{filename}' for edit: {e}")
            return {'success': False, 'error': str(e)}

        occurrences = content.count(old_string)
        if occurrences == 0:
            return {
                'success': False,
                'error': (
                    f"old_string not found in {filename}. "
                    "Make sure it matches the file content exactly, including whitespace."
                )
            }
        if occurrences > 1 and not replace_all:
            return {
                'success': False,
                'error': (
                    f"old_string appears {occurrences} times in {filename}. "
                    "Provide a longer string that matches uniquely, or set replace_all=true."
                )
            }

        if replace_all:
            new_content = content.replace(old_string, new_string)
            replacements = occurrences
        else:
            new_content = content.replace(old_string, new_string, 1)
            replacements = 1

        try:
            file_path.write_text(new_content, encoding='utf-8')
            logger.info(
                f"Edited note '{filename}' for entity '{entity_label}' "
                f"({replacements} replacement(s), {len(new_content)} chars)"
            )
            return {'success': True, 'replacements': replacements, 'new_content': new_content}
        except OSError as e:
            logger.error(f"Failed to edit note '{filename}': {e}")
            return {'success': False, 'error': str(e)}

    def delete_note(
        self,
        entity_label: str,
        filename: str,
        shared: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete a note file.
        
        Args:
            entity_label: The entity's display label (ignored if shared=True)
            filename: Name of the file to delete
            shared: If True, delete from shared folder instead of entity folder
            
        Returns:
            Dict with 'success' and 'error' (if failure)
        """
        # Prevent deletion of index.md
        if filename.lower() == 'index.md':
            return {'success': False, 'error': "Cannot delete index.md - use write to clear it instead"}
        
        if not self._validate_file_extension(filename):
            return {
                'success': False,
                'error': f"Invalid file extension. Allowed: .md, .json, .txt, .html, .xml, .yaml, .yml"
            }
        
        if shared:
            target_dir = self._get_shared_dir()
        else:
            target_dir = self._get_entity_dir(entity_label)
        
        file_path = self._resolve_note_path(target_dir, filename)
        if file_path is None:
            return {'success': False, 'error': "Invalid file path"}
        
        if not file_path.exists():
            return {'success': False, 'error': f"File not found: {filename}"}
        
        try:
            file_path.unlink()
            logger.info(f"Deleted note '{filename}' for entity '{entity_label}'")
            return {'success': True}
        except OSError as e:
            logger.error(f"Failed to delete note '{filename}': {e}")
            return {'success': False, 'error': str(e)}
    
    def list_notes(
        self,
        entity_label: str,
        shared: bool = False,
    ) -> Dict[str, Any]:
        """
        List all note files for an entity or in the shared folder.
        
        Args:
            entity_label: The entity's display label (ignored if shared=True)
            shared: If True, list shared folder instead of entity folder
            
        Returns:
            Dict with 'success', 'files' (list of file info dicts), and 'error' (if failure)
        """
        if shared:
            target_dir = self._get_shared_dir()
        else:
            target_dir = self._get_entity_dir(entity_label)
        
        if not target_dir.exists():
            return {'success': True, 'files': []}
        
        try:
            files = []
            for item in target_dir.iterdir():
                if item.is_file() and self._validate_file_extension(item.name):
                    stat = item.stat()
                    files.append({
                        'filename': item.name,
                        'size_bytes': stat.st_size,
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    })
            
            # Sort by filename for consistent ordering
            files.sort(key=lambda f: f['filename'])
            
            logger.info(f"Listed {len(files)} notes for entity '{entity_label}' (shared={shared})")
            return {'success': True, 'files': files}
        except OSError as e:
            logger.error(f"Failed to list notes: {e}")
            return {'success': False, 'error': str(e)}


# Singleton instance
notes_service = NotesService()
