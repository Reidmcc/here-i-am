"""
Codebase Navigator Tools.

Tool definitions for the codebase navigator, allowing AI entities to
explore codebases efficiently using the Devstral model.
"""

import logging
from typing import TYPE_CHECKING

from app.config import settings
from app.services.tool_service import ToolCategory

if TYPE_CHECKING:
    from app.services.tool_service import ToolService

logger = logging.getLogger(__name__)


async def navigate_codebase(
    task: str,
    repo_path: str = ".",
    query_type: str = "relevance",
) -> str:
    """
    Query the codebase navigator to find relevant files for a task.

    Use this BEFORE starting implementation to understand what code
    is relevant and how the codebase is organized.

    Args:
        task: Description of what you're trying to accomplish
        repo_path: Path to the repository root (default: current directory)
        query_type: One of "relevance", "structure", "dependencies",
                    "entry_points", "impact"

    Returns:
        Formatted results with relevant files and explanations
    """
    from app.services.codebase_navigator_service import codebase_navigator_service
    from app.services.codebase_navigator import NavigatorNotConfiguredError

    # Check if configured
    if not codebase_navigator_service.is_configured():
        return "Error: Codebase navigator is not configured. Set CODEBASE_NAVIGATOR_ENABLED=true and MISTRAL_API_KEY."

    try:
        response = await codebase_navigator_service.navigate(
            task=task,
            repo_path=repo_path,
            query_type=query_type,
        )

        return response.format_for_tool()

    except NavigatorNotConfiguredError as e:
        return f"Error: Navigator not configured: {str(e)}"
    except Exception as e:
        logger.exception(f"Navigator error: {e}")
        return f"Error: {str(e)}"


async def navigate_codebase_structure(repo_path: str = ".") -> str:
    """
    Get an overview of the codebase structure and architecture.

    Use this to understand how the codebase is organized before
    diving into specific tasks.

    Args:
        repo_path: Path to the repository root (default: current directory)

    Returns:
        Architecture overview and key entry points
    """
    return await navigate_codebase(
        task="Provide a comprehensive overview of the codebase architecture, main components, and how they interact.",
        repo_path=repo_path,
        query_type="structure",
    )


async def navigate_find_entry_points(
    feature: str,
    repo_path: str = ".",
) -> str:
    """
    Find the entry points for implementing or modifying a feature.

    Use this to identify where to start making changes.

    Args:
        feature: Description of the feature to implement or modify
        repo_path: Path to the repository root

    Returns:
        List of entry points with explanations
    """
    return await navigate_codebase(
        task=f"Find the entry points and key locations for implementing or modifying: {feature}",
        repo_path=repo_path,
        query_type="entry_points",
    )


async def navigate_assess_impact(
    change: str,
    repo_path: str = ".",
) -> str:
    """
    Assess the potential impact of a code change.

    Use this before making changes to understand what else might be affected.

    Args:
        change: Description of the change being considered
        repo_path: Path to the repository root

    Returns:
        Impact assessment including affected files and tests
    """
    return await navigate_codebase(
        task=f"Assess the impact of this change: {change}. What other files, tests, or documentation might be affected?",
        repo_path=repo_path,
        query_type="impact",
    )


async def navigate_trace_dependencies(
    component: str,
    repo_path: str = ".",
) -> str:
    """
    Trace the dependencies of a component.

    Use this to understand what a component depends on and what depends on it.

    Args:
        component: Name or description of the component to trace
        repo_path: Path to the repository root

    Returns:
        Dependency information including imports and dependents
    """
    return await navigate_codebase(
        task=f"Trace the dependencies for: {component}. Show what it imports and what other code depends on it.",
        repo_path=repo_path,
        query_type="dependencies",
    )


async def navigator_invalidate_cache(repo_path: str = ".") -> str:
    """
    Invalidate the navigator cache for a repository.

    Use this after the codebase has been modified to ensure fresh results.

    Args:
        repo_path: Path to the repository root

    Returns:
        Confirmation of cache invalidation
    """
    from app.services.codebase_navigator_service import codebase_navigator_service

    if not codebase_navigator_service.is_configured():
        return "Error: Codebase navigator is not configured."

    try:
        count = codebase_navigator_service.invalidate_cache(repo_path)
        return f"Cache invalidated. Removed {count} cached entries for {repo_path}"
    except Exception as e:
        logger.exception(f"Cache invalidation error: {e}")
        return f"Error: {str(e)}"


def register_codebase_navigator_tools(tool_service: "ToolService") -> None:
    """
    Register codebase navigator tools with the tool service.

    Only registers if the navigator is enabled in settings.
    """
    if not settings.codebase_navigator_enabled:
        logger.info("Codebase navigator tools disabled (CODEBASE_NAVIGATOR_ENABLED=false)")
        return

    logger.info("Registering codebase navigator tools")

    # Main navigation tool
    tool_service.register_tool(
        name="navigate_codebase",
        description="""Query the codebase navigator to find relevant files for a task.

Use this BEFORE starting implementation to understand what code is relevant and how the codebase is organized.

The navigator analyzes the entire codebase and identifies:
- Files that are DIRECTLY relevant (will need modification)
- Files that provide CONTEXT (help understand how things work)
- Files that might be AFFECTED (dependencies, tests, etc.)

Query types:
- "relevance": Find files relevant to implementing a task (default)
- "structure": Understand codebase architecture and organization
- "dependencies": Trace imports and dependencies
- "entry_points": Find where to start modifications
- "impact": Assess what might be affected by changes

Example:
  navigate_codebase(
    task="Add rate limiting to the API endpoints",
    query_type="relevance"
  )""",
        input_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Description of what you're trying to accomplish",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to the repository root (default: current directory)",
                    "default": ".",
                },
                "query_type": {
                    "type": "string",
                    "enum": ["relevance", "structure", "dependencies", "entry_points", "impact"],
                    "description": "Type of query to perform",
                    "default": "relevance",
                },
            },
            "required": ["task"],
        },
        executor=navigate_codebase,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # Structure overview tool
    tool_service.register_tool(
        name="navigate_codebase_structure",
        description="""Get an overview of the codebase structure and architecture.

Use this to understand how the codebase is organized before diving into specific tasks.

Returns:
- Main components and how they interact
- Key entry points
- Important patterns and conventions""",
        input_schema={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to the repository root (default: current directory)",
                    "default": ".",
                },
            },
            "required": [],
        },
        executor=navigate_codebase_structure,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # Entry points tool
    tool_service.register_tool(
        name="navigate_find_entry_points",
        description="""Find the entry points for implementing or modifying a feature.

Use this to identify where to start making changes for a specific feature.

Returns:
- Key files and functions to modify
- Logical order of changes
- Configuration that might need updates""",
        input_schema={
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "description": "Description of the feature to implement or modify",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to the repository root (default: current directory)",
                    "default": ".",
                },
            },
            "required": ["feature"],
        },
        executor=navigate_find_entry_points,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # Impact assessment tool
    tool_service.register_tool(
        name="navigate_assess_impact",
        description="""Assess the potential impact of a code change.

Use this before making changes to understand what else might be affected.

Returns:
- Files that depend on the changed code
- Tests that might need updating
- Documentation that might need changes
- Potential breaking changes""",
        input_schema={
            "type": "object",
            "properties": {
                "change": {
                    "type": "string",
                    "description": "Description of the change being considered",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to the repository root (default: current directory)",
                    "default": ".",
                },
            },
            "required": ["change"],
        },
        executor=navigate_assess_impact,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # Dependency tracing tool
    tool_service.register_tool(
        name="navigate_trace_dependencies",
        description="""Trace the dependencies of a component.

Use this to understand what a component depends on and what depends on it.

Returns:
- Imports and dependencies
- Files that depend on this component
- Import chains and module relationships""",
        input_schema={
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "description": "Name or description of the component to trace",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Path to the repository root (default: current directory)",
                    "default": ".",
                },
            },
            "required": ["component"],
        },
        executor=navigate_trace_dependencies,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # Cache invalidation tool
    tool_service.register_tool(
        name="navigator_invalidate_cache",
        description="""Invalidate the navigator cache for a repository.

Use this after the codebase has been modified to ensure fresh navigation results.
The cache is automatically invalidated when file contents change, but you can
force invalidation with this tool.""",
        input_schema={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to the repository root (default: current directory)",
                    "default": ".",
                },
            },
            "required": [],
        },
        executor=navigator_invalidate_cache,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    logger.info("Codebase navigator tools registered successfully")
