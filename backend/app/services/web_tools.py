"""
Web tools for AI entities.

Provides web search and content fetching capabilities that AI entities
can use during conversations to gather current information.

Includes JavaScript rendering support via Playwright for single-page applications.
"""

import asyncio
import httpx
import ipaddress
import json
import logging
import socket
from typing import TYPE_CHECKING, List, Optional, Tuple
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.config import settings
from app.services.tool_service import ToolCategory, ToolService, wrap_untrusted_content

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

# User-Agent for both httpx and Playwright fetches. Bot walls (Cloudflare
# et al.) reject anything that self-identifies as a non-browser client, so we
# present as an ordinary browser — this tool is an entity reading the web,
# not a crawler.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Statuses bot walls use to turn away plain HTTP clients. Worth retrying with
# a real headless browser before giving up.
BOT_BLOCK_STATUS_CODES = {403, 429}

# Phrases that mark an anti-bot interstitial rather than real page content
BLOCK_PAGE_INDICATORS = [
    "access denied",
    "just a moment",
    "attention required",
    "verify you are human",
    "you have been blocked",
    "enable javascript and cookies",
    "checking your browser",
]
SEARCH_TIMEOUT = 10.0  # seconds
FETCH_TIMEOUT = 15.0  # seconds
PLAYWRIGHT_TIMEOUT = 60000  # milliseconds (60 seconds for navigation)
PLAYWRIGHT_HARD_TIMEOUT = 90.0  # seconds - absolute maximum for entire Playwright operation
NETWORK_IDLE_TIMEOUT = 500  # milliseconds to wait for network idle after navigation
DEFAULT_NUM_RESULTS = 5
DEFAULT_MAX_LENGTH = 50000

# Minimum text length to consider a page properly rendered
# Pages with less text than this may need JavaScript rendering
MIN_CONTENT_LENGTH = 100

# web_fetch is "read the open web". Only these schemes are honoured; anything
# else (file:, ftp:, gopher:, ...) is refused before a request is made.
ALLOWED_URL_SCHEMES = {"http", "https"}

# Maximum redirect hops. Redirects are followed manually so each hop's
# destination can be re-validated - a redirect is otherwise a way to reach a
# blocked address from an allowed one.
MAX_REDIRECTS = 5

# Redirect statuses handled by the manual redirect loop
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}

# RFC 6598 carrier-grade NAT space. It is not the public internet, but
# ipaddress only classifies it as private from Python 3.13 on, so it is
# checked explicitly rather than left to the version in use.
SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")

# Resource types blocked in the Playwright browser for faster loading
# (only HTML/JS is needed for text extraction)
PLAYWRIGHT_BLOCKED_RESOURCE_TYPES = {"image", "font", "stylesheet", "media", "imageset"}

# Common tracking/analytics domains blocked in the Playwright browser
PLAYWRIGHT_BLOCKED_DOMAINS = {
    "google-analytics.com", "googletagmanager.com", "facebook.net",
    "doubleclick.net", "analytics.", "tracking.", "ads.", "adservice.",
    "hotjar.com", "mixpanel.com", "segment.io", "amplitude.com",
}

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


def _is_blocked_address(ip: ipaddress._BaseAddress) -> bool:
    """
    Whether an IP address is outside the public internet.

    Blocks loopback, private, link-local (including the 169.254.169.254 cloud
    metadata endpoint), multicast, reserved and unspecified ranges, plus
    IPv4-mapped IPv6 forms of the same.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour

    if isinstance(ip, ipaddress.IPv4Address) and ip in SHARED_ADDRESS_SPACE:
        return True

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_fetch_url(url: str) -> Optional[str]:
    """
    Check that a URL is a public-internet HTTP(S) target.

    web_fetch is scoped to the open web, but the process runs alongside the
    application's own unauthenticated API (and, in a cloud deployment, a
    metadata service). Without this check the tool doubles as a request
    forgery primitive: an entity could read every conversation in the
    deployment via http://localhost:8000/api/... and exfiltrate it with a
    second fetch. Hostnames are resolved here so a name that points at a
    private address is rejected too.

    Blocking (does DNS); call it via _validate_fetch_url_async from async
    code. Known limit: httpx resolves the name again when it connects, so a
    hostile DNS server that returns a public address here and a private one
    there could still slip through. Closing that would mean pinning the
    resolved address into the connection with a custom transport; it is not
    addressed here because the threat model is entity misuse and page-borne
    prompt injection, not an attacker operating their own resolver.

    Args:
        url: The URL to validate

    Returns:
        None if the URL is allowed, otherwise an error message
    """
    try:
        parts = urlsplit(url)
    except ValueError as e:
        return f"Error: Malformed URL: {e}"

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        return (
            f"Error: Unsupported URL scheme '{scheme or 'none'}'. "
            f"web_fetch only supports http:// and https:// URLs."
        )

    hostname = parts.hostname
    if not hostname:
        return f"Error: URL has no host: {url}"

    # Literal IP, or a name that has to be resolved first.
    try:
        addresses: List[ipaddress._BaseAddress] = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(hostname, parts.port or (443 if scheme == "https" else 80),
                                          proto=socket.IPPROTO_TCP)
        except socket.gaierror as e:
            return f"Error: Could not resolve host '{hostname}': {e}"
        addresses = []
        for info in resolved:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        if not addresses:
            return f"Error: Could not resolve host '{hostname}' to an IP address."

    for ip in addresses:
        if _is_blocked_address(ip):
            logger.warning(f"Blocked web_fetch to non-public address {ip} for URL: {url}")
            return (
                f"Error: Refusing to fetch '{url}'. It resolves to {ip}, which is not "
                f"a public internet address. web_fetch can only read the open web."
            )

    return None


async def _validate_fetch_url_async(url: str) -> Optional[str]:
    """
    Async wrapper for _validate_fetch_url.

    The address check resolves DNS, which would otherwise block the event
    loop for the duration of the lookup.
    """
    return await asyncio.to_thread(_validate_fetch_url, url)


async def _get_following_redirects(
    client: httpx.AsyncClient,
    url: str,
) -> Tuple[Optional[httpx.Response], str, Optional[str]]:
    """
    GET a URL, following redirects manually so every hop is revalidated.

    httpx's own follow_redirects would happily walk from a public URL to
    127.0.0.1 or the cloud metadata address, which would defeat the check in
    _validate_fetch_url. Each Location is therefore validated before it is
    followed.

    Args:
        client: An httpx client configured with follow_redirects=False
        url: The (already validated) starting URL

    Returns:
        Tuple of (response, final_url, error_message). error_message is None
        on success, in which case response is set.
    """
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,text/plain;q=0.8,*/*;q=0.5",
    }

    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        response = await client.get(current_url, headers=headers)

        # Explicit status check rather than response.is_redirect so the
        # behaviour does not depend on the response object's own helpers.
        if response.status_code not in REDIRECT_STATUS_CODES:
            return response, current_url, None

        location = response.headers.get("location")
        if not location:
            return response, current_url, None

        next_url = str(httpx.URL(current_url).join(location))
        validation_error = await _validate_fetch_url_async(next_url)
        if validation_error:
            logger.warning(f"Blocked redirect from {current_url} to {next_url}")
            return None, current_url, validation_error
        current_url = next_url

    return None, current_url, f"Error: Too many redirects (more than {MAX_REDIRECTS}) for URL: {url}"


def _should_block_playwright_request(request) -> bool:
    """
    Whether the Playwright browser should refuse a request.

    Blocks heavy resource types and tracking domains for speed, then applies
    the same public-internet rule as the httpx path to everything left.

    That last part covers navigations *and* subresources deliberately.
    Checking only navigations is not enough: the page's own JavaScript can
    XHR http://localhost:8000/api/... and write the response into the DOM,
    which the caller then extracts and hands to the entity. The application's
    API sends Access-Control-Allow-Origin: * and has no authentication, so
    nothing on the server side would refuse that read. The address check runs
    last so it only resolves DNS for requests that would otherwise be allowed.

    Args:
        request: A Playwright Request (anything with .resource_type and .url)

    Returns:
        True if the request should be aborted
    """
    if request.resource_type in PLAYWRIGHT_BLOCKED_RESOURCE_TYPES:
        return True

    url_lower = request.url.lower()
    for domain in PLAYWRIGHT_BLOCKED_DOMAINS:
        if domain in url_lower:
            return True

    if _validate_fetch_url(request.url):
        logger.warning(f"Playwright: blocked request to non-public URL {request.url}")
        return True

    return False


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


def _looks_like_block_page(text: str) -> bool:
    """
    Detect whether extracted text is an anti-bot block page rather than
    real content.

    Block interstitials are short; a page with substantial text is treated
    as real content even if it happens to mention a blocked phrase.
    """
    stripped = text.strip()
    if len(stripped) < MIN_CONTENT_LENGTH:
        return True
    if len(stripped) >= 2000:
        return False
    text_lower = stripped.lower()
    return any(indicator in text_lower for indicator in BLOCK_PAGE_INDICATORS)


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
    import sys

    # On Windows, Playwright needs ProactorEventLoop for subprocess support.
    # When running in a thread (via asyncio.to_thread), we need to explicitly
    # set this policy before Playwright creates its internal event loop.
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    def handle_route(route):
        """Route handler that blocks unnecessary requests."""
        if _should_block_playwright_request(route.request):
            route.abort()
        else:
            route.continue_()

    try:
        logger.debug(f"Playwright: Starting browser for {url}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            logger.debug("Playwright: Browser launched successfully")

            try:
                # Create a new context with a reasonable viewport
                context = browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=BROWSER_USER_AGENT,
                )
                page = context.new_page()

                # Block unnecessary resources for faster loading
                page.route("**/*", handle_route)
                logger.debug(f"Playwright: Page created with resource blocking, navigating to {url}")

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

    # Revalidate: this is also reachable directly, not only via web_fetch.
    validation_error = await _validate_fetch_url_async(url)
    if validation_error:
        return None, validation_error

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
            return wrap_untrusted_content(output, "a web search")

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


async def _web_fetch_impl(url: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
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

    # Scheme/address check before any network activity. Redirects are
    # revalidated per hop below, so an allowed URL cannot bounce into the
    # private network.
    validation_error = await _validate_fetch_url_async(url)
    if validation_error:
        return validation_error

    try:
        # Step 1: Fast fetch with httpx
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False) as client:
            response, url, redirect_error = await _get_following_redirects(client, url)
            if redirect_error:
                return redirect_error

            if response.status_code in BOT_BLOCK_STATUS_CODES:
                # Likely a bot wall turning away the plain HTTP client; a real
                # headless browser may be let through.
                logger.info(
                    f"Got {response.status_code} for {url}, retrying with headless browser"
                )
                rendered_html, error = await _fetch_with_playwright(url)

                if error:
                    return (
                        f"Error: Access denied (status {response.status_code}) for URL: {url}. "
                        f"Browser-based retry also failed: {error}"
                    )

                cleaned_text, title_text, _ = _extract_html_content(rendered_html, url)

                if _looks_like_block_page(cleaned_text):
                    return (
                        f"Error: Access denied (status {response.status_code}) for URL: {url}. "
                        f"The site's anti-bot protection also blocked the browser-based retry."
                    )

                if len(cleaned_text) > max_length:
                    cleaned_text = cleaned_text[:max_length] + "\n...[truncated]"

                logger.info(
                    f"Browser retry succeeded for {url}: {len(cleaned_text)} chars "
                    f"(after {response.status_code} from direct fetch)"
                )
                return (
                    f"Content from: {url} [JavaScript rendered]\n"
                    f"Title: {title_text}\n\n{cleaned_text}"
                )
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


async def web_fetch(url: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """
    Fetch a URL and return its content marked as untrusted.

    Page content is written by whoever controls the site, so it is banner-
    wrapped before it reaches the entity's context. Wrapping happens here,
    around every success path of _web_fetch_impl, rather than at each return
    site. Our own error strings are passed through unwrapped.

    Args:
        url: The URL to fetch
        max_length: Maximum content length to return

    Returns:
        Extracted content as text, or error message
    """
    result = await _web_fetch_impl(url, max_length)
    if result.startswith("Error:"):
        return result
    return wrap_untrusted_content(result, "a fetched web page")


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
