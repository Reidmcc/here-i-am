import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# What a reflection says about another memory (issues #366, #368)
LINK_REVISES = "revises"  # the reflection corrects, supersedes, or updates the target
LINK_CITES = "cites"      # the reflection is based on the target
VALID_LINK_KINDS = (LINK_REVISES, LINK_CITES)


class MemoryLink(Base):
    """
    A pointer from a reflection to another memory, written by `memory_save`.

    Memory is append-only, so a changed mind or a grounded account can only
    be an addition: the reflection is a new row, and this is a second new
    row pointing from it at an older one. The older memory's text, status,
    and significance are never touched; the link is rendered next to both
    ends wherever either surfaces (memory_context.format_memory_link_lines).

    - `revises`: the reflection says the target was wrong or is no longer
      true. The target is one of the entity's own memories (a reflection, or
      something it said) — the human's words are not the entity's to mark.
    - `cites`: the reflection is based on the target. Any memory in the
      entity's experience, the human's words included: citing marks nothing
      as wrong.

    Written once, at save time, as part of the reflection as written: never
    edited, added to later, or backfilled. `position` keeps each kind's
    targets in the order the entity gave them.

    Both ends cascade from Message (ORM relationships on Message, plus
    ON DELETE CASCADE where the database enforces it): hard-deleting either
    memory removes the pointer, the same way deleting a conversation takes
    its memory links.

    The table is distinct from `conversation_memory_links`, which records
    which memories were shown in which conversation; this one records what
    the entity said about its own memories, and belongs to no conversation.
    """
    __tablename__ = "memory_links"
    __table_args__ = (
        UniqueConstraint("reflection_id", "target_id", "kind", name="uq_memory_links_triple"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    reflection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
