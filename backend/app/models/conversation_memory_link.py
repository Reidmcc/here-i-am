import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ConversationMemoryLink(Base):
    """Tracks which memories were retrieved in which conversations (for deduplication)"""
    __tablename__ = "conversation_memory_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"))
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id"))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="memory_links",
        foreign_keys=[conversation_id]
    )
    message: Mapped["Message"] = relationship("Message")
