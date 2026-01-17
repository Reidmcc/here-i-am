"""
Integration tests for messages routes.

Tests message editing and deletion.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import uuid
from datetime import datetime, timedelta

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.main import app
from app.database import Base, get_db
from app.models import Conversation, Message, MessageRole


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
def mock_memory_service():
    """Mock memory service for tests."""
    with patch("app.routes.messages.memory_service") as mock:
        mock.is_configured.return_value = True
        mock.delete_memory = AsyncMock(return_value=True)
        mock.store_memory = AsyncMock(return_value=True)
        yield mock


@pytest.fixture
def mock_session_manager():
    """Mock session manager for tests."""
    with patch("app.routes.messages.session_manager") as mock:
        mock.close_session = MagicMock()
        yield mock


@pytest.fixture
def mock_llm_service():
    """Mock LLM service for token counting."""
    with patch("app.routes.messages.llm_service") as mock:
        mock.count_tokens = MagicMock(return_value=10)
        yield mock


@pytest.fixture
async def async_client(test_engine, mock_memory_service, mock_session_manager, mock_llm_service):
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


@pytest.fixture
async def create_conversation_with_messages(test_engine):
    """Create a conversation with human and assistant messages."""
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    created_data = {"conversation_id": None, "messages": []}

    async with async_session() as session:
        conv_id = str(uuid.uuid4())
        conversation = Conversation(
            id=conv_id,
            title="Test Conversation",
            entity_id="test-entity",
        )
        session.add(conversation)
        await session.flush()
        created_data["conversation_id"] = conv_id

        now = datetime.utcnow()

        # Create human message
        human_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv_id,
            role=MessageRole.HUMAN,
            content="Hello, this is my question.",
            created_at=now - timedelta(seconds=10),
        )
        session.add(human_msg)
        created_data["messages"].append({
            "id": human_msg.id,
            "role": "human",
            "content": human_msg.content,
        })

        # Create assistant message (response to human)
        assistant_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv_id,
            role=MessageRole.ASSISTANT,
            content="This is my response.",
            created_at=now - timedelta(seconds=5),
        )
        session.add(assistant_msg)
        created_data["messages"].append({
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
        })

        await session.commit()

    return created_data


class TestUpdateMessage:
    """Tests for updating messages."""

    @pytest.mark.asyncio
    async def test_update_human_message(
        self, async_client, create_conversation_with_messages
    ):
        """Test updating a human message content."""
        human_msg_id = create_conversation_with_messages["messages"][0]["id"]

        response = await async_client.put(
            f"/api/messages/{human_msg_id}",
            json={"content": "Updated question text."}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message"]["content"] == "Updated question text."
        # Assistant message should be deleted
        assert data["deleted_assistant_message_id"] is not None

    @pytest.mark.asyncio
    async def test_update_message_updates_token_count(
        self, async_client, create_conversation_with_messages, mock_llm_service
    ):
        """Test that updating message updates token count."""
        human_msg_id = create_conversation_with_messages["messages"][0]["id"]

        response = await async_client.put(
            f"/api/messages/{human_msg_id}",
            json={"content": "New content with different tokens."}
        )

        assert response.status_code == 200
        mock_llm_service.count_tokens.assert_called_once_with(
            "New content with different tokens."
        )
        assert response.json()["message"]["token_count"] == 10

    @pytest.mark.asyncio
    async def test_update_assistant_message_fails(
        self, async_client, create_conversation_with_messages
    ):
        """Test that updating an assistant message fails."""
        assistant_msg_id = create_conversation_with_messages["messages"][1]["id"]

        response = await async_client.put(
            f"/api/messages/{assistant_msg_id}",
            json={"content": "Trying to edit assistant response."}
        )

        assert response.status_code == 400
        assert "human" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_update_nonexistent_message(self, async_client):
        """Test updating a non-existent message."""
        fake_id = str(uuid.uuid4())

        response = await async_client.put(
            f"/api/messages/{fake_id}",
            json={"content": "New content."}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_update_message_invalidates_session(
        self, async_client, create_conversation_with_messages, mock_session_manager
    ):
        """Test that updating a message invalidates the session cache."""
        human_msg_id = create_conversation_with_messages["messages"][0]["id"]
        conv_id = create_conversation_with_messages["conversation_id"]

        response = await async_client.put(
            f"/api/messages/{human_msg_id}",
            json={"content": "Updated content."}
        )

        assert response.status_code == 200
        mock_session_manager.close_session.assert_called_once_with(conv_id)

    @pytest.mark.asyncio
    async def test_update_message_updates_pinecone(
        self, async_client, create_conversation_with_messages, mock_memory_service
    ):
        """Test that updating a message updates Pinecone embeddings."""
        human_msg_id = create_conversation_with_messages["messages"][0]["id"]
        assistant_msg_id = create_conversation_with_messages["messages"][1]["id"]

        response = await async_client.put(
            f"/api/messages/{human_msg_id}",
            json={"content": "Updated content."}
        )

        assert response.status_code == 200

        # Should delete old embedding and store new one
        assert mock_memory_service.delete_memory.call_count >= 1
        mock_memory_service.store_memory.assert_called_once()


class TestDeleteMessage:
    """Tests for deleting messages."""

    @pytest.mark.asyncio
    async def test_delete_human_message_deletes_response(
        self, async_client, create_conversation_with_messages
    ):
        """Test that deleting a human message also deletes the subsequent response."""
        human_msg_id = create_conversation_with_messages["messages"][0]["id"]
        assistant_msg_id = create_conversation_with_messages["messages"][1]["id"]

        response = await async_client.delete(f"/api/messages/{human_msg_id}")

        assert response.status_code == 200
        data = response.json()
        assert human_msg_id in data["deleted_message_ids"]
        assert assistant_msg_id in data["deleted_message_ids"]
        assert len(data["deleted_message_ids"]) == 2

    @pytest.mark.asyncio
    async def test_delete_assistant_message_only(
        self, async_client, create_conversation_with_messages
    ):
        """Test that deleting an assistant message only deletes that message."""
        assistant_msg_id = create_conversation_with_messages["messages"][1]["id"]

        response = await async_client.delete(f"/api/messages/{assistant_msg_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted_message_ids"] == [assistant_msg_id]

    @pytest.mark.asyncio
    async def test_delete_nonexistent_message(self, async_client):
        """Test deleting a non-existent message."""
        fake_id = str(uuid.uuid4())

        response = await async_client.delete(f"/api/messages/{fake_id}")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_delete_message_invalidates_session(
        self, async_client, create_conversation_with_messages, mock_session_manager
    ):
        """Test that deleting a message invalidates the session cache."""
        human_msg_id = create_conversation_with_messages["messages"][0]["id"]
        conv_id = create_conversation_with_messages["conversation_id"]

        response = await async_client.delete(f"/api/messages/{human_msg_id}")

        assert response.status_code == 200
        mock_session_manager.close_session.assert_called_once_with(conv_id)

    @pytest.mark.asyncio
    async def test_delete_message_removes_from_pinecone(
        self, async_client, create_conversation_with_messages, mock_memory_service
    ):
        """Test that deleting a message removes embeddings from Pinecone."""
        human_msg_id = create_conversation_with_messages["messages"][0]["id"]

        response = await async_client.delete(f"/api/messages/{human_msg_id}")

        assert response.status_code == 200
        # Should delete embeddings for both human and assistant messages
        assert mock_memory_service.delete_memory.call_count >= 2


class TestDeleteLastMessageOnly:
    """Tests for deleting when there's only one message pair."""

    @pytest.fixture
    async def single_exchange(self, test_engine):
        """Create a conversation with just one exchange."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        created_data = {"conversation_id": None, "human_id": None, "assistant_id": None}

        async with async_session() as session:
            conv_id = str(uuid.uuid4())
            conversation = Conversation(
                id=conv_id,
                title="Single Exchange",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.flush()
            created_data["conversation_id"] = conv_id

            now = datetime.utcnow()

            human_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=MessageRole.HUMAN,
                content="Only question.",
                created_at=now - timedelta(seconds=5),
            )
            session.add(human_msg)
            created_data["human_id"] = human_msg.id

            assistant_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=MessageRole.ASSISTANT,
                content="Only response.",
                created_at=now,
            )
            session.add(assistant_msg)
            created_data["assistant_id"] = assistant_msg.id

            await session.commit()

        return created_data

    @pytest.mark.asyncio
    async def test_delete_only_human_message(self, async_client, single_exchange):
        """Test deleting the only human message in a conversation."""
        response = await async_client.delete(
            f"/api/messages/{single_exchange['human_id']}"
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["deleted_message_ids"]) == 2


class TestDeleteMessageWithoutResponse:
    """Tests for deleting a human message that doesn't have a response yet."""

    @pytest.fixture
    async def message_without_response(self, test_engine):
        """Create a conversation with a human message but no response."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        created_data = {"conversation_id": None, "human_id": None}

        async with async_session() as session:
            conv_id = str(uuid.uuid4())
            conversation = Conversation(
                id=conv_id,
                title="No Response Yet",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.flush()
            created_data["conversation_id"] = conv_id

            human_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=MessageRole.HUMAN,
                content="Waiting for response...",
            )
            session.add(human_msg)
            created_data["human_id"] = human_msg.id

            await session.commit()

        return created_data

    @pytest.mark.asyncio
    async def test_delete_human_without_response(
        self, async_client, message_without_response
    ):
        """Test deleting a human message that has no response yet."""
        response = await async_client.delete(
            f"/api/messages/{message_without_response['human_id']}"
        )

        assert response.status_code == 200
        data = response.json()
        # Only the human message should be deleted
        assert len(data["deleted_message_ids"]) == 1
        assert data["deleted_message_ids"][0] == message_without_response["human_id"]


class TestUpdateMessageWithoutResponse:
    """Tests for updating a human message that doesn't have a response."""

    @pytest.fixture
    async def pending_message(self, test_engine):
        """Create a conversation with a human message but no response."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        created_data = {"conversation_id": None, "human_id": None}

        async with async_session() as session:
            conv_id = str(uuid.uuid4())
            conversation = Conversation(
                id=conv_id,
                title="Pending Response",
                entity_id="test-entity",
            )
            session.add(conversation)
            await session.flush()
            created_data["conversation_id"] = conv_id

            human_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=MessageRole.HUMAN,
                content="Original message.",
            )
            session.add(human_msg)
            created_data["human_id"] = human_msg.id

            await session.commit()

        return created_data

    @pytest.mark.asyncio
    async def test_update_human_without_response(self, async_client, pending_message):
        """Test updating a human message that has no response yet."""
        response = await async_client.put(
            f"/api/messages/{pending_message['human_id']}",
            json={"content": "Updated message."}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message"]["content"] == "Updated message."
        # No assistant message to delete
        assert data["deleted_assistant_message_id"] is None
