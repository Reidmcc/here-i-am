from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class ConversationSessionAlias(Base):
    """
    A former Claude Code session id of a conversation (issue #357).

    The desktop app does not resume a session in place: a restart, a
    "continue", or a rewind forks it under a NEW Claude Code session id,
    with the transcript copied. The conversation is the same, so the row
    keeps its id and moves `Conversation.external_session_id` to the new
    session id; every id it was ever keyed on lands here, so a hook that
    still carries an old id (a retry, a late Stop, a session-end) resolves
    to the same conversation instead of registering an empty one.
    """
    __tablename__ = "conversation_session_aliases"

    external_session_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="session_aliases"
    )
