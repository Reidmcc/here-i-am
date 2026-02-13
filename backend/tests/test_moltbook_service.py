"""
Tests for moltbook_service.py - Moltbook social network service.

Tests cover:
- BearerAuth: Authentication flow
- MoltbookService: URL validation, content truncation, response wrapping,
  rate limit handling, error formatting, and all API operations
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx

from app.services.moltbook_service import (
    BearerAuth,
    MoltbookService,
    SECURITY_BANNER,
    MAX_RESPONSE_CHARS,
)


# ============================================================
# Tests for BearerAuth
# ============================================================

class TestBearerAuth:
    """Tests for the Bearer token authentication handler."""

    def test_auth_flow_adds_header(self):
        """Should add Authorization header to request."""
        auth = BearerAuth("test-token")
        request = httpx.Request("GET", "https://example.com")

        # Simulate auth_flow
        gen = auth.auth_flow(request)
        modified_request = next(gen)

        assert "Authorization" in modified_request.headers
        assert modified_request.headers["Authorization"] == "Bearer test-token"


# ============================================================
# Tests for MoltbookService - URL Validation
# ============================================================

class TestMoltbookServiceURLValidation:
    """Tests for API URL validation and correction."""

    @patch("app.services.moltbook_service.settings")
    def test_validates_www_url(self, mock_settings):
        """Should accept www URL without modification."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()
        assert service._validated_api_url == "https://www.moltbook.com/api/v1"

    @patch("app.services.moltbook_service.settings")
    def test_corrects_non_www_url(self, mock_settings):
        """Should correct non-www URL to www."""
        mock_settings.moltbook_api_url = "https://moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()
        assert "www.moltbook.com" in service._validated_api_url

    @patch("app.services.moltbook_service.settings")
    def test_empty_url_defaults(self, mock_settings):
        """Should use default URL when empty."""
        mock_settings.moltbook_api_url = ""
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()
        assert service._validated_api_url == "https://www.moltbook.com/api/v1"


# ============================================================
# Tests for MoltbookService - Content Processing
# ============================================================

class TestMoltbookServiceContentProcessing:
    """Tests for content truncation and response wrapping."""

    @patch("app.services.moltbook_service.settings")
    def test_truncate_short_content(self, mock_settings):
        """Should not truncate content under limit."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        content = "Short content"
        assert service._truncate_content(content) == content

    @patch("app.services.moltbook_service.settings")
    def test_truncate_long_content(self, mock_settings):
        """Should truncate content over limit."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        content = "x" * (MAX_RESPONSE_CHARS + 1000)
        result = service._truncate_content(content)
        assert len(result) < len(content)
        assert "truncated" in result.lower()

    @patch("app.services.moltbook_service.settings")
    def test_wrap_response_dict(self, mock_settings):
        """Should wrap dict response with security banner."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        result = service._wrap_response({"key": "value"})
        assert "UNTRUSTED EXTERNAL CONTENT" in result
        assert '"key": "value"' in result

    @patch("app.services.moltbook_service.settings")
    def test_wrap_response_list(self, mock_settings):
        """Should wrap list response with security banner."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        result = service._wrap_response([1, 2, 3])
        assert "UNTRUSTED EXTERNAL CONTENT" in result

    @patch("app.services.moltbook_service.settings")
    def test_wrap_response_string(self, mock_settings):
        """Should wrap string response with security banner."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        result = service._wrap_response("plain text")
        assert "UNTRUSTED EXTERNAL CONTENT" in result
        assert "plain text" in result


# ============================================================
# Tests for MoltbookService - Error Handling
# ============================================================

class TestMoltbookServiceErrorHandling:
    """Tests for error formatting and rate limit handling."""

    @patch("app.services.moltbook_service.settings")
    def test_format_error_details_basic(self, mock_settings):
        """Should extract message from error response."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        data = {"message": "Something went wrong"}
        result = service._format_error_details(data)
        assert "Something went wrong" in result

    @patch("app.services.moltbook_service.settings")
    def test_format_error_details_multiple_fields(self, mock_settings):
        """Should combine multiple error fields."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        data = {"message": "Error", "error_code": "E001"}
        result = service._format_error_details(data)
        assert "Error" in result
        assert "E001" in result

    @patch("app.services.moltbook_service.settings")
    def test_format_error_details_empty(self, mock_settings):
        """Should return empty string for no error details."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        result = service._format_error_details({})
        assert result == ""

    @patch("app.services.moltbook_service.settings")
    def test_handle_rate_limit_basic(self, mock_settings):
        """Should format rate limit error."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        data = {"message": "Too many requests"}
        result = service._handle_rate_limit(data)
        assert "Rate limit exceeded" in result

    @patch("app.services.moltbook_service.settings")
    def test_handle_rate_limit_with_retry_info(self, mock_settings):
        """Should include retry timing information."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        data = {"message": "Too many", "retry_after": 30}
        result = service._handle_rate_limit(data)
        assert "Retry after: 30 seconds" in result

    @patch("app.services.moltbook_service.settings")
    def test_handle_auth_error(self, mock_settings):
        """Should format auth error with action context."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        data = {"message": "Invalid token"}
        result = service._handle_auth_error(data, "creating post")
        assert "creating post" in result
        assert "Invalid token" in result

    @patch("app.services.moltbook_service.settings")
    def test_format_error_generic(self, mock_settings):
        """Should format a generic error response."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        result = service._format_error(500, {"message": "Internal error"})
        assert "Error 500" in result
        assert "Internal error" in result


# ============================================================
# Tests for MoltbookService - API Operations
# ============================================================

class TestMoltbookServiceOperations:
    """Tests for API operations (feed, posts, comments, etc.)."""

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_get_feed_success(self, mock_settings):
        """Should return feed data on success."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, [{"title": "Post 1"}])

            success, result = await service.get_feed()
            assert success is True
            assert "UNTRUSTED EXTERNAL CONTENT" in result

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_get_feed_rate_limited(self, mock_settings):
        """Should handle rate limit on feed request."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (429, {"message": "Rate limited"})

            success, result = await service.get_feed()
            assert success is False
            assert "Rate limit" in result

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_get_feed_personal(self, mock_settings):
        """Should request personal feed endpoint."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, [])

            await service.get_feed(feed_type="personal")
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[0][1] == "/feed"  # personal endpoint

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_get_submolt_feed_not_found(self, mock_settings):
        """Should handle submolt not found."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (404, {})

            success, result = await service.get_submolt_feed("nonexistent")
            assert success is False
            assert "not found" in result

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_create_post_success(self, mock_settings):
        """Should create post successfully."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (201, {"id": "post-1"})

            success, result = await service.create_post(
                submolt="test",
                title="Test Post",
                content="Content here",
            )
            assert success is True

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_create_post_auth_error(self, mock_settings):
        """Should handle auth error when creating post."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (401, {"message": "Unauthorized"})

            success, result = await service.create_post(
                submolt="test",
                title="Test",
            )
            assert success is False
            assert "Authorization failed" in result

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_create_comment_success(self, mock_settings):
        """Should create comment successfully."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (201, {"id": "comment-1"})

            success, result = await service.create_comment(
                post_id="post-1",
                content="Great post!",
            )
            assert success is True

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_vote_upvote_post(self, mock_settings):
        """Should upvote a post."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, {"vote": "up"})

            success, result = await service.vote("post", "post-1", "up")
            assert success is True
            call_args = mock_request.call_args
            assert "/posts/post-1/upvote" in call_args[0][1]

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_vote_downvote_comment(self, mock_settings):
        """Should downvote a comment."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, {"vote": "down"})

            success, result = await service.vote("comment", "comment-1", "down")
            assert success is True
            call_args = mock_request.call_args
            assert "/comments/comment-1/downvote" in call_args[0][1]

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_search_success(self, mock_settings):
        """Should search content successfully."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, {"results": []})

            success, result = await service.search("AI ethics")
            assert success is True

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_search_truncates_long_query(self, mock_settings):
        """Should truncate query to 500 chars."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, {"results": []})

            long_query = "x" * 1000
            await service.search(long_query)
            call_args = mock_request.call_args
            params = call_args.kwargs.get("params", {})
            assert len(params["q"]) <= 500

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_get_profile_self(self, mock_settings):
        """Should get own profile when no name specified."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, {"name": "self"})

            success, result = await service.get_profile()
            assert success is True
            call_args = mock_request.call_args
            assert call_args[0][1] == "/agents/me"

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_get_profile_other(self, mock_settings):
        """Should get other agent's profile."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, {"name": "other-agent"})

            success, result = await service.get_profile("other-agent")
            assert success is True
            call_args = mock_request.call_args
            assert call_args[0][1] == "/agents/profile"

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_follow_agent(self, mock_settings):
        """Should follow an agent."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, {})

            success, result = await service.follow("agent-name", "follow")
            assert success is True
            assert "followed" in result

            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_unfollow_agent(self, mock_settings):
        """Should unfollow an agent."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, {})

            success, result = await service.follow("agent-name", "unfollow")
            assert success is True
            assert "unfollowed" in result

            call_args = mock_request.call_args
            assert call_args[0][0] == "DELETE"

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_subscribe_to_submolt(self, mock_settings):
        """Should subscribe to a submolt."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, {})

            success, result = await service.subscribe("ai-ethics", "subscribe")
            assert success is True
            assert "subscribed to" in result

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_list_submolts(self, mock_settings):
        """Should list all submolts."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, [{"name": "general"}])

            success, result = await service.list_submolts()
            assert success is True

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_get_post_success(self, mock_settings):
        """Should get a post with comments."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            # First call: get post, second call: get comments
            mock_request.side_effect = [
                (200, {"title": "Test Post"}),
                (200, [{"content": "Comment 1"}]),
            ]

            success, result = await service.get_post("post-1")
            assert success is True
            assert "Test Post" in result

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_get_post_not_found(self, mock_settings):
        """Should handle post not found."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (404, {})

            success, result = await service.get_post("nonexistent")
            assert success is False
            assert "not found" in result

    @patch("app.services.moltbook_service.settings")
    @pytest.mark.asyncio
    async def test_feed_limit_clamped(self, mock_settings):
        """Should clamp feed limit to valid range."""
        mock_settings.moltbook_api_url = "https://www.moltbook.com/api/v1"
        mock_settings.moltbook_api_key = "test-key"
        service = MoltbookService()

        with patch.object(service, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = (200, [])

            await service.get_feed(limit=100)
            call_args = mock_request.call_args
            params = call_args.kwargs.get("params", {})
            assert params["limit"] == 50

            await service.get_feed(limit=-5)
            call_args = mock_request.call_args
            params = call_args.kwargs.get("params", {})
            assert params["limit"] == 1
