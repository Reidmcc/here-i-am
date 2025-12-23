"""
Cache Service for reducing API rate limit impact.

Provides TTL-based in-memory caching for:
- Token counting results
- Memory search results
- Full memory content lookups

Caches are designed to reduce redundant API calls and database queries
during multi-turn conversations and rapid message exchanges.
"""

from typing import Dict, Any, Optional, List, Callable, TypeVar, Generic
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import threading


T = TypeVar('T')


@dataclass
class CacheEntry(Generic[T]):
    """A single cached value with expiration."""
    value: T
    expires_at: datetime
    created_at: datetime = field(default_factory=datetime.utcnow)
    hit_count: int = 0

    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at


class TTLCache(Generic[T]):
    """
    Thread-safe TTL-based cache with automatic expiration.

    Features:
    - Configurable TTL per cache instance
    - Automatic cleanup of expired entries
    - Thread-safe operations
    - Hit/miss statistics
    """

    def __init__(
        self,
        default_ttl_seconds: int = 300,
        max_size: int = 10000,
        cleanup_interval: int = 100,  # Clean up every N operations
    ):
        self._cache: Dict[str, CacheEntry[T]] = {}
        self._default_ttl = timedelta(seconds=default_ttl_seconds)
        self._max_size = max_size
        self._cleanup_interval = cleanup_interval
        self._operation_count = 0
        self._lock = threading.RLock()

        # Statistics
        self._hits = 0
        self._misses = 0

    def _hash_key(self, key: Any) -> str:
        """Create a consistent hash key from any hashable input."""
        if isinstance(key, str):
            return key
        return hashlib.sha256(str(key).encode()).hexdigest()[:32]

    def _maybe_cleanup(self):
        """Periodically clean up expired entries."""
        self._operation_count += 1
        if self._operation_count >= self._cleanup_interval:
            self._operation_count = 0
            self._cleanup_expired()

    def _cleanup_expired(self):
        """Remove all expired entries."""
        now = datetime.utcnow()
        expired_keys = [
            key for key, entry in self._cache.items()
            if entry.expires_at < now
        ]
        for key in expired_keys:
            del self._cache[key]

    def _evict_oldest(self):
        """Evict oldest entries when cache is full."""
        if len(self._cache) >= self._max_size:
            # Remove the oldest 10% of entries
            entries = sorted(
                self._cache.items(),
                key=lambda x: x[1].created_at
            )
            to_remove = max(1, len(entries) // 10)
            for key, _ in entries[:to_remove]:
                del self._cache[key]

    def get(self, key: Any) -> Optional[T]:
        """Get a value from cache if it exists and hasn't expired."""
        with self._lock:
            self._maybe_cleanup()

            hash_key = self._hash_key(key)
            entry = self._cache.get(hash_key)

            if entry is None:
                self._misses += 1
                return None

            if entry.is_expired():
                del self._cache[hash_key]
                self._misses += 1
                return None

            entry.hit_count += 1
            self._hits += 1
            return entry.value

    def set(
        self,
        key: Any,
        value: T,
        ttl_seconds: Optional[int] = None
    ) -> None:
        """Set a value in cache with optional custom TTL."""
        with self._lock:
            self._maybe_cleanup()
            self._evict_oldest()

            hash_key = self._hash_key(key)
            ttl = timedelta(seconds=ttl_seconds) if ttl_seconds else self._default_ttl

            self._cache[hash_key] = CacheEntry(
                value=value,
                expires_at=datetime.utcnow() + ttl,
            )

    def delete(self, key: Any) -> bool:
        """Delete a specific key from cache."""
        with self._lock:
            hash_key = self._hash_key(key)
            if hash_key in self._cache:
                del self._cache[hash_key]
                return True
            return False

    def clear(self) -> int:
        """Clear all entries from cache. Returns count of entries cleared."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def invalidate_by_prefix(self, prefix: str) -> int:
        """Invalidate all entries with keys starting with prefix."""
        with self._lock:
            keys_to_delete = [
                key for key in self._cache.keys()
                if key.startswith(prefix)
            ]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_requests = self._hits + self._misses
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / total_requests if total_requests > 0 else 0,
                "ttl_seconds": self._default_ttl.total_seconds(),
            }


class CacheService:
    """
    Centralized cache service for the application.

    Provides specialized caches for different use cases:
    - token_cache: For token counting results (long TTL, rarely changes)
    - search_cache: For memory search results (short TTL, may change)
    - content_cache: For full memory content (medium TTL)
    - github_tree_cache: For GitHub repository tree structure
    - github_file_cache: For GitHub file contents
    - github_metadata_cache: For GitHub repository/branch metadata
    """

    def __init__(self):
        # Token counting cache - tokens for a given text string
        # Long TTL since token count for same text never changes
        self.token_cache: TTLCache[int] = TTLCache(
            default_ttl_seconds=3600,  # 1 hour
            max_size=50000,
        )

        # Memory search cache - search results for query + entity
        # Short TTL since new memories may be added
        self.search_cache: TTLCache[List[Dict[str, Any]]] = TTLCache(
            default_ttl_seconds=60,  # 1 minute
            max_size=1000,
        )

        # Full memory content cache - content fetched from DB
        # Medium TTL since content rarely changes
        self.content_cache: TTLCache[Dict[str, Any]] = TTLCache(
            default_ttl_seconds=300,  # 5 minutes
            max_size=5000,
        )

        # GitHub tree structure cache - repository file trees
        # 5 minute TTL since tree doesn't change frequently
        self.github_tree_cache: TTLCache[Dict[str, Any]] = TTLCache(
            default_ttl_seconds=300,  # 5 minutes
            max_size=100,
        )

        # GitHub file contents cache - individual files
        # 10 minute TTL since files rarely change during a conversation
        self.github_file_cache: TTLCache[Dict[str, Any]] = TTLCache(
            default_ttl_seconds=600,  # 10 minutes
            max_size=500,
        )

        # GitHub metadata cache - repo info, branch lists
        # 10 minute TTL
        self.github_metadata_cache: TTLCache[Dict[str, Any]] = TTLCache(
            default_ttl_seconds=600,  # 10 minutes
            max_size=200,
        )

        # GitHub PR/issue list cache - more frequently changing
        # 2 minute TTL
        self.github_list_cache: TTLCache[List[Dict[str, Any]]] = TTLCache(
            default_ttl_seconds=120,  # 2 minutes
            max_size=100,
        )

    # Token counting helpers
    def get_token_count(self, text: str) -> Optional[int]:
        """Get cached token count for text."""
        # Use text hash as key to handle large texts efficiently
        key = f"tok:{hashlib.sha256(text.encode()).hexdigest()[:16]}"
        return self.token_cache.get(key)

    def set_token_count(self, text: str, count: int) -> None:
        """Cache token count for text."""
        key = f"tok:{hashlib.sha256(text.encode()).hexdigest()[:16]}"
        self.token_cache.set(key, count)

    # Memory search helpers
    def get_search_results(
        self,
        query: str,
        entity_id: Optional[str],
        top_k: int,
        exclude_conversation_id: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get cached search results."""
        key = f"search:{entity_id}:{top_k}:{exclude_conversation_id}:{hashlib.sha256(query.encode()).hexdigest()[:16]}"
        return self.search_cache.get(key)

    def set_search_results(
        self,
        query: str,
        entity_id: Optional[str],
        top_k: int,
        exclude_conversation_id: Optional[str],
        results: List[Dict[str, Any]],
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Cache search results."""
        key = f"search:{entity_id}:{top_k}:{exclude_conversation_id}:{hashlib.sha256(query.encode()).hexdigest()[:16]}"
        self.search_cache.set(key, results, ttl_seconds)

    def invalidate_search_cache_for_entity(self, entity_id: Optional[str]) -> int:
        """Invalidate all search cache entries for an entity."""
        prefix = f"search:{entity_id}:"
        return self.search_cache.invalidate_by_prefix(prefix)

    # Memory content helpers
    def get_memory_content(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get cached memory content."""
        key = f"mem:{message_id}"
        return self.content_cache.get(key)

    def set_memory_content(self, message_id: str, content: Dict[str, Any]) -> None:
        """Cache memory content."""
        key = f"mem:{message_id}"
        self.content_cache.set(key, content)

    def invalidate_memory_content(self, message_id: str) -> bool:
        """Invalidate cached content for a specific memory."""
        key = f"mem:{message_id}"
        return self.content_cache.delete(key)

    # GitHub cache helpers
    def get_github_tree(
        self,
        repo_label: str,
        ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get cached GitHub tree."""
        key = f"gh_tree:{repo_label}:{ref or 'HEAD'}"
        return self.github_tree_cache.get(key)

    def set_github_tree(
        self,
        repo_label: str,
        ref: Optional[str],
        tree_data: Dict[str, Any],
    ) -> None:
        """Cache GitHub tree."""
        key = f"gh_tree:{repo_label}:{ref or 'HEAD'}"
        self.github_tree_cache.set(key, tree_data)

    def get_github_file(
        self,
        repo_label: str,
        path: str,
        ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get cached GitHub file contents."""
        key = f"gh_file:{repo_label}:{ref or 'HEAD'}:{path}"
        return self.github_file_cache.get(key)

    def set_github_file(
        self,
        repo_label: str,
        path: str,
        ref: Optional[str],
        file_data: Dict[str, Any],
    ) -> None:
        """Cache GitHub file contents."""
        key = f"gh_file:{repo_label}:{ref or 'HEAD'}:{path}"
        self.github_file_cache.set(key, file_data)

    def get_github_metadata(
        self,
        repo_label: str,
        metadata_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Get cached GitHub metadata (repo info, branches, etc.)."""
        key = f"gh_meta:{repo_label}:{metadata_type}"
        return self.github_metadata_cache.get(key)

    def set_github_metadata(
        self,
        repo_label: str,
        metadata_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Cache GitHub metadata."""
        key = f"gh_meta:{repo_label}:{metadata_type}"
        self.github_metadata_cache.set(key, data)

    def get_github_list(
        self,
        repo_label: str,
        list_type: str,
        state: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get cached GitHub list (PRs, issues, etc.)."""
        key = f"gh_list:{repo_label}:{list_type}:{state or 'all'}"
        return self.github_list_cache.get(key)

    def set_github_list(
        self,
        repo_label: str,
        list_type: str,
        state: Optional[str],
        data: List[Dict[str, Any]],
    ) -> None:
        """Cache GitHub list."""
        key = f"gh_list:{repo_label}:{list_type}:{state or 'all'}"
        self.github_list_cache.set(key, data)

    def invalidate_github_cache_for_repo(self, repo_label: str) -> int:
        """Invalidate all GitHub cache entries for a repository."""
        count = 0
        count += self.github_tree_cache.invalidate_by_prefix(f"gh_tree:{repo_label}:")
        count += self.github_file_cache.invalidate_by_prefix(f"gh_file:{repo_label}:")
        count += self.github_metadata_cache.invalidate_by_prefix(f"gh_meta:{repo_label}:")
        count += self.github_list_cache.invalidate_by_prefix(f"gh_list:{repo_label}:")
        return count

    def invalidate_github_tree(self, repo_label: str, ref: Optional[str] = None) -> int:
        """Invalidate GitHub tree cache for a repo/ref."""
        if ref:
            key = f"gh_tree:{repo_label}:{ref}"
            return 1 if self.github_tree_cache.delete(key) else 0
        return self.github_tree_cache.invalidate_by_prefix(f"gh_tree:{repo_label}:")

    def invalidate_github_file(
        self,
        repo_label: str,
        path: str,
        ref: Optional[str] = None,
    ) -> int:
        """Invalidate GitHub file cache for a specific path."""
        if ref:
            key = f"gh_file:{repo_label}:{ref}:{path}"
            return 1 if self.github_file_cache.delete(key) else 0
        # Invalidate for all refs
        count = 0
        # We can't easily invalidate by path without ref, so invalidate all files for this repo
        count += self.github_file_cache.invalidate_by_prefix(f"gh_file:{repo_label}:")
        return count

    # Utility methods
    def clear_all(self) -> Dict[str, int]:
        """Clear all caches. Returns count of entries cleared per cache."""
        return {
            "token_cache": self.token_cache.clear(),
            "search_cache": self.search_cache.clear(),
            "content_cache": self.content_cache.clear(),
            "github_tree_cache": self.github_tree_cache.clear(),
            "github_file_cache": self.github_file_cache.clear(),
            "github_metadata_cache": self.github_metadata_cache.clear(),
            "github_list_cache": self.github_list_cache.clear(),
        }

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all caches."""
        return {
            "token_cache": self.token_cache.get_stats(),
            "search_cache": self.search_cache.get_stats(),
            "content_cache": self.content_cache.get_stats(),
            "github_tree_cache": self.github_tree_cache.get_stats(),
            "github_file_cache": self.github_file_cache.get_stats(),
            "github_metadata_cache": self.github_metadata_cache.get_stats(),
            "github_list_cache": self.github_list_cache.get_stats(),
        }


# Singleton instance
cache_service = CacheService()
