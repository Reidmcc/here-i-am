"""
Disaster recovery between the SQL database and Pinecone.

Two directions:

- rebuild_vectors_from_database: regenerate an entity's Pinecone index from
  the SQL messages table (the SQL DB is the source of truth for content).
  Guards against loss of the vector database.

- restore_database_from_vectors: reconstruct conversations and messages in
  the SQL database from Pinecone record metadata. Only vectorized data comes
  back (human/assistant/reflection text, roles, timestamps, retrieval
  counts) — tool exchanges, titles, attachment blocks, system prompts, and
  memory links are gone — but it is far better than nothing.

Both operations default to dry_run and are non-destructive by default:
rebuild upserts by message ID (optionally wiping the index first), restore
only creates rows that don't already exist.
"""
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Conversation,
    ConversationEntity,
    ConversationType,
    Message,
    MessageRole,
)
from app.services.memory_service import run_pinecone

logger = logging.getLogger(__name__)

# Pinecone's integrated-inference upsert_records accepts at most 96 records
# per request; stay under it.
UPSERT_BATCH_SIZE = 50
FETCH_BATCH_SIZE = 100

# Extracted text-file content is folded into the persisted human message as
# [ATTACHED FILE: ...] blocks (attachment_service.build_persistable_content),
# but vectorization always used the raw typed message only — so rebuilds must
# strip the blocks back out.
ATTACHED_FILE_RE = re.compile(
    r"\[ATTACHED FILE: .*?\]\n.*?\n\[/ATTACHED FILE\]", re.DOTALL
)

# The closing-turn framing is persisted as a human message but deliberately
# never vectorized (routes/chat.py CLOSING_TURN_PROMPT — not imported here to
# avoid a services→routes dependency; the bracket prefix is stable).
CLOSING_TURN_PREFIX = "[CLOSING TURN]"

# Sentinel entity_id marking a multi-entity conversation; real participants
# live in ConversationEntity rows.
MULTI_ENTITY_SENTINEL = "multi-entity"


def strip_attachment_blocks(content: str) -> str:
    """Remove [ATTACHED FILE]...[/ATTACHED FILE] blocks from a human message."""
    return ATTACHED_FILE_RE.sub("", content).strip()


class VectorRebuildService:
    """Rebuild Pinecone from SQL and restore SQL from Pinecone."""

    def __init__(self, memory_service=None):
        # Injectable for tests; defaults to the app singleton.
        if memory_service is None:
            from app.services.memory_service import memory_service as default_ms
            memory_service = default_ms
        self.memory_service = memory_service

    # ------------------------------------------------------------------
    # SQL → Pinecone
    # ------------------------------------------------------------------

    async def rebuild_vectors_from_database(
        self,
        db: AsyncSession,
        entity_id: Optional[str] = None,
        dry_run: bool = True,
        wipe_first: bool = False,
        include_imported: bool = True,
    ) -> Dict[str, Any]:
        """
        Regenerate Pinecone indexes from the SQL messages table.

        Reproduces the live vectorization rules exactly:
        - Only HUMAN / ASSISTANT / REFLECTION messages are memories.
        - Human messages fan out to every participant's index (role="human"),
          with [ATTACHED FILE] blocks stripped (attachment-only messages and
          closing-turn framings are skipped — they were never vectorized).
        - Assistant messages go to the speaker's index as role="assistant"
          and to other participants' indexes under the speaker's label.
        - Assistant rows recording an inter-session message (sibling_session
          set; Claude Code conversations only) go out as role="sibling" with
          the sender's name in metadata, matching the live recording path.
        - Reflections go only to the saving entity's index (role="reflection").
        - times_retrieved metadata is restored from the SQL value.

        Args:
            db: Database session.
            entity_id: Rebuild only this entity's index. None rebuilds all
                configured entities.
            dry_run: If True (default), only report what would be upserted.
            wipe_first: If True, delete everything in the default namespace of
                each targeted index before upserting (removes records for
                messages that no longer exist in SQL). Ignored on dry runs.
            include_imported: If False, skip conversations with
                is_imported=True (imports made "to history only" are
                indistinguishable in the DB from imports made as memories,
                so this is the only way to exclude them).
        """
        result: Dict[str, Any] = {
            "dry_run": dry_run,
            "wipe_first": wipe_first,
            "entities": [],
            "skipped": {
                "attachment_only": 0,
                "closing_turn": 0,
                "empty_content": 0,
                "unattributed_assistant": 0,
                "unconfigured_entity_conversations": 0,
                "imported_conversations": 0,
            },
            "errors": [],
        }

        if not self.memory_service.is_configured():
            result["errors"].append("Pinecone not configured")
            return result

        entities = settings.get_entities()
        if entity_id is not None:
            entities = [e for e in entities if e.index_name == entity_id]
            if not entities:
                result["errors"].append(f"Unknown entity: {entity_id}")
                return result
        configured_indexes = {e.index_name for e in settings.get_entities()}
        target_indexes = {e.index_name for e in entities}

        default_entity = settings.get_default_entity()
        default_index = default_entity.index_name if default_entity else None

        # Load conversations and multi-entity participant rows
        conversations = (await db.execute(select(Conversation))).scalars().all()
        participant_rows = (
            await db.execute(
                select(
                    ConversationEntity.conversation_id, ConversationEntity.entity_id
                ).order_by(ConversationEntity.display_order)
            )
        ).fetchall()
        participants_by_conv: Dict[str, List[str]] = {}
        for conv_id, part_entity in participant_rows:
            participants_by_conv.setdefault(str(conv_id), []).append(part_entity)

        # Resolve each conversation to its participant indexes
        conv_participants: Dict[str, List[str]] = {}
        for conv in conversations:
            conv_id = str(conv.id)
            if not include_imported and conv.is_imported:
                result["skipped"]["imported_conversations"] += 1
                continue
            if conv.entity_id == MULTI_ENTITY_SENTINEL:
                participants = participants_by_conv.get(conv_id, [])
            elif conv.entity_id:
                participants = [conv.entity_id]
            elif default_index:
                # Legacy NULL entity_id → default entity's index
                participants = [default_index]
            else:
                participants = []

            known = [p for p in participants if p in configured_indexes]
            if participants and not known:
                result["skipped"]["unconfigured_entity_conversations"] += 1
                continue
            conv_participants[conv_id] = known

        # Build per-index upsert plans
        plans: Dict[str, List[Dict[str, Any]]] = {idx: [] for idx in target_indexes}

        messages = (
            await db.execute(
                select(Message).where(
                    Message.role.in_(
                        (MessageRole.HUMAN, MessageRole.ASSISTANT, MessageRole.REFLECTION)
                    )
                )
            )
        ).scalars().all()

        for msg in messages:
            conv_id = str(msg.conversation_id)
            participants = conv_participants.get(conv_id)
            if not participants:
                continue

            if msg.role == MessageRole.HUMAN:
                content = strip_attachment_blocks(msg.content or "")
                if not content:
                    if msg.content and "[ATTACHED FILE:" in msg.content:
                        result["skipped"]["attachment_only"] += 1
                    else:
                        result["skipped"]["empty_content"] += 1
                    continue
                if content.startswith(CLOSING_TURN_PREFIX):
                    result["skipped"]["closing_turn"] += 1
                    continue
                for index_name in participants:
                    self._plan_record(plans, index_name, msg, "human", content)

            elif msg.role == MessageRole.ASSISTANT:
                if not (msg.content or "").strip():
                    result["skipped"]["empty_content"] += 1
                    continue
                if msg.sibling_session:
                    # Inter-session message in a Claude Code conversation
                    # (always single-entity): vectorized as role="sibling",
                    # never as the conversation's own assistant voice
                    self._plan_record(
                        plans, participants[0], msg, "sibling", msg.content
                    )
                elif len(participants) == 1:
                    self._plan_record(plans, participants[0], msg, "assistant", msg.content)
                else:
                    speaker = msg.speaker_entity_id
                    if not speaker or speaker not in participants:
                        # Can't attribute the speaker; storing it as
                        # role="assistant" everywhere would let one entity's
                        # words masquerade as another's. Skip and report.
                        result["skipped"]["unattributed_assistant"] += 1
                        continue
                    speaker_entity = settings.get_entity_by_index(speaker)
                    speaker_label = (
                        speaker_entity.label if speaker_entity else None
                    ) or "other_entity"
                    for index_name in participants:
                        role = "assistant" if index_name == speaker else speaker_label
                        self._plan_record(plans, index_name, msg, role, msg.content)

            elif msg.role == MessageRole.REFLECTION:
                if not (msg.content or "").strip():
                    result["skipped"]["empty_content"] += 1
                    continue
                # memory_save always stamps the saving entity; legacy rows
                # without it fall back to the conversation's (single) entity.
                target = msg.speaker_entity_id or (
                    participants[0] if len(participants) == 1 else None
                )
                if target is None or target not in configured_indexes:
                    result["skipped"]["unattributed_assistant"] += 1
                    continue
                self._plan_record(plans, target, msg, "reflection", msg.content)

        # Execute (or just report) per entity
        for entity in entities:
            index_name = entity.index_name
            records = plans.get(index_name, [])
            entity_result = {
                "entity_id": index_name,
                "records_planned": len(records),
                "records_upserted": 0,
                "wiped": False,
                "errors": [],
            }

            if not dry_run:
                index = self.memory_service.get_index(index_name)
                if index is None:
                    entity_result["errors"].append("Could not connect to index")
                    result["entities"].append(entity_result)
                    continue

                if wipe_first:
                    try:
                        await run_pinecone(index.delete, delete_all=True, namespace="")
                        entity_result["wiped"] = True
                    except Exception as e:
                        # An empty/new index can reject delete_all; that's fine
                        logger.warning(
                            f"[REBUILD] Wipe of index '{index_name}' failed "
                            f"(may already be empty): {e}"
                        )
                        entity_result["errors"].append(f"Wipe failed: {e}")

                for i in range(0, len(records), UPSERT_BATCH_SIZE):
                    batch = records[i : i + UPSERT_BATCH_SIZE]
                    try:
                        await run_pinecone(
                            index.upsert_records, namespace="", records=batch
                        )
                        entity_result["records_upserted"] += len(batch)
                    except Exception as e:
                        logger.warning(
                            f"[REBUILD] Batch upsert failed for '{index_name}', "
                            f"retrying records individually: {e}"
                        )
                        for record in batch:
                            try:
                                await run_pinecone(
                                    index.upsert_records, namespace="", records=[record]
                                )
                                entity_result["records_upserted"] += 1
                            except Exception as record_error:
                                entity_result["errors"].append(
                                    f"Record {record['_id'][:8]}...: {record_error}"
                                )

                logger.info(
                    f"[REBUILD] Entity '{index_name}': upserted "
                    f"{entity_result['records_upserted']}/{len(records)} records"
                )

            result["entities"].append(entity_result)

        result["total_records_planned"] = sum(
            e["records_planned"] for e in result["entities"]
        )
        result["total_records_upserted"] = sum(
            e["records_upserted"] for e in result["entities"]
        )
        return result

    @staticmethod
    def _plan_record(
        plans: Dict[str, List[Dict[str, Any]]],
        index_name: str,
        msg: Message,
        role: str,
        content: str,
    ) -> None:
        """Add one Pinecone record (store_memory's exact shape) to a plan."""
        if index_name not in plans:
            return  # Participant exists but isn't targeted by this rebuild
        record = {
            "_id": str(msg.id),
            "text": content,
            "conversation_id": str(msg.conversation_id),
            "created_at": msg.created_at.isoformat(),
            "role": role,
            "content_preview": content[:200],
            "times_retrieved": msg.times_retrieved or 0,
        }
        # Matches store_memory: only set when present (no null metadata)
        if role == "sibling" and msg.sibling_session:
            record["sibling_session"] = msg.sibling_session
        if msg.model:
            record["model"] = msg.model
        plans[index_name].append(record)

    # ------------------------------------------------------------------
    # Pinecone → SQL
    # ------------------------------------------------------------------

    async def restore_database_from_vectors(
        self,
        db: AsyncSession,
        entity_id: Optional[str] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """
        Reconstruct conversations and messages in SQL from Pinecone records.

        Non-destructive: only conversations and messages that don't already
        exist are created, so this can run against a partially intact
        database to fill gaps.

        What comes back: message text (from the vectorized "text" field, or
        the 200-char content_preview when full text is unavailable), roles,
        conversation grouping, timestamps, and retrieval counts. Multi-entity
        conversations are detected (records for the same conversation across
        several indexes) and their ConversationEntity rows recreated;
        label-role copies of assistant messages are deduplicated against the
        speaker's authoritative role="assistant" record.

        What is lost: conversation titles/system prompts/tags, tool
        exchanges, attachment blocks, memory links, pinned/released statuses,
        and anything never vectorized.

        Args:
            db: Database session.
            entity_id: Scan only this entity's index. None scans all
                configured entities (recommended — multi-entity detection
                needs every index).
            dry_run: If True (default), only report what would be created.
        """
        result: Dict[str, Any] = {
            "dry_run": dry_run,
            "entities_scanned": [],
            "records_scanned": 0,
            "unique_messages": 0,
            "conversations_created": 0,
            "conversations_existing": 0,
            "messages_created": 0,
            "messages_existing": 0,
            "messages_preview_only": 0,
            "errors": [],
        }

        if not self.memory_service.is_configured():
            result["errors"].append("Pinecone not configured")
            return result

        entities = settings.get_entities()
        if entity_id is not None:
            entities = [e for e in entities if e.index_name == entity_id]
            if not entities:
                result["errors"].append(f"Unknown entity: {entity_id}")
                return result

        label_to_index = {e.label: e.index_name for e in settings.get_entities()}

        # message_id → best-known record; conversation_id → indexes seen in
        recovered: Dict[str, Dict[str, Any]] = {}
        conv_indexes: Dict[str, Set[str]] = {}

        for entity in entities:
            index_name = entity.index_name
            index = self.memory_service.get_index(index_name)
            if index is None:
                result["errors"].append(f"Could not connect to index '{index_name}'")
                continue

            ids = await self.memory_service.list_all_pinecone_ids(index_name)
            result["entities_scanned"].append(
                {"entity_id": index_name, "records": len(ids)}
            )

            for i in range(0, len(ids), FETCH_BATCH_SIZE):
                batch_ids = ids[i : i + FETCH_BATCH_SIZE]
                try:
                    fetch_result = await run_pinecone(index.fetch, ids=batch_ids)
                except Exception as e:
                    result["errors"].append(
                        f"Fetch failed for '{index_name}' batch at {i}: {e}"
                    )
                    continue

                for record_id in batch_ids:
                    vector = fetch_result.vectors.get(record_id)
                    if vector is None:
                        continue
                    metadata = getattr(vector, "metadata", None) or {}
                    result["records_scanned"] += 1
                    self._merge_record(
                        recovered,
                        conv_indexes,
                        record_id,
                        metadata,
                        index_name,
                        label_to_index,
                    )

        result["unique_messages"] = len(recovered)
        if not recovered:
            return result

        # Existing rows are left untouched
        existing_message_ids = {
            str(row[0]) for row in (await db.execute(select(Message.id))).fetchall()
        }
        existing_conversation_ids = {
            str(row[0])
            for row in (await db.execute(select(Conversation.id))).fetchall()
        }

        # Create missing conversations
        conv_earliest: Dict[str, datetime] = {}
        for record in recovered.values():
            conv_id = record["conversation_id"]
            created = record["created_at"]
            if conv_id not in conv_earliest or created < conv_earliest[conv_id]:
                conv_earliest[conv_id] = created

        for conv_id, indexes_seen in conv_indexes.items():
            if conv_id in existing_conversation_ids:
                result["conversations_existing"] += 1
                continue
            result["conversations_created"] += 1
            if dry_run:
                continue

            is_multi = len(indexes_seen) > 1
            earliest = conv_earliest.get(conv_id, datetime.utcnow())
            conversation = Conversation(
                id=conv_id,
                title=f"[Recovered from Pinecone] {earliest.date().isoformat()}",
                created_at=earliest,
                conversation_type=(
                    ConversationType.MULTI_ENTITY if is_multi else ConversationType.NORMAL
                ),
                entity_id=(
                    MULTI_ENTITY_SENTINEL if is_multi else next(iter(indexes_seen))
                ),
            )
            db.add(conversation)
            if is_multi:
                for order, participant in enumerate(sorted(indexes_seen)):
                    db.add(
                        ConversationEntity(
                            conversation_id=conv_id,
                            entity_id=participant,
                            display_order=order,
                        )
                    )

        # Create missing messages
        for message_id, record in recovered.items():
            if message_id in existing_message_ids:
                result["messages_existing"] += 1
                continue
            result["messages_created"] += 1
            if record["preview_only"]:
                result["messages_preview_only"] += 1
            if dry_run:
                continue

            conv_is_multi = len(conv_indexes.get(record["conversation_id"], set())) > 1
            speaker = record["speaker_entity_id"]
            db.add(
                Message(
                    id=message_id,
                    conversation_id=record["conversation_id"],
                    role=record["role"],
                    content=record["content"],
                    created_at=record["created_at"],
                    times_retrieved=record["times_retrieved"],
                    # Live rows carry a speaker only for reflections and for
                    # assistant messages in multi-entity conversations
                    speaker_entity_id=(
                        speaker
                        if speaker
                        and (record["role"] == MessageRole.REFLECTION or conv_is_multi)
                        else None
                    ),
                    sibling_session=record.get("sibling_session"),
                    model=record.get("model"),
                )
            )

        if not dry_run:
            await db.commit()
            logger.info(
                f"[RESTORE] Created {result['conversations_created']} conversations "
                f"and {result['messages_created']} messages from Pinecone "
                f"({result['messages_preview_only']} preview-only)"
            )

        return result

    @staticmethod
    def _merge_record(
        recovered: Dict[str, Dict[str, Any]],
        conv_indexes: Dict[str, Set[str]],
        record_id: str,
        metadata: Dict[str, Any],
        index_name: str,
        label_to_index: Dict[str, str],
    ) -> None:
        """
        Merge one Pinecone record into the recovery map.

        The same message ID can appear in several indexes: human messages fan
        out to every participant, and assistant messages exist as
        role="assistant" in the speaker's index plus label-role copies in the
        others. The authoritative record (assistant/human/reflection/sibling
        role) wins over a label copy; a label copy is still recoverable alone
        by resolving the label back to an entity.
        """
        conversation_id = str(metadata.get("conversation_id") or "")
        if not conversation_id:
            return  # Not a memory record we can place

        conv_indexes.setdefault(conversation_id, set()).add(index_name)

        raw_role = metadata.get("role") or ""
        content = metadata.get("text") or ""
        preview_only = False
        if not content:
            content = metadata.get("content_preview") or ""
            preview_only = True
        if not content:
            return

        created_at_raw = metadata.get("created_at")
        try:
            created_at = datetime.fromisoformat(created_at_raw)
        except (TypeError, ValueError):
            created_at = datetime.utcnow()

        try:
            times_retrieved = int(metadata.get("times_retrieved") or 0)
        except (TypeError, ValueError):
            times_retrieved = 0

        sibling_session = None
        if raw_role == "human":
            role, speaker, authoritative = MessageRole.HUMAN, None, True
        elif raw_role == "assistant":
            role, speaker, authoritative = MessageRole.ASSISTANT, index_name, True
        elif raw_role == "reflection":
            role, speaker, authoritative = MessageRole.REFLECTION, index_name, True
        elif raw_role == "sibling":
            # An inter-session message recorded in a Claude Code
            # conversation: an ASSISTANT row whose sibling_session column
            # marks the sending session (kept in the record metadata)
            role, speaker, authoritative = MessageRole.ASSISTANT, index_name, True
            sibling_session = (
                str(metadata.get("sibling_session") or "") or "unknown session"
            )
        else:
            # A label-role copy of another entity's assistant message
            role = MessageRole.ASSISTANT
            speaker = label_to_index.get(raw_role)
            authoritative = False

        # The producing model rides along as metadata on every copy of the
        # record (store_memory mirrors Message.model); absent means the row
        # was never attributed, and restore keeps it NULL rather than guess.
        model = str(metadata.get("model") or "").strip() or None

        existing = recovered.get(record_id)
        if existing is not None:
            # Track the highest retrieval count across copies; each index
            # increments its own copy's metadata independently.
            if times_retrieved > existing["times_retrieved"]:
                existing["times_retrieved"] = times_retrieved
            if existing["authoritative"]:
                return
            if not authoritative:
                return

        recovered[record_id] = {
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "created_at": created_at,
            "times_retrieved": max(
                times_retrieved,
                existing["times_retrieved"] if existing else 0,
            ),
            "speaker_entity_id": speaker,
            "sibling_session": sibling_session,
            "model": model,
            "preview_only": preview_only,
            "authoritative": authoritative,
        }


# Singleton instance
vector_rebuild_service = VectorRebuildService()
