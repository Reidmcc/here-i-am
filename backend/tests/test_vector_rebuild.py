"""
Tests for vector_rebuild_service: rebuilding Pinecone from SQL and
restoring SQL from Pinecone.
"""
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import (
    Conversation,
    ConversationEntity,
    ConversationType,
    Message,
    MessageRole,
)
from app.services.vector_rebuild_service import (
    VectorRebuildService,
    strip_attachment_blocks,
)


class FakeIndex:
    """In-memory stand-in for a Pinecone index."""

    def __init__(self, records=None):
        # records: list of dicts in upsert_records shape (with _id)
        self.records = {r["_id"]: dict(r) for r in (records or [])}
        self.upserted = []
        self.deleted_all = False

    def upsert_records(self, namespace, records):
        self.upserted.extend(records)
        for r in records:
            self.records[r["_id"]] = dict(r)

    def delete(self, ids=None, delete_all=False, namespace=None):
        if delete_all:
            self.deleted_all = True
            self.records = {}
        elif ids:
            for i in ids:
                self.records.pop(i, None)

    def fetch(self, ids):
        vectors = {}
        for record_id in ids:
            record = self.records.get(record_id)
            if record is not None:
                metadata = {k: v for k, v in record.items() if k != "_id"}
                vectors[record_id] = SimpleNamespace(metadata=metadata)
        return SimpleNamespace(vectors=vectors)


class FakeMemoryService:
    def __init__(self, indexes):
        self.indexes = indexes  # {index_name: FakeIndex}

    def is_configured(self, entity_id=None):
        return True

    def get_index(self, entity_id=None):
        return self.indexes.get(entity_id)

    async def list_all_pinecone_ids(self, entity_id=None):
        index = self.indexes.get(entity_id)
        return list(index.records.keys()) if index else []


def make_service(indexes):
    return VectorRebuildService(memory_service=FakeMemoryService(indexes))


def test_strip_attachment_blocks():
    content = (
        "[ATTACHED FILE: notes.txt (Text)]\n```\nfile body\n```\n[/ATTACHED FILE]"
        "\n\nWhat do you think of this?"
    )
    assert strip_attachment_blocks(content) == "What do you think of this?"
    # Attachment-only message strips to nothing
    only = "[ATTACHED FILE: a.pdf (PDF)]\n```\nstuff\n```\n[/ATTACHED FILE]"
    assert strip_attachment_blocks(only) == ""
    # Plain messages pass through
    assert strip_attachment_blocks("hello") == "hello"


class TestRebuildVectorsFromDatabase:
    @pytest.mark.asyncio
    async def test_single_entity_rebuild(self, db_session, test_settings):
        conv = Conversation(
            id=str(uuid.uuid4()),
            title="Test",
            entity_id="test-memories",
        )
        db_session.add(conv)

        human = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.HUMAN, content="Hello there", times_retrieved=3,
        )
        assistant = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.ASSISTANT, content="Hi! Nice to meet you.",
        )
        reflection = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.REFLECTION, content="I enjoyed this.",
            speaker_entity_id="test-memories",
        )
        closing = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.HUMAN,
            content="[CLOSING TURN] This conversation is ending.",
        )
        attachment_only = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.HUMAN,
            content="[ATTACHED FILE: a.txt (Text)]\n```\nbody\n```\n[/ATTACHED FILE]",
        )
        with_attachment = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.HUMAN,
            content="[ATTACHED FILE: a.txt (Text)]\n```\nbody\n```\n[/ATTACHED FILE]\n\nSee attached",
        )
        tool_use = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.TOOL_USE, content="[]",
        )
        for m in (human, assistant, reflection, closing, attachment_only, with_attachment, tool_use):
            db_session.add(m)
        await db_session.commit()

        index = FakeIndex()
        service = make_service({"test-memories": index})

        with patch("app.services.vector_rebuild_service.settings", test_settings):
            dry = await service.rebuild_vectors_from_database(db_session, dry_run=True)
            assert dry["dry_run"] is True
            assert dry["total_records_planned"] == 4
            assert dry["skipped"]["closing_turn"] == 1
            assert dry["skipped"]["attachment_only"] == 1
            assert index.upserted == []

            result = await service.rebuild_vectors_from_database(db_session, dry_run=False)

        assert result["total_records_upserted"] == 4
        by_id = {r["_id"]: r for r in index.upserted}
        assert by_id[human.id]["role"] == "human"
        assert by_id[human.id]["times_retrieved"] == 3
        assert by_id[assistant.id]["role"] == "assistant"
        assert by_id[reflection.id]["role"] == "reflection"
        # Attachment blocks are stripped from the vectorized text
        assert by_id[with_attachment.id]["text"] == "See attached"
        assert closing.id not in by_id
        assert tool_use.id not in by_id

    @pytest.mark.asyncio
    async def test_sibling_row_rebuilds_as_sibling_role(
        self, db_session, test_settings
    ):
        # An inter-session message recorded in a Claude Code conversation:
        # ASSISTANT row with sibling_session set. The rebuild must reproduce
        # the live vectorization rule — role="sibling", sender in metadata —
        # or a rebuilt index would let the letter pass as this session's own
        # assistant voice.
        conv = Conversation(
            id=str(uuid.uuid4()),
            title="CC session",
            entity_id="test-memories",
            source="claude_code",
        )
        db_session.add(conv)
        letter = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.ASSISTANT, content="The porch specced it.",
            sibling_session="Porch chat",
        )
        own_reply = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.ASSISTANT, content="And the workshop built it.",
        )
        db_session.add_all([letter, own_reply])
        await db_session.commit()

        index = FakeIndex()
        service = make_service({"test-memories": index})
        with patch("app.services.vector_rebuild_service.settings", test_settings):
            await service.rebuild_vectors_from_database(db_session, dry_run=False)

        by_id = {r["_id"]: r for r in index.upserted}
        assert by_id[letter.id]["role"] == "sibling"
        assert by_id[letter.id]["sibling_session"] == "Porch chat"
        assert by_id[own_reply.id]["role"] == "assistant"
        assert "sibling_session" not in by_id[own_reply.id]

    @pytest.mark.asyncio
    async def test_multi_entity_fan_out(self, db_session, test_settings_multi_entity):
        conv = Conversation(
            id=str(uuid.uuid4()),
            entity_id="multi-entity",
            conversation_type=ConversationType.MULTI_ENTITY,
        )
        db_session.add(conv)
        db_session.add(ConversationEntity(conversation_id=conv.id, entity_id="claude-test", display_order=0))
        db_session.add(ConversationEntity(conversation_id=conv.id, entity_id="gpt-test", display_order=1))

        human = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.HUMAN, content="Hello both of you",
        )
        claude_msg = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.ASSISTANT, content="Claude speaking",
            speaker_entity_id="claude-test",
        )
        unattributed = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.ASSISTANT, content="Who said this?",
        )
        for m in (human, claude_msg, unattributed):
            db_session.add(m)
        await db_session.commit()

        claude_index = FakeIndex()
        gpt_index = FakeIndex()
        service = make_service({"claude-test": claude_index, "gpt-test": gpt_index})

        with patch("app.services.vector_rebuild_service.settings", test_settings_multi_entity):
            result = await service.rebuild_vectors_from_database(db_session, dry_run=False)

        assert result["skipped"]["unattributed_assistant"] == 1

        claude_by_id = {r["_id"]: r for r in claude_index.upserted}
        gpt_by_id = {r["_id"]: r for r in gpt_index.upserted}
        # Human fans out to both indexes as role=human
        assert claude_by_id[human.id]["role"] == "human"
        assert gpt_by_id[human.id]["role"] == "human"
        # Speaker gets role=assistant; the other participant gets the label
        assert claude_by_id[claude_msg.id]["role"] == "assistant"
        assert gpt_by_id[claude_msg.id]["role"] == "Claude Test"
        assert unattributed.id not in claude_by_id
        assert unattributed.id not in gpt_by_id

    @pytest.mark.asyncio
    async def test_null_entity_conversation_uses_default(self, db_session, test_settings_multi_entity):
        conv = Conversation(id=str(uuid.uuid4()), entity_id=None)
        db_session.add(conv)
        msg = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.ASSISTANT, content="Legacy message",
        )
        db_session.add(msg)
        await db_session.commit()

        claude_index = FakeIndex()
        gpt_index = FakeIndex()
        service = make_service({"claude-test": claude_index, "gpt-test": gpt_index})

        with patch("app.services.vector_rebuild_service.settings", test_settings_multi_entity):
            await service.rebuild_vectors_from_database(db_session, dry_run=False)

        # First configured entity is the default
        assert [r["_id"] for r in claude_index.upserted] == [msg.id]
        assert gpt_index.upserted == []

    @pytest.mark.asyncio
    async def test_wipe_first_and_entity_scope(self, db_session, test_settings_multi_entity):
        conv = Conversation(id=str(uuid.uuid4()), entity_id="claude-test")
        db_session.add(conv)
        db_session.add(Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.HUMAN, content="hi",
        ))
        await db_session.commit()

        claude_index = FakeIndex(records=[{
            "_id": "stale", "text": "old", "conversation_id": "gone",
            "created_at": "2024-01-01T00:00:00", "role": "human",
            "content_preview": "old", "times_retrieved": 0,
        }])
        gpt_index = FakeIndex()
        service = make_service({"claude-test": claude_index, "gpt-test": gpt_index})

        with patch("app.services.vector_rebuild_service.settings", test_settings_multi_entity):
            result = await service.rebuild_vectors_from_database(
                db_session, entity_id="claude-test", dry_run=False, wipe_first=True
            )

        assert claude_index.deleted_all is True
        assert "stale" not in claude_index.records
        assert result["entities"][0]["wiped"] is True
        assert result["total_records_upserted"] == 1
        # Only the requested entity is touched
        assert len(result["entities"]) == 1

    @pytest.mark.asyncio
    async def test_unknown_entity_errors(self, db_session, test_settings):
        service = make_service({})
        with patch("app.services.vector_rebuild_service.settings", test_settings):
            result = await service.rebuild_vectors_from_database(
                db_session, entity_id="nope"
            )
        assert "Unknown entity: nope" in result["errors"]


def pinecone_record(message_id, conversation_id, role, text, created_at="2024-06-01T12:00:00", times_retrieved=0):
    return {
        "_id": message_id,
        "text": text,
        "conversation_id": conversation_id,
        "created_at": created_at,
        "role": role,
        "content_preview": text[:200],
        "times_retrieved": times_retrieved,
    }


class TestRestoreDatabaseFromVectors:
    @pytest.mark.asyncio
    async def test_single_entity_restore(self, db_session, test_settings):
        conv_id = str(uuid.uuid4())
        human_id = str(uuid.uuid4())
        assistant_id = str(uuid.uuid4())
        index = FakeIndex(records=[
            pinecone_record(human_id, conv_id, "human", "Hello", times_retrieved=2),
            pinecone_record(assistant_id, conv_id, "assistant", "Hi back"),
        ])
        service = make_service({"test-memories": index})

        with patch("app.services.vector_rebuild_service.settings", test_settings):
            dry = await service.restore_database_from_vectors(db_session, dry_run=True)
            assert dry["conversations_created"] == 1
            assert dry["messages_created"] == 2
            # Dry run creates nothing
            count = (await db_session.execute(select(Message))).scalars().all()
            assert count == []

            result = await service.restore_database_from_vectors(db_session, dry_run=False)

        assert result["conversations_created"] == 1
        assert result["messages_created"] == 2

        conv = (await db_session.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )).scalar_one()
        assert conv.entity_id == "test-memories"
        assert conv.conversation_type == ConversationType.NORMAL
        assert "Recovered from Pinecone" in conv.title

        human = (await db_session.execute(
            select(Message).where(Message.id == human_id)
        )).scalar_one()
        assert human.role == MessageRole.HUMAN
        assert human.content == "Hello"
        assert human.times_retrieved == 2
        assert human.created_at == datetime(2024, 6, 1, 12, 0, 0)

    @pytest.mark.asyncio
    async def test_sibling_record_restores_provenance_column(
        self, db_session, test_settings
    ):
        conv_id = str(uuid.uuid4())
        letter_id = str(uuid.uuid4())
        unsigned_id = str(uuid.uuid4())
        signed = pinecone_record(letter_id, conv_id, "sibling", "The letter")
        signed["sibling_session"] = "Porch chat"
        # A sibling record written without the metadata field (or one that
        # lost it) still restores as an inter-session message
        unsigned = pinecone_record(unsigned_id, conv_id, "sibling", "Unsigned")
        index = FakeIndex(records=[signed, unsigned])
        service = make_service({"test-memories": index})

        with patch("app.services.vector_rebuild_service.settings", test_settings):
            result = await service.restore_database_from_vectors(
                db_session, dry_run=False
            )
        assert result["messages_created"] == 2

        letter = (await db_session.execute(
            select(Message).where(Message.id == letter_id)
        )).scalar_one()
        assert letter.role == MessageRole.ASSISTANT
        assert letter.sibling_session == "Porch chat"

        unsigned_row = (await db_session.execute(
            select(Message).where(Message.id == unsigned_id)
        )).scalar_one()
        assert unsigned_row.role == MessageRole.ASSISTANT
        assert unsigned_row.sibling_session == "unknown session"

    @pytest.mark.asyncio
    async def test_multi_entity_restore_dedupes_label_copies(self, db_session, test_settings_multi_entity):
        conv_id = str(uuid.uuid4())
        human_id = str(uuid.uuid4())
        claude_msg_id = str(uuid.uuid4())
        reflection_id = str(uuid.uuid4())

        claude_index = FakeIndex(records=[
            pinecone_record(human_id, conv_id, "human", "Hello both"),
            pinecone_record(claude_msg_id, conv_id, "assistant", "Claude speaking"),
            pinecone_record(reflection_id, conv_id, "reflection", "My reflection"),
        ])
        gpt_index = FakeIndex(records=[
            pinecone_record(human_id, conv_id, "human", "Hello both"),
            pinecone_record(claude_msg_id, conv_id, "Claude Test", "Claude speaking"),
        ])
        service = make_service({"claude-test": claude_index, "gpt-test": gpt_index})

        with patch("app.services.vector_rebuild_service.settings", test_settings_multi_entity):
            result = await service.restore_database_from_vectors(db_session, dry_run=False)

        # 5 records scanned, but only 3 unique messages
        assert result["records_scanned"] == 5
        assert result["unique_messages"] == 3
        assert result["messages_created"] == 3
        assert result["conversations_created"] == 1

        conv = (await db_session.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )).scalar_one()
        assert conv.entity_id == "multi-entity"
        assert conv.conversation_type == ConversationType.MULTI_ENTITY
        participants = (await db_session.execute(
            select(ConversationEntity.entity_id).where(
                ConversationEntity.conversation_id == conv_id
            )
        )).scalars().all()
        assert sorted(participants) == ["claude-test", "gpt-test"]

        claude_msg = (await db_session.execute(
            select(Message).where(Message.id == claude_msg_id)
        )).scalar_one()
        assert claude_msg.role == MessageRole.ASSISTANT
        # Speaker comes from the authoritative role="assistant" record
        assert claude_msg.speaker_entity_id == "claude-test"

        reflection = (await db_session.execute(
            select(Message).where(Message.id == reflection_id)
        )).scalar_one()
        assert reflection.role == MessageRole.REFLECTION
        assert reflection.speaker_entity_id == "claude-test"

        human = (await db_session.execute(
            select(Message).where(Message.id == human_id)
        )).scalar_one()
        assert human.speaker_entity_id is None

    @pytest.mark.asyncio
    async def test_label_copy_alone_is_recoverable(self, db_session, test_settings_multi_entity):
        # Only the gpt index survived; Claude's message exists only as a
        # label-role copy there.
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        gpt_index = FakeIndex(records=[
            pinecone_record(msg_id, conv_id, "Claude Test", "Claude speaking"),
        ])
        service = make_service({"claude-test": FakeIndex(), "gpt-test": gpt_index})

        with patch("app.services.vector_rebuild_service.settings", test_settings_multi_entity):
            await service.restore_database_from_vectors(db_session, dry_run=False)

        msg = (await db_session.execute(
            select(Message).where(Message.id == msg_id)
        )).scalar_one()
        assert msg.role == MessageRole.ASSISTANT
        # Label resolved back to the entity index... but the conversation was
        # only seen in one index, so it restores as single-entity and the
        # speaker stamp is dropped (matches live single-entity rows).
        conv = (await db_session.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )).scalar_one()
        assert conv.entity_id == "gpt-test"

    @pytest.mark.asyncio
    async def test_existing_rows_untouched(self, db_session, test_settings):
        conv = Conversation(id=str(uuid.uuid4()), title="Original title", entity_id="test-memories")
        db_session.add(conv)
        existing = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.HUMAN, content="Original content",
        )
        db_session.add(existing)
        await db_session.commit()

        new_id = str(uuid.uuid4())
        index = FakeIndex(records=[
            pinecone_record(existing.id, conv.id, "human", "Different content"),
            pinecone_record(new_id, conv.id, "assistant", "Missing message"),
        ])
        service = make_service({"test-memories": index})

        with patch("app.services.vector_rebuild_service.settings", test_settings):
            result = await service.restore_database_from_vectors(db_session, dry_run=False)

        assert result["conversations_existing"] == 1
        assert result["messages_existing"] == 1
        assert result["messages_created"] == 1

        untouched = (await db_session.execute(
            select(Message).where(Message.id == existing.id)
        )).scalar_one()
        assert untouched.content == "Original content"
        conv_row = (await db_session.execute(
            select(Conversation).where(Conversation.id == conv.id)
        )).scalar_one()
        assert conv_row.title == "Original title"
        # The missing message was filled into the existing conversation
        created = (await db_session.execute(
            select(Message).where(Message.id == new_id)
        )).scalar_one()
        assert created.content == "Missing message"

    @pytest.mark.asyncio
    async def test_preview_only_fallback(self, db_session, test_settings):
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        record = pinecone_record(msg_id, conv_id, "human", "unused")
        record["text"] = ""  # Full text lost; only the preview survives
        record["content_preview"] = "Preview text"
        index = FakeIndex(records=[record])
        service = make_service({"test-memories": index})

        with patch("app.services.vector_rebuild_service.settings", test_settings):
            result = await service.restore_database_from_vectors(db_session, dry_run=False)

        assert result["messages_preview_only"] == 1
        msg = (await db_session.execute(
            select(Message).where(Message.id == msg_id)
        )).scalar_one()
        assert msg.content == "Preview text"


class TestModelAttributionRoundTrip:
    """
    Message.model (issue #321) is mirrored into Pinecone metadata purely so
    disaster recovery can round-trip the column. Rebuild writes it only
    where the row has one (no null metadata), restore reads it back, and an
    unattributed record restores as NULL — never inferred.
    """

    @pytest.mark.asyncio
    async def test_rebuild_mirrors_model_only_when_recorded(self, db_session, test_settings):
        conv = Conversation(
            id=str(uuid.uuid4()), title="Attributed", entity_id="test-memories",
        )
        db_session.add(conv)
        tagged = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.ASSISTANT, content="Written on 5.1.",
            model="claude-fable-5-1",
        )
        untagged = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.ASSISTANT, content="Written before the column.",
        )
        human = Message(
            id=str(uuid.uuid4()), conversation_id=conv.id,
            role=MessageRole.HUMAN, content="A prompt.",
        )
        db_session.add_all([tagged, untagged, human])
        await db_session.commit()

        index = FakeIndex()
        service = make_service({"test-memories": index})
        with patch("app.services.vector_rebuild_service.settings", test_settings):
            await service.rebuild_vectors_from_database(db_session, dry_run=False)

        by_id = {r["_id"]: r for r in index.upserted}
        assert by_id[tagged.id]["model"] == "claude-fable-5-1"
        assert "model" not in by_id[untagged.id]
        assert "model" not in by_id[human.id]

    @pytest.mark.asyncio
    async def test_restore_recovers_model_column(self, db_session, test_settings):
        conv_id = str(uuid.uuid4())
        tagged_id = str(uuid.uuid4())
        untagged_id = str(uuid.uuid4())
        tagged = pinecone_record(tagged_id, conv_id, "assistant", "Written on 5.1.")
        tagged["model"] = "claude-fable-5-1"
        untagged = pinecone_record(untagged_id, conv_id, "assistant", "Older.")
        index = FakeIndex(records=[tagged, untagged])
        service = make_service({"test-memories": index})

        with patch("app.services.vector_rebuild_service.settings", test_settings):
            result = await service.restore_database_from_vectors(db_session, dry_run=False)
        assert result["messages_created"] == 2

        rows = {
            m.id: m for m in (await db_session.execute(
                select(Message).where(Message.conversation_id == conv_id)
            )).scalars().all()
        }
        assert rows[tagged_id].model == "claude-fable-5-1"
        assert rows[untagged_id].model is None
