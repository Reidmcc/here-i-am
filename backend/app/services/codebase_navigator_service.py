"""
Codebase Navigator Service.

High-level service for codebase navigation that integrates the indexer,
client, and caching components.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

from app.config import settings
from app.services.codebase_navigator import (
    CodebaseIndexer,
    NavigatorClient,
    NavigatorResponse,
    QueryType,
    NavigatorNotConfiguredError,
)

logger = logging.getLogger(__name__)


class CodebaseNavigatorService:
    """
    High-level service for codebase navigation.

    Manages:
    - Codebase indexing with caching
    - Navigator client lifecycle
    - Query execution and response formatting
    """

    def __init__(self):
        """Initialize the navigator service."""
        self._client: Optional[NavigatorClient] = None
        self._indexers: Dict[str, CodebaseIndexer] = {}  # Cache by root path

    @property
    def client(self) -> NavigatorClient:
        """Lazy-initialize the navigator client."""
        if self._client is None:
            if not settings.codebase_navigator_enabled:
                raise NavigatorNotConfiguredError("Codebase navigator is not enabled")
            if not settings.mistral_api_key:
                raise NavigatorNotConfiguredError("Mistral API key not configured")

            cache_dir = Path(settings.codebase_navigator_cache_dir) if settings.codebase_navigator_cache_enabled else None

            self._client = NavigatorClient(
                api_key=settings.mistral_api_key,
                model=settings.codebase_navigator_model,
                timeout=settings.codebase_navigator_timeout,
                max_retries=settings.codebase_navigator_max_retries,
                cache_dir=cache_dir,
                cache_ttl_hours=settings.codebase_navigator_cache_ttl_hours,
                cache_enabled=settings.codebase_navigator_cache_enabled,
            )

        return self._client

    def is_configured(self) -> bool:
        """Check if the navigator is properly configured."""
        return (
            settings.codebase_navigator_enabled
            and bool(settings.mistral_api_key)
        )

    def get_indexer(
        self,
        root_path: str,
        include_patterns: Optional[list] = None,
        exclude_patterns: Optional[list] = None,
        force_reindex: bool = False,
    ) -> CodebaseIndexer:
        """
        Get or create an indexer for a codebase.

        Args:
            root_path: Root directory of the codebase
            include_patterns: Override default include patterns
            exclude_patterns: Override default exclude patterns
            force_reindex: Force re-indexing even if cached

        Returns:
            Indexed CodebaseIndexer ready for queries
        """
        path = Path(root_path).resolve()
        path_str = str(path)

        # Check if we have a cached indexer
        if path_str in self._indexers and not force_reindex:
            return self._indexers[path_str]

        # Create new indexer
        indexer = CodebaseIndexer(
            root_path=path,
            max_tokens_per_chunk=settings.codebase_navigator_max_tokens_per_chunk,
            include_patterns=include_patterns or settings.get_navigator_include_patterns(),
            exclude_patterns=exclude_patterns or settings.get_navigator_exclude_patterns(),
        )

        # Index the codebase
        indexer.index()

        # Cache for later use
        self._indexers[path_str] = indexer

        return indexer

    async def navigate(
        self,
        task: str,
        repo_path: str = ".",
        query_type: str = "relevance",
        bypass_cache: bool = False,
    ) -> NavigatorResponse:
        """
        Navigate a codebase to find relevant files for a task.

        This is the main entry point for navigation queries.

        Args:
            task: Natural language description of what needs to be done
            repo_path: Path to the repository root
            query_type: Type of query (relevance, structure, dependencies, entry_points, impact)
            bypass_cache: Skip cache lookup

        Returns:
            NavigatorResponse with relevant files and suggestions
        """
        # Validate query type
        try:
            qt = QueryType(query_type)
        except ValueError:
            qt = QueryType.RELEVANCE
            logger.warning(f"Invalid query_type '{query_type}', defaulting to 'relevance'")

        # Get indexer (creates and indexes if needed)
        indexer = self.get_indexer(repo_path)

        # Execute query
        response = await self.client.query_with_indexer(
            task=task,
            indexer=indexer,
            query_type=qt,
            bypass_cache=bypass_cache,
        )

        return response

    def invalidate_cache(self, repo_path: str) -> int:
        """
        Invalidate cached results for a codebase.

        Call this when the codebase has been modified.

        Args:
            repo_path: Path to the repository root

        Returns:
            Number of cache entries invalidated
        """
        path = Path(repo_path).resolve()
        path_str = str(path)

        # Remove cached indexer
        if path_str in self._indexers:
            codebase_hash = self._indexers[path_str].get_codebase_hash()
            del self._indexers[path_str]

            # Invalidate cache entries
            if self._client:
                return self._client.invalidate_cache(codebase_hash)

        return 0

    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        stats = {
            "configured": self.is_configured(),
            "indexed_repos": len(self._indexers),
        }

        if self._client:
            stats["cache"] = self._client.get_cache_stats()

        return stats

    def close(self) -> None:
        """Close service resources."""
        if self._client:
            self._client.close()
            self._client = None
        self._indexers.clear()


# Singleton instance
codebase_navigator_service = CodebaseNavigatorService()
