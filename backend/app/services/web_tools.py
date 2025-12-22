"""
Web tools for AI entities.

Provides web search and content fetching capabilities that AI entities
can use during conversations to gather current information.
"""

import httpx
import json
import logging
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from app.config import settings
from app.services.tool_service import ToolCategory, ToolService

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Constants
BRAVE_SEARCH_API_URL = "https://api.search.brave.com/res/v1/web/search"
SEARCH_TIMEOUT = 10.0  # seconds
FETCH_TIMEOUT = 15.0  # seconds
DEFAULT_NUM_RESULTS = 5
DEFAULT_MAX_LENGTH = 50000


async def web_search(query: str, num_results: int = DEFAULT_NUM_RESULTS) -> str:
    """
    Search the web using Brave Search API.

    Args:
        query: The search query string
        num_results: Number of results to return (default: 5)

    Returns:
        Formatted search results as text, or error message
    """
    api_key = settings.brave_search_api_key
    if not api_key:
        return "Error: Web search is not configured. The BRAVE_SEARCH_API_KEY environment variable is not set."

    # Validate query - Brave API has strict limits
    if not query or not query.strip():
        return "Error: Search query cannot be empty."

    query = query.strip()

    # Brave API limits: max 400 characters, max 50 words
    if len(query) > 400:
        logger.warning(f"Query too long ({len(query)} chars), truncating to 400")
        query = query[:400]

    word_count = len(query.split())
    if word_count > 50:
        logger.warning(f"Query has too many words ({word_count}), truncating to 50")
        words = query.split()[:50]
        query = " ".join(words)

    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
            response = await client.get(
                BRAVE_SEARCH_API_URL,
                headers={
                    "X-Subscription-Token": api_key,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                },
                params={
                    "q": query,
                    "count": min(num_results, 20),  # Brave API limits to 20
                },
            )

            if response.status_code == 401:
                return "Error: Invalid Brave Search API key."
            elif response.status_code == 429:
                return "Error: Brave Search API rate limit exceeded. Please try again later."
            elif response.status_code == 422:
                # 422 Unprocessable Entity - usually validation error
                try:
                    error_data = response.json()
                    error_msg = error_data.get("message", error_data.get("detail", str(error_data)))
                except Exception:
                    error_msg = response.text[:500] if response.text else "Unknown validation error"
                logger.error(f"Brave Search 422 error for query '{query[:100]}...': {error_msg}")
                return f"Error: Search query validation failed - {error_msg}"
            elif response.status_code != 200:
                return f"Error: Brave Search API returned status {response.status_code}"

            data = response.json()

            # Extract web results
            web_results = data.get("web", {}).get("results", [])
            if not web_results:
                return f"No search results found for: {query}"

            # Format results
            formatted_results = []
            for i, result in enumerate(web_results[:num_results], 1):
                title = result.get("title", "No title")
                url = result.get("url", "No URL")
                description = result.get("description", "No description")

                formatted_results.append(
                    f"{i}. {title}\n"
                    f"   URL: {url}\n"
                    f"   {description}"
                )

            output = f"Search results for: {query}\n\n" + "\n\n".join(formatted_results)
            logger.info(f"Web search completed: {len(web_results)} results for '{query}'")
            return output

    except httpx.TimeoutException:
        return f"Error: Search request timed out after {SEARCH_TIMEOUT} seconds."
    except httpx.RequestError as e:
        return f"Error: Failed to connect to search service: {str(e)}"
    except Exception as e:
        logger.exception(f"Unexpected error during web search: {e}")
        return f"Error: An unexpected error occurred during search: {str(e)}"


async def web_fetch(url: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """
    Fetch and extract content from a URL.

    For HTML pages, extracts text content while removing navigation,
    scripts, and other non-content elements.

    Args:
        url: The URL to fetch
        max_length: Maximum content length to return (default: 50000)

    Returns:
        Extracted content as text, or error message
    """
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; HereIAm/1.0; Research Tool)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,text/plain;q=0.8,*/*;q=0.5",
                },
            )

            if response.status_code == 403:
                return f"Error: Access denied (403 Forbidden) for URL: {url}"
            elif response.status_code == 404:
                return f"Error: Page not found (404) for URL: {url}"
            elif response.status_code != 200:
                return f"Error: Failed to fetch URL (status {response.status_code}): {url}"

            content_type = response.headers.get("content-type", "").lower()
            content = response.text

            # Handle JSON content
            if "application/json" in content_type:
                try:
                    json_data = response.json()
                    formatted_json = json.dumps(json_data, indent=2)
                    if len(formatted_json) > max_length:
                        formatted_json = formatted_json[:max_length] + "\n...[truncated]"
                    return f"JSON content from {url}:\n\n{formatted_json}"
                except json.JSONDecodeError:
                    pass  # Fall through to text handling

            # Handle HTML content
            if "text/html" in content_type or content.strip().startswith("<!"):
                soup = BeautifulSoup(content, "html.parser")

                # Remove unwanted elements
                for element in soup.find_all([
                    "script", "style", "nav", "footer", "header",
                    "aside", "noscript", "iframe", "form"
                ]):
                    element.decompose()

                # Try to find main content area
                main_content = None
                for selector in ["main", "article", '[role="main"]', ".content", "#content"]:
                    main_content = soup.select_one(selector)
                    if main_content:
                        break

                # Extract text from main content or body
                if main_content:
                    text = main_content.get_text(separator="\n", strip=True)
                else:
                    body = soup.find("body")
                    if body:
                        text = body.get_text(separator="\n", strip=True)
                    else:
                        text = soup.get_text(separator="\n", strip=True)

                # Clean up whitespace
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                cleaned_text = "\n".join(lines)

                if len(cleaned_text) > max_length:
                    cleaned_text = cleaned_text[:max_length] + "\n...[truncated]"

                # Get page title
                title = soup.find("title")
                title_text = title.get_text(strip=True) if title else "No title"

                output = f"Content from: {url}\nTitle: {title_text}\n\n{cleaned_text}"
                logger.info(f"Web fetch completed: {len(cleaned_text)} chars from '{url}'")
                return output

            # Handle plain text
            if len(content) > max_length:
                content = content[:max_length] + "\n...[truncated]"

            return f"Content from {url}:\n\n{content}"

    except httpx.TimeoutException:
        return f"Error: Request timed out after {FETCH_TIMEOUT} seconds for URL: {url}"
    except httpx.RequestError as e:
        return f"Error: Failed to connect to URL: {str(e)}"
    except Exception as e:
        logger.exception(f"Unexpected error fetching URL: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"


def register_web_tools(tool_service: ToolService) -> None:
    """
    Register web tools with the tool service.

    Args:
        tool_service: The ToolService instance to register with
    """
    # Register web_search tool
    tool_service.register_tool(
        name="web_search",
        description=(
            "Search the web for current information. Use this tool when you need "
            "to find recent news, facts, data, or any information that might be "
            "more current than your training data. Returns a list of search results "
            "with titles, URLs, and descriptions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Be specific and include relevant keywords.",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5, max: 20).",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
        executor=web_search,
        category=ToolCategory.WEB,
        enabled=True,
    )

    # Register web_fetch tool
    tool_service.register_tool(
        name="web_fetch",
        description=(
            "Fetch and read the content of a specific web page. Use this tool when "
            "you have a URL and need to read its content. The tool extracts the main "
            "text content from HTML pages, removing navigation and other non-content "
            "elements. Also handles JSON and plain text content."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch (must include http:// or https://).",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum characters to return (default: 50000).",
                    "default": 50000,
                    "minimum": 1000,
                    "maximum": 100000,
                },
            },
            "required": ["url"],
        },
        executor=web_fetch,
        category=ToolCategory.WEB,
        enabled=True,
    )

    logger.info("Web tools registered: web_search, web_fetch")
