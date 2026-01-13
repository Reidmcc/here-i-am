"""
Custom exceptions for the Codebase Navigator.
"""


class NavigatorError(Exception):
    """Base exception for navigator errors."""
    pass


class CodebaseTooLargeError(NavigatorError):
    """Codebase exceeds maximum processable size even with chunking."""

    def __init__(self, total_tokens: int, max_tokens: int, message: str = None):
        self.total_tokens = total_tokens
        self.max_tokens = max_tokens
        super().__init__(
            message or f"Codebase has {total_tokens:,} tokens, exceeds maximum of {max_tokens:,}"
        )


class NavigatorAPIError(NavigatorError):
    """Error communicating with the navigator API (Mistral/Devstral)."""

    def __init__(self, message: str, status_code: int = None, response_body: str = None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class InvalidResponseError(NavigatorError):
    """Navigator returned unparseable or invalid response."""

    def __init__(self, message: str, raw_response: str = None):
        self.raw_response = raw_response
        super().__init__(message)


class IndexingError(NavigatorError):
    """Error while indexing codebase."""
    pass


class NavigatorNotConfiguredError(NavigatorError):
    """Navigator is not properly configured (e.g., missing API key)."""
    pass


class RateLimitError(NavigatorAPIError):
    """Rate limit exceeded when calling the navigator API."""

    def __init__(self, retry_after: int = None):
        self.retry_after = retry_after
        message = f"Rate limit exceeded"
        if retry_after:
            message += f", retry after {retry_after} seconds"
        super().__init__(message)
