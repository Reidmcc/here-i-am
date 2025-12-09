import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ConversationMemoryLink(Base):
    """Tracks which memories were retrieved in which conversations (for deduplication).

    For multi-entity conversations, entity_id tracks which entity retrieved the memory.
    This allows each entity to maintain its own isolated memory retrieval history.
    """
    __tablename__ = "conversation_memory_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"))
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id"))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=True)  # Which entity retrieved this memory

    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="memory_links",
        foreign_keys=[conversation_id]
    )
    message: Mapped["Message"] = relationship("Message")
