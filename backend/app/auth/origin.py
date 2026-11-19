"""
The same-origin rule for cookie-authenticated requests (issue #396).

A browser attaches the session cookie to a request without being asked, so
a page on another site could aim one at this server and have it arrive
logged in. `SameSite=Strict` already tells the browser not to send the
cookie from another site; this is the server's own check on top of it, for
the requests that change something and for logging in: the `Origin` header
the browser sets must be one this server knows as itself.

"Knows as itself" is two things, and neither is taken from the request:

- **Its own page on this machine**: an origin whose host is a loopback
  name (`localhost`, `127.0.0.1`, `[::1]`, `*.localhost`) and is the same
  host and port the request was sent to. A browser resolves those names
  to this machine and nowhere else.
- **An origin named in AUTH_ALLOWED_ORIGINS**: the address the browser
  uses when the server is reached any other way (through a TLS terminator,
  by a LAN address).

The first version of this rule compared `Origin` with the request's own
`Host` header, or `X-Forwarded-Host`, whatever they said. A DNS-rebinding
page controls both: it is served from a name its author points at
127.0.0.1, so the browser sends that name as `Host` and as `Origin`, and
they match by construction. Such a page has no cookie for the name, so no
data route opens to it, but it could send password guesses to the login
endpoint and read the answers, and hold the login wait at its cap. A
loopback name can't be rebound and a listed origin isn't the attacker's,
so the rule now holds against it (PR #411 review).

Bearer tokens don't go through this. A browser never attaches one by
itself, so there is nothing for another site to borrow.

A second, independent signal is checked in dependency.py for every
cookie-authenticated request, reads included: `Sec-Fetch-Site`, which the
browser sets itself and a page cannot forge.
"""
from typing import Optional
from urllib.parse import urlsplit

from starlette.requests import Request

from app.config import settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# What Sec-Fetch-Site says of a request this server's own page made, or
# one the person typed or bookmarked. "same-site" is another port or
# subdomain, and "cross-site" is anyone
SAME_ORIGIN_FETCH_SITES = frozenset({"same-origin", "none"})

LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def allowed_origins() -> set:
    return {
        origin.strip().rstrip("/").lower()
        for origin in (settings.auth_allowed_origins or "").split(",")
        if origin.strip()
    }


def is_loopback_hostname(hostname: Optional[str]) -> bool:
    name = (hostname or "").lower()
    return name in LOOPBACK_HOSTNAMES or name.endswith(".localhost")


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
    if origin.rstrip("/").lower() in allowed_origins():
        return None
    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
    except ValueError:
        parsed, hostname = None, None
    sent_to = (request.headers.get("host") or "").strip().lower()
    if (
        parsed is not None
        and is_loopback_hostname(hostname)
        and parsed.netloc.lower() == sent_to
    ):
        return None
    return (
        f"This request came from '{origin}', which this server does not know "
        "as its own address. Its page on this machine (http://localhost) is "
        "always accepted; any other address the browser uses has to be named "
        f"in the server's environment: AUTH_ALLOWED_ORIGINS={origin}"
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
