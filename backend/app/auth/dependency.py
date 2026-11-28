"""
The check itself (issue #396): one dependency, applied to the whole app in
main.py, that every request to a FastAPI route passes through before its
handler runs.

Order, for a route that isn't public:

1. An `Authorization` header, if there is one, is the credential: it must
   be a live bearer token. A request that presents a bad one is refused
   even if it also carries a good cookie, so a credential that was offered
   is never quietly ignored in favor of another.
2. Otherwise the session cookie, which must name a live session. The
   browser must not have marked the request as another origin's
   (Sec-Fetch-Site), and for a request that changes something the Origin
   must be this server's own.
3. Otherwise 401.

Then the principal's scope must reach the route, or 403.

A refusal happens before the handler and before anything is written, which
the Claude Code hooks rely on: a 401 or 403 from this server means nothing
of that request was recorded (hook_util.refused_before_recording).
"""
import logging
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import policy, store
from app.auth.origin import SAFE_METHODS, SAME_ORIGIN_FETCH_SITES, origin_problem
from app.auth.policy import (
    SCOPE_ENTITY,
    SCOPE_RESEARCHER,
    VIA_SESSION,
    VIA_TOKEN,
    Principal,
)
from app.database import get_db

logger = logging.getLogger(__name__)

NO_CREDENTIAL = (
    "Authentication required: this request carried no credential. A browser "
    "logs in at the server's own page; a machine sends "
    "'Authorization: Bearer <token>' (mint one on the server with "
    "'python -m app.auth token create')."
)
TOKEN_REFUSED = (
    "The bearer token was refused: it is unknown to this server or has been "
    "revoked. Mint a new one on the server with "
    "'python -m app.auth token create'."
)
SESSION_ENDED = "The login session has expired or was signed out. Log in again."
ENTITY_SCOPE_ONLY = (
    "This token is entity-scoped: it reaches the Claude Code hook endpoints "
    "and /mcp only (auth/policy.py lists them). This endpoint is the "
    "researcher's."
)


def unauthenticated(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown address"


def _bearer_value(header: str) -> Optional[str]:
    scheme, _, value = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


async def _principal_from_token(request: Request, db: AsyncSession, header: str) -> Principal:
    where = f"{request.method} {request.url.path} from {_client(request)}"
    value = _bearer_value(header)
    row = await store.find_token_row(db, value) if value else None
    if row is None:
        logger.warning(f"[AUTH] Refused {where}: an unknown bearer token")
        raise unauthenticated(TOKEN_REFUSED)
    if row.revoked_at is not None:
        logger.warning(f"[AUTH] Refused {where}: the revoked token '{row.name}'")
        raise unauthenticated(TOKEN_REFUSED)
    if row.scope == SCOPE_ENTITY and not row.entity_id:
        # store.create_token never makes one; a row edited by hand could be
        # one, and "no entity" must not come to mean "any entity"
        logger.warning(f"[AUTH] Refused {where}: token '{row.name}' is entity-scoped with no entity")
        raise unauthenticated(TOKEN_REFUSED)
    # Everything the request needs of the row, read before the stamp: a
    # failed stamp rolls the session back, which expires the row, and
    # reading an expired row here would be a 500 for a valid token
    principal = Principal(
        scope=row.scope,
        via=VIA_TOKEN,
        name=row.name,
        entity_id=row.entity_id if row.scope == SCOPE_ENTITY else None,
        credential_id=row.id,
    )
    try:
        await store.stamp_token_used(db, row)
    except Exception as e:
        # A missed last-used stamp (SQLite's one writer was busy) is not a
        # reason to refuse the request
        await db.rollback()
        logger.warning(f"[AUTH] Could not stamp token '{principal.name}' as used: {e}")
    return principal


async def _principal_from_cookie(request: Request, db: AsyncSession, cookie: str) -> Principal:
    where = f"{request.method} {request.url.path} from {_client(request)}"
    session_id = await store.find_session_id(db, cookie)
    if session_id is None:
        logger.info(f"[AUTH] Refused {where}: an expired or unknown session")
        raise unauthenticated(SESSION_ENDED)
    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site not in SAME_ORIGIN_FETCH_SITES:
        # The browser itself says another origin sent this. Covers reads as
        # well as writes, and the pages SameSite lets through: another port
        # on the same host is "same-site"
        problem = (
            "The browser marked this request as coming from another origin "
            f"(Sec-Fetch-Site: {fetch_site}), and the login cookie is only "
            "honored from this server's own page."
        )
        logger.warning(f"[AUTH] Refused {where}: {problem}")
        raise HTTPException(status_code=403, detail=problem)
    if request.method.upper() not in SAFE_METHODS:
        problem = origin_problem(request)
        if problem:
            logger.warning(f"[AUTH] Refused {where}: {problem}")
            raise HTTPException(status_code=403, detail=problem)
    return Principal(
        scope=SCOPE_RESEARCHER,
        via=VIA_SESSION,
        name=store.RESEARCHER,
        credential_id=session_id,
    )


async def resolve_principal(request: Request, db: AsyncSession) -> Principal:
    """Who this request is from, or a 401/403. The one function the test
    suite replaces, so the rest of the suite needn't log in for every call
    (tests/conftest.py); the auth tests run it for real."""
    header = request.headers.get("authorization")
    if header is not None:
        return await _principal_from_token(request, db, header)
    cookie = request.cookies.get(store.SESSION_COOKIE)
    if cookie:
        return await _principal_from_cookie(request, db, cookie)
    logger.info(
        f"[AUTH] Refused {request.method} {request.url.path} from "
        f"{_client(request)}: no credential"
    )
    raise unauthenticated(NO_CREDENTIAL)


async def authenticate(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Optional[Principal]:
    """The app-level dependency. Returns the request's Principal, or None
    for a public route; a handler that needs to know who is calling takes
    `principal: Optional[Principal] = Depends(authenticate)` and gets this
    same answer (FastAPI resolves a dependency once per request)."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    method = request.method.upper()
    if policy.is_public(method, template):
        return None
    principal = await resolve_principal(request, db)
    if not policy.allowed(principal, method, template):
        logger.warning(
            f"[AUTH] Refused {method} {request.url.path} from {_client(request)}: "
            f"token '{principal.name}' ({principal.scope}) is out of scope"
        )
        raise HTTPException(status_code=403, detail=ENTITY_SCOPE_ONLY)
    request.state.principal = principal
    return principal
