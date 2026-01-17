"""
Unit tests for web tools (web_search and web_fetch).

Tests tool functionality including API interactions, error handling, and content extraction.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json
import httpx

from app.services.web_tools import (
    web_search,
    web_fetch,
    register_web_tools,
    BRAVE_SEARCH_API_URL,
    SEARCH_TIMEOUT,
    FETCH_TIMEOUT,
    DEFAULT_NUM_RESULTS,
    DEFAULT_MAX_LENGTH,
)
from app.services.tool_service import ToolService, ToolCategory


class TestWebSearchValidation:
    """Tests for web_search input validation."""

    @pytest.mark.asyncio
    async def test_empty_query(self):
        """Test that empty query returns error."""
        with patch("app.services.web_tools.settings") as mock_settings:
            mock_settings.brave_search_api_key = "test-key"
            result = await web_search("")
            assert "Error" in result
            assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_whitespace_only_query(self):
        """Test that whitespace-only query returns error."""
        with patch("app.services.web_tools.settings") as mock_settings:
            mock_settings.brave_search_api_key = "test-key"
            result = await web_search("   ")
            assert "Error" in result
            assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_missing_api_key(self):
        """Test behavior when API key is not configured."""
        with patch("app.services.web_tools.settings") as mock_settings:
            mock_settings.brave_search_api_key = ""
            result = await web_search("test query")
            assert "Error" in result
            assert "not configured" in result.lower()

    @pytest.mark.asyncio
    async def test_long_query_truncation(self):
        """Test that long queries are truncated."""
        long_query = "a" * 500  # 500 characters, exceeds 400 limit

        with patch("app.services.web_tools.settings") as mock_settings:
            mock_settings.brave_search_api_key = "test-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"web": {"results": []}}

                mock_instance = AsyncMock()
                mock_instance.get = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                await web_search(long_query)

                # Verify the query was truncated in the request
                call_args = mock_instance.get.call_args
                params = call_args.kwargs.get("params", {})
                assert len(params["q"]) <= 400


class TestWebSearchAPIInteraction:
    """Tests for web_search API interactions."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings with API key."""
        with patch("app.services.web_tools.settings") as mock:
            mock.brave_search_api_key = "test-api-key"
            yield mock

    @pytest.fixture
    def mock_successful_response(self):
        """Create a mock successful API response."""
        return {
            "web": {
                "results": [
                    {
                        "title": "Result 1",
                        "url": "https://example.com/1",
                        "description": "Description 1",
                    },
                    {
                        "title": "Result 2",
                        "url": "https://example.com/2",
                        "description": "Description 2",
                    },
                ]
            }
        }

    @pytest.mark.asyncio
    async def test_successful_search(self, mock_settings, mock_successful_response):
        """Test successful search returns formatted results."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_successful_response

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_search("test query")

            assert "Result 1" in result
            assert "Result 2" in result
            assert "https://example.com/1" in result
            assert "https://example.com/2" in result
            assert "Description 1" in result

    @pytest.mark.asyncio
    async def test_no_results(self, mock_settings):
        """Test handling of no search results."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"web": {"results": []}}

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_search("obscure query")

            assert "No search results" in result

    @pytest.mark.asyncio
    async def test_api_auth_error(self, mock_settings):
        """Test handling of 401 unauthorized."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 401

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_search("test")

            assert "Error" in result
            assert "Invalid" in result or "API key" in result

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, mock_settings):
        """Test handling of 429 rate limit."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 429

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_search("test")

            assert "Error" in result
            assert "rate limit" in result.lower()

    @pytest.mark.asyncio
    async def test_validation_error_422(self, mock_settings):
        """Test handling of 422 validation error."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 422
            mock_response.json.return_value = {"message": "Invalid query"}
            mock_response.text = '{"message": "Invalid query"}'

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_search("test")

            assert "Error" in result
            assert "validation" in result.lower()

    @pytest.mark.asyncio
    async def test_timeout_error(self, mock_settings):
        """Test handling of timeout."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_search("test")

            assert "Error" in result
            assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_connection_error(self, mock_settings):
        """Test handling of connection error."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(
                side_effect=httpx.RequestError("Connection failed")
            )
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_search("test")

            assert "Error" in result
            assert "connect" in result.lower()

    @pytest.mark.asyncio
    async def test_num_results_parameter(self, mock_settings, mock_successful_response):
        """Test that num_results parameter is passed correctly."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_successful_response

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await web_search("test", num_results=10)

            call_args = mock_instance.get.call_args
            params = call_args.kwargs.get("params", {})
            assert params["count"] == 10

    @pytest.mark.asyncio
    async def test_num_results_capped_at_20(self, mock_settings, mock_successful_response):
        """Test that num_results is capped at 20."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_successful_response

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await web_search("test", num_results=50)

            call_args = mock_instance.get.call_args
            params = call_args.kwargs.get("params", {})
            assert params["count"] == 20  # Capped at max


class TestWebFetch:
    """Tests for web_fetch functionality."""

    @pytest.mark.asyncio
    async def test_fetch_html_content(self):
        """Test fetching and extracting HTML content."""
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><title>Test Page</title></head>
        <body>
            <nav>Navigation</nav>
            <main>
                <h1>Main Content</h1>
                <p>This is the main paragraph.</p>
            </main>
            <footer>Footer</footer>
        </body>
        </html>
        """

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/html"}
            mock_response.text = html_content

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://example.com")

            assert "Main Content" in result
            assert "main paragraph" in result
            # Nav and footer should be removed
            assert "Navigation" not in result or result.count("Navigation") == 0

    @pytest.mark.asyncio
    async def test_fetch_json_content(self):
        """Test fetching JSON content."""
        json_data = {"key": "value", "items": [1, 2, 3]}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response.text = json.dumps(json_data)
            mock_response.json.return_value = json_data

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://api.example.com/data")

            assert "JSON content" in result
            assert '"key": "value"' in result

    @pytest.mark.asyncio
    async def test_fetch_plain_text(self):
        """Test fetching plain text content."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/plain"}
            mock_response.text = "Plain text content here."

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://example.com/file.txt")

            assert "Plain text content here." in result

    @pytest.mark.asyncio
    async def test_fetch_403_forbidden(self):
        """Test handling of 403 Forbidden."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 403

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://example.com")

            assert "Error" in result
            assert "403" in result or "Forbidden" in result

    @pytest.mark.asyncio
    async def test_fetch_404_not_found(self):
        """Test handling of 404 Not Found."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://example.com/missing")

            assert "Error" in result
            assert "404" in result or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_fetch_timeout(self):
        """Test handling of timeout."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://slow.example.com")

            assert "Error" in result
            assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_fetch_connection_error(self):
        """Test handling of connection error."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(
                side_effect=httpx.RequestError("Connection refused")
            )
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://unreachable.example.com")

            assert "Error" in result
            assert "connect" in result.lower() or "Failed" in result

    @pytest.mark.asyncio
    async def test_fetch_content_truncation(self):
        """Test that long content is truncated."""
        long_content = "x" * 100000  # 100k characters

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/plain"}
            mock_response.text = long_content

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://example.com", max_length=50000)

            assert len(result) <= 60000  # Some overhead for formatting
            assert "truncated" in result.lower()

    @pytest.mark.asyncio
    async def test_fetch_extracts_title(self):
        """Test that page title is extracted."""
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><title>My Page Title</title></head>
        <body><p>Content</p></body>
        </html>
        """

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/html"}
            mock_response.text = html_content

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://example.com")

            assert "My Page Title" in result

    @pytest.mark.asyncio
    async def test_fetch_removes_scripts(self):
        """Test that script tags are removed."""
        html_content = """
        <!DOCTYPE html>
        <html>
        <body>
            <p>Visible content</p>
            <script>alert('malicious');</script>
        </body>
        </html>
        """

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/html"}
            mock_response.text = html_content

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await web_fetch("https://example.com")

            assert "Visible content" in result
            assert "malicious" not in result
            assert "<script>" not in result


class TestWebToolRegistration:
    """Tests for web tool registration."""

    def test_register_web_tools(self):
        """Test that web tools are registered correctly."""
        service = ToolService()
        register_web_tools(service)

        # Check web_search tool
        search_tool = service.get_tool("web_search")
        assert search_tool is not None
        assert search_tool.category == ToolCategory.WEB
        assert search_tool.enabled is True
        assert "search" in search_tool.description.lower()

        # Check web_fetch tool
        fetch_tool = service.get_tool("web_fetch")
        assert fetch_tool is not None
        assert fetch_tool.category == ToolCategory.WEB
        assert fetch_tool.enabled is True
        assert "fetch" in fetch_tool.description.lower() or "read" in fetch_tool.description.lower()

    def test_web_search_schema(self):
        """Test web_search tool schema."""
        service = ToolService()
        register_web_tools(service)

        tool = service.get_tool("web_search")
        schema = tool.input_schema

        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "query" in schema["required"]
        assert "num_results" in schema["properties"]

    def test_web_fetch_schema(self):
        """Test web_fetch tool schema."""
        service = ToolService()
        register_web_tools(service)

        tool = service.get_tool("web_fetch")
        schema = tool.input_schema

        assert schema["type"] == "object"
        assert "url" in schema["properties"]
        assert "url" in schema["required"]
        assert "max_length" in schema["properties"]


class TestConstants:
    """Tests for module constants."""

    def test_brave_api_url(self):
        """Test Brave API URL constant."""
        assert BRAVE_SEARCH_API_URL == "https://api.search.brave.com/res/v1/web/search"

    def test_timeouts(self):
        """Test timeout constants."""
        assert SEARCH_TIMEOUT == 10.0
        assert FETCH_TIMEOUT == 15.0

    def test_defaults(self):
        """Test default constants."""
        assert DEFAULT_NUM_RESULTS == 5
        assert DEFAULT_MAX_LENGTH == 50000
