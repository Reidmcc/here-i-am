"""
The auth tables' reads and writes (issue #396): the password, browser
sessions, and API tokens. See models/auth.py for what is stored and why
none of it can be replayed as a credential.

Every function takes the caller's database session. Nothing here decides
who may do what; that is policy.py and dependency.py.
"""
import asyncio
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import passwords
from app.auth.policy import SCOPE_ENTITY, SCOPE_RESEARCHER, SCOPES
from app.config import settings
from app.models.auth import ApiToken, AuthCredential, AuthSession

logger = logging.getLogger(__name__)

RESEARCHER = "researcher"
SESSION_COOKIE = "him_session"
# The prefix makes a token recognizable in a config file or a leak scan
TOKEN_PREFIX = "him_"
# 256 bits from the OS. At that entropy a fast digest is enough to store:
# there is nothing to brute-force, so a slow hash would buy nothing and
# cost every request.
SECRET_BYTES = 32

# last_used_at / last_seen_at are for a person reading the token list, not
# for a decision that needs precision: stamped at most this often, so an
# authenticated request is a read, and SQLite's one writer isn't taken on
# every hook call
STAMP_INTERVAL = timedelta(minutes=1)


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.utcnow()


# --- The password

async def get_password_hash(db: AsyncSession) -> Optional[str]:
    result = await db.execute(
        select(AuthCredential.password_hash).where(AuthCredential.name == RESEARCHER)
    )
    return result.scalar_one_or_none()


async def set_password(db: AsyncSession, password: str) -> int:
    """Set the researcher's password and end every browser session (a
    changed password must not leave the old one's logins standing). Returns
    how many sessions were ended. Raises ValueError for an unusable one."""
    problem = passwords.password_problem(password)
    if problem:
        raise ValueError(problem)
    hashed = await asyncio.to_thread(passwords.hash_password, password)
    row = await db.get(AuthCredential, RESEARCHER)
    if row is None:
        db.add(AuthCredential(name=RESEARCHER, password_hash=hashed, updated_at=_now()))
    else:
        row.password_hash = hashed
        row.updated_at = _now()
    ended = await db.execute(delete(AuthSession))
    await db.commit()
    return ended.rowcount or 0


async def check_password(db: AsyncSession, password: str) -> Optional[bool]:
    """True or False for a right or wrong password; None when no password
    has been set, so nobody can log in at all. The caller serializes these
    (routes/auth.login): each takes 64 MB and a tenth of a second."""
    stored = await get_password_hash(db)
    if stored is None:
        return None
    right = await asyncio.to_thread(passwords.verify_password, password, stored)
    if right and passwords.needs_rehash(stored):
        # The cost was raised since this hash was made, and the password is
        # in hand only now: store it again at today's cost. Sessions stay.
        try:
            row = await db.get(AuthCredential, RESEARCHER)
            row.password_hash = await asyncio.to_thread(passwords.hash_password, password)
            row.updated_at = _now()
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.warning(f"[AUTH] Could not re-hash the password at today's cost: {e}")
    return right


# --- Browser sessions

async def create_session(
    db: AsyncSession, user_agent: Optional[str] = None, replacing: Optional[str] = None
) -> Tuple[str, AuthSession]:
    """Open a session. Returns (the cookie's value, the row); the value is
    not stored and can't be recovered from the row.

    `replacing` is the cookie the browser logged in with, if it had one:
    that session ends here, so logging in again never leaves the old one
    standing. Sessions past either limit are cleared out at the same time
    (otherwise one is only ever deleted when its own cookie comes back)."""
    value = secrets.token_urlsafe(SECRET_BYTES)
    now = _now()
    idle_cutoff = now - timedelta(days=settings.auth_session_idle_days)
    ended = or_(AuthSession.expires_at <= now, AuthSession.last_seen_at <= idle_cutoff)
    if replacing:
        ended = or_(ended, AuthSession.id == digest(replacing))
    await db.execute(delete(AuthSession).where(ended))
    session = AuthSession(
        id=digest(value),
        created_at=now,
        expires_at=now + timedelta(days=settings.auth_session_max_days),
        last_seen_at=now,
        user_agent=(user_agent or "")[:300] or None,
    )
    db.add(session)
    await db.commit()
    return value, session


def session_idle_deadline(session: AuthSession) -> datetime:
    return session.last_seen_at + timedelta(days=settings.auth_session_idle_days)


async def find_session_id(db: AsyncSession, cookie_value: str) -> Optional[str]:
    """The id of the live session a cookie names, or None. One past either
    limit is refused and deleted on sight.

    The verdict is reached from what was read, before anything is
    written: the delete and the last-seen stamp are housekeeping, and a
    write that fails (SQLite's one writer was busy) must neither let an
    expired session in nor turn a live one into an error."""
    if not cookie_value:
        return None
    session = await db.get(AuthSession, digest(cookie_value))
    if session is None:
        return None
    session_id = session.id
    now = _now()
    expired = session.expires_at <= now or session_idle_deadline(session) <= now
    try:
        if expired:
            await db.delete(session)
            await db.commit()
        elif now - session.last_seen_at >= STAMP_INTERVAL:
            session.last_seen_at = now
            await db.commit()
    except Exception as e:
        await db.rollback()
        logger.warning(f"[AUTH] Could not update a login session's row: {e}")
    return None if expired else session_id


async def delete_session(db: AsyncSession, session_id: str) -> None:
    await db.execute(delete(AuthSession).where(AuthSession.id == session_id))
    await db.commit()


async def delete_all_sessions(db: AsyncSession) -> int:
    result = await db.execute(delete(AuthSession))
    await db.commit()
    return result.rowcount or 0


async def list_sessions(db: AsyncSession) -> List[AuthSession]:
    result = await db.execute(select(AuthSession).order_by(AuthSession.created_at))
    return list(result.scalars().all())


# --- API tokens

async def create_token(
    db: AsyncSession, name: str, scope: str, entity_id: Optional[str] = None
) -> Tuple[str, ApiToken]:
    """Mint a token. Returns (the token, the row); the token is shown once
    and can't be recovered from the row. Raises ValueError for a scope that
    doesn't exist, an entity token with no entity (or a researcher token
    with one), or a name an active token already has: a name is how a token
    is revoked, so it has to mean one token."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A token needs a name (the machine or script it is for).")
    if scope not in SCOPES:
        raise ValueError(f"Unknown scope '{scope}' (use one of: {', '.join(SCOPES)}).")
    if scope == SCOPE_ENTITY and not entity_id:
        raise ValueError("An entity token must be bound to an entity.")
    if scope == SCOPE_RESEARCHER and entity_id:
        raise ValueError("A researcher token is not bound to an entity.")
    existing = await db.execute(
        select(ApiToken.id).where(ApiToken.name == name, ApiToken.revoked_at.is_(None))
    )
    if existing.first() is not None:
        raise ValueError(
            f"An active token named '{name}' already exists. Revoke it first, "
            "or choose another name."
        )
    value = TOKEN_PREFIX + secrets.token_urlsafe(SECRET_BYTES)
    token = ApiToken(
        id=str(uuid.uuid4()),
        name=name[:100],
        token_hash=digest(value),
        scope=scope,
        entity_id=entity_id,
        created_at=_now(),
    )
    db.add(token)
    await db.commit()
    return value, token


async def find_token_row(db: AsyncSession, value: str) -> Optional[ApiToken]:
    """The row a presented token names, revoked or not (the caller refuses
    a revoked one, and can say which machine presented it)."""
    if not value or not value.startswith(TOKEN_PREFIX):
        return None
    result = await db.execute(select(ApiToken).where(ApiToken.token_hash == digest(value)))
    return result.scalar_one_or_none()


async def stamp_token_used(db: AsyncSession, token: ApiToken) -> None:
    now = _now()
    if token.last_used_at is not None and now - token.last_used_at < STAMP_INTERVAL:
        return
    await db.execute(
        update(ApiToken).where(ApiToken.id == token.id).values(last_used_at=now)
    )
    await db.commit()


async def list_tokens(db: AsyncSession) -> List[ApiToken]:
    result = await db.execute(select(ApiToken).order_by(ApiToken.created_at))
    return list(result.scalars().all())


async def revoke_tokens(db: AsyncSession, name_or_id: str) -> List[ApiToken]:
    """Revoke the active token with this name or id (an id prefix of eight
    or more characters is enough). Returns the rows revoked: empty when
    nothing matched."""
    wanted = (name_or_id or "").strip()
    if not wanted:
        return []
    result = await db.execute(select(ApiToken).where(ApiToken.revoked_at.is_(None)))
    matched = [
        token for token in result.scalars().all()
        if token.name == wanted
        or token.id == wanted
        or (len(wanted) >= 8 and token.id.startswith(wanted))
    ]
    now = _now()
    for token in matched:
        token.revoked_at = now
    if matched:
        await db.commit()
    return matched
