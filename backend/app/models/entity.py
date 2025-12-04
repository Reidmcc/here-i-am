import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Entity(Base):
    """
    Represents an AI entity with its own memory space (Pinecone index).

    Entities can be created either through environment variables or dynamically
    via the API. Database-stored entities take precedence over env-configured ones
    with the same index_name.
    """
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=datetime.utcnow, nullable=True)

    # Core identity
    index_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # LLM configuration
    llm_provider: Mapped[str] = mapped_column(String(50), default="anthropic")  # "anthropic" or "openai"
    default_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
