"""
Authentication module for Here I Am remote server deployment.

Provides password-based authentication with session tokens.
"""

import secrets
import hashlib
import hmac
import time
import logging
from typing import Optional, Tuple
from datetime import datetime, timedelta
from fastapi import Request, Response, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import settings

logger = logging.getLogger(__name__)

# In-memory session storage
# Maps session_token -> (created_at, expires_at)
_active_sessions: dict[str, Tuple[float, float]] = {}

# Session secret for token generation (auto-generated if not configured)
_session_secret: str = ""


def _get_session_secret() -> str:
    """Get or generate the session secret."""
    global _session_secret
    if not _session_secret:
        if settings.auth_session_secret:
            _session_secret = settings.auth_session_secret
        else:
            # Generate a random secret for this server instance
            _session_secret = secrets.token_hex(32)
            logger.warning(
                "No AUTH_SESSION_SECRET configured. Generated temporary secret. "
                "Sessions will be invalidated on server restart."
            )
    return _session_secret


def _hash_password(password: str) -> str:
    """Create a secure hash of the password for comparison."""
    secret = _get_session_secret()
    return hmac.new(
        secret.encode(),
        password.encode(),
        hashlib.sha256
    ).hexdigest()


def verify_password(password: str) -> bool:
    """Verify if the provided password matches the configured password."""
    if not settings.auth_password:
        return False
    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(
        _hash_password(password),
        _hash_password(settings.auth_password)
    )


def create_session() -> str:
    """Create a new session and return the session token."""
    # Generate a secure random token
    token = secrets.token_urlsafe(32)

    # Calculate expiration time
    now = time.time()
    expires_at = now + (settings.auth_session_timeout_hours * 3600)

    # Store session
    _active_sessions[token] = (now, expires_at)

    logger.info(f"Created new session (expires in {settings.auth_session_timeout_hours}h)")
    return token


def validate_session(token: str) -> bool:
    """Validate a session token. Returns True if valid and not expired."""
    if not token:
        return False

    session = _active_sessions.get(token)
    if not session:
        return False

    created_at, expires_at = session
    now = time.time()

    if now > expires_at:
        # Session expired, clean it up
        del _active_sessions[token]
        logger.info("Session expired and removed")
        return False

    return True


def invalidate_session(token: str) -> bool:
    """Invalidate a session token. Returns True if token existed."""
    if token in _active_sessions:
        del _active_sessions[token]
        logger.info("Session invalidated")
        return True
    return False


def cleanup_expired_sessions() -> int:
    """Remove all expired sessions. Returns count of removed sessions."""
    now = time.time()
    expired = [
        token for token, (_, expires_at) in _active_sessions.items()
        if now > expires_at
    ]
    for token in expired:
        del _active_sessions[token]
    if expired:
        logger.info(f"Cleaned up {len(expired)} expired sessions")
    return len(expired)


def get_session_info(token: str) -> Optional[dict]:
    """Get information about a session."""
    session = _active_sessions.get(token)
    if not session:
        return None

    created_at, expires_at = session
    now = time.time()

    return {
        "created_at": datetime.fromtimestamp(created_at).isoformat(),
        "expires_at": datetime.fromtimestamp(expires_at).isoformat(),
        "remaining_seconds": max(0, int(expires_at - now)),
        "remaining_hours": max(0, round((expires_at - now) / 3600, 1)),
    }


# Public paths that don't require authentication
PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/status",
    "/api/health",
}

# Path prefixes that don't require authentication (static files)
PUBLIC_PATH_PREFIXES = (
    "/login.html",
    "/css/",
    "/js/",
    "/favicon",
)


def _is_public_path(path: str) -> bool:
    """Check if a path is public (doesn't require authentication)."""
    # Exact matches
    if path in PUBLIC_PATHS:
        return True

    # Prefix matches (static files, etc.)
    if path.startswith(PUBLIC_PATH_PREFIXES):
        return True

    # Root path serves login page when not authenticated
    if path == "/" or path == "":
        return True

    return False


def get_token_from_request(request: Request) -> Optional[str]:
    """Extract session token from request (cookie or Authorization header)."""
    # Try cookie first
    token = request.cookies.get("session_token")
    if token:
        return token

    # Try Authorization header (Bearer token)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware that protects API routes.

    When auth_enabled is True:
    - Public paths are accessible without authentication
    - All other paths require a valid session token
    - Unauthenticated API requests get 401 response
    - Unauthenticated page requests redirect to login
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip auth check if auth is not enabled
        if not settings.auth_enabled:
            return await call_next(request)

        path = request.url.path

        # Allow public paths
        if _is_public_path(path):
            return await call_next(request)

        # Get session token
        token = get_token_from_request(request)

        # Validate session
        if not token or not validate_session(token):
            # Periodic cleanup of expired sessions
            cleanup_expired_sessions()

            # API requests get 401 JSON response
            if path.startswith("/api/"):
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Authentication required"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Page requests get redirected to login
            # But we need to serve the index.html (which handles login)
            # So we let it through but the frontend will redirect
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication required"},
            )

        # Token is valid, proceed
        return await call_next(request)


# Auth route handlers (to be registered in main.py)

async def login_handler(request: Request) -> Response:
    """Handle login requests."""
    try:
        body = await request.json()
        password = body.get("password", "")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request body"
        )

    if not verify_password(password):
        logger.warning(f"Failed login attempt from {request.client.host}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password"
        )

    # Create session
    token = create_session()

    # Create response with session cookie
    response = JSONResponse(content={
        "success": True,
        "message": "Login successful",
        "session_info": get_session_info(token),
    })

    # Set secure cookie
    # Use SameSite=Lax for cross-origin compatibility, Secure only if not localhost
    is_localhost = request.url.hostname in ("localhost", "127.0.0.1")
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=not is_localhost,
        samesite="lax",
        max_age=settings.auth_session_timeout_hours * 3600,
    )

    logger.info(f"Successful login from {request.client.host}")
    return response


async def logout_handler(request: Request) -> Response:
    """Handle logout requests."""
    token = get_token_from_request(request)
    if token:
        invalidate_session(token)

    response = JSONResponse(content={
        "success": True,
        "message": "Logged out successfully",
    })

    # Clear the session cookie
    response.delete_cookie(key="session_token")

    return response


async def auth_status_handler(request: Request) -> Response:
    """Check authentication status."""
    # If auth is not enabled, always return authenticated
    if not settings.auth_enabled:
        return JSONResponse(content={
            "auth_enabled": False,
            "authenticated": True,
            "message": "Authentication is not enabled",
        })

    token = get_token_from_request(request)
    is_valid = token and validate_session(token)

    response_data = {
        "auth_enabled": True,
        "authenticated": is_valid,
    }

    if is_valid:
        response_data["session_info"] = get_session_info(token)

    return JSONResponse(content=response_data)
