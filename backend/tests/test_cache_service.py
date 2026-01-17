"""
Unit tests for the Cache Service.

Tests TTL-based caching, expiration, eviction, and statistics.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
import time
import threading

from app.services.cache_service import (
    TTLCache,
    CacheEntry,
    CacheService,
)


class TestCacheEntry:
    """Tests for CacheEntry dataclass."""

    def test_cache_entry_creation(self):
        """Test creating a cache entry."""
        entry = CacheEntry(
            value="test_value",
            expires_at=datetime.utcnow() + timedelta(seconds=60),
        )

        assert entry.value == "test_value"
        assert entry.hit_count == 0
        assert entry.created_at is not None

    def test_cache_entry_not_expired(self):
        """Test that a fresh entry is not expired."""
        entry = CacheEntry(
            value="test",
            expires_at=datetime.utcnow() + timedelta(seconds=60),
        )

        assert entry.is_expired() is False

    def test_cache_entry_expired(self):
        """Test that an old entry is expired."""
        entry = CacheEntry(
            value="test",
            expires_at=datetime.utcnow() - timedelta(seconds=1),
        )

        assert entry.is_expired() is True


class TestTTLCache:
    """Tests for TTLCache class."""

    @pytest.fixture
    def cache(self):
        """Create a fresh TTL cache for each test."""
        return TTLCache(default_ttl_seconds=60, max_size=100)

    def test_set_and_get(self, cache):
        """Test basic set and get operations."""
        cache.set("key1", "value1")
        result = cache.get("key1")
        assert result == "value1"

    def test_get_missing_key(self, cache):
        """Test getting a key that doesn't exist."""
        result = cache.get("nonexistent")
        assert result is None

    def test_get_expired_key(self):
        """Test that expired entries return None."""
        # Use a very short TTL cache to ensure expiration
        cache = TTLCache(default_ttl_seconds=1, max_size=100)
        cache.set("expiring", "value", ttl_seconds=1)

        # Immediately should be available
        assert cache.get("expiring") == "value"

        # Wait for expiration (1.1 seconds to be safe)
        time.sleep(1.1)
        result = cache.get("expiring")
        assert result is None

    def test_custom_ttl(self, cache):
        """Test setting a custom TTL."""
        cache.set("short_lived", "value", ttl_seconds=1)
        assert cache.get("short_lived") == "value"

    def test_delete_key(self, cache):
        """Test deleting a specific key."""
        cache.set("to_delete", "value")
        assert cache.get("to_delete") == "value"

        result = cache.delete("to_delete")
        assert result is True
        assert cache.get("to_delete") is None

    def test_delete_nonexistent_key(self, cache):
        """Test deleting a key that doesn't exist."""
        result = cache.delete("nonexistent")
        assert result is False

    def test_clear_cache(self, cache):
        """Test clearing all entries."""
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        count = cache.clear()
        assert count == 3
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.get("key3") is None

    def test_invalidate_by_prefix(self, cache):
        """Test invalidating keys by prefix."""
        cache.set("user:1", "value1")
        cache.set("user:2", "value2")
        cache.set("other:1", "value3")

        count = cache.invalidate_by_prefix("user:")
        assert count == 2
        assert cache.get("user:1") is None
        assert cache.get("user:2") is None
        assert cache.get("other:1") == "value3"

    def test_hash_key_string(self, cache):
        """Test that string keys are used as-is."""
        hash_key = cache._hash_key("simple_key")
        assert hash_key == "simple_key"

    def test_hash_key_complex(self, cache):
        """Test that complex keys are hashed."""
        hash_key = cache._hash_key({"complex": "key"})
        assert len(hash_key) == 32  # SHA256 truncated to 32 chars

    def test_statistics_hits(self, cache):
        """Test that hits are counted correctly."""
        cache.set("key", "value")
        cache.get("key")
        cache.get("key")
        cache.get("key")

        stats = cache.get_stats()
        assert stats["hits"] == 3
        assert stats["misses"] == 0

    def test_statistics_misses(self, cache):
        """Test that misses are counted correctly."""
        cache.get("nonexistent1")
        cache.get("nonexistent2")

        stats = cache.get_stats()
        assert stats["misses"] == 2
        assert stats["hits"] == 0

    def test_statistics_hit_rate(self, cache):
        """Test hit rate calculation."""
        cache.set("key", "value")
        cache.get("key")  # Hit
        cache.get("key")  # Hit
        cache.get("missing")  # Miss

        stats = cache.get_stats()
        assert stats["hit_rate"] == pytest.approx(2/3)

    def test_statistics_size(self, cache):
        """Test size tracking."""
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        stats = cache.get_stats()
        assert stats["size"] == 2
        assert stats["max_size"] == 100

    def test_eviction_when_full(self):
        """Test that oldest entries are evicted when cache is full."""
        small_cache = TTLCache(default_ttl_seconds=60, max_size=10)

        # Fill the cache
        for i in range(15):
            small_cache.set(f"key{i}", f"value{i}")
            time.sleep(0.001)  # Ensure different timestamps

        stats = small_cache.get_stats()
        # Should be at most max_size after eviction
        assert stats["size"] <= 10

    def test_periodic_cleanup(self):
        """Test that expired entries are cleaned up periodically."""
        cache = TTLCache(
            default_ttl_seconds=0,  # Expire immediately
            cleanup_interval=2,  # Clean up every 2 operations
        )

        # Add some entries
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        time.sleep(0.01)  # Let them expire

        # Trigger cleanup with operations
        cache.get("trigger1")
        cache.get("trigger2")

        stats = cache.get_stats()
        assert stats["size"] == 0  # All expired entries cleaned

    def test_thread_safety(self, cache):
        """Test that cache operations are thread-safe."""
        errors = []

        def writer():
            try:
                for i in range(100):
                    cache.set(f"key{i}", f"value{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(100):
                    cache.get(f"key{i}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestCacheService:
    """Tests for CacheService with specialized caches."""

    @pytest.fixture
    def service(self):
        """Create a fresh CacheService instance."""
        return CacheService()

    def test_token_cache_set_get(self, service):
        """Test token counting cache helpers."""
        service.set_token_count("hello world", 2)
        result = service.get_token_count("hello world")
        assert result == 2

    def test_token_cache_missing(self, service):
        """Test missing token count."""
        result = service.get_token_count("unknown text")
        assert result is None

    def test_search_cache_set_get(self, service):
        """Test memory search cache helpers."""
        results = [{"id": "1", "content": "test"}]
        service.set_search_results(
            query="test query",
            entity_id="entity1",
            top_k=5,
            exclude_conversation_id=None,
            results=results,
        )

        cached = service.get_search_results(
            query="test query",
            entity_id="entity1",
            top_k=5,
            exclude_conversation_id=None,
        )
        assert cached == results

    def test_search_cache_different_params(self, service):
        """Test that different parameters create different cache keys."""
        results1 = [{"id": "1"}]
        results2 = [{"id": "2"}]

        service.set_search_results("query", "entity1", 5, None, results1)
        service.set_search_results("query", "entity2", 5, None, results2)

        cached1 = service.get_search_results("query", "entity1", 5, None)
        cached2 = service.get_search_results("query", "entity2", 5, None)

        assert cached1 == results1
        assert cached2 == results2

    def test_search_cache_invalidation(self, service):
        """Test invalidating search cache for an entity."""
        service.set_search_results("q1", "entity1", 5, None, [{"id": "1"}])
        service.set_search_results("q2", "entity1", 5, None, [{"id": "2"}])
        service.set_search_results("q3", "entity2", 5, None, [{"id": "3"}])

        count = service.invalidate_search_cache_for_entity("entity1")
        assert count == 2

        assert service.get_search_results("q1", "entity1", 5, None) is None
        assert service.get_search_results("q2", "entity1", 5, None) is None
        assert service.get_search_results("q3", "entity2", 5, None) is not None

    def test_memory_content_cache(self, service):
        """Test memory content cache helpers."""
        content = {"id": "msg1", "content": "Test memory content"}
        service.set_memory_content("msg1", content)

        cached = service.get_memory_content("msg1")
        assert cached == content

    def test_memory_content_invalidation(self, service):
        """Test invalidating specific memory content."""
        service.set_memory_content("msg1", {"content": "test"})
        assert service.get_memory_content("msg1") is not None

        result = service.invalidate_memory_content("msg1")
        assert result is True
        assert service.get_memory_content("msg1") is None

    def test_github_tree_cache(self, service):
        """Test GitHub tree cache helpers."""
        tree_data = {"sha": "abc123", "tree": []}
        service.set_github_tree("my-repo", "main", tree_data)

        cached = service.get_github_tree("my-repo", "main")
        assert cached == tree_data

    def test_github_tree_cache_default_ref(self, service):
        """Test GitHub tree cache with default ref."""
        tree_data = {"sha": "abc123"}
        service.set_github_tree("my-repo", None, tree_data)

        cached = service.get_github_tree("my-repo", None)
        assert cached == tree_data

    def test_github_file_cache(self, service):
        """Test GitHub file cache helpers."""
        file_data = {"content": "file contents", "sha": "abc"}
        service.set_github_file("my-repo", "src/main.py", "main", file_data)

        cached = service.get_github_file("my-repo", "src/main.py", "main")
        assert cached == file_data

    def test_github_metadata_cache(self, service):
        """Test GitHub metadata cache helpers."""
        metadata = {"stars": 100, "forks": 10}
        service.set_github_metadata("my-repo", "info", metadata)

        cached = service.get_github_metadata("my-repo", "info")
        assert cached == metadata

    def test_github_list_cache(self, service):
        """Test GitHub list cache helpers."""
        pr_list = [{"number": 1, "title": "PR 1"}]
        service.set_github_list("my-repo", "prs", "open", pr_list)

        cached = service.get_github_list("my-repo", "prs", "open")
        assert cached == pr_list

    def test_github_cache_invalidation(self, service):
        """Test invalidating all GitHub caches for a repo."""
        service.set_github_tree("my-repo", "main", {"tree": []})
        service.set_github_file("my-repo", "file.py", "main", {"content": ""})
        service.set_github_metadata("my-repo", "info", {"stars": 1})
        service.set_github_list("my-repo", "prs", "open", [])

        count = service.invalidate_github_cache_for_repo("my-repo")
        assert count == 4

        assert service.get_github_tree("my-repo", "main") is None
        assert service.get_github_file("my-repo", "file.py", "main") is None
        assert service.get_github_metadata("my-repo", "info") is None
        assert service.get_github_list("my-repo", "prs", "open") is None

    def test_github_tree_invalidation(self, service):
        """Test invalidating GitHub tree cache."""
        service.set_github_tree("my-repo", "main", {"tree": []})
        service.set_github_tree("my-repo", "dev", {"tree": []})

        # Invalidate specific ref
        count = service.invalidate_github_tree("my-repo", "main")
        assert count == 1
        assert service.get_github_tree("my-repo", "main") is None
        assert service.get_github_tree("my-repo", "dev") is not None

    def test_github_tree_invalidation_all_refs(self, service):
        """Test invalidating all tree cache entries for a repo."""
        service.set_github_tree("my-repo", "main", {"tree": []})
        service.set_github_tree("my-repo", "dev", {"tree": []})

        count = service.invalidate_github_tree("my-repo", None)
        assert count == 2

    def test_github_file_invalidation(self, service):
        """Test invalidating GitHub file cache."""
        service.set_github_file("my-repo", "file.py", "main", {"content": ""})

        count = service.invalidate_github_file("my-repo", "file.py", "main")
        assert count == 1
        assert service.get_github_file("my-repo", "file.py", "main") is None

    def test_clear_all_caches(self, service):
        """Test clearing all caches."""
        service.set_token_count("text", 10)
        service.set_search_results("q", "e", 5, None, [])
        service.set_memory_content("m1", {})
        service.set_github_tree("repo", "main", {})

        result = service.clear_all()

        assert result["token_cache"] >= 1
        assert result["search_cache"] >= 1
        assert result["content_cache"] >= 1
        assert result["github_tree_cache"] >= 1

    def test_get_all_stats(self, service):
        """Test getting stats for all caches."""
        stats = service.get_all_stats()

        expected_caches = [
            "token_cache",
            "search_cache",
            "content_cache",
            "github_tree_cache",
            "github_file_cache",
            "github_metadata_cache",
            "github_list_cache",
        ]

        for cache_name in expected_caches:
            assert cache_name in stats
            assert "size" in stats[cache_name]
            assert "hits" in stats[cache_name]
            assert "misses" in stats[cache_name]


class TestCacheServiceTTLs:
    """Tests for verifying TTL configurations."""

    def test_token_cache_ttl(self):
        """Verify token cache has 1 hour TTL."""
        service = CacheService()
        stats = service.token_cache.get_stats()
        assert stats["ttl_seconds"] == 3600  # 1 hour

    def test_search_cache_ttl(self):
        """Verify search cache has 1 minute TTL."""
        service = CacheService()
        stats = service.search_cache.get_stats()
        assert stats["ttl_seconds"] == 60  # 1 minute

    def test_content_cache_ttl(self):
        """Verify content cache has 5 minute TTL."""
        service = CacheService()
        stats = service.content_cache.get_stats()
        assert stats["ttl_seconds"] == 300  # 5 minutes

    def test_github_tree_cache_ttl(self):
        """Verify GitHub tree cache has 5 minute TTL."""
        service = CacheService()
        stats = service.github_tree_cache.get_stats()
        assert stats["ttl_seconds"] == 300  # 5 minutes

    def test_github_file_cache_ttl(self):
        """Verify GitHub file cache has 10 minute TTL."""
        service = CacheService()
        stats = service.github_file_cache.get_stats()
        assert stats["ttl_seconds"] == 600  # 10 minutes

    def test_github_list_cache_ttl(self):
        """Verify GitHub list cache has 2 minute TTL."""
        service = CacheService()
        stats = service.github_list_cache.get_stats()
        assert stats["ttl_seconds"] == 120  # 2 minutes
