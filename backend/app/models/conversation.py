import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Text, DateTime, JSON, Enum as SQLEnum, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
import enum


class ConversationType(str, enum.Enum):
    NORMAL = "normal"
    REFLECTION = "reflection"
    MULTI_ENTITY = "multi_entity"


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
    llm_model_used: Mapped[str] = mapped_column(String(100), default="claude-sonnet-4-5-20250929")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Entity ID is the Pinecone index name for the AI entity this conversation belongs to
    # NULL means use the default entity (for backward compatibility)
    entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Archived conversations are hidden from the main list and excluded from memory retrieval
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Imported conversations are hidden from the conversation list but their messages are stored as memories
    is_imported: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-entity system prompts (JSON: { entity_id: system_prompt, ... })
    # Allows different system prompts for each entity in multi-entity conversations
    # or overriding the default system prompt for single-entity conversations
    entity_system_prompts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # External link fields for connecting conversations to external services (e.g., OGS games)
    # external_link_type: Type of external link (e.g., "ogs_game", "github_issue")
    external_link_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # external_link_id: Unique identifier for the external resource (e.g., OGS game ID)
    external_link_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # external_link_metadata: Additional metadata about the external link (JSON)
    # For OGS games: {"opponent": "username", "color": "black", "board_size": 19, ...}
    external_link_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    messages: Mapped[List["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )
    memory_links: Mapped[List["ConversationMemoryLink"]] = relationship(
        "ConversationMemoryLink",
        back_populates="conversation",
        cascade="all, delete-orphan",
        foreign_keys="ConversationMemoryLink.conversation_id"
    )
    # For multi-entity conversations: tracks which entities participate
    entities: Mapped[List["ConversationEntity"]] = relationship(
        "ConversationEntity",
        back_populates="conversation",
        cascade="all, delete-orphan"
    )
