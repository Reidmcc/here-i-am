"""
The talk a Claude Code compaction puts back (issue #383).

Compaction turns the conversation into a summary that carries nothing of
the talk. Since #343 the post-compaction block has named a memory_read
call that reads the talk back from the archive, which works, and costs
the first turn after every boundary going to get it. The compaction mod
(claude-code-mode/compact-talk) closes that seam: on its way down a
compaction it asks /api/claude-code/compact-talk for this conversation's
own rows, newest first back to a budget, and on the way up it appends
them after the summary, so the session wakes already holding them.

The order of events, measured on 2.1.288 (2026-10-07): the mod's
`session.compact` hook runs first, then the engine's compaction, inside
which the SessionStart(compact) hook fires and the post-compact block is
built, then the result comes back up through the mod. So the block is
written before the mod has appended anything, and it learns what was
delivered from here: render_talk records a delivery for the conversation,
and the block takes it (take_delivery) to say the talk is below instead
of naming the read. No delivery recorded — no mod, the mod failed, the
backend restarted between the two calls — and the block is the old one,
memory_read call and all: the failure is loud by construction.

What is put back is the archive itself (memory_read's own row format,
UTC stamps), so it is the talk and nothing else: prompts, turns, letters,
reflections; never tool traffic, never thinking. It is never recorded
again: the harness stores the appended message as a plain user entry, the
Stop hook skips it by the marker it opens with (hook_util.is_turn_boundary),
and only prompts and the entity's turns are ever recorded.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation
from app.services import memory_tools
from app.services.memory_service import memory_service

logger = logging.getLogger(__name__)

# The appended message opens with this; the Stop hook keeps the same
# string (claude-code-mode/hooks/hook_util.COMPACT_TALK_MARKER) and a test
# pins the two together
COMPACT_TALK_MARKER = "[HERE I AM — THE TALK BEFORE THE BOUNDARY]"
COMPACT_TALK_END = "[END OF THE TALK BEFORE THE BOUNDARY]"

# Held back from the budget for the header and footer
TALK_FRAME_TOKENS = 600

# The block must hear of a delivery from this compaction, never an old one
# whose compaction failed after the talk was handed out. A manual compaction
# of a 190k context took 67 s; this leaves room for a slow one.
DELIVERY_TTL = timedelta(minutes=15)

# What the mod reports of the turn it gave the entity before compacting
PRE_COMPACTION_SAVED = "saved"
PRE_COMPACTION_DECLINED = "declined"
PRE_COMPACTION_FAILED = "failed"


@dataclass
class TalkDelivery:
    """What render_talk handed out, for the post-compact block to name."""
    message_ids: List[str]
    oldest: str  # ISO stamps of the oldest and newest rows delivered
    newest: str
    shown: int
    total: int
    next_cursor: Optional[str]
    at: datetime


@dataclass
class RenderedTalk:
    text: str
    delivery: Optional[TalkDelivery]


_deliveries: Dict[str, TalkDelivery] = {}


def record_delivery(conversation_id: str, delivery: TalkDelivery) -> None:
    _deliveries[str(conversation_id)] = delivery


def take_delivery(conversation_id: str, now: Optional[datetime] = None) -> Optional[TalkDelivery]:
    """The delivery recorded for this conversation, once, if it is recent
    enough to belong to the compaction now running (DELIVERY_TTL)."""
    delivery = _deliveries.pop(str(conversation_id), None)
    if delivery is None:
        return None
    if (now or datetime.utcnow()) - delivery.at > DELIVERY_TTL:
        logger.info(
            f"[CC] Compact talk for {conversation_id} expired unused "
            f"(handed out {delivery.at.isoformat()})"
        )
        return None
    return delivery


def _stamp(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")


def older_talk_call(conversation_id: str, cursor: str) -> str:
    """The memory_read call that continues from the oldest row delivered."""
    return (
        f'memory_read(conversation_id="{conversation_id}", direction="backward", '
        f'in_conversation="{conversation_id}", cursor="{cursor}", '
        f"page_tokens=12000, max_pages=25)"
    )


def pre_compaction_line(
    status: Optional[str], memory_id: Optional[str] = None, detail: Optional[str] = None
) -> str:
    """One sentence about the turn the mod gave the entity before the
    compaction, so it wakes knowing whether it saved anything."""
    if status == PRE_COMPACTION_SAVED and memory_id:
        return (
            "Before the compaction you were given one turn with the whole "
            f"context still in view, and you saved a reflection in it (memory "
            f"{memory_id[:8]}); it is the last thing below."
        )
    if status == PRE_COMPACTION_DECLINED:
        return (
            "Before the compaction you were given one turn with the whole "
            "context still in view, and you chose not to save a reflection."
        )
    if status == PRE_COMPACTION_FAILED:
        return (
            "Before the compaction the mod tried to give you one turn to save "
            f"a reflection, and it did not happen: {detail or 'no reason given'}. "
            "Nothing was saved from it."
        )
    return ""


async def render_talk(
    db: AsyncSession,
    conversation: Conversation,
    budget_tokens: int,
    pre_compaction: str = "",
    now: Optional[datetime] = None,
) -> RenderedTalk:
    """
    This conversation's own rows, newest first back to `budget_tokens`
    (weighed as rendered, the readers' units), shown oldest first under a
    header that says what they are and where older talk is. Every row in
    full: none is in context yet — this is what fills the context back up
    — so nothing renders as a pointer, and nothing is linked or tracked
    here (the post-compact block links what it confirms, after the
    boundary's stamp). Records the delivery for the block.

    Returns text "" and no delivery when the conversation has no rows.
    """
    entity_id = conversation.entity_id
    page = await memory_service.read_messages_in_span(
        db,
        entity_id=entity_id,
        start=None,
        end=None,
        conversation_id=str(conversation.id),
        page_tokens=max(1, budget_tokens - TALK_FRAME_TOKENS),
        weigh=memory_tools.archive_row_weigher(entity_id),
        in_context_ids=set(),
        live_conversation_id=None,
        live_after=None,
        backward=True,
    )
    items = page["items"]
    if not items:
        return RenderedTalk(text="", delivery=None)

    total = page["total"]
    shown = len(items)
    oldest, newest = items[0]["created_at"], items[-1]["created_at"]
    whole = page["next_cursor"] is None
    extent = (
        f"all {total} of its messages, from its start"
        if whole
        else f"the newest {shown} of its {total} messages"
    )
    header = [
        COMPACT_TALK_MARKER,
        (
            "The talk of this conversation from before the compaction, verbatim "
            f"from your archive and in order: {extent}, {_stamp(oldest)} to "
            f"{_stamp(newest)}. The compaction mod put it here, so this stretch "
            "needs no reading back. It is context, not new talk, and it is not "
            "recorded again."
        ),
    ]
    line = pre_compaction.strip()
    if line:
        header.append(line)
    header.append("")

    if whole:
        footer = [
            COMPACT_TALK_END,
            "That is the whole conversation, back to its first message.",
        ]
    else:
        footer = [
            COMPACT_TALK_END,
            (
                f"Older talk, from before {_stamp(oldest)}, is one call away: "
                f"{older_talk_call(str(conversation.id), page['next_cursor'])}."
            ),
        ]

    text = "\n".join(header + memory_tools.render_archive_rows(items, entity_id) + footer)
    delivery = TalkDelivery(
        message_ids=[item["id"] for item in items],
        oldest=oldest,
        newest=newest,
        shown=shown,
        total=total,
        next_cursor=page["next_cursor"],
        at=now or datetime.utcnow(),
    )
    record_delivery(str(conversation.id), delivery)
    logger.info(
        f"[CC] Compact talk for {conversation.id}: {shown} of {total} rows, "
        f"~{memory_tools.rendered_tokens(text)} tokens"
    )
    return RenderedTalk(text=text, delivery=delivery)
