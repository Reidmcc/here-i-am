"""
Unit tests for NotesService and notes tools.
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import shutil
import os

from app.services.notes_service import NotesService, notes_service
from app.services.notes_tools import (
    register_notes_tools,
    set_current_entity_label,
    get_current_entity_label,
    _notes_read,
    _notes_write,
    _notes_delete,
    _notes_list,
)
from app.services.tool_service import ToolService, ToolCategory


class TestNotesServiceConfiguration:
    """Tests for NotesService configuration."""

    def test_service_initialization(self):
        """Test NotesService initializes correctly."""
        service = NotesService()
        assert service._base_dir is None  # Lazy initialization

    def test_base_dir_uses_settings(self):
        """Test base_dir uses settings.notes_base_dir."""
        with patch("app.services.notes_service.settings") as mock_settings:
            mock_settings.notes_base_dir = "/custom/notes/path"
            
            service = NotesService()
            # Compare as Path objects to handle cross-platform path separators
            assert service.base_dir == Path("/custom/notes/path")

    def test_base_dir_fallback(self):
        """Test base_dir falls back to ./notes if not configured."""
        with patch("app.services.notes_service.settings") as mock_settings:
            # Simulate settings without notes_base_dir attribute
            del mock_settings.notes_base_dir
            mock_settings.configure_mock(**{})
            
            service = NotesService()
            # Should use default
            assert "notes" in str(service.base_dir)


class TestNotesServiceFilenameValidation:
    """Tests for filename validation."""

    def test_valid_extensions(self):
        """Test that valid file extensions are allowed."""
        service = NotesService()
        
        valid_files = [
            "index.md",
            "notes.txt",
            "data.json",
            "page.html",
            "config.xml",
            "settings.yaml",
            "config.yml",
        ]
        
        for filename in valid_files:
            assert service._validate_file_extension(filename), f"{filename} should be valid"

    def test_invalid_extensions(self):
        """Test that invalid file extensions are rejected."""
        service = NotesService()
        
        invalid_files = [
            "script.py",
            "program.exe",
            "library.so",
            "image.png",
            "document.pdf",
            "noextension",
        ]
        
        for filename in invalid_files:
            assert not service._validate_file_extension(filename), f"{filename} should be invalid"

    def test_filename_sanitization(self):
        """Test that filenames are properly sanitized."""
        service = NotesService()
        
        # Test various unsafe characters
        assert service._sanitize_filename("normal_name") == "normal_name"
        assert service._sanitize_filename("name<with>special") == "name_with_special"
        assert service._sanitize_filename("path/sep\\test") == "path_sep_test"
        assert service._sanitize_filename('name"with"quotes') == "name_with_quotes"
        assert service._sanitize_filename("...leading_dots") == "leading_dots"
        assert service._sanitize_filename("   spaces   ") == "spaces"
        assert service._sanitize_filename("") == "unnamed"


class TestNotesServiceFileOperations:
    """Tests for file read/write/delete operations."""

    @pytest.fixture
    def temp_notes_dir(self):
        """Create a temporary directory for notes testing."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def notes_service_with_temp_dir(self, temp_notes_dir):
        """Create a NotesService pointing to a temp directory."""
        with patch("app.services.notes_service.settings") as mock_settings:
            mock_settings.notes_base_dir = temp_notes_dir
            service = NotesService()
            # Force re-initialization of base_dir
            service._base_dir = None
            yield service

    def test_write_note_creates_file(self, notes_service_with_temp_dir, temp_notes_dir):
        """Test writing a note creates the file."""
        service = notes_service_with_temp_dir
        
        result = service.write_note(
            entity_label="TestEntity",
            filename="test.md",
            content="# Test Content\n\nThis is a test.",
        )
        
        assert result["success"] is True
        assert result["created"] is True
        
        # Verify file exists
        expected_path = Path(temp_notes_dir) / "TestEntity" / "test.md"
        assert expected_path.exists()
        assert expected_path.read_text() == "# Test Content\n\nThis is a test."

    def test_write_note_updates_existing(self, notes_service_with_temp_dir, temp_notes_dir):
        """Test writing to an existing file updates it."""
        service = notes_service_with_temp_dir
        
        # Create initial file
        service.write_note("TestEntity", "test.md", "Original content")
        
        # Update it
        result = service.write_note("TestEntity", "test.md", "Updated content")
        
        assert result["success"] is True
        assert result["created"] is False  # Updated, not created
        
        expected_path = Path(temp_notes_dir) / "TestEntity" / "test.md"
        assert expected_path.read_text() == "Updated content"

    def test_read_note_returns_content(self, notes_service_with_temp_dir, temp_notes_dir):
        """Test reading a note returns its content."""
        service = notes_service_with_temp_dir
        
        # Create a file first
        service.write_note("TestEntity", "readme.md", "Hello World")
        
        result = service.read_note("TestEntity", "readme.md")
        
        assert result["success"] is True
        assert result["content"] == "Hello World"

    def test_read_nonexistent_note_fails(self, notes_service_with_temp_dir):
        """Test reading a non-existent note returns error."""
        service = notes_service_with_temp_dir
        
        result = service.read_note("TestEntity", "doesnotexist.md")
        
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_delete_note_removes_file(self, notes_service_with_temp_dir, temp_notes_dir):
        """Test deleting a note removes the file."""
        service = notes_service_with_temp_dir
        
        # Create a file first
        service.write_note("TestEntity", "todelete.md", "Delete me")
        expected_path = Path(temp_notes_dir) / "TestEntity" / "todelete.md"
        assert expected_path.exists()
        
        # Delete it
        result = service.delete_note("TestEntity", "todelete.md")
        
        assert result["success"] is True
        assert not expected_path.exists()

    def test_delete_index_md_prevented(self, notes_service_with_temp_dir):
        """Test that index.md cannot be deleted."""
        service = notes_service_with_temp_dir
        
        # Create index.md
        service.write_note("TestEntity", "index.md", "Important content")
        
        # Try to delete it
        result = service.delete_note("TestEntity", "index.md")
        
        assert result["success"] is False
        assert "cannot delete" in result["error"].lower()

    def test_list_notes_returns_files(self, notes_service_with_temp_dir, temp_notes_dir):
        """Test listing notes returns file information."""
        service = notes_service_with_temp_dir
        
        # Create some files
        service.write_note("TestEntity", "file1.md", "Content 1")
        service.write_note("TestEntity", "file2.txt", "Content 2")
        service.write_note("TestEntity", "data.json", '{"key": "value"}')
        
        result = service.list_notes("TestEntity")
        
        assert result["success"] is True
        assert len(result["files"]) == 3
        
        filenames = [f["filename"] for f in result["files"]]
        assert "file1.md" in filenames
        assert "file2.txt" in filenames
        assert "data.json" in filenames
        
        # Check file info structure
        for file_info in result["files"]:
            assert "filename" in file_info
            assert "size_bytes" in file_info
            assert "modified" in file_info

    def test_list_empty_folder(self, notes_service_with_temp_dir):
        """Test listing notes for entity with no files."""
        service = notes_service_with_temp_dir
        
        result = service.list_notes("NonexistentEntity")
        
        assert result["success"] is True
        assert result["files"] == []

    def test_invalid_extension_rejected(self, notes_service_with_temp_dir):
        """Test that invalid file extensions are rejected."""
        service = notes_service_with_temp_dir
        
        result = service.write_note("TestEntity", "script.py", "print('hello')")
        
        assert result["success"] is False
        assert "extension" in result["error"].lower()

    def test_path_traversal_prevented(self, notes_service_with_temp_dir, temp_notes_dir):
        """Test that path traversal attacks are prevented."""
        service = notes_service_with_temp_dir
        
        # Try to write outside the notes directory
        result = service.write_note("TestEntity", "../../../etc/passwd.md", "malicious")
        
        assert result["success"] is False
        assert "invalid" in result["error"].lower()


class TestNotesServiceSharedNotes:
    """Tests for shared notes functionality."""

    @pytest.fixture
    def temp_notes_dir(self):
        """Create a temporary directory for notes testing."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def notes_service_with_temp_dir(self, temp_notes_dir):
        """Create a NotesService pointing to a temp directory."""
        with patch("app.services.notes_service.settings") as mock_settings:
            mock_settings.notes_base_dir = temp_notes_dir
            service = NotesService()
            service._base_dir = None
            yield service

    def test_write_shared_note(self, notes_service_with_temp_dir, temp_notes_dir):
        """Test writing to shared notes folder."""
        service = notes_service_with_temp_dir
        
        result = service.write_note(
            entity_label="ignored",
            filename="shared_info.md",
            content="Shared content",
            shared=True,
        )
        
        assert result["success"] is True
        
        expected_path = Path(temp_notes_dir) / "shared" / "shared_info.md"
        assert expected_path.exists()

    def test_read_shared_note(self, notes_service_with_temp_dir):
        """Test reading from shared notes folder."""
        service = notes_service_with_temp_dir
        
        service.write_note("ignored", "info.md", "Shared info", shared=True)
        
        result = service.read_note("ignored", "info.md", shared=True)
        
        assert result["success"] is True
        assert result["content"] == "Shared info"

    def test_list_shared_notes(self, notes_service_with_temp_dir):
        """Test listing shared notes."""
        service = notes_service_with_temp_dir
        
        service.write_note("ignored", "file1.md", "Content 1", shared=True)
        service.write_note("ignored", "file2.md", "Content 2", shared=True)
        
        result = service.list_notes("ignored", shared=True)
        
        assert result["success"] is True
        assert len(result["files"]) == 2


class TestNotesServiceIndexInjection:
    """Tests for index.md auto-injection."""

    @pytest.fixture
    def temp_notes_dir(self):
        """Create a temporary directory for notes testing."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def notes_service_with_temp_dir(self, temp_notes_dir):
        """Create a NotesService pointing to a temp directory."""
        with patch("app.services.notes_service.settings") as mock_settings:
            mock_settings.notes_base_dir = temp_notes_dir
            service = NotesService()
            service._base_dir = None
            yield service

    def test_get_index_content_returns_content(self, notes_service_with_temp_dir):
        """Test get_index_content returns index.md content."""
        service = notes_service_with_temp_dir
        
        # Create index.md
        service.write_note("Kira", "index.md", "# Kira's Notes\n\nImportant things.")
        
        content = service.get_index_content("Kira")
        
        assert content == "# Kira's Notes\n\nImportant things."

    def test_get_index_content_returns_none_if_missing(self, notes_service_with_temp_dir):
        """Test get_index_content returns None if no index.md exists."""
        service = notes_service_with_temp_dir
        
        content = service.get_index_content("NonexistentEntity")
        
        assert content is None

    def test_get_shared_index_content(self, notes_service_with_temp_dir):
        """Test get_shared_index_content returns shared index.md."""
        service = notes_service_with_temp_dir
        
        service.write_note("ignored", "index.md", "Shared notes content", shared=True)
        
        content = service.get_shared_index_content()
        
        assert content == "Shared notes content"


class TestNotesTools:
    """Tests for notes tool functions."""

    @pytest.fixture
    def temp_notes_dir(self):
        """Create a temporary directory for notes testing."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture(autouse=True)
    def setup_notes_service(self, temp_notes_dir):
        """Set up notes service with temp directory for each test."""
        with patch("app.services.notes_service.settings") as mock_settings:
            mock_settings.notes_base_dir = temp_notes_dir
            mock_settings.notes_enabled = True
            notes_service._base_dir = None
            notes_service._base_dir = Path(temp_notes_dir)
            yield
            # Reset entity label after each test
            set_current_entity_label("")

    def test_entity_label_context(self):
        """Test setting and getting entity label context."""
        set_current_entity_label("TestEntity")
        assert get_current_entity_label() == "TestEntity"
        
        set_current_entity_label("AnotherEntity")
        assert get_current_entity_label() == "AnotherEntity"

    @pytest.mark.asyncio
    async def test_notes_write_tool(self, temp_notes_dir):
        """Test notes_write tool function."""
        set_current_entity_label("TestEntity")
        
        result = await _notes_write("test.md", "Test content")
        
        assert "Created" in result or "Updated" in result
        
        # Verify file was created
        expected_path = Path(temp_notes_dir) / "TestEntity" / "test.md"
        assert expected_path.exists()

    @pytest.mark.asyncio
    async def test_notes_read_tool(self, temp_notes_dir):
        """Test notes_read tool function."""
        set_current_entity_label("TestEntity")
        
        # Write first
        await _notes_write("readme.md", "Read me please")
        
        # Then read
        result = await _notes_read("readme.md")
        
        assert result == "Read me please"

    @pytest.mark.asyncio
    async def test_notes_list_tool(self, temp_notes_dir):
        """Test notes_list tool function."""
        set_current_entity_label("TestEntity")
        
        # Create some files
        await _notes_write("file1.md", "Content 1")
        await _notes_write("file2.md", "Content 2")
        
        result = await _notes_list()
        
        assert "file1.md" in result
        assert "file2.md" in result

    @pytest.mark.asyncio
    async def test_notes_delete_tool(self, temp_notes_dir):
        """Test notes_delete tool function."""
        set_current_entity_label("TestEntity")
        
        # Create a file
        await _notes_write("todelete.md", "Delete me")
        
        # Delete it
        result = await _notes_delete("todelete.md")
        
        assert "Deleted" in result
        
        # Verify it's gone
        expected_path = Path(temp_notes_dir) / "TestEntity" / "todelete.md"
        assert not expected_path.exists()

    @pytest.mark.asyncio
    async def test_notes_tool_without_entity_context(self):
        """Test notes tools fail gracefully without entity context."""
        set_current_entity_label("")  # Clear context
        
        result = await _notes_read("test.md")
        
        assert "Error" in result
        assert "entity context" in result.lower()

    @pytest.mark.asyncio
    async def test_notes_shared_operations(self, temp_notes_dir):
        """Test shared notes operations via tools."""
        set_current_entity_label("TestEntity")
        
        # Write to shared
        result = await _notes_write("shared_file.md", "Shared content", shared=True)
        assert "Created" in result
        
        # Read from shared
        result = await _notes_read("shared_file.md", shared=True)
        assert result == "Shared content"
        
        # List shared
        result = await _notes_list(shared=True)
        assert "shared_file.md" in result


class TestNotesToolRegistration:
    """Tests for notes tool registration."""

    def test_register_notes_tools(self):
        """Test that notes tools are properly registered."""
        tool_service = ToolService()
        
        with patch("app.services.notes_tools.settings") as mock_settings:
            mock_settings.notes_enabled = True
            register_notes_tools(tool_service)
        
        # Check all tools are registered
        assert tool_service.get_tool("notes_read") is not None
        assert tool_service.get_tool("notes_write") is not None
        assert tool_service.get_tool("notes_delete") is not None
        assert tool_service.get_tool("notes_list") is not None

    def test_tools_have_correct_category(self):
        """Test that notes tools have MEMORY category."""
        tool_service = ToolService()
        
        with patch("app.services.notes_tools.settings") as mock_settings:
            mock_settings.notes_enabled = True
            register_notes_tools(tool_service)
        
        for tool_name in ["notes_read", "notes_write", "notes_delete", "notes_list"]:
            tool = tool_service.get_tool(tool_name)
            assert tool.category == ToolCategory.MEMORY

    def test_tools_not_registered_when_disabled(self):
        """Test that tools aren't registered when notes_enabled=False."""
        tool_service = ToolService()
        
        with patch("app.services.notes_tools.settings") as mock_settings:
            mock_settings.notes_enabled = False
            register_notes_tools(tool_service)
        
        # Tools should not be registered
        assert tool_service.get_tool("notes_read") is None
        assert tool_service.get_tool("notes_write") is None
