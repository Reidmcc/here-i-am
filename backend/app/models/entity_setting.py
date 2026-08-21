from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EntitySetting(Base):
    """
    Per-entity, user-editable settings that must survive across sessions.

    Entities themselves are defined in config (PINECONE_INDEXES), which is
    immutable at runtime. This table holds the mutable per-entity preferences
    the researcher sets in the UI — the entity's default system prompt and its
    thinking effort — keyed by the entity's Pinecone index name.
    """
    __tablename__ = "entity_settings"

    # Matches Conversation.entity_id / EntityConfig.index_name (the Pinecone index name)
    entity_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    # Default system prompt for this entity. NULL means "no system prompt".
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Thinking effort for this entity (low, medium, high, xhigh, max).
    # NULL means "follow settings.default_thinking_effort".
    thinking_effort: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True
    )
