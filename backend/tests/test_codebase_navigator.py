"""
Unit tests for Codebase Navigator module.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path
import tempfile
import shutil
import json
import os

from app.services.codebase_navigator import (
    CodebaseIndexer,
    NavigatorClient,
    NavigatorCache,
    NavigatorResponse,
    RelevantFile,
    CodeSection,
    QueryType,
    CodebaseChunk,
    CodebaseIndex,
    CacheKey,
)
from app.services.codebase_navigator.exceptions import (
    IndexingError,
    NavigatorNotConfiguredError,
    InvalidResponseError,
)
from app.services.codebase_navigator_service import CodebaseNavigatorService
from app.services.codebase_navigator_tools import (
    register_codebase_navigator_tools,
    navigate_codebase,
)
from app.services.tool_service import ToolService, ToolCategory


class TestCodebaseIndexer:
    """Tests for CodebaseIndexer class."""

    @pytest.fixture
    def temp_codebase(self):
        """Create a temporary codebase for testing."""
        temp_dir = tempfile.mkdtemp()

        # Create some test files
        (Path(temp_dir) / "main.py").write_text("def main():\n    print('hello')\n")
        (Path(temp_dir) / "utils.py").write_text("def helper():\n    pass\n")
        (Path(temp_dir) / "README.md").write_text("# Test Project\n\nDescription.")

        # Create a subdirectory
        src_dir = Path(temp_dir) / "src"
        src_dir.mkdir()
        (src_dir / "module.py").write_text("class MyClass:\n    def method(self):\n        pass\n")

        # Create an ignored directory
        node_modules = Path(temp_dir) / "node_modules"
        node_modules.mkdir()
        (node_modules / "package.js").write_text("// should be ignored")

        yield temp_dir
        shutil.rmtree(temp_dir)

    def test_indexer_initialization(self, temp_codebase):
        """Test CodebaseIndexer initializes correctly."""
        indexer = CodebaseIndexer(Path(temp_codebase))

        assert indexer.root_path == Path(temp_codebase).resolve()
        assert indexer.max_tokens_per_chunk == 200_000
        assert len(indexer.include_patterns) > 0
        assert len(indexer.exclude_patterns) > 0

    def test_indexer_custom_patterns(self, temp_codebase):
        """Test CodebaseIndexer with custom include/exclude patterns."""
        indexer = CodebaseIndexer(
            Path(temp_codebase),
            include_patterns=["*.py"],
            exclude_patterns=["test_*.py"],
        )

        assert indexer.include_patterns == ["*.py"]
        assert indexer.exclude_patterns == ["test_*.py"]

    def test_indexer_scans_files(self, temp_codebase):
        """Test that indexer correctly scans files."""
        indexer = CodebaseIndexer(Path(temp_codebase))
        index = indexer.index()

        assert index.total_files > 0
        assert index.total_tokens > 0

        # Check that expected files are included
        file_paths = [f.relative_path for f in index.files]
        assert "main.py" in file_paths
        assert "utils.py" in file_paths
        assert "README.md" in file_paths
        assert "src/module.py" in file_paths or "src\\module.py" in file_paths

        # Check that node_modules is excluded
        for f in index.files:
            assert "node_modules" not in f.relative_path

    def test_indexer_creates_chunks(self, temp_codebase):
        """Test that indexer creates appropriate chunks."""
        indexer = CodebaseIndexer(Path(temp_codebase))
        index = indexer.index()

        # Small codebase should fit in one chunk
        assert index.total_chunks >= 1
        assert len(index.chunks) >= 1

    def test_indexer_get_chunk(self, temp_codebase):
        """Test getting a specific chunk."""
        indexer = CodebaseIndexer(Path(temp_codebase))
        indexer.index()

        chunk = indexer.get_chunk(0)

        assert isinstance(chunk, CodebaseChunk)
        assert chunk.chunk_id == 0
        assert len(chunk.files) > 0
        assert chunk.token_count > 0

    def test_indexer_raises_on_invalid_path(self):
        """Test that indexer raises error for invalid path."""
        indexer = CodebaseIndexer(Path("/nonexistent/path"))

        with pytest.raises(IndexingError):
            indexer.index()

    def test_indexer_format_chunk_for_query(self, temp_codebase):
        """Test formatting a chunk for a query."""
        indexer = CodebaseIndexer(Path(temp_codebase))
        indexer.index()

        chunk = indexer.get_chunk(0)
        formatted = indexer.format_chunk_for_query(chunk)

        assert "=== FILE:" in formatted
        assert "=== END FILE ===" in formatted
        assert "[line 1]" in formatted

    def test_indexer_codebase_hash(self, temp_codebase):
        """Test codebase hash generation."""
        indexer = CodebaseIndexer(Path(temp_codebase))
        indexer.index()

        hash1 = indexer.get_codebase_hash()
        assert len(hash1) == 16  # SHA256 truncated to 16 chars

        # Same codebase should produce same hash
        hash2 = indexer.get_codebase_hash()
        assert hash1 == hash2

    def test_indexer_respects_gitignore(self, temp_codebase):
        """Test that indexer respects .gitignore patterns."""
        # Create a .gitignore file
        gitignore_path = Path(temp_codebase) / ".gitignore"
        gitignore_path.write_text("ignored_file.py\n")

        # Create a file that should be ignored
        (Path(temp_codebase) / "ignored_file.py").write_text("# should be ignored")

        indexer = CodebaseIndexer(Path(temp_codebase))
        index = indexer.index()

        file_paths = [f.relative_path for f in index.files]
        assert "ignored_file.py" not in file_paths


class TestNavigatorCache:
    """Tests for NavigatorCache class."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create a temporary directory for cache testing."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def cache(self, temp_cache_dir):
        """Create a cache instance."""
        return NavigatorCache(
            cache_dir=temp_cache_dir,
            ttl_hours=24,
            enabled=True,
        )

    def test_cache_initialization(self, cache, temp_cache_dir):
        """Test cache initializes correctly."""
        assert cache.enabled
        assert cache.ttl_hours == 24
        assert (temp_cache_dir / "navigator_cache.db").exists()

    def test_cache_set_and_get(self, cache):
        """Test setting and getting cached values."""
        response = NavigatorResponse(
            relevant_files=[
                RelevantFile(path="test.py", relevance="high", reason="Test file")
            ],
            confidence=0.9,
            tokens_used=100,
            chunks_queried=1,
        )

        key = CacheKey(
            codebase_hash="abc123",
            task_hash="def456",
            query_type=QueryType.RELEVANCE,
        )

        cache.set(key, response)
        retrieved = cache.get(key)

        assert retrieved is not None
        assert retrieved.cached is True
        assert len(retrieved.relevant_files) == 1
        assert retrieved.relevant_files[0].path == "test.py"

    def test_cache_miss(self, cache):
        """Test cache miss returns None."""
        key = CacheKey(
            codebase_hash="nonexistent",
            task_hash="nonexistent",
            query_type=QueryType.RELEVANCE,
        )

        result = cache.get(key)
        assert result is None

    def test_cache_invalidation(self, cache):
        """Test cache invalidation by codebase hash."""
        response = NavigatorResponse(
            relevant_files=[],
            confidence=0.5,
            tokens_used=50,
            chunks_queried=1,
        )

        key = CacheKey(
            codebase_hash="to_invalidate",
            task_hash="task123",
            query_type=QueryType.RELEVANCE,
        )

        cache.set(key, response)
        assert cache.get(key) is not None

        count = cache.invalidate_codebase("to_invalidate")
        assert count == 1
        assert cache.get(key) is None

    def test_cache_stats(self, cache):
        """Test cache statistics."""
        stats = cache.get_stats()

        assert "enabled" in stats
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate" in stats

    def test_cache_disabled(self, temp_cache_dir):
        """Test cache when disabled."""
        cache = NavigatorCache(
            cache_dir=temp_cache_dir,
            enabled=False,
        )

        response = NavigatorResponse(
            relevant_files=[],
            confidence=0.5,
            tokens_used=50,
            chunks_queried=1,
        )

        key = CacheKey(
            codebase_hash="test",
            task_hash="test",
            query_type=QueryType.RELEVANCE,
        )

        cache.set(key, response)
        result = cache.get(key)

        assert result is None  # Disabled cache doesn't store


class TestNavigatorClient:
    """Tests for NavigatorClient class."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create a temporary directory for cache testing."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    def test_client_initialization(self, temp_cache_dir):
        """Test client initializes correctly."""
        client = NavigatorClient(
            api_key="test_key",
            model="devstral-small-latest",
            cache_dir=temp_cache_dir,
        )

        assert client._api_key == "test_key"
        assert client._model == "devstral-small-latest"
        assert client._cache is not None

    def test_client_requires_api_key(self):
        """Test client raises error without API key."""
        client = NavigatorClient(api_key=None)

        with patch("app.services.codebase_navigator.client.settings") as mock_settings:
            mock_settings.mistral_api_key = ""

            with pytest.raises(NavigatorNotConfiguredError):
                _ = client.api_key

    def test_parse_response_valid_json(self):
        """Test parsing valid JSON response."""
        client = NavigatorClient(api_key="test")

        response_text = json.dumps({
            "relevant_files": [
                {"path": "test.py", "relevance": "high", "reason": "Test"}
            ],
            "confidence": 0.9,
        })

        result = client._parse_response(response_text)

        assert "relevant_files" in result
        assert result["confidence"] == 0.9

    def test_parse_response_json_in_markdown(self):
        """Test parsing JSON embedded in markdown code block."""
        client = NavigatorClient(api_key="test")

        response_text = """Here's my analysis:

```json
{
    "relevant_files": [],
    "confidence": 0.8
}
```
"""

        result = client._parse_response(response_text)
        assert result["confidence"] == 0.8

    def test_parse_response_invalid_raises(self):
        """Test that invalid response raises error."""
        client = NavigatorClient(api_key="test")

        with pytest.raises(InvalidResponseError):
            client._parse_response("This is not valid JSON at all")

    @pytest.mark.asyncio
    async def test_client_query_calls_api(self, temp_cache_dir):
        """Test that query method calls the API."""
        client = NavigatorClient(
            api_key="test_key",
            cache_dir=temp_cache_dir,
            cache_enabled=False,
        )

        # Create a mock chunk and indexer
        mock_chunk = CodebaseChunk(
            chunk_id=0,
            total_chunks=1,
            files=[],
            manifest="",
            token_count=100,
        )

        mock_indexer = MagicMock()
        mock_indexer.format_chunk_for_query.return_value = "=== FILE: test.py ===\ncode\n=== END FILE ==="

        # Mock the API call
        with patch.object(client, "_call_api") as mock_call:
            mock_call.return_value = (json.dumps({
                "relevant_files": [],
                "confidence": 0.5,
            }), 100)

            response = await client.query(
                task="Test task",
                codebase=mock_chunk,
                indexer=mock_indexer,
            )

            assert mock_call.called
            assert response.confidence == 0.5


class TestNavigatorModels:
    """Tests for Navigator data models."""

    def test_relevant_file_from_dict(self):
        """Test creating RelevantFile from dict."""
        data = {
            "path": "src/main.py",
            "relevance": "high",
            "reason": "Main entry point",
            "specific_sections": [
                {"start_line": 10, "end_line": 20, "name": "main", "description": "Entry function"}
            ],
        }

        file = RelevantFile.from_dict(data)

        assert file.path == "src/main.py"
        assert file.relevance == "high"
        assert len(file.specific_sections) == 1
        assert file.specific_sections[0].name == "main"

    def test_navigator_response_to_dict(self):
        """Test NavigatorResponse serialization."""
        response = NavigatorResponse(
            relevant_files=[
                RelevantFile(path="test.py", relevance="medium", reason="Test")
            ],
            architecture_notes="Simple architecture",
            confidence=0.85,
            tokens_used=500,
            chunks_queried=1,
        )

        data = response.to_dict()

        assert "relevant_files" in data
        assert data["confidence"] == 0.85
        assert data["architecture_notes"] == "Simple architecture"

    def test_navigator_response_format_for_tool(self):
        """Test formatting response for tool output."""
        response = NavigatorResponse(
            relevant_files=[
                RelevantFile(path="high.py", relevance="high", reason="Important"),
                RelevantFile(path="low.py", relevance="low", reason="Minor"),
            ],
            architecture_notes="Test architecture",
            suggested_approach="Start with high.py",
            confidence=0.9,
            tokens_used=1000,
            chunks_queried=2,
        )

        formatted = response.format_for_tool()

        assert "=== Codebase Navigator Results ===" in formatted
        assert "high.py" in formatted
        assert "low.py" in formatted
        assert "HIGH Relevance" in formatted
        assert "Architecture Overview" in formatted
        assert "Suggested Approach" in formatted

    def test_query_type_enum(self):
        """Test QueryType enum values."""
        assert QueryType.RELEVANCE.value == "relevance"
        assert QueryType.STRUCTURE.value == "structure"
        assert QueryType.DEPENDENCIES.value == "dependencies"
        assert QueryType.ENTRY_POINTS.value == "entry_points"
        assert QueryType.IMPACT.value == "impact"


class TestCodebaseNavigatorService:
    """Tests for CodebaseNavigatorService."""

    def test_service_initialization(self):
        """Test service initializes correctly."""
        service = CodebaseNavigatorService()

        assert service._client is None
        assert service._indexers == {}

    def test_service_is_configured(self):
        """Test is_configured check."""
        service = CodebaseNavigatorService()

        with patch("app.services.codebase_navigator_service.settings") as mock_settings:
            mock_settings.codebase_navigator_enabled = True
            mock_settings.mistral_api_key = "test_key"

            assert service.is_configured()

            mock_settings.codebase_navigator_enabled = False
            assert not service.is_configured()

            mock_settings.codebase_navigator_enabled = True
            mock_settings.mistral_api_key = ""
            assert not service.is_configured()

    @pytest.fixture
    def temp_codebase(self):
        """Create a temporary codebase for testing."""
        temp_dir = tempfile.mkdtemp()
        (Path(temp_dir) / "test.py").write_text("print('test')")
        yield temp_dir
        shutil.rmtree(temp_dir)

    def test_service_get_indexer(self, temp_codebase):
        """Test getting an indexer from the service."""
        service = CodebaseNavigatorService()

        with patch("app.services.codebase_navigator_service.settings") as mock_settings:
            mock_settings.codebase_navigator_max_tokens_per_chunk = 200000
            mock_settings.get_navigator_include_patterns.return_value = ["*.py"]
            mock_settings.get_navigator_exclude_patterns.return_value = []

            indexer = service.get_indexer(temp_codebase)

            assert indexer is not None
            assert temp_codebase in str(indexer.root_path)

            # Second call should return cached indexer
            indexer2 = service.get_indexer(temp_codebase)
            assert indexer is indexer2

    def test_service_stats(self):
        """Test getting service statistics."""
        service = CodebaseNavigatorService()

        with patch("app.services.codebase_navigator_service.settings") as mock_settings:
            mock_settings.codebase_navigator_enabled = False

            stats = service.get_stats()

            assert "configured" in stats
            assert "indexed_repos" in stats


class TestNavigatorTools:
    """Tests for Navigator tool registration."""

    def test_register_tools_when_enabled(self):
        """Test tools are registered when navigator is enabled."""
        tool_service = ToolService()

        with patch("app.services.codebase_navigator_tools.settings") as mock_settings:
            mock_settings.codebase_navigator_enabled = True
            register_codebase_navigator_tools(tool_service)

        assert tool_service.get_tool("navigate_codebase") is not None
        assert tool_service.get_tool("navigate_codebase_structure") is not None
        assert tool_service.get_tool("navigate_find_entry_points") is not None
        assert tool_service.get_tool("navigate_assess_impact") is not None
        assert tool_service.get_tool("navigate_trace_dependencies") is not None

    def test_register_tools_when_disabled(self):
        """Test tools are not registered when navigator is disabled."""
        tool_service = ToolService()

        with patch("app.services.codebase_navigator_tools.settings") as mock_settings:
            mock_settings.codebase_navigator_enabled = False
            register_codebase_navigator_tools(tool_service)

        assert tool_service.get_tool("navigate_codebase") is None

    def test_tools_have_correct_category(self):
        """Test tools have UTILITY category."""
        tool_service = ToolService()

        with patch("app.services.codebase_navigator_tools.settings") as mock_settings:
            mock_settings.codebase_navigator_enabled = True
            register_codebase_navigator_tools(tool_service)

        tool = tool_service.get_tool("navigate_codebase")
        assert tool.category == ToolCategory.UTILITY

    @pytest.mark.asyncio
    async def test_navigate_codebase_not_configured(self):
        """Test navigate_codebase returns error when not configured."""
        with patch("app.services.codebase_navigator_tools.codebase_navigator_service") as mock_service:
            mock_service.is_configured.return_value = False

            result = await navigate_codebase("Test task")

            assert "Error" in result
            assert "not configured" in result


class TestCacheKey:
    """Tests for CacheKey class."""

    def test_cache_key_to_string(self):
        """Test cache key string conversion."""
        key = CacheKey(
            codebase_hash="abc123",
            task_hash="def456",
            query_type=QueryType.RELEVANCE,
        )

        key_str = key.to_string()

        assert "abc123" in key_str
        assert "def456" in key_str
        assert "relevance" in key_str

    def test_cache_key_from_task(self):
        """Test creating cache key from task."""
        key = CacheKey.from_task(
            codebase_hash="abc123",
            task="Find all database models",
            query_type=QueryType.RELEVANCE,
        )

        assert key.codebase_hash == "abc123"
        assert len(key.task_hash) == 16  # SHA256 truncated
        assert key.query_type == QueryType.RELEVANCE

    def test_cache_key_different_tasks(self):
        """Test that different tasks produce different keys."""
        key1 = CacheKey.from_task("hash", "Task 1", QueryType.RELEVANCE)
        key2 = CacheKey.from_task("hash", "Task 2", QueryType.RELEVANCE)

        assert key1.task_hash != key2.task_hash
