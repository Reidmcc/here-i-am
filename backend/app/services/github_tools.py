"""
GitHub tools for AI entities.

Provides repository operations that AI entities can use during conversations
to read code, create branches, commit changes, and manage PRs/issues.
"""

import logging
from typing import List, Optional, TYPE_CHECKING

from app.config import settings
from app.services.github_service import github_service
from app.services.tool_service import ToolCategory, ToolService

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _format_available_repos() -> str:
    """
    Format the list of available repositories for error messages.

    Shows both the label and the owner/repo path to help the AI
    understand the repository configuration.

    Returns:
        Formatted string like: "Label1" (owner1/repo1), "Label2" (owner2/repo2)
    """
    repos = github_service.get_repos()
    if not repos:
        return ""
    return ", ".join(f'"{r.label}" ({r.owner}/{r.repo})' for r in repos)


def _repo_not_found_error(repo_label: str) -> str:
    """
    Generate a helpful error message when a repository is not found.

    Args:
        repo_label: The label that was searched for

    Returns:
        Error message with available repositories listed
    """
    available = _format_available_repos()
    if available:
        return f"Error: Repository '{repo_label}' not found. Available repositories: {available}"
    return f"Error: Repository '{repo_label}' not found. No repositories are configured."


# =============================================================================
# Read Tools (capability: "read")
# =============================================================================

async def github_repo_info(repo_label: str) -> str:
    """
    Get repository metadata including description, default branch, visibility.

    Args:
        repo_label: The label of the configured repository

    Returns:
        Formatted repository information or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("read"):
        return f"Error: Read capability is not enabled for repository '{repo_label}'."

    try:
        success, data = await github_service.get_repo_info(repo)

        if not success:
            return f"Error: {data.get('message', 'Failed to get repository info')}"

        lines = [
            f"Repository: {data.get('full_name')}",
            f"Description: {data.get('description') or 'No description'}",
            f"Default Branch: {data.get('default_branch')}",
            f"Visibility: {data.get('visibility')}",
            f"Language: {data.get('language') or 'Not specified'}",
            f"Stars: {data.get('stars'):,}",
            f"Open Issues: {data.get('open_issues'):,}",
            f"Forks: {data.get('forks'):,}",
            f"Created: {data.get('created_at')}",
            f"Last Updated: {data.get('updated_at')}",
            f"URL: {data.get('html_url')}",
        ]
        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error getting repo info: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_list_contents(
    repo_label: str,
    path: str = "",
    ref: Optional[str] = None,
) -> str:
    """
    List files and directories at a path in the repository.

    Args:
        repo_label: The label of the configured repository
        path: Path within the repository (empty for root)
        ref: Git reference (branch, tag, commit SHA). Defaults to default branch.

    Returns:
        Formatted directory listing or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("read"):
        return f"Error: Read capability is not enabled for repository '{repo_label}'."

    try:
        # Try local clone first if available and no specific ref requested
        source = "GitHub API"
        if not ref and github_service.has_local_clone(repo):
            logger.info(f"[{repo_label}] Reading directory '{path or '/'}' from LOCAL CLONE at {repo.local_clone_path}")
            success, items = github_service.list_contents_local(repo, path)
            source = "local clone"
        else:
            if ref:
                logger.info(f"[{repo_label}] Reading directory '{path or '/'}' from GitHub API (ref={ref} specified)")
            else:
                logger.info(f"[{repo_label}] Reading directory '{path or '/'}' from GitHub API (no local clone)")
            success, items = await github_service.list_contents(repo, path, ref)

        if not success:
            if items and items[0].get("error"):
                return f"Error: {items[0].get('message', 'Failed to list contents')}"
            return "Error: Failed to list contents"

        if not items:
            return f"Directory '{path or '/'}' is empty."

        header = f"Contents of {path or '/'}"
        if ref:
            header += f" (ref: {ref})"
        header += f" [source: {source}]:"
        lines = [header]
        lines.append("")

        for item in items:
            if item.get("type") == "dir":
                lines.append(f"📁 {item.get('name')}/")
            else:
                size = item.get("size", 0) or 0
                if size >= 1024 * 1024:
                    size_str = f"{size / (1024 * 1024):.1f}MB"
                elif size >= 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size}B"
                lines.append(f"📄 {item.get('name')} ({size_str})")

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error listing contents: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_get_file(
    repo_label: str,
    path: str,
    ref: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:
    """
    Get file contents from the repository.

    Args:
        repo_label: The label of the configured repository
        path: Path to the file
        ref: Git reference (branch, tag, commit SHA). Defaults to default branch.
        start_line: Optional starting line number (1-indexed, inclusive)
        end_line: Optional ending line number (1-indexed, inclusive)

    Returns:
        File contents or error message. For binary files, returns metadata only.
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("read"):
        return f"Error: Read capability is not enabled for repository '{repo_label}'."

    try:
        # Try local clone first if available and no specific ref requested
        source = "GitHub API"
        if not ref and github_service.has_local_clone(repo):
            logger.info(f"[{repo_label}] Reading file '{path}' from LOCAL CLONE at {repo.local_clone_path}")
            success, data = github_service.get_file_contents_local(repo, path)
            source = "local clone"
        else:
            if ref:
                logger.info(f"[{repo_label}] Reading file '{path}' from GitHub API (ref={ref} specified)")
            else:
                logger.info(f"[{repo_label}] Reading file '{path}' from GitHub API (no local clone)")
            success, data = await github_service.get_file_contents(repo, path, ref)

        if not success:
            return f"Error: {data.get('message', 'Failed to get file')}"

        if data.get("type") == "binary":
            lines = [
                f"Binary file: {data.get('name')}",
                f"Size: {data.get('size'):,} bytes",
            ]
            if data.get("sha"):
                lines.append(f"SHA: {data.get('sha')}")
            if data.get("download_url"):
                lines.append(f"Download URL: {data.get('download_url')}")
            lines.append(f"[source: {source}]")
            return "\n".join(lines)

        content = data.get("content", "")

        # Apply line range if specified
        if start_line is not None or end_line is not None:
            lines = content.split("\n")
            start_idx = (start_line - 1) if start_line else 0
            end_idx = end_line if end_line else len(lines)

            # Clamp to valid range
            start_idx = max(0, min(start_idx, len(lines)))
            end_idx = max(start_idx, min(end_idx, len(lines)))

            selected_lines = lines[start_idx:end_idx]

            header = f"File: {path}"
            if ref:
                header += f" (ref: {ref})"
            header += f" [lines {start_idx + 1}-{end_idx} of {len(lines)}] [source: {source}]"

            # Add line numbers
            numbered_lines = [
                f"{i + start_idx + 1:4d} | {line}"
                for i, line in enumerate(selected_lines)
            ]

            return f"{header}\n\n" + "\n".join(numbered_lines)

        # Return full content with header
        header = f"File: {path}"
        if ref:
            header += f" (ref: {ref})"
        header += f" ({data.get('size'):,} bytes) [source: {source}]"

        return f"{header}\n\n{content}"

    except Exception as e:
        logger.exception(f"Error getting file: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_search_code(repo_label: str, query: str) -> str:
    """
    Search for code in the repository.

    Args:
        repo_label: The label of the configured repository
        query: Search query (supports GitHub code search syntax)

    Returns:
        Search results with file paths and snippets, or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("read"):
        return f"Error: Read capability is not enabled for repository '{repo_label}'."

    try:
        success, results = await github_service.search_code(repo, query)

        if not success:
            if results and results[0].get("error"):
                return f"Error: {results[0].get('message', 'Search failed')}"
            return "Error: Search failed"

        if not results:
            return f"No results found for query: {query}"

        lines = [f"Search results for '{query}' ({len(results)} matches):"]
        lines.append("")

        for i, result in enumerate(results, 1):
            lines.append(f"{i}. {result.get('path')}")
            if result.get("html_url"):
                lines.append(f"   URL: {result.get('html_url')}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error searching code: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_list_branches(repo_label: str) -> str:
    """
    List all branches in the repository.

    Args:
        repo_label: The label of the configured repository

    Returns:
        Formatted list of branches with their latest commit SHA
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("read"):
        return f"Error: Read capability is not enabled for repository '{repo_label}'."

    try:
        success, branches = await github_service.list_branches(repo)

        if not success:
            if branches and branches[0].get("error"):
                return f"Error: {branches[0].get('message', 'Failed to list branches')}"
            return "Error: Failed to list branches"

        lines = [f"Branches in {repo.owner}/{repo.repo}:"]
        lines.append("")

        for branch in branches:
            name = branch.get("name")
            sha = branch.get("sha", "")[:7]
            markers = []
            if branch.get("is_default"):
                markers.append("default")
            if branch.get("protected"):
                markers.append("protected")

            marker_str = f" ({', '.join(markers)})" if markers else ""
            lines.append(f"• {name} [{sha}]{marker_str}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error listing branches: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


# =============================================================================
# Branch Tools (capability: "branch")
# =============================================================================

async def github_create_branch(
    repo_label: str,
    branch_name: str,
    from_ref: Optional[str] = None,
) -> str:
    """
    Create a new branch in the repository.

    Args:
        repo_label: The label of the configured repository
        branch_name: Name for the new branch
        from_ref: Source branch/ref to create from (defaults to default branch)

    Returns:
        Success message with branch details or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("branch"):
        return f"Error: Branch capability is not enabled for repository '{repo_label}'."

    try:
        success, data = await github_service.create_branch(repo, branch_name, from_ref)

        if not success:
            return f"Error: {data.get('message', 'Failed to create branch')}"

        source = from_ref or "default branch"
        return f"Created branch '{branch_name}' from {source}\nSHA: {data.get('sha')}"

    except Exception as e:
        logger.exception(f"Error creating branch: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


# =============================================================================
# Commit Tools (capability: "commit")
# =============================================================================

async def github_commit_file(
    repo_label: str,
    path: str,
    content: str,
    message: str,
    branch: str,
) -> str:
    """
    Create or update a file with a commit.

    Args:
        repo_label: The label of the configured repository
        path: File path within the repository
        content: File content to commit
        message: Commit message
        branch: Target branch (required, cannot be a protected branch)

    Returns:
        Success message with commit SHA and URL, or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("commit"):
        return f"Error: Commit capability is not enabled for repository '{repo_label}'."

    try:
        success, data = await github_service.commit_file(repo, path, content, message, branch)

        if not success:
            return f"Error: {data.get('message', 'Failed to commit file')}"

        action = data.get("action", "committed")
        lines = [
            f"Successfully {action} file: {path}",
            f"Branch: {branch}",
            f"Commit SHA: {data.get('sha')}",
        ]
        if data.get("html_url"):
            lines.append(f"View on GitHub: {data.get('html_url')}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error committing file: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_delete_file(
    repo_label: str,
    path: str,
    message: str,
    branch: str,
) -> str:
    """
    Delete a file with a commit.

    Args:
        repo_label: The label of the configured repository
        path: File path to delete
        message: Commit message
        branch: Target branch (required, cannot be a protected branch)

    Returns:
        Success message with commit SHA, or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("commit"):
        return f"Error: Commit capability is not enabled for repository '{repo_label}'."

    try:
        success, data = await github_service.delete_file(repo, path, message, branch)

        if not success:
            return f"Error: {data.get('message', 'Failed to delete file')}"

        return f"Successfully deleted file: {path}\nBranch: {branch}\nCommit SHA: {data.get('sha')}"

    except Exception as e:
        logger.exception(f"Error deleting file: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


# =============================================================================
# Pull Request Tools (capability: "pr")
# =============================================================================

async def github_list_pull_requests(
    repo_label: str,
    state: str = "open",
) -> str:
    """
    List pull requests in the repository.

    Args:
        repo_label: The label of the configured repository
        state: PR state filter: "open", "closed", or "all"

    Returns:
        Formatted list of pull requests or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("pr"):
        return f"Error: PR capability is not enabled for repository '{repo_label}'."

    try:
        success, prs = await github_service.list_pull_requests(repo, state)

        if not success:
            if prs and prs[0].get("error"):
                return f"Error: {prs[0].get('message', 'Failed to list PRs')}"
            return "Error: Failed to list pull requests"

        if not prs:
            return f"No {state} pull requests found."

        lines = [f"Pull requests ({state}):"]
        lines.append("")

        for pr in prs:
            draft = " [DRAFT]" if pr.get("draft") else ""
            lines.append(f"#{pr.get('number')}: {pr.get('title')}{draft}")
            lines.append(f"   Author: {pr.get('author')} | {pr.get('head')} → {pr.get('base')}")
            lines.append(f"   URL: {pr.get('html_url')}")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error listing PRs: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_get_pull_request(
    repo_label: str,
    pr_number: int,
) -> str:
    """
    Get pull request details including the diff.

    Args:
        repo_label: The label of the configured repository
        pr_number: Pull request number

    Returns:
        PR details with title, body, state, and diff, or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("pr"):
        return f"Error: PR capability is not enabled for repository '{repo_label}'."

    try:
        success, data = await github_service.get_pull_request(repo, pr_number)

        if not success:
            return f"Error: {data.get('message', 'Failed to get PR')}"

        lines = [
            f"Pull Request #{data.get('number')}: {data.get('title')}",
            f"State: {data.get('state')}" + (" (merged)" if data.get("merged") else ""),
            f"Author: {data.get('author')}",
            f"Branch: {data.get('head')} → {data.get('base')}",
            f"Changes: +{data.get('additions', 0)} -{data.get('deletions', 0)} in {data.get('changed_files', 0)} files",
            f"URL: {data.get('html_url')}",
        ]

        if data.get("reviewers"):
            lines.append(f"Reviewers: {', '.join(data.get('reviewers'))}")

        lines.append("")
        lines.append("--- Description ---")
        lines.append(data.get("body") or "(No description)")

        if data.get("diff"):
            lines.append("")
            lines.append("--- Diff ---")
            lines.append(data.get("diff"))

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error getting PR: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_create_pull_request(
    repo_label: str,
    title: str,
    body: str,
    head: str,
    base: Optional[str] = None,
) -> str:
    """
    Create a pull request.

    Args:
        repo_label: The label of the configured repository
        title: PR title
        body: PR description (will be combined with any PR template)
        head: Source branch
        base: Target branch (defaults to repository's default branch)

    Returns:
        Success message with PR number and URL, or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("pr"):
        return f"Error: PR capability is not enabled for repository '{repo_label}'."

    try:
        success, data = await github_service.create_pull_request(repo, title, body, head, base)

        if not success:
            return f"Error: {data.get('message', 'Failed to create PR')}"

        lines = [
            f"Created Pull Request #{data.get('number')}: {data.get('title', title)}",
            f"Branch: {data.get('head')} → {data.get('base')}",
            f"URL: {data.get('html_url')}",
        ]

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error creating PR: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


# =============================================================================
# Issue Tools (capability: "issue")
# =============================================================================

async def github_list_issues(
    repo_label: str,
    state: str = "open",
    labels: Optional[str] = None,
) -> str:
    """
    List issues in the repository (excluding pull requests).

    Args:
        repo_label: The label of the configured repository
        state: Issue state filter: "open", "closed", or "all"
        labels: Optional comma-separated label filter

    Returns:
        Formatted list of issues or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("issue"):
        return f"Error: Issue capability is not enabled for repository '{repo_label}'."

    try:
        success, issues = await github_service.list_issues(repo, state, labels)

        if not success:
            if issues and issues[0].get("error"):
                return f"Error: {issues[0].get('message', 'Failed to list issues')}"
            return "Error: Failed to list issues"

        if not issues:
            filter_desc = f" with labels '{labels}'" if labels else ""
            return f"No {state} issues found{filter_desc}."

        lines = [f"Issues ({state}):"]
        lines.append("")

        for issue in issues:
            label_str = ""
            if issue.get("labels"):
                label_str = f" [{', '.join(issue.get('labels'))}]"

            lines.append(f"#{issue.get('number')}: {issue.get('title')}{label_str}")
            lines.append(f"   Author: {issue.get('author')} | Comments: {issue.get('comments', 0)}")
            lines.append(f"   URL: {issue.get('html_url')}")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error listing issues: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_get_issue(
    repo_label: str,
    issue_number: int,
) -> str:
    """
    Get issue details including comments.

    Args:
        repo_label: The label of the configured repository
        issue_number: Issue number

    Returns:
        Issue details with title, body, state, labels, and comments
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("issue"):
        return f"Error: Issue capability is not enabled for repository '{repo_label}'."

    try:
        success, data = await github_service.get_issue(repo, issue_number)

        if not success:
            return f"Error: {data.get('message', 'Failed to get issue')}"

        lines = [
            f"Issue #{data.get('number')}: {data.get('title')}",
            f"State: {data.get('state')}",
            f"Author: {data.get('author')}",
            f"URL: {data.get('html_url')}",
        ]

        if data.get("labels"):
            lines.append(f"Labels: {', '.join(data.get('labels'))}")

        if data.get("assignees"):
            lines.append(f"Assignees: {', '.join(data.get('assignees'))}")

        lines.append("")
        lines.append("--- Description ---")
        lines.append(data.get("body") or "(No description)")

        comments = data.get("comments", [])
        if comments:
            lines.append("")
            lines.append(f"--- Comments ({len(comments)}) ---")
            for comment in comments:
                lines.append(f"\n[{comment.get('author')} at {comment.get('created_at')}]")
                lines.append(comment.get("body", ""))

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error getting issue: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_create_issue(
    repo_label: str,
    title: str,
    body: str,
    labels: Optional[List[str]] = None,
) -> str:
    """
    Create an issue in the repository.

    Args:
        repo_label: The label of the configured repository
        title: Issue title
        body: Issue body/description
        labels: Optional list of labels to apply

    Returns:
        Success message with issue number and URL, or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("issue"):
        return f"Error: Issue capability is not enabled for repository '{repo_label}'."

    try:
        success, data = await github_service.create_issue(repo, title, body, labels)

        if not success:
            return f"Error: {data.get('message', 'Failed to create issue')}"

        lines = [
            f"Created Issue #{data.get('number')}: {data.get('title', title)}",
            f"URL: {data.get('html_url')}",
        ]

        if data.get("template_info"):
            lines.append(f"Note: {data.get('template_info')}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error creating issue: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_add_comment(
    repo_label: str,
    number: int,
    body: str,
) -> str:
    """
    Add a comment to an issue or pull request.

    Args:
        repo_label: The label of the configured repository
        number: Issue or PR number
        body: Comment text

    Returns:
        Success message with comment URL, or error message
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    # Allow comment if either issue or PR capability is enabled
    if not repo.has_capability("issue") and not repo.has_capability("pr"):
        return f"Error: Neither issue nor PR capability is enabled for repository '{repo_label}'."

    try:
        success, data = await github_service.add_comment(repo, number, body)

        if not success:
            return f"Error: {data.get('message', 'Failed to add comment')}"

        return f"Added comment to #{number}\nURL: {data.get('html_url')}"

    except Exception as e:
        logger.exception(f"Error adding comment: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


# =============================================================================
# Tool Registration
# =============================================================================

def register_github_tools(tool_service: ToolService) -> None:
    """
    Register GitHub tools with the tool service.

    Only registers tools whose capabilities are enabled for at least one configured repo.
    """
    if not settings.github_tools_enabled:
        logger.info("GitHub tools disabled (GITHUB_TOOLS_ENABLED=false)")
        return

    repos = github_service.get_repos()
    if not repos:
        logger.info("No GitHub repositories configured, skipping tool registration")
        return

    # Determine which capabilities are available across all repos
    all_capabilities = set()
    for repo in repos:
        all_capabilities.update(repo.capabilities)

    logger.info(f"Registering GitHub tools for capabilities: {all_capabilities}")

    # Build a description for repo_label that shows available repos upfront
    # Format: "Label" (owner/repo) for each configured repository
    repo_list = ", ".join(f'"{r.label}" ({r.owner}/{r.repo})' for r in repos)
    repo_label_description = f"The label of the configured repository. Available: {repo_list}"

    # Read tools
    if "read" in all_capabilities:
        tool_service.register_tool(
            name="github_repo_info",
            description=(
                "Get information about a GitHub repository including description, "
                "default branch, visibility, stars, and other metadata."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                },
                "required": ["repo_label"],
            },
            executor=github_repo_info,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_list_contents",
            description=(
                "List files and directories at a path in a GitHub repository. "
                "Use an empty path to list the root directory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "path": {
                        "type": "string",
                        "description": "Path within the repository (empty for root)",
                        "default": "",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git reference (branch, tag, SHA). Defaults to default branch.",
                    },
                },
                "required": ["repo_label"],
            },
            executor=github_list_contents,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_get_file",
            description=(
                "Get the contents of a file from a GitHub repository. "
                "For text files, returns the content. For binary files, returns metadata. "
                "Supports optional line range for reading specific portions."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to the file within the repository",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git reference (branch, tag, SHA). Defaults to default branch.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Starting line number (1-indexed, inclusive)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Ending line number (1-indexed, inclusive)",
                    },
                },
                "required": ["repo_label", "path"],
            },
            executor=github_get_file,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_search_code",
            description=(
                "Search for code in a GitHub repository. "
                "Supports GitHub code search syntax for advanced queries."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (supports GitHub code search syntax)",
                    },
                },
                "required": ["repo_label", "query"],
            },
            executor=github_search_code,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_list_branches",
            description="List all branches in a GitHub repository with their latest commit SHA.",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                },
                "required": ["repo_label"],
            },
            executor=github_list_branches,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

    # Branch tools
    if "branch" in all_capabilities:
        tool_service.register_tool(
            name="github_create_branch",
            description=(
                "Create a new branch in a GitHub repository from an existing reference. "
                "Defaults to creating from the repository's default branch."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "Name for the new branch",
                    },
                    "from_ref": {
                        "type": "string",
                        "description": "Source branch/ref to create from (defaults to default branch)",
                    },
                },
                "required": ["repo_label", "branch_name"],
            },
            executor=github_create_branch,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

    # Commit tools
    if "commit" in all_capabilities:
        tool_service.register_tool(
            name="github_commit_file",
            description=(
                "Create or update a file in a GitHub repository with a commit. "
                "The branch parameter is required and cannot be a protected branch."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "path": {
                        "type": "string",
                        "description": "File path within the repository",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content to commit",
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Target branch (required, cannot be protected)",
                    },
                },
                "required": ["repo_label", "path", "content", "message", "branch"],
            },
            executor=github_commit_file,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_delete_file",
            description=(
                "Delete a file from a GitHub repository with a commit. "
                "The branch parameter is required and cannot be a protected branch."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "path": {
                        "type": "string",
                        "description": "File path to delete",
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Target branch (required, cannot be protected)",
                    },
                },
                "required": ["repo_label", "path", "message", "branch"],
            },
            executor=github_delete_file,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

    # PR tools
    if "pr" in all_capabilities:
        tool_service.register_tool(
            name="github_list_pull_requests",
            description="List pull requests in a GitHub repository with optional state filter.",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "state": {
                        "type": "string",
                        "enum": ["open", "closed", "all"],
                        "description": "PR state filter (default: open)",
                        "default": "open",
                    },
                },
                "required": ["repo_label"],
            },
            executor=github_list_pull_requests,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_get_pull_request",
            description="Get detailed information about a pull request including the diff.",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "pr_number": {
                        "type": "integer",
                        "description": "Pull request number",
                    },
                },
                "required": ["repo_label", "pr_number"],
            },
            executor=github_get_pull_request,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_create_pull_request",
            description=(
                "Create a new pull request. The PR template will be automatically included if present."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "title": {
                        "type": "string",
                        "description": "Pull request title",
                    },
                    "body": {
                        "type": "string",
                        "description": "Pull request description",
                    },
                    "head": {
                        "type": "string",
                        "description": "Source branch",
                    },
                    "base": {
                        "type": "string",
                        "description": "Target branch (defaults to repository's default branch)",
                    },
                },
                "required": ["repo_label", "title", "body", "head"],
            },
            executor=github_create_pull_request,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

    # Issue tools
    if "issue" in all_capabilities:
        tool_service.register_tool(
            name="github_list_issues",
            description="List issues in a GitHub repository (excludes pull requests).",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "state": {
                        "type": "string",
                        "enum": ["open", "closed", "all"],
                        "description": "Issue state filter (default: open)",
                        "default": "open",
                    },
                    "labels": {
                        "type": "string",
                        "description": "Comma-separated label filter",
                    },
                },
                "required": ["repo_label"],
            },
            executor=github_list_issues,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_get_issue",
            description="Get detailed information about an issue including comments.",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "issue_number": {
                        "type": "integer",
                        "description": "Issue number",
                    },
                },
                "required": ["repo_label", "issue_number"],
            },
            executor=github_get_issue,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_create_issue",
            description="Create a new issue in a GitHub repository.",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "title": {
                        "type": "string",
                        "description": "Issue title",
                    },
                    "body": {
                        "type": "string",
                        "description": "Issue body/description",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of labels to apply",
                    },
                },
                "required": ["repo_label", "title", "body"],
            },
            executor=github_create_issue,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

    # Comment tool (available if issue or PR capability is enabled)
    if "issue" in all_capabilities or "pr" in all_capabilities:
        tool_service.register_tool(
            name="github_add_comment",
            description="Add a comment to an issue or pull request.",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "number": {
                        "type": "integer",
                        "description": "Issue or pull request number",
                    },
                    "body": {
                        "type": "string",
                        "description": "Comment text",
                    },
                },
                "required": ["repo_label", "number", "body"],
            },
            executor=github_add_comment,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

    registered_count = len([t for t in tool_service.list_tools() if t.category == ToolCategory.GITHUB])
    logger.info(f"GitHub tools registered: {registered_count} tools")
