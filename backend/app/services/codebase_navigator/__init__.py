"""
Codebase Navigator module.

Provides cost-efficient codebase exploration using Mistral Devstral model.
This allows the AI entity to delegate codebase exploration tasks to a specialized
model that can ingest large portions of codebases within its 256k context window.

Main components:
- CodebaseIndexer: Indexes and chunks codebases
- NavigatorClient: Communicates with Mistral API
- NavigatorCache: Caches responses to avoid redundant queries
- Models: Data structures for queries and responses

Usage:
    from app.services.codebase_navigator import (
        CodebaseIndexer,
        NavigatorClient,
        QueryType,
    )

    # Index the codebase
    indexer = CodebaseIndexer(Path("./my-project"))
    index = indexer.index()

    # Query for relevant files
    client = NavigatorClient()
    response = await client.query_with_indexer(
        task="Add user authentication",
        indexer=indexer,
        query_type=QueryType.RELEVANCE,
    )
"""

from .models import (
    QueryType,
    FileInfo,
    FileContent,
    CodebaseChunk,
    CodebaseIndex,
    CodeSection,
    RelevantFile,
    NavigatorResponse,
    NavigatorQuery,
)
from .indexer import CodebaseIndexer
from .client import NavigatorClient
from .cache import NavigatorCache, CacheKey
from .exceptions import (
    NavigatorError,
    CodebaseTooLargeError,
    NavigatorAPIError,
    InvalidResponseError,
    IndexingError,
    NavigatorNotConfiguredError,
    RateLimitError,
)

__all__ = [
    # Models
    "QueryType",
    "FileInfo",
    "FileContent",
    "CodebaseChunk",
    "CodebaseIndex",
    "CodeSection",
    "RelevantFile",
    "NavigatorResponse",
    "NavigatorQuery",
    # Classes
    "CodebaseIndexer",
    "NavigatorClient",
    "NavigatorCache",
    "CacheKey",
    # Exceptions
    "NavigatorError",
    "CodebaseTooLargeError",
    "NavigatorAPIError",
    "InvalidResponseError",
    "IndexingError",
    "NavigatorNotConfiguredError",
    "RateLimitError",
]
