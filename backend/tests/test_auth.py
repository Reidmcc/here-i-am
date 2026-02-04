"""
Unit tests for authentication functionality.

Tests cover:
- Auth configuration validation
- Session management (create, validate, invalidate, cleanup)
- Password verification
- Auth middleware behavior
- Auth API handlers
"""
import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.auth import (
    verify_password,
    create_session,
    validate_session,
    invalidate_session,
    cleanup_expired_sessions,
    get_session_info,
    get_token_from_request,
    _is_public_path,
    AuthMiddleware,
    login_handler,
    logout_handler,
    auth_status_handler,
    _active_sessions,
    _get_session_secret,
)


# ============================================================================
# Auth Configuration Tests
# ============================================================================

class TestAuthConfig:
    """Tests for authentication configuration in Settings."""

    def test_auth_disabled_by_default(self):
        """Test that authentication is disabled by default."""
        settings = Settings(
            anthropic_api_key="test-key",
            _env_file=None,
        )
        assert settings.auth_enabled is False

    def test_auth_enabled_config(self):
        """Test authentication enabled configuration."""
        settings = Settings(
            anthropic_api_key="test-key",
            auth_enabled=True,
            auth_password="test_password_123",
            _env_file=None,
        )
        assert settings.auth_enabled is True
        assert settings.auth_password == "test_password_123"

    def test_auth_session_timeout_default(self):
        """Test default session timeout is 24 hours."""
        settings = Settings(
            anthropic_api_key="test-key",
            _env_file=None,
        )
        assert settings.auth_session_timeout_hours == 24

    def test_auth_session_timeout_custom(self):
        """Test custom session timeout."""
        settings = Settings(
            anthropic_api_key="test-key",
            auth_session_timeout_hours=48,
            _env_file=None,
        )
        assert settings.auth_session_timeout_hours == 48

    def test_validate_auth_config_missing_password(self):
        """Test validation fails when auth enabled but password missing."""
        settings = Settings(
            anthropic_api_key="test-key",
            auth_enabled=True,
            auth_password="",
            _env_file=None,
        )
        with pytest.raises(ValueError) as exc_info:
            settings.validate_auth_config()
        assert "AUTH_PASSWORD must be set" in str(exc_info.value)

    def test_validate_auth_config_short_password(self):
        """Test validation fails when password too short."""
        settings = Settings(
            anthropic_api_key="test-key",
            auth_enabled=True,
            auth_password="short",
            _env_file=None,
        )
        with pytest.raises(ValueError) as exc_info:
            settings.validate_auth_config()
        assert "at least 8 characters" in str(exc_info.value)

    def test_validate_auth_config_valid_password(self):
        """Test validation passes with valid password."""
        settings = Settings(
            anthropic_api_key="test-key",
            auth_enabled=True,
            auth_password="valid_password_123",
            _env_file=None,
        )
        # Should not raise
        settings.validate_auth_config()

    def test_validate_auth_config_disabled(self):
        """Test validation passes when auth disabled (no password required)."""
        settings = Settings(
            anthropic_api_key="test-key",
            auth_enabled=False,
            auth_password="",
            _env_file=None,
        )
        # Should not raise
        settings.validate_auth_config()

    def test_server_host_default(self):
        """Test server host defaults to localhost."""
        settings = Settings(
            anthropic_api_key="test-key",
            _env_file=None,
        )
        assert settings.server_host == "127.0.0.1"

    def test_server_port_default(self):
        """Test server port defaults to 8000."""
        settings = Settings(
            anthropic_api_key="test-key",
            _env_file=None,
        )
        assert settings.server_port == 8000

    def test_get_auth_allowed_origins_empty(self):
        """Test get_auth_allowed_origins returns empty list when not configured."""
        settings = Settings(
            anthropic_api_key="test-key",
            auth_allowed_origins="",
            _env_file=None,
        )
        assert settings.get_auth_allowed_origins() == []

    def test_get_auth_allowed_origins_single(self):
        """Test get_auth_allowed_origins with single origin."""
        settings = Settings(
            anthropic_api_key="test-key",
            auth_allowed_origins="https://example.com",
            _env_file=None,
        )
        assert settings.get_auth_allowed_origins() == ["https://example.com"]

    def test_get_auth_allowed_origins_multiple(self):
        """Test get_auth_allowed_origins with multiple origins."""
        settings = Settings(
            anthropic_api_key="test-key",
            auth_allowed_origins="https://example.com, https://localhost:8000",
            _env_file=None,
        )
        origins = settings.get_auth_allowed_origins()
        assert len(origins) == 2
        assert "https://example.com" in origins
        assert "https://localhost:8000" in origins


# ============================================================================
# Session Management Tests
# ============================================================================

class TestSessionManagement:
    """Tests for session management functions."""

    def setup_method(self):
        """Clear active sessions before each test."""
        _active_sessions.clear()

    def test_create_session(self):
        """Test session creation returns token."""
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_timeout_hours = 24
            token = create_session()

        assert token is not None
        assert len(token) > 20  # URL-safe token should be reasonably long
        assert token in _active_sessions

    def test_validate_session_valid(self):
        """Test validating a valid session."""
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_timeout_hours = 24
            token = create_session()

        assert validate_session(token) is True

    def test_validate_session_invalid_token(self):
        """Test validating an invalid token."""
        assert validate_session("invalid_token") is False

    def test_validate_session_empty_token(self):
        """Test validating an empty token."""
        assert validate_session("") is False
        assert validate_session(None) is False

    def test_validate_session_expired(self):
        """Test validating an expired session."""
        # Create session with very short timeout
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_timeout_hours = 0  # Immediate expiration
            token = create_session()

        # Wait a tiny bit to ensure expiration
        time.sleep(0.01)

        # Manually set expiration to past
        created_at, _ = _active_sessions[token]
        _active_sessions[token] = (created_at, time.time() - 1)

        assert validate_session(token) is False
        # Expired session should be cleaned up
        assert token not in _active_sessions

    def test_invalidate_session_exists(self):
        """Test invalidating an existing session."""
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_timeout_hours = 24
            token = create_session()

        assert invalidate_session(token) is True
        assert token not in _active_sessions

    def test_invalidate_session_not_exists(self):
        """Test invalidating a non-existent session."""
        result = invalidate_session("nonexistent_token")
        assert result is False

    def test_cleanup_expired_sessions(self):
        """Test cleanup of expired sessions."""
        # Create some sessions
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_timeout_hours = 24
            valid_token = create_session()

            # Create an expired session manually
            expired_token = "expired_test_token"
            _active_sessions[expired_token] = (time.time() - 100, time.time() - 1)

        count = cleanup_expired_sessions()

        assert count == 1
        assert expired_token not in _active_sessions
        assert valid_token in _active_sessions

    def test_get_session_info_valid(self):
        """Test getting info for valid session."""
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_timeout_hours = 24
            token = create_session()

        info = get_session_info(token)

        assert info is not None
        assert "created_at" in info
        assert "expires_at" in info
        assert "remaining_seconds" in info
        assert "remaining_hours" in info
        assert info["remaining_seconds"] > 0

    def test_get_session_info_invalid(self):
        """Test getting info for invalid session."""
        info = get_session_info("invalid_token")
        assert info is None


# ============================================================================
# Password Verification Tests
# ============================================================================

class TestPasswordVerification:
    """Tests for password verification."""

    def test_verify_password_correct(self):
        """Test verifying correct password."""
        with patch('app.auth.settings') as mock_settings, \
             patch('app.auth._get_session_secret', return_value="test_secret"):
            mock_settings.auth_password = "correct_password"
            result = verify_password("correct_password")
        assert result is True

    def test_verify_password_incorrect(self):
        """Test verifying incorrect password."""
        with patch('app.auth.settings') as mock_settings, \
             patch('app.auth._get_session_secret', return_value="test_secret"):
            mock_settings.auth_password = "correct_password"
            result = verify_password("wrong_password")
        assert result is False

    def test_verify_password_empty(self):
        """Test verifying empty password when none configured."""
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_password = ""
            result = verify_password("")
        assert result is False

    def test_verify_password_case_sensitive(self):
        """Test password verification is case sensitive."""
        with patch('app.auth.settings') as mock_settings, \
             patch('app.auth._get_session_secret', return_value="test_secret"):
            mock_settings.auth_password = "Password123"
            assert verify_password("Password123") is True
            assert verify_password("password123") is False
            assert verify_password("PASSWORD123") is False


# ============================================================================
# Public Path Detection Tests
# ============================================================================

class TestPublicPaths:
    """Tests for public path detection."""

    def test_login_path_is_public(self):
        """Test login path is public."""
        assert _is_public_path("/api/auth/login") is True

    def test_status_path_is_public(self):
        """Test auth status path is public."""
        assert _is_public_path("/api/auth/status") is True

    def test_health_path_is_public(self):
        """Test health check path is public."""
        assert _is_public_path("/api/health") is True

    def test_root_path_is_public(self):
        """Test root path is public."""
        assert _is_public_path("/") is True
        assert _is_public_path("") is True

    def test_static_paths_are_public(self):
        """Test static file paths are public."""
        assert _is_public_path("/css/styles.css") is True
        assert _is_public_path("/js/app.js") is True

    def test_api_paths_are_not_public(self):
        """Test API paths are not public."""
        assert _is_public_path("/api/conversations/") is False
        assert _is_public_path("/api/chat/send") is False
        assert _is_public_path("/api/memories/") is False


# ============================================================================
# Token Extraction Tests
# ============================================================================

class TestTokenExtraction:
    """Tests for token extraction from requests."""

    def test_extract_token_from_cookie(self):
        """Test extracting token from cookie."""
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"session_token": "test_token_123"}
        mock_request.headers = {}

        token = get_token_from_request(mock_request)
        assert token == "test_token_123"

    def test_extract_token_from_header(self):
        """Test extracting token from Authorization header."""
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {}
        mock_request.headers = {"Authorization": "Bearer test_token_456"}

        token = get_token_from_request(mock_request)
        assert token == "test_token_456"

    def test_extract_token_cookie_priority(self):
        """Test cookie takes priority over header."""
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"session_token": "cookie_token"}
        mock_request.headers = {"Authorization": "Bearer header_token"}

        token = get_token_from_request(mock_request)
        assert token == "cookie_token"

    def test_extract_token_none(self):
        """Test extracting token when none present."""
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {}
        mock_request.headers = {}

        token = get_token_from_request(mock_request)
        assert token is None


# ============================================================================
# Auth Handlers Tests
# ============================================================================

class TestAuthHandlers:
    """Tests for auth handler functions."""

    def setup_method(self):
        """Clear active sessions before each test."""
        _active_sessions.clear()

    @pytest.mark.asyncio
    async def test_login_handler_success(self):
        """Test successful login."""
        mock_request = MagicMock(spec=Request)
        mock_request.json = AsyncMock(return_value={"password": "correct_password"})
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.url = MagicMock()
        mock_request.url.hostname = "localhost"

        with patch('app.auth.settings') as mock_settings, \
             patch('app.auth._get_session_secret', return_value="test_secret"):
            mock_settings.auth_password = "correct_password"
            mock_settings.auth_session_timeout_hours = 24

            response = await login_handler(mock_request)

        assert isinstance(response, JSONResponse)
        # Check response body
        import json
        body = json.loads(response.body.decode())
        assert body["success"] is True
        assert "session_info" in body

    @pytest.mark.asyncio
    async def test_login_handler_wrong_password(self):
        """Test login with wrong password."""
        mock_request = MagicMock(spec=Request)
        mock_request.json = AsyncMock(return_value={"password": "wrong_password"})
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with patch('app.auth.settings') as mock_settings, \
             patch('app.auth._get_session_secret', return_value="test_secret"):
            mock_settings.auth_password = "correct_password"

            with pytest.raises(Exception) as exc_info:
                await login_handler(mock_request)
            assert "Invalid password" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_logout_handler(self):
        """Test logout handler."""
        # Create a session first
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_timeout_hours = 24
            token = create_session()

        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"session_token": token}
        mock_request.headers = {}

        response = await logout_handler(mock_request)

        assert isinstance(response, JSONResponse)
        import json
        body = json.loads(response.body.decode())
        assert body["success"] is True
        # Session should be invalidated
        assert token not in _active_sessions

    @pytest.mark.asyncio
    async def test_auth_status_handler_authenticated(self):
        """Test auth status when authenticated."""
        # Create a session first
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_timeout_hours = 24
            mock_settings.auth_enabled = True
            token = create_session()

            mock_request = MagicMock(spec=Request)
            mock_request.cookies = {"session_token": token}
            mock_request.headers = {}

            response = await auth_status_handler(mock_request)

        assert isinstance(response, JSONResponse)
        import json
        body = json.loads(response.body.decode())
        assert body["auth_enabled"] is True
        assert body["authenticated"] is True
        assert "session_info" in body

    @pytest.mark.asyncio
    async def test_auth_status_handler_not_authenticated(self):
        """Test auth status when not authenticated."""
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_enabled = True

            mock_request = MagicMock(spec=Request)
            mock_request.cookies = {}
            mock_request.headers = {}

            response = await auth_status_handler(mock_request)

        assert isinstance(response, JSONResponse)
        import json
        body = json.loads(response.body.decode())
        assert body["auth_enabled"] is True
        assert body["authenticated"] is False

    @pytest.mark.asyncio
    async def test_auth_status_handler_auth_disabled(self):
        """Test auth status when auth is disabled."""
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_enabled = False

            mock_request = MagicMock(spec=Request)
            mock_request.cookies = {}
            mock_request.headers = {}

            response = await auth_status_handler(mock_request)

        assert isinstance(response, JSONResponse)
        import json
        body = json.loads(response.body.decode())
        assert body["auth_enabled"] is False
        assert body["authenticated"] is True


# ============================================================================
# Auth Middleware Tests
# ============================================================================

class TestAuthMiddleware:
    """Tests for AuthMiddleware."""

    def setup_method(self):
        """Clear active sessions before each test."""
        _active_sessions.clear()

    @pytest.mark.asyncio
    async def test_middleware_auth_disabled(self):
        """Test middleware passes through when auth disabled."""
        middleware = AuthMiddleware(app=None)

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/api/conversations/"

        mock_response = MagicMock()
        mock_call_next = AsyncMock(return_value=mock_response)

        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_enabled = False

            response = await middleware.dispatch(mock_request, mock_call_next)

        assert response == mock_response
        mock_call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_public_path_allowed(self):
        """Test middleware allows public paths without auth."""
        middleware = AuthMiddleware(app=None)

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/api/health"

        mock_response = MagicMock()
        mock_call_next = AsyncMock(return_value=mock_response)

        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_enabled = True

            response = await middleware.dispatch(mock_request, mock_call_next)

        assert response == mock_response
        mock_call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_valid_session_allowed(self):
        """Test middleware allows requests with valid session."""
        # Create valid session
        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_timeout_hours = 24
            token = create_session()

        middleware = AuthMiddleware(app=None)

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/api/conversations/"
        mock_request.cookies = {"session_token": token}
        mock_request.headers = {}

        mock_response = MagicMock()
        mock_call_next = AsyncMock(return_value=mock_response)

        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_enabled = True

            response = await middleware.dispatch(mock_request, mock_call_next)

        assert response == mock_response
        mock_call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_no_token_returns_401(self):
        """Test middleware returns 401 for API paths without token."""
        middleware = AuthMiddleware(app=None)

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/api/conversations/"
        mock_request.cookies = {}
        mock_request.headers = {}

        mock_call_next = AsyncMock()

        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_enabled = True

            response = await middleware.dispatch(mock_request, mock_call_next)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 401
        mock_call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_invalid_token_returns_401(self):
        """Test middleware returns 401 for invalid token."""
        middleware = AuthMiddleware(app=None)

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/api/conversations/"
        mock_request.cookies = {"session_token": "invalid_token"}
        mock_request.headers = {}

        mock_call_next = AsyncMock()

        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_enabled = True

            response = await middleware.dispatch(mock_request, mock_call_next)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 401
        mock_call_next.assert_not_called()


# ============================================================================
# Session Secret Tests
# ============================================================================

class TestSessionSecret:
    """Tests for session secret handling."""

    def test_get_session_secret_configured(self):
        """Test session secret uses configured value."""
        # Reset the cached secret
        import app.auth as auth_module
        auth_module._session_secret = ""

        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_secret = "my_configured_secret"
            secret = _get_session_secret()

        assert secret == "my_configured_secret"

    def test_get_session_secret_auto_generated(self):
        """Test session secret is auto-generated when not configured."""
        # Reset the cached secret
        import app.auth as auth_module
        auth_module._session_secret = ""

        with patch('app.auth.settings') as mock_settings:
            mock_settings.auth_session_secret = ""
            secret = _get_session_secret()

        assert secret is not None
        assert len(secret) == 64  # 32 bytes hex = 64 characters
