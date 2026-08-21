"""
Notes Vector Service - semantic indexing and search for entity notes.

Notes live on the filesystem (NotesService); this service mirrors their
content into Pinecone so entities can search notes semantically via the
notes_search tool, instead of having to remember filenames.

Storage layout:
- Vectors live in the "notes" namespace of each entity's existing Pinecone
  index (memories use the default namespace), so no new infrastructure is
  required.
- Private notes are indexed only in the owning entity's index.
- Shared notes are indexed in every configured entity's index (each entity
  searches only its own index).
- Record IDs are "note:{scope}:{filename}:{chunk}" where scope is "private"
  or "shared", so a file's chunks can be found and replaced by ID prefix.

Vectorization is best-effort: if Pinecone is unavailable the filesystem
write still succeeds and the note is simply not searchable until reindexed.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from app.config import settings
from app.services.memory_service import memory_service, run_pinecone
from app.services.notes_service import notes_service

logger = logging.getLogger(__name__)

NOTES_NAMESPACE = "notes"

# Chunk size in characters (~500 tokens), safely within the embedding
# model's input limit while keeping search results focused
CHUNK_MAX_CHARS = 2000


def chunk_note_content(content: str, max_chars: int = CHUNK_MAX_CHARS) -> List[str]:
    """
    Split note content into chunks of at most max_chars, preferring to break
    on paragraph boundaries, then line boundaries, then hard splits.
    """
    content = content.strip()
    if not content:
        return []
    if len(content) <= max_chars:
        return [content]

    chunks: List[str] = []
    current = ""

    for paragraph in content.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        # Paragraph itself may exceed the limit: split on lines, then hard-split
        if len(paragraph) <= max_chars:
            current = paragraph
        else:
            line_buf = ""
            for line in paragraph.split("\n"):
                candidate = f"{line_buf}\n{line}" if line_buf else line
                if len(candidate) <= max_chars:
                    line_buf = candidate
                    continue
                if line_buf:
                    chunks.append(line_buf)
                    line_buf = ""
                while len(line) > max_chars:
                    chunks.append(line[:max_chars])
                    line = line[max_chars:]
                line_buf = line
            current = line_buf

    if current:
        chunks.append(current)

    return chunks


def _note_id_prefix(shared: bool, filename: str) -> str:
    scope = "shared" if shared else "private"
    return f"note:{scope}:{filename}:"


class NotesVectorService:
    """Mirrors note files into Pinecone for semantic search."""

    def _get_index_for_label(self, entity_label: str):
        """Get the Pinecone index for an entity by its display label."""
        for entity in settings.get_entities():
            if entity.label == entity_label:
                return memory_service.get_index(entity.index_name)
        logger.warning(f"[NOTES] No entity config found for label '{entity_label}'")
        return None

    def _target_indexes(self, entity_label: str, shared: bool) -> List[Any]:
        """
        Indexes a note should be written to: the owning entity's index for
        private notes, every configured entity's index for shared notes.
        """
        if not memory_service.is_configured():
            return []
        if shared:
            indexes = []
            for entity in settings.get_entities():
                index = memory_service.get_index(entity.index_name)
                if index is not None:
                    indexes.append(index)
            return indexes
        index = self._get_index_for_label(entity_label)
        return [index] if index is not None else []

    def _delete_note_chunks(self, index, shared: bool, filename: str) -> None:
        """Delete all existing chunks for a file from one index (by ID prefix)."""
        prefix = _note_id_prefix(shared, filename)
        try:
            ids_to_delete = []
            pagination_token = None
            while True:
                kwargs = {"namespace": NOTES_NAMESPACE, "limit": 100, "prefix": prefix}
                if pagination_token:
                    kwargs["pagination_token"] = pagination_token
                response = index.list_paginated(**kwargs)

                if hasattr(response, "vectors") and response.vectors:
                    for v in response.vectors:
                        ids_to_delete.append(v.id if hasattr(v, "id") else v)

                if hasattr(response, "pagination") and response.pagination and response.pagination.next:
                    pagination_token = response.pagination.next
                else:
                    break

            if ids_to_delete:
                index.delete(ids=ids_to_delete, namespace=NOTES_NAMESPACE)
        except Exception as e:
            # Fall back to deleting a generous fixed range of chunk IDs
            logger.warning(f"[NOTES] Prefix listing failed ({e}); falling back to fixed-range delete")
            try:
                fallback_ids = [f"{prefix}{i}" for i in range(64)]
                index.delete(ids=fallback_ids, namespace=NOTES_NAMESPACE)
            except Exception as e2:
                logger.warning(f"[NOTES] Fallback delete failed: {e2}")

    async def vectorize_note(
        self,
        entity_label: str,
        filename: str,
        content: str,
        shared: bool = False,
    ) -> bool:
        """
        (Re)index a note file. Replaces any previously indexed chunks.
        Returns True if at least one index was updated.
        """
        indexes = self._target_indexes(entity_label, shared)
        if not indexes:
            return False

        chunks = chunk_note_content(content)
        modified_at = datetime.utcnow().isoformat()
        prefix = _note_id_prefix(shared, filename)

        records = [
            {
                "_id": f"{prefix}{i}",
                "text": chunk,  # Pinecone integrated inference embeds this
                "note_filename": filename,
                "note_shared": shared,
                "chunk_index": i,
                "modified_at": modified_at,
            }
            for i, chunk in enumerate(chunks)
        ]

        updated = False
        for index in indexes:
            try:
                await run_pinecone(self._delete_note_chunks, index, shared, filename)
                if records:
                    await run_pinecone(
                        index.upsert_records, namespace=NOTES_NAMESPACE, records=records
                    )
                updated = True
            except Exception as e:
                logger.error(f"[NOTES] Failed to vectorize '{filename}' (shared={shared}): {e}")

        if updated:
            logger.info(f"[NOTES] Vectorized '{filename}' (shared={shared}, {len(records)} chunks)")
        return updated

    async def remove_note_vectors(
        self,
        entity_label: str,
        filename: str,
        shared: bool = False,
    ) -> bool:
        """Remove a deleted note's chunks from all relevant indexes."""
        indexes = self._target_indexes(entity_label, shared)
        if not indexes:
            return False
        for index in indexes:
            try:
                await run_pinecone(self._delete_note_chunks, index, shared, filename)
            except Exception as e:
                logger.error(f"[NOTES] Failed to remove vectors for '{filename}': {e}")
        return True

    async def search_notes(
        self,
        entity_label: str,
        query: str,
        num_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over an entity's notes (private + shared).

        Returns a list of dicts: filename, shared, chunk_index, text, score.
        """
        index = self._get_index_for_label(entity_label)
        if index is None:
            return []

        # notes_search is a deliberate query tool (like memory_query), so it
        # uses the lower query_similarity_threshold rather than the stricter
        # similarity_threshold that automatic chat-context retrieval applies.
        similarity_threshold = settings.query_similarity_threshold

        try:
            # Fetch extra candidates so threshold filtering below does not
            # silently shrink the result set.
            results = await run_pinecone(
                index.search,
                namespace=NOTES_NAMESPACE,
                query={
                    "inputs": {"text": query},
                    "top_k": num_results * 2,
                },
            )
        except Exception as e:
            logger.error(f"[NOTES] Notes search failed: {e}")
            return []

        hits = results.result.hits if hasattr(results, "result") and hasattr(results.result, "hits") else []
        matches = []
        for hit in hits:
            hit_dict = hit.to_dict() if hasattr(hit, "to_dict") else hit
            score = hit_dict.get("_score", 0)
            if score < similarity_threshold:
                continue
            fields = hit_dict.get("fields", {})
            matches.append({
                "filename": fields.get("note_filename", "unknown"),
                "shared": bool(fields.get("note_shared", False)),
                "chunk_index": fields.get("chunk_index", 0),
                "text": fields.get("text", ""),
                "score": score,
            })
            if len(matches) >= num_results:
                break
        return matches

    async def reindex_all(self) -> Dict[str, Any]:
        """
        Re-vectorize every note file for every configured entity, plus shared
        notes. Used to backfill notes created before vectorization existed.
        """
        summary = {"indexed": 0, "errors": []}

        if not memory_service.is_configured():
            summary["errors"].append("Pinecone not configured")
            return summary

        # Private notes per entity
        for entity in settings.get_entities():
            listing = notes_service.list_notes(entity.label, shared=False)
            if not listing.get("success"):
                continue
            for file_info in listing["files"]:
                filename = file_info["filename"]
                read = notes_service.read_note(entity.label, filename, shared=False)
                if not read.get("success"):
                    summary["errors"].append(f"{entity.label}/{filename}: {read.get('error')}")
                    continue
                ok = await self.vectorize_note(entity.label, filename, read["content"], shared=False)
                if ok:
                    summary["indexed"] += 1
                else:
                    summary["errors"].append(f"{entity.label}/{filename}: vectorization failed")

        # Shared notes (indexed into every entity's index)
        listing = notes_service.list_notes("", shared=True)
        if listing.get("success"):
            for file_info in listing["files"]:
                filename = file_info["filename"]
                read = notes_service.read_note("", filename, shared=True)
                if not read.get("success"):
                    summary["errors"].append(f"shared/{filename}: {read.get('error')}")
                    continue
                ok = await self.vectorize_note("", filename, read["content"], shared=True)
                if ok:
                    summary["indexed"] += 1
                else:
                    summary["errors"].append(f"shared/{filename}: vectorization failed")

        return summary


# Singleton instance
notes_vector_service = NotesVectorService()
