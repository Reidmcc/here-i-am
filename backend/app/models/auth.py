"""
Authentication tables (issue #396).

Nothing in these tables can be turned back into a credential: the password
is an scrypt hash, and session ids and API tokens are stored as SHA-256
digests of high-entropy random values, looked up by digest. A copy of the
database (a backup, a restore to another machine) therefore carries who may
log in, but not the means to.

All times are naive UTC, like every other stored time in this database.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuthCredential(Base):
    """
    The researcher's password, hashed. One row, named "researcher": the
    application has exactly one human account, and it is set only by the
    CLI on the server (`python -m app.auth set-password`), never over HTTP.
    """
    __tablename__ = "auth_credentials"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuthSession(Base):
    """
    A logged-in browser. `id` is the SHA-256 of the cookie's value, so the
    row can't be replayed as a cookie. A session ends at `expires_at` (the
    absolute limit, fixed at login) or after sitting idle past the idle
    limit measured from `last_seen_at`, whichever comes first; logging out
    deletes the row.
    """
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


class ApiToken(Base):
    """
    A machine's credential: one per machine (or per script), revocable one
    at a time. `scope` is "entity" or "researcher"; an entity token is bound
    to one entity (`entity_id`, the index name) and reaches only the Claude
    Code endpoints. Revoking stamps `revoked_at` and keeps the row, so the
    list still says which machine once held what.
    """
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
