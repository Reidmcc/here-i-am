"""
Route-level tests for resuming a turn interrupted inside the tool loop.

The stream endpoint has to persist a resumed turn exactly as it would have
persisted the uninterrupted one: same human message, same send timestamp
(which the context timestamp prefix is derived from), and the whole response
text including what streamed before the interruption.
"""
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.models import Conversation, ConversationType, Message, MessageRole
from app.routes import chat as chat_routes
from app.services.conversation_session import ConversationSession, PendingToolTurn


@pytest.fixture
async def session_factory(test_engine):
    """A sessionmaker bound to the test database."""
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def conversation(session_factory):
    """A plain single-entity conversation to stream into."""
    conv = Conversation(
        id=str(uuid.uuid4()),
        title="Test Conversation",
        conversation_type=ConversationType.NORMAL,
        llm_model_used="claude-sonnet-4-5-20250929",
        entity_id="test-memories",
    )
    async with session_factory() as db:
        db.add(conv)
        await db.commit()
    return conv


def make_stashed_session(conversation_id, sent_at, message="What is the weather?"):
    """A warm session carrying an interrupted turn with one completed tool call."""
    session = ConversationSession(
        conversation_id=conversation_id,
        entity_id="test-memories",
        model="claude-sonnet-4-5-20250929",
    )
    turn = PendingToolTurn(
        entity_id="test-memories",
        conversation_id=conversation_id,
        raw_user_message=message,
        stamped_user_message=f"[2026-01-01 10:00 UTC] {message}",
        message_sent_at=sent_at,
        persistable_content=message,
        vectorize_user_message=message,
        base_messages=[{"role": "user", "content": "base"}],
    )
    turn.tool_exchanges.append({
        "assistant": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tool-1", "name": "web_search", "input": {"query": "weather"}}
            ],
        },
        "user": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "Sunny", "is_error": False}
            ],
        },
    })
    turn.accumulated_tool_uses.append({
        "call": {"name": "web_search", "id": "tool-1", "input": {"query": "weather"}},
        "result": {"content": "Sunny", "is_error": False},
    })
    turn.full_content = "Let me check. "
    turn.next_iteration = 2
    session.stash_pending_tool_turn(turn, error="overloaded", error_type="overloaded")
    return session


async def post_stream(client, body):
    """POST to the stream endpoint and parse the SSE events out of the body."""
    response = await client.post("/api/chat/stream", json=body)
    assert response.status_code == 200
    events = []
    event_type = None
    for line in response.text.split("\n"):
        if line.startswith("event: "):
            event_type = line[len("event: "):].strip()
        elif line.startswith("data: ") and event_type:
            events.append((event_type, json.loads(line[len("data: "):])))
            event_type = None
    return events


@pytest.fixture
async def stream_client(session_factory):
    """
    Test client for the stream endpoint.

    The endpoint manages its own DB session (streaming outlives the request
    dependency), so async_session_maker is patched rather than get_db.
    Vectorization is switched off and token counting is stubbed (tiktoken
    fetches its encoding over the network) - neither is what these tests are
    about.
    """
    with patch("app.routes.chat.async_session_maker", session_factory), \
         patch("app.routes.chat.memory_service") as mock_memory, \
         patch.object(chat_routes.llm_service, "count_tokens", return_value=10):
        mock_memory.is_configured.return_value = False
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


class TestStreamResume:
    """Tests for `resume` on the stream endpoint."""

    @pytest.mark.asyncio
    async def test_resume_continues_the_stashed_turn(
        self, stream_client, session_factory, conversation
    ):
        """A resumable turn is continued, and persisted as one complete turn."""
        sent_at = datetime.utcnow() - timedelta(minutes=2)
        session = make_stashed_session(conversation.id, sent_at)

        async def fake_resume(session, turn, tool_schemas=None):
            # Continue the stashed turn: prior text plus the new tail
            yield {"type": "token", "content": "It is sunny."}
            yield {
                "type": "done",
                "content": turn.full_content + "It is sunny.",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 30, "output_tokens": 12},
                "stop_reason": "end_turn",
                "tool_exchanges": turn.tool_exchanges,
            }

        with patch("app.routes.chat.session_manager") as mock_manager:
            mock_manager.get_session.return_value = session
            mock_manager.resume_message_stream = fake_resume
            mock_manager.process_message_stream = MagicMock(
                side_effect=AssertionError("should not start a fresh turn")
            )

            events = await post_stream(stream_client, {
                "conversation_id": conversation.id,
                "message": "What is the weather?",
                "resume": True,
                "resume_turn_id": session.pending_tool_turn.turn_id,
            })

        assert [e for e, _ in events if e == "stored"]

        async with session_factory() as db:
            result = await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at)
            )
            messages = result.scalars().all()

        human = [m for m in messages if m.role == MessageRole.HUMAN]
        assistant = [m for m in messages if m.role == MessageRole.ASSISTANT]
        tool_use = [m for m in messages if m.role == MessageRole.TOOL_USE]
        tool_result = [m for m in messages if m.role == MessageRole.TOOL_RESULT]

        # The human message is written once, with the ORIGINAL send time: the
        # timestamp prefix stamped into context was derived from it, and a
        # session reload has to re-render the identical prefix.
        assert len(human) == 1
        assert human[0].content == "What is the weather?"
        assert human[0].created_at == sent_at

        # The tool exchange from before the interruption is persisted
        assert len(tool_use) == 1
        assert len(tool_result) == 1

        # The response text spans both sides of the interruption
        assert len(assistant) == 1
        assert assistant[0].content == "Let me check. It is sunny."

    @pytest.mark.asyncio
    async def test_resume_naming_a_different_turn_falls_back_to_a_fresh_send(
        self, stream_client, session_factory, conversation
    ):
        """A resume for some other turn must not consume this stash."""
        session = make_stashed_session(conversation.id, datetime.utcnow())

        async def fake_process(**kwargs):
            yield {"type": "token", "content": "Fresh answer"}
            yield {
                "type": "done",
                "content": "Fresh answer",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {},
                "stop_reason": "end_turn",
            }

        with patch("app.routes.chat.session_manager") as mock_manager:
            mock_manager.get_session.return_value = session
            mock_manager.process_message_stream = fake_process
            mock_manager.resume_message_stream = MagicMock(
                side_effect=AssertionError("must not resume another turn")
            )

            await post_stream(stream_client, {
                "conversation_id": conversation.id,
                "message": "Different message",
                "resume": True,
                "resume_turn_id": "not-the-stashed-turn",
            })

        async with session_factory() as db:
            result = await db.execute(
                select(Message).where(Message.conversation_id == conversation.id)
            )
            messages = result.scalars().all()

        # The fresh turn's own message is stored, not the stashed turn's
        assert [m.content for m in messages if m.role == MessageRole.HUMAN] == ["Different message"]

    @pytest.mark.asyncio
    async def test_resume_without_a_stashed_turn_falls_back_to_a_fresh_send(
        self, stream_client, session_factory, conversation
    ):
        """
        Resuming when there is nothing to resume runs a normal turn.

        The stash lives in memory, so a server restart or an expiry can remove
        it at any time; the client asks optimistically and must not get an
        error for guessing wrong.
        """
        session = ConversationSession(
            conversation_id=conversation.id,
            entity_id="test-memories",
            model="claude-sonnet-4-5-20250929",
        )

        async def fake_process(**kwargs):
            yield {"type": "token", "content": "Fresh answer"}
            yield {
                "type": "done",
                "content": "Fresh answer",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 10, "output_tokens": 4},
                "stop_reason": "end_turn",
            }

        with patch("app.routes.chat.session_manager") as mock_manager:
            mock_manager.get_session.return_value = session
            mock_manager.process_message_stream = fake_process
            mock_manager.resume_message_stream = MagicMock(
                side_effect=AssertionError("nothing to resume")
            )

            events = await post_stream(stream_client, {
                "conversation_id": conversation.id,
                "message": "Hello again",
                "resume": True,
            })

        assert not [e for e, _ in events if e == "error"]

        async with session_factory() as db:
            result = await db.execute(
                select(Message).where(Message.conversation_id == conversation.id)
            )
            messages = result.scalars().all()

        assert [m.content for m in messages if m.role == MessageRole.HUMAN] == ["Hello again"]
        assert [m.content for m in messages if m.role == MessageRole.ASSISTANT] == ["Fresh answer"]

    @pytest.mark.asyncio
    async def test_interrupted_turn_error_persists_nothing(
        self, stream_client, session_factory, conversation
    ):
        """
        An interrupted turn writes no rows.

        The turn is only half-finished; persisting it would leave a partial
        exchange in history that the resume would then duplicate.
        """
        async def fake_process(**kwargs):
            yield {"type": "token", "content": "Let me check. "}
            yield {"type": "tool_start", "tool_name": "web_search", "tool_id": "tool-1", "input": {}}
            yield {
                "type": "error",
                "error": "overloaded",
                "error_type": "interrupted_tool_turn",
                "provider_error_type": "overloaded",
                "completed_iterations": 1,
                "completed_tool_calls": 1,
            }

        session = ConversationSession(
            conversation_id=conversation.id,
            entity_id="test-memories",
            model="claude-sonnet-4-5-20250929",
        )

        with patch("app.routes.chat.session_manager") as mock_manager:
            mock_manager.get_session.return_value = session
            mock_manager.process_message_stream = fake_process

            events = await post_stream(stream_client, {
                "conversation_id": conversation.id,
                "message": "What is the weather?",
            })

        error = next(data for etype, data in events if etype == "error")
        assert error["error_type"] == "interrupted_tool_turn"
        assert error["completed_tool_calls"] == 1

        # The session is left warm for the resume, not closed
        assert not mock_manager.close_session.called

        async with session_factory() as db:
            result = await db.execute(
                select(Message).where(Message.conversation_id == conversation.id)
            )
            assert result.scalars().all() == []

    @pytest.mark.asyncio
    async def test_stream_rewind_is_forwarded_to_the_client(
        self, stream_client, conversation
    ):
        """The client needs the rewind to drop text a failed attempt streamed."""
        async def fake_process(**kwargs):
            yield {"type": "token", "content": "Half a sent"}
            yield {"type": "stream_rewind", "checkpoint": 0}
            yield {"type": "token", "content": "Complete answer"}
            yield {
                "type": "done",
                "content": "Complete answer",
                "model": "claude-sonnet-4-5-20250929",
                "usage": {"input_tokens": 10, "output_tokens": 4},
                "stop_reason": "end_turn",
            }

        session = ConversationSession(
            conversation_id=conversation.id,
            entity_id="test-memories",
            model="claude-sonnet-4-5-20250929",
        )

        with patch("app.routes.chat.session_manager") as mock_manager:
            mock_manager.get_session.return_value = session
            mock_manager.process_message_stream = fake_process

            events = await post_stream(stream_client, {
                "conversation_id": conversation.id,
                "message": "Hello",
            })

        rewinds = [data for etype, data in events if etype == "stream_rewind"]
        assert rewinds == [{"type": "stream_rewind", "checkpoint": 0}]
