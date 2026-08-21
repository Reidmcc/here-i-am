"""
Message history maintenance shared by the routes that rewrite a turn.

A response that used tools is persisted as several rows: one TOOL_USE /
TOOL_RESULT pair per iteration, then the assistant row. Anything that
discards the response (regenerate, editing the human message, deleting it)
has to take the tool exchange rows with it - they are not addressable from
the UI, so a leftover pair is invisible to the researcher until the
conversation is reloaded, where it renders as a tool card belonging to a
response that no longer exists and is replayed into the LLM context as a
tool call the assistant never made.
"""
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message, MessageRole

logger = logging.getLogger(__name__)

TOOL_EXCHANGE_ROLES = (MessageRole.TOOL_USE, MessageRole.TOOL_RESULT)

# Rows a response leaves behind that are not part of the conversational
# back-and-forth: the tool exchanges, plus the reflections memory_save wrote
# while the tools ran (skipped when the context is rebuilt, see
# SessionManager.load_session_from_db).
NON_CONVERSATIONAL_ROLES = TOOL_EXCHANGE_ROLES + (MessageRole.REFLECTION,)


async def delete_tool_exchange_messages(
    db: AsyncSession,
    conversation_id: str,
    after: Optional[datetime] = None,
    before: Optional[datetime] = None,
) -> List[str]:
    """
    Delete the tool exchange rows of one turn.

    The bounds are the surrounding conversational messages: `after` is the
    message the response answered (its human message, or the previous
    assistant message for a continuation) and `before` is the response
    itself, or the start of the next turn when the response is gone. Both
    are exclusive, and an omitted bound means "to that end of the
    conversation".

    Returns the IDs of the deleted rows. Tool exchanges are never
    vectorized, so there is nothing to remove from the vector store.
    """
    conditions = [
        Message.conversation_id == str(conversation_id),
        Message.role.in_(TOOL_EXCHANGE_ROLES),
    ]
    if after is not None:
        conditions.append(Message.created_at > after)
    if before is not None:
        conditions.append(Message.created_at < before)

    result = await db.execute(
        select(Message).where(and_(*conditions)).order_by(Message.created_at)
    )
    messages = result.scalars().all()

    deleted_ids = []
    for message in messages:
        deleted_ids.append(str(message.id))
        await db.delete(message)

    if deleted_ids:
        logger.info(
            f"[HISTORY] Removed {len(deleted_ids)} tool exchange messages from "
            f"conversation {str(conversation_id)[:8]}... with the response they belonged to"
        )

    return deleted_ids


async def find_preceding_conversational_message(
    db: AsyncSession,
    conversation_id: str,
    message: Message,
) -> Optional[Message]:
    """
    Find the message immediately before `message`, ignoring the tool
    exchange and reflection rows of its own response.

    Callers care about the conversational shape of the history (was this
    response prompted by the human, or is it a continuation of another
    entity's turn?), which the interleaved TOOL_USE / TOOL_RESULT and
    REFLECTION rows otherwise hide.
    """
    result = await db.execute(
        select(Message)
        .where(
            and_(
                Message.conversation_id == str(conversation_id),
                Message.created_at <= message.created_at,
                Message.id != str(message.id),
                Message.role.notin_(NON_CONVERSATIONAL_ROLES),
            )
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
