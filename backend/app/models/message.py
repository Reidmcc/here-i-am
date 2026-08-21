import enum
import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class MessageRole(str, enum.Enum):
    HUMAN = "human"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    # Tool exchange roles - content is JSON for these
    TOOL_USE = "tool_use"      # Assistant's tool call request
    TOOL_RESULT = "tool_result"  # Tool execution result
    # Self-authored memory written by the entity via the memory_save tool.
    # Not part of the conversational back-and-forth; vectorized like other memories.
    REFLECTION = "reflection"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"))
    role: Mapped[MessageRole] = mapped_column(SQLEnum(MessageRole))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Memory tracking
    times_retrieved: Mapped[int] = mapped_column(Integer, default=0)
    last_retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Entity-controlled memory status:
    #   NULL       - normal memory (default significance dynamics)
    #   "pinned"   - exempt from age-based significance decay
    #   "released" - excluded from memory retrieval (still stored, reversible)
    memory_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # For multi-entity conversations: tracks which entity spoke this message
    # NULL for single-entity conversations or human messages in multi-entity
    # For AI responses in multi-entity, this is the entity that generated the response
    speaker_entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    @property
    def is_tool_exchange(self) -> bool:
        """Check if this message is part of a tool exchange."""
        return self.role in (MessageRole.TOOL_USE, MessageRole.TOOL_RESULT)

    @property
    def content_blocks(self) -> Union[str, List[Dict[str, Any]]]:
        """
        Get content as either a string or parsed JSON content blocks.

        For TOOL_USE and TOOL_RESULT messages, content is stored as JSON.
        For other message types, content is a plain string.
        """
        if self.is_tool_exchange:
            try:
                return json.loads(self.content)
            except (json.JSONDecodeError, TypeError):
                return self.content
        return self.content

    @staticmethod
    def serialize_content_blocks(content_blocks: List[Dict[str, Any]]) -> str:
        """Serialize content blocks to JSON for storage."""
        return json.dumps(content_blocks)
