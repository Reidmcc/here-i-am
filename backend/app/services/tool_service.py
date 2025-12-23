"""
Tool Service for managing AI tool use capabilities.

This service provides:
- Tool registration with schemas and executor functions
- Tool schema generation in Anthropic format
- Tool execution with error handling
- Batch tool execution with async parallelization
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Awaitable
import logging

logger = logging.getLogger(__name__)


class ToolCategory(str, Enum):
    """Categories for organizing tools."""
    WEB = "web"
    MEMORY = "memory"
    UTILITY = "utility"
    GITHUB = "github"


@dataclass
class ToolResult:
    """Result from a tool execution."""
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class ToolDefinition:
    """Definition of a tool including its schema and executor."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    executor: Callable[..., Awaitable[str]]
    category: ToolCategory
    enabled: bool = True


class ToolService:
    """
    Service for managing tool registration and execution.

    Tools allow AI entities to perform actions like web searches,
    content fetching, and other external operations during conversations.
    """

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        logger.info("ToolService initialized")

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        executor: Callable[..., Awaitable[str]],
        category: ToolCategory = ToolCategory.UTILITY,
        enabled: bool = True,
    ) -> None:
        """
        Register a new tool.

        Args:
            name: Unique tool name (snake_case recommended)
            description: Human-readable description for the AI
            input_schema: JSON Schema for the tool's input parameters
            executor: Async function that executes the tool
            category: Tool category for organization
            enabled: Whether the tool is enabled by default
        """
        if name in self._tools:
            logger.warning(f"Tool '{name}' already registered, overwriting")

        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            executor=executor,
            category=category,
            enabled=enabled,
        )
        logger.info(f"Registered tool: {name} (category={category.value}, enabled={enabled})")

    def unregister_tool(self, name: str) -> bool:
        """
        Unregister a tool.

        Args:
            name: Tool name to unregister

        Returns:
            True if tool was removed, False if not found
        """
        if name in self._tools:
            del self._tools[name]
            logger.info(f"Unregistered tool: {name}")
            return True
        return False

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Get a tool definition by name."""
        return self._tools.get(name)

    def list_tools(
        self,
        categories: Optional[List[ToolCategory]] = None,
        enabled_only: bool = True,
    ) -> List[ToolDefinition]:
        """
        List registered tools.

        Args:
            categories: Filter by categories (None = all)
            enabled_only: Only return enabled tools

        Returns:
            List of matching tool definitions
        """
        tools = list(self._tools.values())

        if enabled_only:
            tools = [t for t in tools if t.enabled]

        if categories:
            category_set: Set[ToolCategory] = set(categories)
            tools = [t for t in tools if t.category in category_set]

        return tools

    def get_tool_schemas(
        self,
        categories: Optional[List[ToolCategory]] = None,
        enabled_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Get tool schemas in Anthropic API format.

        Args:
            categories: Filter by categories (None = all)
            enabled_only: Only return enabled tools

        Returns:
            List of tool schemas ready for Anthropic API
        """
        tools = self.list_tools(categories=categories, enabled_only=enabled_only)

        schemas = []
        for tool in tools:
            schema = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            schemas.append(schema)

        return schemas

    async def execute_tool(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> ToolResult:
        """
        Execute a single tool.

        Args:
            tool_use_id: Unique ID for this tool use
            tool_name: Name of the tool to execute
            tool_input: Input parameters for the tool

        Returns:
            ToolResult with the execution result or error
        """
        tool = self._tools.get(tool_name)

        if not tool:
            logger.error(f"Tool not found: {tool_name}")
            return ToolResult(
                tool_use_id=tool_use_id,
                content=f"Error: Tool '{tool_name}' not found",
                is_error=True,
            )

        if not tool.enabled:
            logger.warning(f"Tool disabled: {tool_name}")
            return ToolResult(
                tool_use_id=tool_use_id,
                content=f"Error: Tool '{tool_name}' is currently disabled",
                is_error=True,
            )

        try:
            logger.info(f"Executing tool: {tool_name} with input: {tool_input}")
            result = await tool.executor(**tool_input)
            logger.info(f"Tool {tool_name} completed successfully (result length: {len(result)})")
            return ToolResult(
                tool_use_id=tool_use_id,
                content=result,
                is_error=False,
            )
        except Exception as e:
            error_msg = f"Error executing tool '{tool_name}': {str(e)}"
            logger.exception(error_msg)
            return ToolResult(
                tool_use_id=tool_use_id,
                content=error_msg,
                is_error=True,
            )

    async def execute_tools(
        self,
        tool_calls: List[Dict[str, Any]],
    ) -> List[ToolResult]:
        """
        Execute multiple tools in parallel.

        Args:
            tool_calls: List of tool calls, each with:
                - id: Tool use ID
                - name: Tool name
                - input: Tool input parameters

        Returns:
            List of ToolResults in the same order as input
        """
        if not tool_calls:
            return []

        # Create tasks for parallel execution
        tasks = []
        for call in tool_calls:
            task = self.execute_tool(
                tool_use_id=call["id"],
                tool_name=call["name"],
                tool_input=call.get("input", {}),
            )
            tasks.append(task)

        # Execute all tools in parallel
        results = await asyncio.gather(*tasks, return_exceptions=False)

        return list(results)

    def set_tool_enabled(self, name: str, enabled: bool) -> bool:
        """
        Enable or disable a tool.

        Args:
            name: Tool name
            enabled: Whether to enable or disable

        Returns:
            True if tool was found and updated, False otherwise
        """
        tool = self._tools.get(name)
        if tool:
            tool.enabled = enabled
            logger.info(f"Tool '{name}' {'enabled' if enabled else 'disabled'}")
            return True
        return False


# Singleton instance
tool_service = ToolService()
