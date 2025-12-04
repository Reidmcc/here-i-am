"""
Pytest configuration and fixtures for Here I Am tests.
"""
import pytest
import asyncio
from datetime import datetime
from typing import AsyncGenerator, Dict, Any, List
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Conversation, Message, ConversationMemoryLink, MessageRole, ConversationType
from app.config import Settings, EntityConfig


# Test database URL - in-memory SQLite
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


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
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
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
def test_settings():
    """Create test settings with mock API keys."""
    return Settings(
        anthropic_api_key="test-anthropic-key",
        openai_api_key="test-openai-key",
        pinecone_api_key="test-pinecone-key",
        pinecone_index_name="test-memories",
        here_i_am_database_url=TEST_DATABASE_URL,
        debug=False,
        retrieval_top_k=5,
        similarity_threshold=0.7,
        default_model="claude-sonnet-4-20250514",
        default_openai_model="gpt-4o",
        default_temperature=1.0,
        default_max_tokens=4096,
    )


@pytest.fixture
def test_settings_multi_entity():
    """Create test settings with multiple entities."""
    return Settings(
        anthropic_api_key="test-anthropic-key",
        openai_api_key="test-openai-key",
        pinecone_api_key="test-pinecone-key",
        pinecone_indexes='[{"index_name": "claude-test", "label": "Claude Test", "description": "Test entity", "model_provider": "anthropic"}, {"index_name": "gpt-test", "label": "GPT Test", "description": "OpenAI test", "model_provider": "openai", "default_model": "gpt-4o"}]',
        here_i_am_database_url=TEST_DATABASE_URL,
    )


@pytest.fixture
def test_settings_no_pinecone():
    """Create test settings without Pinecone configured."""
    return Settings(
        anthropic_api_key="test-anthropic-key",
        openai_api_key="",
        pinecone_api_key="",
        here_i_am_database_url=TEST_DATABASE_URL,
    )


@pytest.fixture
def sample_conversation_id():
    """Generate a sample conversation ID."""
    return str(uuid.uuid4())


@pytest.fixture
def sample_message_id():
    """Generate a sample message ID."""
    return str(uuid.uuid4())


@pytest.fixture
async def sample_conversation(db_session, sample_conversation_id) -> Conversation:
    """Create a sample conversation in the database."""
    conversation = Conversation(
        id=sample_conversation_id,
        title="Test Conversation",
        conversation_type=ConversationType.NORMAL,
        model_used="claude-sonnet-4-20250514",
        entity_id="test-memories",
    )
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation)
    return conversation


@pytest.fixture
async def sample_messages(db_session, sample_conversation) -> List[Message]:
    """Create sample messages in the database."""
    messages = [
        Message(
            id=str(uuid.uuid4()),
            conversation_id=sample_conversation.id,
            role=MessageRole.HUMAN,
            content="Hello, how are you?",
            token_count=5,
        ),
        Message(
            id=str(uuid.uuid4()),
            conversation_id=sample_conversation.id,
            role=MessageRole.ASSISTANT,
            content="I'm doing well, thank you for asking!",
            token_count=10,
        ),
    ]
    for msg in messages:
        db_session.add(msg)
    await db_session.commit()
    for msg in messages:
        await db_session.refresh(msg)
    return messages


@pytest.fixture
def mock_anthropic_client():
    """Create a mock Anthropic client."""
    mock_client = MagicMock()

    # Mock messages.create response
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="This is a test response from Claude.")]
    mock_response.model = "claude-sonnet-4-20250514"
    mock_response.usage.input_tokens = 100
    mock_response.usage.output_tokens = 50
    mock_response.stop_reason = "end_turn"

    mock_client.messages = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    # Mock embeddings.create response
    mock_embedding_response = MagicMock()
    mock_embedding_response.data = [MagicMock(embedding=[0.1] * 1024)]
    mock_client.embeddings = MagicMock()
    mock_client.embeddings.create = AsyncMock(return_value=mock_embedding_response)

    return mock_client


@pytest.fixture
def mock_openai_client():
    """Create a mock OpenAI client."""
    mock_client = MagicMock()

    # Mock chat.completions.create response
    mock_choice = MagicMock()
    mock_choice.message.content = "This is a test response from GPT."
    mock_choice.finish_reason = "stop"

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.model = "gpt-4o"
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50

    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    return mock_client


@pytest.fixture
def mock_pinecone_index():
    """Create a mock Pinecone index."""
    mock_index = MagicMock()

    # Mock query response
    mock_match = MagicMock()
    mock_match.id = "test-memory-id"
    mock_match.score = 0.9
    mock_match.metadata = {
        "conversation_id": "old-conversation-id",
        "created_at": "2024-01-01T12:00:00",
        "role": "assistant",
        "content_preview": "This is a test memory...",
        "times_retrieved": 5,
    }

    mock_query_result = MagicMock()
    mock_query_result.matches = [mock_match]
    mock_index.query = MagicMock(return_value=mock_query_result)

    # Mock fetch response
    mock_vector = MagicMock()
    mock_vector.metadata = {"times_retrieved": 5}
    mock_fetch_result = MagicMock()
    mock_fetch_result.vectors = {"test-memory-id": mock_vector}
    mock_index.fetch = MagicMock(return_value=mock_fetch_result)

    # Mock upsert and update
    mock_index.upsert = MagicMock()
    mock_index.update = MagicMock()
    mock_index.delete = MagicMock()

    return mock_index


@pytest.fixture
def sample_memories():
    """Create sample memory data for injection."""
    return [
        {
            "id": "mem-1",
            "content": "I remember you mentioned enjoying programming.",
            "created_at": "2024-01-01",
            "times_retrieved": 3,
            "role": "assistant",
        },
        {
            "id": "mem-2",
            "content": "We discussed AI ethics last time.",
            "created_at": "2024-01-02",
            "times_retrieved": 1,
            "role": "human",
        },
    ]


@pytest.fixture
def sample_conversation_context():
    """Create sample conversation context."""
    return [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi there!"},
    ]


@pytest.fixture
def sample_api_messages():
    """Create sample API messages for LLM calls."""
    return [
        {"role": "user", "content": "What is Python?"},
    ]
