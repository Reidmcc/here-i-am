from typing import List, Dict, Any, Optional
from datetime import datetime
from pinecone import Pinecone
from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.config import settings
from app.models import Message, ConversationMemoryLink
import numpy as np


class MemoryService:
    def __init__(self):
        self._pc = None
        self._index = None
        self._anthropic = None

    @property
    def pc(self):
        if self._pc is None and settings.pinecone_api_key:
            self._pc = Pinecone(api_key=settings.pinecone_api_key)
        return self._pc

    @property
    def index(self):
        if self._index is None and self.pc:
            self._index = self.pc.Index(settings.pinecone_index_name)
        return self._index

    @property
    def anthropic(self):
        if self._anthropic is None:
            self._anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._anthropic

    def is_configured(self) -> bool:
        """Check if Pinecone is configured and available."""
        return bool(settings.pinecone_api_key)

    async def get_embedding(self, text: str) -> List[float]:
        """
        Get embedding for text using Anthropic's voyage embeddings via their API.

        For now, we use a simple approach with the Anthropic client.
        In production, you might use a dedicated embedding model.
        """
        # Use Anthropic's embeddings endpoint
        # Note: As of early 2024, Anthropic recommends using Voyage AI for embeddings
        # For simplicity, we'll use a direct embedding approach

        # Truncate text if too long (embedding models have limits)
        max_chars = 8000
        if len(text) > max_chars:
            text = text[:max_chars]

        try:
            # Use Voyage embeddings via Anthropic
            response = await self.anthropic.embeddings.create(
                model="voyage-3",
                input=[text]
            )
            return response.data[0].embedding
        except Exception:
            # Fallback: If embeddings fail, we still need to function
            # Return None and handle gracefully
            return None

    async def store_memory(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        created_at: datetime,
    ) -> bool:
        """
        Store a message as a memory in the vector database.

        Returns True if successful, False otherwise.
        """
        if not self.is_configured():
            return False

        embedding = await self.get_embedding(content)
        if embedding is None:
            return False

        # Create content preview for metadata
        content_preview = content[:200] if len(content) > 200 else content

        try:
            self.index.upsert(
                vectors=[{
                    "id": message_id,
                    "values": embedding,
                    "metadata": {
                        "conversation_id": conversation_id,
                        "created_at": created_at.isoformat(),
                        "role": role,
                        "content_preview": content_preview,
                        "times_retrieved": 0,
                    }
                }]
            )
            return True
        except Exception as e:
            print(f"Error storing memory: {e}")
            return False

    async def search_memories(
        self,
        query: str,
        top_k: int = None,
        exclude_conversation_id: Optional[str] = None,
        exclude_ids: Optional[set] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant memories using semantic similarity.

        Args:
            query: The query text to search for
            top_k: Number of results to return (defaults to config)
            exclude_conversation_id: Conversation ID to exclude from results
            exclude_ids: Set of message IDs to exclude (for deduplication)

        Returns:
            List of memory dicts with id, content, score, metadata
        """
        if not self.is_configured():
            return []

        top_k = top_k or settings.retrieval_top_k
        exclude_ids = exclude_ids or set()

        embedding = await self.get_embedding(query)
        if embedding is None:
            return []

        try:
            # Query more than we need to allow for filtering
            fetch_k = top_k * 3

            results = self.index.query(
                vector=embedding,
                top_k=fetch_k,
                include_metadata=True,
            )

            memories = []
            for match in results.matches:
                # Skip if below similarity threshold
                if match.score < settings.similarity_threshold:
                    continue

                # Skip if in exclude set
                if match.id in exclude_ids:
                    continue

                # Skip if from current conversation (don't retrieve your own context)
                if exclude_conversation_id and match.metadata.get("conversation_id") == exclude_conversation_id:
                    continue

                memories.append({
                    "id": match.id,
                    "score": match.score,
                    "conversation_id": match.metadata.get("conversation_id"),
                    "created_at": match.metadata.get("created_at"),
                    "role": match.metadata.get("role"),
                    "content_preview": match.metadata.get("content_preview"),
                    "times_retrieved": match.metadata.get("times_retrieved", 0),
                })

                if len(memories) >= top_k:
                    break

            return memories
        except Exception as e:
            print(f"Error searching memories: {e}")
            return []

    async def get_full_memory_content(
        self,
        message_id: str,
        db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """
        Get full memory content from the SQL database.
        """
        result = await db.execute(
            select(Message).where(Message.id == message_id)
        )
        message = result.scalar_one_or_none()

        if message:
            return {
                "id": message.id,
                "conversation_id": message.conversation_id,
                "role": message.role.value,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
                "times_retrieved": message.times_retrieved,
                "last_retrieved_at": message.last_retrieved_at.isoformat() if message.last_retrieved_at else None,
            }
        return None

    async def update_retrieval_count(
        self,
        message_id: str,
        conversation_id: str,
        db: AsyncSession
    ) -> bool:
        """
        Update retrieval count for a memory.

        - Increments times_retrieved in SQL database
        - Updates last_retrieved_at timestamp
        - Creates ConversationMemoryLink for tracking
        - Updates Pinecone metadata
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
                try:
                    # Fetch current vector to get metadata
                    fetch_result = self.index.fetch(ids=[message_id])
                    if message_id in fetch_result.vectors:
                        current_count = fetch_result.vectors[message_id].metadata.get("times_retrieved", 0)
                        # Update with incremented count
                        self.index.update(
                            id=message_id,
                            set_metadata={"times_retrieved": current_count + 1}
                        )
                except Exception as e:
                    print(f"Warning: Could not update Pinecone metadata: {e}")

            return True
        except Exception as e:
            print(f"Error updating retrieval count: {e}")
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

    async def delete_memory(self, message_id: str) -> bool:
        """Delete a memory from the vector database."""
        if not self.is_configured():
            return False

        try:
            self.index.delete(ids=[message_id])
            return True
        except Exception as e:
            print(f"Error deleting memory: {e}")
            return False


# Singleton instance
memory_service = MemoryService()
