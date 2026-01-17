"""
Unit tests for the Tool Service.

Tests tool registration, execution, filtering, and error handling.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
import asyncio

from app.services.tool_service import (
    ToolService,
    ToolCategory,
    ToolResult,
    ToolDefinition,
)


@pytest.fixture
def tool_service():
    """Create a fresh ToolService instance for each test."""
    return ToolService()


@pytest.fixture
def sample_executor():
    """Create a sample async executor function."""
    async def executor(query: str, num_results: int = 5) -> str:
        return f"Results for '{query}' (count: {num_results})"
    return executor


@pytest.fixture
def failing_executor():
    """Create an executor that raises an exception."""
    async def executor(query: str) -> str:
        raise ValueError("Test error: something went wrong")
    return executor


class TestToolRegistration:
    """Tests for tool registration functionality."""

    def test_register_tool_basic(self, tool_service, sample_executor):
        """Test registering a tool with basic parameters."""
        tool_service.register_tool(
            name="test_tool",
            description="A test tool",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
            executor=sample_executor,
        )

        tool = tool_service.get_tool("test_tool")
        assert tool is not None
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert tool.enabled is True
        assert tool.category == ToolCategory.UTILITY

    def test_register_tool_with_category(self, tool_service, sample_executor):
        """Test registering a tool with a specific category."""
        tool_service.register_tool(
            name="web_tool",
            description="A web tool",
            input_schema={"type": "object", "properties": {}},
            executor=sample_executor,
            category=ToolCategory.WEB,
        )

        tool = tool_service.get_tool("web_tool")
        assert tool.category == ToolCategory.WEB

    def test_register_tool_disabled(self, tool_service, sample_executor):
        """Test registering a disabled tool."""
        tool_service.register_tool(
            name="disabled_tool",
            description="A disabled tool",
            input_schema={"type": "object", "properties": {}},
            executor=sample_executor,
            enabled=False,
        )

        tool = tool_service.get_tool("disabled_tool")
        assert tool is not None
        assert tool.enabled is False

    def test_register_tool_overwrites_existing(self, tool_service, sample_executor):
        """Test that registering a tool with same name overwrites."""
        tool_service.register_tool(
            name="duplicate",
            description="First version",
            input_schema={"type": "object"},
            executor=sample_executor,
        )

        tool_service.register_tool(
            name="duplicate",
            description="Second version",
            input_schema={"type": "object"},
            executor=sample_executor,
        )

        tool = tool_service.get_tool("duplicate")
        assert tool.description == "Second version"

    def test_unregister_tool(self, tool_service, sample_executor):
        """Test unregistering a tool."""
        tool_service.register_tool(
            name="to_remove",
            description="Tool to remove",
            input_schema={"type": "object"},
            executor=sample_executor,
        )

        # Verify it exists
        assert tool_service.get_tool("to_remove") is not None

        # Unregister
        result = tool_service.unregister_tool("to_remove")
        assert result is True

        # Verify it's gone
        assert tool_service.get_tool("to_remove") is None

    def test_unregister_nonexistent_tool(self, tool_service):
        """Test unregistering a tool that doesn't exist."""
        result = tool_service.unregister_tool("nonexistent")
        assert result is False

    def test_get_nonexistent_tool(self, tool_service):
        """Test getting a tool that doesn't exist."""
        tool = tool_service.get_tool("nonexistent")
        assert tool is None


class TestToolListing:
    """Tests for listing tools with filtering."""

    @pytest.fixture
    def service_with_tools(self, tool_service, sample_executor):
        """Create a service with multiple registered tools."""
        # Register web tools
        tool_service.register_tool(
            name="web_search",
            description="Search the web",
            input_schema={"type": "object"},
            executor=sample_executor,
            category=ToolCategory.WEB,
            enabled=True,
        )
        tool_service.register_tool(
            name="web_fetch",
            description="Fetch a URL",
            input_schema={"type": "object"},
            executor=sample_executor,
            category=ToolCategory.WEB,
            enabled=True,
        )

        # Register memory tools
        tool_service.register_tool(
            name="memory_query",
            description="Query memories",
            input_schema={"type": "object"},
            executor=sample_executor,
            category=ToolCategory.MEMORY,
            enabled=True,
        )

        # Register disabled tool
        tool_service.register_tool(
            name="disabled_tool",
            description="Disabled",
            input_schema={"type": "object"},
            executor=sample_executor,
            category=ToolCategory.UTILITY,
            enabled=False,
        )

        return tool_service

    def test_list_all_enabled_tools(self, service_with_tools):
        """Test listing all enabled tools."""
        tools = service_with_tools.list_tools()
        assert len(tools) == 3  # Excludes disabled tool
        names = {t.name for t in tools}
        assert "disabled_tool" not in names

    def test_list_all_tools_including_disabled(self, service_with_tools):
        """Test listing all tools including disabled."""
        tools = service_with_tools.list_tools(enabled_only=False)
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert "disabled_tool" in names

    def test_list_tools_by_category(self, service_with_tools):
        """Test filtering tools by category."""
        web_tools = service_with_tools.list_tools(categories=[ToolCategory.WEB])
        assert len(web_tools) == 2
        for tool in web_tools:
            assert tool.category == ToolCategory.WEB

    def test_list_tools_by_multiple_categories(self, service_with_tools):
        """Test filtering by multiple categories."""
        tools = service_with_tools.list_tools(
            categories=[ToolCategory.WEB, ToolCategory.MEMORY]
        )
        assert len(tools) == 3

    def test_list_tools_empty_category(self, service_with_tools):
        """Test listing with a category that has no tools."""
        tools = service_with_tools.list_tools(categories=[ToolCategory.GITHUB])
        assert len(tools) == 0


class TestToolSchemas:
    """Tests for generating tool schemas."""

    @pytest.fixture
    def service_with_schemas(self, tool_service, sample_executor):
        """Create a service with tools that have full schemas."""
        tool_service.register_tool(
            name="search",
            description="Search for information",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
            executor=sample_executor,
            category=ToolCategory.WEB,
        )
        return tool_service

    def test_get_tool_schemas(self, service_with_schemas):
        """Test generating schemas in Anthropic format."""
        schemas = service_with_schemas.get_tool_schemas()
        assert len(schemas) == 1

        schema = schemas[0]
        assert schema["name"] == "search"
        assert schema["description"] == "Search for information"
        assert "input_schema" in schema
        assert schema["input_schema"]["type"] == "object"
        assert "query" in schema["input_schema"]["properties"]

    def test_get_tool_schemas_by_category(self, service_with_schemas, sample_executor):
        """Test filtering schemas by category."""
        # Add a non-web tool
        service_with_schemas.register_tool(
            name="other",
            description="Other tool",
            input_schema={"type": "object"},
            executor=sample_executor,
            category=ToolCategory.UTILITY,
        )

        web_schemas = service_with_schemas.get_tool_schemas(
            categories=[ToolCategory.WEB]
        )
        assert len(web_schemas) == 1
        assert web_schemas[0]["name"] == "search"


class TestToolExecution:
    """Tests for tool execution."""

    @pytest.mark.asyncio
    async def test_execute_tool_success(self, tool_service, sample_executor):
        """Test successful tool execution."""
        tool_service.register_tool(
            name="test_tool",
            description="Test",
            input_schema={"type": "object"},
            executor=sample_executor,
        )

        result = await tool_service.execute_tool(
            tool_use_id="test-123",
            tool_name="test_tool",
            tool_input={"query": "hello", "num_results": 3},
        )

        assert isinstance(result, ToolResult)
        assert result.tool_use_id == "test-123"
        assert result.is_error is False
        assert "hello" in result.content
        assert "3" in result.content

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self, tool_service):
        """Test executing a non-existent tool."""
        result = await tool_service.execute_tool(
            tool_use_id="test-123",
            tool_name="nonexistent",
            tool_input={},
        )

        assert result.is_error is True
        assert "not found" in result.content.lower()

    @pytest.mark.asyncio
    async def test_execute_disabled_tool(self, tool_service, sample_executor):
        """Test executing a disabled tool."""
        tool_service.register_tool(
            name="disabled",
            description="Disabled tool",
            input_schema={"type": "object"},
            executor=sample_executor,
            enabled=False,
        )

        result = await tool_service.execute_tool(
            tool_use_id="test-123",
            tool_name="disabled",
            tool_input={},
        )

        assert result.is_error is True
        assert "disabled" in result.content.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_with_error(self, tool_service, failing_executor):
        """Test handling executor errors."""
        tool_service.register_tool(
            name="failing",
            description="Failing tool",
            input_schema={"type": "object"},
            executor=failing_executor,
        )

        result = await tool_service.execute_tool(
            tool_use_id="test-123",
            tool_name="failing",
            tool_input={"query": "test"},
        )

        assert result.is_error is True
        assert "error" in result.content.lower()
        assert "something went wrong" in result.content.lower()


class TestBatchExecution:
    """Tests for executing multiple tools in parallel."""

    @pytest.mark.asyncio
    async def test_execute_tools_parallel(self, tool_service):
        """Test executing multiple tools in parallel."""
        call_order = []

        async def slow_executor(name: str) -> str:
            call_order.append(f"start-{name}")
            await asyncio.sleep(0.01)  # Small delay
            call_order.append(f"end-{name}")
            return f"Result for {name}"

        tool_service.register_tool(
            name="tool_a",
            description="Tool A",
            input_schema={"type": "object"},
            executor=slow_executor,
        )
        tool_service.register_tool(
            name="tool_b",
            description="Tool B",
            input_schema={"type": "object"},
            executor=slow_executor,
        )

        results = await tool_service.execute_tools([
            {"id": "1", "name": "tool_a", "input": {"name": "A"}},
            {"id": "2", "name": "tool_b", "input": {"name": "B"}},
        ])

        assert len(results) == 2
        assert results[0].content == "Result for A"
        assert results[1].content == "Result for B"

        # Verify parallel execution: both should start before either ends
        assert call_order[0].startswith("start")
        assert call_order[1].startswith("start")

    @pytest.mark.asyncio
    async def test_execute_tools_empty_list(self, tool_service):
        """Test executing an empty list of tools."""
        results = await tool_service.execute_tools([])
        assert results == []

    @pytest.mark.asyncio
    async def test_execute_tools_with_missing_input(self, tool_service, sample_executor):
        """Test executing tools with missing input field."""
        tool_service.register_tool(
            name="test",
            description="Test",
            input_schema={"type": "object"},
            executor=sample_executor,
        )

        results = await tool_service.execute_tools([
            {"id": "1", "name": "test"},  # Missing 'input' key
        ])

        assert len(results) == 1
        # Should still execute with empty input


class TestToolEnableDisable:
    """Tests for enabling/disabling tools."""

    def test_set_tool_enabled(self, tool_service, sample_executor):
        """Test enabling and disabling a tool."""
        tool_service.register_tool(
            name="toggle_tool",
            description="Toggleable",
            input_schema={"type": "object"},
            executor=sample_executor,
            enabled=True,
        )

        # Disable
        result = tool_service.set_tool_enabled("toggle_tool", False)
        assert result is True
        assert tool_service.get_tool("toggle_tool").enabled is False

        # Re-enable
        result = tool_service.set_tool_enabled("toggle_tool", True)
        assert result is True
        assert tool_service.get_tool("toggle_tool").enabled is True

    def test_set_tool_enabled_nonexistent(self, tool_service):
        """Test enabling/disabling a non-existent tool."""
        result = tool_service.set_tool_enabled("nonexistent", True)
        assert result is False


class TestToolCategories:
    """Tests for tool categories."""

    def test_tool_category_values(self):
        """Test that all expected categories exist."""
        assert ToolCategory.WEB.value == "web"
        assert ToolCategory.MEMORY.value == "memory"
        assert ToolCategory.UTILITY.value == "utility"
        assert ToolCategory.GITHUB.value == "github"

    def test_tool_category_is_string_enum(self):
        """Test that categories are string enums."""
        assert isinstance(ToolCategory.WEB, str)
        assert ToolCategory.WEB == "web"


class TestToolDefinition:
    """Tests for ToolDefinition dataclass."""

    def test_tool_definition_defaults(self, sample_executor):
        """Test ToolDefinition default values."""
        tool = ToolDefinition(
            name="test",
            description="Test tool",
            input_schema={"type": "object"},
            executor=sample_executor,
            category=ToolCategory.UTILITY,
        )

        assert tool.enabled is True

    def test_tool_result_defaults(self):
        """Test ToolResult default values."""
        result = ToolResult(
            tool_use_id="123",
            content="Test content",
        )

        assert result.is_error is False
