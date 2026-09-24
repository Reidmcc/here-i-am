import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.message import Message


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
    # The memory-link marker lines (memory_context.format_memory_link_lines:
    # what this memory revises or cites, which reflections later revised or
    # cited it) exactly as rendered when the memory was inserted into a
    # native conversation's context. Session reload re-renders the memory
    # from this, never from the current links: a correction made after the
    # insertion shows on the memory's NEXT surfacing, and never re-renders a
    # marker already in the cached context (issues #366, #368). NULL = no
    # marker lines (and every link from before the column, which predates
    # links too). Claude Code links leave it NULL — those conversations are
    # never rebuilt.
    annotation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="memory_links",
        foreign_keys=[conversation_id]
    )
    message: Mapped["Message"] = relationship("Message")
