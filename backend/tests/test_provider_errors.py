"""
Unit tests for provider error classification.

The tool loop retries an iteration only when the failure looks transient, so
the cost of a misclassification is asymmetric: calling a permanent failure
retryable wastes API calls on a request that cannot succeed, while calling a
transient failure permanent just falls through to the (safe) resume path.
"""
import httpx
import pytest

from app.services.provider_errors import (
    classify_stream_error,
    describe_error_event,
    is_retryable_error_event,
    stream_error_event,
)


def _status_error(status_code: int) -> Exception:
    """An SDK-style error exposing status_code directly."""
    return type("APIStatusError", (Exception,), {"status_code": status_code})("boom")


class TestClassifyStreamError:
    """Tests for classify_stream_error."""

    @pytest.mark.parametrize(
        "status,expected_type",
        [
            (429, "rate_limit"),
            (529, "overloaded"),
            (500, "server_error"),
            (502, "server_error"),
            (503, "server_error"),
            (504, "server_error"),
            (508, "server_error"),  # unlisted 5xx is still server-side
        ],
    )
    def test_transient_status_codes_are_retryable(self, status, expected_type):
        error_type, retryable = classify_stream_error(_status_error(status))
        assert (error_type, retryable) == (expected_type, True)

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
    def test_client_error_status_codes_are_not_retryable(self, status):
        error_type, retryable = classify_stream_error(_status_error(status))
        assert error_type == f"http_{status}"
        assert retryable is False

    def test_status_code_read_from_attached_response(self):
        """httpx-level errors carry the status on a response object."""
        exc = type(
            "HTTPStatusError",
            (Exception,),
            {"response": type("Response", (), {"status_code": 529})()},
        )("boom")
        assert classify_stream_error(exc) == ("overloaded", True)

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("refused"),
            httpx.RemoteProtocolError("truncated"),
            TimeoutError("timed out"),
            ConnectionResetError("reset"),
        ],
    )
    def test_connection_failures_are_retryable(self, exc):
        error_type, retryable = classify_stream_error(exc)
        assert (error_type, retryable) == ("connection", True)

    def test_sdk_connection_subclass_matched_through_ancestry(self):
        """SDK wrappers subclass their own base; the whole MRO is checked."""
        base = type("APIConnectionError", (Exception,), {})
        subclass = type("APITimeoutError", (base,), {})
        assert classify_stream_error(subclass("timed out")) == ("connection", True)

    def test_unknown_errors_are_not_retryable(self):
        """Anything unrecognized degrades to the safe answer."""
        assert classify_stream_error(ValueError("something odd")) == ("unknown", False)


class TestStreamErrorEvent:
    """Tests for the event helpers the streaming services and loop use."""

    def test_event_carries_message_type_and_retryability(self):
        event = stream_error_event(_status_error(529))
        assert event == {
            "type": "error",
            "error": "boom",
            "error_type": "overloaded",
            "retryable": True,
        }

    def test_is_retryable_error_event(self):
        assert is_retryable_error_event(stream_error_event(_status_error(429))) is True
        assert is_retryable_error_event(stream_error_event(_status_error(400))) is False
        # Errors raised outside the provider services carry no classification
        assert is_retryable_error_event({"type": "error", "error": "Unknown model"}) is False
        assert is_retryable_error_event(None) is False

    def test_describe_error_event(self):
        assert describe_error_event(stream_error_event(_status_error(429))) == "rate_limit: boom"
        assert describe_error_event(None) == "unknown error"
