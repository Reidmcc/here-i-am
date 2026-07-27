"""
Security regression tests for tool scope boundaries.

Each case here corresponds to a way a tool could be driven outside the
actions it was designed for, using only ordinary tool arguments:

- GitHub: retargeting the API at a different repository via path traversal
- GitHub: widening a code search past the configured repository
- GitHub: reading blocklisted sensitive files through the API path
- Notes: reaching another entity's notes folder
- web_fetch: reaching the loopback interface, private ranges, cloud
  metadata, or non-HTTP schemes
"""

import ipaddress
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import GitHubRepoConfig
from app.services.github_service import (
    GitHubService,
    InvalidTargetError,
    safe_git_ref,
    safe_repo_path,
)
from app.services.notes_service import NotesService
from app.services.web_tools import _is_blocked_address, _validate_fetch_url, web_fetch


# =============================================================================
# GitHub: URL path traversal
# =============================================================================

class TestSafeRepoPath:
    """safe_repo_path must reject anything that could retarget the URL."""

    def test_accepts_ordinary_paths(self):
        assert safe_repo_path("src/app/main.py") == "src/app/main.py"
        assert safe_repo_path("README.md") == "README.md"
        assert safe_repo_path("") == ""

    def test_strips_leading_and_trailing_slashes(self):
        assert safe_repo_path("/src/main.py") == "src/main.py"
        assert safe_repo_path("src/") == "src"

    def test_rejects_dot_segments(self):
        for bad in [
            "../../../../repos/victim/private/contents/secret.txt",
            "a/../../../user/repos",
            "..",
            "./x.py",
            "src/../../etc/passwd",
        ]:
            with pytest.raises(InvalidTargetError):
                safe_repo_path(bad)

    def test_rejects_backslash_traversal(self):
        with pytest.raises(InvalidTargetError):
            safe_repo_path("..\\..\\repos\\victim\\private")

    def test_percent_encodes_url_significant_characters(self):
        # '?' and '#' would otherwise start a query string or fragment
        assert safe_repo_path("dir/a?b.md") == "dir/a%3Fb.md"
        assert safe_repo_path("dir/a#b.md") == "dir/a%23b.md"
        assert safe_repo_path("dir/a b.md") == "dir/a%20b.md"

    def test_encoded_traversal_is_not_decoded_into_a_dot_segment(self):
        # %2F stays encoded, so this cannot become a path separator
        assert safe_repo_path("a%2F..%2Fb.md") == "a%252F..%252Fb.md"


class TestSafeGitRef:
    """safe_git_ref must reject refs that could retarget the URL."""

    def test_accepts_ordinary_refs(self):
        assert safe_git_ref("main") == "main"
        assert safe_git_ref("feature/some-branch") == "feature/some-branch"
        assert safe_git_ref("a1b2c3d4") == "a1b2c3d4"

    def test_rejects_traversal_and_illegal_ref_characters(self):
        for bad in ["../../other", "..", "/main", "main/", "ma in", "re:f", "x~1", "y^2", "a?b"]:
            with pytest.raises(InvalidTargetError):
                safe_git_ref(bad)

    def test_rejects_empty(self):
        with pytest.raises(InvalidTargetError):
            safe_git_ref("")


@pytest.fixture
def repo_config():
    return GitHubRepoConfig(
        owner="owner",
        repo="repo",
        label="myrepo",
        token="ghp_test",
        protected_branches=["main"],
        capabilities=["read", "commit", "branch", "pr", "issue"],
    )


class TestGitHubTraversalIsBlocked:
    """No request may leave the configured repository's endpoint space."""

    @pytest.mark.asyncio
    async def test_get_file_contents_rejects_traversal(self, repo_config):
        service = GitHubService()
        with patch.object(service, "_request", new=AsyncMock()) as mock_request:
            success, data = await service.get_file_contents(
                repo_config, "../../../../repos/victim/private/contents/secret.txt"
            )
        assert success is False
        assert data["error"] == "invalid_path"
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_commit_file_rejects_traversal(self, repo_config):
        service = GitHubService()
        with patch.object(service, "_request", new=AsyncMock()) as mock_request:
            success, data = await service.commit_file(
                repo_config,
                path="../../../../repos/victim/private/contents/backdoor.txt",
                content="x",
                message="m",
                branch="feature",
            )
        assert success is False
        assert data["error"] == "invalid_path"
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_file_rejects_traversal(self, repo_config):
        service = GitHubService()
        with patch.object(service, "_request", new=AsyncMock()) as mock_request:
            success, data = await service.delete_file(
                repo_config,
                path="../../../../repos/victim/private/contents/README.md",
                message="m",
                branch="feature",
            )
        assert success is False
        assert data["error"] == "invalid_path"
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_branch_rejects_traversal_in_from_ref(self, repo_config):
        service = GitHubService()
        with patch.object(service, "_request", new=AsyncMock()) as mock_request:
            success, data = await service.create_branch(
                repo_config, branch_name="new", from_ref="../../../victim/private/git/ref/heads/main"
            )
        assert success is False
        assert data["error"] == "invalid_ref"
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_contents_rejects_traversal(self, repo_config):
        service = GitHubService()
        with patch.object(service, "_request", new=AsyncMock()) as mock_request:
            success, items = await service.list_contents(repo_config, "../../../../repos/victim/private")
        assert success is False
        assert items[0]["error"] == "invalid_path"
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_request_backstop_blocks_traversal_from_any_caller(self, repo_config):
        """Even a call site that forgot to validate cannot escape."""
        service = GitHubService()
        status, data = await service._request(
            "PUT", "/repos/owner/repo/contents/../../victim/private/contents/x", "token"
        )
        assert status == 400
        assert data["error"] == "invalid_path"

    @pytest.mark.asyncio
    async def test_valid_path_still_reaches_the_expected_endpoint(self, repo_config):
        service = GitHubService()
        with patch.object(service, "_request", new=AsyncMock(return_value=(404, {}))) as mock_request:
            await service.get_file_contents(repo_config, "src/main.py")
        endpoint = mock_request.call_args[0][1]
        assert endpoint == "/repos/owner/repo/contents/src/main.py"


class TestCodeSearchStaysScoped:
    """Code search must not be widened past the configured repository."""

    @pytest.mark.asyncio
    async def test_rejects_scope_qualifiers_in_query(self, repo_config):
        service = GitHubService()
        for bad_query in [
            "secret repo:someoneelse/private",
            "secret org:evilcorp",
            "secret user:someone",
            "secret OWNER:someone",
        ]:
            with patch.object(service, "_request", new=AsyncMock()) as mock_request:
                success, results = await service.search_code(repo_config, bad_query)
            assert success is False, bad_query
            assert results[0]["error"] == "invalid_query"
            mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_the_repo_scoped_query(self, repo_config):
        """The scoping qualifier must actually be transmitted."""
        service = GitHubService()
        with patch.object(service, "_request", new=AsyncMock(return_value=(200, {"items": []}))) as mock_request:
            success, _ = await service.search_code(repo_config, "needle")
        assert success is True
        params = mock_request.call_args.kwargs["params"]
        assert params["q"] == "needle repo:owner/repo"

    @pytest.mark.asyncio
    async def test_drops_results_from_other_repositories(self, repo_config):
        service = GitHubService()
        payload = {
            "items": [
                {"path": "a.py", "repository": {"full_name": "owner/repo"}},
                {"path": "b.py", "repository": {"full_name": "victim/private"}},
            ]
        }
        with patch.object(service, "_request", new=AsyncMock(return_value=(200, payload))):
            success, results = await service.search_code(repo_config, "needle")
        assert success is True
        assert [r["path"] for r in results] == ["a.py"]


class TestSensitiveFilesBlockedOnApiPath:
    """The sensitive-file blocklist must not be bypassable by supplying a ref."""

    @pytest.mark.asyncio
    async def test_get_file_contents_blocks_sensitive_file(self, repo_config):
        service = GitHubService()
        with patch.object(service, "_request", new=AsyncMock()) as mock_request:
            success, data = await service.get_file_contents(repo_config, ".env", ref="main")
        assert success is False
        assert data["error"] == "sensitive_file"
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_listing_hides_sensitive_files(self, repo_config):
        service = GitHubService()
        payload = [
            {"type": "file", "name": "main.py", "path": "main.py", "size": 1, "sha": "a"},
            {"type": "file", "name": ".env", "path": ".env", "size": 1, "sha": "b"},
            {"type": "file", "name": "id_rsa", "path": "id_rsa", "size": 1, "sha": "c"},
        ]
        with patch.object(service, "_request", new=AsyncMock(return_value=(200, payload))):
            success, items = await service.list_contents(repo_config, "")
        assert success is True
        assert [i["name"] for i in items] == ["main.py"]

    @pytest.mark.asyncio
    async def test_tree_hides_sensitive_files(self, repo_config):
        service = GitHubService()
        payload = {
            "sha": "abc",
            "tree": [
                {"path": "main.py", "type": "blob", "size": 1},
                {"path": "config/secrets.json", "type": "blob", "size": 1},
            ],
            "truncated": False,
        }
        with patch.object(service, "_request", new=AsyncMock(return_value=(200, payload))):
            success, data = await service.get_tree(repo_config)
        assert success is True
        assert [i["path"] for i in data["tree"]] == ["main.py"]


# =============================================================================
# Notes: cross-entity access
# =============================================================================

class TestNotesStayInTheirFolder:
    """One entity must not be able to reach another entity's notes."""

    @pytest.fixture
    def notes(self, tmp_path):
        service = NotesService()
        service._base_dir = tmp_path
        return service

    def test_prefix_named_sibling_is_not_reachable(self, notes, tmp_path):
        """'Ada' must not reach 'Adam' - the case a startswith check misses."""
        victim = tmp_path / "Adam"
        victim.mkdir(parents=True)
        (victim / "index.md").write_text("Adam's private notes", encoding="utf-8")

        read = notes.read_note("Ada", "../Adam/index.md")
        assert read["success"] is False
        assert read["error"] == "Invalid file path"

        written = notes.write_note("Ada", "../Adam/index.md", "overwritten")
        assert written["success"] is False
        assert (victim / "index.md").read_text(encoding="utf-8") == "Adam's private notes"

        deleted = notes.delete_note("Ada", "../Adam/notes.md")
        assert deleted["success"] is False

    def test_index_guard_cannot_be_bypassed_with_a_path(self, notes, tmp_path):
        victim = tmp_path / "Adam"
        victim.mkdir(parents=True)
        (victim / "index.md").write_text("keep me", encoding="utf-8")

        result = notes.delete_note("Ada", "../Adam/index.md")
        assert result["success"] is False
        assert (victim / "index.md").exists()

    def test_escape_above_the_notes_root_is_blocked(self, notes):
        for bad in ["../../etc/passwd.md", "/etc/evil.md", "../outside.md"]:
            assert notes.write_note("Ada", bad, "x")["success"] is False

    def test_subdirectory_paths_are_rejected(self, notes):
        assert notes.write_note("Ada", "sub/dir/note.md", "x")["success"] is False

    def test_edit_note_rejects_traversal(self, notes, tmp_path):
        victim = tmp_path / "Adam"
        victim.mkdir(parents=True)
        (victim / "index.md").write_text("original", encoding="utf-8")

        result = notes.edit_note("Ada", "../Adam/index.md", "original", "tampered")
        assert result["success"] is False
        assert (victim / "index.md").read_text(encoding="utf-8") == "original"

    def test_ordinary_notes_still_work(self, notes):
        assert notes.write_note("Ada", "thoughts.md", "hello")["success"] is True
        assert notes.read_note("Ada", "thoughts.md")["content"] == "hello"
        assert notes.write_note("Ada", "shared-note.md", "s", shared=True)["success"] is True
        assert notes.read_note("Ada", "shared-note.md", shared=True)["content"] == "s"
        assert notes.delete_note("Ada", "thoughts.md")["success"] is True


# =============================================================================
# web_fetch: SSRF
# =============================================================================

class TestBlockedAddresses:
    def test_blocks_non_public_ranges(self):
        for addr in [
            "127.0.0.1", "127.5.5.5", "0.0.0.0", "10.0.0.1", "192.168.1.1",
            "172.16.0.1", "169.254.169.254", "224.0.0.1", "240.0.0.1",
            "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1",
        ]:
            assert _is_blocked_address(ipaddress.ip_address(addr)) is True, addr

    def test_allows_public_addresses(self):
        for addr in ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"]:
            assert _is_blocked_address(ipaddress.ip_address(addr)) is False, addr


class TestValidateFetchUrl:
    def test_rejects_non_http_schemes(self):
        for url in [
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com",
            "data:text/html,<script>alert(1)</script>",
        ]:
            error = _validate_fetch_url(url)
            assert error is not None and "scheme" in error.lower(), url

    def test_rejects_loopback_and_metadata_by_literal_ip(self):
        for url in [
            "http://127.0.0.1:8000/api/conversations",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/internal",
            "http://[::1]:8000/api/entities",
        ]:
            error = _validate_fetch_url(url)
            assert error is not None and "public internet" in error, url

    def test_rejects_hostname_that_resolves_to_loopback(self):
        with patch("app.services.web_tools.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 80))]
            error = _validate_fetch_url("http://internal.example.com/admin")
        assert error is not None and "public internet" in error

    def test_allows_public_hostname(self):
        with patch("app.services.web_tools.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
            assert _validate_fetch_url("https://example.com/page") is None

    def test_rejects_url_without_a_host(self):
        assert _validate_fetch_url("http://") is not None


class TestWebFetchRefusesNonPublicTargets:
    @pytest.mark.asyncio
    async def test_does_not_issue_a_request_for_a_blocked_target(self):
        with patch("httpx.AsyncClient") as mock_client:
            result = await web_fetch("http://127.0.0.1:8000/api/conversations")
        assert "public internet" in result
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_refuses_file_scheme(self):
        with patch("httpx.AsyncClient") as mock_client:
            result = await web_fetch("file:///etc/passwd")
        assert "scheme" in result.lower()
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_redirect_into_the_private_network_is_blocked(self):
        """A public URL must not be able to bounce the fetch to loopback."""
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"location": "http://127.0.0.1:8000/api/conversations"}

        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=redirect)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_instance), \
             patch("app.services.web_tools.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("93.184.216.34", 80))]):
            result = await web_fetch("http://example.com/redirector")

        assert "public internet" in result
        # The first hop was made; the second was refused.
        assert mock_instance.get.await_count == 1
