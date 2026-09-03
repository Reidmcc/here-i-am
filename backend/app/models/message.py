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

    # Inter-session provenance for Claude Code conversations: the display
    # name of the sibling Claude Code session whose SendMessage delivery this
    # message records (issue #312). The words are the entity's own — the row
    # keeps role=ASSISTANT — but they were authored in a different session
    # than the conversation they landed in, and that channel must stay
    # visible in the record. NULL everywhere else. Non-NULL also switches the
    # vectorized role to "sibling" (see claude_code_mode
    # .persist_and_vectorize_message).
    sibling_session: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # The model that produced this message (issue #321): the provider model
    # id as the request/transcript reported it, e.g. "claude-fable-5-1".
    # Written forward-only at persist time by the native chat routes (the
    # responding session's model), the native memory_save tool, and the
    # Claude Code Stop hook (from the transcript entry). NULL means "not
    # recorded" — every row from before this column, every human message,
    # every tool result, and Claude Code reflections (the MCP endpoint has
    # no trustworthy source for the calling model). It is NEVER backfilled:
    # substrate history was serial and single-model only until it wasn't,
    # and painting inferred models onto old rows would be confabulation in
    # schema form. It is also never rendered into inline memory markers —
    # a memory must not arrive stamped with its substrate — and reaches the
    # entity only through memory_query's opt-in include_model.
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

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
