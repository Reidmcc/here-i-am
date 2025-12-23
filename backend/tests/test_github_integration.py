"""
Unit tests for GitHub integration functionality.

Tests cover:
- GitHubRepoConfig class
- GitHubService class
- GitHub tools
- GitHub routes
- Settings GitHub methods
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json
import base64
import time
import sys

# Mock heavy modules before they're imported
sys.modules['app.services.google_service'] = MagicMock()
sys.modules['app.services.anthropic_service'] = MagicMock()
sys.modules['app.services.openai_service'] = MagicMock()
sys.modules['app.services.llm_service'] = MagicMock()
sys.modules['app.services.memory_service'] = MagicMock()
sys.modules['app.services.session_manager'] = MagicMock()
sys.modules['app.services.cache_service'] = MagicMock()
sys.modules['app.services.tts_service'] = MagicMock()
sys.modules['app.services.xtts_service'] = MagicMock()
sys.modules['app.services.styletts2_service'] = MagicMock()
sys.modules['app.services.web_tools'] = MagicMock()

from app.config import Settings, GitHubRepoConfig
from app.services.github_service import GitHubService, RateLimitInfo, BINARY_EXTENSIONS


# =============================================================================
# GitHubRepoConfig Tests
# =============================================================================

class TestGitHubRepoConfig:
    """Tests for GitHubRepoConfig class."""

    def test_github_repo_config_creation(self):
        """Test basic GitHubRepoConfig creation."""
        config = GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test Project",
            token="ghp_test123",
        )

        assert config.owner == "test-owner"
        assert config.repo == "test-repo"
        assert config.label == "Test Project"
        assert config.token == "ghp_test123"
        assert config.protected_branches == ["main", "master"]  # default
        assert config.capabilities == ["read", "branch", "commit", "pr", "issue"]  # default
        assert config.local_clone_path is None

    def test_github_repo_config_custom_values(self):
        """Test GitHubRepoConfig with custom values."""
        config = GitHubRepoConfig(
            owner="my-org",
            repo="my-repo",
            label="Custom Project",
            token="ghp_custom",
            protected_branches=["main", "production"],
            capabilities=["read", "branch"],
            local_clone_path="/path/to/clone",
        )

        assert config.protected_branches == ["main", "production"]
        assert config.capabilities == ["read", "branch"]
        assert config.local_clone_path == "/path/to/clone"

    def test_github_repo_config_windows_path_backslashes(self):
        """Test GitHubRepoConfig normalizes Windows paths with backslashes."""
        config = GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test Project",
            token="ghp_test",
            local_clone_path="C:\\Users\\developer\\repos\\my-project",
        )

        # Backslashes should be converted to forward slashes
        assert "\\" not in config.local_clone_path
        assert "C:/Users/developer/repos/my-project" in config.local_clone_path or \
               config.local_clone_path.endswith("Users/developer/repos/my-project")

    def test_github_repo_config_windows_path_forward_slashes(self):
        """Test GitHubRepoConfig handles Windows paths with forward slashes."""
        config = GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test Project",
            token="ghp_test",
            local_clone_path="C:/Users/developer/repos/my-project",
        )

        # Path should be preserved (forward slashes are valid on Windows too)
        assert "Users/developer/repos/my-project" in config.local_clone_path

    def test_github_repo_config_windows_unc_path(self):
        """Test GitHubRepoConfig handles Windows UNC paths."""
        config = GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test Project",
            token="ghp_test",
            local_clone_path="\\\\server\\share\\repos\\my-project",
        )

        # UNC paths should be normalized (backslashes converted)
        assert "\\" not in config.local_clone_path
        # The exact format depends on OS, but it should be valid
        assert "my-project" in config.local_clone_path

    def test_github_repo_config_to_dict_without_token(self):
        """Test to_dict excludes token by default."""
        config = GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test Project",
            token="ghp_secret",
        )

        result = config.to_dict(include_token=False)

        assert "token" not in result
        assert result["owner"] == "test-owner"
        assert result["repo"] == "test-repo"
        assert result["label"] == "Test Project"

    def test_github_repo_config_to_dict_with_token(self):
        """Test to_dict includes token when requested."""
        config = GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test Project",
            token="ghp_secret",
        )

        result = config.to_dict(include_token=True)

        assert result["token"] == "ghp_secret"

    def test_github_repo_config_has_capability(self):
        """Test has_capability method."""
        config = GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test Project",
            token="ghp_test",
            capabilities=["read", "branch"],
        )

        assert config.has_capability("read") is True
        assert config.has_capability("branch") is True
        assert config.has_capability("commit") is False
        assert config.has_capability("pr") is False
        assert config.has_capability("issue") is False


# =============================================================================
# Settings GitHub Methods Tests
# =============================================================================

class TestSettingsGitHubMethods:
    """Tests for Settings class GitHub-related methods."""

    def test_get_github_repos_empty(self):
        """Test get_github_repos returns empty list when not configured."""
        settings = Settings(
            anthropic_api_key="test-key",
            github_repos="",
            _env_file=None,
        )

        repos = settings.get_github_repos()

        assert repos == []

    def test_get_github_repos_single(self):
        """Test get_github_repos with single repo."""
        settings = Settings(
            anthropic_api_key="test-key",
            github_repos='[{"owner": "test-owner", "repo": "test-repo", "label": "Test", "token": "ghp_test"}]',
            _env_file=None,
        )

        repos = settings.get_github_repos()

        assert len(repos) == 1
        assert repos[0].owner == "test-owner"
        assert repos[0].repo == "test-repo"
        assert repos[0].label == "Test"
        assert repos[0].token == "ghp_test"

    def test_get_github_repos_multiple(self):
        """Test get_github_repos with multiple repos."""
        repos_json = json.dumps([
            {"owner": "owner1", "repo": "repo1", "label": "Project 1", "token": "ghp_1"},
            {"owner": "owner2", "repo": "repo2", "label": "Project 2", "token": "ghp_2",
             "capabilities": ["read"], "protected_branches": ["main"]},
        ])
        settings = Settings(
            anthropic_api_key="test-key",
            github_repos=repos_json,
            _env_file=None,
        )

        repos = settings.get_github_repos()

        assert len(repos) == 2
        assert repos[0].label == "Project 1"
        assert repos[1].label == "Project 2"
        assert repos[1].capabilities == ["read"]
        assert repos[1].protected_branches == ["main"]

    def test_get_github_repos_invalid_json(self):
        """Test get_github_repos raises ValueError on invalid JSON."""
        settings = Settings(
            anthropic_api_key="test-key",
            github_repos="invalid json [[[",
            _env_file=None,
        )

        with pytest.raises(ValueError) as exc_info:
            settings.get_github_repos()

        assert "Invalid JSON in GITHUB_REPOS" in str(exc_info.value)

    def test_get_github_repos_skips_incomplete(self):
        """Test get_github_repos skips repos missing required fields."""
        repos_json = json.dumps([
            {"owner": "owner1", "repo": "repo1", "label": "Good", "token": "ghp_1"},
            {"owner": "owner2", "repo": "repo2", "label": "No Token"},  # Missing token
            {"owner": "owner3", "label": "No Repo", "token": "ghp_3"},  # Missing repo
        ])
        settings = Settings(
            anthropic_api_key="test-key",
            github_repos=repos_json,
            _env_file=None,
        )

        repos = settings.get_github_repos()

        assert len(repos) == 1
        assert repos[0].label == "Good"

    def test_get_github_repo_by_label_found(self):
        """Test get_github_repo_by_label when repo exists."""
        settings = Settings(
            anthropic_api_key="test-key",
            github_repos='[{"owner": "test", "repo": "test", "label": "My Project", "token": "ghp_test"}]',
            _env_file=None,
        )

        repo = settings.get_github_repo_by_label("My Project")

        assert repo is not None
        assert repo.label == "My Project"

    def test_get_github_repo_by_label_case_insensitive(self):
        """Test get_github_repo_by_label is case-insensitive."""
        settings = Settings(
            anthropic_api_key="test-key",
            github_repos='[{"owner": "test", "repo": "test", "label": "My Project", "token": "ghp_test"}]',
            _env_file=None,
        )

        repo = settings.get_github_repo_by_label("MY PROJECT")

        assert repo is not None
        assert repo.label == "My Project"

    def test_get_github_repo_by_label_not_found(self):
        """Test get_github_repo_by_label returns None when not found."""
        settings = Settings(
            anthropic_api_key="test-key",
            github_repos='[{"owner": "test", "repo": "test", "label": "My Project", "token": "ghp_test"}]',
            _env_file=None,
        )

        repo = settings.get_github_repo_by_label("Nonexistent")

        assert repo is None


# =============================================================================
# RateLimitInfo Tests
# =============================================================================

class TestRateLimitInfo:
    """Tests for RateLimitInfo dataclass."""

    def test_rate_limit_info_creation(self):
        """Test RateLimitInfo creation."""
        info = RateLimitInfo(
            remaining=4500,
            limit=5000,
            reset_timestamp=int(time.time()) + 3600,
        )

        assert info.remaining == 4500
        assert info.limit == 5000

    def test_rate_limit_reset_time_formatted(self):
        """Test reset_time_formatted property."""
        future_time = int(time.time()) + 120  # 2 minutes from now
        info = RateLimitInfo(
            remaining=100,
            limit=5000,
            reset_timestamp=future_time,
        )

        formatted = info.reset_time_formatted
        assert "remaining" in formatted.lower()

    def test_rate_limit_reset_time_formatted_past(self):
        """Test reset_time_formatted for past reset time."""
        past_time = int(time.time()) - 60  # 1 minute ago
        info = RateLimitInfo(
            remaining=100,
            limit=5000,
            reset_timestamp=past_time,
        )

        formatted = info.reset_time_formatted
        # Should not include "remaining" since time has passed
        assert "remaining" not in formatted.lower() or "0m 0s" in formatted


# =============================================================================
# GitHubService Tests
# =============================================================================

class TestGitHubService:
    """Tests for GitHubService class."""

    def test_github_service_initialization(self):
        """Test GitHubService initializes correctly."""
        service = GitHubService()
        assert service._rate_limits == {}

    def test_hash_token(self):
        """Test _hash_token produces consistent hashes."""
        service = GitHubService()

        hash1 = service._hash_token("ghp_test123")
        hash2 = service._hash_token("ghp_test123")
        hash3 = service._hash_token("ghp_different")

        assert hash1 == hash2
        assert hash1 != hash3
        assert len(hash1) == 16

    def test_check_rate_limit_no_info(self):
        """Test check_rate_limit returns None when no info."""
        service = GitHubService()

        result = service.check_rate_limit("ghp_test")

        assert result is None

    def test_is_binary_file_by_extension(self):
        """Test is_binary_file detects binary by extension."""
        service = GitHubService()

        assert service.is_binary_file("image.png") is True
        assert service.is_binary_file("data.pdf") is True
        assert service.is_binary_file("archive.zip") is True
        assert service.is_binary_file("code.py") is False
        assert service.is_binary_file("README.md") is False

    def test_is_binary_file_by_content(self):
        """Test is_binary_file detects binary by null bytes."""
        service = GitHubService()

        binary_content = b"Hello\x00World"
        text_content = b"Hello World"

        assert service.is_binary_file("file.dat", binary_content) is True
        assert service.is_binary_file("file.dat", text_content) is False

    @pytest.mark.asyncio
    async def test_request_rate_limit_exceeded(self):
        """Test _request returns error when rate limited."""
        service = GitHubService()

        # Set up rate limit as exhausted
        token = "ghp_test"
        token_hash = service._hash_token(token)
        service._rate_limits[token_hash] = RateLimitInfo(
            remaining=0,
            limit=5000,
            reset_timestamp=int(time.time()) + 3600,
        )

        status, data = await service._request("GET", "/test", token)

        assert status == 429
        assert "rate limit" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_request_updates_rate_limit(self):
        """Test _request updates rate limit from response headers."""
        service = GitHubService()
        token = "ghp_test"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "4999",
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Reset": str(int(time.time()) + 3600),
        }
        mock_response.json.return_value = {"test": "data"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            status, data = await service._request("GET", "/test", token)

        assert status == 200

        # Check rate limit was updated
        rate_info = service.check_rate_limit(token)
        assert rate_info is not None
        assert rate_info.remaining == 4999
        assert rate_info.limit == 5000


# =============================================================================
# GitHubService File Operations Tests
# =============================================================================

class TestGitHubServiceFileOperations:
    """Tests for GitHubService file operations."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock repo config."""
        return GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test",
            token="ghp_test",
            capabilities=["read", "branch", "commit", "pr", "issue"],
        )

    @pytest.mark.asyncio
    async def test_get_file_contents_text_file(self, mock_repo):
        """Test get_file_contents for text files."""
        service = GitHubService()

        content_text = "Hello, World!"
        content_b64 = base64.b64encode(content_text.encode()).decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {
            "type": "file",
            "content": content_b64,
            "size": len(content_text),
            "sha": "abc123",
            "name": "test.txt",
            "path": "test.txt",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            success, data = await service.get_file_contents(mock_repo, "test.txt")

        assert success is True
        assert data["type"] == "text"
        assert data["content"] == content_text

    @pytest.mark.asyncio
    async def test_get_file_contents_binary_file(self, mock_repo):
        """Test get_file_contents detects binary files."""
        service = GitHubService()

        binary_content = b"\x89PNG\r\n\x1a\n\x00\x00"
        content_b64 = base64.b64encode(binary_content).decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {
            "type": "file",
            "content": content_b64,
            "size": len(binary_content),
            "sha": "abc123",
            "name": "image.png",
            "path": "image.png",
            "download_url": "https://example.com/image.png",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            success, data = await service.get_file_contents(mock_repo, "image.png")

        assert success is True
        assert data["type"] == "binary"
        assert "download_url" in data

    @pytest.mark.asyncio
    async def test_get_file_contents_not_found(self, mock_repo):
        """Test get_file_contents handles 404."""
        service = GitHubService()

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.headers = {}
        mock_response.json.return_value = {"message": "Not Found"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            success, data = await service.get_file_contents(mock_repo, "nonexistent.txt")

        assert success is False
        assert data["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_list_contents(self, mock_repo):
        """Test list_contents returns sorted directory listing."""
        service = GitHubService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = [
            {"type": "file", "name": "README.md", "path": "README.md", "size": 100, "sha": "a"},
            {"type": "dir", "name": "src", "path": "src", "sha": "b"},
            {"type": "file", "name": "package.json", "path": "package.json", "size": 200, "sha": "c"},
        ]

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            success, items = await service.list_contents(mock_repo, "")

        assert success is True
        assert len(items) == 3
        # Directories should come first
        assert items[0]["type"] == "dir"
        assert items[0]["name"] == "src"

    @pytest.mark.asyncio
    async def test_commit_file_protected_branch(self, mock_repo):
        """Test commit_file rejects protected branches."""
        service = GitHubService()

        success, data = await service.commit_file(
            mock_repo,
            path="test.txt",
            content="Hello",
            message="Test commit",
            branch="main",  # Protected by default
        )

        assert success is False
        assert data["error"] == "protected_branch"
        assert "protected branch" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_file_protected_branch(self, mock_repo):
        """Test delete_file rejects protected branches."""
        service = GitHubService()

        success, data = await service.delete_file(
            mock_repo,
            path="test.txt",
            message="Delete test",
            branch="master",  # Protected by default
        )

        assert success is False
        assert data["error"] == "protected_branch"


# =============================================================================
# GitHubService Local Clone Tests
# =============================================================================

class TestGitHubServiceLocalClone:
    """Tests for GitHubService local clone operations."""

    @pytest.fixture
    def temp_repo_dir(self, tmp_path):
        """Create a temporary directory with .git folder to simulate a repo."""
        repo_dir = tmp_path / "test-repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        return repo_dir

    @pytest.fixture
    def mock_repo_with_local(self, temp_repo_dir):
        """Create a mock repo config with local_clone_path."""
        return GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test",
            token="ghp_test",
            local_clone_path=str(temp_repo_dir),
        )

    @pytest.fixture
    def mock_repo_no_local(self):
        """Create a mock repo config without local_clone_path."""
        return GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test",
            token="ghp_test",
        )

    def test_has_local_clone_true(self, mock_repo_with_local):
        """Test has_local_clone returns True for valid local clone."""
        service = GitHubService()
        assert service.has_local_clone(mock_repo_with_local) is True

    def test_has_local_clone_false_no_path(self, mock_repo_no_local):
        """Test has_local_clone returns False when no path configured."""
        service = GitHubService()
        assert service.has_local_clone(mock_repo_no_local) is False

    def test_has_local_clone_false_invalid_path(self):
        """Test has_local_clone returns False for invalid path."""
        service = GitHubService()
        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="ghp_test",
            local_clone_path="/nonexistent/path/to/repo",
        )
        assert service.has_local_clone(repo) is False

    def test_get_file_contents_local_text_file(self, mock_repo_with_local, temp_repo_dir):
        """Test reading a text file from local clone."""
        service = GitHubService()

        # Create a test file
        test_file = temp_repo_dir / "test.txt"
        test_file.write_text("Hello, World!")

        success, data = service.get_file_contents_local(mock_repo_with_local, "test.txt")

        assert success is True
        assert data["type"] == "text"
        assert data["content"] == "Hello, World!"
        assert data["source"] == "local"
        assert data["name"] == "test.txt"

    def test_get_file_contents_local_binary_file(self, mock_repo_with_local, temp_repo_dir):
        """Test reading a binary file from local clone."""
        service = GitHubService()

        # Create a binary file (PNG signature)
        binary_file = temp_repo_dir / "image.png"
        binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")

        success, data = service.get_file_contents_local(mock_repo_with_local, "image.png")

        assert success is True
        assert data["type"] == "binary"
        assert data["source"] == "local"

    def test_get_file_contents_local_not_found(self, mock_repo_with_local):
        """Test reading nonexistent file from local clone."""
        service = GitHubService()

        success, data = service.get_file_contents_local(mock_repo_with_local, "nonexistent.txt")

        assert success is False
        assert data["error"] == "not_found"

    def test_get_file_contents_local_path_escape(self, mock_repo_with_local):
        """Test that path traversal attacks are blocked."""
        service = GitHubService()

        success, data = service.get_file_contents_local(mock_repo_with_local, "../../../etc/passwd")

        assert success is False
        assert data["error"] == "invalid_path"
        assert "escapes" in data["message"].lower()

    def test_list_contents_local_directory(self, mock_repo_with_local, temp_repo_dir):
        """Test listing directory contents from local clone."""
        service = GitHubService()

        # Create some files and directories
        (temp_repo_dir / "src").mkdir()
        (temp_repo_dir / "README.md").write_text("# Test")
        (temp_repo_dir / "main.py").write_text("print('hello')")
        (temp_repo_dir / "src" / "app.py").write_text("# app")

        success, items = service.list_contents_local(mock_repo_with_local, "")

        assert success is True
        assert len(items) == 3  # src/, README.md, main.py
        # Directories come first
        assert items[0]["type"] == "dir"
        assert items[0]["name"] == "src"
        # Files after directories
        assert items[1]["type"] == "file"
        assert items[2]["type"] == "file"

    def test_list_contents_local_subdirectory(self, mock_repo_with_local, temp_repo_dir):
        """Test listing subdirectory contents from local clone."""
        service = GitHubService()

        # Create a subdirectory with files
        src_dir = temp_repo_dir / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("# app")
        (src_dir / "utils.py").write_text("# utils")

        success, items = service.list_contents_local(mock_repo_with_local, "src")

        assert success is True
        assert len(items) == 2
        assert all(item["type"] == "file" for item in items)

    def test_list_contents_local_not_found(self, mock_repo_with_local):
        """Test listing nonexistent directory from local clone."""
        service = GitHubService()

        success, items = service.list_contents_local(mock_repo_with_local, "nonexistent")

        assert success is False
        assert items[0]["error"] == "not_found"

    def test_list_contents_local_skips_hidden_files(self, mock_repo_with_local, temp_repo_dir):
        """Test that hidden files are skipped in directory listing."""
        service = GitHubService()

        # Create visible and hidden files
        (temp_repo_dir / "visible.txt").write_text("visible")
        (temp_repo_dir / ".hidden").write_text("hidden")
        (temp_repo_dir / ".gitignore").write_text("*.pyc")

        success, items = service.list_contents_local(mock_repo_with_local, "")

        assert success is True
        assert len(items) == 1  # Only visible.txt
        assert items[0]["name"] == "visible.txt"


# =============================================================================
# GitHub Tools Tests
# =============================================================================

class TestGitHubTools:
    """Tests for GitHub tool functions."""

    @pytest.fixture
    def mock_settings_with_github(self):
        """Mock settings with GitHub enabled."""
        with patch('app.services.github_tools.settings') as mock_settings:
            mock_settings.github_tools_enabled = True
            yield mock_settings

    @pytest.fixture
    def mock_github_service(self):
        """Mock GitHub service."""
        with patch('app.services.github_tools.github_service') as mock_service:
            # Default to no local clone to avoid local file reading in tests
            mock_service.has_local_clone.return_value = False
            yield mock_service

    @pytest.mark.asyncio
    async def test_github_repo_info_not_found(self, mock_github_service):
        """Test github_repo_info when repo not found."""
        from app.services.github_tools import github_repo_info

        mock_github_service.get_repo_by_label.return_value = None
        mock_github_service.get_repos.return_value = []

        result = await github_repo_info("Nonexistent")

        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_github_repo_info_no_capability(self, mock_github_service):
        """Test github_repo_info when read capability disabled."""
        from app.services.github_tools import github_repo_info

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = False
        mock_github_service.get_repo_by_label.return_value = mock_repo

        result = await github_repo_info("Test")

        assert "Error" in result
        assert "capability" in result.lower()

    @pytest.mark.asyncio
    async def test_github_repo_info_success(self, mock_github_service):
        """Test github_repo_info success."""
        from app.services.github_tools import github_repo_info

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_repo.owner = "test-owner"
        mock_repo.repo = "test-repo"
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_repo_info = AsyncMock(return_value=(True, {
            "full_name": "test-owner/test-repo",
            "description": "Test repo",
            "default_branch": "main",
            "visibility": "public",
            "language": "Python",
            "stars": 100,
            "open_issues": 5,
            "forks": 10,
            "created_at": "2024-01-01",
            "updated_at": "2024-12-01",
            "html_url": "https://github.com/test-owner/test-repo",
        }))

        result = await github_repo_info("Test")

        assert "test-owner/test-repo" in result
        assert "Python" in result
        assert "100" in result

    @pytest.mark.asyncio
    async def test_github_list_contents_empty(self, mock_github_service):
        """Test github_list_contents for empty directory."""
        from app.services.github_tools import github_list_contents

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.list_contents = AsyncMock(return_value=(True, []))

        result = await github_list_contents("Test", "")

        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_github_get_file_with_line_range(self, mock_github_service):
        """Test github_get_file with line range."""
        from app.services.github_tools import github_get_file

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_file_contents = AsyncMock(return_value=(True, {
            "type": "text",
            "content": "line1\nline2\nline3\nline4\nline5",
            "size": 30,
            "sha": "abc123",
            "name": "test.txt",
            "path": "test.txt",
        }))

        result = await github_get_file("Test", "test.txt", start_line=2, end_line=4)

        assert "line2" in result
        assert "line3" in result
        assert "line4" in result
        assert "lines 2-4" in result

    @pytest.mark.asyncio
    async def test_github_create_branch_success(self, mock_github_service):
        """Test github_create_branch success."""
        from app.services.github_tools import github_create_branch

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.create_branch = AsyncMock(return_value=(True, {
            "name": "feature-branch",
            "sha": "abc123",
        }))

        result = await github_create_branch("Test", "feature-branch", "main")

        assert "Created branch" in result
        assert "feature-branch" in result

    @pytest.mark.asyncio
    async def test_github_commit_file_no_capability(self, mock_github_service):
        """Test github_commit_file when commit capability disabled."""
        from app.services.github_tools import github_commit_file

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = False
        mock_github_service.get_repo_by_label.return_value = mock_repo

        result = await github_commit_file("Test", "test.txt", "content", "message", "branch")

        assert "Error" in result
        assert "capability" in result.lower()

    @pytest.mark.asyncio
    async def test_github_list_pull_requests_success(self, mock_github_service):
        """Test github_list_pull_requests success."""
        from app.services.github_tools import github_list_pull_requests

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.list_pull_requests = AsyncMock(return_value=(True, [
            {
                "number": 1,
                "title": "Test PR",
                "draft": False,
                "author": "user",
                "head": "feature",
                "base": "main",
                "html_url": "https://github.com/test/test/pull/1",
            }
        ]))

        result = await github_list_pull_requests("Test", "open")

        assert "#1" in result
        assert "Test PR" in result

    @pytest.mark.asyncio
    async def test_github_create_issue_success(self, mock_github_service):
        """Test github_create_issue success."""
        from app.services.github_tools import github_create_issue

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.create_issue = AsyncMock(return_value=(True, {
            "number": 42,
            "title": "New Issue",
            "html_url": "https://github.com/test/test/issues/42",
        }))

        result = await github_create_issue("Test", "New Issue", "Issue body")

        assert "#42" in result
        assert "Created Issue" in result

    @pytest.mark.asyncio
    async def test_github_add_comment_requires_capability(self, mock_github_service):
        """Test github_add_comment requires issue or pr capability."""
        from app.services.github_tools import github_add_comment

        mock_repo = MagicMock()
        mock_repo.has_capability.side_effect = lambda c: False  # No capabilities
        mock_github_service.get_repo_by_label.return_value = mock_repo

        result = await github_add_comment("Test", 1, "Comment")

        assert "Error" in result
        assert "capability" in result.lower()


# =============================================================================
# GitHub Tool Registration Tests
# =============================================================================

class TestGitHubToolRegistration:
    """Tests for GitHub tool registration."""

    def test_register_github_tools_disabled(self):
        """Test tools not registered when GitHub disabled."""
        from app.services.github_tools import register_github_tools
        from app.services.tool_service import ToolService, ToolCategory

        tool_service = ToolService()

        with patch('app.services.github_tools.settings') as mock_settings:
            mock_settings.github_tools_enabled = False
            register_github_tools(tool_service)

        github_tools = [t for t in tool_service.list_tools() if t.category == ToolCategory.GITHUB]
        assert len(github_tools) == 0

    def test_register_github_tools_no_repos(self):
        """Test tools not registered when no repos configured."""
        from app.services.github_tools import register_github_tools
        from app.services.tool_service import ToolService, ToolCategory

        tool_service = ToolService()

        with patch('app.services.github_tools.settings') as mock_settings, \
             patch('app.services.github_tools.github_service') as mock_service:
            mock_settings.github_tools_enabled = True
            mock_service.get_repos.return_value = []
            register_github_tools(tool_service)

        github_tools = [t for t in tool_service.list_tools() if t.category == ToolCategory.GITHUB]
        assert len(github_tools) == 0

    def test_register_github_tools_read_only(self):
        """Test only read tools registered for read-only repos."""
        from app.services.github_tools import register_github_tools
        from app.services.tool_service import ToolService, ToolCategory

        tool_service = ToolService()

        read_only_repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="ghp_test",
            capabilities=["read"],
        )

        with patch('app.services.github_tools.settings') as mock_settings, \
             patch('app.services.github_tools.github_service') as mock_service:
            mock_settings.github_tools_enabled = True
            mock_service.get_repos.return_value = [read_only_repo]
            register_github_tools(tool_service)

        github_tools = tool_service.list_tools()
        github_tool_names = [t.name for t in github_tools if t.category == ToolCategory.GITHUB]

        # Should have read tools
        assert "github_repo_info" in github_tool_names
        assert "github_get_file" in github_tool_names

        # Should NOT have write tools
        assert "github_create_branch" not in github_tool_names
        assert "github_commit_file" not in github_tool_names

    def test_register_github_tools_full_capabilities(self):
        """Test all tools registered for full capability repos."""
        from app.services.github_tools import register_github_tools
        from app.services.tool_service import ToolService, ToolCategory

        tool_service = ToolService()

        full_repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="ghp_test",
            capabilities=["read", "branch", "commit", "pr", "issue"],
        )

        with patch('app.services.github_tools.settings') as mock_settings, \
             patch('app.services.github_tools.github_service') as mock_service:
            mock_settings.github_tools_enabled = True
            mock_service.get_repos.return_value = [full_repo]
            register_github_tools(tool_service)

        github_tools = tool_service.list_tools()
        github_tool_names = [t.name for t in github_tools if t.category == ToolCategory.GITHUB]

        # Should have all tool categories
        assert "github_repo_info" in github_tool_names  # read
        assert "github_create_branch" in github_tool_names  # branch
        assert "github_commit_file" in github_tool_names  # commit
        assert "github_create_pull_request" in github_tool_names  # pr
        assert "github_create_issue" in github_tool_names  # issue
        assert "github_add_comment" in github_tool_names  # issue/pr


# =============================================================================
# GitHub Routes Tests
# =============================================================================

class TestGitHubRoutes:
    """Tests for GitHub API routes."""

    @pytest.mark.asyncio
    async def test_list_repos_disabled(self):
        """Test list_repos returns empty when GitHub disabled."""
        from app.routes.github import list_repos

        with patch('app.routes.github.settings') as mock_settings:
            mock_settings.github_tools_enabled = False
            result = await list_repos()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_repos_returns_repos(self):
        """Test list_repos returns configured repos."""
        from app.routes.github import list_repos

        mock_repo = GitHubRepoConfig(
            owner="test-owner",
            repo="test-repo",
            label="Test Project",
            token="ghp_secret",
            capabilities=["read"],
        )

        with patch('app.routes.github.settings') as mock_settings, \
             patch('app.routes.github.github_service') as mock_service:
            mock_settings.github_tools_enabled = True
            mock_service.get_repos.return_value = [mock_repo]
            result = await list_repos()

        assert len(result) == 1
        assert result[0]["label"] == "Test Project"
        assert "token" not in result[0]  # Token should be excluded

    @pytest.mark.asyncio
    async def test_get_rate_limits_disabled(self):
        """Test get_rate_limits returns disabled status."""
        from app.routes.github import get_rate_limits

        with patch('app.routes.github.settings') as mock_settings:
            mock_settings.github_tools_enabled = False
            result = await get_rate_limits()

        assert result["enabled"] is False
        assert result["repos"] == {}

    @pytest.mark.asyncio
    async def test_get_rate_limits_with_info(self):
        """Test get_rate_limits returns rate limit info."""
        from app.routes.github import get_rate_limits

        mock_repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="ghp_test",
        )

        mock_rate_info = RateLimitInfo(
            remaining=4500,
            limit=5000,
            reset_timestamp=int(time.time()) + 3600,
        )

        with patch('app.routes.github.settings') as mock_settings, \
             patch('app.routes.github.github_service') as mock_service:
            mock_settings.github_tools_enabled = True
            mock_service.get_repos.return_value = [mock_repo]
            mock_service.check_rate_limit.return_value = mock_rate_info
            result = await get_rate_limits()

        assert result["enabled"] is True
        assert "Test" in result["repos"]
        assert result["repos"]["Test"]["remaining"] == 4500
        assert result["repos"]["Test"]["limit"] == 5000

    @pytest.mark.asyncio
    async def test_get_rate_limits_unknown_status(self):
        """Test get_rate_limits returns unknown when no rate info."""
        from app.routes.github import get_rate_limits

        mock_repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="ghp_test",
        )

        with patch('app.routes.github.settings') as mock_settings, \
             patch('app.routes.github.github_service') as mock_service:
            mock_settings.github_tools_enabled = True
            mock_service.get_repos.return_value = [mock_repo]
            mock_service.check_rate_limit.return_value = None  # No rate info yet
            result = await get_rate_limits()

        assert result["enabled"] is True
        assert result["repos"]["Test"]["remaining"] is None
        assert result["repos"]["Test"]["status"] == "unknown"
