import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, JSON, Enum as SQLEnum, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
import enum


class EventStatus(str, enum.Enum):
    """Status of an external event."""
    PENDING = "pending"        # Event received, not yet processed
    PROCESSING = "processing"  # Event is being processed
    COMPLETED = "completed"    # Event processed successfully
    FAILED = "failed"          # Event processing failed
    SKIPPED = "skipped"        # Event skipped (e.g., not our turn)


class ExternalEvent(Base):
    """
    Tracks external events from services like OGS (Online-Go Server).

    External events are asynchronous notifications from external services
    that may trigger AI responses (e.g., a Go move being played, triggering
    the AI to respond with its own move).
    """
    __tablename__ = "external_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Event source and type
    # source: The external service (e.g., "ogs", "github")
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    # event_type: The type of event (e.g., "game_move", "game_phase_change", "issue_created")
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # External resource identification
    # external_id: The ID of the resource in the external service (e.g., OGS game ID)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Event payload (raw data from external service)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Processing status
    status: Mapped[EventStatus] = mapped_column(
        SQLEnum(EventStatus), default=EventStatus.PENDING
    )
    # Error message if processing failed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Number of retry attempts
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # Response tracking
    # response_message_id: ID of the message created in response to this event
    response_message_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # conversation_id: ID of the conversation associated with this event
    conversation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # entity_id: The AI entity that processed this event
    entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
