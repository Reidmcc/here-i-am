"""
Tests for conversation import functionality.

Tests cover:
- OpenAI export format parsing
- Anthropic export format parsing
- Format auto-detection
- Preview endpoint
- Import endpoint with selective import
- Deduplication using message IDs
- Import to history vs memory-only
"""
import pytest
import json
import uuid
from unittest.mock import patch, MagicMock, AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, MessageRole, ConversationType
from app.routes.conversations import (
    _parse_openai_export,
    _parse_anthropic_export,
    _detect_and_parse_export,
)


# Sample export data fixtures

@pytest.fixture
def sample_openai_export():
    """Sample OpenAI ChatGPT export format."""
    return [
        {
            "id": "conv-openai-1",
            "title": "Test OpenAI Conversation",
            "create_time": 1700000000,
            "mapping": {
                "node-1": {
                    "id": "node-1",
                    "message": {
                        "id": "msg-openai-1",
                        "author": {"role": "user"},
                        "content": {"parts": ["Hello from OpenAI"]},
                        "create_time": 1700000001,
                    },
                },
                "node-2": {
                    "id": "node-2",
                    "message": {
                        "id": "msg-openai-2",
                        "author": {"role": "assistant"},
                        "content": {"parts": ["Hi! How can I help you?"]},
                        "create_time": 1700000002,
                    },
                },
                "node-3": {
                    "id": "node-3",
                    "message": {
                        "id": "msg-openai-3",
                        "author": {"role": "system"},
                        "content": {"parts": ["You are a helpful assistant"]},
                        "create_time": 1700000000,
                    },
                },
            },
        },
        {
            "id": "conv-openai-2",
            "title": "Second Conversation",
            "mapping": {
                "node-a": {
                    "message": {
                        "id": "msg-openai-4",
                        "author": {"role": "user"},
                        "content": {"parts": ["Another question"]},
                        "create_time": 1700000010,
                    },
                },
            },
        },
    ]


@pytest.fixture
def sample_anthropic_export():
    """Sample Anthropic Claude export format."""
    return [
        {
            "uuid": "conv-anthropic-1",
            "name": "Test Anthropic Conversation",
            "created_at": "2024-01-01T12:00:00Z",
            "chat_messages": [
                {
                    "uuid": "msg-anthropic-1",
                    "sender": "human",
                    "text": "Hello from Anthropic",
                },
                {
                    "uuid": "msg-anthropic-2",
                    "sender": "assistant",
                    "text": "Hello! How may I assist you?",
                },
            ],
        },
        {
            "uuid": "conv-anthropic-2",
            "name": "Another Chat",
            "chat_messages": [
                {
                    "uuid": "msg-anthropic-3",
                    "sender": "human",
                    "text": "Quick question",
                },
            ],
        },
    ]


class TestOpenAIParser:
    """Tests for OpenAI export format parsing."""

    def test_parse_basic_conversation(self, sample_openai_export):
        """Test parsing a basic OpenAI conversation."""
        result = _parse_openai_export(sample_openai_export, include_ids=False)

        assert len(result) == 2
        assert result[0]["title"] == "Test OpenAI Conversation"
        assert result[0]["message_count"] == 2

        # Check messages are sorted by timestamp
        messages = result[0]["messages"]
        assert messages[0]["role"] == "human"
        assert messages[0]["content"] == "Hello from OpenAI"
        assert messages[1]["role"] == "assistant"

    def test_parse_with_ids(self, sample_openai_export):
        """Test parsing with message IDs for deduplication."""
        result = _parse_openai_export(sample_openai_export, include_ids=True)

        assert len(result) == 2
        assert result[0]["id"] == "conv-openai-1"
        assert result[0]["index"] == 0

        messages = result[0]["messages"]
        assert messages[0]["id"] == "msg-openai-1"
        assert messages[1]["id"] == "msg-openai-2"

    def test_skips_system_messages(self, sample_openai_export):
        """Test that system messages are skipped."""
        result = _parse_openai_export(sample_openai_export, include_ids=False)

        # First conversation has 3 nodes but only 2 user/assistant messages
        assert result[0]["message_count"] == 2

    def test_handles_empty_mapping(self):
        """Test handling of conversation with empty mapping."""
        data = [{"id": "test", "title": "Empty", "mapping": {}}]
        result = _parse_openai_export(data)
        assert len(result) == 0

    def test_handles_multipart_content(self):
        """Test handling of content with multiple parts."""
        data = [
            {
                "id": "test",
                "title": "Multipart",
                "mapping": {
                    "node-1": {
                        "message": {
                            "id": "msg-1",
                            "author": {"role": "user"},
                            "content": {"parts": ["Part 1 ", "Part 2"]},
                            "create_time": 1700000001,
                        },
                    },
                },
            }
        ]
        result = _parse_openai_export(data)
        assert result[0]["messages"][0]["content"] == "Part 1 Part 2"


class TestAnthropicParser:
    """Tests for Anthropic export format parsing."""

    def test_parse_basic_conversation(self, sample_anthropic_export):
        """Test parsing a basic Anthropic conversation."""
        result = _parse_anthropic_export(sample_anthropic_export, include_ids=False)

        assert len(result) == 2
        assert result[0]["title"] == "Test Anthropic Conversation"
        assert result[0]["message_count"] == 2

        messages = result[0]["messages"]
        assert messages[0]["role"] == "human"
        assert messages[0]["content"] == "Hello from Anthropic"
        assert messages[1]["role"] == "assistant"

    def test_parse_with_ids(self, sample_anthropic_export):
        """Test parsing with message IDs for deduplication."""
        result = _parse_anthropic_export(sample_anthropic_export, include_ids=True)

        assert result[0]["id"] == "conv-anthropic-1"
        assert result[0]["index"] == 0

        messages = result[0]["messages"]
        assert messages[0]["id"] == "msg-anthropic-1"
        assert messages[1]["id"] == "msg-anthropic-2"

    def test_handles_empty_messages(self):
        """Test handling of conversation with empty messages."""
        data = [{"uuid": "test", "name": "Empty", "chat_messages": []}]
        result = _parse_anthropic_export(data)
        assert len(result) == 0

    def test_skips_empty_text(self):
        """Test that messages with empty text are skipped."""
        data = [
            {
                "uuid": "test",
                "name": "Test",
                "chat_messages": [
                    {"uuid": "m1", "sender": "human", "text": "Hello"},
                    {"uuid": "m2", "sender": "assistant", "text": "  "},
                ],
            }
        ]
        result = _parse_anthropic_export(data)
        assert result[0]["message_count"] == 1


class TestFormatDetection:
    """Tests for export format auto-detection."""

    def test_detect_openai_format(self, sample_openai_export):
        """Test detection of OpenAI format."""
        content = json.dumps(sample_openai_export)
        conversations, source = _detect_and_parse_export(content)

        assert source == "openai"
        assert len(conversations) == 2

    def test_detect_anthropic_format(self, sample_anthropic_export):
        """Test detection of Anthropic format."""
        content = json.dumps(sample_anthropic_export)
        conversations, source = _detect_and_parse_export(content)

        assert source == "anthropic"
        assert len(conversations) == 2

    def test_source_hint_openai(self, sample_openai_export):
        """Test using source hint for OpenAI."""
        content = json.dumps(sample_openai_export)
        conversations, source = _detect_and_parse_export(content, source_hint="openai")

        assert source == "openai"

    def test_source_hint_anthropic(self, sample_anthropic_export):
        """Test using source hint for Anthropic."""
        content = json.dumps(sample_anthropic_export)
        conversations, source = _detect_and_parse_export(content, source_hint="anthropic")

        assert source == "anthropic"

    def test_invalid_json(self):
        """Test handling of invalid JSON."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            _detect_and_parse_export("not valid json")

    def test_not_array(self):
        """Test handling of non-array JSON."""
        with pytest.raises(ValueError, match="must contain a JSON array"):
            _detect_and_parse_export('{"not": "an array"}')

    def test_empty_array(self):
        """Test handling of empty array."""
        conversations, source = _detect_and_parse_export("[]")
        assert conversations == []
        assert source == "unknown"

    def test_unknown_format(self):
        """Test handling of unknown format."""
        content = json.dumps([{"unknown": "format"}])
        with pytest.raises(ValueError, match="Could not detect export format"):
            _detect_and_parse_export(content)


class TestImportConversationsIntegration:
    """Integration tests for import functionality with database."""

    @pytest.fixture
    def mock_memory_service(self):
        """Create a mock memory service."""
        with patch("app.services.memory_service") as mock:
            mock.is_configured.return_value = True
            mock.store_memory = AsyncMock(return_value=True)
            yield mock

    @pytest.fixture
    def mock_settings_with_entity(self):
        """Mock settings with a test entity configured."""
        mock_entity = MagicMock()
        mock_entity.index_name = "test-entity"
        mock_entity.label = "Test Entity"

        with patch("app.routes.conversations.settings") as mock:
            mock.get_entity_by_index.return_value = mock_entity
            yield mock

    async def test_import_creates_hidden_conversation(
        self, db_session, sample_anthropic_export, mock_memory_service, mock_settings_with_entity
    ):
        """Test that importing creates hidden (is_imported=True) conversation."""
        from app.routes.conversations import import_external_conversations, ExternalConversationImport

        data = ExternalConversationImport(
            content=json.dumps(sample_anthropic_export),
            entity_id="test-entity",
            source="anthropic",
        )

        result = await import_external_conversations(data, db_session)

        assert result["status"] == "imported"
        assert result["conversations_imported"] == 2

        # Check conversations are marked as imported
        query = select(Conversation).where(Conversation.is_imported == True)
        db_result = await db_session.execute(query)
        imported_convs = db_result.scalars().all()

        assert len(imported_convs) == 2
        assert all(c.is_imported for c in imported_convs)

    async def test_import_to_history_creates_visible_conversation(
        self, db_session, sample_anthropic_export, mock_memory_service, mock_settings_with_entity
    ):
        """Test that importing to history creates visible conversation."""
        from app.routes.conversations import import_external_conversations, ExternalConversationImport

        data = ExternalConversationImport(
            content=json.dumps(sample_anthropic_export),
            entity_id="test-entity",
            source="anthropic",
            selected_conversations=[
                {"index": 0, "import_as_memory": True, "import_to_history": True},
            ],
        )

        result = await import_external_conversations(data, db_session)

        assert result["conversations_to_history"] == 1

        # Check conversation is NOT marked as imported (visible in UI)
        query = select(Conversation).where(Conversation.is_imported == False)
        db_result = await db_session.execute(query)
        visible_convs = db_result.scalars().all()

        assert len(visible_convs) == 1
        assert visible_convs[0].title == "Test Anthropic Conversation"

    async def test_deduplication_by_message_id(
        self, db_session, sample_anthropic_export, mock_memory_service, mock_settings_with_entity
    ):
        """Test that messages with existing IDs are skipped."""
        from app.routes.conversations import import_external_conversations, ExternalConversationImport

        # First import
        data = ExternalConversationImport(
            content=json.dumps(sample_anthropic_export),
            entity_id="test-entity",
            source="anthropic",
        )
        result1 = await import_external_conversations(data, db_session)

        assert result1["messages_imported"] == 3  # All messages imported

        # Second import of same file
        result2 = await import_external_conversations(data, db_session)

        assert result2["messages_imported"] == 0  # No new messages
        assert result2["messages_skipped"] == 3  # All skipped as duplicates

    async def test_selective_import(
        self, db_session, sample_anthropic_export, mock_memory_service, mock_settings_with_entity
    ):
        """Test importing only selected conversations."""
        from app.routes.conversations import import_external_conversations, ExternalConversationImport

        data = ExternalConversationImport(
            content=json.dumps(sample_anthropic_export),
            entity_id="test-entity",
            source="anthropic",
            selected_conversations=[
                {"index": 0, "import_as_memory": True, "import_to_history": False},
                # index 1 not included - should be skipped
            ],
        )

        result = await import_external_conversations(data, db_session)

        assert result["conversations_imported"] == 1
        assert result["messages_imported"] == 2  # Only first conversation

    async def test_memory_only_import(
        self, db_session, sample_anthropic_export, mock_memory_service, mock_settings_with_entity
    ):
        """Test importing as memory only (not to history)."""
        from app.routes.conversations import import_external_conversations, ExternalConversationImport

        data = ExternalConversationImport(
            content=json.dumps(sample_anthropic_export),
            entity_id="test-entity",
            source="anthropic",
            selected_conversations=[
                {"index": 0, "import_as_memory": True, "import_to_history": False},
            ],
        )

        result = await import_external_conversations(data, db_session)

        assert result["conversations_to_history"] == 0
        assert result["memories_stored"] == 2

    async def test_history_only_import_no_memory(
        self, db_session, sample_anthropic_export, mock_settings_with_entity
    ):
        """Test importing to history only (without storing as memories)."""
        from app.routes.conversations import import_external_conversations, ExternalConversationImport

        with patch("app.services.memory_service") as mock_mem:
            mock_mem.is_configured.return_value = True
            mock_mem.store_memory = AsyncMock(return_value=True)

            data = ExternalConversationImport(
                content=json.dumps(sample_anthropic_export),
                entity_id="test-entity",
                source="anthropic",
                selected_conversations=[
                    {"index": 0, "import_as_memory": False, "import_to_history": True},
                ],
            )

            result = await import_external_conversations(data, db_session)

            assert result["conversations_to_history"] == 1
            assert result["memories_stored"] == 0
            mock_mem.store_memory.assert_not_called()


class TestPreviewEndpoint:
    """Tests for the preview endpoint."""

    @pytest.fixture
    def mock_settings_with_entity(self):
        """Mock settings with a test entity configured."""
        mock_entity = MagicMock()
        mock_entity.index_name = "test-entity"

        with patch("app.routes.conversations.settings") as mock:
            mock.get_entity_by_index.return_value = mock_entity
            yield mock

    async def test_preview_returns_conversation_list(
        self, db_session, sample_anthropic_export, mock_settings_with_entity
    ):
        """Test that preview returns list of conversations."""
        from app.routes.conversations import preview_external_conversations, ExternalConversationPreview

        data = ExternalConversationPreview(
            content=json.dumps(sample_anthropic_export),
            entity_id="test-entity",
        )

        result = await preview_external_conversations(data, db_session)

        assert result["source_format"] == "anthropic"
        assert result["total_conversations"] == 2
        assert len(result["conversations"]) == 2

        conv = result["conversations"][0]
        assert conv["title"] == "Test Anthropic Conversation"
        assert conv["message_count"] == 2
        assert conv["already_imported"] == False

    async def test_preview_shows_already_imported(
        self, db_session, sample_anthropic_export, mock_settings_with_entity
    ):
        """Test that preview shows which conversations are already imported."""
        from app.routes.conversations import (
            preview_external_conversations,
            import_external_conversations,
            ExternalConversationPreview,
            ExternalConversationImport,
        )

        with patch("app.services.memory_service") as mock_mem:
            mock_mem.is_configured.return_value = True
            mock_mem.store_memory = AsyncMock(return_value=True)

            # First import the conversations
            import_data = ExternalConversationImport(
                content=json.dumps(sample_anthropic_export),
                entity_id="test-entity",
                source="anthropic",
            )
            await import_external_conversations(import_data, db_session)

        # Now preview the same file
        preview_data = ExternalConversationPreview(
            content=json.dumps(sample_anthropic_export),
            entity_id="test-entity",
        )

        result = await preview_external_conversations(preview_data, db_session)

        # Both conversations should show as already imported
        assert result["conversations"][0]["already_imported"] == True
        assert result["conversations"][1]["already_imported"] == True

    async def test_preview_shows_partial_import(
        self, db_session, mock_settings_with_entity
    ):
        """Test that preview shows partial import status."""
        from app.routes.conversations import preview_external_conversations, ExternalConversationPreview
        from app.models import Message, MessageRole

        # Create a message with an ID that will match the export
        existing_msg = Message(
            id="msg-partial-1",
            conversation_id=str(uuid.uuid4()),
            role=MessageRole.HUMAN,
            content="Existing message",
        )

        # We need a conversation for the message
        conv = Conversation(
            title="Temp",
            conversation_type=ConversationType.NORMAL,
            entity_id="test-entity",
        )
        db_session.add(conv)
        await db_session.flush()

        existing_msg.conversation_id = conv.id
        db_session.add(existing_msg)
        await db_session.commit()

        # Export with some matching IDs
        export_data = [
            {
                "uuid": "conv-1",
                "name": "Partial",
                "chat_messages": [
                    {"uuid": "msg-partial-1", "sender": "human", "text": "Existing"},
                    {"uuid": "msg-partial-2", "sender": "assistant", "text": "New"},
                ],
            }
        ]

        preview_data = ExternalConversationPreview(
            content=json.dumps(export_data),
            entity_id="test-entity",
        )

        result = await preview_external_conversations(preview_data, db_session)

        conv_preview = result["conversations"][0]
        assert conv_preview["imported_count"] == 1
        assert conv_preview["message_count"] == 2
        assert conv_preview["already_imported"] == False  # Not fully imported


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.fixture
    def mock_settings_no_entity(self):
        """Mock settings with no entity configured."""
        with patch("app.routes.conversations.settings") as mock:
            mock.get_entity_by_index.return_value = None
            yield mock

    async def test_import_with_invalid_entity(self, db_session, mock_settings_no_entity):
        """Test import with non-existent entity."""
        from app.routes.conversations import import_external_conversations, ExternalConversationImport
        from fastapi import HTTPException

        data = ExternalConversationImport(
            content=json.dumps([]),
            entity_id="non-existent",
        )

        with pytest.raises(HTTPException) as exc_info:
            await import_external_conversations(data, db_session)

        assert exc_info.value.status_code == 400
        assert "not configured" in str(exc_info.value.detail)

    async def test_import_empty_file(self, db_session):
        """Test import with empty file."""
        from app.routes.conversations import import_external_conversations, ExternalConversationImport
        from fastapi import HTTPException

        with patch("app.routes.conversations.settings") as mock:
            mock.get_entity_by_index.return_value = MagicMock()

            data = ExternalConversationImport(
                content="[]",
                entity_id="test",
            )

            with pytest.raises(HTTPException) as exc_info:
                await import_external_conversations(data, db_session)

            assert exc_info.value.status_code == 400
            assert "No conversations found" in str(exc_info.value.detail)

    def test_parse_openai_with_dict_content(self):
        """Test OpenAI parser with dict content parts."""
        data = [
            {
                "id": "test",
                "title": "Dict Content",
                "mapping": {
                    "node-1": {
                        "message": {
                            "id": "msg-1",
                            "author": {"role": "user"},
                            "content": {"parts": [{"text": "Hello from dict"}]},
                            "create_time": 1700000001,
                        },
                    },
                },
            }
        ]
        result = _parse_openai_export(data)
        assert result[0]["messages"][0]["content"] == "Hello from dict"

    def test_parse_anthropic_with_user_sender(self):
        """Test Anthropic parser handles 'user' as sender."""
        data = [
            {
                "uuid": "test",
                "name": "Test",
                "chat_messages": [
                    {"uuid": "m1", "sender": "user", "text": "From user"},
                ],
            }
        ]
        result = _parse_anthropic_export(data)
        assert result[0]["messages"][0]["role"] == "human"

    async def test_skip_conversation_when_all_duplicates(
        self, db_session
    ):
        """Test that conversation is not created if all messages are duplicates."""
        from app.routes.conversations import import_external_conversations, ExternalConversationImport

        with patch("app.routes.conversations.settings") as mock_settings:
            mock_settings.get_entity_by_index.return_value = MagicMock()

            with patch("app.services.memory_service") as mock_mem:
                mock_mem.is_configured.return_value = True
                mock_mem.store_memory = AsyncMock(return_value=True)

                export_data = [
                    {
                        "uuid": "conv-dup",
                        "name": "Duplicate Conv",
                        "chat_messages": [
                            {"uuid": "dup-msg-1", "sender": "human", "text": "Hello"},
                        ],
                    }
                ]

                # First import
                data = ExternalConversationImport(
                    content=json.dumps(export_data),
                    entity_id="test",
                    source="anthropic",
                )
                result1 = await import_external_conversations(data, db_session)
                assert result1["conversations_imported"] == 1

                # Count conversations before second import
                count_before = await db_session.execute(select(Conversation))
                convs_before = len(count_before.scalars().all())

                # Second import - should not create new conversation
                result2 = await import_external_conversations(data, db_session)
                assert result2["conversations_imported"] == 0

                count_after = await db_session.execute(select(Conversation))
                convs_after = len(count_after.scalars().all())

                assert convs_after == convs_before
