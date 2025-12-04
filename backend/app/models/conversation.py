import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Text, DateTime, JSON, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
import enum


class ConversationType(str, enum.Enum):
    NORMAL = "normal"
    REFLECTION = "reflection"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=datetime.utcnow, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    conversation_type: Mapped[ConversationType] = mapped_column(
        SQLEnum(ConversationType), default=ConversationType.NORMAL
    )
    system_prompt_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_used: Mapped[str] = mapped_column(String(100), default="claude-sonnet-4-5-latest")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Entity ID is the Pinecone index name for the AI entity this conversation belongs to
    # NULL means use the default entity (for backward compatibility)
    entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    messages: Mapped[List["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )
    memory_links: Mapped[List["ConversationMemoryLink"]] = relationship(
        "ConversationMemoryLink",
        back_populates="conversation",
        cascade="all, delete-orphan",
        foreign_keys="ConversationMemoryLink.conversation_id"
    )
