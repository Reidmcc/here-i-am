import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ConversationEntity(Base):
    """
    Tracks which entities participate in a multi-entity conversation.

    For multi-entity conversations, the conversation.entity_id is set to a special
    value like "multi-entity" and this table tracks the actual entities involved.
    """
    __tablename__ = "conversation_entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"))
    entity_id: Mapped[str] = mapped_column(String(100))  # Pinecone index name
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Order in which entities were added (for display purposes)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="entities")
