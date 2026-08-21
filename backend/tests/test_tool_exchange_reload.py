"""
Tests for tool exchange fidelity across a conversation reload.

A response that used tools is persisted as TOOL_USE / TOOL_RESULT rows plus
the assistant row. Reloading a conversation has to reproduce what the live
stream showed: the whole response text on the assistant row, one tool card
per call, and no exchanges left over from a response that was discarded.
"""
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import Conversation, ConversationType, Message, MessageRole
from app.services.message_history import (
    delete_tool_exchange_messages,
    find_preceding_conversational_message,
)
from app.services.session_manager import SessionManager


@pytest.fixture
async def conversation(db_session):
    """A single-entity conversation to hang messages off."""
    conv = Conversation(
        id=str(uuid.uuid4()),
        title="Tool exchange reload",
        entity_id="test-entity",
        conversation_type=ConversationType.NORMAL,
    )
    db_session.add(conv)
    await db_session.commit()
    return conv


def tool_use_content(tool_id="tu-1", name="memory_save", **input_fields):
    """Serialized TOOL_USE content blocks, text block included."""
    return json.dumps([
        {"type": "text", "text": "Let me save that."},
        {"type": "tool_use", "id": tool_id, "name": name, "input": input_fields},
    ])


async def make_turn(db, conversation, base, *, with_tools=True, human="hi", answer="done"):
    """
    Persist one turn the way routes/chat.py does: human message, then (when
    tools ran) a tool exchange pair, then the assistant response.
    """
    rows = [Message(
        conversation_id=conversation.id,
        role=MessageRole.HUMAN,
        content=human,
        created_at=base,
    )]
    if with_tools:
        rows.append(Message(
            conversation_id=conversation.id,
            role=MessageRole.TOOL_USE,
            content=tool_use_content(content="A reflection."),
            created_at=base + timedelta(seconds=1),
        ))
        rows.append(Message(
            conversation_id=conversation.id,
            role=MessageRole.TOOL_RESULT,
            content=json.dumps([
                {"type": "tool_result", "tool_use_id": "tu-1", "content": "Saved.", "is_error": False}
            ]),
            created_at=base + timedelta(seconds=1, microseconds=1),
        ))
    rows.append(Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=answer,
        created_at=base + timedelta(seconds=2),
    ))
    for row in rows:
        db.add(row)
    await db.commit()
    return rows


async def conversation_rows(db, conversation):
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
    )
    return list(result.scalars().all())


class TestStreamedContentSurvivesToolCalls:
    """The assistant row must hold everything the model said, not just the
    text of the iteration that ended the tool loop."""

    @pytest.mark.asyncio
    async def test_done_event_carries_text_from_every_iteration(
        self, db_session, sample_conversation
    ):
        manager = SessionManager()

        with patch("app.services.session_manager.memory_service") as mock_memory, \
             patch("app.services.session_manager.llm_service") as mock_llm, \
             patch("app.services.session_manager.tool_service") as mock_tool, \
             patch("app.services.session_manager.settings") as mock_settings:
            mock_memory.is_configured.return_value = False
            mock_memory.get_archived_conversation_ids = AsyncMock(return_value=set())
            mock_memory.get_retrieved_memories_with_timestamps = AsyncMock(return_value=[])
            mock_settings.default_model = "claude-sonnet-4-5-20250929"
            mock_settings.default_temperature = 1.0
            mock_settings.default_max_tokens = 64000
            mock_settings.context_token_limit = 150000
            mock_settings.tool_use_max_iterations = 10

            mock_llm.build_messages.side_effect = lambda **kwargs: [
                {"role": "user", "content": "base"}
            ]
            mock_llm.count_tokens = MagicMock(return_value=10)

            call_count = [0]

            async def mock_stream(messages, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    yield {"type": "start", "model": "claude-sonnet-4-5-20250929"}
                    yield {"type": "token", "content": "Thinking out loud."}
                    yield {
                        "type": "done",
                        "content": "Thinking out loud.",
                        "content_blocks": [
                            {"type": "text", "text": "Thinking out loud."},
                            {"type": "tool_use", "id": "tu-1", "name": "memory_save",
                             "input": {"content": "A reflection."}},
                        ],
                        "tool_use": [{"id": "tu-1", "name": "memory_save",
                                      "input": {"content": "A reflection."}}],
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {},
                        "stop_reason": "tool_use",
                    }
                else:
                    yield {"type": "token", "content": "Saved it."}
                    yield {
                        "type": "done",
                        "content": "Saved it.",
                        "content_blocks": [{"type": "text", "text": "Saved it."}],
                        "model": "claude-sonnet-4-5-20250929",
                        "usage": {},
                        "stop_reason": "end_turn",
                    }

            mock_llm.send_message_stream = mock_stream

            tool_result = MagicMock()
            tool_result.tool_use_id = "tu-1"
            tool_result.content = "Saved reflection as memory abcd1234."
            tool_result.is_error = False
            mock_tool.execute_tool = AsyncMock(return_value=tool_result)

            session = await manager.load_session_from_db(
                sample_conversation.id, db_session
            )

            events = []
            async for event in manager.process_message_stream(
                session=session,
                user_message="hi",
                db=db_session,
                tool_schemas=[{"name": "memory_save"}],
            ):
                events.append(event)

            done = [e for e in events if e["type"] == "done"][-1]

            # The routes persist and vectorize this field. Dropping the text
            # that preceded the tool call would leave the assistant row (and
            # its memory) holding only the closing fragment.
            assert done["content"] == "Thinking out loud. Saved it."

            # ...and it matches what went into the session context, so a
            # reload rebuilds the prompt the cache was written for.
            assistant_msgs = [
                m for m in session.conversation_context
                if m["role"] == "assistant" and isinstance(m.get("content"), str)
            ]
            assert assistant_msgs[-1]["content"] == "Thinking out loud. Saved it."


class TestDeleteToolExchangeMessages:
    """Discarding a response takes its tool exchange rows with it."""

    @pytest.mark.asyncio
    async def test_deletes_only_the_bounded_exchanges(self, db_session, conversation):
        first = datetime(2026, 1, 1, 12, 0, 0)
        second = datetime(2026, 1, 1, 12, 5, 0)
        await make_turn(db_session, conversation, first, human="one", answer="first")
        await make_turn(db_session, conversation, second, human="two", answer="second")

        deleted = await delete_tool_exchange_messages(
            db_session,
            conversation.id,
            after=second,
            before=second + timedelta(seconds=2),
        )
        await db_session.commit()

        assert len(deleted) == 2

        roles = [m.role for m in await conversation_rows(db_session, conversation)]
        assert roles == [
            MessageRole.HUMAN,
            MessageRole.TOOL_USE,
            MessageRole.TOOL_RESULT,
            MessageRole.ASSISTANT,
            MessageRole.HUMAN,
            MessageRole.ASSISTANT,
        ]

    @pytest.mark.asyncio
    async def test_open_upper_bound_reaches_the_end(self, db_session, conversation):
        base = datetime(2026, 1, 1, 12, 0, 0)
        await make_turn(db_session, conversation, base)

        deleted = await delete_tool_exchange_messages(
            db_session, conversation.id, after=base
        )
        await db_session.commit()

        assert len(deleted) == 2
        roles = [m.role for m in await conversation_rows(db_session, conversation)]
        assert roles == [MessageRole.HUMAN, MessageRole.ASSISTANT]

    @pytest.mark.asyncio
    async def test_leaves_conversational_rows_alone(self, db_session, conversation):
        base = datetime(2026, 1, 1, 12, 0, 0)
        await make_turn(db_session, conversation, base, with_tools=False)

        deleted = await delete_tool_exchange_messages(db_session, conversation.id)
        await db_session.commit()

        assert deleted == []
        assert len(await conversation_rows(db_session, conversation)) == 2

    @pytest.mark.asyncio
    async def test_keeps_reflections(self, db_session, conversation):
        """A saved memory outlives the response that saved it - it is
        vectorized and retrievable, not part of the discarded turn."""
        base = datetime(2026, 1, 1, 12, 0, 0)
        await make_turn(db_session, conversation, base)
        db_session.add(Message(
            conversation_id=conversation.id,
            role=MessageRole.REFLECTION,
            content="A reflection.",
            created_at=base + timedelta(seconds=1, microseconds=500),
        ))
        await db_session.commit()

        await delete_tool_exchange_messages(db_session, conversation.id, after=base)
        await db_session.commit()

        roles = [m.role for m in await conversation_rows(db_session, conversation)]
        assert MessageRole.REFLECTION in roles


class TestFindPrecedingConversationalMessage:
    """Continuation detection has to look past a response's own bookkeeping."""

    @pytest.mark.asyncio
    async def test_skips_tool_and_reflection_rows(self, db_session, conversation):
        base = datetime(2026, 1, 1, 12, 0, 0)
        human = Message(
            conversation_id=conversation.id,
            role=MessageRole.HUMAN,
            content="hi",
            created_at=base,
        )
        db_session.add(human)
        db_session.add(Message(
            conversation_id=conversation.id,
            role=MessageRole.REFLECTION,
            content="A reflection.",
            created_at=base + timedelta(seconds=1),
        ))
        db_session.add(Message(
            conversation_id=conversation.id,
            role=MessageRole.TOOL_USE,
            content=tool_use_content(content="A reflection."),
            created_at=base + timedelta(seconds=2),
        ))
        db_session.add(Message(
            conversation_id=conversation.id,
            role=MessageRole.TOOL_RESULT,
            content="[]",
            created_at=base + timedelta(seconds=3),
        ))
        assistant = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="done",
            created_at=base + timedelta(seconds=4),
        )
        db_session.add(assistant)
        await db_session.commit()

        preceding = await find_preceding_conversational_message(
            db_session, conversation.id, assistant
        )

        assert preceding is not None
        assert preceding.id == human.id

    @pytest.mark.asyncio
    async def test_finds_the_previous_assistant_for_a_continuation(
        self, db_session, conversation
    ):
        base = datetime(2026, 1, 1, 12, 0, 0)
        first = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="first speaker",
            created_at=base,
        )
        db_session.add(first)
        db_session.add(Message(
            conversation_id=conversation.id,
            role=MessageRole.TOOL_USE,
            content=tool_use_content(name="web_search", query="x"),
            created_at=base + timedelta(seconds=1),
        ))
        second = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="second speaker",
            created_at=base + timedelta(seconds=2),
        )
        db_session.add(second)
        await db_session.commit()

        preceding = await find_preceding_conversational_message(
            db_session, conversation.id, second
        )

        assert preceding is not None
        assert preceding.id == first.id
        assert preceding.role == MessageRole.ASSISTANT

    @pytest.mark.asyncio
    async def test_returns_none_for_the_first_message(self, db_session, conversation):
        base = datetime(2026, 1, 1, 12, 0, 0)
        human = Message(
            conversation_id=conversation.id,
            role=MessageRole.HUMAN,
            content="hi",
            created_at=base,
        )
        db_session.add(human)
        await db_session.commit()

        assert await find_preceding_conversational_message(
            db_session, conversation.id, human
        ) is None
