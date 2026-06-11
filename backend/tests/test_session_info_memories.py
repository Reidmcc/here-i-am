"""
Tests for per-entity memory reconstruction in the session-info endpoint.

When the user leaves a multi-entity conversation and returns, the in-memory
session only retains the most recent responding entity's memories. The
"Retrieved Memories" panel groups memories per entity, so the backend rebuilds
that grouping from the persisted ConversationMemoryLink records. These tests
cover that reconstruction (_build_memories_by_entity).
"""
import uuid
from datetime import datetime
from unittest.mock import patch, AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.database import get_db
from app.models import (
    Conversation,
    ConversationEntity,
    ConversationMemoryLink,
    Message,
    MessageRole,
    ConversationType,
)
from app.routes.chat import _build_memories_by_entity
from app.services import session_manager
from app.services.conversation_session import MemoryEntry


@pytest.fixture
async def async_client(test_engine):
    """Async test client with the DB dependency pointed at the in-memory engine."""
    async_session = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def _seed_multi_entity(db, *, entity_ids):
    conv = Conversation(
        id=str(uuid.uuid4()),
        entity_id="multi-entity",
        conversation_type=ConversationType.MULTI_ENTITY,
    )
    db.add(conv)
    await db.flush()

    for order, eid in enumerate(entity_ids):
        db.add(ConversationEntity(
            conversation_id=conv.id,
            entity_id=eid,
            display_order=order,
        ))
    await db.commit()
    return conv


async def _add_memory(db, *, conversation_id, entity_id, content, role=MessageRole.HUMAN,
                      times_retrieved=1, source_conversation=None):
    """Create a source message and a link recording its retrieval by an entity."""
    source_conv_id = source_conversation or conversation_id
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=source_conv_id,
        role=role,
        content=content,
        times_retrieved=times_retrieved,
    )
    db.add(msg)
    await db.flush()
    db.add(ConversationMemoryLink(
        conversation_id=conversation_id,
        message_id=msg.id,
        entity_id=entity_id,
        retrieved_at=datetime.utcnow(),
    ))
    await db.commit()
    return msg


@pytest.mark.asyncio
async def test_build_groups_memories_per_entity(db_session):
    conv = await _seed_multi_entity(db_session, entity_ids=["entity-a", "entity-b"])

    await _add_memory(db_session, conversation_id=conv.id, entity_id="entity-a",
                      content="A's memory one")
    await _add_memory(db_session, conversation_id=conv.id, entity_id="entity-b",
                      content="B's memory one", role=MessageRole.ASSISTANT)
    await _add_memory(db_session, conversation_id=conv.id, entity_id="entity-b",
                      content="B's memory two")

    with patch("app.routes.chat.memory_service.is_configured", return_value=False), \
         patch("app.routes.chat.get_entity_label", side_effect=lambda eid: f"Label::{eid}"):
        grouped = await _build_memories_by_entity(conv.id, db_session)

    assert set(grouped.keys()) == {"entity-a", "entity-b"}
    assert grouped["entity-a"]["label"] == "Label::entity-a"
    assert len(grouped["entity-a"]["memories"]) == 1
    assert len(grouped["entity-b"]["memories"]) == 2
    contents = {m["content"] for m in grouped["entity-b"]["memories"]}
    assert contents == {"B's memory one", "B's memory two"}


@pytest.mark.asyncio
async def test_build_excludes_entities_with_no_memories(db_session):
    conv = await _seed_multi_entity(db_session, entity_ids=["entity-a", "entity-b"])
    await _add_memory(db_session, conversation_id=conv.id, entity_id="entity-a",
                      content="only A retrieved")

    with patch("app.routes.chat.memory_service.is_configured", return_value=False), \
         patch("app.routes.chat.get_entity_label", side_effect=lambda eid: f"Label::{eid}"):
        grouped = await _build_memories_by_entity(conv.id, db_session)

    # entity-b never retrieved anything, so it is omitted rather than shown empty.
    assert set(grouped.keys()) == {"entity-a"}


@pytest.mark.asyncio
async def test_build_skips_memories_from_archived_source_conversations(db_session):
    conv = await _seed_multi_entity(db_session, entity_ids=["entity-a"])

    # One memory whose source conversation is archived, one that is live.
    archived_src = str(uuid.uuid4())
    await _add_memory(db_session, conversation_id=conv.id, entity_id="entity-a",
                      content="from archived source", source_conversation=archived_src)
    await _add_memory(db_session, conversation_id=conv.id, entity_id="entity-a",
                      content="from live source")

    with patch("app.routes.chat.memory_service.is_configured", return_value=True), \
         patch("app.routes.chat.memory_service.get_archived_conversation_ids",
               new=AsyncMock(return_value={archived_src})), \
         patch("app.routes.chat.get_entity_label", side_effect=lambda eid: f"Label::{eid}"):
        grouped = await _build_memories_by_entity(conv.id, db_session)

    memories = grouped["entity-a"]["memories"]
    assert len(memories) == 1
    assert memories[0]["content"] == "from live source"


@pytest.mark.asyncio
async def test_session_info_returns_memories_in_memory_in_context_mode(async_client):
    """Regression: memory-in-context mode tracks in-context memories via the
    memory_tracker, not session.in_context_ids. The session-info endpoint must
    read the right source, otherwise the panel blanks after switching away and
    back from a single-entity conversation.
    """
    conversation_id = str(uuid.uuid4())
    session = session_manager.create_session(
        conversation_id=conversation_id,
        model="claude-sonnet-4-5-20250929",
        entity_id="test-entity",
    )
    session.use_memory_in_context = True

    memory = MemoryEntry(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role="human",
        content="a remembered thing",
        created_at=datetime.utcnow().isoformat(),
        times_retrieved=3,
        score=0.88,
    )
    inserted, _ = session.insert_memory_into_context(memory)
    assert inserted
    # The legacy set stays empty in this mode - the bug was reading from it.
    assert session.in_context_ids == set()

    try:
        response = await async_client.get(f"/api/chat/session/{conversation_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["memories_in_context"] == 1
        assert [m["id"] for m in data["memories"]] == [memory.id]
        assert data["memories"][0]["content"] == "a remembered thing"
    finally:
        session_manager.close_session(conversation_id)


@pytest.mark.asyncio
async def test_build_returns_empty_for_non_multi_entity(db_session):
    # A conversation with no participant rows yields no groups.
    conv = Conversation(id=str(uuid.uuid4()), entity_id="solo")
    db_session.add(conv)
    await db_session.commit()

    with patch("app.routes.chat.memory_service.is_configured", return_value=False), \
         patch("app.routes.chat.get_entity_label", side_effect=lambda eid: f"Label::{eid}"):
        grouped = await _build_memories_by_entity(conv.id, db_session)

    assert grouped == {}
