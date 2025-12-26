"""
GitHub tools for AI entities.

Provides repository operations that AI entities can use during conversations
to read code, create branches, commit changes, and manage PRs/issues.

Includes composite tools for efficient multi-file operations:
- github_tree: Get full repository tree structure in one call
- github_get_files: Fetch multiple files in a single call
- github_explore: Comprehensive first look at a repository
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, TYPE_CHECKING

from app.config import settings
from app.services.github_service import github_service
from app.services.tool_service import ToolCategory, ToolService
from app.services.cache_service import cache_service

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Constants for efficiency
DEFAULT_MAX_LINES = 500
DEFAULT_TREE_DEPTH = 3
MAX_TREE_DEPTH = 10
MAX_FILES_PER_REQUEST = 10
MAX_SEARCH_RESULTS = 10
SEARCH_CONTEXT_LINES = 2


# =============================================================================
# Patch Utilities
# =============================================================================

@dataclass
class PatchResult:
    """Result of applying a patch."""
    success: bool
    content: Optional[str] = None
    error: Optional[str] = None


def apply_patch(original: str, patch: str) -> PatchResult:
    """
    Apply a unified diff patch to original content.

    This is a pure Python implementation that handles standard unified diff format.
    Based on public domain code (CC0) by Isaac Turner, adapted for this use case.

    Patch format:
    - Optional --- and +++ headers (skipped if present)
    - Hunk headers: @@ -start,count +start,count @@
    - Context lines: space-prefixed (unchanged)
    - Remove lines: minus-prefixed (delete from original)
    - Add lines: plus-prefixed (insert into result)

    Line numbers in @@ headers are 1-indexed.

    Args:
        original: Original file content
        patch: Unified diff patch to apply

    Returns:
        PatchResult with success status and either content or error message
    """
    # Split original into lines (preserve line endings info)
    original_lines = original.split('\n')

    # Parse patch into hunks
    patch_lines = patch.split('\n')
    hunks: List[Tuple[int, int, int, int, List[str]]] = []

    i = 0
    while i < len(patch_lines):
        line = patch_lines[i]

        # Skip --- and +++ header lines
        if line.startswith('---') or line.startswith('+++'):
            i += 1
            continue

        # Look for hunk header: @@ -start,count +start,count @@
        hunk_match = re.match(
            r'^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@',
            line
        )

        if hunk_match:
            # Parse hunk header
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4)) if hunk_match.group(4) else 1

            # Collect hunk lines until next hunk or end
            i += 1
            hunk_lines: List[str] = []
            while i < len(patch_lines):
                hunk_line = patch_lines[i]
                # Stop at next hunk header or end
                if hunk_line.startswith('@@') or hunk_line.startswith('---') or hunk_line.startswith('+++'):
                    break
                # Include lines that start with space, +, -, or are empty (empty context line)
                if hunk_line == '' or hunk_line[0] in ' +-':
                    hunk_lines.append(hunk_line)
                    i += 1
                elif hunk_line.startswith('\\'):
                    # "\ No newline at end of file" - skip
                    i += 1
                else:
                    # Unknown line format, might be end of patch
                    break

            hunks.append((old_start, old_count, new_start, new_count, hunk_lines))
        else:
            i += 1

    if not hunks:
        return PatchResult(
            success=False,
            error="No valid hunks found in patch. Ensure patch uses unified diff format with @@ headers."
        )

    # Apply hunks in order
    # We need to track our position in original and apply adjustments
    result_lines: List[str] = []
    original_pos = 0  # 0-indexed position in original_lines

    for hunk_idx, (old_start, old_count, new_start, new_count, hunk_lines) in enumerate(hunks):
        # Convert to 0-indexed
        old_start_idx = old_start - 1

        # Copy unchanged lines before this hunk
        if old_start_idx > original_pos:
            result_lines.extend(original_lines[original_pos:old_start_idx])
        elif old_start_idx < original_pos:
            return PatchResult(
                success=False,
                error=f"Hunk {hunk_idx + 1}: Overlapping hunks detected. "
                      f"Expected position {original_pos + 1}, but hunk starts at {old_start}."
            )

        # Process hunk lines
        hunk_original_idx = old_start_idx
        context_mismatch = False

        for hunk_line in hunk_lines:
            if hunk_line == '':
                # Empty line is treated as context (empty line in original)
                prefix = ' '
                content = ''
            else:
                prefix = hunk_line[0]
                content = hunk_line[1:] if len(hunk_line) > 1 else ''

            if prefix == ' ':
                # Context line - verify it matches original
                if hunk_original_idx >= len(original_lines):
                    return PatchResult(
                        success=False,
                        error=f"Hunk {hunk_idx + 1}: Patch extends beyond end of file. "
                              f"Expected context line at position {hunk_original_idx + 1}."
                    )
                if original_lines[hunk_original_idx] != content:
                    context_mismatch = True
                    return PatchResult(
                        success=False,
                        error=f"Hunk {hunk_idx + 1}: Context mismatch at line {hunk_original_idx + 1}. "
                              f"Expected: '{content[:50]}{'...' if len(content) > 50 else ''}', "
                              f"Found: '{original_lines[hunk_original_idx][:50]}{'...' if len(original_lines[hunk_original_idx]) > 50 else ''}'."
                    )
                result_lines.append(content)
                hunk_original_idx += 1

            elif prefix == '-':
                # Remove line - verify it matches original
                if hunk_original_idx >= len(original_lines):
                    return PatchResult(
                        success=False,
                        error=f"Hunk {hunk_idx + 1}: Cannot remove line at position {hunk_original_idx + 1}, "
                              f"file only has {len(original_lines)} lines."
                    )
                if original_lines[hunk_original_idx] != content:
                    return PatchResult(
                        success=False,
                        error=f"Hunk {hunk_idx + 1}: Line to remove at position {hunk_original_idx + 1} doesn't match. "
                              f"Expected: '{content[:50]}{'...' if len(content) > 50 else ''}', "
                              f"Found: '{original_lines[hunk_original_idx][:50]}{'...' if len(original_lines[hunk_original_idx]) > 50 else ''}'."
                    )
                # Don't add to result (removing the line)
                hunk_original_idx += 1

            elif prefix == '+':
                # Add line
                result_lines.append(content)
                # Don't advance hunk_original_idx (adding, not consuming original)

        # Update position after hunk
        original_pos = hunk_original_idx

    # Copy remaining lines after last hunk
    if original_pos < len(original_lines):
        result_lines.extend(original_lines[original_pos:])

    return PatchResult(
        success=True,
        content='\n'.join(result_lines)
    )


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


def _format_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f}KB"
    return f"{size_bytes}B"


def _build_tree_view(
    tree_items: List[dict],
    max_depth: int = DEFAULT_TREE_DEPTH,
    include_sizes: bool = True,
) -> tuple[str, int, int]:
    """
    Build a formatted tree view from GitHub tree API response.

    Args:
        tree_items: List of tree items from GitHub API
        max_depth: Maximum depth to display
        include_sizes: Whether to include file sizes

    Returns:
        Tuple of (formatted_tree_string, file_count, dir_count)
    """
    # Build tree structure
    tree: dict = {}
    file_count = 0
    dir_count = 0

    for item in tree_items:
        path = item.get("path", "")
        item_type = item.get("type", "")  # "blob" or "tree"
        size = item.get("size", 0)

        parts = path.split("/")
        depth = len(parts)

        # Skip items beyond max depth
        if depth > max_depth:
            continue

        # Build nested dict
        current = tree
        for i, part in enumerate(parts[:-1]):
            if part not in current:
                current[part] = {"_type": "dir", "_children": {}}
                dir_count += 1
            current = current[part]["_children"]

        # Add leaf node
        if item_type == "blob":
            current[parts[-1]] = {"_type": "file", "_size": size}
            file_count += 1
        elif item_type == "tree" and parts[-1] not in current:
            current[parts[-1]] = {"_type": "dir", "_children": {}}
            dir_count += 1

    # Format output
    lines = []

    def format_node(node: dict, prefix: str = "", is_last: bool = True) -> None:
        items = sorted(node.items(), key=lambda x: (x[1].get("_type") == "file", x[0].lower()))
        for i, (name, data) in enumerate(items):
            if name.startswith("_"):
                continue

            is_last_item = i == len([k for k in node.keys() if not k.startswith("_")]) - 1
            current_prefix = "└── " if is_last_item else "├── "
            next_prefix = "    " if is_last_item else "│   "

            if data.get("_type") == "dir":
                lines.append(f"{prefix}{current_prefix}{name}/")
                children = data.get("_children", {})
                if children:
                    format_node(children, prefix + next_prefix, is_last_item)
            else:
                size_str = f" ({_format_size(data.get('_size', 0))})" if include_sizes else ""
                lines.append(f"{prefix}{current_prefix}{name}{size_str}")

    format_node(tree)
    return "\n".join(lines), file_count, dir_count


def _count_code_structures(content: str, file_path: str) -> dict:
    """
    Count code structures (functions, classes) using simple regex patterns.

    Args:
        content: File content
        file_path: File path for language detection

    Returns:
        Dict with counts: {"functions": N, "classes": N}
    """
    counts = {"functions": 0, "classes": 0}
    ext = file_path.split(".")[-1].lower() if "." in file_path else ""

    if ext in ("py", "pyw"):
        # Python: def and class at start of line
        counts["functions"] = len(re.findall(r"^\s*def\s+\w+", content, re.MULTILINE))
        counts["classes"] = len(re.findall(r"^\s*class\s+\w+", content, re.MULTILINE))
    elif ext in ("js", "jsx", "ts", "tsx", "mjs", "cjs"):
        # JavaScript/TypeScript: function, class, const/let with arrow
        counts["functions"] = len(re.findall(r"\bfunction\s+\w+", content))
        counts["functions"] += len(re.findall(r"(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\(", content))
        counts["classes"] = len(re.findall(r"\bclass\s+\w+", content))
    elif ext in ("java", "kt", "scala"):
        # Java/Kotlin/Scala
        counts["functions"] = len(re.findall(r"(?:public|private|protected|static|\s)+\w+\s+\w+\s*\(", content))
        counts["classes"] = len(re.findall(r"\bclass\s+\w+", content))
    elif ext in ("go",):
        # Go
        counts["functions"] = len(re.findall(r"\bfunc\s+", content))
        counts["classes"] = len(re.findall(r"\btype\s+\w+\s+struct\s*\{", content))
    elif ext in ("rs",):
        # Rust
        counts["functions"] = len(re.findall(r"\bfn\s+\w+", content))
        counts["classes"] = len(re.findall(r"\b(?:struct|impl)\s+\w+", content))
    elif ext in ("rb",):
        # Ruby
        counts["functions"] = len(re.findall(r"\bdef\s+\w+", content))
        counts["classes"] = len(re.findall(r"\bclass\s+\w+", content))
    elif ext in ("php",):
        # PHP
        counts["functions"] = len(re.findall(r"\bfunction\s+\w+", content))
        counts["classes"] = len(re.findall(r"\bclass\s+\w+", content))
    elif ext in ("c", "cpp", "cc", "h", "hpp"):
        # C/C++
        counts["functions"] = len(re.findall(r"\w+\s+\w+\s*\([^)]*\)\s*\{", content))
        counts["classes"] = len(re.findall(r"\bclass\s+\w+", content))

    return counts


def _truncate_file_content(
    content: str,
    max_lines: int,
    file_path: str,
) -> tuple[str, bool, str]:
    """
    Truncate file content with smart summarization.

    Args:
        content: Full file content
        max_lines: Maximum lines to include
        file_path: File path for structure analysis

    Returns:
        Tuple of (truncated_content, was_truncated, summary_footer)
    """
    lines = content.split("\n")
    total_lines = len(lines)

    if total_lines <= max_lines:
        return content, False, ""

    truncated = "\n".join(lines[:max_lines])

    # Build summary
    summary_parts = [
        f"[... File truncated. Showing lines 1-{max_lines} of {total_lines} total.]",
        "[To read more, use github_get_file with start_line and end_line parameters.]",
    ]

    # Add structure summary for code files
    structures = _count_code_structures(content, file_path)
    if structures["functions"] > 0 or structures["classes"] > 0:
        struct_parts = []
        if structures["functions"] > 0:
            struct_parts.append(f"{structures['functions']} functions")
        if structures["classes"] > 0:
            struct_parts.append(f"{structures['classes']} classes")
        summary_parts.append(f"[File structure summary: {', '.join(struct_parts)} detected]")

    return truncated, True, "\n".join(summary_parts)


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
    max_lines: Optional[int] = None,
    bypass_cache: bool = False,
) -> str:
    """
    Get file contents from the repository.

    For reading a single file or specific line ranges. If you need multiple files,
    prefer github_get_files. Files over 500 lines are automatically truncated.

    Args:
        repo_label: The label of the configured repository
        path: Path to the file
        ref: Git reference (branch, tag, commit SHA). Defaults to default branch.
        start_line: Optional starting line number (1-indexed, inclusive)
        end_line: Optional ending line number (1-indexed, inclusive)
        max_lines: Maximum lines to return (default 500). Ignored if start_line/end_line specified.
        bypass_cache: Set to true to fetch fresh data instead of cached

    Returns:
        File contents or error message. For binary files, returns metadata only.
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("read"):
        return f"Error: Read capability is not enabled for repository '{repo_label}'."

    # Default max_lines
    if max_lines is None:
        max_lines = DEFAULT_MAX_LINES

    try:
        # Check cache first (unless bypassing or using line ranges)
        cached_data = None
        if not bypass_cache and start_line is None and end_line is None:
            cached_data = cache_service.get_github_file(repo_label, path, ref)
            if cached_data:
                logger.info(f"[{repo_label}] Cache HIT for file '{path}'")

        if cached_data:
            data = cached_data
            source = "cache"
            success = True
        else:
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

            # Cache the result on success
            if success and data.get("type") != "binary":
                cache_service.set_github_file(repo_label, path, ref, data)

        if not success:
            return f"Error: {data.get('message', 'Failed to get file')}"

        if data.get("type") == "binary":
            size = data.get('size', 0)
            lines = [
                f"Binary file: {data.get('name')}",
                f"Size: {_format_size(size)}",
            ]
            return "\n".join(lines)

        content = data.get("content", "")
        total_lines = len(content.split("\n"))

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
            header += f" [lines {start_idx + 1}-{end_idx} of {total_lines}]"

            # Add line numbers
            numbered_lines = [
                f"{i + start_idx + 1:4d} | {line}"
                for i, line in enumerate(selected_lines)
            ]

            return f"{header}\n\n" + "\n".join(numbered_lines)

        # Apply truncation if needed
        truncated_content, was_truncated, summary = _truncate_file_content(content, max_lines, path)

        # Build header
        size = data.get('size', len(content))
        header = f"File: {path}"
        if ref:
            header += f" (ref: {ref})"
        header += f" ({_format_size(size)}, {total_lines} lines)"
        if source == "cache":
            header += " [cached]"

        if was_truncated:
            return f"{header}\n\n{truncated_content}\n\n{summary}"

        return f"{header}\n\n{truncated_content}"

    except Exception as e:
        logger.exception(f"Error getting file: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_search_code(repo_label: str, query: str) -> str:
    """
    Search for code in the repository.

    Returns max 10 matches with file paths. For more detailed results,
    use github_get_file to read specific files.

    Args:
        repo_label: The label of the configured repository
        query: Search query (supports GitHub code search syntax)

    Returns:
        Search results with file paths (max 10), or error message
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

        total_results = len(results)
        # Limit to MAX_SEARCH_RESULTS
        displayed_results = results[:MAX_SEARCH_RESULTS]

        lines = [f"Search results for '{query}':"]
        if total_results > MAX_SEARCH_RESULTS:
            lines[0] += f" (showing {MAX_SEARCH_RESULTS} of {total_results} matches)"
        else:
            lines[0] += f" ({total_results} matches)"
        lines.append("")

        for i, result in enumerate(displayed_results, 1):
            lines.append(f"{i}. {result.get('path')}")

        if total_results > MAX_SEARCH_RESULTS:
            lines.append("")
            lines.append(f"[{total_results - MAX_SEARCH_RESULTS} more matches not shown. Refine your query for more specific results.]")

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
# Composite Tools (capability: "read") - Efficiency optimized
# =============================================================================

async def github_tree(
    repo_label: str,
    ref: Optional[str] = None,
    max_depth: int = DEFAULT_TREE_DEPTH,
    include_sizes: bool = True,
    bypass_cache: bool = False,
) -> str:
    """
    Get the full repository tree structure in a single call.

    Use this first when exploring a new repository. Returns the complete file
    structure in one call, eliminating the need for multiple list_contents calls.

    Args:
        repo_label: The label of the configured repository
        ref: Branch, tag, or commit SHA. Defaults to the repo's default branch.
        max_depth: How deep to traverse (default 3, max 10).
        include_sizes: Include file sizes (default true).
        bypass_cache: Set to true to fetch fresh data instead of cached.

    Returns:
        Formatted tree view with file/directory structure.
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("read"):
        return f"Error: Read capability is not enabled for repository '{repo_label}'."

    # Clamp max_depth
    max_depth = min(max(1, max_depth), MAX_TREE_DEPTH)

    try:
        from_cache = False
        from_local = False

        # Check cache first
        if not bypass_cache:
            cached = cache_service.get_github_tree(repo_label, ref)
            if cached:
                logger.info(f"[{repo_label}] Cache HIT for tree (ref={ref or 'HEAD'})")
                tree_data = cached
                from_cache = True

        if not from_cache:
            # Try local clone first if no specific ref and local clone is available
            if not ref and github_service.has_local_clone(repo):
                success, tree_data = github_service.get_tree_local(repo)
                if success:
                    from_local = True
                    logger.info(f"[{repo_label}] Reading tree from local clone")
                else:
                    logger.warning(f"[{repo_label}] Local tree read failed, falling back to API: {tree_data.get('message')}")

            # Fall back to API if local clone not available or failed
            if not from_cache and not from_local:
                # Get default branch if needed
                if not ref:
                    default_branch = await github_service.get_default_branch(repo)
                    ref = default_branch

                success, tree_data = await github_service.get_tree(repo, ref, recursive=True)
                logger.info(f"[{repo_label}] Reading tree from GitHub API (ref={ref})")

                if not success:
                    return f"Error: {tree_data.get('message', 'Failed to get tree')}"

            # Cache the tree (only cache API results, not local)
            if not from_local:
                cache_service.set_github_tree(repo_label, ref, tree_data)

        # Build formatted tree view
        tree_items = tree_data.get("tree", [])
        tree_view, file_count, dir_count = _build_tree_view(
            tree_items, max_depth=max_depth, include_sizes=include_sizes
        )

        # Build header
        header_parts = [f"Repository: {repo.owner}/{repo.repo}"]
        if ref:
            header_parts.append(f"(branch: {ref})")
        if from_cache:
            header_parts.append("[cached]")
        elif from_local:
            header_parts.append("[local]")

        header = " ".join(header_parts)

        # Build footer
        footer = f"\nTotal: {file_count} files, {dir_count} directories"
        if tree_data.get("truncated"):
            footer += " (tree truncated by GitHub API)"
        if max_depth < MAX_TREE_DEPTH:
            footer += f" (depth limited to {max_depth})"

        return f"{header}\n\n{tree_view}\n{footer}"

    except Exception as e:
        logger.exception(f"Error getting tree: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_get_files(
    repo_label: str,
    paths: List[str],
    ref: Optional[str] = None,
    max_lines_per_file: int = DEFAULT_MAX_LINES,
    bypass_cache: bool = False,
) -> str:
    """
    Fetch multiple files in a single tool call.

    More efficient than multiple github_get_file calls when you know which files
    you need. Fetches files in parallel.

    Args:
        repo_label: The label of the configured repository
        paths: List of file paths to fetch (max 10)
        ref: Branch, tag, or commit SHA. Defaults to the repo's default branch.
        max_lines_per_file: Truncate files to this many lines (default 500).
        bypass_cache: Set to true to fetch fresh data instead of cached.

    Returns:
        Concatenated file contents with clear separators.
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("read"):
        return f"Error: Read capability is not enabled for repository '{repo_label}'."

    # Limit number of files
    if len(paths) > MAX_FILES_PER_REQUEST:
        return f"Error: Maximum {MAX_FILES_PER_REQUEST} files per request. Requested: {len(paths)}"

    if not paths:
        return "Error: No file paths specified."

    try:
        # Fetch files in parallel
        async def fetch_file(path: str) -> tuple[str, str, bool]:
            """Fetch a single file and return (path, content, is_error)."""
            # Check cache first
            if not bypass_cache:
                cached = cache_service.get_github_file(repo_label, path, ref)
                if cached:
                    logger.info(f"[{repo_label}] Cache HIT for file '{path}'")
                    return path, cached, False

            # Try local clone first
            if not ref and github_service.has_local_clone(repo):
                success, data = github_service.get_file_contents_local(repo, path)
                logger.info(f"[{repo_label}] Reading file '{path}' from local clone")
            else:
                success, data = await github_service.get_file_contents(repo, path, ref)
                logger.info(f"[{repo_label}] Reading file '{path}' from GitHub API")

            if not success:
                return path, data, True

            # Cache on success
            if data.get("type") != "binary":
                cache_service.set_github_file(repo_label, path, ref, data)

            return path, data, False

        # Execute in parallel
        results = await asyncio.gather(*[fetch_file(p) for p in paths], return_exceptions=True)

        # Format output
        output_parts = []
        for result in results:
            if isinstance(result, Exception):
                output_parts.append(f"Error: {str(result)}")
                continue

            path, data, is_error = result

            if is_error:
                output_parts.append(f"===== {path} (ERROR) =====\n{data.get('message', 'Failed to get file')}\n")
                continue

            if data.get("type") == "binary":
                size = data.get('size', 0)
                output_parts.append(f"===== {path} (binary, {_format_size(size)}) =====\n[Binary file - content not shown]\n")
                continue

            content = data.get("content", "")
            lines = content.split("\n")
            total_lines = len(lines)
            size = data.get("size", len(content))

            # Truncate if needed
            truncated_content, was_truncated, _ = _truncate_file_content(
                content, max_lines_per_file, path
            )

            header = f"===== {path} ({_format_size(size)}, {total_lines} lines"
            if was_truncated:
                header += f", TRUNCATED to first {max_lines_per_file} lines"
            header += ") ====="

            output_parts.append(f"{header}\n\n{truncated_content}")

            if was_truncated:
                omitted = total_lines - max_lines_per_file
                output_parts.append(f"\n[... {omitted} lines omitted. Use github_get_file with start_line/end_line to read specific sections.]\n")
            else:
                output_parts.append("\n")

        return "\n".join(output_parts)

    except Exception as e:
        logger.exception(f"Error getting files: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_explore(
    repo_label: str,
    ref: Optional[str] = None,
    bypass_cache: bool = False,
) -> str:
    """
    Get a comprehensive first look at a repository in a single call.

    Best starting point for a new repository. Returns metadata, file structure,
    and key documentation files in one call. Useful for orientation before
    deeper work.

    Args:
        repo_label: The label of the configured repository
        ref: Branch, tag, or commit SHA. Defaults to the repo's default branch.
        bypass_cache: Set to true to fetch fresh data instead of cached.

    Returns:
        Combined response with repo metadata, tree structure (depth 2), and
        contents of key files (README.md, CLAUDE.md, etc.) if they exist.
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("read"):
        return f"Error: Read capability is not enabled for repository '{repo_label}'."

    MAX_DOC_LINES = 200
    KEY_FILES = [
        "README.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
    ]
    PROJECT_FILES = [
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "Gemfile",
        "composer.json",
    ]

    try:
        output_parts = []

        # Track if ref was originally specified by the user
        user_specified_ref = ref is not None

        # 1. Repository metadata
        success, repo_info = await github_service.get_repo_info(repo)
        if success:
            output_parts.append("=== Repository Info ===")
            output_parts.append(f"Name: {repo_info.get('full_name')}")
            output_parts.append(f"Description: {repo_info.get('description') or 'No description'}")
            output_parts.append(f"Default Branch: {repo_info.get('default_branch')}")
            output_parts.append(f"Visibility: {repo_info.get('visibility')}")
            output_parts.append(f"Language: {repo_info.get('language') or 'Not specified'}")
            output_parts.append(f"Stars: {repo_info.get('stars', 0):,}")
            output_parts.append("")

            # Use default branch if ref not specified
            if not ref:
                ref = repo_info.get('default_branch', 'main')

        # 2. Tree structure (depth 2)
        tree_from_cache = False
        tree_from_local = False

        if not bypass_cache:
            cached_tree = cache_service.get_github_tree(repo_label, ref)
        else:
            cached_tree = None

        if cached_tree:
            tree_data = cached_tree
            tree_from_cache = True
            logger.info(f"[{repo_label}] Cache HIT for tree in explore")
        else:
            # Try local clone first if no specific ref was specified by user and local clone is available
            if not user_specified_ref and github_service.has_local_clone(repo):
                success, tree_data = github_service.get_tree_local(repo)
                if success:
                    tree_from_local = True
                    logger.info(f"[{repo_label}] Reading tree from local clone in explore")
                else:
                    logger.warning(f"[{repo_label}] Local tree read failed in explore, falling back to API")
                    success, tree_data = await github_service.get_tree(repo, ref, recursive=True)
                    logger.info(f"[{repo_label}] Reading tree from GitHub API in explore")
            else:
                success, tree_data = await github_service.get_tree(repo, ref, recursive=True)
                logger.info(f"[{repo_label}] Reading tree from GitHub API in explore (ref={ref})")

            if success and not tree_from_local:
                cache_service.set_github_tree(repo_label, ref, tree_data)

        if tree_data and not tree_data.get("error"):
            tree_items = tree_data.get("tree", [])
            tree_view, file_count, dir_count = _build_tree_view(
                tree_items, max_depth=2, include_sizes=True
            )

            output_parts.append("=== File Structure (depth 2) ===")
            if tree_from_cache:
                output_parts.append("[cached]")
            elif tree_from_local:
                output_parts.append("[local]")
            output_parts.append(tree_view)
            output_parts.append(f"\nTotal: {file_count} files, {dir_count} directories")
            output_parts.append("")

        # 3. Key documentation files
        docs_found = []

        async def try_get_file(path: str) -> Optional[dict]:
            """Try to get a file, return None if not found."""
            # Check cache first
            if not bypass_cache:
                cached = cache_service.get_github_file(repo_label, path, ref)
                if cached:
                    logger.info(f"[{repo_label}] Cache HIT for file '{path}' in explore")
                    return cached

            if not user_specified_ref and github_service.has_local_clone(repo):
                success, data = github_service.get_file_contents_local(repo, path)
                if success:
                    logger.info(f"[{repo_label}] Reading file '{path}' from local clone in explore")
            else:
                success, data = await github_service.get_file_contents(repo, path, ref)
                if success:
                    logger.info(f"[{repo_label}] Reading file '{path}' from GitHub API in explore")

            if success and data.get("type") == "text":
                cache_service.set_github_file(repo_label, path, ref, data)
                return data
            return None

        # Fetch key docs in parallel
        doc_tasks = [try_get_file(path) for path in KEY_FILES]
        doc_results = await asyncio.gather(*doc_tasks, return_exceptions=True)

        for path, result in zip(KEY_FILES, doc_results):
            if isinstance(result, Exception) or result is None:
                continue

            content = result.get("content", "")
            lines = content.split("\n")

            if len(lines) <= MAX_DOC_LINES:
                docs_found.append((path, content, False))
            else:
                truncated = "\n".join(lines[:MAX_DOC_LINES])
                docs_found.append((path, truncated, True))

        # Try to find project file
        project_tasks = [try_get_file(path) for path in PROJECT_FILES]
        project_results = await asyncio.gather(*project_tasks, return_exceptions=True)

        for path, result in zip(PROJECT_FILES, project_results):
            if isinstance(result, Exception) or result is None:
                continue

            content = result.get("content", "")
            lines = content.split("\n")

            if len(lines) <= MAX_DOC_LINES:
                docs_found.append((path, content, False))
            else:
                truncated = "\n".join(lines[:MAX_DOC_LINES])
                docs_found.append((path, truncated, True))
            break  # Only include first project file found

        # Add documentation files to output
        if docs_found:
            output_parts.append("=== Key Files ===")
            for path, content, was_truncated in docs_found:
                lines_count = len(content.split("\n"))
                header = f"\n--- {path}"
                if was_truncated:
                    header += f" (first {MAX_DOC_LINES} of {lines_count}+ lines)"
                header += " ---"
                output_parts.append(header)
                output_parts.append(content)
                if was_truncated:
                    output_parts.append(f"[... truncated. Use github_get_file to read full content.]")

        return "\n".join(output_parts)

    except Exception as e:
        logger.exception(f"Error exploring repository: {e}")
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

        # Invalidate cache for this file and tree (commit changes the repo state)
        cache_service.invalidate_github_file(repo_label, path, branch)
        cache_service.invalidate_github_tree(repo_label, branch)

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

        # Invalidate cache for this file and tree (delete changes the repo state)
        cache_service.invalidate_github_file(repo_label, path, branch)
        cache_service.invalidate_github_tree(repo_label, branch)

        return f"Successfully deleted file: {path}\nBranch: {branch}\nCommit SHA: {data.get('sha')}"

    except Exception as e:
        logger.exception(f"Error deleting file: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


async def github_commit_patch(
    repo_label: str,
    path: str,
    patch: str,
    message: str,
    branch: str,
) -> str:
    """
    Apply a unified diff patch to a file and commit the result.

    More token-efficient than github_commit_file for large files. Instead of
    transmitting the complete file content, only transmit the changes as a
    unified diff patch.

    Args:
        repo_label: The label of the configured repository
        path: File path within the repository
        patch: Unified diff patch to apply. Format:
               - Optional --- and +++ headers (skipped if present)
               - Hunk headers: @@ -start,count +start,count @@
               - Context lines: space-prefixed (unchanged)
               - Remove lines: minus-prefixed
               - Add lines: plus-prefixed
        message: Commit message
        branch: Target branch (required, cannot be a protected branch)

    Returns:
        Success message with commit SHA and URL, or error message

    Example patch format:
        @@ -10,4 +10,5 @@
         context line
        -old line to remove
        +new line to add
         more context
        +another new line
    """
    repo = github_service.get_repo_by_label(repo_label)
    if not repo:
        return _repo_not_found_error(repo_label)

    if not repo.has_capability("commit"):
        return f"Error: Commit capability is not enabled for repository '{repo_label}'."

    try:
        # Get current file contents
        # Try local clone first if available
        if github_service.has_local_clone(repo):
            logger.info(f"[{repo_label}] Reading file '{path}' from local clone for patch")
            success, file_data = github_service.get_file_contents_local(repo, path)
        else:
            logger.info(f"[{repo_label}] Reading file '{path}' from GitHub API for patch")
            success, file_data = await github_service.get_file_contents(repo, path, branch)

        if not success:
            return f"Error: {file_data.get('message', 'Failed to get file')}"

        # Ensure it's a text file
        if file_data.get("type") == "binary":
            return f"Error: Cannot apply patch to binary file: {path}"

        original_content = file_data.get("content", "")

        # Apply the patch
        patch_result = apply_patch(original_content, patch)

        if not patch_result.success:
            return f"Error: Patch failed to apply. {patch_result.error}"

        # Commit the patched content using existing commit flow
        success, data = await github_service.commit_file(
            repo, path, patch_result.content, message, branch
        )

        if not success:
            return f"Error: {data.get('message', 'Failed to commit patched file')}"

        # Invalidate cache for this file and tree
        cache_service.invalidate_github_file(repo_label, path, branch)
        cache_service.invalidate_github_tree(repo_label, branch)

        lines = [
            f"Successfully patched and committed file: {path}",
            f"Branch: {branch}",
            f"Commit SHA: {data.get('sha')}",
        ]
        if data.get("html_url"):
            lines.append(f"View on GitHub: {data.get('html_url')}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception(f"Error committing patch: {e}")
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
        # Composite/efficiency tools - register first for better discovery
        tool_service.register_tool(
            name="github_explore",
            description=(
                "Best starting point for a new repository. Returns metadata, file structure "
                "(depth 2), and key documentation files (README.md, CLAUDE.md, etc.) in one call. "
                "Use this first to understand a repository before deeper exploration."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch, tag, or commit SHA. Defaults to default branch.",
                    },
                    "bypass_cache": {
                        "type": "boolean",
                        "description": "Set to true to fetch fresh data instead of cached.",
                        "default": False,
                    },
                },
                "required": ["repo_label"],
            },
            executor=github_explore,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_tree",
            description=(
                "Get the full repository tree structure in a single call. Use this instead of "
                "repeated github_list_contents calls to see the complete directory structure. "
                "Returns a formatted tree view with file sizes."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch, tag, or commit SHA. Defaults to default branch.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "How deep to traverse (default 3, max 10).",
                        "default": 3,
                    },
                    "include_sizes": {
                        "type": "boolean",
                        "description": "Include file sizes in output (default true).",
                        "default": True,
                    },
                    "bypass_cache": {
                        "type": "boolean",
                        "description": "Set to true to fetch fresh data instead of cached.",
                        "default": False,
                    },
                },
                "required": ["repo_label"],
            },
            executor=github_tree,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        tool_service.register_tool(
            name="github_get_files",
            description=(
                "Fetch up to 10 files in a single call. More efficient than multiple "
                "github_get_file calls when you know which files you need. Files are "
                "fetched in parallel and returned with clear separators."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_label": {
                        "type": "string",
                        "description": repo_label_description,
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to fetch (max 10).",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch, tag, or commit SHA. Defaults to default branch.",
                    },
                    "max_lines_per_file": {
                        "type": "integer",
                        "description": "Truncate files to this many lines (default 500).",
                        "default": 500,
                    },
                    "bypass_cache": {
                        "type": "boolean",
                        "description": "Set to true to fetch fresh data instead of cached.",
                        "default": False,
                    },
                },
                "required": ["repo_label", "paths"],
            },
            executor=github_get_files,
            category=ToolCategory.GITHUB,
            enabled=True,
        )

        # Standard read tools
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
                "List files and directories at a path. For a complete tree view, prefer "
                "github_tree which returns the full structure in one call."
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
                "Read a single file or specific line ranges. If you need multiple files, "
                "prefer github_get_files. Files over 500 lines are automatically truncated "
                "with a structure summary."
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
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum lines to return (default 500). Ignored if start_line/end_line specified.",
                        "default": 500,
                    },
                    "bypass_cache": {
                        "type": "boolean",
                        "description": "Set to true to fetch fresh data instead of cached.",
                        "default": False,
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
                "Search for code patterns in a repository. Returns max 10 file paths matching "
                "the query. Use github_get_file or github_get_files to read the matched files."
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

        tool_service.register_tool(
            name="github_commit_patch",
            description=(
                "Apply a unified diff patch to a file and commit the result. "
                "More token-efficient than github_commit_file for large files - "
                "only transmit the changes instead of full file content. "
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
                    "patch": {
                        "type": "string",
                        "description": (
                            "Unified diff patch to apply. Format: "
                            "@@ -start,count +start,count @@ header, "
                            "space-prefixed context lines, "
                            "minus-prefixed lines to remove, "
                            "plus-prefixed lines to add. "
                            "Line numbers are 1-indexed."
                        ),
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
                "required": ["repo_label", "path", "patch", "message", "branch"],
            },
            executor=github_commit_patch,
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
