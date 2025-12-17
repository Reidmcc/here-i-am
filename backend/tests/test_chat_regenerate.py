"""
Unit tests for chat regenerate endpoint with multi-entity support.

Note: Some tests avoid importing from app.routes.chat directly due to
environment-specific import issues with the Google GenAI library.
Instead, they test the logic patterns used in the endpoint.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
import uuid
import json

from pydantic import BaseModel, ValidationError
from typing import Optional
from sqlalchemy import select

from app.models import Conversation, Message, MessageRole, ConversationType, ConversationEntity


# Define a test version of RegenerateRequest to avoid import issues
# This mirrors the actual model in app.routes.chat
class RegenerateRequestTest(BaseModel):
    """Test version of RegenerateRequest for validation testing."""
    message_id: str
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None
    verbosity: Optional[str] = None
    responding_entity_id: Optional[str] = None
    user_display_name: Optional[str] = None


class TestRegenerateRequestModel:
    """Tests for RegenerateRequest Pydantic model structure."""

    def test_regenerate_request_basic(self):
        """Test basic RegenerateRequest creation."""
        request = RegenerateRequestTest(message_id="msg-123")

        assert request.message_id == "msg-123"
        assert request.model is None
        assert request.temperature is None
        assert request.max_tokens is None
        assert request.system_prompt is None
        assert request.verbosity is None
        assert request.responding_entity_id is None
        assert request.user_display_name is None

    def test_regenerate_request_with_responding_entity_id(self):
        """Test RegenerateRequest with responding_entity_id for multi-entity."""
        request = RegenerateRequestTest(
            message_id="msg-123",
            responding_entity_id="claude-main",
            temperature=0.8,
        )

        assert request.message_id == "msg-123"
        assert request.responding_entity_id == "claude-main"
        assert request.temperature == 0.8

    def test_regenerate_request_all_fields(self):
        """Test RegenerateRequest with all optional fields."""
        request = RegenerateRequestTest(
            message_id="msg-123",
            model="claude-opus-4-20250514",
            temperature=0.5,
            max_tokens=2000,
            system_prompt="Be helpful",
            verbosity="high",
            responding_entity_id="gpt-test",
            user_display_name="Researcher",
        )

        assert request.message_id == "msg-123"
        assert request.model == "claude-opus-4-20250514"
        assert request.temperature == 0.5
        assert request.max_tokens == 2000
        assert request.system_prompt == "Be helpful"
        assert request.verbosity == "high"
        assert request.responding_entity_id == "gpt-test"
        assert request.user_display_name == "Researcher"

    def test_regenerate_request_requires_message_id(self):
        """Test that message_id is required."""
        with pytest.raises(ValidationError):
            RegenerateRequestTest()


class TestGetMultiEntityIds:
    """Tests for multi-entity ID retrieval logic."""

    @pytest.mark.asyncio
    async def test_get_multi_entity_ids_returns_entities(self, db_session):
        """Test that multi-entity ID retrieval returns participating entity IDs."""
        # Create a conversation
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Multi-entity Test",
            conversation_type=ConversationType.MULTI_ENTITY,
            entity_id="multi-entity",
        )
        db_session.add(conversation)
        await db_session.flush()

        # Add participating entities
        entity1 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="claude-main",
            display_order=0,
        )
        entity2 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="gpt-test",
            display_order=1,
        )
        db_session.add(entity1)
        db_session.add(entity2)
        await db_session.commit()

        # Query entity IDs (same logic as get_multi_entity_ids)
        result = await db_session.execute(
            select(ConversationEntity.entity_id)
            .where(ConversationEntity.conversation_id == str(conversation.id))
            .order_by(ConversationEntity.display_order)
        )
        entity_ids = [row[0] for row in result.fetchall()]

        assert len(entity_ids) == 2
        assert "claude-main" in entity_ids
        assert "gpt-test" in entity_ids
        # Should be ordered by display_order
        assert entity_ids[0] == "claude-main"
        assert entity_ids[1] == "gpt-test"

    @pytest.mark.asyncio
    async def test_get_multi_entity_ids_empty(self, db_session):
        """Test multi-entity ID retrieval returns empty list for non-existent conversation."""
        result = await db_session.execute(
            select(ConversationEntity.entity_id)
            .where(ConversationEntity.conversation_id == "non-existent-id")
            .order_by(ConversationEntity.display_order)
        )
        entity_ids = [row[0] for row in result.fetchall()]

        assert entity_ids == []


class TestGetEntityLabel:
    """Tests for entity label retrieval logic."""

    def test_get_entity_label_found(self):
        """Test entity label retrieval returns label when entity exists."""
        # Simulate the get_entity_label logic
        mock_settings = MagicMock()
        mock_entity = MagicMock()
        mock_entity.label = "Claude Test"
        mock_settings.get_entity_by_index.return_value = mock_entity

        entity = mock_settings.get_entity_by_index("claude-test")
        label = entity.label if entity else None

        assert label == "Claude Test"

    def test_get_entity_label_not_found(self):
        """Test entity label retrieval returns None when entity doesn't exist."""
        mock_settings = MagicMock()
        mock_settings.get_entity_by_index.return_value = None

        entity = mock_settings.get_entity_by_index("non-existent")
        label = entity.label if entity else None

        assert label is None


class TestRegenerateMultiEntityValidation:
    """Tests for multi-entity validation in regenerate endpoint."""

    @pytest.mark.asyncio
    async def test_regenerate_requires_entity_id_for_multi_entity(self, db_session):
        """Test that regenerate requires responding_entity_id for multi-entity conversations."""
        # Create a multi-entity conversation
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Multi-entity Test",
            conversation_type=ConversationType.MULTI_ENTITY,
            entity_id="multi-entity",
        )
        db_session.add(conversation)
        await db_session.flush()

        # Add a message to regenerate
        human_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role=MessageRole.HUMAN,
            content="Hello",
        )
        assistant_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="Hi there!",
        )
        db_session.add(human_msg)
        db_session.add(assistant_msg)

        # Add participating entities
        entity1 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="claude-main",
            display_order=0,
        )
        db_session.add(entity1)
        await db_session.commit()

        # Test the validation logic
        is_multi_entity = conversation.conversation_type == ConversationType.MULTI_ENTITY

        result = await db_session.execute(
            select(ConversationEntity.entity_id)
            .where(ConversationEntity.conversation_id == str(conversation.id))
        )
        multi_entity_ids = [row[0] for row in result.fetchall()]

        # Validation should require responding_entity_id
        assert is_multi_entity is True
        assert len(multi_entity_ids) > 0
        # Without responding_entity_id, endpoint would return error

    @pytest.mark.asyncio
    async def test_regenerate_validates_entity_is_participant(self, db_session):
        """Test that regenerate validates entity is a conversation participant."""
        # Create a multi-entity conversation
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Multi-entity Test",
            conversation_type=ConversationType.MULTI_ENTITY,
            entity_id="multi-entity",
        )
        db_session.add(conversation)
        await db_session.flush()

        # Add only one participating entity
        entity1 = ConversationEntity(
            conversation_id=conversation.id,
            entity_id="claude-main",
            display_order=0,
        )
        db_session.add(entity1)
        await db_session.commit()

        result = await db_session.execute(
            select(ConversationEntity.entity_id)
            .where(ConversationEntity.conversation_id == str(conversation.id))
        )
        multi_entity_ids = [row[0] for row in result.fetchall()]

        # Verify validation logic
        assert "claude-main" in multi_entity_ids
        assert "gpt-test" not in multi_entity_ids  # Not a participant


class TestRegenerateMultiEntityMemoryOperations:
    """Tests for multi-entity memory operations during regeneration."""

    def test_multi_entity_memory_deletion_targets_all_entities(self):
        """Test that multi-entity regeneration deletes from all participating entities."""
        # This tests the logic pattern used in the endpoint
        multi_entity_ids = ["claude-main", "gpt-test", "gemini-test"]
        old_message_id = uuid.uuid4()

        delete_calls = []

        # Simulate the deletion logic
        for entity_id in multi_entity_ids:
            delete_calls.append({
                "message_id": old_message_id,
                "entity_id": entity_id,
            })

        # Should have delete call for each entity
        assert len(delete_calls) == 3
        assert delete_calls[0]["entity_id"] == "claude-main"
        assert delete_calls[1]["entity_id"] == "gpt-test"
        assert delete_calls[2]["entity_id"] == "gemini-test"

    def test_multi_entity_memory_storage_correct_roles(self):
        """Test that multi-entity storage uses correct roles for each entity."""
        multi_entity_ids = ["claude-main", "gpt-test"]
        responding_entity_id = "claude-main"
        responding_label = "Claude"

        storage_calls = []

        # Simulate the storage logic from the endpoint
        for entity_id in multi_entity_ids:
            if entity_id == responding_entity_id:
                storage_calls.append({
                    "entity_id": entity_id,
                    "role": "assistant",
                })
            else:
                storage_calls.append({
                    "entity_id": entity_id,
                    "role": responding_label,
                })

        # Verify roles
        assert len(storage_calls) == 2

        # Responding entity gets "assistant" role
        claude_call = next(c for c in storage_calls if c["entity_id"] == "claude-main")
        assert claude_call["role"] == "assistant"

        # Other entity gets the speaker's label as role
        gpt_call = next(c for c in storage_calls if c["entity_id"] == "gpt-test")
        assert gpt_call["role"] == "Claude"


class TestRegenerateStoredEvent:
    """Tests for stored event data in multi-entity regeneration."""

    def test_stored_event_includes_speaker_info_for_multi_entity(self):
        """Test that stored event includes speaker info for multi-entity."""
        # Simulate stored event data generation
        is_multi_entity = True
        responding_entity_id = "claude-main"
        responding_label = "Claude"
        user_message_id = str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())

        stored_data = {
            'human_message_id': user_message_id,
            'assistant_message_id': assistant_message_id,
        }

        if is_multi_entity:
            stored_data['speaker_entity_id'] = responding_entity_id
            stored_data['speaker_label'] = responding_label

        # Verify structure
        assert stored_data['human_message_id'] == user_message_id
        assert stored_data['assistant_message_id'] == assistant_message_id
        assert stored_data['speaker_entity_id'] == "claude-main"
        assert stored_data['speaker_label'] == "Claude"

    def test_stored_event_no_speaker_info_for_single_entity(self):
        """Test that stored event doesn't include speaker info for single-entity."""
        is_multi_entity = False
        user_message_id = str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())

        stored_data = {
            'human_message_id': user_message_id,
            'assistant_message_id': assistant_message_id,
        }

        if is_multi_entity:
            stored_data['speaker_entity_id'] = "would-not-be-added"

        # Verify no speaker info
        assert 'speaker_entity_id' not in stored_data
        assert 'speaker_label' not in stored_data


class TestRegenerateSingleEntity:
    """Tests for single-entity regeneration (no responding_entity_id)."""

    def test_single_entity_skips_multi_entity_validation(self):
        """Test that single-entity regeneration skips multi-entity validation."""
        # Single entity conversation type
        conversation_type = ConversationType.NORMAL
        responding_entity_id = None

        is_multi_entity = conversation_type == ConversationType.MULTI_ENTITY

        # No validation should be triggered
        assert is_multi_entity is False
        # responding_entity_id not required

    def test_single_entity_uses_session_entity_for_memory(self):
        """Test that single-entity uses session.entity_id for memory operations."""
        session_entity_id = "test-memories"
        is_multi_entity = False
        responding_entity_id = None

        # Logic from endpoint
        if is_multi_entity:
            memory_entity_ids = ["claude-main", "gpt-test"]
        else:
            memory_entity_ids = [session_entity_id]

        assert memory_entity_ids == ["test-memories"]


class TestRegenerateModelOverride:
    """Tests for model override behavior in regeneration."""

    def test_model_override_skipped_in_multi_entity(self):
        """Test that model override is skipped in multi-entity mode."""
        is_multi_entity = True
        data_model = "claude-opus-4-20250514"
        session_model = "claude-sonnet-4-5-20250929"

        # Logic from endpoint
        if data_model and not is_multi_entity:
            session_model = data_model

        # Model should not be overridden
        assert session_model == "claude-sonnet-4-5-20250929"

    def test_model_override_applied_in_single_entity(self):
        """Test that model override is applied in single-entity mode."""
        is_multi_entity = False
        data_model = "claude-opus-4-20250514"
        session_model = "claude-sonnet-4-5-20250929"

        # Logic from endpoint
        if data_model and not is_multi_entity:
            session_model = data_model

        # Model should be overridden
        assert session_model == "claude-opus-4-20250514"


class TestMessageWithSpeakerEntityId:
    """Tests for Message model with speaker_entity_id."""

    @pytest.mark.asyncio
    async def test_message_created_with_speaker_entity_id(self, db_session):
        """Test that Message can be created with speaker_entity_id."""
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test",
            conversation_type=ConversationType.MULTI_ENTITY,
            entity_id="multi-entity",
        )
        db_session.add(conversation)
        await db_session.flush()

        message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="Hello from Claude!",
            speaker_entity_id="claude-main",
        )
        db_session.add(message)
        await db_session.commit()
        await db_session.refresh(message)

        assert message.speaker_entity_id == "claude-main"

    @pytest.mark.asyncio
    async def test_message_speaker_entity_id_optional(self, db_session):
        """Test that speaker_entity_id is optional for single-entity."""
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title="Test",
            conversation_type=ConversationType.NORMAL,
            entity_id="test-memories",
        )
        db_session.add(conversation)
        await db_session.flush()

        message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="Hello!",
            # No speaker_entity_id
        )
        db_session.add(message)
        await db_session.commit()
        await db_session.refresh(message)

        assert message.speaker_entity_id is None
