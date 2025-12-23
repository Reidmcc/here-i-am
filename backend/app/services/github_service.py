"""
GitHub Service for repository integration.

This service provides:
- GitHub API client with rate limit tracking
- Repository configuration management
- Binary file detection and large file handling
"""

import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings, GitHubRepoConfig

logger = logging.getLogger(__name__)

# Constants
GITHUB_API_BASE = "https://api.github.com"
GITHUB_TIMEOUT = 30.0  # seconds
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
CONTENTS_API_LIMIT = 1 * 1024 * 1024  # 1MB - Contents API limit

# Binary file extensions
BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.svg',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.tar', '.gz', '.rar', '.7z',
    '.exe', '.dll', '.so', '.dylib',
    '.mp3', '.mp4', '.wav', '.avi', '.mov', '.mkv',
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    '.pyc', '.pyo', '.class', '.o', '.a',
    '.db', '.sqlite', '.sqlite3',
}


@dataclass
class RateLimitInfo:
    """Rate limit information for a GitHub token."""
    remaining: int
    limit: int
    reset_timestamp: int

    @property
    def reset_time_formatted(self) -> str:
        """Format reset time as human-readable string."""
        reset_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.reset_timestamp))
        seconds_until_reset = max(0, self.reset_timestamp - int(time.time()))
        if seconds_until_reset > 0:
            minutes = seconds_until_reset // 60
            seconds = seconds_until_reset % 60
            return f"{reset_time} ({minutes}m {seconds}s remaining)"
        return reset_time


class GitHubService:
    """
    Service for GitHub API interactions.

    Provides:
    - Repository configuration management
    - Rate limit tracking per token
    - Centralized API request handling
    - Binary file detection
    - Large file handling (Git Data API fallback)
    """

    def __init__(self):
        # Rate limit tracking: token_hash -> RateLimitInfo
        self._rate_limits: Dict[str, RateLimitInfo] = {}
        logger.info("GitHubService initialized")

    def _hash_token(self, token: str) -> str:
        """Hash a token for use as a dictionary key."""
        return hashlib.sha256(token.encode()).hexdigest()[:16]

    def get_repos(self) -> List[GitHubRepoConfig]:
        """Get all configured GitHub repositories."""
        if not settings.github_tools_enabled:
            return []
        return settings.get_github_repos()

    def get_repo_by_label(self, label: str) -> Optional[GitHubRepoConfig]:
        """Get a repository configuration by label (case-insensitive)."""
        if not settings.github_tools_enabled:
            return None
        return settings.get_github_repo_by_label(label)

    def check_rate_limit(self, token: str) -> Optional[RateLimitInfo]:
        """Check the current rate limit status for a token."""
        token_hash = self._hash_token(token)
        return self._rate_limits.get(token_hash)

    def _update_rate_limit(self, token: str, response: httpx.Response) -> None:
        """Update rate limit info from response headers."""
        try:
            remaining = int(response.headers.get("X-RateLimit-Remaining", -1))
            limit = int(response.headers.get("X-RateLimit-Limit", -1))
            reset_ts = int(response.headers.get("X-RateLimit-Reset", 0))

            if remaining >= 0 and limit > 0:
                token_hash = self._hash_token(token)
                self._rate_limits[token_hash] = RateLimitInfo(
                    remaining=remaining,
                    limit=limit,
                    reset_timestamp=reset_ts,
                )

                # Log warning if rate limit is low
                if remaining < limit * 0.1:  # Less than 10%
                    logger.warning(
                        f"GitHub rate limit low: {remaining}/{limit} remaining, "
                        f"resets at {time.strftime('%H:%M:%S', time.localtime(reset_ts))}"
                    )
        except (ValueError, TypeError) as e:
            logger.debug(f"Could not parse rate limit headers: {e}")

    async def _request(
        self,
        method: str,
        endpoint: str,
        token: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        accept: str = "application/vnd.github.v3+json",
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Make a request to the GitHub API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            endpoint: API endpoint (without base URL)
            token: GitHub personal access token
            params: Query parameters
            json_data: JSON body data
            accept: Accept header value

        Returns:
            Tuple of (status_code, response_data)

        Raises:
            Exception on network errors
        """
        # Check if rate limited
        rate_info = self.check_rate_limit(token)
        if rate_info and rate_info.remaining == 0:
            if rate_info.reset_timestamp > int(time.time()):
                return 429, {
                    "error": "Rate limit exceeded",
                    "message": f"GitHub API rate limit exceeded. Resets at {rate_info.reset_time_formatted}",
                    "reset_timestamp": rate_info.reset_timestamp,
                }

        url = f"{GITHUB_API_BASE}{endpoint}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data,
            )

            # Update rate limit tracking
            self._update_rate_limit(token, response)

            # Parse response
            try:
                data = response.json()
            except Exception:
                data = {"raw": response.text}

            return response.status_code, data

    def is_binary_file(self, path: str, content: Optional[bytes] = None) -> bool:
        """
        Detect if a file is binary.

        Uses extension heuristics and content inspection.
        """
        # Check extension
        for ext in BINARY_EXTENSIONS:
            if path.lower().endswith(ext):
                return True

        # Check content for null bytes
        if content:
            # Check first 8KB for null bytes
            sample = content[:8192]
            if b'\x00' in sample:
                return True

        return False

    async def get_file_contents(
        self,
        repo: GitHubRepoConfig,
        path: str,
        ref: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Get file contents, handling large files via Git Data API.

        Returns:
            Tuple of (success, data)
            On success, data contains: content, encoding, size, sha, name, path
            For binary files: type, size, sha, download_url
            On failure, data contains: error, message
        """
        # First try Contents API
        endpoint = f"/repos/{repo.owner}/{repo.repo}/contents/{path}"
        params = {"ref": ref} if ref else {}

        status, data = await self._request("GET", endpoint, repo.token, params=params)

        if status == 404:
            return False, {"error": "not_found", "message": f"File not found: {path}"}

        if status == 403 and "too large" in str(data.get("message", "")).lower():
            # File too large for Contents API, use Git Data API
            return await self._get_large_file(repo, path, ref)

        if status != 200:
            return False, {
                "error": f"api_error_{status}",
                "message": data.get("message", f"GitHub API returned {status}"),
            }

        # Check if it's a file (not directory)
        if data.get("type") != "file":
            return False, {
                "error": "not_a_file",
                "message": f"Path is a {data.get('type', 'unknown')}, not a file",
            }

        # Check file size
        file_size = data.get("size", 0)
        if file_size > MAX_FILE_SIZE:
            return False, {
                "error": "file_too_large",
                "message": f"File is too large ({file_size} bytes, max {MAX_FILE_SIZE})",
            }

        # Decode content
        content_b64 = data.get("content", "")
        try:
            content_bytes = base64.b64decode(content_b64)
        except Exception as e:
            return False, {"error": "decode_error", "message": f"Failed to decode content: {e}"}

        # Check if binary
        if self.is_binary_file(path, content_bytes):
            return True, {
                "type": "binary",
                "size": file_size,
                "sha": data.get("sha"),
                "download_url": data.get("download_url"),
                "name": data.get("name"),
                "path": path,
            }

        # Decode as text
        try:
            content_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Might be binary after all
            return True, {
                "type": "binary",
                "size": file_size,
                "sha": data.get("sha"),
                "download_url": data.get("download_url"),
                "name": data.get("name"),
                "path": path,
            }

        return True, {
            "type": "text",
            "content": content_text,
            "encoding": "utf-8",
            "size": file_size,
            "sha": data.get("sha"),
            "name": data.get("name"),
            "path": path,
        }

    async def _get_large_file(
        self,
        repo: GitHubRepoConfig,
        path: str,
        ref: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Get large file using Git Data API (blob endpoint).

        This handles files between 1MB and 100MB.
        """
        # First, get the tree to find the blob SHA
        tree_ref = ref or "HEAD"
        endpoint = f"/repos/{repo.owner}/{repo.repo}/git/trees/{tree_ref}"
        params = {"recursive": "true"}

        status, data = await self._request("GET", endpoint, repo.token, params=params)

        if status != 200:
            return False, {
                "error": f"tree_error_{status}",
                "message": f"Failed to get repository tree: {data.get('message', status)}",
            }

        # Find the file in the tree
        blob_sha = None
        file_size = 0
        for item in data.get("tree", []):
            if item.get("path") == path and item.get("type") == "blob":
                blob_sha = item.get("sha")
                file_size = item.get("size", 0)
                break

        if not blob_sha:
            return False, {"error": "not_found", "message": f"File not found in tree: {path}"}

        if file_size > MAX_FILE_SIZE:
            return False, {
                "error": "file_too_large",
                "message": f"File is too large ({file_size} bytes, max {MAX_FILE_SIZE})",
            }

        # Get the blob
        endpoint = f"/repos/{repo.owner}/{repo.repo}/git/blobs/{blob_sha}"
        status, data = await self._request("GET", endpoint, repo.token)

        if status != 200:
            return False, {
                "error": f"blob_error_{status}",
                "message": f"Failed to get blob: {data.get('message', status)}",
            }

        # Decode content
        content_b64 = data.get("content", "")
        try:
            content_bytes = base64.b64decode(content_b64)
        except Exception as e:
            return False, {"error": "decode_error", "message": f"Failed to decode blob: {e}"}

        # Check if binary
        if self.is_binary_file(path, content_bytes):
            return True, {
                "type": "binary",
                "size": file_size,
                "sha": blob_sha,
                "name": path.split("/")[-1],
                "path": path,
            }

        # Decode as text
        try:
            content_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return True, {
                "type": "binary",
                "size": file_size,
                "sha": blob_sha,
                "name": path.split("/")[-1],
                "path": path,
            }

        return True, {
            "type": "text",
            "content": content_text,
            "encoding": "utf-8",
            "size": file_size,
            "sha": blob_sha,
            "name": path.split("/")[-1],
            "path": path,
        }

    async def get_repo_info(self, repo: GitHubRepoConfig) -> Tuple[bool, Dict[str, Any]]:
        """Get repository metadata."""
        endpoint = f"/repos/{repo.owner}/{repo.repo}"
        status, data = await self._request("GET", endpoint, repo.token)

        if status != 200:
            return False, {
                "error": f"api_error_{status}",
                "message": data.get("message", f"GitHub API returned {status}"),
            }

        return True, {
            "name": data.get("name"),
            "full_name": data.get("full_name"),
            "description": data.get("description"),
            "default_branch": data.get("default_branch"),
            "visibility": data.get("visibility", "private" if data.get("private") else "public"),
            "stars": data.get("stargazers_count", 0),
            "open_issues": data.get("open_issues_count", 0),
            "forks": data.get("forks_count", 0),
            "language": data.get("language"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "html_url": data.get("html_url"),
        }

    async def list_contents(
        self,
        repo: GitHubRepoConfig,
        path: str = "",
        ref: Optional[str] = None,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """List files and directories at a path."""
        endpoint = f"/repos/{repo.owner}/{repo.repo}/contents/{path}"
        params = {"ref": ref} if ref else {}

        status, data = await self._request("GET", endpoint, repo.token, params=params)

        if status == 404:
            return False, [{"error": "not_found", "message": f"Path not found: {path or '/'}"}]

        if status != 200:
            return False, [{
                "error": f"api_error_{status}",
                "message": data.get("message", f"GitHub API returned {status}"),
            }]

        # Handle single file response
        if isinstance(data, dict):
            if data.get("type") == "file":
                return True, [{
                    "type": "file",
                    "name": data.get("name"),
                    "path": data.get("path"),
                    "size": data.get("size"),
                    "sha": data.get("sha"),
                }]
            else:
                return False, [{"error": "unexpected", "message": "Unexpected response format"}]

        # Parse directory listing
        items = []
        for item in data:
            items.append({
                "type": item.get("type"),  # "file" or "dir"
                "name": item.get("name"),
                "path": item.get("path"),
                "size": item.get("size") if item.get("type") == "file" else None,
                "sha": item.get("sha"),
            })

        # Sort: directories first, then files, alphabetically
        items.sort(key=lambda x: (0 if x["type"] == "dir" else 1, x["name"].lower()))
        return True, items

    async def list_branches(self, repo: GitHubRepoConfig) -> Tuple[bool, List[Dict[str, Any]]]:
        """List all branches in the repository."""
        endpoint = f"/repos/{repo.owner}/{repo.repo}/branches"
        status, data = await self._request("GET", endpoint, repo.token, params={"per_page": 100})

        if status != 200:
            return False, [{
                "error": f"api_error_{status}",
                "message": data.get("message", f"GitHub API returned {status}"),
            }]

        # Get default branch
        success, repo_info = await self.get_repo_info(repo)
        default_branch = repo_info.get("default_branch", "main") if success else "main"

        branches = []
        for branch in data:
            branches.append({
                "name": branch.get("name"),
                "sha": branch.get("commit", {}).get("sha"),
                "is_default": branch.get("name") == default_branch,
                "protected": branch.get("protected", False),
            })

        return True, branches

    async def create_branch(
        self,
        repo: GitHubRepoConfig,
        branch_name: str,
        from_ref: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Create a new branch from a reference."""
        # Get the SHA of the source reference
        if from_ref:
            endpoint = f"/repos/{repo.owner}/{repo.repo}/git/ref/heads/{from_ref}"
        else:
            # Get default branch
            success, repo_info = await self.get_repo_info(repo)
            if not success:
                return False, repo_info
            default_branch = repo_info.get("default_branch", "main")
            endpoint = f"/repos/{repo.owner}/{repo.repo}/git/ref/heads/{default_branch}"

        status, data = await self._request("GET", endpoint, repo.token)

        if status != 200:
            return False, {
                "error": f"ref_error_{status}",
                "message": data.get("message", f"Failed to get source reference: {status}"),
            }

        source_sha = data.get("object", {}).get("sha")
        if not source_sha:
            return False, {"error": "no_sha", "message": "Could not get SHA of source reference"}

        # Create the new branch
        endpoint = f"/repos/{repo.owner}/{repo.repo}/git/refs"
        create_data = {
            "ref": f"refs/heads/{branch_name}",
            "sha": source_sha,
        }

        status, data = await self._request("POST", endpoint, repo.token, json_data=create_data)

        if status == 422:
            return False, {
                "error": "already_exists",
                "message": f"Branch '{branch_name}' already exists",
            }

        if status != 201:
            return False, {
                "error": f"create_error_{status}",
                "message": data.get("message", f"Failed to create branch: {status}"),
            }

        return True, {
            "name": branch_name,
            "sha": source_sha,
            "ref": f"refs/heads/{branch_name}",
        }

    async def commit_file(
        self,
        repo: GitHubRepoConfig,
        path: str,
        content: str,
        message: str,
        branch: str,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Create or update a file with a commit."""
        # Check protected branch
        if branch in repo.protected_branches:
            return False, {
                "error": "protected_branch",
                "message": f"Cannot commit directly to protected branch '{branch}'. Create a feature branch first.",
            }

        # Get existing file SHA if it exists
        endpoint = f"/repos/{repo.owner}/{repo.repo}/contents/{path}"
        params = {"ref": branch}
        status, existing = await self._request("GET", endpoint, repo.token, params=params)

        file_sha = None
        if status == 200 and existing.get("type") == "file":
            file_sha = existing.get("sha")

        # Create/update the file
        commit_data = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if file_sha:
            commit_data["sha"] = file_sha

        status, data = await self._request("PUT", endpoint, repo.token, json_data=commit_data)

        if status not in (200, 201):
            return False, {
                "error": f"commit_error_{status}",
                "message": data.get("message", f"Failed to commit: {status}"),
            }

        commit_sha = data.get("commit", {}).get("sha")
        html_url = data.get("content", {}).get("html_url")

        return True, {
            "sha": commit_sha,
            "path": path,
            "branch": branch,
            "html_url": html_url,
            "action": "updated" if file_sha else "created",
        }

    async def delete_file(
        self,
        repo: GitHubRepoConfig,
        path: str,
        message: str,
        branch: str,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Delete a file with a commit."""
        # Check protected branch
        if branch in repo.protected_branches:
            return False, {
                "error": "protected_branch",
                "message": f"Cannot commit directly to protected branch '{branch}'. Create a feature branch first.",
            }

        # Get existing file SHA
        endpoint = f"/repos/{repo.owner}/{repo.repo}/contents/{path}"
        params = {"ref": branch}
        status, existing = await self._request("GET", endpoint, repo.token, params=params)

        if status == 404:
            return False, {"error": "not_found", "message": f"File not found: {path}"}

        if status != 200:
            return False, {
                "error": f"api_error_{status}",
                "message": existing.get("message", f"GitHub API returned {status}"),
            }

        file_sha = existing.get("sha")
        if not file_sha:
            return False, {"error": "no_sha", "message": "Could not get file SHA"}

        # Delete the file
        delete_data = {
            "message": message,
            "sha": file_sha,
            "branch": branch,
        }

        status, data = await self._request("DELETE", endpoint, repo.token, json_data=delete_data)

        if status != 200:
            return False, {
                "error": f"delete_error_{status}",
                "message": data.get("message", f"Failed to delete: {status}"),
            }

        return True, {
            "sha": data.get("commit", {}).get("sha"),
            "path": path,
            "branch": branch,
        }

    async def search_code(
        self,
        repo: GitHubRepoConfig,
        query: str,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """Search for code in the repository."""
        endpoint = "/search/code"
        search_query = f"{query} repo:{repo.owner}/{repo.repo}"
        params = {"q": search_query, "per_page": 30}

        status, data = await self._request("GET", endpoint, repo.token)

        if status == 422:
            return False, [{
                "error": "validation_error",
                "message": data.get("message", "Invalid search query"),
            }]

        if status != 200:
            return False, [{
                "error": f"api_error_{status}",
                "message": data.get("message", f"GitHub API returned {status}"),
            }]

        results = []
        for item in data.get("items", []):
            results.append({
                "path": item.get("path"),
                "name": item.get("name"),
                "sha": item.get("sha"),
                "html_url": item.get("html_url"),
                "score": item.get("score"),
            })

        return True, results

    async def list_pull_requests(
        self,
        repo: GitHubRepoConfig,
        state: str = "open",
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """List pull requests."""
        endpoint = f"/repos/{repo.owner}/{repo.repo}/pulls"
        params = {"state": state, "per_page": 30}

        status, data = await self._request("GET", endpoint, repo.token, params=params)

        if status != 200:
            return False, [{
                "error": f"api_error_{status}",
                "message": data.get("message", f"GitHub API returned {status}"),
            }]

        prs = []
        for pr in data:
            prs.append({
                "number": pr.get("number"),
                "title": pr.get("title"),
                "state": pr.get("state"),
                "author": pr.get("user", {}).get("login"),
                "head": pr.get("head", {}).get("ref"),
                "base": pr.get("base", {}).get("ref"),
                "created_at": pr.get("created_at"),
                "updated_at": pr.get("updated_at"),
                "html_url": pr.get("html_url"),
                "draft": pr.get("draft", False),
            })

        return True, prs

    async def get_pull_request(
        self,
        repo: GitHubRepoConfig,
        pr_number: int,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Get pull request details including diff."""
        endpoint = f"/repos/{repo.owner}/{repo.repo}/pulls/{pr_number}"

        status, data = await self._request("GET", endpoint, repo.token)

        if status == 404:
            return False, {"error": "not_found", "message": f"Pull request #{pr_number} not found"}

        if status != 200:
            return False, {
                "error": f"api_error_{status}",
                "message": data.get("message", f"GitHub API returned {status}"),
            }

        # Get diff
        diff_status, diff_data = await self._request(
            "GET", endpoint, repo.token,
            accept="application/vnd.github.v3.diff"
        )

        diff = None
        if diff_status == 200 and isinstance(diff_data, dict):
            diff = diff_data.get("raw", "")
        elif diff_status == 200:
            diff = str(diff_data)

        # Summarize if diff is too large
        if diff and len(diff) > 50000:
            diff = diff[:50000] + "\n\n... [diff truncated, too large to display] ..."

        return True, {
            "number": data.get("number"),
            "title": data.get("title"),
            "body": data.get("body"),
            "state": data.get("state"),
            "author": data.get("user", {}).get("login"),
            "head": data.get("head", {}).get("ref"),
            "base": data.get("base", {}).get("ref"),
            "mergeable": data.get("mergeable"),
            "merged": data.get("merged", False),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "html_url": data.get("html_url"),
            "additions": data.get("additions"),
            "deletions": data.get("deletions"),
            "changed_files": data.get("changed_files"),
            "diff": diff,
            "reviewers": [r.get("login") for r in data.get("requested_reviewers", [])],
        }

    async def create_pull_request(
        self,
        repo: GitHubRepoConfig,
        title: str,
        body: str,
        head: str,
        base: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Create a pull request."""
        # Get default branch if base not specified
        if not base:
            success, repo_info = await self.get_repo_info(repo)
            if not success:
                return False, repo_info
            base = repo_info.get("default_branch", "main")

        # Check for PR template
        template_content = None
        for template_path in [".github/PULL_REQUEST_TEMPLATE.md", "PULL_REQUEST_TEMPLATE.md"]:
            success, template_data = await self.get_file_contents(repo, template_path)
            if success and template_data.get("type") == "text":
                template_content = template_data.get("content", "")
                break

        # Combine template with body
        if template_content:
            if body:
                body = f"{body}\n\n---\n\n{template_content}"
            else:
                body = template_content

        endpoint = f"/repos/{repo.owner}/{repo.repo}/pulls"
        pr_data = {
            "title": title,
            "body": body or "",
            "head": head,
            "base": base,
        }

        status, data = await self._request("POST", endpoint, repo.token, json_data=pr_data)

        if status == 422:
            return False, {
                "error": "validation_error",
                "message": data.get("message", "Failed to create PR - check if branch exists and has commits"),
            }

        if status != 201:
            return False, {
                "error": f"create_error_{status}",
                "message": data.get("message", f"Failed to create PR: {status}"),
            }

        return True, {
            "number": data.get("number"),
            "title": data.get("title"),
            "html_url": data.get("html_url"),
            "head": head,
            "base": base,
        }

    async def list_issues(
        self,
        repo: GitHubRepoConfig,
        state: str = "open",
        labels: Optional[str] = None,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """List issues (excluding pull requests)."""
        endpoint = f"/repos/{repo.owner}/{repo.repo}/issues"
        params = {"state": state, "per_page": 30}
        if labels:
            params["labels"] = labels

        status, data = await self._request("GET", endpoint, repo.token, params=params)

        if status != 200:
            return False, [{
                "error": f"api_error_{status}",
                "message": data.get("message", f"GitHub API returned {status}"),
            }]

        issues = []
        for issue in data:
            # Skip pull requests (they also appear in issues endpoint)
            if issue.get("pull_request"):
                continue

            issues.append({
                "number": issue.get("number"),
                "title": issue.get("title"),
                "state": issue.get("state"),
                "author": issue.get("user", {}).get("login"),
                "labels": [l.get("name") for l in issue.get("labels", [])],
                "created_at": issue.get("created_at"),
                "updated_at": issue.get("updated_at"),
                "html_url": issue.get("html_url"),
                "comments": issue.get("comments", 0),
            })

        return True, issues

    async def get_issue(
        self,
        repo: GitHubRepoConfig,
        issue_number: int,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Get issue details including comments."""
        endpoint = f"/repos/{repo.owner}/{repo.repo}/issues/{issue_number}"

        status, data = await self._request("GET", endpoint, repo.token)

        if status == 404:
            return False, {"error": "not_found", "message": f"Issue #{issue_number} not found"}

        if status != 200:
            return False, {
                "error": f"api_error_{status}",
                "message": data.get("message", f"GitHub API returned {status}"),
            }

        # Get comments
        comments_endpoint = f"/repos/{repo.owner}/{repo.repo}/issues/{issue_number}/comments"
        comments_status, comments_data = await self._request(
            "GET", comments_endpoint, repo.token, params={"per_page": 30}
        )

        comments = []
        if comments_status == 200 and isinstance(comments_data, list):
            for comment in comments_data:
                comments.append({
                    "id": comment.get("id"),
                    "author": comment.get("user", {}).get("login"),
                    "body": comment.get("body"),
                    "created_at": comment.get("created_at"),
                })

        return True, {
            "number": data.get("number"),
            "title": data.get("title"),
            "body": data.get("body"),
            "state": data.get("state"),
            "author": data.get("user", {}).get("login"),
            "labels": [l.get("name") for l in data.get("labels", [])],
            "assignees": [a.get("login") for a in data.get("assignees", [])],
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "html_url": data.get("html_url"),
            "comments": comments,
        }

    async def create_issue(
        self,
        repo: GitHubRepoConfig,
        title: str,
        body: str,
        labels: Optional[List[str]] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Create an issue."""
        # Check for issue templates
        template_info = None
        template_paths = [
            ".github/ISSUE_TEMPLATE.md",
            "ISSUE_TEMPLATE.md",
        ]

        for path in template_paths:
            success, data = await self.get_file_contents(repo, path)
            if success:
                template_info = f"Issue template found at {path}"
                break

        # Check for template directory
        if not template_info:
            success, items = await self.list_contents(repo, ".github/ISSUE_TEMPLATE")
            if success and items:
                template_names = [i.get("name") for i in items if i.get("type") == "file"]
                if template_names:
                    template_info = f"Issue templates available: {', '.join(template_names)}"

        endpoint = f"/repos/{repo.owner}/{repo.repo}/issues"
        issue_data = {
            "title": title,
            "body": body or "",
        }
        if labels:
            issue_data["labels"] = labels

        status, data = await self._request("POST", endpoint, repo.token, json_data=issue_data)

        if status != 201:
            return False, {
                "error": f"create_error_{status}",
                "message": data.get("message", f"Failed to create issue: {status}"),
            }

        result = {
            "number": data.get("number"),
            "title": data.get("title"),
            "html_url": data.get("html_url"),
        }

        if template_info and not body:
            result["template_info"] = template_info

        return True, result

    async def add_comment(
        self,
        repo: GitHubRepoConfig,
        number: int,
        body: str,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Add a comment to an issue or pull request."""
        endpoint = f"/repos/{repo.owner}/{repo.repo}/issues/{number}/comments"
        comment_data = {"body": body}

        status, data = await self._request("POST", endpoint, repo.token, json_data=comment_data)

        if status == 404:
            return False, {"error": "not_found", "message": f"Issue/PR #{number} not found"}

        if status != 201:
            return False, {
                "error": f"comment_error_{status}",
                "message": data.get("message", f"Failed to add comment: {status}"),
            }

        return True, {
            "id": data.get("id"),
            "html_url": data.get("html_url"),
            "issue_number": number,
        }


# Singleton instance
github_service = GitHubService()
