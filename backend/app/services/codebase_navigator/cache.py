"""
Cache for Codebase Navigator responses.

Uses SQLite for persistent storage of navigator responses to avoid redundant API calls.
"""

import json
import logging
import hashlib
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from .models import NavigatorResponse, QueryType

logger = logging.getLogger(__name__)


@dataclass
class CacheKey:
    """Key for cache lookups."""
    codebase_hash: str  # Hash of file contents/metadata
    task_hash: str      # Hash of task description
    query_type: QueryType

    def to_string(self) -> str:
        """Convert to a string key."""
        return f"{self.codebase_hash}:{self.task_hash}:{self.query_type.value}"

    @staticmethod
    def from_task(codebase_hash: str, task: str, query_type: QueryType) -> "CacheKey":
        """Create a cache key from a task."""
        task_hash = hashlib.sha256(task.encode()).hexdigest()[:16]
        return CacheKey(
            codebase_hash=codebase_hash,
            task_hash=task_hash,
            query_type=query_type,
        )


class NavigatorCache:
    """
    Caches navigator responses to avoid redundant queries.

    Cache invalidation:
    - Codebase hash changes (files modified)
    - TTL expiration (default: 24 hours for same codebase)
    - Manual invalidation

    Storage:
    - SQLite database in cache_dir
    - Stores serialized NavigatorResponse objects
    """

    def __init__(
        self,
        cache_dir: Path,
        ttl_hours: int = 24,
        enabled: bool = True,
    ):
        """
        Initialize the cache.

        Args:
            cache_dir: Directory to store the SQLite database
            ttl_hours: Time-to-live for cached entries in hours
            enabled: Whether caching is enabled
        """
        self.cache_dir = Path(cache_dir)
        self.ttl_hours = ttl_hours
        self.enabled = enabled
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._hits = 0
        self._misses = 0

        if self.enabled:
            self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            db_path = self.cache_dir / "navigator_cache.db"

            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

            # Create table if not exists
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS navigator_cache (
                    cache_key TEXT PRIMARY KEY,
                    codebase_hash TEXT NOT NULL,
                    task_hash TEXT NOT NULL,
                    query_type TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)

            # Create index on codebase_hash for efficient invalidation
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_codebase_hash
                ON navigator_cache(codebase_hash)
            """)

            self._conn.commit()
            logger.info(f"Navigator cache initialized at {db_path}")

        except Exception as e:
            logger.warning(f"Failed to initialize cache: {e}")
            self.enabled = False

    def get(self, key: CacheKey) -> Optional[NavigatorResponse]:
        """
        Get a cached response if it exists and is not expired.

        Args:
            key: The cache key to look up

        Returns:
            NavigatorResponse if found and valid, None otherwise
        """
        if not self.enabled or not self._conn:
            return None

        with self._lock:
            try:
                cursor = self._conn.execute(
                    """
                    SELECT response_json, expires_at FROM navigator_cache
                    WHERE cache_key = ?
                    """,
                    (key.to_string(),)
                )
                row = cursor.fetchone()

                if not row:
                    self._misses += 1
                    return None

                # Check expiration
                expires_at = datetime.fromisoformat(row["expires_at"])
                if datetime.utcnow() > expires_at:
                    # Entry expired, delete it
                    self._conn.execute(
                        "DELETE FROM navigator_cache WHERE cache_key = ?",
                        (key.to_string(),)
                    )
                    self._conn.commit()
                    self._misses += 1
                    return None

                # Parse and return
                response_data = json.loads(row["response_json"])
                response = NavigatorResponse.from_dict(response_data)
                response.cached = True
                self._hits += 1
                return response

            except Exception as e:
                logger.warning(f"Cache get error: {e}")
                self._misses += 1
                return None

    def set(self, key: CacheKey, response: NavigatorResponse) -> None:
        """
        Store a response in the cache.

        Args:
            key: The cache key
            response: The response to cache
        """
        if not self.enabled or not self._conn:
            return

        with self._lock:
            try:
                now = datetime.utcnow()
                expires_at = now + timedelta(hours=self.ttl_hours)

                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO navigator_cache
                    (cache_key, codebase_hash, task_hash, query_type, response_json, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key.to_string(),
                        key.codebase_hash,
                        key.task_hash,
                        key.query_type.value,
                        json.dumps(response.to_dict()),
                        now.isoformat(),
                        expires_at.isoformat(),
                    )
                )
                self._conn.commit()
                logger.debug(f"Cached response for key: {key.to_string()}")

            except Exception as e:
                logger.warning(f"Cache set error: {e}")

    def invalidate_codebase(self, codebase_hash: str) -> int:
        """
        Invalidate all cached entries for a codebase.

        Args:
            codebase_hash: The hash of the codebase to invalidate

        Returns:
            Number of entries invalidated
        """
        if not self.enabled or not self._conn:
            return 0

        with self._lock:
            try:
                cursor = self._conn.execute(
                    "DELETE FROM navigator_cache WHERE codebase_hash = ?",
                    (codebase_hash,)
                )
                self._conn.commit()
                count = cursor.rowcount
                if count > 0:
                    logger.info(f"Invalidated {count} cache entries for codebase {codebase_hash}")
                return count

            except Exception as e:
                logger.warning(f"Cache invalidation error: {e}")
                return 0

    def clear_expired(self) -> int:
        """
        Clear all expired entries from the cache.

        Returns:
            Number of entries cleared
        """
        if not self.enabled or not self._conn:
            return 0

        with self._lock:
            try:
                now = datetime.utcnow().isoformat()
                cursor = self._conn.execute(
                    "DELETE FROM navigator_cache WHERE expires_at < ?",
                    (now,)
                )
                self._conn.commit()
                count = cursor.rowcount
                if count > 0:
                    logger.info(f"Cleared {count} expired cache entries")
                return count

            except Exception as e:
                logger.warning(f"Cache cleanup error: {e}")
                return 0

    def clear_all(self) -> int:
        """
        Clear all entries from the cache.

        Returns:
            Number of entries cleared
        """
        if not self.enabled or not self._conn:
            return 0

        with self._lock:
            try:
                cursor = self._conn.execute("DELETE FROM navigator_cache")
                self._conn.commit()
                count = cursor.rowcount
                logger.info(f"Cleared all {count} cache entries")
                return count

            except Exception as e:
                logger.warning(f"Cache clear error: {e}")
                return 0

    def get_stats(self) -> dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0

        stats = {
            "enabled": self.enabled,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1%}",
            "ttl_hours": self.ttl_hours,
        }

        if self.enabled and self._conn:
            with self._lock:
                try:
                    cursor = self._conn.execute("SELECT COUNT(*) as count FROM navigator_cache")
                    stats["entries"] = cursor.fetchone()["count"]
                except Exception:
                    stats["entries"] = "unknown"

        return stats

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            with self._lock:
                try:
                    self._conn.close()
                    self._conn = None
                    logger.info("Navigator cache closed")
                except Exception as e:
                    logger.warning(f"Error closing cache: {e}")
