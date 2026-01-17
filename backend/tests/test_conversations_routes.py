"""
Integration tests for conversations routes.

Tests conversation CRUD, archiving, and entity handling.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import uuid
from datetime import datetime

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db
from app.models import Conversation, Message, MessageRole, ConversationType
from app.config import Settings, EntityConfig


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def db_session(test_engine) -> AsyncSession:
    """Create a test database session."""
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def mock_settings():
    """Mock settings with test entities."""
    with patch("app.routes.conversations.settings") as mock:
        mock.get_entity_by_index.return_value = EntityConfig(
            index_name="test-entity",
            label="Test Entity",
            description="Test entity for testing",
            llm_provider="anthropic",
        )
        mock.get_entities.return_value = [
            EntityConfig(
                index_name="test-entity",
                label="Test Entity",
                description="Test entity for testing",
                llm_provider="anthropic",
            )
        ]
        yield mock


@pytest.fixture
async def async_client(test_engine, mock_settings):
    """Create an async test client with database override."""
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


class TestCreateConversation:
    """Tests for creating conversations."""

    @pytest.mark.asyncio
    async def test_create_conversation_basic(self, async_client):
        """Test creating a basic conversation."""
        response = await async_client.post(
            "/api/conversations/",
            json={
                "title": "Test Conversation",
                "entity_id": "test-entity",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Test Conversation"
        assert data["entity_id"] == "test-entity"
        assert data["conversation_type"] == "normal"
        assert data["is_archived"] is False

    @pytest.mark.asyncio
    async def test_create_conversation_with_system_prompt(self, async_client):
        """Test creating a conversation with system prompt."""
        response = await async_client.post(
            "/api/conversations/",
            json={
                "title": "System Prompt Test",
                "entity_id": "test-entity",
                "system_prompt": "You are a helpful assistant.",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["system_prompt_used"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_create_conversation_reflection_type(self, async_client):
        """Test creating a reflection type conversation."""
        response = await async_client.post(
            "/api/conversations/",
            json={
                "title": "Reflection Test",
                "entity_id": "test-entity",
                "conversation_type": "reflection",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["conversation_type"] == "reflection"

    @pytest.mark.asyncio
    async def test_create_conversation_invalid_entity(self, async_client, mock_settings):
        """Test creating a conversation with invalid entity ID."""
        mock_settings.get_entity_by_index.return_value = None

        response = await async_client.post(
            "/api/conversations/",
            json={
                "title": "Invalid Entity Test",
                "entity_id": "nonexistent-entity",
            }
        )

        assert response.status_code == 400
        assert "not configured" in response.json()["detail"].lower()


class TestListConversations:
    """Tests for listing conversations."""

    @pytest.mark.asyncio
    async def test_list_empty(self, async_client):
        """Test listing when no conversations exist."""
        response = await async_client.get("/api/conversations/")

        assert response.status_code == 200
        data = response.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_list_with_conversations(self, async_client, test_engine):
        """Test listing conversations with messages."""
        # Create conversation and messages directly in database
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Test Conv",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.flush()

            message = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=MessageRole.HUMAN,
                content="Hello, this is a test message.",
            )
            session.add(message)
            await session.commit()

        response = await async_client.get("/api/conversations/")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Conv"
        assert data[0]["message_count"] == 1
        assert "Hello" in data[0]["preview"]

    @pytest.mark.asyncio
    async def test_list_excludes_archived(self, async_client, test_engine):
        """Test that archived conversations are excluded by default."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        async with async_session() as session:
            # Create archived conversation
            conv = Conversation(
                id=str(uuid.uuid4()),
                title="Archived Conv",
                entity_id="test-entity",
                is_archived=True,
            )
            session.add(conv)
            await session.flush()

            msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv.id,
                role=MessageRole.HUMAN,
                content="Test",
            )
            session.add(msg)
            await session.commit()

        response = await async_client.get("/api/conversations/")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0  # Archived excluded

    @pytest.mark.asyncio
    async def test_list_includes_archived_when_requested(self, async_client, test_engine):
        """Test that archived conversations are included when requested."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        async with async_session() as session:
            conv = Conversation(
                id=str(uuid.uuid4()),
                title="Archived Conv",
                entity_id="test-entity",
                is_archived=True,
            )
            session.add(conv)
            await session.flush()

            msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv.id,
                role=MessageRole.HUMAN,
                content="Test",
            )
            session.add(msg)
            await session.commit()

        response = await async_client.get(
            "/api/conversations/",
            params={"include_archived": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_list_filter_by_entity(self, async_client, test_engine):
        """Test filtering conversations by entity_id."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        async with async_session() as session:
            # Create conversations for different entities
            for entity_id in ["entity-1", "entity-2"]:
                conv = Conversation(
                    id=str(uuid.uuid4()),
                    title=f"Conv for {entity_id}",
                    entity_id=entity_id,
                )
                session.add(conv)
                await session.flush()

                msg = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conv.id,
                    role=MessageRole.HUMAN,
                    content="Test",
                )
                session.add(msg)

            await session.commit()

        response = await async_client.get(
            "/api/conversations/",
            params={"entity_id": "entity-1"}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["entity_id"] == "entity-1"


class TestGetConversation:
    """Tests for getting a specific conversation."""

    @pytest.mark.asyncio
    async def test_get_existing_conversation(self, async_client, test_engine):
        """Test getting an existing conversation."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Get Test Conv",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.commit()

        response = await async_client.get(f"/api/conversations/{conv_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == conv_id
        assert data["title"] == "Get Test Conv"

    @pytest.mark.asyncio
    async def test_get_nonexistent_conversation(self, async_client):
        """Test getting a non-existent conversation."""
        fake_id = str(uuid.uuid4())
        response = await async_client.get(f"/api/conversations/{fake_id}")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestUpdateConversation:
    """Tests for updating conversations."""

    @pytest.mark.asyncio
    async def test_update_title(self, async_client, test_engine):
        """Test updating conversation title."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Original Title",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.commit()

        response = await async_client.patch(
            f"/api/conversations/{conv_id}",
            json={"title": "Updated Title"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated Title"

    @pytest.mark.asyncio
    async def test_update_tags(self, async_client, test_engine):
        """Test updating conversation tags."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Tags Test",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.commit()

        response = await async_client.patch(
            f"/api/conversations/{conv_id}",
            json={"tags": ["tag1", "tag2"]}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tags"] == ["tag1", "tag2"]

    @pytest.mark.asyncio
    async def test_update_notes(self, async_client, test_engine):
        """Test updating conversation notes."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Notes Test",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.commit()

        response = await async_client.patch(
            f"/api/conversations/{conv_id}",
            json={"notes": "Some important notes."}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["notes"] == "Some important notes."

    @pytest.mark.asyncio
    async def test_update_nonexistent_conversation(self, async_client):
        """Test updating a non-existent conversation."""
        fake_id = str(uuid.uuid4())
        response = await async_client.patch(
            f"/api/conversations/{fake_id}",
            json={"title": "New Title"}
        )

        assert response.status_code == 404


class TestArchiveConversation:
    """Tests for archiving and unarchiving conversations."""

    @pytest.mark.asyncio
    async def test_archive_conversation(self, async_client, test_engine):
        """Test archiving a conversation."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Archive Test",
                entity_id="test-entity",
                is_archived=False,
            )
            session.add(conversation)
            await session.commit()

        response = await async_client.post(f"/api/conversations/{conv_id}/archive")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "archived"
        assert data["id"] == conv_id

    @pytest.mark.asyncio
    async def test_archive_already_archived(self, async_client, test_engine):
        """Test archiving an already archived conversation."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Already Archived",
                entity_id="test-entity",
                is_archived=True,
            )
            session.add(conversation)
            await session.commit()

        response = await async_client.post(f"/api/conversations/{conv_id}/archive")

        assert response.status_code == 400
        assert "already archived" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_unarchive_conversation(self, async_client, test_engine):
        """Test unarchiving a conversation."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Unarchive Test",
                entity_id="test-entity",
                is_archived=True,
            )
            session.add(conversation)
            await session.commit()

        response = await async_client.post(f"/api/conversations/{conv_id}/unarchive")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unarchived"
        assert data["id"] == conv_id

    @pytest.mark.asyncio
    async def test_unarchive_not_archived(self, async_client, test_engine):
        """Test unarchiving a conversation that isn't archived."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Not Archived",
                entity_id="test-entity",
                is_archived=False,
            )
            session.add(conversation)
            await session.commit()

        response = await async_client.post(f"/api/conversations/{conv_id}/unarchive")

        assert response.status_code == 400
        assert "not archived" in response.json()["detail"].lower()


class TestDeleteConversation:
    """Tests for deleting conversations."""

    @pytest.mark.asyncio
    async def test_delete_archived_conversation(self, async_client, test_engine):
        """Test deleting an archived conversation."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Delete Test",
                entity_id="test-entity",
                is_archived=True,
            )
            session.add(conversation)
            await session.commit()

        # Mock memory service (imported inside function from app.services)
        with patch("app.services.memory_service") as mock_memory:
            mock_memory.is_configured.return_value = False

            response = await async_client.delete(f"/api/conversations/{conv_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["id"] == conv_id

    @pytest.mark.asyncio
    async def test_delete_non_archived_fails(self, async_client, test_engine):
        """Test that deleting a non-archived conversation fails."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Cannot Delete",
                entity_id="test-entity",
                is_archived=False,
            )
            session.add(conversation)
            await session.commit()

        response = await async_client.delete(f"/api/conversations/{conv_id}")

        assert response.status_code == 400
        assert "archived" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_conversation(self, async_client):
        """Test deleting a non-existent conversation."""
        fake_id = str(uuid.uuid4())
        response = await async_client.delete(f"/api/conversations/{fake_id}")

        assert response.status_code == 404


class TestExportConversation:
    """Tests for exporting conversations."""

    @pytest.mark.asyncio
    async def test_export_conversation(self, async_client, test_engine):
        """Test exporting a conversation to JSON."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Export Test",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.flush()

            msg1 = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=MessageRole.HUMAN,
                content="Hello!",
            )
            msg2 = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=MessageRole.ASSISTANT,
                content="Hi there!",
            )
            session.add(msg1)
            session.add(msg2)
            await session.commit()

        response = await async_client.get(f"/api/conversations/{conv_id}/export")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == conv_id
        assert data["title"] == "Export Test"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["content"] == "Hello!"
        assert data["messages"][1]["content"] == "Hi there!"

    @pytest.mark.asyncio
    async def test_export_nonexistent_conversation(self, async_client):
        """Test exporting a non-existent conversation."""
        fake_id = str(uuid.uuid4())
        response = await async_client.get(f"/api/conversations/{fake_id}/export")

        assert response.status_code == 404


class TestGetConversationMessages:
    """Tests for getting conversation messages."""

    @pytest.mark.asyncio
    async def test_get_messages(self, async_client, test_engine):
        """Test getting messages for a conversation."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        conv_id = str(uuid.uuid4())
        async with async_session() as session:
            conversation = Conversation(
                id=conv_id,
                title="Messages Test",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.flush()

            for i, role in enumerate([MessageRole.HUMAN, MessageRole.ASSISTANT]):
                msg = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conv_id,
                    role=role,
                    content=f"Message {i+1}",
                )
                session.add(msg)

            await session.commit()

        response = await async_client.get(f"/api/conversations/{conv_id}/messages")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["role"] == "human"
        assert data[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_get_messages_nonexistent_conversation(self, async_client):
        """Test getting messages for a non-existent conversation."""
        fake_id = str(uuid.uuid4())
        response = await async_client.get(f"/api/conversations/{fake_id}/messages")

        assert response.status_code == 404


class TestListArchivedConversations:
    """Tests for listing archived conversations."""

    @pytest.mark.asyncio
    async def test_list_archived(self, async_client, test_engine):
        """Test listing archived conversations."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        async with async_session() as session:
            # Create one archived and one non-archived
            for i, archived in enumerate([True, False]):
                conv = Conversation(
                    id=str(uuid.uuid4()),
                    title=f"Conv {i}",
                    entity_id="test-entity",
                    is_archived=archived,
                )
                session.add(conv)
                await session.flush()

                msg = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conv.id,
                    role=MessageRole.HUMAN,
                    content="Test",
                )
                session.add(msg)

            await session.commit()

        response = await async_client.get("/api/conversations/archived")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["is_archived"] is True
