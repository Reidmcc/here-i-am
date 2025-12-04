"""
Unit tests for SQLAlchemy models.
"""
import pytest
import uuid
from datetime import datetime

from sqlalchemy import select

from app.models import (
    Conversation,
    Message,
    ConversationMemoryLink,
    ConversationType,
    MessageRole,
)


class TestConversationModel:
    """Tests for Conversation model."""

    async def test_create_conversation(self, db_session):
        """Test creating a new conversation."""
        conversation_id = str(uuid.uuid4())
        conversation = Conversation(
            id=conversation_id,
            title="Test Conversation",
            conversation_type=ConversationType.NORMAL,
            model_used="claude-sonnet-4-5-latest",
        )
        db_session.add(conversation)
        await db_session.commit()

        result = await db_session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        saved = result.scalar_one()

        assert saved.id == conversation_id
        assert saved.title == "Test Conversation"
        assert saved.conversation_type == ConversationType.NORMAL
        assert saved.model_used == "claude-sonnet-4-5-latest"
        assert saved.created_at is not None

    async def test_conversation_default_values(self, db_session):
        """Test conversation default values."""
        conversation = Conversation()
        db_session.add(conversation)
        await db_session.commit()

        assert conversation.id is not None
        assert conversation.conversation_type == ConversationType.NORMAL
        assert conversation.model_used == "claude-sonnet-4-5-latest"
        assert conversation.created_at is not None
        assert conversation.title is None
        assert conversation.system_prompt_used is None
        assert conversation.entity_id is None

    async def test_conversation_with_entity_id(self, db_session):
        """Test conversation with entity_id set."""
        conversation = Conversation(
            title="Entity Test",
            entity_id="claude-main",
        )
        db_session.add(conversation)
        await db_session.commit()

        assert conversation.entity_id == "claude-main"

    async def test_conversation_reflection_type(self, db_session):
        """Test conversation with reflection type."""
        conversation = Conversation(
            conversation_type=ConversationType.REFLECTION,
        )
        db_session.add(conversation)
        await db_session.commit()

        assert conversation.conversation_type == ConversationType.REFLECTION

    async def test_conversation_with_tags(self, db_session):
        """Test conversation with JSON tags."""
        conversation = Conversation(
            tags={"topic": "testing", "priority": "high"},
        )
        db_session.add(conversation)
        await db_session.commit()
        await db_session.refresh(conversation)

        assert conversation.tags == {"topic": "testing", "priority": "high"}

    async def test_conversation_with_notes(self, db_session):
        """Test conversation with notes."""
        conversation = Conversation(
            notes="This is a test note for the conversation.",
        )
        db_session.add(conversation)
        await db_session.commit()

        assert conversation.notes == "This is a test note for the conversation."


class TestMessageModel:
    """Tests for Message model."""

    async def test_create_message(self, db_session, sample_conversation):
        """Test creating a new message."""
        message_id = str(uuid.uuid4())
        message = Message(
            id=message_id,
            conversation_id=sample_conversation.id,
            role=MessageRole.HUMAN,
            content="Hello, world!",
            token_count=3,
        )
        db_session.add(message)
        await db_session.commit()

        result = await db_session.execute(
            select(Message).where(Message.id == message_id)
        )
        saved = result.scalar_one()

        assert saved.id == message_id
        assert saved.conversation_id == sample_conversation.id
        assert saved.role == MessageRole.HUMAN
        assert saved.content == "Hello, world!"
        assert saved.token_count == 3

    async def test_message_roles(self, db_session, sample_conversation):
        """Test all message roles."""
        for role in [MessageRole.HUMAN, MessageRole.ASSISTANT, MessageRole.SYSTEM]:
            message = Message(
                conversation_id=sample_conversation.id,
                role=role,
                content=f"Message with role {role.value}",
            )
            db_session.add(message)
            await db_session.commit()
            await db_session.refresh(message)

            assert message.role == role

    async def test_message_memory_tracking_defaults(self, db_session, sample_conversation):
        """Test message memory tracking default values."""
        message = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.ASSISTANT,
            content="Test message",
        )
        db_session.add(message)
        await db_session.commit()

        assert message.times_retrieved == 0
        assert message.last_retrieved_at is None

    async def test_message_memory_tracking_update(self, db_session, sample_conversation):
        """Test updating message memory tracking."""
        message = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.ASSISTANT,
            content="Test message",
        )
        db_session.add(message)
        await db_session.commit()

        # Update tracking
        message.times_retrieved = 5
        message.last_retrieved_at = datetime.utcnow()
        await db_session.commit()
        await db_session.refresh(message)

        assert message.times_retrieved == 5
        assert message.last_retrieved_at is not None

    async def test_message_relationship_to_conversation(self, db_session, sample_conversation):
        """Test message relationship to conversation."""
        message = Message(
            conversation_id=sample_conversation.id,
            role=MessageRole.HUMAN,
            content="Test relationship",
        )
        db_session.add(message)
        await db_session.commit()
        await db_session.refresh(message)

        # Access the relationship
        assert message.conversation is not None
        assert message.conversation.id == sample_conversation.id


class TestConversationMemoryLinkModel:
    """Tests for ConversationMemoryLink model."""

    async def test_create_memory_link(self, db_session, sample_conversation, sample_messages):
        """Test creating a memory link."""
        message = sample_messages[0]
        link = ConversationMemoryLink(
            conversation_id=sample_conversation.id,
            message_id=message.id,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        assert link.id is not None
        assert link.conversation_id == sample_conversation.id
        assert link.message_id == message.id
        assert link.retrieved_at is not None

    async def test_multiple_memory_links_same_conversation(self, db_session, sample_conversation, sample_messages):
        """Test multiple memory links for the same conversation."""
        links = []
        for msg in sample_messages:
            link = ConversationMemoryLink(
                conversation_id=sample_conversation.id,
                message_id=msg.id,
            )
            db_session.add(link)
            links.append(link)
        await db_session.commit()

        result = await db_session.execute(
            select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == sample_conversation.id
            )
        )
        saved_links = result.scalars().all()

        assert len(saved_links) == 2

    async def test_memory_link_relationships(self, db_session, sample_conversation, sample_messages):
        """Test memory link relationships."""
        message = sample_messages[0]
        link = ConversationMemoryLink(
            conversation_id=sample_conversation.id,
            message_id=message.id,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        # Access relationships
        assert link.conversation is not None
        assert link.message is not None
        assert link.conversation.id == sample_conversation.id
        assert link.message.id == message.id


class TestCascadeDeletes:
    """Tests for cascade delete behavior."""

    async def test_delete_conversation_cascades_to_messages(self, db_session, sample_conversation, sample_messages):
        """Test that deleting a conversation deletes its messages."""
        conversation_id = sample_conversation.id
        message_ids = [m.id for m in sample_messages]

        # Verify messages exist
        for msg_id in message_ids:
            result = await db_session.execute(
                select(Message).where(Message.id == msg_id)
            )
            assert result.scalar_one_or_none() is not None

        # Delete conversation
        await db_session.delete(sample_conversation)
        await db_session.commit()

        # Verify messages are deleted
        for msg_id in message_ids:
            result = await db_session.execute(
                select(Message).where(Message.id == msg_id)
            )
            assert result.scalar_one_or_none() is None

    async def test_delete_conversation_cascades_to_memory_links(
        self, db_session, sample_conversation, sample_messages
    ):
        """Test that deleting a conversation deletes its memory links."""
        # Create memory links
        for msg in sample_messages:
            link = ConversationMemoryLink(
                conversation_id=sample_conversation.id,
                message_id=msg.id,
            )
            db_session.add(link)
        await db_session.commit()

        # Verify links exist
        result = await db_session.execute(
            select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == sample_conversation.id
            )
        )
        assert len(result.scalars().all()) == 2

        # Delete conversation
        await db_session.delete(sample_conversation)
        await db_session.commit()

        # Verify links are deleted
        result = await db_session.execute(
            select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == sample_conversation.id
            )
        )
        assert len(result.scalars().all()) == 0
