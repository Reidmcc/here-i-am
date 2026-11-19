"""
Logging in and out (issue #396). The rest of authentication is app/auth;
this is its HTTP surface, and it is deliberately small: a password buys a
session, a session can be ended, and anyone holding a credential can ask
what it is. Nothing here sets a password or mints a token. Those are made
by `python -m app.auth` on the server's own machine.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import store
from app.auth.dependency import authenticate
from app.auth.origin import cookie_secure, origin_problem
from app.auth.policy import VIA_SESSION, Principal
from app.auth.throttle import login_throttle
from app.config import settings
from app.database import get_db
from app.models.auth import AuthSession

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger(__name__)

NO_PASSWORD_SET = (
    "No password has been set on this server, so nobody can log in yet. On "
    "the server's machine, in the backend directory, run: python -m app.auth init"
)


class LoginRequest(BaseModel):
    password: str


class SessionInfo(BaseModel):
    authenticated: bool = True
    scope: str
    via: str
    name: str
    entity_id: Optional[str] = None
    # When a browser session ends at the latest (its absolute limit), as an
    # ISO time in UTC; null for a token, which has no expiry
    expires_at: Optional[str] = None


_login_lock_for_loop: Optional[tuple] = None


def _login_lock() -> asyncio.Lock:
    """One lock per event loop: an asyncio lock belongs to the loop it was
    first waited on in, and a server has one loop while a test run has
    several."""
    global _login_lock_for_loop
    loop = asyncio.get_running_loop()
    if _login_lock_for_loop is None or _login_lock_for_loop[0] is not loop:
        _login_lock_for_loop = (loop, asyncio.Lock())
    return _login_lock_for_loop[1]


def _refuse_while_throttled() -> None:
    wait = login_throttle.retry_after()
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many wrong passwords. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )


def _iso_utc(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat() + "Z"


def _set_session_cookie(response: Response, request: Request, value: str) -> None:
    response.set_cookie(
        store.SESSION_COOKIE,
        value,
        max_age=settings.auth_session_max_days * 24 * 60 * 60,
        path="/",
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request),
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        store.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request),
    )


@router.post("/login", response_model=SessionInfo)
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchange the researcher's password for a browser session.

    Public by necessity (policy.PUBLIC_ROUTES), so everything about it is
    arranged to give a stranger nothing: it must come from this server's
    own page, wrong guesses buy a growing wait during which no password is
    looked at, and a wrong password and a right one differ only in the
    answer.
    """
    problem = origin_problem(request)
    if problem:
        raise HTTPException(status_code=403, detail=problem)

    _refuse_while_throttled()
    # One password check at a time, and the wait is asked about again once
    # it is this request's turn: a burst sent all at once would otherwise
    # pass the check above together and each get its guess looked at
    async with _login_lock():
        _refuse_while_throttled()
        verdict = await store.check_password(db, data.password)
        if verdict is False:
            login_throttle.failure()
        elif verdict:
            login_throttle.success()
    if verdict is None:
        raise HTTPException(status_code=503, detail=NO_PASSWORD_SET)
    if not verdict:
        client = request.client.host if request.client else "unknown address"
        logger.warning(f"[AUTH] Wrong password at login from {client}")
        raise HTTPException(status_code=401, detail="Wrong password.")

    value, session = await store.create_session(
        db,
        request.headers.get("user-agent"),
        replacing=request.cookies.get(store.SESSION_COOKIE),
    )
    _set_session_cookie(response, request, value)
    logger.info("[AUTH] The researcher logged in")
    return SessionInfo(
        scope="researcher",
        via=VIA_SESSION,
        name=store.RESEARCHER,
        expires_at=_iso_utc(session.expires_at),
    )


@router.get("/session", response_model=SessionInfo)
async def session_info(
    principal: Optional[Principal] = Depends(authenticate),
    db: AsyncSession = Depends(get_db),
):
    """Who this request's credential is: how the page learns it is logged
    in, and how a machine checks its token. A request with no live
    credential never gets here (it is answered 401)."""
    expires_at = None
    if principal.via == VIA_SESSION and principal.credential_id:
        row = await db.get(AuthSession, principal.credential_id)
        expires_at = _iso_utc(row.expires_at) if row else None
    return SessionInfo(
        scope=principal.scope,
        via=principal.via,
        name=principal.name,
        entity_id=principal.entity_id,
        expires_at=expires_at,
    )


class LogoutResponse(BaseModel):
    signed_out: int


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    principal: Optional[Principal] = Depends(authenticate),
    db: AsyncSession = Depends(get_db),
):
    """End this browser's session. A token is not a session and is not
    affected (revoke it with the CLI)."""
    ended = 0
    if principal.via == VIA_SESSION and principal.credential_id:
        await store.delete_session(db, principal.credential_id)
        ended = 1
    _clear_session_cookie(response, request)
    return LogoutResponse(signed_out=ended)


@router.post("/logout-all", response_model=LogoutResponse)
async def logout_everywhere(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """End every browser session, this one included: for a lost device."""
    ended = await store.delete_all_sessions(db)
    _clear_session_cookie(response, request)
    logger.info(f"[AUTH] Signed out everywhere ({ended} sessions ended)")
    return LogoutResponse(signed_out=ended)
