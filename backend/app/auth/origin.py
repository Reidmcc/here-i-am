"""
The same-origin rule for cookie-authenticated requests (issue #396).

A browser attaches the session cookie to a request without being asked, so
a page on another site could aim one at this server and have it arrive
logged in. `SameSite=Strict` already tells the browser not to send the
cookie from another site; this is the server's own check on top of it, for
the requests that change something: the `Origin` header the browser sets
must name this server.

"This server" is the `Host` the request was sent to. Behind a TLS
terminator that rewrites `Host`, the name the browser used arrives as
`X-Forwarded-Host`, and that is accepted too: a page on another site can't
set that header on a cross-site request without a preflight this server
never grants (it sends no CORS headers at all). AUTH_ALLOWED_ORIGINS names
any further origin outright, for a proxy that does neither.

Only the host and port are compared, not the scheme: the terminator speaks
https to the browser and http to this server, so the scheme this server
sees says nothing about the one the browser used.

Bearer tokens don't go through this. A browser never attaches one by
itself, so there is nothing for another site to borrow.
"""
from typing import Optional
from urllib.parse import urlsplit

from starlette.requests import Request

from app.config import settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _allowed_origins() -> set:
    return {
        origin.strip().rstrip("/").lower()
        for origin in (settings.auth_allowed_origins or "").split(",")
        if origin.strip()
    }


def _own_hosts(request: Request) -> set:
    hosts = set()
    for header in ("host", "x-forwarded-host"):
        value = request.headers.get(header) or ""
        # A chain of proxies appends; the first is the name the browser used
        first = value.split(",")[0].strip().lower()
        if first:
            hosts.add(first)
    return hosts


def origin_problem(request: Request) -> Optional[str]:
    """Why this request's Origin isn't this server's, or None when it is.
    Asked only for cookie-authenticated requests with an unsafe method, and
    for logging in."""
    origin = (request.headers.get("origin") or "").strip()
    if not origin or origin.lower() == "null":
        return (
            "This request carries no Origin header saying which page sent it, "
            "and a browser login or a request that changes something with the "
            "login cookie must come from this server's own page. A browser "
            "always sends one; a script should use a bearer token instead."
        )
    if origin.rstrip("/").lower() in _allowed_origins():
        return None
    host = urlsplit(origin).netloc.lower()
    own = _own_hosts(request)
    if host and host in own:
        return None
    return (
        f"This request came from '{origin}', which is not this server "
        f"({', '.join(sorted(own)) or 'no Host header'}). If the server is "
        "behind a proxy that rewrites the Host header, set "
        f"AUTH_ALLOWED_ORIGINS={origin} in its environment."
    )


def arrived_over_https(request: Request) -> bool:
    """Whether the browser reached this server over https, as far as the
    request can say: the Origin it sent (which a TLS terminator passes
    through untouched), else the scheme uvicorn saw."""
    origin = (request.headers.get("origin") or "").strip().lower()
    if origin.startswith("https://"):
        return True
    if origin.startswith("http://"):
        return False
    return request.url.scheme == "https"


def cookie_secure(request: Request) -> bool:
    """Whether to mark the session cookie `Secure`. "auto" follows how the
    login arrived: over https the cookie is Secure and never travels in the
    clear; over plain http (a local install at http://localhost) it has to
    go without, or a browser that doesn't treat localhost as secure would
    drop it and the login would silently not stick."""
    mode = (settings.auth_cookie_secure or "auto").strip().lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    return arrived_over_https(request)
