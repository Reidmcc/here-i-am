import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from pinecone import Pinecone
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.models import (
    LINK_CITES,
    LINK_REVISES,
    Conversation,
    ConversationEntity,
    ConversationIdAlias,
    ConversationMemoryLink,
    MemoryLink,
    Message,
    MessageRole,
)
from app.services.memory_context import (
    SINCE_LAST_SESSION,
    format_archive_change_notice,
    format_archive_nothing_changed,
    format_memory_link_lines,
    format_status_change_notice,
    format_status_nothing_changed,
)

logger = logging.getLogger(__name__)


# Who wrote a memory's status (Message.status_set_by): the entity through its
# memory_mark / memory_release tools, or the researcher through the status
# override route. Recorded on every write, including clears, so a reversal of
# the entity's choice is attributed and reportable.
STATUS_SET_BY_ENTITY = "entity"
STATUS_SET_BY_RESEARCHER = "researcher"

# "Not passed" for the notice builders' optional precomputed anchor, whose
# None already means "the entity has never spoken".
_ANCHOR_UNSET = object()
VALID_STATUS_SETTERS = (STATUS_SET_BY_ENTITY, STATUS_SET_BY_RESEARCHER)

# Only these roles are vectorized, so only they can be memories with a status
MEMORY_ROLES = (MessageRole.HUMAN, MessageRole.ASSISTANT, MessageRole.REFLECTION)


# Role filters accepted by search_memories. Memories carry a "role" metadata
# field: "human" for the human's messages, and "assistant", "reflection", or
# another entity's speaker label (multi-entity conversations) for everything
# the AI side produced. "human" and "ai" therefore partition the store, and
# "ai" is expressed as "not human" so speaker labels — an open set, one per
# configured entity — are covered without enumerating them. "reflection"
# narrows "ai" to the entity's own memory_save reflections.
ROLE_FILTER_HUMAN = "human"
ROLE_FILTER_AI = "ai"
ROLE_FILTER_REFLECTION = "reflection"
VALID_ROLE_FILTERS = (ROLE_FILTER_HUMAN, ROLE_FILTER_AI, ROLE_FILTER_REFLECTION)


def normalize_role_filter(role_filter: Optional[str]) -> Optional[str]:
    """
    Normalize a role filter to "human", "ai", "reflection", or None (no
    filtering).

    Accepts None, "" and "all" as "no filter". Unrecognized values are
    treated as no filter (logged), so a bad value widens results rather
    than silently returning nothing.
    """
    if role_filter is None:
        return None
    normalized = str(role_filter).strip().lower()
    if normalized in ("", "all", "any"):
        return None
    if normalized in VALID_ROLE_FILTERS:
        return normalized
    logger.warning(f"[MEMORY] Unknown role_filter '{role_filter}', ignoring")
    return None


def role_matches_filter(role: Optional[str], role_filter: Optional[str]) -> bool:
    """Whether a memory's role metadata satisfies a normalized role filter."""
    if not role_filter:
        return True
    if role_filter == ROLE_FILTER_REFLECTION:
        return role == ROLE_FILTER_REFLECTION
    is_human = role == ROLE_FILTER_HUMAN
    return is_human if role_filter == ROLE_FILTER_HUMAN else not is_human


# SQLite caps bound parameters per statement; a reader's page can name a
# few hundred rows, so memory-link IN lists are chunked below this
LINK_QUERY_CHUNK = 400


async def load_memory_link_ids(
    db: AsyncSession, reflection_ids: List[str]
) -> Dict[str, Dict[str, List[str]]]:
    """
    {reflection_id: {"revises": [target ids], "cites": [target ids]}}, full
    ids in the order the entity gave them — the shape mirrored into
    Pinecone metadata and exports. Reflections without links are absent.
    Module-level (plain SQL, no Pinecone) so the vector rebuild can use it
    with an injected memory service.
    """
    ids = list(dict.fromkeys(str(rid) for rid in reflection_ids if rid))
    result: Dict[str, Dict[str, List[str]]] = {}
    for start in range(0, len(ids), LINK_QUERY_CHUNK):
        chunk = ids[start:start + LINK_QUERY_CHUNK]
        rows = await db.execute(
            select(MemoryLink.reflection_id, MemoryLink.kind, MemoryLink.target_id)
            .where(MemoryLink.reflection_id.in_(chunk))
            .order_by(MemoryLink.reflection_id, MemoryLink.kind, MemoryLink.position)
        )
        for reflection_id, kind, target_id in rows.all():
            entry = result.setdefault(str(reflection_id), {LINK_REVISES: [], LINK_CITES: []})
            if kind in entry:
                entry[kind].append(str(target_id))
    return result


async def load_memory_links(
    db: AsyncSession,
    memory_ids: List[str],
    entity_id: Optional[str] = None,
    include_withdrawn: bool = False,
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """
    Every memory link touching the given memories, both directions, for
    rendering their marker lines (memory_context.format_memory_link_lines).

    Returns {memory_id: {"revises", "cites", "revised_by", "cited_by"}}
    for the ids that have any link (an unlinked memory is absent). The
    first two are what the memory — a reflection — points at, in the
    order the entity gave them; the last two are the reflections
    pointing at it, oldest first. Each end is {"id", "created_at",
    "role", "speaker_entity_id", "sibling_session", "state"}, where
    state is None, "released" (memory_status), or "withdrawn" (its
    conversation is archived).

    The two directions treat an archived conversation differently, on
    purpose. A reflection's OWN sources that left view are labeled, never
    dropped: the reflection is still in view, and its sources line is
    part of it as written. But a reflection that has itself been
    withdrawn is dropped from the reverse direction: archiving takes a
    conversation where something went wrong off every memory surface,
    and a correction saved there — possibly "correcting" something true
    — must not outlive the archive as a pointer on the memory it
    revised, which the entity could not even open. include_withdrawn
    keeps them (the researcher's memory browser, which sees archived
    conversations anyway).

    entity_id scopes the reverse direction to the reflections that
    entity wrote: "what have I concluded from this" is about one's own
    conclusions, and another entity's reflections are not part of this
    one's experience. The forward direction is the reflection as
    written and is shown whole.

    Not guarded: a failure raises into the caller like any other read on
    its session. (A swallowed error here would leave the caller's
    transaction aborted on Postgres, turning a lost marker into a lost
    memory link on the next statement.)
    """
    ids = list(dict.fromkeys(str(mid) for mid in memory_ids if mid))
    if not ids:
        return {}

    def end_of(message: Message, archived: bool) -> Dict[str, Any]:
        if archived:
            state = "withdrawn"
        elif message.memory_status == "released":
            state = "released"
        else:
            state = None
        return {
            "id": str(message.id),
            "created_at": message.created_at.isoformat() if message.created_at else "",
            "role": message.role.value,
            "speaker_entity_id": message.speaker_entity_id,
            "sibling_session": message.sibling_session,
            "state": state,
        }

    links: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def bucket(memory_id: str) -> Dict[str, List[Dict[str, Any]]]:
        return links.setdefault(
            memory_id, {"revises": [], "cites": [], "revised_by": [], "cited_by": []}
        )

    for start in range(0, len(ids), LINK_QUERY_CHUNK):
        chunk = ids[start:start + LINK_QUERY_CHUNK]
        outgoing = await db.execute(
            select(MemoryLink, Message, Conversation.is_archived)
            .join(Message, Message.id == MemoryLink.target_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(MemoryLink.reflection_id.in_(chunk))
            .order_by(MemoryLink.reflection_id, MemoryLink.kind, MemoryLink.position)
        )
        for link, target, archived in outgoing.all():
            key = "revises" if link.kind == LINK_REVISES else "cites"
            bucket(str(link.reflection_id))[key].append(end_of(target, bool(archived)))

        incoming_query = (
            select(MemoryLink, Message, Conversation.is_archived)
            .join(Message, Message.id == MemoryLink.reflection_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(MemoryLink.target_id.in_(chunk))
            .order_by(Message.created_at, Message.id)
        )
        if entity_id:
            incoming_query = incoming_query.where(Message.speaker_entity_id == entity_id)
        if not include_withdrawn:
            incoming_query = incoming_query.where(Conversation.is_archived == False)
        incoming = await db.execute(incoming_query)
        for link, reflection, archived in incoming.all():
            key = "revised_by" if link.kind == LINK_REVISES else "cited_by"
            ends = bucket(str(link.target_id))[key]
            # One reflection can both revise and cite a memory; each
            # kind lists it once
            if all(end["id"] != str(reflection.id) for end in ends):
                ends.append(end_of(reflection, bool(archived)))
    return links


async def load_memory_link_annotations(
    db: AsyncSession,
    memories: List[Tuple[str, str]],
    entity_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    The rendered marker lines for each (memory_id, role), newline-joined
    — the `annotation` a [MEMORY] context marker carries under its
    header. Memories without links are absent.
    """
    if not memories:
        return {}
    links = await load_memory_links(
        db, [memory_id for memory_id, _ in memories], entity_id=entity_id
    )
    labels = {entity.index_name: entity.label for entity in settings.get_entities()}
    annotations: Dict[str, str] = {}
    for memory_id, role in memories:
        lines = format_memory_link_lines(
            links.get(str(memory_id)), role, entity_id=entity_id, entity_labels=labels
        )
        if lines:
            annotations[str(memory_id)] = "\n".join(lines)
    return annotations


async def check_link_target(
    db: AsyncSession,
    message: Message,
    entity_id: Optional[str],
    kind: str,
    include_released: bool,
) -> Tuple[Optional[Conversation], Optional[str]]:
    """
    Whether `message` may be the target of a `kind` link written for
    entity_id: (its conversation, None) when it may, (None, the reason)
    when it may not. The one rule for every path that writes memory links
    — memory_save and the seed import alike — so no path can write a link
    the tool would refuse.

    The readers' visibility rules apply: the target must be a memory row,
    in the entity's own experience (its conversations and the
    multi-entity ones it takes part in), not in an archived conversation,
    and not released unless include_released says so. `revises` also
    requires the entity's OWN words — a reflection it saved or something
    it said: the entity's account of its own past is its to give, and the
    human's words (or another entity's) are not its to mark wrong.
    `cites` takes anything in the experience; citing marks nothing.
    """
    short_id = str(message.id)[:8]
    if message.role not in MEMORY_ROLES:
        return None, f"Memory {short_id} is a {message.role.value} row, not a memory."
    conversation = (await db.execute(
        select(Conversation).where(
            Conversation.id == message.conversation_id,
            memory_service._entity_experience_clause(entity_id),
        )
    )).scalar_one_or_none()
    if conversation is None:
        return None, f"Memory {short_id} is not part of your experience."
    if conversation.is_archived:
        return None, (
            f"Memory {short_id} is in an archived conversation, which is withdrawn "
            "from every memory surface."
        )
    if message.memory_status == "released" and not include_released:
        return None, (
            f"Memory {short_id} is released. Pass include_released=true to link it "
            "anyway, or restore it first with memory_release undo=true."
        )
    if kind == LINK_REVISES:
        if message.role == MessageRole.HUMAN:
            return None, (
                f"Memory {short_id} is the human's words; revises marks only your "
                "own memories (to point at what someone else said, use cites)."
            )
        # A reflection names its author; so does an assistant row in a
        # multi-entity room. In a single-entity room the experience check
        # above already says the words are this entity's.
        if (
            message.role == MessageRole.REFLECTION
            or conversation.entity_id == "multi-entity"
        ) and message.speaker_entity_id != entity_id:
            return None, (
                f"Memory {short_id} is another entity's; revises marks only your "
                "own memories."
            )
    return conversation, None


def stage_memory_links(
    db: AsyncSession,
    reflection_id: str,
    revises: Optional[List[str]] = None,
    cites: Optional[List[str]] = None,
    created_at: Optional[datetime] = None,
) -> int:
    """
    Stage a reflection's links on the session (the caller commits, in the
    same transaction as the reflection itself). Returns how many were
    staged. Targets are full ids, already validated; duplicates within a
    kind are dropped, order kept.
    """
    staged = 0
    for kind, targets in ((LINK_REVISES, revises), (LINK_CITES, cites)):
        for position, target_id in enumerate(dict.fromkeys(targets or [])):
            db.add(MemoryLink(
                reflection_id=str(reflection_id),
                target_id=str(target_id),
                kind=kind,
                position=position,
                created_at=created_at or datetime.utcnow(),
            ))
            staged += 1
    return staged


def created_before(created_at_value: Any, cutoff: datetime) -> bool:
    """
    Whether a memory's created_at metadata (ISO string, naive UTC) predates a
    naive-UTC cutoff. Missing or unparseable timestamps count as NOT before —
    callers use this to decide whether a same-conversation memory escapes the
    compaction-boundary exclusion, and an unknown creation time must fail
    closed (stay excluded) rather than leak a possibly-in-context memory.
    """
    try:
        parsed = datetime.fromisoformat(str(created_at_value))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed < cutoff


async def run_pinecone(fn, *args, **kwargs):
    """
    Run a blocking Pinecone SDK call off the event loop.

    The Pinecone client is synchronous. Called directly from an async function
    it blocks the entire event loop for the duration of the network round trip,
    so a slow or stalled Pinecone request freezes every other request the
    server is handling — not just the turn that made the call, and with nothing
    logged until it returns.

    This moves the call to a worker thread. Note that it bounds the damage
    rather than the call: a request that never returns still leaks its thread,
    because there is no way to cancel a blocking socket read from outside. The
    server stays responsive either way.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)



class ReadCursor(NamedTuple):
    """A decoded archive-reader page cursor (see MemoryService.encode_read_cursor)."""

    created_at: datetime
    message_id: str
    backward: bool
    page: int


class MemoryService:
    """
    Memory service using Pinecone with integrated inference (llama-text-embed-v2).

    Pinecone handles embedding generation internally - we pass raw text and
    Pinecone generates embeddings using the model configured on the index.

    Includes caching for:
    - Memory search results (short TTL to reduce Pinecone API calls)
    - Full memory content lookups (medium TTL to reduce DB queries)
    """
    def __init__(self):
        self._pc = None
        self._indexes: Dict[str, Any] = {}  # Cache for multiple indexes
        self._cache_service = None

    @property
    def cache(self):
        """Lazy load cache service to avoid circular imports."""
        if self._cache_service is None:
            from app.services.cache_service import cache_service
            self._cache_service = cache_service
        return self._cache_service

    @property
    def pc(self):
        if self._pc is None and settings.pinecone_api_key:
            self._pc = Pinecone(api_key=settings.pinecone_api_key)
        return self._pc

    def get_index(self, entity_id: Optional[str] = None):
        """
        Get a Pinecone index by entity_id (index name).

        Args:
            entity_id: The Pinecone index name. If None, uses the default entity.

        Returns:
            Pinecone Index object or None if not configured.
        """
        if not self.pc:
            return None

        # Use default entity if not specified
        if entity_id is None:
            entity = settings.get_default_entity()
            entity_id = entity.index_name
        else:
            entity = settings.get_entity_by_index(entity_id)

        # Return cached index if available
        if entity_id in self._indexes:
            return self._indexes[entity_id]

        # Create and cache new index connection
        try:
            # Use host if provided in entity config (required for serverless indexes)
            if entity and entity.host:
                index = self.pc.Index(entity_id, host=entity.host)
            else:
                index = self.pc.Index(entity_id)
            self._indexes[entity_id] = index
            return index
        except Exception as e:
            logger.error(f"Error connecting to Pinecone index '{entity_id}': {e}")
            return None

    @property
    def index(self):
        """Backward-compatible property that returns the default index."""
        return self.get_index(None)

    def is_configured(self, entity_id: Optional[str] = None) -> bool:
        """
        Check if Pinecone is configured and the specified entity's index is available.

        Args:
            entity_id: The entity to check. If None, checks if Pinecone is configured at all.
        """
        if not settings.pinecone_api_key:
            return False

        if entity_id is None:
            return True

        # Verify the entity exists in configuration
        return settings.get_entity_by_index(entity_id) is not None

    async def store_memory(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        created_at: datetime,
        entity_id: Optional[str] = None,
        sibling_session: Optional[str] = None,
        model: Optional[str] = None,
        revises: Optional[List[str]] = None,
        cites: Optional[List[str]] = None,
    ) -> bool:
        """
        Store a message as a memory in the vector database.

        Uses Pinecone's integrated inference - pass raw text and Pinecone
        generates embeddings using the model configured on the index.

        Args:
            message_id: Unique ID for the message
            conversation_id: ID of the conversation
            role: Message role (human/assistant)
            content: Message content
            created_at: When the message was created
            entity_id: The Pinecone index name. If None, uses default entity.
            sibling_session: For role="sibling" records (inter-session
                messages in Claude Code conversations): the sending session's
                display name, kept in metadata so restore-from-vectors can
                recover the provenance column.
            model: The model that produced the message (Message.model),
                mirrored into metadata purely so restore-from-vectors can
                recover the column. Retrieval never reads it from here.
            revises / cites: For a reflection, the full ids of the memories
                it revises or cites (MemoryLink rows, issues #366/#368),
                mirrored into metadata purely so restore-from-vectors can
                recover the links. Retrieval never reads them from here.

        Returns True if successful, False otherwise.
        """
        logger.debug(f"store_memory called for entity_id={entity_id}")

        if not self.is_configured():
            logger.debug("store_memory: Pinecone not configured")
            return False

        index = self.get_index(entity_id)
        if index is None:
            logger.warning(f"store_memory: Failed to get index for entity_id={entity_id}")
            return False

        logger.debug("store_memory: Got index, upserting with integrated inference...")

        # Create content preview for metadata
        content_preview = content[:200] if len(content) > 200 else content

        try:
            # Use Pinecone's integrated inference - upsert_records passes raw text
            # and Pinecone generates embeddings using the index's configured model
            record = {
                "_id": message_id,
                "text": content,  # Pinecone will embed this using llama-text-embed-v2
                "conversation_id": conversation_id,
                "created_at": created_at.isoformat(),
                "role": role,
                "content_preview": content_preview,
                "times_retrieved": 0,
            }
            # Only set when present — Pinecone metadata fields can't be null
            if sibling_session:
                record["sibling_session"] = sibling_session
            if model:
                record["model"] = model
            if revises:
                record["revises"] = [str(mid) for mid in revises]
            if cites:
                record["cites"] = [str(mid) for mid in cites]
            await run_pinecone(
                index.upsert_records,
                namespace="",
                records=[record],
            )
            logger.debug("store_memory: Successfully upserted to Pinecone")
            return True
        except Exception as e:
            logger.error(f"Error storing memory: {e}")
            return False

    async def search_memories(
        self,
        query: str,
        top_k: int = None,
        exclude_conversation_id: Optional[str] = None,
        exclude_ids: Optional[set] = None,
        entity_id: Optional[str] = None,
        use_cache: bool = True,
        similarity_threshold: Optional[float] = None,
        role_filter: Optional[str] = None,
        exclude_conversation_after: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant memories using semantic similarity.

        Uses Pinecone's integrated inference - pass raw text and Pinecone
        generates embeddings using the model configured on the index.

        Results are cached for 60 seconds by default to reduce Pinecone API calls
        during multi-turn conversations with similar queries.

        Args:
            query: The query text to search for
            top_k: Number of results to return (defaults to config)
            exclude_conversation_id: Conversation ID to exclude from results
            exclude_ids: Set of message IDs to exclude (for deduplication)
            entity_id: The Pinecone index name. If None, uses default entity.
            use_cache: Whether to use cached results (default True)
            similarity_threshold: Minimum score to include a result. Defaults
                to settings.similarity_threshold (automatic retrieval from chat
                messages); deliberate queries (memory_query tool, memory browser
                search) pass the lower settings.query_similarity_threshold since
                short search strings carry sparser semantic content.
            role_filter: Restrict results by who authored the memory:
                "human" (the human's messages), "ai" (the entity's own
                messages and reflections, plus other entities' messages in
                multi-entity conversations), "reflection" (only the
                entity's saved reflections), or None/"all" for no
                restriction. Applied as a Pinecone metadata filter so the
                top_k slots are filled with matching memories rather than
                shrunk by post-filtering.
            exclude_conversation_after: Only meaningful together with
                exclude_conversation_id: narrow that exclusion to memories
                created at or after this naive-UTC moment. Used for compacted
                Claude Code conversations, where messages recorded before the
                last compaction survive in context only as a paraphrased
                summary and so become eligible for retrieval again. Pinecone
                cannot range-filter the ISO-string created_at metadata, so
                with a cutoff the conversation exclusion moves from the
                Pinecone filter to the Python post-filter (fetch_k headroom
                absorbs the discarded candidates).

        Returns:
            List of memory dicts with id, content, score, metadata
        """
        if not self.is_configured():
            return []

        index = self.get_index(entity_id)
        if index is None:
            return []

        top_k = top_k or settings.retrieval_top_k
        exclude_ids = exclude_ids or set()
        if similarity_threshold is None:
            similarity_threshold = settings.similarity_threshold
        role_filter = normalize_role_filter(role_filter)

        # Normalize exclude_conversation_id to string for consistent comparison
        exclude_conv_id_normalized = str(exclude_conversation_id) if exclude_conversation_id else None
        # The compaction cutoff only narrows a conversation exclusion
        if exclude_conv_id_normalized is None:
            exclude_conversation_after = None

        # Check cache first (before exclude_ids filtering, which happens post-query)
        # Cache key doesn't include exclude_ids since we filter after retrieval
        if use_cache:
            cached_results = self.cache.get_search_results(
                query=query,
                entity_id=entity_id,
                top_k=top_k * 2,  # Cache the larger fetch_k results
                exclude_conversation_id=exclude_conv_id_normalized,
                role_filter=role_filter,
                exclude_conversation_after=exclude_conversation_after,
            )
            if cached_results is not None:
                logger.info(f"[MEMORY] Cache HIT for entity={entity_id}")
                # Apply exclude_ids filter to cached results
                # (cached results are pre-threshold, so a per-call threshold is safe)
                filtered = []
                for mem in cached_results:
                    if mem["id"] in exclude_ids:
                        continue
                    if mem["score"] < similarity_threshold:
                        continue
                    # The role filter is part of the cache key, so entries are
                    # already narrowed; re-checking costs nothing and keeps a
                    # stale or mis-keyed entry from widening the result
                    if not role_matches_filter(mem.get("role"), role_filter):
                        continue
                    filtered.append(mem)
                    if len(filtered) >= top_k:
                        break
                return filtered

        try:
            # Query more than we need to allow for filtering by exclude_ids
            fetch_k = top_k * 2

            logger.info(f"[MEMORY] Searching memories: threshold={similarity_threshold}, top_k={top_k}, entity={entity_id}")

            # Build search query with optional metadata filter
            search_query = {
                "inputs": {"text": query},  # Pinecone will embed this
                "top_k": fetch_k,
            }

            # Add metadata filters at Pinecone level (multiple keys are ANDed).
            # This is more efficient than filtering in Python after retrieval,
            # and for the role filter it also means fetch_k candidates all match
            # instead of most of them being discarded afterwards.
            metadata_filter = {}

            # With a compaction cutoff the exclusion is conditional on
            # created_at, which Pinecone can't range-filter (ISO strings), so
            # it happens in the hit loop below instead
            if exclude_conv_id_normalized and exclude_conversation_after is None:
                metadata_filter["conversation_id"] = {"$ne": exclude_conv_id_normalized}
                logger.debug(f"[MEMORY] Excluding conversation_id: {exclude_conv_id_normalized}")

            if role_filter == ROLE_FILTER_HUMAN:
                metadata_filter["role"] = {"$eq": ROLE_FILTER_HUMAN}
            elif role_filter == ROLE_FILTER_AI:
                # Everything that isn't the human: "assistant", "reflection",
                # and other entities' speaker labels
                metadata_filter["role"] = {"$ne": ROLE_FILTER_HUMAN}
            elif role_filter == ROLE_FILTER_REFLECTION:
                metadata_filter["role"] = {"$eq": ROLE_FILTER_REFLECTION}
            if role_filter:
                logger.debug(f"[MEMORY] Restricting to role_filter={role_filter}")

            if metadata_filter:
                search_query["filter"] = metadata_filter

            # Use Pinecone's integrated inference - search with raw text
            results = await run_pinecone(
                index.search,
                namespace="",
                query=search_query,
            )

            all_memories = []
            # Pinecone inference search returns: results.result.hits
            # Each hit has: _id, _score, fields (metadata dict)
            hits = results.result.hits if hasattr(results, 'result') and hasattr(results.result, 'hits') else []
            logger.info(f"[MEMORY] Pinecone returned {len(hits)} candidate memories")

            for hit in hits:
                # Get hit properties via to_dict()
                hit_dict = hit.to_dict() if hasattr(hit, 'to_dict') else hit
                match_id = hit_dict.get('_id')
                match_score = hit_dict.get('_score', 0)
                fields = hit_dict.get('fields', {})
                conv_id = fields.get("conversation_id")
                role = fields.get("role")

                # Skip roles the caller filtered out (fallback for the Pinecone
                # filter above; also covers records written before the role
                # metadata field existed)
                if not role_matches_filter(role, role_filter):
                    logger.info(
                        f"[MEMORY] SKIP (role fallback, filter={role_filter}): "
                        f"{match_id[:8]}... role={role}"
                    )
                    continue

                # Skip same conversation (this filter is part of cache key)
                # Ensure both values are strings for consistent comparison
                conv_id_str = str(conv_id) if conv_id else None
                if exclude_conv_id_normalized and conv_id_str == exclude_conv_id_normalized:
                    if exclude_conversation_after is None:
                        # This should not happen if Pinecone filter worked - log at INFO level for debugging
                        logger.info(f"[MEMORY] SKIP (same conversation fallback): {match_id[:8]}... conv_id={conv_id_str}")
                        continue
                    # Compaction boundary: only the post-compaction slice of
                    # the conversation is still verbatim in context, so only
                    # it stays excluded
                    if not created_before(fields.get("created_at"), exclude_conversation_after):
                        logger.info(
                            f"[MEMORY] SKIP (same conversation, post-compaction): "
                            f"{match_id[:8]}... conv_id={conv_id_str}"
                        )
                        continue

                all_memories.append({
                    "id": match_id,
                    "score": match_score,
                    "conversation_id": conv_id,
                    "created_at": fields.get("created_at"),
                    "role": role,
                    "content_preview": fields.get("content_preview"),
                    "times_retrieved": fields.get("times_retrieved", 0),
                })

            # Cache the raw results (before exclude_ids and threshold filtering)
            if use_cache:
                self.cache.set_search_results(
                    query=query,
                    entity_id=entity_id,
                    top_k=fetch_k,
                    exclude_conversation_id=exclude_conv_id_normalized,
                    results=all_memories,
                    role_filter=role_filter,
                    exclude_conversation_after=exclude_conversation_after,
                )

            # Now apply exclude_ids and threshold filtering
            memories = []
            for mem in all_memories:
                if mem["score"] < similarity_threshold:
                    logger.debug(f"SKIP (score {mem['score']:.3f} < {similarity_threshold}): {mem['id'][:8]}...")
                    continue

                if mem["id"] in exclude_ids:
                    logger.debug(f"SKIP (already retrieved): {mem['id'][:8]}...")
                    continue

                logger.debug(f"INCLUDE: {mem['id'][:8]}... score={mem['score']:.3f}")
                memories.append(mem)

                if len(memories) >= top_k:
                    break

            logger.info(f"[MEMORY] Search complete: returning {len(memories)} memories (filtered from {len(hits)} candidates)")
            return memories
        except Exception as e:
            logger.error(f"Error searching memories: {e}")
            return []

    async def get_recent_reflections(
        self,
        db: AsyncSession,
        entity_id: Optional[str] = None,
        limit: Optional[int] = None,
        exclude_conversation_id: Optional[str] = None,
        exclude_ids: Optional[Set[str]] = None,
        since: Optional[datetime] = None,
        exclude_conversation_after: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get the most recently created reflection memories, purely by recency.

        Backs the first-turn recent-reflections feature
        (settings.recent_reflections_enabled). Unlike search_memories this
        never touches Pinecone: selection is by created_at alone, regardless
        of semantic content.

        Excludes released reflections, reflections from archived
        conversations, the conversation in exclude_conversation_id
        (reflections are never retrievable in the conversation where they
        were saved), and any IDs in exclude_ids (deduplication against
        semantically retrieved memories, which reflections remain eligible
        for).

        Args:
            db: Database session
            entity_id: The entity whose reflections to fetch (reflections are
                attributed via Message.speaker_entity_id). If None, uses the
                default entity.
            limit: Max reflections to return (defaults to
                settings.recent_reflections_count)
            exclude_conversation_id: Conversation ID to exclude
            exclude_ids: Set of memory IDs to exclude
            since: Only return reflections created strictly after this
                (naive UTC) moment. Backs memory_query's recent mode, where
                the entity catches up on reflections saved by concurrent
                sessions after a given point in time.
            exclude_conversation_after: Only meaningful together with
                exclude_conversation_id: narrow that exclusion to reflections
                created at or after this naive-UTC moment. Used for compacted
                Claude Code conversations — reflections saved there before
                the last compaction are no longer verbatim in context, so
                the never-retrievable-where-saved rule stops applying to
                them (matching the post-compact injection, which re-shows
                them deliberately).

        Returns:
            List of memory dicts (same shape as get_full_memory_content),
            most recent first.
        """
        if limit is None:
            limit = settings.recent_reflections_count
        if limit <= 0:
            return []

        # Reflections always carry the saving entity's index name in
        # speaker_entity_id (memory_save requires entity context), so scope
        # by that rather than by conversation ownership — it is also correct
        # for multi-entity conversations.
        if entity_id is None:
            default_entity = settings.get_default_entity()
            entity_id = default_entity.index_name if default_entity else None
        if entity_id is None:
            return []

        query = (
            select(Message, Conversation.source)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.role == MessageRole.REFLECTION,
                Message.speaker_entity_id == entity_id,
                # NULL status is a normal memory; only "released" is excluded
                or_(Message.memory_status.is_(None), Message.memory_status != "released"),
                Conversation.is_archived == False,
            )
        )
        if exclude_conversation_id:
            if exclude_conversation_after is not None:
                query = query.where(or_(
                    Message.conversation_id != str(exclude_conversation_id),
                    Message.created_at < exclude_conversation_after,
                ))
            else:
                query = query.where(Message.conversation_id != str(exclude_conversation_id))
        if exclude_ids:
            query = query.where(Message.id.not_in([str(mid) for mid in exclude_ids]))
        if since is not None:
            query = query.where(Message.created_at > since)

        query = query.order_by(Message.created_at.desc()).limit(limit)

        result = await db.execute(query)
        rows = result.all()

        reflections = [
            {
                "id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "role": m.role.value,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "times_retrieved": m.times_retrieved,
                "last_retrieved_at": m.last_retrieved_at.isoformat() if m.last_retrieved_at else None,
                "memory_status": m.memory_status,
                "source": conversation_source or "native",
                # Substrate provenance — surfaced only on explicit request
                # (memory_query include_model), never in context markers
                "model": m.model,
            }
            for m, conversation_source in rows
        ]
        logger.info(
            f"[MEMORY] Recent reflections: found {len(reflections)} for entity={entity_id} (limit={limit})"
        )
        return reflections

    async def get_full_memory_content(
        self,
        message_id: str,
        db: AsyncSession,
        use_cache: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Get full memory content from the SQL database.

        Results are cached for 5 minutes to reduce database queries when
        the same memory is accessed multiple times.

        Args:
            message_id: The message/memory ID to fetch
            db: Database session
            use_cache: Whether to use cached results (default True)

        Returns:
            Dict with memory content or None if not found
        """
        # Normalize ID to string
        message_id = str(message_id)

        # Check cache first
        if use_cache:
            cached_content = self.cache.get_memory_content(message_id)
            if cached_content is not None:
                return cached_content

        result = await db.execute(
            select(Message, Conversation.source)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.id == message_id)
        )
        row = result.first()
        message, conversation_source = row if row else (None, None)

        if message:
            content_dict = {
                "id": str(message.id),
                "conversation_id": str(message.conversation_id),
                "role": message.role.value,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
                "times_retrieved": message.times_retrieved,
                "last_retrieved_at": message.last_retrieved_at.isoformat() if message.last_retrieved_at else None,
                "memory_status": message.memory_status,
                "status_set_by": message.status_set_by,
                "status_set_at": message.status_set_at.isoformat() if message.status_set_at else None,
                # Which experience the memory was formed in ("native" or
                # "claude_code") — rendered into memory markers and tool
                # output so the entity can see a memory's provenance
                "source": conversation_source or "native",
                # Inter-session provenance: the sibling Claude Code session
                # that authored this message, when it records a SendMessage
                # delivery (None everywhere else) — rendered into role labels
                "sibling_session": message.sibling_session,
                # Which model produced the message (None = not recorded).
                # Carried for memory_query's opt-in include_model only —
                # the marker renderer deliberately never reads it.
                "model": message.model,
            }
            # Cache the result
            if use_cache:
                self.cache.set_memory_content(message_id, content_dict)
            return content_dict
        else:
            # Log details for debugging orphaned Pinecone records
            logger.warning(f"[MEMORY] Message ID '{message_id}' not found in SQL database (may be orphaned in Pinecone)")
        return None

    async def update_retrieval_count(
        self,
        message_id: str,
        conversation_id: str,
        db: AsyncSession,
        entity_id: Optional[str] = None,
        create_link: bool = True,
        link_retrieved_at: Optional[datetime] = None,
        link_annotation: Optional[str] = None,
    ) -> bool:
        """
        Update retrieval count for a memory.

        - Increments times_retrieved in SQL database
        - Updates last_retrieved_at timestamp
        - Creates ConversationMemoryLink for tracking (unless create_link=False)
        - Updates Pinecone metadata

        Args:
            message_id: The message/memory ID
            conversation_id: The conversation this retrieval is for
            db: Database session
            entity_id: The Pinecone index name. If None, uses default entity.
            create_link: Whether to record a ConversationMemoryLink. The link
                drives session-reload re-insertion of the memory into the
                conversation context, so it must only be created for memories
                actually inserted as context messages. memory_query passes
                False: its results live inside the persisted tool_result
                blocks, and re-inserting them on reload would both duplicate
                them and change the rebuilt context mid-history, breaking
                prompt-cache stability.
            link_retrieved_at: Timestamp to store on the link. Session-reload
                re-insertion interleaves memories with messages by this value,
                so callers pass a timestamp anchored just before the turn's
                human message row (see session_helpers.make_link_timestamper)
                to reproduce the live insertion position (memories precede the
                message that triggered them). Defaults to now.
            link_annotation: The memory's link marker lines exactly as the
                live context rendered them (ConversationMemoryLink.annotation),
                so reload reproduces the marker instead of re-rendering it
                from links that may have changed since.
        """
        try:
            # Update SQL record
            await db.execute(
                update(Message)
                .where(Message.id == message_id)
                .values(
                    times_retrieved=Message.times_retrieved + 1,
                    last_retrieved_at=datetime.utcnow()
                )
            )

            # Create link record for deduplication tracking
            # Include entity_id for multi-entity conversation isolation
            if create_link:
                link = ConversationMemoryLink(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    entity_id=entity_id,
                    retrieved_at=link_retrieved_at or datetime.utcnow(),
                    annotation=link_annotation or None,
                )
                db.add(link)
            await db.commit()

            # Update Pinecone metadata (get current count and increment)
            if self.is_configured():
                index = self.get_index(entity_id)
                if index:
                    try:
                        # Fetch current vector to get metadata
                        fetch_result = await run_pinecone(index.fetch, ids=[message_id])
                        if message_id in fetch_result.vectors:
                            current_count = fetch_result.vectors[message_id].metadata.get("times_retrieved", 0)
                            # Update with incremented count
                            await run_pinecone(
                                index.update,
                                id=message_id,
                                set_metadata={"times_retrieved": current_count + 1}
                            )
                    except Exception as e:
                        logger.warning(f"Could not update Pinecone metadata: {e}")

            return True
        except Exception as e:
            logger.error(f"Error updating retrieval count: {e}")
            await db.rollback()
            return False

    async def record_memory_link(
        self,
        message_id: str,
        conversation_id: str,
        db: AsyncSession,
        entity_id: Optional[str] = None,
        retrieved_at: Optional[datetime] = None,
        annotation: Optional[str] = None,
    ) -> bool:
        """
        Create a ConversationMemoryLink WITHOUT touching retrieval tracking.

        Used for recency-based injections (recent reflections on the first
        turn): the link keeps session-reload re-insertion and deduplication
        working, but times_retrieved / last_retrieved_at stay reserved for
        semantic recall so recency injections don't inflate significance.

        Args:
            message_id: The message/memory ID
            conversation_id: The conversation the memory was injected into
            db: Database session
            entity_id: Entity scoping for multi-entity conversation isolation
            retrieved_at: Timestamp to store on the link (see
                update_retrieval_count.link_retrieved_at). Defaults to now.
            annotation: The marker lines the live context rendered (see
                update_retrieval_count.link_annotation).
        """
        try:
            link = ConversationMemoryLink(
                conversation_id=conversation_id,
                message_id=message_id,
                entity_id=entity_id,
                retrieved_at=retrieved_at or datetime.utcnow(),
                annotation=annotation or None,
            )
            db.add(link)
            await db.commit()
            return True
        except Exception as e:
            logger.error(f"Error recording memory link: {e}")
            await db.rollback()
            return False

    async def refresh_memory_link_timestamps(
        self,
        conversation_id: str,
        message_ids: List[str],
        db: AsyncSession,
        entity_id: Optional[str] = None,
    ) -> bool:
        """
        Bump retrieved_at to now on existing ConversationMemoryLinks.

        Claude Code only: the post-compact injection re-shows reflections
        that may already be linked, and after a compaction only links newer
        than last_compacted_at count as "in context" — so re-showing must
        move the link past that boundary or dedup would immediately treat
        the just-injected reflection as out of view. Never use this for
        native conversations: there retrieved_at drives where a session
        reload re-inserts the memory into the rebuilt context, and moving
        it would relocate the memory mid-history and bust the prompt cache.
        """
        if not message_ids:
            return True
        try:
            query = (
                update(ConversationMemoryLink)
                .where(
                    ConversationMemoryLink.conversation_id == conversation_id,
                    ConversationMemoryLink.message_id.in_(
                        [str(mid) for mid in message_ids]
                    ),
                )
                .values(retrieved_at=datetime.utcnow())
            )
            if entity_id is not None:
                query = query.where(ConversationMemoryLink.entity_id == entity_id)
            await db.execute(query)
            await db.commit()
            return True
        except Exception as e:
            logger.error(f"Error refreshing memory link timestamps: {e}")
            await db.rollback()
            return False

    async def resolve_memory_id_prefixes(
        self,
        db: AsyncSession,
        prefixes: List[str],
    ) -> List[str]:
        """
        Resolve short memory ID prefixes (as shown in memory_query output and
        memory markers) back to full message IDs. Prefixes that match zero or
        multiple messages are dropped — callers use the result for retrieval
        deduplication, where a missing ID just means one memory isn't
        deduplicated rather than an error.
        """
        resolved: List[str] = []
        for prefix in dict.fromkeys(prefixes):  # dedupe, preserve order
            try:
                result = await db.execute(
                    select(Message.id).where(Message.id.like(f"{prefix}%")).limit(2)
                )
                matches = result.scalars().all()
                if len(matches) == 1:
                    resolved.append(str(matches[0]))
                elif len(matches) > 1:
                    logger.debug(f"Memory ID prefix '{prefix}' is ambiguous; skipping for dedup")
            except Exception as e:
                logger.warning(f"Error resolving memory ID prefix '{prefix}': {e}")
        return resolved

    async def get_retrieved_ids_for_conversation(
        self,
        conversation_id: str,
        db: AsyncSession,
        entity_id: Optional[str] = None,
        linked_after: Optional[datetime] = None,
    ) -> set:
        """
        Get all message IDs that have been retrieved in a conversation.
        Used for session deduplication.

        Args:
            conversation_id: The conversation to get retrieved IDs for
            db: Database session
            entity_id: Optional entity filter. For multi-entity conversations,
                      this filters to only memories retrieved by that entity.
                      If None, returns all retrieved memories (backward compatible).
            linked_after: Only count links with retrieved_at strictly after
                      this naive-UTC moment. Claude Code callers pass the
                      conversation's last_compacted_at: links from before a
                      compaction no longer represent in-context content, so
                      those memories become eligible for retrieval again
                      (the post-compact injection refreshes the links for
                      what it re-shows — see refresh_memory_link_timestamps).

        Note: Returns string IDs to match Pinecone's string ID format.
        """
        query = select(ConversationMemoryLink.message_id).where(
            ConversationMemoryLink.conversation_id == conversation_id
        )

        # For multi-entity conversations, filter by entity_id
        if entity_id is not None:
            query = query.where(ConversationMemoryLink.entity_id == entity_id)

        if linked_after is not None:
            query = query.where(ConversationMemoryLink.retrieved_at > linked_after)

        result = await db.execute(query)
        # Convert to strings to match Pinecone's string ID format
        return set(str(row[0]) for row in result.fetchall())

    async def get_retrieved_memories_with_timestamps(
        self,
        conversation_id: str,
        db: AsyncSession,
        entity_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all retrieved memory IDs with their retrieval timestamps.
        Used for re-inserting memories at their original positions on session reload.

        Args:
            conversation_id: The conversation to get retrieved memories for
            db: Database session
            entity_id: Optional entity filter for multi-entity conversations

        Returns:
            List of dicts with 'message_id', 'retrieved_at', and 'annotation'
            (the link marker lines rendered at insertion, or None) for each
            retrieved memory
        """
        query = select(
            ConversationMemoryLink.message_id,
            ConversationMemoryLink.retrieved_at,
            ConversationMemoryLink.annotation,
        ).where(
            ConversationMemoryLink.conversation_id == conversation_id
        ).order_by(ConversationMemoryLink.retrieved_at)

        if entity_id is not None:
            query = query.where(ConversationMemoryLink.entity_id == entity_id)

        result = await db.execute(query)
        return [
            {"message_id": str(row[0]), "retrieved_at": row[1], "annotation": row[2]}
            for row in result.fetchall()
        ]

    async def cleanup_memory_query_links(
        self,
        db: AsyncSession,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """
        Delete stale ConversationMemoryLinks created by memory_query calls.

        memory_query used to record a ConversationMemoryLink for every result
        (it no longer does — see update_retrieval_count.create_link). Those
        legacy links make session reload inject the query results into the
        rebuilt context as [MEMORY] messages the live context never contained,
        which duplicates them and breaks prompt-cache stability on the first
        turn after any reload.

        Query results are identified from the persisted tool exchanges: the
        memory-ID prefixes in "--- Memory <id[:8]> (...)" markers inside
        tool_result blocks answering a memory_query tool_use. A link is
        deleted when its message_id matches a prefix from the same
        conversation (scoped to the querying entity when the tool_use has a
        speaker_entity_id, so multi-entity participants' own links survive).

        Known limitation: if a memory surfaced in memory_query output AND was
        legitimately auto-retrieved in the same conversation (possible only
        when the query saw it while it was out of context), its auto link is
        removed too. The content remains visible in the persisted tool_result,
        and subsequent reloads stay self-consistent.

        SQL-only — works even when Pinecone is not configured.
        """
        marker_re = re.compile(r"--- Memory ([0-9a-fA-F]{8}) \(")

        # Map memory_query tool_use ids to their conversation and speaker
        result = await db.execute(
            select(Message).where(Message.role == MessageRole.TOOL_USE)
        )
        query_tool_use: Dict[str, tuple] = {}
        for msg in result.scalars().all():
            blocks = msg.content_blocks
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if block.get("type") == "tool_use" and block.get("name") == "memory_query":
                    query_tool_use[block.get("id")] = (
                        str(msg.conversation_id),
                        msg.speaker_entity_id,
                    )

        # Collect memory-ID prefixes from the matching tool_result blocks,
        # grouped by (conversation, querying entity)
        prefixes_by_scope: Dict[tuple, Set[str]] = {}
        if query_tool_use:
            result = await db.execute(
                select(Message).where(Message.role == MessageRole.TOOL_RESULT)
            )
            for msg in result.scalars().all():
                blocks = msg.content_blocks
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if block.get("type") != "tool_result":
                        continue
                    scope = query_tool_use.get(block.get("tool_use_id"))
                    if scope is None:
                        continue
                    content = block.get("content")
                    if not isinstance(content, str):
                        continue
                    found = marker_re.findall(content)
                    if found:
                        prefixes_by_scope.setdefault(scope, set()).update(found)

        # Delete links whose memory matches a query-result prefix
        links_matched = 0
        links_deleted = 0
        for (conversation_id, speaker_entity_id), prefixes in prefixes_by_scope.items():
            link_query = select(ConversationMemoryLink).where(
                ConversationMemoryLink.conversation_id == conversation_id
            )
            # Multi-entity tool_use rows carry the querying entity; scope the
            # deletion to its links so other participants' links for the same
            # memory survive. Single-entity rows have no speaker — the whole
            # conversation belongs to one entity, so no extra filter.
            if speaker_entity_id is not None:
                link_query = link_query.where(
                    ConversationMemoryLink.entity_id == speaker_entity_id
                )
            result = await db.execute(link_query)
            for link in result.scalars().all():
                if any(str(link.message_id).startswith(p) for p in prefixes):
                    links_matched += 1
                    if not dry_run:
                        await db.delete(link)
                        links_deleted += 1

        if not dry_run:
            await db.commit()

        summary = {
            "dry_run": dry_run,
            "conversations_with_query_results": len(
                {conv for (conv, _entity) in prefixes_by_scope}
            ),
            "links_matched": links_matched,
            "links_deleted": links_deleted,
        }
        logger.info(f"[MEMORY] memory_query link cleanup: {summary}")
        return summary

    async def get_archived_conversation_ids(
        self,
        db: AsyncSession,
        entity_id: Optional[str] = None,
    ) -> Set[str]:
        """
        Get IDs of all archived conversations relevant to a specific entity.

        Used to filter out memories from archived conversations during retrieval.

        This method handles three cases:
        1. Single-entity conversations where entity_id matches
        2. Multi-entity conversations where the entity is a participant
        3. Legacy conversations with NULL entity_id (only for the default entity)

        Args:
            db: Database session
            entity_id: Optional entity filter. If provided, returns archived
                       conversations where this entity's memories would be stored.
        """
        archived_ids: Set[str] = set()

        if entity_id is None:
            # No filter - return all archived conversations
            query = select(Conversation.id).where(Conversation.is_archived == True)
            result = await db.execute(query)
            return set(row[0] for row in result.fetchall())

        # Case 1: Single-entity conversations with matching entity_id
        single_entity_query = select(Conversation.id).where(
            Conversation.is_archived == True,
            Conversation.entity_id == entity_id
        )
        result = await db.execute(single_entity_query)
        archived_ids.update(row[0] for row in result.fetchall())

        # Case 2: Multi-entity conversations where this entity is a participant
        # These have entity_id = "multi-entity" but store memories in each participant's index
        multi_entity_query = select(Conversation.id).where(
            Conversation.is_archived == True,
            Conversation.entity_id == "multi-entity"
        ).join(
            ConversationEntity,
            ConversationEntity.conversation_id == Conversation.id
        ).where(
            ConversationEntity.entity_id == entity_id
        )
        result = await db.execute(multi_entity_query)
        archived_ids.update(row[0] for row in result.fetchall())

        # Case 3: Legacy conversations with NULL entity_id (for default entity only)
        # These have their memories stored in the default entity's Pinecone index
        default_entity = settings.get_default_entity()
        if default_entity and default_entity.index_name == entity_id:
            null_entity_query = select(Conversation.id).where(
                Conversation.is_archived == True,
                Conversation.entity_id.is_(None)
            )
            result = await db.execute(null_entity_query)
            archived_ids.update(row[0] for row in result.fetchall())

        return archived_ids

    async def set_memory_status(
        self,
        message_id: str,
        status: Optional[str],
        db: AsyncSession,
        *,
        set_by: str,
    ) -> bool:
        """
        Set or clear a memory's status ("pinned", "released", or None).

        Pinned memories are exempt from age-based significance decay.
        Released memories are excluded from retrieval (but kept in storage,
        so the status can be reversed).

        set_by ("entity" or "researcher") is recorded with the write time
        on the row (status_set_by / status_set_at) — for clears as much as
        for sets, since a researcher clearing the entity's release is
        exactly the change the entity's session-start notice must report.

        The status lives in SQL (source of truth); retrieval paths read it via
        get_full_memory_content, so we invalidate that cache here.
        """
        if status not in (None, "pinned", "released"):
            raise ValueError(f"Invalid memory status: {status}")
        if set_by not in VALID_STATUS_SETTERS:
            raise ValueError(f"Invalid status setter: {set_by}")

        try:
            await db.execute(
                update(Message)
                .where(Message.id == message_id)
                .values(
                    memory_status=status,
                    status_set_by=set_by,
                    status_set_at=datetime.utcnow(),
                )
            )
            await db.commit()
            self.cache.invalidate_memory_content(str(message_id))
            logger.info(
                f"[MEMORY] Set memory_status={status} for {str(message_id)[:8]}... (by {set_by})"
            )
            return True
        except Exception as e:
            logger.error(f"Error setting memory status: {e}")
            await db.rollback()
            return False

    def _entity_experience_clause(self, entity_id: str):
        """
        Filter (for a query joined to Conversation) selecting the
        conversations that make up this entity's experience: its own
        single-entity conversations, multi-entity conversations it
        participates in, and — for the default entity — legacy conversations
        with a NULL entity_id. The same three cases
        get_archived_conversation_ids enumerates.
        """
        participant = select(ConversationEntity.conversation_id).where(
            ConversationEntity.entity_id == entity_id
        )
        clauses = [
            Conversation.entity_id == entity_id,
            and_(
                Conversation.entity_id == "multi-entity",
                Conversation.id.in_(participant),
            ),
        ]
        default_entity = settings.get_default_entity()
        if default_entity and default_entity.index_name == entity_id:
            clauses.append(Conversation.entity_id.is_(None))
        return or_(*clauses)

    @staticmethod
    def _sql_role_clause(role_filter: Optional[str]):
        """
        The SQL-side equivalent of search_memories' Pinecone role filter:
        "human" is the human's rows, "reflection" the entity's memory_save
        rows, and "ai" everything else (assistant rows, including other
        entities' in multi-entity conversations and inter-session letters).
        None matches every memory role.
        """
        role_filter = normalize_role_filter(role_filter)
        if role_filter == ROLE_FILTER_HUMAN:
            return Message.role == MessageRole.HUMAN
        if role_filter == ROLE_FILTER_REFLECTION:
            return Message.role == MessageRole.REFLECTION
        if role_filter == ROLE_FILTER_AI:
            return Message.role != MessageRole.HUMAN
        return Message.role.in_(MEMORY_ROLES)

    def _released_conditions(self, entity_id: str, role_filter: Optional[str]) -> list:
        return [
            Message.memory_status == "released",
            Message.role.in_(MEMORY_ROLES),
            self._sql_role_clause(role_filter),
            self._entity_experience_clause(entity_id),
        ]

    async def get_released_memories(
        self,
        db: AsyncSession,
        entity_id: str,
        limit: int,
        exclude_conversation_id: Optional[str] = None,
        exclude_ids: Optional[Set[str]] = None,
        since: Optional[datetime] = None,
        role_filter: Optional[str] = None,
        exclude_conversation_after: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        The entity's released memories, most recently released first.

        Backs memory_query mode="released": the entity's own review channel
        for what it (or the researcher) withdrew from retrieval, so a release
        is reversible by the one who can still see it. Pure SQL — released
        memories are excluded from vector retrieval, and this is curation,
        not recall, so nothing here touches times_retrieved.

        Whoever released a memory, it is listed (status_set_by says who);
        legacy releases with no recorded release time sort last, and are
        excluded when `since` is given (it bounds the release time,
        status_set_at). Exclusions mirror get_recent_reflections: the
        current conversation (narrowed to its post-compaction slice via
        exclude_conversation_after) and any ids already in view.
        """
        if limit <= 0:
            return []

        query = (
            select(Message, Conversation.source)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*self._released_conditions(entity_id, role_filter))
        )
        if exclude_conversation_id:
            if exclude_conversation_after is not None:
                query = query.where(or_(
                    Message.conversation_id != str(exclude_conversation_id),
                    Message.created_at < exclude_conversation_after,
                ))
            else:
                query = query.where(Message.conversation_id != str(exclude_conversation_id))
        if exclude_ids:
            query = query.where(Message.id.not_in([str(mid) for mid in exclude_ids]))
        if since is not None:
            query = query.where(Message.status_set_at > since)

        query = query.order_by(
            Message.status_set_at.desc().nulls_last(),
            Message.created_at.desc(),
        ).limit(limit)

        result = await db.execute(query)
        rows = result.all()
        memories = [
            {
                "id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "role": m.role.value,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "times_retrieved": m.times_retrieved,
                "last_retrieved_at": m.last_retrieved_at.isoformat() if m.last_retrieved_at else None,
                "memory_status": m.memory_status,
                "status_set_by": m.status_set_by,
                "status_set_at": m.status_set_at.isoformat() if m.status_set_at else None,
                "source": conversation_source or "native",
                "model": m.model,
            }
            for m, conversation_source in rows
        ]
        logger.info(
            f"[MEMORY] Released memories: found {len(memories)} for entity={entity_id} (limit={limit})"
        )
        return memories

    async def count_released_memories(
        self,
        db: AsyncSession,
        entity_id: str,
        role_filter: Optional[str] = None,
    ) -> int:
        """How many of the entity's memories are currently released (no exclusions)."""
        query = (
            select(func.count(Message.id))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*self._released_conditions(entity_id, role_filter))
        )
        result = await db.execute(query)
        return int(result.scalar_one() or 0)

    # ------------------------------------------------------------------
    # Archive readers (issue #343): the record in order, verbatim
    # ------------------------------------------------------------------
    #
    # Everything else here reaches the archive by similarity; these read it
    # by position (a span, the neighbors of one row) or by the exact words
    # (find_messages). Pure SQL over Message — no Pinecone, no ranking, no
    # retrieval tracking — ordered by (created_at, id) so a page is stable
    # and a cursor can resume it. Nothing is ever truncated: a single
    # message larger than the page budget comes back alone, whole.

    # Tags a cursor carries after its sort key (issue #351). The direction
    # tag means a cursor is never resumed in the other direction by mistake:
    # the two directions read the key from opposite sides, and a forward
    # cursor fed to a backward call would re-show the page it came from.
    # The page tag numbers the page the cursor came from, so a walk can be
    # capped by page count (the readers' max_pages) even though every page
    # is its own call.
    CURSOR_BACKWARD_TAG = "backward"
    CURSOR_PAGE_TAG = "page="

    @classmethod
    def encode_read_cursor(
        cls, created_at: datetime, message_id: str, backward: bool = False, page: int = 1
    ) -> str:
        """
        A page cursor, readable on purpose: the sort key of the row at the
        page's leading edge — the last row shown reading forward, the
        oldest row shown reading backward (then tagged with the direction)
        — and the number of the page it came from.
        """
        cursor = f"{created_at.isoformat()}|{message_id}"
        if backward:
            cursor += f"|{cls.CURSOR_BACKWARD_TAG}"
        return f"{cursor}|{cls.CURSOR_PAGE_TAG}{int(page)}"

    @classmethod
    def decode_read_cursor(cls, cursor: str) -> Optional["ReadCursor"]:
        """
        The inverse of encode_read_cursor: a ReadCursor (created_at,
        message_id, backward, page); None when the cursor is malformed. A
        cursor with no page tag (built by hand) counts as page 1.
        """
        try:
            parts = str(cursor).strip().split("|")
            if len(parts) < 2:
                return None
            stamp, message_id = parts[0], parts[1]
            parsed = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            return None
        backward = False
        page = 1
        for tag in parts[2:]:
            if tag == cls.CURSOR_BACKWARD_TAG:
                backward = True
            elif tag.startswith(cls.CURSOR_PAGE_TAG) and tag[len(cls.CURSOR_PAGE_TAG):].isdigit():
                page = max(1, int(tag[len(cls.CURSOR_PAGE_TAG):]))
            else:
                return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        if not message_id:
            return None
        return ReadCursor(parsed, message_id, backward, page)

    @staticmethod
    def _after_key(created_at: datetime, message_id: str):
        """Rows sorting strictly after (created_at, message_id)."""
        return or_(
            Message.created_at > created_at,
            and_(Message.created_at == created_at, Message.id > message_id),
        )

    @staticmethod
    def _before_key(created_at: datetime, message_id: str):
        """Rows sorting strictly before (created_at, message_id)."""
        return or_(
            Message.created_at < created_at,
            and_(Message.created_at == created_at, Message.id < message_id),
        )

    @staticmethod
    def _row_in_context(
        message: Message,
        in_context_ids: Optional[Set[str]],
        live_conversation_id: Optional[str],
        live_after: Optional[datetime],
    ) -> bool:
        """
        Whether a row's content is already in the reader's live context, so
        the page shows it as a pointer instead of repeating it: a memory
        retrieved into context from anywhere (in_context_ids), or one of the
        live conversation's own messages — all of them for a native session,
        and for a compacted Claude Code session only those created at or
        after the compaction boundary (earlier ones survive only as summary,
        which is exactly what a reader is for).
        """
        if in_context_ids and str(message.id) in in_context_ids:
            return True
        if live_conversation_id and str(message.conversation_id) == str(live_conversation_id):
            return live_after is None or message.created_at >= live_after
        return False

    @staticmethod
    def _archive_row(
        message: Message, conversation: Conversation, in_context: bool = False
    ) -> Dict[str, Any]:
        """The dict shape both archive readers return, one row per message."""
        return {
            "in_context": in_context,
            "id": str(message.id),
            "conversation_id": str(message.conversation_id),
            "conversation_title": conversation.title,
            "role": message.role.value,
            "content": message.content,
            "created_at": message.created_at.isoformat(),
            "token_count": message.token_count,
            "times_retrieved": message.times_retrieved,
            "memory_status": message.memory_status,
            "status_set_by": message.status_set_by,
            "status_set_at": message.status_set_at.isoformat() if message.status_set_at else None,
            "speaker_entity_id": message.speaker_entity_id,
            "sibling_session": message.sibling_session,
            "source": conversation.source or "native",
            "model": message.model,
        }

    # Page weight of a row rendered as an in-context pointer (header only)
    POINTER_TOKENS = 24

    @staticmethod
    def estimate_message_tokens(row: Dict[str, Any]) -> int:
        """
        The paging core's default row weight, when the caller passes no
        `weigh`: the stored token_count, or a length estimate when the row
        predates counting; a pointer's fixed weight when the row is already
        in context. The tools don't use it (issue #353): token_count is a
        tiktoken count of the content alone, and a page budgeted by it
        overran the harness's tool-result cap, so they weigh the row as
        rendered instead (memory_tools._page_weigher). Display/budgeting only.
        """
        if row.get("in_context"):
            return MemoryService.POINTER_TOKENS
        count = row.get("token_count")
        if count:
            return int(count)
        return max(1, len(row.get("content") or "") // 4)

    def _span_conditions(
        self,
        entity_id: str,
        role_filter: Optional[str],
        conversation_id: Optional[str],
        include_released: bool,
    ) -> list:
        conditions = [
            Message.role.in_(MEMORY_ROLES),
            self._sql_role_clause(role_filter),
            self._entity_experience_clause(entity_id),
            # Archived conversations are withdrawn from every memory surface:
            # archiving is how the researcher removes a conversation where
            # something went wrong, and a reader that showed it would undo that
            Conversation.is_archived == False,
        ]
        if conversation_id:
            conditions.append(Message.conversation_id == str(conversation_id))
        if not include_released:
            conditions.append(
                or_(Message.memory_status.is_(None), Message.memory_status != "released")
            )
        return conditions

    async def resolve_conversation_prefix(
        self,
        db: AsyncSession,
        entity_id: str,
        id_or_prefix: str,
    ) -> Tuple[Optional[Conversation], Optional[str], bool]:
        """
        Resolve a conversation id or its prefix (as memory_read prints them)
        to one of this entity's conversations. Returns (conversation, error,
        via_alias) — exactly one of the first two is None.

        A retired id resolves too (issue #359). When a late fork adoption
        merges a row away, its id lives on as a `ConversationIdAlias`, and
        it is an id the entity has been handed and may have written down —
        in a reflection, in notes, in a recipe it saved. "No conversation of
        yours found" would be false of it: the conversation exists, under
        another id. `via_alias` says so, so the caller can name the id the
        read actually landed on rather than redirect silently.

        Current ids win over retired ones at every step, and an exact match
        wins over a prefix, so a live conversation is never shadowed.
        """
        id_or_prefix = str(id_or_prefix or "").strip()
        if len(id_or_prefix) < 6:
            return None, "Conversation ID must be at least 6 characters.", False
        live_query = (
            select(Conversation)
            .where(
                Conversation.id.like(f"{id_or_prefix}%"),
                self._entity_experience_clause(entity_id),
                Conversation.is_archived == False,
            )
            .limit(5)
        )
        matches = (await db.execute(live_query)).scalars().all()
        exact = [c for c in matches if str(c.id) == id_or_prefix]
        if exact:
            return exact[0], None, False

        alias_query = (
            select(Conversation, ConversationIdAlias.alias_id)
            .join(
                ConversationIdAlias,
                ConversationIdAlias.conversation_id == Conversation.id,
            )
            .where(
                ConversationIdAlias.alias_id.like(f"{id_or_prefix}%"),
                self._entity_experience_clause(entity_id),
                Conversation.is_archived == False,
            )
            .limit(5)
        )
        alias_rows = (await db.execute(alias_query)).all()
        alias_exact = [conv for conv, alias_id in alias_rows if alias_id == id_or_prefix]
        if alias_exact:
            return alias_exact[0], None, True

        if len(matches) == 1:
            return matches[0], None, False
        if len(matches) > 1:
            ids = ", ".join(str(c.id)[:12] + "..." for c in matches)
            return None, (
                f"Conversation ID prefix '{id_or_prefix}' is ambiguous ({ids}). "
                "Use a longer prefix."
            ), False

        # Several aliases can name one conversation; that is not ambiguity
        alias_matches = {str(conv.id): conv for conv, _ in alias_rows}
        if len(alias_matches) == 1:
            return next(iter(alias_matches.values())), None, True
        if len(alias_matches) > 1:
            ids = ", ".join(cid[:12] + "..." for cid in alias_matches)
            return None, (
                f"Conversation ID prefix '{id_or_prefix}' is ambiguous ({ids}). "
                "Use a longer prefix."
            ), False
        return None, f"No conversation of yours found with ID '{id_or_prefix}'.", False

    async def _read_archive_page(
        self,
        db: AsyncSession,
        conditions: list,
        cursor: Optional[str],
        page_tokens: int,
        batch_size: int,
        in_context_ids: Optional[Set[str]],
        live_conversation_id: Optional[str],
        live_after: Optional[datetime],
        log_label: str,
        backward: bool = False,
        weigh: Optional[Callable[[Dict[str, Any]], int]] = None,
        entity_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        The paging core shared by read_messages_in_span and find_messages:
        every row matching `conditions` (a Message/Conversation join), in
        (created_at, id) order, one token-bounded page at a time. The page
        fills until adding the next row would exceed page_tokens, each row
        weighed by `weigh` (default estimate_message_tokens; the tools pass
        one that measures the row as rendered, issue #353); an oversized
        first row is returned alone and whole. Rows already in the reader's
        live context (see _row_in_context) come back flagged in_context so
        the tool renders them as pointers.

        `backward` (issue #351) reads from the other end: the page takes
        the newest rows not yet shown, walking toward the oldest, and the
        cursor marks the page's older edge. Its items still come back
        oldest-first, so a page reads like the archive in either direction.

        Each row carries its memory links ("links", load_memory_links'
        entry, reverse direction scoped to entity_id) before it is weighed,
        so the page budget counts the marker lines the tools print.

        Returns {"items", "total", "offset", "remaining", "next_cursor",
        "backward", "page"}: total is the row count under the same
        conditions, offset how many rows precede this page in archive order
        (so the page's positions are offset+1 .. offset+len(items) either
        way), remaining how many rows are still unread in the reading
        direction (later rows forward, earlier rows backward), next_cursor
        None at the end, page this page's number in the walk (1 without a
        cursor; the cursor's page + 1 with one).
        """
        total = int((await db.execute(
            select(func.count(Message.id))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*conditions)
        )).scalar_one() or 0)

        # Rows already shown on earlier pages: at or before the cursor key
        # reading forward, at or after it reading backward
        shown = 0
        position = self.decode_read_cursor(cursor) if cursor else None
        key = (position.created_at, position.message_id) if position is not None else None
        page_number = position.page + 1 if position is not None else 1
        if key is not None:
            beyond = self._after_key(*key) if backward else self._before_key(*key)
            shown = int((await db.execute(
                select(func.count(Message.id))
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(*conditions, or_(
                    beyond,
                    and_(Message.created_at == key[0], Message.id == key[1]),
                ))
            )).scalar_one() or 0)

        weigh = weigh or self.estimate_message_tokens
        items: List[Dict[str, Any]] = []
        used = 0
        next_cursor: Optional[str] = None
        exhausted = False
        while not exhausted and next_cursor is None:
            query = (
                select(Message, Conversation)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(*conditions)
            )
            if key is not None:
                query = query.where(
                    self._before_key(*key) if backward else self._after_key(*key)
                )
            if backward:
                query = query.order_by(Message.created_at.desc(), Message.id.desc())
            else:
                query = query.order_by(Message.created_at.asc(), Message.id.asc())
            rows = (await db.execute(query.limit(batch_size))).all()
            if len(rows) < batch_size:
                exhausted = True
            batch_links = await load_memory_links(
                db, [str(message.id) for message, _ in rows], entity_id=entity_id
            )
            for message, conversation in rows:
                row = self._archive_row(
                    message,
                    conversation,
                    in_context=self._row_in_context(
                        message, in_context_ids, live_conversation_id, live_after
                    ),
                )
                row["links"] = batch_links.get(str(message.id))
                weight = weigh(row)
                if items and used + weight > page_tokens:
                    # The page's leading edge: the last row taken, which is
                    # the oldest one when reading backward
                    last = items[-1]
                    next_cursor = self.encode_read_cursor(
                        datetime.fromisoformat(last["created_at"]), last["id"],
                        backward=backward, page=page_number,
                    )
                    break
                items.append(row)
                used += weight
                key = (message.created_at, str(message.id))
            if not rows:
                exhausted = True

        if backward:
            # Selected newest-first; rendered oldest-first like any page
            items.reverse()
            remaining = max(0, total - shown - len(items))
            offset = remaining
        else:
            offset = shown
            remaining = max(0, total - shown - len(items))

        logger.info(
            f"[MEMORY] {log_label}: {len(items)} of {total} rows "
            f"(page={page_number}, offset={offset}, remaining={remaining}, "
            f"direction={'backward' if backward else 'forward'}, tokens~{used})"
        )
        return {
            "items": items,
            "total": total,
            "offset": offset,
            "remaining": remaining,
            "next_cursor": next_cursor,
            "backward": backward,
            "page": page_number,
        }

    # Text-match modes for find_messages / the memory_find tool
    MATCH_PHRASE = "phrase"
    MATCH_ALL = "all"
    MATCH_ANY = "any"
    VALID_MATCH_MODES = (MATCH_PHRASE, MATCH_ALL, MATCH_ANY)

    @staticmethod
    def _text_pattern(words: List[str], whole_words: bool) -> str:
        """
        A regular expression matching the given words in sequence, each
        literal, any whitespace between them (so a quote that wrapped a
        line still matches), case-insensitive; bounded by non-word
        characters when whole_words, so a name is not found inside another
        word ("Sage" in "message"). Python-re syntax, which SQLite runs
        through SQLAlchemy's registered REGEXP function and Postgres reads
        as an ARE (`(?i)`, `\\s`, `\\w`, and the lookarounds are common to
        both; re.escape's backslash-punctuation escapes are literals in
        both).
        """
        body = r"\s+".join(re.escape(word) for word in words)
        if whole_words:
            body = r"(?<!\w)" + body + r"(?!\w)"
        return "(?i)" + body

    @classmethod
    def text_match_clause(cls, text: str, match: str = MATCH_PHRASE, whole_words: bool = True):
        """
        The SQL condition for "Message.content contains `text`", as the
        memory_find tool means it: a case-insensitive regular-expression
        match (see _text_pattern) with the text taken literally. `phrase`
        (default) matches its words in that order with any whitespace
        between them; `all` requires every word, in any order; `any`
        requires at least one. whole_words (default) matches only where
        the text begins and ends at a word boundary. Raises ValueError for
        empty text or an unknown mode.
        """
        text = str(text or "").strip()
        if not text:
            raise ValueError("text is empty")
        if match not in cls.VALID_MATCH_MODES:
            raise ValueError(f"unknown match mode '{match}'")
        words = text.split()
        if match == cls.MATCH_PHRASE:
            return Message.content.regexp_match(cls._text_pattern(words, whole_words))
        clauses = [
            Message.content.regexp_match(cls._text_pattern([word], whole_words))
            for word in words
        ]
        if len(clauses) == 1:
            return clauses[0]
        return and_(*clauses) if match == cls.MATCH_ALL else or_(*clauses)

    async def find_messages(
        self,
        db: AsyncSession,
        entity_id: str,
        text: str,
        match: str = MATCH_PHRASE,
        whole_words: bool = True,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        role_filter: Optional[str] = None,
        conversation_id: Optional[str] = None,
        include_released: bool = False,
        cursor: Optional[str] = None,
        page_tokens: int = 8000,
        batch_size: int = 200,
        in_context_ids: Optional[Set[str]] = None,
        live_conversation_id: Optional[str] = None,
        live_after: Optional[datetime] = None,
        backward: bool = False,
        weigh: Optional[Callable[[Dict[str, Any]], int]] = None,
    ) -> Dict[str, Any]:
        """
        Every message in the entity's archive whose content contains `text`
        (see text_match_clause; whole words by default), in (created_at, id) order, one
        token-bounded page at a time — the memory_find tool. The record by
        WORD, where read_messages_in_span is the record by position: pure
        SQL, no Pinecone, no ranking, no retrieval tracking, and the same
        visibility rules (the entity's experience, memory_query's role
        filter, optional single conversation, released skipped unless
        asked, archived hidden, in-context rows flagged for pointer
        rendering). `start` / `end` (naive UTC, inclusive) optionally bound
        the search; either may be None. `backward` pages from the newest
        match toward the oldest. Paging and the return shape:
        _read_archive_page. A total of 0 is a real answer — the words
        appear nowhere the entity can see — which similarity search cannot
        give.
        """
        conditions = self._span_conditions(
            entity_id, role_filter, conversation_id, include_released
        ) + [self.text_match_clause(text, match, whole_words)]
        if start is not None:
            conditions.append(Message.created_at >= start)
        if end is not None:
            conditions.append(Message.created_at <= end)
        return await self._read_archive_page(
            db, conditions, cursor, page_tokens, batch_size,
            in_context_ids, live_conversation_id, live_after,
            log_label=f"Archive find ({match}) for entity={entity_id}",
            backward=backward, weigh=weigh, entity_id=entity_id,
        )

    async def read_messages_in_span(
        self,
        db: AsyncSession,
        entity_id: str,
        start: Optional[datetime],
        end: Optional[datetime],
        role_filter: Optional[str] = None,
        conversation_id: Optional[str] = None,
        include_released: bool = False,
        cursor: Optional[str] = None,
        page_tokens: int = 8000,
        batch_size: int = 200,
        in_context_ids: Optional[Set[str]] = None,
        live_conversation_id: Optional[str] = None,
        live_after: Optional[datetime] = None,
        backward: bool = False,
        weigh: Optional[Callable[[Dict[str, Any]], int]] = None,
    ) -> Dict[str, Any]:
        """
        Read the entity's archive between two naive-UTC moments (inclusive;
        None leaves that end open), in (created_at, id) order, one
        token-bounded page at a time — or, with `backward`, from the newest
        row in the span toward the oldest (issue #351: the page a compacted
        session wants is the stretch just before the boundary).

        Backs the memory_read tool. Scope is the entity's experience
        (_entity_experience_clause) narrowed by the same role filter
        memory_query's `source` uses and optionally to one conversation.
        Released memories are skipped unless include_released (they were
        withdrawn on purpose) and archived conversations are hidden entirely,
        as everywhere else (archiving removes a conversation where something
        went wrong from every memory surface). The current conversation is
        NOT excluded, but rows whose content is already in live context
        (see _row_in_context) come back flagged in_context so the tool
        renders them as pointers — the page stays whole and in order without
        duplicating what the reader can already see. Nothing here touches
        times_retrieved. Paging and the return shape: _read_archive_page
        (total is the span's row count under the same filters).
        """
        conditions = self._span_conditions(
            entity_id, role_filter, conversation_id, include_released
        )
        if start is not None:
            conditions.append(Message.created_at >= start)
        if end is not None:
            conditions.append(Message.created_at <= end)
        return await self._read_archive_page(
            db, conditions, cursor, page_tokens, batch_size,
            in_context_ids, live_conversation_id, live_after,
            log_label=f"Archive read in span for entity={entity_id}",
            backward=backward, weigh=weigh, entity_id=entity_id,
        )

    async def read_message_neighbors(
        self,
        db: AsyncSession,
        message: Message,
        before: int = 2,
        after: int = 2,
        include_released: bool = False,
        in_context_ids: Optional[Set[str]] = None,
        live_conversation_id: Optional[str] = None,
        live_after: Optional[datetime] = None,
        entity_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        The messages immediately around one message in its own
        conversation, in order — the memory_neighbors tool. Same row shape
        and skip rules as read_messages_in_span (the requested message
        itself is always shown, released or not; an archived conversation
        returns no items and "archived": True). Returns {"items",
        "target_index", "hit_start", "hit_end"}: hit_start/hit_end say the
        window reached the conversation's first/last memory row.
        """
        conversation = (await db.execute(
            select(Conversation).where(Conversation.id == message.conversation_id)
        )).scalar_one()
        if conversation.is_archived:
            # Withdrawn from every memory surface, this one included
            return {
                "items": [], "target_index": -1, "hit_start": True, "hit_end": True,
                "archived": True,
            }
        conditions = [
            Message.role.in_(MEMORY_ROLES),
            Message.conversation_id == str(message.conversation_id),
        ]
        if not include_released:
            conditions.append(
                or_(Message.memory_status.is_(None), Message.memory_status != "released")
            )
        key = (message.created_at, str(message.id))

        earlier = (await db.execute(
            select(Message)
            .where(*conditions, self._before_key(*key))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(before + 1)
        )).scalars().all()
        later = (await db.execute(
            select(Message)
            .where(*conditions, self._after_key(*key))
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(after + 1)
        )).scalars().all()

        hit_start = len(earlier) <= before
        hit_end = len(later) <= after
        earlier = list(reversed(earlier[:before]))
        later = later[:after]

        def row(m: Message) -> Dict[str, Any]:
            return self._archive_row(
                m,
                conversation,
                in_context=self._row_in_context(
                    m, in_context_ids, live_conversation_id, live_after
                ),
            )

        items = [row(m) for m in earlier]
        target_index = len(items)
        items.append(row(message))
        items.extend(row(m) for m in later)
        window_links = await load_memory_links(
            db, [item["id"] for item in items], entity_id=entity_id
        )
        for item in items:
            item["links"] = window_links.get(item["id"])
        return {
            "items": items,
            "target_index": target_index,
            "hit_start": hit_start,
            "hit_end": hit_end,
        }

    async def link_memories_once(
        self,
        conversation_id: str,
        message_ids: List[str],
        db: AsyncSession,
        entity_id: Optional[str] = None,
    ) -> int:
        """
        Record a ConversationMemoryLink for each id not already linked to the
        conversation, and bump the timestamp of those that are, so all of
        them count as in view from now — the Claude Code dedup record for
        what an archive read just put on the table (never touches
        times_retrieved). Claude Code only: the timestamp refresh is unsafe
        for native conversations (see refresh_memory_link_timestamps).
        Returns how many new links were written.
        """
        ids = list(dict.fromkeys(str(mid) for mid in message_ids))
        if not ids:
            return 0
        already = await self.get_retrieved_ids_for_conversation(
            conversation_id, db, entity_id=entity_id
        )
        to_refresh = [mid for mid in ids if mid in already]
        if to_refresh:
            await self.refresh_memory_link_timestamps(
                conversation_id=conversation_id,
                message_ids=to_refresh,
                db=db,
                entity_id=entity_id,
            )
        written = 0
        for mid in ids:
            if mid in already:
                continue
            if await self.record_memory_link(
                message_id=mid, conversation_id=conversation_id, db=db, entity_id=entity_id
            ):
                written += 1
        return written

    async def get_last_session_anchor(
        self,
        db: AsyncSession,
        entity_id: str,
        exclude_conversation_id: Optional[str] = None,
        only_conversation_id: Optional[str] = None,
    ) -> Optional[datetime]:
        """
        The "since your last session" boundary for the researcher-change
        notices: the latest moment, across the entity's other conversations
        where it has spoken, at which a session's notice check ran. None
        when it has never spoken.

        Per conversation that moment is the start of the turn that carried
        the notice — the last turn-starting row (a human message, or a
        delivered inter-session letter) before the entity's first response
        there, or the first response itself when nothing precedes it. A
        session checks as that turn begins (natively just after the send
        timestamp the human row carries; in Claude Code at SessionStart,
        just before the first prompt is recorded), so each change lands
        either before the anchor and was told by that session, or after it
        and is told by the next. Anchoring on the response itself lost every
        change made during a first turn, which in Claude Code lasts as long
        as the agentic turn does.

        The latest such moment, not the first response of the latest
        speaker (issue #367's review): since fork adoption a standing room
        keeps one conversation for weeks and speaks on nearly every tick,
        so the latest speaker's first response is usually a room's birth,
        and every new session would hear every change since then again.

        Conversations without a response (an unspoken native tab, a Claude
        Code session that only fired SessionStart) never had a first turn
        to notify, so they cannot anchor. The residual window where a change
        goes untold is the gap between the check and the recorded turn
        start: none natively (the send timestamp comes first), and in Claude
        Code the seconds between SessionStart and the first prompt hook —
        longer in a CLI session opened well before its first prompt, or one
        whose first turn was an unrecorded wakeup (then the first response
        anchors).

        only_conversation_id narrows it to one conversation's own first-turn
        start — when that conversation was told, for a long-running Claude
        Code session re-told after a compaction (see
        build_researcher_change_notices' callers). None if it never spoke.
        """
        # An inter-session letter is recorded as an assistant row too
        # (sibling_session set) but is a delivery, not a turn this
        # conversation's session took — it never carried a first-turn notice
        spoke = and_(
            Message.role == MessageRole.ASSISTANT,
            Message.sibling_session.is_(None),
            or_(
                Message.speaker_entity_id.is_(None),
                Message.speaker_entity_id == entity_id,
            ),
        )
        conditions = [spoke, self._entity_experience_clause(entity_id)]
        if exclude_conversation_id:
            conditions.append(Message.conversation_id != str(exclude_conversation_id))
        if only_conversation_id:
            conditions.append(Message.conversation_id == str(only_conversation_id))

        first_responses = (
            select(
                Message.conversation_id.label("conversation_id"),
                func.min(Message.created_at).label("responded_at"),
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*conditions)
            .group_by(Message.conversation_id)
            .subquery()
        )
        starter = aliased(Message)
        turn_start = (
            select(func.max(starter.created_at))
            .where(
                starter.conversation_id == first_responses.c.conversation_id,
                starter.created_at < first_responses.c.responded_at,
                or_(
                    starter.role == MessageRole.HUMAN,
                    starter.sibling_session.isnot(None),
                ),
            )
            .correlate(first_responses)
            .scalar_subquery()
        )
        anchors = select(
            func.max(func.coalesce(turn_start, first_responses.c.responded_at))
        )
        return (await db.execute(anchors)).scalar_one_or_none()

    async def get_researcher_status_changes(
        self,
        db: AsyncSession,
        entity_id: str,
        since: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Memories of this entity whose status was last written by the
        researcher (set or cleared), optionally only after `since`, oldest
        change first. Each dict carries the current status and when the
        researcher wrote it, plus enough content for a snippet.
        """
        conditions = [
            Message.status_set_by == STATUS_SET_BY_RESEARCHER,
            Message.role.in_(MEMORY_ROLES),
            self._entity_experience_clause(entity_id),
        ]
        if since is not None:
            conditions.append(Message.status_set_at > since)
        query = (
            select(Message, Conversation.source)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*conditions)
            .order_by(Message.status_set_at.asc(), Message.created_at.asc())
        )
        rows = (await db.execute(query)).all()
        return [
            {
                "id": str(m.id),
                "role": m.role.value,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "memory_status": m.memory_status,
                "status_set_by": m.status_set_by,
                "status_set_at": m.status_set_at,
                "source": conversation_source or "native",
            }
            for m, conversation_source in rows
        ]

    async def build_status_change_notice(
        self,
        db: AsyncSession,
        entity_id: str,
        exclude_conversation_id: Optional[str] = None,
        anchor: Any = _ANCHOR_UNSET,
        since: str = SINCE_LAST_SESSION,
    ) -> Optional[str]:
        """
        The session-start notice of researcher-set status changes since the
        entity's last session (see get_last_session_anchor), or None when
        there are none. Silence means nothing changed: the notice is the
        entity's only way of learning that a choice about its own memory was
        made or reversed on its behalf, so it must never be swallowed —
        callers treat a failure here as loud, not as "no changes".
        """
        if anchor is _ANCHOR_UNSET:
            anchor = await self.get_last_session_anchor(
                db, entity_id, exclude_conversation_id=exclude_conversation_id
            )
        changes = await self.get_researcher_status_changes(db, entity_id, since=anchor)
        if not changes:
            return None
        logger.info(
            f"[MEMORY] Status notice: {len(changes)} researcher-set change(s) for "
            f"entity={entity_id} since {anchor.isoformat() if anchor else 'ever'}"
        )
        return format_status_change_notice(changes, since=since)

    async def get_researcher_archive_changes(
        self,
        db: AsyncSession,
        entity_id: str,
        since: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Conversations of this entity's experience whose archive state
        changed, optionally only after `since`, oldest change first (issue
        #367). Each dict carries the current state, when and with what note
        it changed, the conversation's source, and its span and size in
        memory messages — deliberately no title and no content.

        Every archive change is the researcher's (only the archive routes
        write is_archived), so unlike status changes there is no setter to
        filter on. Multi-entity conversations reach every participant
        through _entity_experience_clause.
        """
        conditions = [
            Conversation.archive_changed_at.isnot(None),
            self._entity_experience_clause(entity_id),
        ]
        if since is not None:
            conditions.append(Conversation.archive_changed_at > since)
        conversations = (await db.execute(
            select(Conversation)
            .where(*conditions)
            .order_by(Conversation.archive_changed_at.asc(), Conversation.id.asc())
        )).scalars().all()
        if not conversations:
            return []

        spans = {
            row[0]: row[1:]
            for row in (await db.execute(
                select(
                    Message.conversation_id,
                    func.min(Message.created_at),
                    func.max(Message.created_at),
                    func.count(Message.id),
                )
                .where(
                    Message.conversation_id.in_([c.id for c in conversations]),
                    Message.role.in_(MEMORY_ROLES),
                )
                .group_by(Message.conversation_id)
            )).all()
        }
        changes = []
        for conversation in conversations:
            first_at, last_at, message_count = spans.get(conversation.id, (None, None, 0))
            changes.append({
                "id": conversation.id,
                "is_archived": bool(conversation.is_archived),
                "archive_changed_at": conversation.archive_changed_at,
                "archive_note": conversation.archive_note,
                "source": conversation.source or "native",
                "created_at": conversation.created_at,
                "first_message_at": first_at,
                "last_message_at": last_at,
                "message_count": message_count,
            })
        return changes

    async def build_archive_change_notice(
        self,
        db: AsyncSession,
        entity_id: str,
        exclude_conversation_id: Optional[str] = None,
        anchor: Any = _ANCHOR_UNSET,
        since: str = SINCE_LAST_SESSION,
    ) -> Optional[str]:
        """
        The session-start notice of conversations archived or unarchived
        since the entity's last session, or None when there are none — the
        same anchor, and the same once-only and never-swallowed rules, as
        build_status_change_notice. `anchor` (both builders) takes one
        computed by the caller, so notices built together agree on it;
        `since` names that window in the header.
        """
        if anchor is _ANCHOR_UNSET:
            anchor = await self.get_last_session_anchor(
                db, entity_id, exclude_conversation_id=exclude_conversation_id
            )
        changes = await self.get_researcher_archive_changes(db, entity_id, since=anchor)
        if not changes:
            return None
        logger.info(
            f"[MEMORY] Archive notice: {len(changes)} archive change(s) for "
            f"entity={entity_id} since {anchor.isoformat() if anchor else 'ever'}"
        )
        return format_archive_change_notice(changes, since=since)

    async def build_researcher_change_notices(
        self,
        db: AsyncSession,
        entity_id: str,
        exclude_conversation_id: Optional[str] = None,
        anchor: Any = _ANCHOR_UNSET,
        since: str = SINCE_LAST_SESSION,
        report_nothing: bool = False,
    ) -> List[str]:
        """
        Every session-start notice of a change the researcher made to the
        entity's memory — memory status overrides, then archived/unarchived
        conversations — for the native first turn and the Claude Code
        blocks (identity, post-compaction) to deliver the same way.

        The window is "since your last session" (get_last_session_anchor,
        computed once so the two notices agree) unless the caller passes
        its own `anchor` and names it in `since` — a compacted Claude Code
        session is told what changed since *it* was last told.

        Never raises. Silence would have to mean "nothing changed", so each
        check that fails is reported in place of its notice, one failing
        doesn't hide the other, and a failed anchor is reported by both.
        With report_nothing, a check that ran and found nothing says so
        too, like a retrieval that matched nothing. Every caller passes it
        now: the native first turn could only afford to since its notice is
        stored and replayed on reload (SessionManager._inject_status_change_notice).
        """
        def status_failed(error: Exception) -> str:
            return (
                "[MEMORY STATUS NOTICE] Could not check for researcher-set "
                f"memory status changes {since} ({error}). If it matters, ask "
                'the researcher, or review with memory_query mode="released".'
            )

        def archive_failed(error: Exception) -> str:
            return (
                "[MEMORY ARCHIVE NOTICE] Could not check whether the researcher "
                f"withdrew or restored any of your conversations {since} "
                f"({error}). If it matters, ask the researcher."
            )

        if anchor is _ANCHOR_UNSET:
            try:
                anchor = await self.get_last_session_anchor(
                    db, entity_id, exclude_conversation_id=exclude_conversation_id
                )
            except Exception as e:
                logger.error(f"[MEMORY] Last-session anchor failed: {e}")
                return [status_failed(e), archive_failed(e)]

        notices: List[str] = []
        for build, failed, nothing, label in (
            (self.build_status_change_notice, status_failed,
             format_status_nothing_changed, "Status"),
            (self.build_archive_change_notice, archive_failed,
             format_archive_nothing_changed, "Archive"),
        ):
            try:
                notice = await build(
                    db, entity_id, exclude_conversation_id=exclude_conversation_id,
                    anchor=anchor, since=since,
                )
                if not notice and report_nothing:
                    notice = nothing(since)
            except Exception as e:
                logger.error(f"[MEMORY] {label}-change notice failed: {e}")
                notice = failed(e)
            if notice:
                notices.append(notice)
        return notices

    async def delete_memory(self, message_id: str, entity_id: Optional[str] = None) -> bool:
        """
        Delete a memory from the vector database.

        Args:
            message_id: The message/memory ID to delete
            entity_id: The Pinecone index name. If None, uses default entity.
        """
        if not self.is_configured():
            return False

        index = self.get_index(entity_id)
        if index is None:
            return False

        try:
            await run_pinecone(index.delete, ids=[message_id])
            return True
        except Exception as e:
            logger.error(f"Error deleting memory: {e}")
            return False

    async def repoint_memories(
        self,
        message_ids: List[str],
        conversation_id: str,
        entity_id: Optional[str] = None,
    ) -> int:
        """
        Move memories to another conversation in the vector store's
        metadata, for messages that changed conversation in SQL (issue
        #359's late fork adoption).

        `conversation_id` is not decoration there: same-conversation
        exclusion is a Pinecone metadata filter, so a memory left under a
        retired id would be recalled into the very room that just said it.
        Best-effort and non-fatal, like every Pinecone write here — SQL is
        the archive, and a rebuild restores the metadata from it.

        Returns how many records were updated.
        """
        ids = [str(mid) for mid in message_ids if mid]
        if not ids or not self.is_configured():
            return 0
        index = self.get_index(entity_id)
        if index is None:
            return 0
        updated = 0
        for message_id in ids:
            try:
                await run_pinecone(
                    index.update,
                    id=message_id,
                    set_metadata={"conversation_id": conversation_id},
                )
                updated += 1
            except Exception as e:
                logger.warning(
                    f"Could not repoint memory {message_id[:8]}... to "
                    f"conversation {conversation_id[:8]}...: {e}"
                )
        return updated

    async def list_all_pinecone_ids(
        self,
        entity_id: Optional[str] = None,
    ) -> List[str]:
        """
        List all record IDs stored in a Pinecone index.

        Uses pagination to handle large indexes.

        Args:
            entity_id: The Pinecone index name. If None, uses default entity.

        Returns:
            List of all record IDs in the index.
        """
        if not self.is_configured():
            return []

        index = self.get_index(entity_id)
        if index is None:
            return []

        all_ids = []
        try:
            # Use list_paginated() for explicit pagination control
            # This works better with serverless indexes using integrated inference
            pagination_token = None

            while True:
                if pagination_token:
                    response = await run_pinecone(
                        index.list_paginated,
                        namespace="",
                        limit=100,
                        pagination_token=pagination_token
                    )
                else:
                    response = await run_pinecone(
                        index.list_paginated,
                        namespace="",
                        limit=100
                    )

                # Extract IDs from the response
                if hasattr(response, 'vectors') and response.vectors:
                    for v in response.vectors:
                        if hasattr(v, 'id'):
                            all_ids.append(v.id)
                        elif isinstance(v, str):
                            all_ids.append(v)

                # Check for more pages
                if hasattr(response, 'pagination') and response.pagination and response.pagination.next:
                    pagination_token = response.pagination.next
                else:
                    break

            logger.info(f"[MEMORY] Listed {len(all_ids)} records from Pinecone entity={entity_id}")
            return all_ids
        except Exception as e:
            logger.error(f"Error listing Pinecone IDs for entity={entity_id}: {e}")
            import traceback
            logger.error(f"[MEMORY] Traceback: {traceback.format_exc()}")
            return []

    async def find_orphaned_records(
        self,
        db: AsyncSession,
        entity_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find records that exist in Pinecone but not in the SQL database.

        These orphans typically occur when:
        - A conversation or message was deleted but Pinecone deletion failed
        - Database was restored from an older backup
        - Records were created during development/testing

        Args:
            db: Database session
            entity_id: The Pinecone index name. If None, uses default entity.

        Returns:
            List of dicts with orphaned record info (id, metadata if available)
        """
        if not self.is_configured():
            return []

        index = self.get_index(entity_id)
        if index is None:
            return []

        # Get all IDs from Pinecone
        pinecone_ids = await self.list_all_pinecone_ids(entity_id)
        if not pinecone_ids:
            return []

        # Get all message IDs from SQL
        result = await db.execute(select(Message.id))
        sql_ids = set(str(row[0]) for row in result.fetchall())

        # Find orphans (in Pinecone but not in SQL)
        orphan_ids = [pid for pid in pinecone_ids if pid not in sql_ids]
        logger.info(f"[MEMORY] Found {len(orphan_ids)} orphaned records (Pinecone: {len(pinecone_ids)}, SQL: {len(sql_ids)})")

        # Fetch metadata for orphans if there aren't too many
        orphans = []
        if orphan_ids:
            try:
                # Fetch in batches of 100 to get metadata
                for i in range(0, len(orphan_ids), 100):
                    batch_ids = orphan_ids[i:i+100]
                    fetch_result = await run_pinecone(index.fetch, ids=batch_ids)

                    for oid in batch_ids:
                        orphan_info = {"id": oid, "metadata": None}
                        if oid in fetch_result.vectors:
                            metadata = fetch_result.vectors[oid].metadata
                            orphan_info["metadata"] = {
                                "conversation_id": metadata.get("conversation_id"),
                                "role": metadata.get("role"),
                                "created_at": metadata.get("created_at"),
                                "content_preview": metadata.get("content_preview", "")[:100],
                            }
                        orphans.append(orphan_info)
            except Exception as e:
                logger.warning(f"Could not fetch metadata for orphans: {e}")
                # Fall back to just IDs
                orphans = [{"id": oid, "metadata": None} for oid in orphan_ids]

        return orphans

    async def cleanup_orphaned_records(
        self,
        db: AsyncSession,
        entity_id: Optional[str] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """
        Clean up orphaned Pinecone records that don't exist in SQL.

        Args:
            db: Database session
            entity_id: The Pinecone index name. If None, uses default entity.
            dry_run: If True, only report what would be deleted. If False, actually delete.

        Returns:
            Dict with cleanup results: found, deleted, errors
        """
        result = {
            "entity_id": entity_id,
            "dry_run": dry_run,
            "orphans_found": 0,
            "orphans_deleted": 0,
            "errors": [],
            "orphan_ids": [],
        }

        if not self.is_configured():
            result["errors"].append("Pinecone not configured")
            return result

        index = self.get_index(entity_id)
        if index is None:
            result["errors"].append(f"Could not connect to index for entity={entity_id}")
            return result

        # Find orphaned records
        orphans = await self.find_orphaned_records(db, entity_id)
        result["orphans_found"] = len(orphans)
        result["orphan_ids"] = [o["id"] for o in orphans]

        if not orphans:
            logger.info(f"[MEMORY] No orphaned records found for entity={entity_id}")
            return result

        if dry_run:
            logger.info(f"[MEMORY] Dry run: would delete {len(orphans)} orphaned records")
            return result

        # Actually delete the orphans
        orphan_ids = [o["id"] for o in orphans]
        try:
            # Delete in batches of 100
            for i in range(0, len(orphan_ids), 100):
                batch_ids = orphan_ids[i:i+100]
                await run_pinecone(index.delete, ids=batch_ids)
                result["orphans_deleted"] += len(batch_ids)

            logger.info(f"[MEMORY] Deleted {result['orphans_deleted']} orphaned records from entity={entity_id}")
        except Exception as e:
            error_msg = f"Error deleting orphans: {e}"
            logger.error(f"[MEMORY] {error_msg}")
            result["errors"].append(error_msg)

        return result

    def test_connection(self) -> Dict[str, Any]:
        """
        Test Pinecone connection for all configured entities.

        Returns a dict with:
            - configured: bool - whether Pinecone is configured at all
            - entities: list of dicts with entity_id, success, message, and stats
        """
        result = {
            "configured": False,
            "entities": []
        }

        # Check if Pinecone is configured
        if not settings.pinecone_api_key:
            return result

        result["configured"] = True

        # Get all configured entities
        entities = settings.get_entities()
        if not entities:
            result["entities"].append({
                "entity_id": None,
                "success": False,
                "message": "No entities configured in PINECONE_INDEXES",
                "stats": None
            })
            return result

        # Test connection to each entity's index
        for entity in entities:
            entity_result = {
                "entity_id": entity.index_name,
                "label": entity.label,
                "host": entity.host,
                "success": False,
                "message": "",
                "stats": None
            }

            try:
                # Clear cached index to force fresh connection
                if entity.index_name in self._indexes:
                    del self._indexes[entity.index_name]

                index = self.get_index(entity.index_name)
                if index is None:
                    entity_result["message"] = "Failed to connect to index"
                else:
                    # Try to get index stats to verify connection works
                    stats = index.describe_index_stats()
                    entity_result["success"] = True
                    entity_result["message"] = "Connection successful"
                    entity_result["stats"] = {
                        "total_vector_count": stats.total_vector_count,
                        "dimension": stats.dimension,
                    }
            except Exception as e:
                entity_result["message"] = f"Connection error: {str(e)}"

            result["entities"].append(entity_result)

        return result


# Singleton instance
memory_service = MemoryService()
