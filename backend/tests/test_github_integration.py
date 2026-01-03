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

# Import configuration and GitHub-specific modules
# Note: We import these specific modules directly without mocking dependencies
# at module level to avoid polluting other test files during pytest collection.
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


# =============================================================================
# GitHub Tools Efficiency Helper Functions Tests
# =============================================================================

class TestGitHubToolsHelperFunctions:
    """Tests for GitHub tools helper functions."""

    def test_format_size_bytes(self):
        """Test _format_size for bytes."""
        from app.services.github_tools import _format_size

        assert _format_size(0) == "0B"
        assert _format_size(100) == "100B"
        assert _format_size(1023) == "1023B"

    def test_format_size_kilobytes(self):
        """Test _format_size for kilobytes."""
        from app.services.github_tools import _format_size

        assert _format_size(1024) == "1.0KB"
        assert _format_size(2048) == "2.0KB"
        assert _format_size(1536) == "1.5KB"
        assert _format_size(1024 * 1024 - 1) == "1024.0KB"

    def test_format_size_megabytes(self):
        """Test _format_size for megabytes."""
        from app.services.github_tools import _format_size

        assert _format_size(1024 * 1024) == "1.0MB"
        assert _format_size(5 * 1024 * 1024) == "5.0MB"
        assert _format_size(int(1.5 * 1024 * 1024)) == "1.5MB"

    def test_count_code_structures_python(self):
        """Test _count_code_structures for Python files."""
        from app.services.github_tools import _count_code_structures

        python_code = """
class MyClass:
    def __init__(self):
        pass

    def method(self):
        pass

def standalone_function():
    pass

class AnotherClass:
    pass
"""
        counts = _count_code_structures(python_code, "test.py")
        assert counts["functions"] == 3  # __init__, method, standalone_function
        assert counts["classes"] == 2  # MyClass, AnotherClass

    def test_count_code_structures_javascript(self):
        """Test _count_code_structures for JavaScript files."""
        from app.services.github_tools import _count_code_structures

        js_code = """
function namedFunction() {}

const arrowFunc = () => {};

class MyClass {
    method() {}
}

let anotherArrow = async () => {};
"""
        counts = _count_code_structures(js_code, "test.js")
        assert counts["functions"] >= 2  # namedFunction plus arrow functions
        assert counts["classes"] == 1  # MyClass

    def test_count_code_structures_unsupported_extension(self):
        """Test _count_code_structures for unsupported file types."""
        from app.services.github_tools import _count_code_structures

        content = "some random text content"
        counts = _count_code_structures(content, "test.txt")
        assert counts["functions"] == 0
        assert counts["classes"] == 0

    def test_truncate_file_content_no_truncation(self):
        """Test _truncate_file_content when file fits within limit."""
        from app.services.github_tools import _truncate_file_content

        content = "line1\nline2\nline3"
        result, was_truncated, summary = _truncate_file_content(content, 10, "test.txt")

        assert result == content
        assert was_truncated is False
        assert summary == ""

    def test_truncate_file_content_with_truncation(self):
        """Test _truncate_file_content when file exceeds limit."""
        from app.services.github_tools import _truncate_file_content

        content = "\n".join([f"line{i}" for i in range(100)])
        result, was_truncated, summary = _truncate_file_content(content, 10, "test.txt")

        assert was_truncated is True
        assert len(result.split("\n")) == 10
        assert "truncated" in summary.lower()
        assert "100" in summary  # total lines

    def test_truncate_file_content_with_code_structure_summary(self):
        """Test _truncate_file_content includes structure summary for code files."""
        from app.services.github_tools import _truncate_file_content

        python_content = "\n".join([
            "def func1(): pass",
            "def func2(): pass",
            "class MyClass: pass",
        ] + [f"# comment line {i}" for i in range(100)])

        result, was_truncated, summary = _truncate_file_content(python_content, 10, "test.py")

        assert was_truncated is True
        assert "functions" in summary.lower()
        assert "classes" in summary.lower()

    def test_build_tree_view_basic(self):
        """Test _build_tree_view with basic structure."""
        from app.services.github_tools import _build_tree_view

        tree_items = [
            {"path": "README.md", "type": "blob", "size": 1000},
            {"path": "src", "type": "tree"},
            {"path": "src/main.py", "type": "blob", "size": 500},
        ]

        tree_view, file_count, dir_count = _build_tree_view(tree_items, max_depth=3)

        assert "README.md" in tree_view
        assert "src/" in tree_view
        assert "main.py" in tree_view
        assert file_count == 2  # README.md, main.py
        assert dir_count == 1  # src

    def test_build_tree_view_respects_max_depth(self):
        """Test _build_tree_view respects max_depth limit."""
        from app.services.github_tools import _build_tree_view

        tree_items = [
            {"path": "level1", "type": "tree"},
            {"path": "level1/level2", "type": "tree"},
            {"path": "level1/level2/level3", "type": "tree"},
            {"path": "level1/level2/level3/deep.txt", "type": "blob", "size": 100},
        ]

        tree_view, file_count, dir_count = _build_tree_view(tree_items, max_depth=2)

        assert "level1/" in tree_view
        assert "level2/" in tree_view
        # level3 should be excluded (depth 3)
        assert "level3" not in tree_view
        assert "deep.txt" not in tree_view

    def test_build_tree_view_with_sizes(self):
        """Test _build_tree_view includes file sizes when enabled."""
        from app.services.github_tools import _build_tree_view

        tree_items = [
            {"path": "small.txt", "type": "blob", "size": 100},
            {"path": "large.bin", "type": "blob", "size": 1024 * 1024 * 2},
        ]

        tree_view, _, _ = _build_tree_view(tree_items, include_sizes=True)

        assert "100B" in tree_view
        assert "2.0MB" in tree_view

    def test_build_tree_view_without_sizes(self):
        """Test _build_tree_view excludes file sizes when disabled."""
        from app.services.github_tools import _build_tree_view

        tree_items = [
            {"path": "file.txt", "type": "blob", "size": 1024},
        ]

        tree_view, _, _ = _build_tree_view(tree_items, include_sizes=False)

        assert "1.0KB" not in tree_view


# =============================================================================
# GitHub Composite Tools Tests
# =============================================================================

class TestGitHubCompositeTools:
    """Tests for GitHub composite tools (github_tree, github_get_files, github_explore)."""

    @pytest.fixture
    def mock_github_service(self):
        """Mock GitHub service."""
        with patch('app.services.github_tools.github_service') as mock_service:
            mock_service.has_local_clone.return_value = False
            yield mock_service

    @pytest.fixture
    def mock_cache_service(self):
        """Mock cache service."""
        with patch('app.services.github_tools.cache_service') as mock_cache:
            # Default to cache miss
            mock_cache.get_github_tree.return_value = None
            mock_cache.get_github_file.return_value = None
            yield mock_cache

    @pytest.mark.asyncio
    async def test_github_tree_repo_not_found(self, mock_github_service, mock_cache_service):
        """Test github_tree when repository not found."""
        from app.services.github_tools import github_tree

        mock_github_service.get_repo_by_label.return_value = None
        mock_github_service.get_repos.return_value = []

        result = await github_tree("Nonexistent")

        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_github_tree_no_capability(self, mock_github_service, mock_cache_service):
        """Test github_tree when read capability disabled."""
        from app.services.github_tools import github_tree

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = False
        mock_github_service.get_repo_by_label.return_value = mock_repo

        result = await github_tree("Test")

        assert "Error" in result
        assert "capability" in result.lower()

    @pytest.mark.asyncio
    async def test_github_tree_success(self, mock_github_service, mock_cache_service):
        """Test github_tree success."""
        from app.services.github_tools import github_tree

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_repo.owner = "test-owner"
        mock_repo.repo = "test-repo"
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_default_branch = AsyncMock(return_value="main")
        mock_github_service.get_tree = AsyncMock(return_value=(True, {
            "sha": "abc123",
            "tree": [
                {"path": "README.md", "type": "blob", "size": 1000},
                {"path": "src", "type": "tree"},
                {"path": "src/main.py", "type": "blob", "size": 500},
            ],
            "truncated": False,
        }))

        result = await github_tree("Test")

        assert "test-owner/test-repo" in result
        assert "README.md" in result
        assert "main.py" in result
        mock_cache_service.set_github_tree.assert_called_once()

    @pytest.mark.asyncio
    async def test_github_tree_uses_cache(self, mock_github_service, mock_cache_service):
        """Test github_tree uses cached data."""
        from app.services.github_tools import github_tree

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_repo.owner = "test-owner"
        mock_repo.repo = "test-repo"
        mock_github_service.get_repo_by_label.return_value = mock_repo

        # Return cached data
        mock_cache_service.get_github_tree.return_value = {
            "sha": "cached123",
            "tree": [{"path": "cached.txt", "type": "blob", "size": 100}],
            "truncated": False,
        }

        result = await github_tree("Test", ref="main")

        assert "[cached]" in result
        assert "cached.txt" in result
        # Should not call get_tree since cache hit
        mock_github_service.get_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_github_tree_bypass_cache(self, mock_github_service, mock_cache_service):
        """Test github_tree bypasses cache when requested."""
        from app.services.github_tools import github_tree

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_repo.owner = "test-owner"
        mock_repo.repo = "test-repo"
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_default_branch = AsyncMock(return_value="main")
        mock_github_service.get_tree = AsyncMock(return_value=(True, {
            "sha": "fresh123",
            "tree": [{"path": "fresh.txt", "type": "blob", "size": 100}],
            "truncated": False,
        }))

        result = await github_tree("Test", bypass_cache=True)

        assert "fresh.txt" in result
        # Should not check cache when bypassing
        mock_cache_service.get_github_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_github_get_files_repo_not_found(self, mock_github_service, mock_cache_service):
        """Test github_get_files when repository not found."""
        from app.services.github_tools import github_get_files

        mock_github_service.get_repo_by_label.return_value = None
        mock_github_service.get_repos.return_value = []

        result = await github_get_files("Nonexistent", ["file.txt"])

        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_github_get_files_too_many_files(self, mock_github_service, mock_cache_service):
        """Test github_get_files rejects too many files."""
        from app.services.github_tools import github_get_files

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo

        paths = [f"file{i}.txt" for i in range(15)]  # More than MAX_FILES_PER_REQUEST
        result = await github_get_files("Test", paths)

        assert "Error" in result
        assert "Maximum" in result

    @pytest.mark.asyncio
    async def test_github_get_files_empty_paths(self, mock_github_service, mock_cache_service):
        """Test github_get_files rejects empty paths."""
        from app.services.github_tools import github_get_files

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo

        result = await github_get_files("Test", [])

        assert "Error" in result
        assert "No file paths" in result

    @pytest.mark.asyncio
    async def test_github_get_files_success(self, mock_github_service, mock_cache_service):
        """Test github_get_files success with multiple files."""
        from app.services.github_tools import github_get_files

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_file_contents = AsyncMock(side_effect=[
            (True, {"type": "text", "content": "file 1 content", "size": 14, "name": "file1.txt"}),
            (True, {"type": "text", "content": "file 2 content", "size": 14, "name": "file2.txt"}),
        ])

        result = await github_get_files("Test", ["file1.txt", "file2.txt"])

        assert "file1.txt" in result
        assert "file2.txt" in result
        assert "file 1 content" in result
        assert "file 2 content" in result

    @pytest.mark.asyncio
    async def test_github_get_files_handles_errors(self, mock_github_service, mock_cache_service):
        """Test github_get_files handles file fetch errors gracefully."""
        from app.services.github_tools import github_get_files

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_file_contents = AsyncMock(side_effect=[
            (True, {"type": "text", "content": "good content", "size": 12, "name": "good.txt"}),
            (False, {"error": "not_found", "message": "File not found"}),
        ])

        result = await github_get_files("Test", ["good.txt", "bad.txt"])

        assert "good.txt" in result
        assert "good content" in result
        assert "bad.txt" in result
        assert "ERROR" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_github_explore_success(self, mock_github_service, mock_cache_service):
        """Test github_explore success."""
        from app.services.github_tools import github_explore

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_repo.owner = "test-owner"
        mock_repo.repo = "test-repo"
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_repo_info = AsyncMock(return_value=(True, {
            "full_name": "test-owner/test-repo",
            "description": "Test description",
            "default_branch": "main",
            "visibility": "public",
            "language": "Python",
            "stars": 100,
        }))
        mock_github_service.get_tree = AsyncMock(return_value=(True, {
            "sha": "abc123",
            "tree": [
                {"path": "README.md", "type": "blob", "size": 500},
                {"path": "src", "type": "tree"},
            ],
            "truncated": False,
        }))
        mock_github_service.get_file_contents = AsyncMock(return_value=(True, {
            "type": "text",
            "content": "# Test README\n\nProject description.",
        }))

        result = await github_explore("Test")

        assert "Repository Info" in result
        assert "test-owner/test-repo" in result
        assert "File Structure" in result
        assert "README.md" in result


# =============================================================================
# GitHub Modified Tools Tests
# =============================================================================

class TestGitHubModifiedTools:
    """Tests for modified GitHub tools (github_get_file with truncation, github_search_code with limit)."""

    @pytest.fixture
    def mock_github_service(self):
        """Mock GitHub service."""
        with patch('app.services.github_tools.github_service') as mock_service:
            mock_service.has_local_clone.return_value = False
            yield mock_service

    @pytest.fixture
    def mock_cache_service(self):
        """Mock cache service."""
        with patch('app.services.github_tools.cache_service') as mock_cache:
            mock_cache.get_github_file.return_value = None
            yield mock_cache

    @pytest.mark.asyncio
    async def test_github_get_file_truncates_large_file(self, mock_github_service, mock_cache_service):
        """Test github_get_file truncates files over max_lines."""
        from app.services.github_tools import github_get_file

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo

        # Create a file with 1000 lines
        large_content = "\n".join([f"def func{i}(): pass" for i in range(1000)])
        mock_github_service.get_file_contents = AsyncMock(return_value=(True, {
            "type": "text",
            "content": large_content,
            "size": len(large_content),
            "name": "large.py",
            "path": "large.py",
        }))

        result = await github_get_file("Test", "large.py", max_lines=100)

        assert "truncated" in result.lower()
        assert "1000" in result  # total lines
        assert "100" in result  # truncated to

    @pytest.mark.asyncio
    async def test_github_get_file_uses_cache(self, mock_github_service, mock_cache_service):
        """Test github_get_file uses cached data."""
        from app.services.github_tools import github_get_file

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo

        # Return cached data
        mock_cache_service.get_github_file.return_value = {
            "type": "text",
            "content": "cached content",
            "size": 14,
            "name": "cached.txt",
            "path": "cached.txt",
        }

        result = await github_get_file("Test", "cached.txt")

        assert "cached content" in result
        assert "[cached]" in result
        mock_github_service.get_file_contents.assert_not_called()

    @pytest.mark.asyncio
    async def test_github_get_file_bypass_cache(self, mock_github_service, mock_cache_service):
        """Test github_get_file bypasses cache when requested."""
        from app.services.github_tools import github_get_file

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_file_contents = AsyncMock(return_value=(True, {
            "type": "text",
            "content": "fresh content",
            "size": 13,
            "name": "fresh.txt",
            "path": "fresh.txt",
        }))

        result = await github_get_file("Test", "fresh.txt", bypass_cache=True)

        assert "fresh content" in result
        mock_cache_service.get_github_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_github_get_file_caches_result(self, mock_github_service, mock_cache_service):
        """Test github_get_file caches the result."""
        from app.services.github_tools import github_get_file

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_file_contents = AsyncMock(return_value=(True, {
            "type": "text",
            "content": "new content",
            "size": 11,
            "name": "new.txt",
            "path": "new.txt",
        }))

        await github_get_file("Test", "new.txt")

        mock_cache_service.set_github_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_github_search_code_limits_results(self, mock_github_service, mock_cache_service):
        """Test github_search_code limits results to MAX_SEARCH_RESULTS."""
        from app.services.github_tools import github_search_code, MAX_SEARCH_RESULTS

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo

        # Return more results than MAX_SEARCH_RESULTS
        many_results = [{"path": f"file{i}.py"} for i in range(25)]
        mock_github_service.search_code = AsyncMock(return_value=(True, many_results))

        result = await github_search_code("Test", "function")

        # Should show limited results
        assert f"showing {MAX_SEARCH_RESULTS}" in result
        assert "25" in result  # total matches
        assert "more matches not shown" in result.lower()

    @pytest.mark.asyncio
    async def test_github_search_code_shows_all_when_under_limit(self, mock_github_service, mock_cache_service):
        """Test github_search_code shows all results when under limit."""
        from app.services.github_tools import github_search_code

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo

        results = [{"path": f"file{i}.py"} for i in range(5)]
        mock_github_service.search_code = AsyncMock(return_value=(True, results))

        result = await github_search_code("Test", "function")

        assert "5 matches" in result
        assert "more matches not shown" not in result.lower()


# =============================================================================
# GitHub Cache Invalidation Tests
# =============================================================================

class TestGitHubCacheInvalidation:
    """Tests for GitHub cache invalidation on write operations."""

    @pytest.fixture
    def mock_github_service(self):
        """Mock GitHub service."""
        with patch('app.services.github_tools.github_service') as mock_service:
            mock_service.has_local_clone.return_value = False
            yield mock_service

    @pytest.fixture
    def mock_cache_service(self):
        """Mock cache service."""
        with patch('app.services.github_tools.cache_service') as mock_cache:
            yield mock_cache

    @pytest.mark.asyncio
    async def test_github_commit_file_invalidates_cache(self, mock_github_service, mock_cache_service):
        """Test github_commit_file invalidates file and tree cache."""
        from app.services.github_tools import github_commit_file

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.commit_file = AsyncMock(return_value=(True, {
            "sha": "abc123",
            "action": "updated",
            "html_url": "https://github.com/test/test/commit/abc123",
        }))

        await github_commit_file("Test", "file.txt", "content", "commit msg", "feature-branch")

        # Should invalidate both file cache and tree cache
        mock_cache_service.invalidate_github_file.assert_called_once_with("Test", "file.txt", "feature-branch")
        mock_cache_service.invalidate_github_tree.assert_called_once_with("Test", "feature-branch")

    @pytest.mark.asyncio
    async def test_github_delete_file_invalidates_cache(self, mock_github_service, mock_cache_service):
        """Test github_delete_file invalidates file and tree cache."""
        from app.services.github_tools import github_delete_file

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.delete_file = AsyncMock(return_value=(True, {
            "sha": "def456",
        }))

        await github_delete_file("Test", "deleted.txt", "delete msg", "feature-branch")

        # Should invalidate both file cache and tree cache
        mock_cache_service.invalidate_github_file.assert_called_once_with("Test", "deleted.txt", "feature-branch")
        mock_cache_service.invalidate_github_tree.assert_called_once_with("Test", "feature-branch")

    @pytest.mark.asyncio
    async def test_github_commit_file_no_invalidation_on_failure(self, mock_github_service, mock_cache_service):
        """Test github_commit_file does not invalidate cache on failure."""
        from app.services.github_tools import github_commit_file

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.commit_file = AsyncMock(return_value=(False, {
            "error": "conflict",
            "message": "File conflict",
        }))

        await github_commit_file("Test", "file.txt", "content", "commit msg", "feature-branch")

        # Should NOT invalidate cache when commit fails
        mock_cache_service.invalidate_github_file.assert_not_called()
        mock_cache_service.invalidate_github_tree.assert_not_called()


# =============================================================================
# GitHub Tool Registration with Composite Tools Tests
# =============================================================================

class TestGitHubToolRegistrationComposite:
    """Tests for GitHub tool registration including composite tools."""

    def test_composite_tools_registered_for_read_capability(self):
        """Test composite tools are registered when read capability is available."""
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

        # Should have composite tools
        assert "github_explore" in github_tool_names
        assert "github_tree" in github_tool_names
        assert "github_get_files" in github_tool_names

        # Should also have standard read tools
        assert "github_repo_info" in github_tool_names
        assert "github_get_file" in github_tool_names
        assert "github_search_code" in github_tool_names


# =============================================================================
# GitHub Local Clone Tree Tests
# =============================================================================

class TestGitHubServiceLocalTree:
    """Tests for get_tree_local method."""

    def test_get_tree_local_no_local_clone(self):
        """Test get_tree_local when no local clone is configured."""
        from app.services.github_service import GitHubService

        service = GitHubService()
        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="ghp_test",
            local_clone_path=None,
        )

        success, result = service.get_tree_local(repo)

        assert success is False
        assert result["error"] == "no_local_clone"

    def test_get_tree_local_invalid_path(self, tmp_path):
        """Test get_tree_local with non-existent path."""
        from app.services.github_service import GitHubService

        service = GitHubService()
        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="ghp_test",
            local_clone_path=str(tmp_path / "nonexistent"),
        )

        success, result = service.get_tree_local(repo)

        assert success is False
        assert result["error"] == "not_found"

    def test_get_tree_local_success(self, tmp_path):
        """Test get_tree_local with valid local clone."""
        from app.services.github_service import GitHubService

        # Create mock repository structure
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "src" / "main.py").write_text("print('hello')")

        service = GitHubService()
        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="ghp_test",
            local_clone_path=str(tmp_path),
        )

        success, result = service.get_tree_local(repo)

        assert success is True
        assert result["source"] == "local"
        assert result["sha"] is None
        assert result["truncated"] is False

        tree = result["tree"]
        paths = [item["path"] for item in tree]
        assert "README.md" in paths
        assert "src" in paths
        # Note: path separator may vary, check for the file name
        src_main_paths = [p for p in paths if "main.py" in p]
        assert len(src_main_paths) == 1

    def test_get_tree_local_skips_hidden_files(self, tmp_path):
        """Test get_tree_local skips hidden files and .git."""
        from app.services.github_service import GitHubService

        # Create mock repository structure
        (tmp_path / ".git").mkdir()
        (tmp_path / ".hidden_file").write_text("hidden")
        (tmp_path / ".hidden_dir").mkdir()
        (tmp_path / ".hidden_dir" / "file.txt").write_text("in hidden dir")
        (tmp_path / "visible.txt").write_text("visible")

        service = GitHubService()
        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="ghp_test",
            local_clone_path=str(tmp_path),
        )

        success, result = service.get_tree_local(repo)

        assert success is True
        tree = result["tree"]
        paths = [item["path"] for item in tree]

        # Should only have visible.txt
        assert "visible.txt" in paths
        assert ".hidden_file" not in paths
        assert ".hidden_dir" not in paths
        assert ".git" not in paths


# =============================================================================
# GitHub Composite Tools Local Clone Tests
# =============================================================================

class TestGitHubCompositeToolsLocalClone:
    """Tests for composite tools using local clone."""

    @pytest.fixture
    def mock_github_service(self):
        """Mock GitHub service."""
        with patch('app.services.github_tools.github_service') as mock_service:
            yield mock_service

    @pytest.fixture
    def mock_cache_service(self):
        """Mock cache service."""
        with patch('app.services.github_tools.cache_service') as mock_cache:
            mock_cache.get_github_tree.return_value = None
            mock_cache.get_github_file.return_value = None
            yield mock_cache

    @pytest.mark.asyncio
    async def test_github_tree_uses_local_clone(self, mock_github_service, mock_cache_service):
        """Test github_tree uses local clone when available and no ref specified."""
        from app.services.github_tools import github_tree

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_repo.owner = "test-owner"
        mock_repo.repo = "test-repo"
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.has_local_clone.return_value = True
        mock_github_service.get_tree_local.return_value = (True, {
            "sha": None,
            "tree": [
                {"path": "local_file.txt", "type": "blob", "size": 100},
            ],
            "truncated": False,
            "source": "local",
        })

        result = await github_tree("Test")  # No ref specified

        assert "local_file.txt" in result
        assert "[local]" in result
        mock_github_service.get_tree_local.assert_called_once()
        mock_github_service.get_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_github_tree_uses_api_when_ref_specified(self, mock_github_service, mock_cache_service):
        """Test github_tree uses API when ref is specified even if local clone available."""
        from app.services.github_tools import github_tree

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_repo.owner = "test-owner"
        mock_repo.repo = "test-repo"
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.has_local_clone.return_value = True
        mock_github_service.get_tree = AsyncMock(return_value=(True, {
            "sha": "abc123",
            "tree": [{"path": "api_file.txt", "type": "blob", "size": 100}],
            "truncated": False,
        }))

        result = await github_tree("Test", ref="feature-branch")  # ref specified

        assert "api_file.txt" in result
        assert "[local]" not in result
        mock_github_service.get_tree_local.assert_not_called()
        mock_github_service.get_tree.assert_called_once()

    @pytest.mark.asyncio
    async def test_github_get_files_uses_local_clone(self, mock_github_service, mock_cache_service):
        """Test github_get_files uses local clone when available and no ref specified."""
        from app.services.github_tools import github_get_files

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.has_local_clone.return_value = True
        mock_github_service.get_file_contents_local.return_value = (True, {
            "type": "text",
            "content": "local content",
            "size": 13,
            "name": "test.txt",
        })

        result = await github_get_files("Test", ["test.txt"])

        assert "local content" in result
        mock_github_service.get_file_contents_local.assert_called_once()
        mock_github_service.get_file_contents.assert_not_called()

    @pytest.mark.asyncio
    async def test_github_get_files_uses_api_when_ref_specified(self, mock_github_service, mock_cache_service):
        """Test github_get_files uses API when ref is specified."""
        from app.services.github_tools import github_get_files

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.has_local_clone.return_value = True
        mock_github_service.get_file_contents = AsyncMock(return_value=(True, {
            "type": "text",
            "content": "api content",
            "size": 11,
            "name": "test.txt",
        }))

        result = await github_get_files("Test", ["test.txt"], ref="feature-branch")

        assert "api content" in result
        mock_github_service.get_file_contents_local.assert_not_called()
        mock_github_service.get_file_contents.assert_called_once()

    @pytest.mark.asyncio
    async def test_github_explore_uses_local_clone_for_tree(self, mock_github_service, mock_cache_service):
        """Test github_explore uses local clone for tree when available."""
        from app.services.github_tools import github_explore

        mock_repo = MagicMock()
        mock_repo.has_capability.return_value = True
        mock_repo.owner = "test-owner"
        mock_repo.repo = "test-repo"
        mock_github_service.get_repo_by_label.return_value = mock_repo
        mock_github_service.get_repo_info = AsyncMock(return_value=(True, {
            "full_name": "test-owner/test-repo",
            "description": "Test",
            "default_branch": "main",
            "visibility": "public",
            "language": "Python",
            "stars": 0,
        }))
        mock_github_service.has_local_clone.return_value = True
        mock_github_service.get_tree_local.return_value = (True, {
            "sha": None,
            "tree": [{"path": "local_tree.txt", "type": "blob", "size": 100}],
            "truncated": False,
            "source": "local",
        })
        mock_github_service.get_file_contents_local.return_value = (False, {"error": "not_found"})

        result = await github_explore("Test")

        assert "local_tree.txt" in result
        assert "[local]" in result
        mock_github_service.get_tree_local.assert_called_once()
        mock_github_service.get_tree.assert_not_called()


class TestGitHubServiceGitignoreSupport:
    """Tests for .gitignore and sensitive file blocking in local clone operations."""

    @pytest.fixture
    def github_service_instance(self):
        """Create a real GitHubService instance for testing gitignore methods."""
        from app.services.github_service import GitHubService
        return GitHubService()

    def test_is_sensitive_file_env(self, github_service_instance):
        """Test that .env files are detected as sensitive."""
        assert github_service_instance._is_sensitive_file(".env") is True
        assert github_service_instance._is_sensitive_file(".env.local") is True
        assert github_service_instance._is_sensitive_file(".env.production") is True
        assert github_service_instance._is_sensitive_file("config/.env") is True

    def test_is_sensitive_file_credentials(self, github_service_instance):
        """Test that credential files are detected as sensitive."""
        assert github_service_instance._is_sensitive_file("credentials.json") is True
        assert github_service_instance._is_sensitive_file("secrets.yaml") is True
        assert github_service_instance._is_sensitive_file("api_key.txt") is True

    def test_is_sensitive_file_keys(self, github_service_instance):
        """Test that key files are detected as sensitive."""
        assert github_service_instance._is_sensitive_file("server.pem") is True
        assert github_service_instance._is_sensitive_file("private.key") is True
        assert github_service_instance._is_sensitive_file("id_rsa") is True
        assert github_service_instance._is_sensitive_file(".ssh/id_rsa") is True

    def test_is_sensitive_file_normal_files(self, github_service_instance):
        """Test that normal files are NOT detected as sensitive."""
        assert github_service_instance._is_sensitive_file("README.md") is False
        assert github_service_instance._is_sensitive_file("main.py") is False
        assert github_service_instance._is_sensitive_file("package.json") is False
        assert github_service_instance._is_sensitive_file("src/app.ts") is False

    def test_parse_gitignore_empty(self, github_service_instance, tmp_path):
        """Test parsing empty .gitignore."""
        patterns = github_service_instance._parse_gitignore(tmp_path)
        assert patterns == []

    def test_parse_gitignore_with_patterns(self, github_service_instance, tmp_path):
        """Test parsing .gitignore with patterns."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n*.pyc\n# comment\n\n__pycache__/\n")

        patterns = github_service_instance._parse_gitignore(tmp_path)

        assert "node_modules/" in patterns
        assert "*.pyc" in patterns
        assert "__pycache__/" in patterns
        assert "# comment" not in patterns
        assert "" not in patterns

    def test_matches_gitignore_simple_pattern(self, github_service_instance):
        """Test simple gitignore pattern matching."""
        patterns = ["*.pyc", "node_modules/"]

        assert github_service_instance._matches_gitignore("test.pyc", patterns) is True
        assert github_service_instance._matches_gitignore("src/test.pyc", patterns) is True
        assert github_service_instance._matches_gitignore("test.py", patterns) is False

    def test_matches_gitignore_directory_pattern(self, github_service_instance):
        """Test directory gitignore pattern matching."""
        patterns = ["node_modules/", "__pycache__/"]

        assert github_service_instance._matches_gitignore("node_modules", patterns) is True
        assert github_service_instance._matches_gitignore("src/node_modules", patterns) is True
        assert github_service_instance._matches_gitignore("__pycache__", patterns) is True

    def test_matches_gitignore_negation(self, github_service_instance):
        """Test gitignore negation pattern."""
        patterns = ["*.log", "!important.log"]

        assert github_service_instance._matches_gitignore("debug.log", patterns) is True
        assert github_service_instance._matches_gitignore("important.log", patterns) is False

    def test_should_exclude_path_sensitive(self, github_service_instance):
        """Test _should_exclude_path detects sensitive files."""
        should_exclude, reason = github_service_instance._should_exclude_path(
            ".env", [], is_directory=False
        )
        assert should_exclude is True
        assert reason == "sensitive_file"

    def test_should_exclude_path_gitignore(self, github_service_instance):
        """Test _should_exclude_path detects gitignored files."""
        patterns = ["*.pyc", "node_modules/"]
        should_exclude, reason = github_service_instance._should_exclude_path(
            "test.pyc", patterns, is_directory=False
        )
        assert should_exclude is True
        assert reason == "gitignore"

    def test_should_exclude_path_allowed(self, github_service_instance):
        """Test _should_exclude_path allows normal files."""
        patterns = ["*.pyc"]
        should_exclude, reason = github_service_instance._should_exclude_path(
            "main.py", patterns, is_directory=False
        )
        assert should_exclude is False
        assert reason is None

    def test_get_file_contents_local_blocks_env(self, github_service_instance, tmp_path):
        """Test get_file_contents_local blocks .env files."""
        from app.config import GitHubRepoConfig

        # Create test structure
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET_KEY=abc123")

        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="test-token",
            local_clone_path=str(tmp_path),
        )

        success, data = github_service_instance.get_file_contents_local(repo, ".env")

        assert success is False
        assert data["error"] == "sensitive_file"
        assert "sensitive file" in data["message"]

    def test_get_file_contents_local_blocks_gitignored(self, github_service_instance, tmp_path):
        """Test get_file_contents_local blocks gitignored files."""
        from app.config import GitHubRepoConfig

        # Create test structure
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("build/\n*.log\n")
        log_file = tmp_path / "debug.log"
        log_file.write_text("debug output")

        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="test-token",
            local_clone_path=str(tmp_path),
        )

        success, data = github_service_instance.get_file_contents_local(repo, "debug.log")

        assert success is False
        assert data["error"] == "gitignored"
        assert ".gitignore" in data["message"]

    def test_get_file_contents_local_allows_normal_files(self, github_service_instance, tmp_path):
        """Test get_file_contents_local allows normal files."""
        from app.config import GitHubRepoConfig

        # Create test structure
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        readme = tmp_path / "README.md"
        readme.write_text("# Test Project")

        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="test-token",
            local_clone_path=str(tmp_path),
        )

        success, data = github_service_instance.get_file_contents_local(repo, "README.md")

        assert success is True
        assert data["content"] == "# Test Project"

    def test_list_contents_local_filters_sensitive(self, github_service_instance, tmp_path):
        """Test list_contents_local filters out sensitive files."""
        from app.config import GitHubRepoConfig

        # Create test structure
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / ".env").write_text("SECRET=123")
        (tmp_path / "main.py").write_text("print('hello')")

        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="test-token",
            local_clone_path=str(tmp_path),
        )

        success, items = github_service_instance.list_contents_local(repo)

        assert success is True
        names = [item["name"] for item in items]
        assert "README.md" in names
        assert "main.py" in names
        assert ".env" not in names

    def test_list_contents_local_filters_gitignored(self, github_service_instance, tmp_path):
        """Test list_contents_local filters out gitignored files."""
        from app.config import GitHubRepoConfig

        # Create test structure
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.log\nbuild/\n")
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "debug.log").write_text("debug")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "output.js").write_text("compiled")

        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="test-token",
            local_clone_path=str(tmp_path),
        )

        success, items = github_service_instance.list_contents_local(repo)

        assert success is True
        names = [item["name"] for item in items]
        assert "README.md" in names
        assert "debug.log" not in names
        assert "build" not in names

    def test_get_tree_local_filters_sensitive(self, github_service_instance, tmp_path):
        """Test get_tree_local filters out sensitive files."""
        from app.config import GitHubRepoConfig

        # Create test structure
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / ".env").write_text("SECRET=123")
        (tmp_path / "credentials.json").write_text("{}")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("print('hello')")

        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="test-token",
            local_clone_path=str(tmp_path),
        )

        success, data = github_service_instance.get_tree_local(repo)

        assert success is True
        paths = [item["path"] for item in data["tree"]]
        assert "README.md" in paths
        assert "src" in paths
        assert "src/main.py" in paths
        assert ".env" not in paths
        assert "credentials.json" not in paths

    def test_get_tree_local_filters_gitignored(self, github_service_instance, tmp_path):
        """Test get_tree_local filters out gitignored files and directories."""
        from app.config import GitHubRepoConfig

        # Create test structure
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n*.pyc\n__pycache__/\n")
        (tmp_path / "README.md").write_text("# Test")
        (tmp_path / "test.pyc").write_text("compiled")
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "lodash").mkdir()
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "module.cpython-39.pyc").write_bytes(b"\x00")

        repo = GitHubRepoConfig(
            owner="test",
            repo="test",
            label="Test",
            token="test-token",
            local_clone_path=str(tmp_path),
        )

        success, data = github_service_instance.get_tree_local(repo)

        assert success is True
        paths = [item["path"] for item in data["tree"]]
        assert "README.md" in paths
        assert "test.pyc" not in paths
        assert "node_modules" not in paths
        assert "node_modules/lodash" not in paths
        assert "__pycache__" not in paths

    def test_sensitive_files_case_insensitive(self, github_service_instance):
        """Test that sensitive file detection is case insensitive."""
        assert github_service_instance._is_sensitive_file(".ENV") is True
        assert github_service_instance._is_sensitive_file(".Env") is True
        assert github_service_instance._is_sensitive_file("CREDENTIALS.JSON") is True
        assert github_service_instance._is_sensitive_file("Secrets.yaml") is True
