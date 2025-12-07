from typing import List, Dict, Any, Optional, Set
from datetime import datetime
import logging

from pinecone import Pinecone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.config import settings
from app.models import Message, ConversationMemoryLink, Conversation
import numpy as np

logger = logging.getLogger(__name__)


class MemoryService:
    """
    Memory service using Pinecone with integrated inference (llama-text-embed-v2).

    Pinecone handles embedding generation internally - we pass raw text and
    Pinecone generates embeddings using the model configured on the index.

    Includes caching for:
    - Memory search results (short TTL to reduce Pinecone API calls)
    - Full memory content lookups (medium TTL to reduce DB queries)
    """
    def __init__(self):
        self._pc = None
        self._indexes: Dict[str, Any] = {}  # Cache for multiple indexes
        self._cache_service = None

    @property
    def cache(self):
        """Lazy load cache service to avoid circular imports."""
        if self._cache_service is None:
            from app.services.cache_service import cache_service
            self._cache_service = cache_service
        return self._cache_service

    @property
    def pc(self):
        if self._pc is None and settings.pinecone_api_key:
            self._pc = Pinecone(api_key=settings.pinecone_api_key)
        return self._pc

    def get_index(self, entity_id: Optional[str] = None):
        """
        Get a Pinecone index by entity_id (index name).

        Args:
            entity_id: The Pinecone index name. If None, uses the default entity.

        Returns:
            Pinecone Index object or None if not configured.
        """
        if not self.pc:
            return None

        # Use default entity if not specified
        if entity_id is None:
            entity = settings.get_default_entity()
            entity_id = entity.index_name
        else:
            entity = settings.get_entity_by_index(entity_id)

        # Return cached index if available
        if entity_id in self._indexes:
            return self._indexes[entity_id]

        # Create and cache new index connection
        try:
            # Use host if provided in entity config (required for serverless indexes)
            if entity and entity.host:
                index = self.pc.Index(entity_id, host=entity.host)
            else:
                index = self.pc.Index(entity_id)
            self._indexes[entity_id] = index
            return index
        except Exception as e:
            logger.error(f"Error connecting to Pinecone index '{entity_id}': {e}")
            return None

    @property
    def index(self):
        """Backward-compatible property that returns the default index."""
        return self.get_index(None)

    def is_configured(self, entity_id: Optional[str] = None) -> bool:
        """
        Check if Pinecone is configured and the specified entity's index is available.

        Args:
            entity_id: The entity to check. If None, checks if Pinecone is configured at all.
        """
        if not settings.pinecone_api_key:
            return False

        if entity_id is None:
            return True

        # Verify the entity exists in configuration
        return settings.get_entity_by_index(entity_id) is not None

    async def store_memory(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        created_at: datetime,
        entity_id: Optional[str] = None,
    ) -> bool:
        """
        Store a message as a memory in the vector database.

        Uses Pinecone's integrated inference - pass raw text and Pinecone
        generates embeddings using the model configured on the index.

        Args:
            message_id: Unique ID for the message
            conversation_id: ID of the conversation
            role: Message role (human/assistant)
            content: Message content
            created_at: When the message was created
            entity_id: The Pinecone index name. If None, uses default entity.

        Returns True if successful, False otherwise.
        """
        logger.debug(f"store_memory called for entity_id={entity_id}")

        if not self.is_configured():
            logger.debug("store_memory: Pinecone not configured")
            return False

        index = self.get_index(entity_id)
        if index is None:
            logger.warning(f"store_memory: Failed to get index for entity_id={entity_id}")
            return False

        logger.debug("store_memory: Got index, upserting with integrated inference...")

        # Create content preview for metadata
        content_preview = content[:200] if len(content) > 200 else content

        try:
            # Use Pinecone's integrated inference - upsert_records passes raw text
            # and Pinecone generates embeddings using the index's configured model
            index.upsert_records(
                namespace="",
                records=[{
                    "_id": message_id,
                    "text": content,  # Pinecone will embed this using llama-text-embed-v2
                    "conversation_id": conversation_id,
                    "created_at": created_at.isoformat(),
                    "role": role,
                    "content_preview": content_preview,
                    "times_retrieved": 0,
                }]
            )
            logger.debug("store_memory: Successfully upserted to Pinecone")
            return True
        except Exception as e:
            logger.error(f"Error storing memory: {e}")
            return False

    async def search_memories(
        self,
        query: str,
        top_k: int = None,
        exclude_conversation_id: Optional[str] = None,
        exclude_ids: Optional[set] = None,
        entity_id: Optional[str] = None,
        use_cache: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant memories using semantic similarity.

        Uses Pinecone's integrated inference - pass raw text and Pinecone
        generates embeddings using the model configured on the index.

        Results are cached for 60 seconds by default to reduce Pinecone API calls
        during multi-turn conversations with similar queries.

        Args:
            query: The query text to search for
            top_k: Number of results to return (defaults to config)
            exclude_conversation_id: Conversation ID to exclude from results
            exclude_ids: Set of message IDs to exclude (for deduplication)
            entity_id: The Pinecone index name. If None, uses default entity.
            use_cache: Whether to use cached results (default True)

        Returns:
            List of memory dicts with id, content, score, metadata
        """
        if not self.is_configured():
            return []

        index = self.get_index(entity_id)
        if index is None:
            return []

        top_k = top_k or settings.retrieval_top_k
        exclude_ids = exclude_ids or set()

        # Check cache first (before exclude_ids filtering, which happens post-query)
        # Cache key doesn't include exclude_ids since we filter after retrieval
        if use_cache:
            cached_results = self.cache.get_search_results(
                query=query,
                entity_id=entity_id,
                top_k=top_k * 2,  # Cache the larger fetch_k results
                exclude_conversation_id=exclude_conversation_id,
            )
            if cached_results is not None:
                logger.info(f"[MEMORY] Cache HIT for entity={entity_id}")
                # Apply exclude_ids filter to cached results
                filtered = []
                for mem in cached_results:
                    if mem["id"] in exclude_ids:
                        continue
                    if mem["score"] < settings.similarity_threshold:
                        continue
                    filtered.append(mem)
                    if len(filtered) >= top_k:
                        break
                return filtered

        try:
            # Query more than we need to allow for filtering by exclude_ids
            fetch_k = top_k * 2

            logger.info(f"[MEMORY] Searching memories: threshold={settings.similarity_threshold}, top_k={top_k}, entity={entity_id}")

            # Build search query with optional metadata filter
            search_query = {
                "inputs": {"text": query},  # Pinecone will embed this
                "top_k": fetch_k,
            }

            # Add metadata filter to exclude current conversation at Pinecone level
            # This is more efficient than filtering in Python after retrieval
            if exclude_conversation_id:
                search_query["filter"] = {
                    "conversation_id": {"$ne": exclude_conversation_id}
                }

            # Use Pinecone's integrated inference - search with raw text
            results = index.search(
                namespace="",
                query=search_query,
            )

            all_memories = []
            # Pinecone inference search returns: results.result.hits
            # Each hit has: _id, _score, fields (metadata dict)
            hits = results.result.hits if hasattr(results, 'result') and hasattr(results.result, 'hits') else []
            logger.info(f"[MEMORY] Pinecone returned {len(hits)} candidate memories")

            for hit in hits:
                # Get hit properties via to_dict()
                hit_dict = hit.to_dict() if hasattr(hit, 'to_dict') else hit
                match_id = hit_dict.get('_id')
                match_score = hit_dict.get('_score', 0)
                fields = hit_dict.get('fields', {})
                conv_id = fields.get("conversation_id")

                # Skip same conversation (this filter is part of cache key)
                if exclude_conversation_id and conv_id == exclude_conversation_id:
                    logger.debug(f"SKIP (same conversation): {match_id[:8]}...")
                    continue

                all_memories.append({
                    "id": match_id,
                    "score": match_score,
                    "conversation_id": conv_id,
                    "created_at": fields.get("created_at"),
                    "role": fields.get("role"),
                    "content_preview": fields.get("content_preview"),
                    "times_retrieved": fields.get("times_retrieved", 0),
                })

            # Cache the raw results (before exclude_ids and threshold filtering)
            if use_cache:
                self.cache.set_search_results(
                    query=query,
                    entity_id=entity_id,
                    top_k=fetch_k,
                    exclude_conversation_id=exclude_conversation_id,
                    results=all_memories,
                )

            # Now apply exclude_ids and threshold filtering
            memories = []
            for mem in all_memories:
                if mem["score"] < settings.similarity_threshold:
                    logger.debug(f"SKIP (score {mem['score']:.3f} < {settings.similarity_threshold}): {mem['id'][:8]}...")
                    continue

                if mem["id"] in exclude_ids:
                    logger.debug(f"SKIP (already retrieved): {mem['id'][:8]}...")
                    continue

                logger.debug(f"INCLUDE: {mem['id'][:8]}... score={mem['score']:.3f}")
                memories.append(mem)

                if len(memories) >= top_k:
                    break

            logger.info(f"[MEMORY] Search complete: returning {len(memories)} memories (filtered from {len(hits)} candidates)")
            return memories
        except Exception as e:
            logger.error(f"Error searching memories: {e}")
            return []

    async def get_full_memory_content(
        self,
        message_id: str,
        db: AsyncSession,
        use_cache: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Get full memory content from the SQL database.

        Results are cached for 5 minutes to reduce database queries when
        the same memory is accessed multiple times.

        Args:
            message_id: The message/memory ID to fetch
            db: Database session
            use_cache: Whether to use cached results (default True)

        Returns:
            Dict with memory content or None if not found
        """
        # Check cache first
        if use_cache:
            cached_content = self.cache.get_memory_content(message_id)
            if cached_content is not None:
                return cached_content

        result = await db.execute(
            select(Message).where(Message.id == message_id)
        )
        message = result.scalar_one_or_none()

        if message:
            content_dict = {
                "id": str(message.id),
                "conversation_id": str(message.conversation_id),
                "role": message.role.value,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
                "times_retrieved": message.times_retrieved,
                "last_retrieved_at": message.last_retrieved_at.isoformat() if message.last_retrieved_at else None,
            }
            # Cache the result
            if use_cache:
                self.cache.set_memory_content(message_id, content_dict)
            return content_dict
        return None

    async def update_retrieval_count(
        self,
        message_id: str,
        conversation_id: str,
        db: AsyncSession,
        entity_id: Optional[str] = None,
    ) -> bool:
        """
        Update retrieval count for a memory.

        - Increments times_retrieved in SQL database
        - Updates last_retrieved_at timestamp
        - Creates ConversationMemoryLink for tracking
        - Updates Pinecone metadata

        Args:
            message_id: The message/memory ID
            conversation_id: The conversation this retrieval is for
            db: Database session
            entity_id: The Pinecone index name. If None, uses default entity.
        """
        try:
            # Update SQL record
            await db.execute(
                update(Message)
                .where(Message.id == message_id)
                .values(
                    times_retrieved=Message.times_retrieved + 1,
                    last_retrieved_at=datetime.utcnow()
                )
            )

            # Create link record for deduplication tracking
            link = ConversationMemoryLink(
                conversation_id=conversation_id,
                message_id=message_id,
            )
            db.add(link)
            await db.commit()

            # Update Pinecone metadata (get current count and increment)
            if self.is_configured():
                index = self.get_index(entity_id)
                if index:
                    try:
                        # Fetch current vector to get metadata
                        fetch_result = index.fetch(ids=[message_id])
                        if message_id in fetch_result.vectors:
                            current_count = fetch_result.vectors[message_id].metadata.get("times_retrieved", 0)
                            # Update with incremented count
                            index.update(
                                id=message_id,
                                set_metadata={"times_retrieved": current_count + 1}
                            )
                    except Exception as e:
                        logger.warning(f"Could not update Pinecone metadata: {e}")

            return True
        except Exception as e:
            logger.error(f"Error updating retrieval count: {e}")
            await db.rollback()
            return False

    async def get_retrieved_ids_for_conversation(
        self,
        conversation_id: str,
        db: AsyncSession
    ) -> set:
        """
        Get all message IDs that have been retrieved in a conversation.
        Used for session deduplication.
        """
        result = await db.execute(
            select(ConversationMemoryLink.message_id)
            .where(ConversationMemoryLink.conversation_id == conversation_id)
        )
        return set(row[0] for row in result.fetchall())

    async def get_archived_conversation_ids(
        self,
        db: AsyncSession,
        entity_id: Optional[str] = None,
    ) -> Set[str]:
        """
        Get IDs of all archived conversations.

        Used to filter out memories from archived conversations during retrieval.

        Args:
            db: Database session
            entity_id: Optional entity filter. If provided, only returns archived
                       conversations for that entity.
        """
        query = select(Conversation.id).where(Conversation.is_archived == True)

        if entity_id is not None:
            query = query.where(Conversation.entity_id == entity_id)

        result = await db.execute(query)
        return set(row[0] for row in result.fetchall())

    async def delete_memory(self, message_id: str, entity_id: Optional[str] = None) -> bool:
        """
        Delete a memory from the vector database.

        Args:
            message_id: The message/memory ID to delete
            entity_id: The Pinecone index name. If None, uses default entity.
        """
        if not self.is_configured():
            return False

        index = self.get_index(entity_id)
        if index is None:
            return False

        try:
            index.delete(ids=[message_id])
            return True
        except Exception as e:
            logger.error(f"Error deleting memory: {e}")
            return False

    def test_connection(self) -> Dict[str, Any]:
        """
        Test Pinecone connection for all configured entities.

        Returns a dict with:
            - configured: bool - whether Pinecone is configured at all
            - entities: list of dicts with entity_id, success, message, and stats
        """
        result = {
            "configured": False,
            "entities": []
        }

        # Check if Pinecone is configured
        if not settings.pinecone_api_key:
            return result

        result["configured"] = True

        # Get all configured entities
        entities = settings.get_entities()
        if not entities:
            result["entities"].append({
                "entity_id": None,
                "success": False,
                "message": "No entities configured in PINECONE_INDEXES",
                "stats": None
            })
            return result

        # Test connection to each entity's index
        for entity in entities:
            entity_result = {
                "entity_id": entity.index_name,
                "label": entity.label,
                "host": entity.host,
                "success": False,
                "message": "",
                "stats": None
            }

            try:
                # Clear cached index to force fresh connection
                if entity.index_name in self._indexes:
                    del self._indexes[entity.index_name]

                index = self.get_index(entity.index_name)
                if index is None:
                    entity_result["message"] = "Failed to connect to index"
                else:
                    # Try to get index stats to verify connection works
                    stats = index.describe_index_stats()
                    entity_result["success"] = True
                    entity_result["message"] = "Connection successful"
                    entity_result["stats"] = {
                        "total_vector_count": stats.total_vector_count,
                        "dimension": stats.dimension,
                    }
            except Exception as e:
                entity_result["message"] = f"Connection error: {str(e)}"

            result["entities"].append(entity_result)

        return result


# Singleton instance
memory_service = MemoryService()
