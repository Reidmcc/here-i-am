from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class ConversationIdAlias(Base):
    """
    A conversation id that was handed to a Claude Code session's context and
    has since been merged into another conversation (issue #359).

    Distinct from `ConversationSessionAlias`, which maps former *session*
    ids: this maps former *conversation* ids. It exists because late fork
    adoption changes which conversation a running session records into.
    When the desktop app runs a fork's first hooks before it has written
    the files the lineage hints are read from, the backend cannot tell the
    fork from a new session and opens a row for it — and the session's
    identity block already names that row's id. A later hook brings the
    hints, the row is merged into the parent it continues, and the id the
    session was told would otherwise resolve to nothing. Every MCP tool
    call carries a conversation id, so the retired one has to keep working:
    the entity is told the new id once, and the old one keeps resolving
    either way.
    """
    __tablename__ = "conversation_id_aliases"

    # The retired conversation id — the one an entity may still be holding
    alias_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped["Conversation"] = relationship("Conversation")
