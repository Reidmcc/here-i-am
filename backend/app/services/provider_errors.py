"""
Provider error classification.

The streaming services catch every exception and surface it as an
`{"type": "error"}` event. The tool loop needs to know whether that error is
worth retrying (the provider was overloaded, the connection dropped) or
permanent (a malformed request, a bad API key), so it can transparently
re-run an iteration instead of throwing away the tool work already done.

Classification is deliberately duck-typed rather than keyed on SDK exception
classes: the Anthropic, OpenAI and Google SDKs are optional dependencies of
one another here, and MiniMax rides on the Anthropic client. Anything we
cannot positively identify is reported as NOT retryable, so an unknown
failure falls through to the resume path (which is safe) instead of being
replayed against the provider.
"""

from typing import Any, Optional, Tuple


# Transient server-side conditions. 529 is Anthropic's "overloaded".
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504, 529}

# Client-side problems: retrying sends the identical request and fails again.
FATAL_STATUS_CODES = {400, 401, 403, 404, 405, 409, 410, 413, 414, 415, 422}

# Connection/timeout failures, by exception class name. Covers the SDK
# wrappers (anthropic/openai APIConnectionError, APITimeoutError), the httpx
# transport errors underneath them, and the stdlib/asyncio timeouts.
CONNECTION_ERROR_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "APIConnectionTimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "ReadError",
    "WriteError",
    "RemoteProtocolError",
    "ProtocolError",
    "ConnectionResetError",
    "IncompleteRead",
    "ServerDisconnectedError",
    "ClientConnectorError",
    "TimeoutError",
    "ConnectionError",
}

# Named error types, most specific first, so the UI can say something more
# useful than "an error occurred".
STATUS_ERROR_TYPES = {
    429: "rate_limit",
    529: "overloaded",
}


def _extract_status_code(exc: BaseException) -> Optional[int]:
    """
    Pull an HTTP status code off an exception, if it carries one.

    SDK errors expose `status_code` directly; httpx-level errors carry it on
    an attached `response`.
    """
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int):
            return candidate
    return None


def classify_stream_error(exc: BaseException) -> Tuple[str, bool]:
    """
    Classify a streaming exception.

    Returns (error_type, retryable). error_type is a short stable slug for
    logs and the UI ("overloaded", "rate_limit", "server_error", "connection",
    "http_400", "unknown"); retryable says whether re-sending the identical
    request has a chance of succeeding.
    """
    status = _extract_status_code(exc)
    if status is not None:
        if status in FATAL_STATUS_CODES:
            return (f"http_{status}", False)
        if status in RETRYABLE_STATUS_CODES:
            return (STATUS_ERROR_TYPES.get(status, "server_error"), True)
        # Unlisted 5xx are still server-side; unlisted 4xx are still ours.
        if status >= 500:
            return ("server_error", True)
        return (f"http_{status}", False)

    # No status code: look for a connection/timeout failure by class name,
    # walking the exception's ancestry so SDK subclasses are caught too.
    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & CONNECTION_ERROR_NAMES:
        return ("connection", True)

    return ("unknown", False)


def stream_error_event(exc: BaseException) -> dict:
    """Build the `{"type": "error"}` stream event for an exception."""
    error_type, retryable = classify_stream_error(exc)
    return {
        "type": "error",
        "error": str(exc),
        "error_type": error_type,
        "retryable": retryable,
    }


def is_retryable_error_event(event: Optional[dict]) -> bool:
    """Whether an error event came from a transient, worth-retrying failure."""
    return bool(event) and bool(event.get("retryable"))


def describe_error_event(event: Optional[dict]) -> str:
    """Human-readable one-liner for an error event, for logging."""
    if not event:
        return "unknown error"
    error_type: Any = event.get("error_type") or "unknown"
    return f"{error_type}: {event.get('error', '')}"
