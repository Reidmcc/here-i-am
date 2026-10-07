"""
Unit tests for NotesService and notes tools.
"""
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings as real_settings
from app.services.conversation_session import ConversationSession
from app.services.notes_service import NotesService, notes_service
from app.services.notes_tools import (
    NOTE_IN_CONTEXT_MARKER,
    _notes_delete,
    _notes_edit,
    _notes_list,
    _notes_read,
    _notes_write,
    consume_last_note_stamps,
    get_current_entity_label,
    note_content_hash,
    register_notes_tools,
    set_current_entity_label,
)
from app.services.tool_service import ToolCategory, ToolService


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

        # New turn: without this, notes_read dedup would return a pointer to
        # the same-turn notes_write instead of the content
        set_current_entity_label("TestEntity")

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

        # New turn: reset same-turn notes_read dedup state
        set_current_entity_label("TestEntity")

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


class TestNotesSearchThreshold:
    """notes_search should filter with the deliberate-query threshold."""

    def _fake_index(self, scores):
        """Build a fake Pinecone index whose search returns the given scores."""
        hits = []
        for i, score in enumerate(scores):
            hit = MagicMock()
            hit.to_dict.return_value = {
                "_score": score,
                "fields": {
                    "note_filename": f"note{i}.md",
                    "note_shared": False,
                    "chunk_index": 0,
                    "text": f"chunk {i}",
                },
            }
            hits.append(hit)
        index = MagicMock()
        index.search.return_value = MagicMock(result=MagicMock(hits=hits))
        return index

    @pytest.mark.asyncio
    async def test_uses_query_similarity_threshold(self):
        from app.services.notes_vector_service import NotesVectorService

        service = NotesVectorService()
        # Scores straddling the query threshold (0.2) but all below the
        # automatic-retrieval threshold (0.4).
        index = self._fake_index([0.35, 0.25, 0.15, 0.05])

        with patch("app.services.notes_vector_service.settings") as mock_settings, \
             patch.object(service, "_get_index_for_label", return_value=index):
            mock_settings.query_similarity_threshold = 0.2
            mock_settings.similarity_threshold = 0.4

            matches = await service.search_notes("q", "query", num_results=5)

        # Only the two hits >= 0.2 survive; the stricter 0.4 would have dropped all.
        assert [m["score"] for m in matches] == [0.35, 0.25]

    @pytest.mark.asyncio
    async def test_respects_num_results_after_filtering(self):
        from app.services.notes_vector_service import NotesVectorService

        service = NotesVectorService()
        index = self._fake_index([0.9, 0.8, 0.7, 0.6])

        with patch("app.services.notes_vector_service.settings") as mock_settings, \
             patch.object(service, "_get_index_for_label", return_value=index):
            mock_settings.query_similarity_threshold = 0.2

            matches = await service.search_notes("q", "query", num_results=2)

        assert len(matches) == 2
        # Fetches extra candidates (top_k = num_results * 2) to survive filtering.
        assert index.search.call_args.kwargs["query"]["top_k"] == 4


class TestNotesServiceEdit:
    """Tests for NotesService.edit_note (exact string replacement)."""

    @pytest.fixture
    def temp_notes_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def notes_service_with_temp_dir(self, temp_notes_dir):
        with patch("app.services.notes_service.settings") as mock_settings:
            mock_settings.notes_base_dir = temp_notes_dir
            service = NotesService()
            service._base_dir = None
            yield service

    def test_edit_note_replaces_string(self, notes_service_with_temp_dir, temp_notes_dir):
        service = notes_service_with_temp_dir
        service.write_note("TestEntity", "plan.md", "# Plan\n\nStatus: draft\n")

        result = service.edit_note(
            "TestEntity", "plan.md", old_string="Status: draft", new_string="Status: final"
        )

        assert result["success"] is True
        assert result["replacements"] == 1
        assert result["new_content"] == "# Plan\n\nStatus: final\n"
        path = Path(temp_notes_dir) / "TestEntity" / "plan.md"
        assert path.read_text() == "# Plan\n\nStatus: final\n"

    def test_edit_note_requires_existing_file(self, notes_service_with_temp_dir):
        result = notes_service_with_temp_dir.edit_note(
            "TestEntity", "missing.md", old_string="a", new_string="b"
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()
        assert "notes_write" in result["error"]

    def test_edit_note_old_string_not_found(self, notes_service_with_temp_dir):
        service = notes_service_with_temp_dir
        service.write_note("TestEntity", "plan.md", "some content")

        result = service.edit_note("TestEntity", "plan.md", old_string="absent", new_string="x")

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_edit_note_ambiguous_without_replace_all(self, notes_service_with_temp_dir, temp_notes_dir):
        service = notes_service_with_temp_dir
        service.write_note("TestEntity", "plan.md", "item\nitem\n")

        result = service.edit_note("TestEntity", "plan.md", old_string="item", new_string="thing")

        assert result["success"] is False
        assert "2 times" in result["error"]
        assert "replace_all" in result["error"]
        # File untouched
        path = Path(temp_notes_dir) / "TestEntity" / "plan.md"
        assert path.read_text() == "item\nitem\n"

    def test_edit_note_replace_all(self, notes_service_with_temp_dir, temp_notes_dir):
        service = notes_service_with_temp_dir
        service.write_note("TestEntity", "plan.md", "item\nitem\n")

        result = service.edit_note(
            "TestEntity", "plan.md", old_string="item", new_string="thing", replace_all=True
        )

        assert result["success"] is True
        assert result["replacements"] == 2
        path = Path(temp_notes_dir) / "TestEntity" / "plan.md"
        assert path.read_text() == "thing\nthing\n"

    def test_edit_note_identical_strings_rejected(self, notes_service_with_temp_dir):
        service = notes_service_with_temp_dir
        service.write_note("TestEntity", "plan.md", "content")

        result = service.edit_note("TestEntity", "plan.md", old_string="content", new_string="content")

        assert result["success"] is False
        assert "identical" in result["error"]

    def test_edit_note_empty_old_string_rejected(self, notes_service_with_temp_dir):
        service = notes_service_with_temp_dir
        service.write_note("TestEntity", "plan.md", "content")

        result = service.edit_note("TestEntity", "plan.md", old_string="", new_string="x")

        assert result["success"] is False
        assert "empty" in result["error"]

    def test_edit_note_invalid_extension(self, notes_service_with_temp_dir):
        result = notes_service_with_temp_dir.edit_note(
            "TestEntity", "script.py", old_string="a", new_string="b"
        )
        assert result["success"] is False
        assert "extension" in result["error"].lower()

    def test_edit_note_shared(self, notes_service_with_temp_dir, temp_notes_dir):
        service = notes_service_with_temp_dir
        service.write_note("TestEntity", "shared.md", "v1", shared=True)

        result = service.edit_note(
            "TestEntity", "shared.md", old_string="v1", new_string="v2", shared=True
        )

        assert result["success"] is True
        path = Path(temp_notes_dir) / "shared" / "shared.md"
        assert path.read_text() == "v2"


class TestNotesEditTool:
    """Tests for the notes_edit tool executor."""

    @pytest.fixture
    def temp_notes_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture(autouse=True)
    def setup_notes_service(self, temp_notes_dir):
        with patch("app.services.notes_service.settings") as mock_settings:
            mock_settings.notes_base_dir = temp_notes_dir
            mock_settings.notes_enabled = True
            notes_service._base_dir = Path(temp_notes_dir)
            yield
            notes_service._base_dir = None
            set_current_entity_label("")

    @pytest.mark.asyncio
    async def test_notes_edit_tool(self, temp_notes_dir):
        set_current_entity_label("TestEntity")
        await _notes_write("plan.md", "Status: draft")

        result = await _notes_edit("plan.md", old_string="draft", new_string="final")

        assert result == "Edited 'plan.md' in your notes (1 replacement)"
        path = Path(temp_notes_dir) / "TestEntity" / "plan.md"
        assert path.read_text() == "Status: final"

    @pytest.mark.asyncio
    async def test_notes_edit_tool_replace_all_plural(self, temp_notes_dir):
        set_current_entity_label("TestEntity")
        await _notes_write("plan.md", "a a")

        result = await _notes_edit("plan.md", old_string="a", new_string="b", replace_all=True)

        assert result == "Edited 'plan.md' in your notes (2 replacements)"

    @pytest.mark.asyncio
    async def test_notes_edit_tool_missing_file(self):
        set_current_entity_label("TestEntity")

        result = await _notes_edit("missing.md", old_string="a", new_string="b")

        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_notes_edit_tool_without_entity_context(self):
        set_current_entity_label("")

        result = await _notes_edit("plan.md", old_string="a", new_string="b")

        assert "Error" in result
        assert "entity context" in result.lower()

    @pytest.mark.asyncio
    async def test_notes_edit_records_delta_stamp(self, temp_notes_dir):
        set_current_entity_label("TestEntity")
        await _notes_write("plan.md", "Status: draft")
        consume_last_note_stamps()  # discard the write stamp

        await _notes_edit("plan.md", old_string="draft", new_string="final")

        stamps = consume_last_note_stamps()
        assert stamps == [{
            "owner": "TestEntity",
            "filename": "plan.md",
            "hash": note_content_hash("Status: final"),
            "source": "edit",
        }]
        # Consuming clears the pending stamps
        assert consume_last_note_stamps() == []

    @pytest.mark.asyncio
    async def test_notes_write_records_full_stamp(self, temp_notes_dir):
        set_current_entity_label("TestEntity")

        await _notes_write("plan.md", "Status: draft")

        stamps = consume_last_note_stamps()
        assert stamps == [{
            "owner": "TestEntity",
            "filename": "plan.md",
            "hash": note_content_hash("Status: draft"),
            "source": "write",
        }]


class TestNotesReadDedup:
    """Tests for notes_read returning pointers to in-context note copies."""

    @pytest.fixture
    def temp_notes_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture(autouse=True)
    def setup_notes_service(self, temp_notes_dir):
        with patch("app.services.notes_service.settings") as mock_settings:
            mock_settings.notes_base_dir = temp_notes_dir
            mock_settings.notes_enabled = True
            notes_service._base_dir = Path(temp_notes_dir)
            yield
            notes_service._base_dir = None
            set_current_entity_label("")

    def _session_with_stamps(self, stamps, is_multi_entity=False):
        session = ConversationSession(conversation_id="conv-1", is_multi_entity=is_multi_entity)
        if stamps:
            session.conversation_context.append({
                "role": "user",
                "content": "[ENTITY NOTES]...",
                "is_notes": True,
                "note_stamps": stamps,
            })
        return session

    @pytest.mark.asyncio
    async def test_same_turn_write_then_read_returns_pointer(self, temp_notes_dir):
        set_current_entity_label("TestEntity")
        await _notes_write("plan.md", "Status: draft")

        result = await _notes_read("plan.md")

        assert result.startswith(NOTE_IN_CONTEXT_MARKER)
        assert "notes_write" in result

    @pytest.mark.asyncio
    async def test_same_turn_read_then_read_returns_pointer(self, temp_notes_dir):
        set_current_entity_label("TestEntity")
        await _notes_write("plan.md", "Status: draft")
        set_current_entity_label("TestEntity")  # new turn

        first = await _notes_read("plan.md")
        second = await _notes_read("plan.md")

        assert first == "Status: draft"
        assert second.startswith(NOTE_IN_CONTEXT_MARKER)
        assert "notes_read" in second

    @pytest.mark.asyncio
    async def test_seed_stamp_returns_pointer_to_notes_block(self, temp_notes_dir):
        content = "# My index\n"
        (Path(temp_notes_dir) / "TestEntity").mkdir(parents=True)
        (Path(temp_notes_dir) / "TestEntity" / "index.md").write_text(content)

        session = self._session_with_stamps([{
            "owner": "TestEntity",
            "filename": "index.md",
            "hash": note_content_hash(content),
            "source": "seed",
        }])
        set_current_entity_label("TestEntity", session=session)

        result = await _notes_read("index.md")

        assert result.startswith(NOTE_IN_CONTEXT_MARKER)
        assert "[ENTITY NOTES]" in result

    @pytest.mark.asyncio
    async def test_stale_seed_stamp_returns_full_content(self, temp_notes_dir):
        (Path(temp_notes_dir) / "TestEntity").mkdir(parents=True)
        (Path(temp_notes_dir) / "TestEntity" / "index.md").write_text("changed on disk")

        session = self._session_with_stamps([{
            "owner": "TestEntity",
            "filename": "index.md",
            "hash": note_content_hash("old content"),
            "source": "seed",
        }])
        set_current_entity_label("TestEntity", session=session)

        result = await _notes_read("index.md")

        assert result == "changed on disk"

    @pytest.mark.asyncio
    async def test_edit_chain_returns_combine_pointer(self, temp_notes_dir):
        """Seed copy + a later notes_edit: pointer says to combine them."""
        (Path(temp_notes_dir) / "TestEntity").mkdir(parents=True)
        (Path(temp_notes_dir) / "TestEntity" / "index.md").write_text("Status: final")

        session = self._session_with_stamps([{
            "owner": "TestEntity",
            "filename": "index.md",
            "hash": note_content_hash("Status: draft"),
            "source": "seed",
        }])
        # A later tool exchange carried the edit's delta stamp
        session.conversation_context.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Edited..."}],
            "is_tool_result": True,
            "note_stamps": [{
                "owner": "TestEntity",
                "filename": "index.md",
                "hash": note_content_hash("Status: final"),
                "source": "edit",
            }],
        })
        set_current_entity_label("TestEntity", session=session)

        result = await _notes_read("index.md")

        assert result.startswith(NOTE_IN_CONTEXT_MARKER)
        assert "notes_edit" in result
        assert "[ENTITY NOTES]" in result

    @pytest.mark.asyncio
    async def test_delta_without_full_base_returns_content(self, temp_notes_dir):
        """An edit stamp whose full base copy was trimmed out: no dedup."""
        (Path(temp_notes_dir) / "TestEntity").mkdir(parents=True)
        (Path(temp_notes_dir) / "TestEntity" / "notes.md").write_text("Status: final")

        session = ConversationSession(conversation_id="conv-1")
        session.conversation_context.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Edited..."}],
            "is_tool_result": True,
            "note_stamps": [{
                "owner": "TestEntity",
                "filename": "notes.md",
                "hash": note_content_hash("Status: final"),
                "source": "edit",
            }],
        })
        set_current_entity_label("TestEntity", session=session)

        result = await _notes_read("notes.md")

        assert result == "Status: final"

    @pytest.mark.asyncio
    async def test_dedup_disabled_returns_content(self, temp_notes_dir, monkeypatch):
        monkeypatch.setattr(real_settings, "notes_read_dedup_enabled", False)
        set_current_entity_label("TestEntity")
        await _notes_write("plan.md", "Status: draft")

        result = await _notes_read("plan.md")

        assert result == "Status: draft"

    @pytest.mark.asyncio
    async def test_owner_scoping_ignores_other_entity_stamps(self, temp_notes_dir):
        """Another entity's stamp for the same filename must not dedupe ours."""
        (Path(temp_notes_dir) / "TestEntity").mkdir(parents=True)
        (Path(temp_notes_dir) / "TestEntity" / "ideas.md").write_text("mine")

        session = self._session_with_stamps([{
            "owner": "OtherEntity",
            "filename": "ideas.md",
            "hash": note_content_hash("mine"),
            "source": "read",
        }])
        set_current_entity_label("TestEntity", session=session)

        result = await _notes_read("ideas.md")

        assert result == "mine"

    @pytest.mark.asyncio
    async def test_multi_entity_index_points_to_per_turn_block(self, temp_notes_dir):
        (Path(temp_notes_dir) / "TestEntity").mkdir(parents=True)
        (Path(temp_notes_dir) / "TestEntity" / "index.md").write_text("# index")

        session = ConversationSession(conversation_id="conv-1", is_multi_entity=True)
        set_current_entity_label("TestEntity", session=session)

        result = await _notes_read("index.md")

        assert result.startswith(NOTE_IN_CONTEXT_MARKER)
        assert "[ENTITY NOTES]" in result
        assert "re-read from disk every turn" in result

    @pytest.mark.asyncio
    async def test_multi_entity_index_edited_this_turn(self, temp_notes_dir):
        """After a same-turn edit, the pointer says block + this turn's edits."""
        (Path(temp_notes_dir) / "TestEntity").mkdir(parents=True)
        (Path(temp_notes_dir) / "TestEntity" / "index.md").write_text("Status: draft")

        session = ConversationSession(conversation_id="conv-1", is_multi_entity=True)
        set_current_entity_label("TestEntity", session=session)
        await _notes_edit("index.md", old_string="draft", new_string="final")

        result = await _notes_read("index.md")

        assert result.startswith(NOTE_IN_CONTEXT_MARKER)
        assert "notes_edit" in result

    @pytest.mark.asyncio
    async def test_multi_entity_other_files_not_deduped_without_stamps(self, temp_notes_dir):
        """Only index files get the per-turn-block treatment in multi-entity."""
        (Path(temp_notes_dir) / "TestEntity").mkdir(parents=True)
        (Path(temp_notes_dir) / "TestEntity" / "other.md").write_text("content")

        session = ConversationSession(conversation_id="conv-1", is_multi_entity=True)
        set_current_entity_label("TestEntity", session=session)

        result = await _notes_read("other.md")

        assert result == "content"


class TestNotesVectorSync:
    """Incremental notes sync (Claude Code mode's notes bridge)."""

    @pytest.fixture
    def synced_service(self, tmp_path, monkeypatch):
        """A NotesVectorService with fake vectorize/remove that honor the
        hash-recording contract, over a temp notes tree."""
        from app.services.notes_vector_service import (
            NotesVectorService,
            _content_hash,
        )

        monkeypatch.setattr(notes_service, "_base_dir", tmp_path)
        entity_dir = tmp_path / "TestEntity"
        entity_dir.mkdir(parents=True)
        (entity_dir / "index.md").write_text("index content", encoding="utf-8")
        (entity_dir / "projects.md").write_text("projects", encoding="utf-8")
        (tmp_path / "shared").mkdir()
        (tmp_path / "shared" / "rules.md").write_text("rules", encoding="utf-8")

        service = NotesVectorService()
        vectorized = []
        removed = []

        async def fake_vectorize(entity_label, filename, content, shared=False, log_result=True):
            vectorized.append((filename, shared))
            key = (service._scope_key(entity_label, shared), filename)
            service._synced_hashes[key] = _content_hash(content)
            return True

        async def fake_remove(entity_label, filename, shared=False):
            removed.append((filename, shared))
            service._synced_hashes.pop(
                (service._scope_key(entity_label, shared), filename), None
            )
            return True

        monkeypatch.setattr(service, "vectorize_note", fake_vectorize)
        monkeypatch.setattr(service, "remove_note_vectors", fake_remove)
        from app.services.notes_vector_service import memory_service as nvs_memory
        monkeypatch.setattr(nvs_memory, "is_configured", lambda entity_id=None: True)
        return service, tmp_path, vectorized, removed

    @pytest.mark.asyncio
    async def test_first_sync_vectorizes_everything(self, synced_service):
        service, _, vectorized, _ = synced_service

        summary = await service.sync_entity_notes("TestEntity")

        assert summary["indexed"] == 3
        assert summary["errors"] == []
        assert sorted(vectorized) == [
            ("index.md", False), ("projects.md", False), ("rules.md", True),
        ]

    @pytest.mark.asyncio
    async def test_second_sync_is_a_no_op(self, synced_service):
        service, _, vectorized, _ = synced_service
        await service.sync_entity_notes("TestEntity")
        vectorized.clear()

        summary = await service.sync_entity_notes("TestEntity")

        assert summary["indexed"] == 0
        assert summary["unchanged"] == 3
        assert vectorized == []

    @pytest.mark.asyncio
    async def test_only_changed_files_revectorize(self, synced_service):
        service, tmp_path, vectorized, _ = synced_service
        await service.sync_entity_notes("TestEntity")
        vectorized.clear()

        (tmp_path / "TestEntity" / "projects.md").write_text(
            "projects, updated by a Claude Code session", encoding="utf-8"
        )

        summary = await service.sync_entity_notes("TestEntity")

        assert summary["indexed"] == 1
        assert summary["unchanged"] == 2
        assert vectorized == [("projects.md", False)]

    @pytest.mark.asyncio
    async def test_deleted_files_lose_their_vectors(self, synced_service):
        service, tmp_path, vectorized, removed = synced_service
        await service.sync_entity_notes("TestEntity")
        vectorized.clear()

        (tmp_path / "TestEntity" / "projects.md").unlink()

        summary = await service.sync_entity_notes("TestEntity")

        assert summary["removed"] == 1
        assert removed == [("projects.md", False)]
        # And it stays gone on the next sync
        summary2 = await service.sync_entity_notes("TestEntity")
        assert summary2["removed"] == 0

    @pytest.mark.asyncio
    async def test_unconfigured_memory_reports_error(self, tmp_path, monkeypatch):
        from app.services.notes_vector_service import NotesVectorService
        from app.services.notes_vector_service import memory_service as nvs_memory
        monkeypatch.setattr(nvs_memory, "is_configured", lambda entity_id=None: False)
        service = NotesVectorService()

        summary = await service.sync_entity_notes("TestEntity")

        assert summary["indexed"] == 0
        assert "Pinecone not configured" in summary["errors"]


class _FakeNotesIndex:
    """A Pinecone index stand-in recording what vectorize/remove send it.

    Holds ids in a set, enforces the real per-request caps (96 upsert
    records, 1000 delete ids) by raising like the server does, and can be
    told to fail the Nth upsert call or every listing.
    """

    def __init__(self, existing_ids=(), fail_upsert_call=None, fail_listing=False):
        self.ids = set(existing_ids)
        self.upsert_sizes = []
        self.delete_sizes = []
        self.fail_upsert_call = fail_upsert_call
        self.fail_listing = fail_listing

    def upsert_records(self, namespace, records):
        self.upsert_sizes.append(len(records))
        if len(records) > 96:
            raise RuntimeError("Batch size exceeds 96")
        if self.fail_upsert_call == len(self.upsert_sizes):
            raise RuntimeError("simulated upsert failure")
        self.ids.update(r["_id"] for r in records)

    def list_paginated(self, namespace, limit, prefix, pagination_token=None):
        from types import SimpleNamespace

        if self.fail_listing:
            raise RuntimeError("simulated listing failure")
        matching = sorted(i for i in self.ids if i.startswith(prefix))
        start = int(pagination_token or 0)
        page = matching[start : start + limit]
        more = start + limit < len(matching)
        return SimpleNamespace(
            vectors=[SimpleNamespace(id=i) for i in page],
            pagination=SimpleNamespace(next=str(start + limit)) if more else None,
        )

    def delete(self, ids, namespace):
        self.delete_sizes.append(len(ids))
        if len(ids) > 1000:
            raise RuntimeError("too many ids")
        self.ids.difference_update(ids)


class TestNotesVectorizeBatching:
    """Large notes vectorize in batches under Pinecone's 96-record cap, and a
    failure never leaves a note without vectors or with its hash recorded."""

    CHUNKS = 200

    @staticmethod
    def _note(chunks):
        # 1500-char paragraphs: two never fit in one 2000-char chunk, so each
        # paragraph is exactly one chunk
        return "\n\n".join(f"{i:04d}" + "x" * 1496 for i in range(chunks))

    @staticmethod
    def _ids(count, start=0):
        return {f"note:private:big.md:{i}" for i in range(start, count)}

    @pytest.fixture
    def service_with(self, monkeypatch):
        from app.services.notes_vector_service import NotesVectorService

        def make(index):
            service = NotesVectorService()
            monkeypatch.setattr(service, "_target_indexes", lambda label, shared: [index])
            return service

        return make

    def test_fixture_note_has_the_intended_chunk_count(self):
        from app.services.notes_vector_service import chunk_note_content

        assert len(chunk_note_content(self._note(self.CHUNKS))) == self.CHUNKS

    @pytest.mark.asyncio
    async def test_no_upsert_call_exceeds_the_batch_size(self, service_with):
        from app.services.memory_service import UPSERT_BATCH_SIZE

        index = _FakeNotesIndex()
        service = service_with(index)

        ok = await service.vectorize_note("TestEntity", "big.md", self._note(self.CHUNKS))

        assert ok is True
        assert UPSERT_BATCH_SIZE < 96
        assert max(index.upsert_sizes) <= UPSERT_BATCH_SIZE
        assert sum(index.upsert_sizes) == self.CHUNKS
        assert index.ids == self._ids(self.CHUNKS)
        assert ("private:TestEntity", "big.md") in service._synced_hashes

    @pytest.mark.asyncio
    async def test_failure_midway_keeps_old_vectors_and_leaves_hash_unrecorded(
        self, service_with
    ):
        # The note was indexed before at 300 chunks; the rewrite fails on its
        # second batch
        index = _FakeNotesIndex(existing_ids=self._ids(300), fail_upsert_call=2)
        service = service_with(index)

        ok = await service.vectorize_note("TestEntity", "big.md", self._note(self.CHUNKS))

        assert ok is False
        assert index.delete_sizes == []  # nothing pruned before the upsert finished
        assert index.ids == self._ids(300)  # still searchable
        assert ("private:TestEntity", "big.md") not in service._synced_hashes

    @pytest.mark.asyncio
    async def test_prune_removes_only_chunks_past_the_new_end(self, service_with):
        index = _FakeNotesIndex(existing_ids=self._ids(300))
        service = service_with(index)

        ok = await service.vectorize_note("TestEntity", "big.md", self._note(self.CHUNKS))

        assert ok is True
        assert index.ids == self._ids(self.CHUNKS)

    @pytest.mark.asyncio
    async def test_fallback_delete_reaches_past_64_and_past_the_known_count(
        self, service_with
    ):
        from app.services.memory_service import DELETE_BATCH_SIZE

        # Listing fails; the file was last written at 1500 chunks
        index = _FakeNotesIndex(existing_ids=self._ids(1500), fail_listing=True)
        service = service_with(index)
        service._chunk_counts[("private:TestEntity", "big.md")] = 1500

        ok = await service.vectorize_note("TestEntity", "big.md", self._note(self.CHUNKS))

        assert ok is True
        assert index.ids == self._ids(self.CHUNKS)
        assert max(index.delete_sizes) <= DELETE_BATCH_SIZE

    @pytest.mark.asyncio
    async def test_fallback_delete_without_a_known_count_uses_the_generous_bound(
        self, service_with
    ):
        # After a restart nothing is known; the bound still covers a 156-chunk note
        index = _FakeNotesIndex(existing_ids=self._ids(156), fail_listing=True)
        service = service_with(index)

        removed = await service.remove_note_vectors("TestEntity", "big.md")

        assert removed is True
        assert index.ids == set()

    @pytest.mark.asyncio
    async def test_failed_removal_stays_tracked_for_retry(self, service_with):
        index = _FakeNotesIndex(existing_ids=self._ids(10))
        service = service_with(index)
        key = ("private:TestEntity", "big.md")
        service._synced_hashes[key] = "old-hash"

        def failing_delete(ids, namespace):
            raise RuntimeError("simulated delete failure")

        index.delete = failing_delete

        removed = await service.remove_note_vectors("TestEntity", "big.md")

        assert removed is False
        assert key in service._synced_hashes

    @pytest.mark.asyncio
    async def test_failed_prune_after_a_good_upsert_leaves_hash_unrecorded(
        self, service_with
    ):
        index = _FakeNotesIndex(existing_ids=self._ids(300))
        service = service_with(index)
        key = ("private:TestEntity", "big.md")
        service._chunk_counts[key] = 300

        def failing_delete(ids, namespace):
            raise RuntimeError("simulated delete failure")

        index.delete = failing_delete

        ok = await service.vectorize_note("TestEntity", "big.md", self._note(self.CHUNKS))

        assert ok is False
        assert key not in service._synced_hashes
        # Chunks 200..299 may still be there; the next fallback must reach them
        assert service._chunk_counts[key] == 300

    @pytest.mark.asyncio
    async def test_shared_note_failing_in_one_of_two_indexes_is_not_done(
        self, monkeypatch
    ):
        from app.services.notes_vector_service import NotesVectorService

        good = _FakeNotesIndex()
        bad = _FakeNotesIndex(fail_upsert_call=1)
        service = NotesVectorService()
        # Both orders: a "last index decides" bug gets through only when the
        # failing index comes first, an "any index" bug only when it's second
        for order in ([bad, good], [good, bad]):
            bad.upsert_sizes.clear()
            monkeypatch.setattr(
                service, "_target_indexes", lambda label, shared, o=order: o
            )

            ok = await service.vectorize_note(
                "TestEntity", "big.md", self._note(self.CHUNKS), shared=True
            )

            assert ok is False
            assert ("shared", "big.md") not in service._synced_hashes


class TestNotesReindexPrunesOrphans:
    """reindex_all deletes the vectors of note files no longer on disk. The
    incremental sync can't: it removes only files it has tracked since the
    backend started, so a note deleted while the backend was down (or before
    the first sync after a restart) kept its chunks in notes_search."""

    @pytest.fixture
    def world(self, tmp_path, monkeypatch):
        """Two entities, Ada (index-a) and Bo (index-b), over a temp notes
        tree; returns (service, indexes by name, notes root, entities)."""
        import sys
        from types import SimpleNamespace

        from app.services.notes_vector_service import NotesVectorService

        # The module itself: app.services binds this name to the singleton
        nvs = sys.modules["app.services.notes_vector_service"]
        monkeypatch.setattr(notes_service, "_base_dir", tmp_path)
        for folder in ("Ada", "Bo", "shared"):
            (tmp_path / folder).mkdir()

        indexes = {"index-a": _FakeNotesIndex(), "index-b": _FakeNotesIndex()}
        entities = [
            SimpleNamespace(label="Ada", index_name="index-a"),
            SimpleNamespace(label="Bo", index_name="index-b"),
        ]
        monkeypatch.setattr(nvs, "settings", SimpleNamespace(get_entities=lambda: entities))
        monkeypatch.setattr(nvs.memory_service, "is_configured", lambda entity_id=None: True)
        monkeypatch.setattr(nvs.memory_service, "get_index", lambda name=None: indexes.get(name))
        # A fresh service: the in-memory maps are empty, as after a restart
        return NotesVectorService(), indexes, tmp_path, entities

    @staticmethod
    def _write(root, folder, filename, text="some note text"):
        (root / folder / filename).write_text(text, encoding="utf-8")

    @staticmethod
    def _refuse_deletes(ids, namespace):
        raise RuntimeError("simulated delete failure")

    @pytest.mark.asyncio
    async def test_private_note_deleted_while_down_is_pruned(self, world):
        service, indexes, root, _ = world
        self._write(root, "Ada", "kept.md")
        # gone.md was vectorized in an earlier run and deleted while the
        # backend was down; the fresh service has never heard of it
        indexes["index-a"].ids.update({"note:private:gone.md:0", "note:private:gone.md:1"})

        summary = await service.reindex_all()

        assert summary["removed"] == 1
        assert summary["errors"] == []
        assert indexes["index-a"].ids == {"note:private:kept.md:0"}

    @pytest.mark.asyncio
    async def test_sync_alone_never_sees_it(self, world):
        # The gap this closes, pinned: a fresh service's sync leaves the orphan
        service, indexes, root, _ = world
        self._write(root, "Ada", "kept.md")
        indexes["index-a"].ids.add("note:private:gone.md:0")

        summary = await service.sync_entity_notes("Ada")

        assert summary["removed"] == 0
        assert "note:private:gone.md:0" in indexes["index-a"].ids

    @pytest.mark.asyncio
    async def test_private_prune_follows_each_entitys_own_folder(self, world):
        service, indexes, root, _ = world
        # Both entities once had plan.md; only Bo still does
        self._write(root, "Bo", "plan.md")
        indexes["index-a"].ids.add("note:private:plan.md:0")
        indexes["index-b"].ids.add("note:private:plan.md:0")

        summary = await service.reindex_all()

        assert summary["removed"] == 1
        assert "note:private:plan.md:0" not in indexes["index-a"].ids
        assert "note:private:plan.md:0" in indexes["index-b"].ids

    @pytest.mark.asyncio
    async def test_shared_orphan_is_pruned_from_every_index_and_counted_once(self, world):
        service, indexes, root, _ = world
        self._write(root, "shared", "rules.md")
        for index in indexes.values():
            index.ids.update({"note:shared:old.md:0", "note:shared:old.md:1"})

        summary = await service.reindex_all()

        assert summary["removed"] == 1
        for index in indexes.values():
            assert index.ids == {"note:shared:rules.md:0"}

    @pytest.mark.asyncio
    async def test_entities_sharing_an_index_keep_each_others_private_notes(self, world):
        service, indexes, root, entities = world
        entities[1].index_name = "index-a"  # Bo now lives in Ada's index too
        self._write(root, "Bo", "bo-only.md")
        indexes["index-a"].ids.update(
            {"note:private:bo-only.md:0", "note:private:nobodys.md:0"}
        )

        summary = await service.reindex_all()

        assert summary["removed"] == 1
        assert "note:private:bo-only.md:0" in indexes["index-a"].ids
        assert "note:private:nobodys.md:0" not in indexes["index-a"].ids

    @pytest.mark.asyncio
    async def test_orphans_are_deleted_in_batches_under_the_cap(self, world):
        from app.services.memory_service import DELETE_BATCH_SIZE

        service, indexes, root, _ = world
        indexes["index-a"].ids.update(f"note:private:huge.md:{i}" for i in range(1500))

        summary = await service.reindex_all()

        assert summary["removed"] == 1
        assert summary["errors"] == []
        assert indexes["index-a"].ids == set()
        assert max(indexes["index-a"].delete_sizes) <= DELETE_BATCH_SIZE

    @pytest.mark.asyncio
    async def test_filenames_with_colons_and_foreign_ids(self, world):
        service, indexes, root, _ = world
        foreign = {"note:shared:no-chunk-number", "note:private:x.md:abc", "memory:123"}
        indexes["index-a"].ids.update(foreign | {"note:private:a:b.md:3"})

        summary = await service.reindex_all()

        # "a:b.md" parsed whole (between the scope and the LAST colon) and
        # pruned; ids not in our shape are left alone rather than guessed at
        assert summary["removed"] == 1
        assert indexes["index-a"].ids == foreign

    @pytest.mark.asyncio
    async def test_failed_id_listing_prunes_nothing_and_says_so(self, world):
        service, indexes, root, _ = world
        indexes["index-a"].ids.add("note:private:gone.md:0")
        indexes["index-a"].fail_listing = True

        summary = await service.reindex_all()

        assert summary["removed"] == 0
        assert "note:private:gone.md:0" in indexes["index-a"].ids
        assert any("orphans not pruned" in e for e in summary["errors"])

    @pytest.mark.asyncio
    async def test_failed_disk_listing_is_not_read_as_an_empty_folder(
        self, world, monkeypatch
    ):
        service, indexes, root, _ = world
        self._write(root, "shared", "rules.md")
        indexes["index-a"].ids.add("note:shared:rules.md:0")
        real_list = notes_service.list_notes

        def broken_shared(label, shared=False):
            if shared:
                return {"success": False, "error": "disk on fire"}
            return real_list(label, shared=shared)

        monkeypatch.setattr(notes_service, "list_notes", broken_shared)

        summary = await service.reindex_all()

        assert "note:shared:rules.md:0" in indexes["index-a"].ids
        assert any("listing note files failed" in e for e in summary["errors"])

    @pytest.mark.asyncio
    async def test_failed_delete_is_an_error_not_a_removal(self, world, monkeypatch):
        service, indexes, root, _ = world
        indexes["index-a"].ids.add("note:private:gone.md:0")
        monkeypatch.setattr(indexes["index-a"], "delete", self._refuse_deletes)

        summary = await service.reindex_all()

        assert summary["removed"] == 0
        assert any("orphaned chunk(s) failed" in e for e in summary["errors"])

    # Both orders: a "last index decides" bug gets through only when the
    # failing index comes first, an "any index" bug only when it's second
    @pytest.mark.parametrize("failing,good", [("index-a", "index-b"), ("index-b", "index-a")])
    @pytest.mark.asyncio
    async def test_shared_note_failing_in_one_index_is_not_counted(
        self, world, monkeypatch, failing, good
    ):
        service, indexes, root, _ = world
        for index in indexes.values():
            index.ids.add("note:shared:old.md:0")
        monkeypatch.setattr(indexes[failing], "delete", self._refuse_deletes)

        summary = await service.reindex_all()

        assert summary["removed"] == 0
        assert indexes[good].ids == set()
        assert indexes[failing].ids == {"note:shared:old.md:0"}

    @pytest.mark.asyncio
    async def test_note_created_mid_prune_is_kept(self, world, monkeypatch):
        # A sync writes new.md and its chunk while the prune is listing ids.
        # Disk is read after the ids, so the file is there and its chunk kept.
        service, indexes, root, _ = world
        index = indexes["index-a"]
        real_listing = index.list_paginated
        landed = []

        def listing_while_a_sync_lands(namespace, limit, prefix, pagination_token=None):
            if prefix == "note:private:" and not landed:
                landed.append(True)
                self._write(root, "Ada", "new.md")
                index.ids.add("note:private:new.md:0")
            return real_listing(namespace, limit, prefix, pagination_token)

        monkeypatch.setattr(index, "list_paginated", listing_while_a_sync_lands)

        summary = await service.reindex_all()

        assert landed
        assert summary["removed"] == 0
        assert "note:private:new.md:0" in index.ids

    @pytest.mark.asyncio
    async def test_route_reports_removed(self, monkeypatch):
        import app.routes.notes as notes_routes

        async def fake_reindex():
            return {"indexed": 3, "removed": 2, "errors": []}

        monkeypatch.setattr(notes_routes.settings, "notes_enabled", True)
        monkeypatch.setattr(
            notes_routes.memory_service, "is_configured", lambda entity_id=None: True
        )
        monkeypatch.setattr(notes_routes.notes_vector_service, "reindex_all", fake_reindex)

        assert await notes_routes.reindex_notes() == {"indexed": 3, "removed": 2, "errors": []}
