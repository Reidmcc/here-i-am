"""
Web tools for AI entities.

Provides web search and content fetching capabilities that AI entities
can use during conversations to gather current information.

Includes JavaScript rendering support via Playwright for single-page applications.
"""

import asyncio
import httpx
import json
import logging
from typing import TYPE_CHECKING, Optional, Tuple

from bs4 import BeautifulSoup

from app.config import settings
from app.services.tool_service import ToolCategory, ToolService

# Try to import Playwright for JavaScript rendering support
# Gracefully handle if not installed
# We use the sync API + asyncio.to_thread() to avoid event loop conflicts on Windows
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Constants
BRAVE_SEARCH_API_URL = "https://api.search.brave.com/res/v1/web/search"
SEARCH_TIMEOUT = 10.0  # seconds
FETCH_TIMEOUT = 15.0  # seconds
PLAYWRIGHT_TIMEOUT = 20000  # milliseconds (20 seconds for navigation)
PLAYWRIGHT_HARD_TIMEOUT = 45.0  # seconds - absolute maximum for entire Playwright operation
NETWORK_IDLE_TIMEOUT = 500  # milliseconds to wait for network idle after navigation
DEFAULT_NUM_RESULTS = 5
DEFAULT_MAX_LENGTH = 50000

# Minimum text length to consider a page properly rendered
# Pages with less text than this may need JavaScript rendering
MIN_CONTENT_LENGTH = 100

# SPA framework container IDs that suggest JavaScript rendering is needed
SPA_CONTAINER_IDS = ["root", "app", "__next", "__nuxt", "___gatsby"]

# Loading indicators that suggest the page hasn't fully rendered
LOADING_INDICATORS = [
    "loading...",
    "please wait",
    "javascript is required",
    "enable javascript",
    "javascript must be enabled",
    "this page requires javascript",
]


def _needs_javascript_rendering(html_content: str, extracted_text: str) -> Tuple[bool, str]:
    """
    Detect if a page likely needs JavaScript rendering.

    Analyzes the HTML content and extracted text for indicators that
    the page is a single-page application (SPA) that hasn't rendered
    its content yet.

    Args:
        html_content: Raw HTML content from httpx
        extracted_text: Text extracted from the HTML

    Returns:
        Tuple of (needs_rendering: bool, reason: str)
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Check 1: Very little text content
    text_length = len(extracted_text.strip())
    if text_length < MIN_CONTENT_LENGTH:
        # Check if there's significant JavaScript but minimal content
        scripts = soup.find_all("script")
        if len(scripts) > 2:  # More than 2 script tags suggests JS-heavy page
            return True, f"minimal content ({text_length} chars) with {len(scripts)} script tags"

    # Check 2: SPA container with minimal or no content
    for container_id in SPA_CONTAINER_IDS:
        container = soup.find(id=container_id)
        if container:
            container_text = container.get_text(strip=True)
            # Empty or near-empty SPA container
            if len(container_text) < MIN_CONTENT_LENGTH:
                return True, f"empty SPA container (id='{container_id}')"

    # Check 3: Loading indicators in the text
    text_lower = extracted_text.lower()
    for indicator in LOADING_INDICATORS:
        if indicator in text_lower:
            # Make sure this is significant (not just mentioned in passing)
            if text_length < 500 or text_lower.count(indicator) > 0:
                return True, f"loading indicator found: '{indicator}'"

    # Check 4: Noscript tag with meaningful content suggests JS-dependent page
    noscript = soup.find("noscript")
    if noscript:
        noscript_text = noscript.get_text(strip=True)
        if "javascript" in noscript_text.lower() or "enable" in noscript_text.lower():
            # Page has a noscript warning about JavaScript
            if text_length < 500:
                return True, "noscript warning found with minimal content"

    # Check 5: Data attributes suggesting React/Vue/Angular hydration needed
    hydration_attrs = ["data-reactroot", "data-react-helmet", "ng-app", "v-cloak"]
    for attr in hydration_attrs:
        if soup.find(attrs={attr: True}):
            if text_length < MIN_CONTENT_LENGTH:
                return True, f"hydration attribute '{attr}' found with minimal content"

    return False, "page appears to be static HTML"


def _fetch_with_playwright_sync(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Synchronous Playwright fetch - runs in a separate thread.

    Uses Playwright's sync API to avoid event loop conflicts on Windows.
    This function is called via asyncio.to_thread() from the async wrapper.

    Args:
        url: The URL to fetch

    Returns:
        Tuple of (html_content, error_message)
        On success: (html_content, None)
        On failure: (None, error_message)
    """
    try:
        logger.debug(f"Playwright: Starting browser for {url}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            logger.debug("Playwright: Browser launched successfully")

            try:
                # Create a new context with a reasonable viewport
                context = browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                )
                page = context.new_page()
                logger.debug(f"Playwright: Page created, navigating to {url}")

                # Try to navigate with networkidle first (best for SPAs)
                # But use a shorter timeout since networkidle can hang on some pages
                try:
                    page.goto(
                        url,
                        timeout=PLAYWRIGHT_TIMEOUT,
                        wait_until="networkidle",
                    )
                    logger.debug("Playwright: Navigation completed (networkidle)")
                except Exception as nav_error:
                    # If networkidle times out, try again with just domcontentloaded
                    # This is faster but may miss some dynamic content
                    logger.warning(f"Playwright: networkidle failed ({type(nav_error).__name__}), retrying with domcontentloaded")
                    page.goto(
                        url,
                        timeout=PLAYWRIGHT_TIMEOUT,
                        wait_until="domcontentloaded",
                    )
                    # Give JavaScript a bit more time to render after DOM is ready
                    page.wait_for_timeout(2000)
                    logger.debug("Playwright: Navigation completed (domcontentloaded + wait)")

                # Additional small wait to ensure any final rendering is complete
                page.wait_for_timeout(NETWORK_IDLE_TIMEOUT)

                # Get the rendered HTML
                html_content = page.content()
                content_length = len(html_content) if html_content else 0
                logger.debug(f"Playwright: Got content, {content_length} chars")

                context.close()
                return html_content, None

            finally:
                browser.close()

    except Exception as e:
        # Get detailed error information
        error_type = type(e).__name__
        error_msg = str(e) if str(e) else repr(e)

        logger.error(f"Playwright error ({error_type}) fetching {url}: {error_msg}")

        # Provide more helpful error messages based on error type/content
        if "Executable doesn't exist" in error_msg or "browserType.launch" in error_msg:
            return None, "Playwright browsers not installed. Run: playwright install chromium"
        elif "Timeout" in error_msg or "Timeout" in error_type:
            return None, f"Page load timed out after {PLAYWRIGHT_TIMEOUT // 1000} seconds"
        elif not error_msg or error_msg == "None" or error_msg == f"{error_type}()":
            return None, f"JavaScript rendering failed: {error_type} (no details available)"
        else:
            return None, f"JavaScript rendering failed ({error_type}): {error_msg}"


async def _fetch_with_playwright(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch a URL using Playwright with JavaScript rendering.

    Launches a headless Chromium browser, navigates to the URL,
    waits for the page to load, and returns the rendered HTML.

    Uses Playwright's sync API in a separate thread to avoid event loop
    conflicts on Windows. Wrapped with a hard timeout to prevent indefinite hangs.

    Args:
        url: The URL to fetch

    Returns:
        Tuple of (html_content, error_message)
        On success: (html_content, None)
        On failure: (None, error_message)
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None, "Playwright is not installed. Install with: pip install playwright && playwright install chromium"

    # Run the synchronous Playwright code in a separate thread
    # This avoids event loop conflicts that cause NotImplementedError on Windows
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_with_playwright_sync, url),
            timeout=PLAYWRIGHT_HARD_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error(f"Playwright: Hard timeout ({PLAYWRIGHT_HARD_TIMEOUT}s) exceeded for {url}")
        return None, f"Page rendering timed out after {PLAYWRIGHT_HARD_TIMEOUT} seconds (page may have continuous network activity)"
    except asyncio.CancelledError:
        logger.warning(f"Playwright: Operation cancelled for {url}")
        return None, "Page rendering was cancelled"


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


def _extract_html_content(html_content: str, url: str) -> Tuple[str, str, str]:
    """
    Extract text content from HTML.

    Args:
        html_content: Raw HTML content
        url: The URL (for logging)

    Returns:
        Tuple of (cleaned_text, title_text, raw_extracted_text)
        raw_extracted_text is before whitespace cleanup (for detection)
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Get title before removing elements
    title = soup.find("title")
    title_text = title.get_text(strip=True) if title else "No title"

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

    raw_text = text  # Save for detection before cleanup

    # Clean up whitespace
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    cleaned_text = "\n".join(lines)

    return cleaned_text, title_text, raw_text


async def web_fetch(url: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """
    Fetch and extract content from a URL.

    For HTML pages, extracts text content while removing navigation,
    scripts, and other non-content elements. Automatically detects
    JavaScript-rendered pages (SPAs) and uses Playwright for rendering
    when needed.

    The hybrid approach:
    1. First attempts a fast fetch using httpx
    2. Analyzes the response for SPA indicators (empty containers, loading text)
    3. Falls back to Playwright rendering if JavaScript execution is needed

    Args:
        url: The URL to fetch
        max_length: Maximum content length to return (default: 50000)

    Returns:
        Extracted content as text, or error message
    """
    used_playwright = False

    try:
        # Step 1: Fast fetch with httpx
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

            # Handle JSON content (no JavaScript rendering needed)
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
                # Extract text from the initial HTML
                cleaned_text, title_text, raw_text = _extract_html_content(content, url)

                # Step 2: Check if JavaScript rendering is needed
                needs_js, reason = _needs_javascript_rendering(content, raw_text)

                if needs_js:
                    logger.info(f"SPA detected for {url}: {reason}. Attempting Playwright render.")

                    # Step 3: Fall back to Playwright
                    rendered_html, error = await _fetch_with_playwright(url)

                    if error:
                        # Playwright failed - return what we have with a note
                        logger.warning(f"Playwright rendering failed for {url}: {error}")
                        note = f"\n\n[Note: This page appears to require JavaScript ({reason}), but rendering failed: {error}]"

                        if len(cleaned_text) > max_length:
                            cleaned_text = cleaned_text[:max_length] + "\n...[truncated]"

                        output = f"Content from: {url}\nTitle: {title_text}\n\n{cleaned_text}{note}"
                        return output
                    else:
                        # Re-extract content from rendered HTML
                        cleaned_text, title_text, _ = _extract_html_content(rendered_html, url)
                        used_playwright = True
                        logger.info(f"Playwright render successful for {url}: {len(cleaned_text)} chars")

                if len(cleaned_text) > max_length:
                    cleaned_text = cleaned_text[:max_length] + "\n...[truncated]"

                # Add note about JavaScript rendering if used
                render_note = " [JavaScript rendered]" if used_playwright else ""
                output = f"Content from: {url}{render_note}\nTitle: {title_text}\n\n{cleaned_text}"
                logger.info(f"Web fetch completed: {len(cleaned_text)} chars from '{url}'{render_note}")
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
            "elements. Also handles JSON and plain text content. Automatically detects "
            "and renders JavaScript-heavy pages (SPAs) using a headless browser when needed."
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
