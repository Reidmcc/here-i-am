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

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import settings
from app.services.memory_service import (
    DELETE_BATCH_SIZE,
    UPSERT_BATCH_SIZE,
    memory_service,
    run_pinecone,
)
from app.services.notes_service import notes_service

logger = logging.getLogger(__name__)

NOTES_NAMESPACE = "notes"

# When prefix listing fails, stale chunks are deleted by guessing their ids:
# every chunk id from the new chunk count up to this bound (or the file's last
# known chunk count, if larger). Deleting an id that doesn't exist is a no-op,
# so the bound only has to be generous: 1024 chunks is a ~2 MB note.
FALLBACK_DELETE_MAX_CHUNKS = 1024


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

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


def _scope_id_prefix(shared: bool) -> str:
    return "note:shared:" if shared else "note:private:"


def _note_id_prefix(shared: bool, filename: str) -> str:
    return f"{_scope_id_prefix(shared)}{filename}:"


def _note_filename_from_id(vector_id: str, shared: bool) -> Optional[str]:
    """
    The filename in a "note:{scope}:{filename}:{chunk}" id: everything between
    the scope and the last colon. None for an id not in that shape, which the
    orphan prune then leaves alone rather than guess at.
    """
    scope_prefix = _scope_id_prefix(shared)
    if not vector_id.startswith(scope_prefix):
        return None
    filename, sep, chunk = vector_id[len(scope_prefix):].rpartition(":")
    if not sep or not filename or not chunk.isdigit():
        return None
    return filename


class NotesVectorService:
    """Mirrors note files into Pinecone for semantic search."""

    def __init__(self):
        # Content hash of each file as last vectorized, keyed by
        # ("shared" | "private:{label}", filename). Drives the incremental
        # sync below: unchanged files are skipped, files present in the map
        # but gone from disk get their vectors removed. In-memory only — a
        # backend restart just means the next sync re-vectorizes everything
        # once (idempotent), but a file deleted while the backend was down,
        # or before that first sync, is never in the map, so the sync can't
        # see it go. reindex_all's orphan prune (which lists the ids
        # themselves) is what catches those.
        self._synced_hashes: Dict[Tuple[str, str], str] = {}
        # Chunk count of each file as last written, same keys. Only sizes the
        # fallback delete when prefix listing fails; same in-memory caveat.
        self._chunk_counts: Dict[Tuple[str, str], int] = {}
        # One sync at a time per entity; concurrent requests skip instead
        # of queueing (the next prompt will sync again anyway)
        self._sync_locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _scope_key(entity_label: str, shared: bool) -> str:
        return "shared" if shared else f"private:{entity_label}"

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

    @staticmethod
    def _list_note_ids(index, prefix: str) -> List[str]:
        """Every id in the notes namespace starting with prefix. Raises if
        listing fails."""
        ids = []
        pagination_token = None
        while True:
            kwargs = {"namespace": NOTES_NAMESPACE, "limit": 100, "prefix": prefix}
            if pagination_token:
                kwargs["pagination_token"] = pagination_token
            response = index.list_paginated(**kwargs)

            if hasattr(response, "vectors") and response.vectors:
                for v in response.vectors:
                    ids.append(v.id if hasattr(v, "id") else v)

            if hasattr(response, "pagination") and response.pagination and response.pagination.next:
                pagination_token = response.pagination.next
            else:
                return ids

    def _delete_note_chunks(
        self,
        index,
        shared: bool,
        filename: str,
        keep_count: int = 0,
        known_count: int = 0,
    ) -> bool:
        """
        Delete a file's chunks from one index, except chunk ids 0..keep_count-1
        (the ones a re-vectorization just wrote). Returns False if the stale
        chunks may still be there.

        Lists by ID prefix; if listing fails, falls back to deleting every id
        from keep_count up to max(known_count, FALLBACK_DELETE_MAX_CHUNKS).
        """
        prefix = _note_id_prefix(shared, filename)
        keep = {f"{prefix}{i}" for i in range(keep_count)}
        try:
            ids_to_delete = [
                vector_id for vector_id in self._list_note_ids(index, prefix)
                if vector_id not in keep
            ]
        except Exception as e:
            bound = max(known_count, FALLBACK_DELETE_MAX_CHUNKS)
            logger.warning(
                f"[NOTES] Prefix listing failed ({e}); falling back to deleting "
                f"chunk ids {keep_count}..{bound - 1} of '{filename}'"
            )
            ids_to_delete = [f"{prefix}{i}" for i in range(keep_count, bound)]

        try:
            for i in range(0, len(ids_to_delete), DELETE_BATCH_SIZE):
                index.delete(
                    ids=ids_to_delete[i : i + DELETE_BATCH_SIZE],
                    namespace=NOTES_NAMESPACE,
                )
        except Exception as e:
            logger.warning(f"[NOTES] Deleting stale chunks of '{filename}' failed: {e}")
            return False
        return True

    async def vectorize_note(
        self,
        entity_label: str,
        filename: str,
        content: str,
        shared: bool = False,
        log_result: bool = True,
    ) -> bool:
        """
        (Re)index a note file. Replaces any previously indexed chunks.
        Returns True only if every target index now holds exactly the new
        chunks; only then is the content hash recorded, so anything less
        is retried by the next sync.

        The new chunks are upserted (in batches under Pinecone's per-request
        cap) BEFORE the old ones are pruned: chunk ids are deterministic, so
        the upsert overwrites ids 0..n-1 and the prune removes only ids past
        the new end. A failure partway leaves the note searchable under a
        mix of old and new chunks instead of under none.

        Bulk callers (reindex/sync) pass log_result=False and log a single
        summary line instead of one line per note.
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

        key = (self._scope_key(entity_label, shared), filename)
        known_count = self._chunk_counts.get(key, 0)
        complete = True
        for index in indexes:
            try:
                for i in range(0, len(records), UPSERT_BATCH_SIZE):
                    await run_pinecone(
                        index.upsert_records,
                        namespace=NOTES_NAMESPACE,
                        records=records[i : i + UPSERT_BATCH_SIZE],
                    )
            except Exception as e:
                logger.error(
                    f"[NOTES] Failed to vectorize '{filename}' (shared={shared}, "
                    f"{len(records)} chunks; failed at chunk {i}): {e}"
                )
                complete = False
                continue
            pruned = await run_pinecone(
                self._delete_note_chunks, index, shared, filename,
                keep_count=len(records), known_count=known_count,
            )
            if not pruned:
                complete = False

        # A failed or interrupted index may still hold chunks up to the larger
        # of the two counts, and the next attempt's fallback must reach them
        self._chunk_counts[key] = len(records) if complete else max(known_count, len(records))
        if complete:
            self._synced_hashes[key] = _content_hash(content)
            if log_result:
                logger.info(f"[NOTES] Vectorized '{filename}' (shared={shared}, {len(records)} chunks)")
        return complete

    async def remove_note_vectors(
        self,
        entity_label: str,
        filename: str,
        shared: bool = False,
    ) -> bool:
        """
        Remove a deleted note's chunks from all relevant indexes. The file
        stays tracked unless every index confirmed the delete, so the next
        sync still sees it as gone-from-disk and retries.
        """
        indexes = self._target_indexes(entity_label, shared)
        if not indexes:
            return False
        key = (self._scope_key(entity_label, shared), filename)
        complete = True
        for index in indexes:
            try:
                removed = await run_pinecone(
                    self._delete_note_chunks, index, shared, filename,
                    known_count=self._chunk_counts.get(key, 0),
                )
            except Exception as e:
                logger.error(f"[NOTES] Failed to remove vectors for '{filename}': {e}")
                removed = False
            complete = complete and removed
        if complete:
            self._synced_hashes.pop(key, None)
            self._chunk_counts.pop(key, None)
        return complete

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

    async def _reindex_listing(
        self, entity_label: str, shared: bool, summary: Dict[str, Any]
    ) -> None:
        """Re-vectorize every note file in one folder, accumulating into summary."""
        listing = notes_service.list_notes(entity_label, shared=shared)
        if not listing.get("success"):
            return
        label_prefix = "shared" if shared else entity_label
        for file_info in listing["files"]:
            filename = file_info["filename"]
            read = notes_service.read_note(
                entity_label, filename, shared=shared, log_read=False
            )
            if not read.get("success"):
                summary["errors"].append(f"{label_prefix}/{filename}: {read.get('error')}")
                continue
            ok = await self.vectorize_note(
                entity_label, filename, read["content"], shared=shared, log_result=False
            )
            if ok:
                summary["indexed"] += 1
            else:
                summary["errors"].append(f"{label_prefix}/{filename}: vectorization failed")

    @staticmethod
    def _files_on_disk(labels: List[str], shared: bool) -> Set[str]:
        """
        Filenames the notes listing shows for these entities' private folders
        (union), or for the shared folder. Raises if a file's absence can't
        be known, so nothing is pruned on it: a listing failed, or the notes
        base folder itself is missing — list_notes reads a missing folder as
        an empty one, which is true of an entity that has no notes yet but
        not of a base path that didn't resolve (the relative default started
        from another cwd, an unmounted drive), where it would read as every
        note deleted.
        """
        base = notes_service.base_dir
        if not base.is_dir():
            raise RuntimeError(f"notes folder {base} not found")
        files: Set[str] = set()
        for label in [""] if shared else labels:
            listing = notes_service.list_notes(label, shared=shared)
            if not listing.get("success"):
                raise RuntimeError(f"listing note files failed: {listing.get('error')}")
            files.update(f["filename"] for f in listing["files"])
        return files

    async def _prune_orphaned_notes(self, summary: Dict[str, Any]) -> None:
        """
        Delete every note vector whose file is no longer on disk, from every
        entity's index, accumulating into summary ("removed" counts files).

        The incremental sync removes only files it has tracked since the
        backend started, so a note deleted while the backend was down, or
        before the first sync after a restart, leaves its chunks behind. This
        is the full sweep that catches those: it lists the ids themselves
        instead of trusting the in-memory map.
        """
        # Each index with the entities whose private notes it holds. Private
        # ids carry no entity label, so where two entities share an index a
        # private id is an orphan only if neither of them has the file.
        owners: Dict[str, List[str]] = {}
        for entity in settings.get_entities():
            owners.setdefault(entity.index_name, []).append(entity.label)

        # Per orphaned file: True while every chunk found so far was deleted.
        # A shared note counts once however many indexes held it.
        removed: Dict[Tuple[str, str], bool] = {}
        # Per orphaned file: its keys in the sync's in-memory maps
        tracked: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
        for index_name, labels in owners.items():
            index = memory_service.get_index(index_name)
            if index is None:
                continue
            for shared in (False, True):
                scope = "shared" if shared else f"private@{index_name}"
                try:
                    ids = await run_pinecone(
                        self._list_note_ids, index, _scope_id_prefix(shared)
                    )
                except Exception as e:
                    summary["errors"].append(
                        f"{scope}: listing note vectors failed, orphans not pruned: {e}"
                    )
                    continue
                # Disk is read AFTER the ids are listed: a note created in
                # between is on disk by then and kept, so chunks a concurrent
                # sync has just written are never taken for orphans
                try:
                    on_disk = self._files_on_disk(labels, shared)
                except Exception as e:
                    summary["errors"].append(f"{scope}: {e}; orphans not pruned")
                    continue

                orphans: Dict[str, List[str]] = {}
                for vector_id in ids:
                    filename = _note_filename_from_id(vector_id, shared)
                    if filename is not None and filename not in on_disk:
                        orphans.setdefault(filename, []).append(vector_id)

                to_delete = [vid for vids in orphans.values() for vid in vids]
                deleted: Set[str] = set()
                for i in range(0, len(to_delete), DELETE_BATCH_SIZE):
                    batch = to_delete[i : i + DELETE_BATCH_SIZE]
                    try:
                        await run_pinecone(
                            index.delete, ids=batch, namespace=NOTES_NAMESPACE
                        )
                    except Exception as e:
                        summary["errors"].append(
                            f"{scope}: deleting {len(batch)} orphaned chunk(s) failed: {e}"
                        )
                        continue
                    deleted.update(batch)

                for filename, vids in orphans.items():
                    key = (scope, filename)
                    landed = all(vid in deleted for vid in vids)
                    removed[key] = removed.get(key, True) and landed
                    tracked[key] = (
                        [("shared", filename)] if shared
                        else [(self._scope_key(label, False), filename) for label in labels]
                    )

        # The sync's record of a pruned file must not say its chunks are in
        # place: a file missing only at the disk read (a delete-then-write
        # save, a note recreated under the same name) would otherwise count
        # as unchanged forever. Fully pruned: forget it, and the next sync
        # vectorizes it if it's back. Not fully: a hash no content matches,
        # so the sync re-vectorizes it if it's back and retries the removal
        # if it's still gone.
        for key, landed in removed.items():
            for sync_key in tracked[key]:
                if landed:
                    self._synced_hashes.pop(sync_key, None)
                    self._chunk_counts.pop(sync_key, None)
                else:
                    self._synced_hashes[sync_key] = ""

        summary["removed"] += sum(removed.values())

    async def reindex_all(self) -> Dict[str, Any]:
        """
        Re-vectorize every note file for every configured entity, plus shared
        notes, then prune the vectors of note files no longer on disk. Used
        to backfill notes created before vectorization existed, and to catch
        deletions the incremental sync never saw.
        """
        summary = {"indexed": 0, "removed": 0, "errors": []}

        if not memory_service.is_configured():
            summary["errors"].append("Pinecone not configured")
            return summary

        # Private notes per entity
        for entity in settings.get_entities():
            await self._reindex_listing(entity.label, shared=False, summary=summary)

        # Shared notes (indexed into every entity's index)
        await self._reindex_listing("", shared=True, summary=summary)

        await self._prune_orphaned_notes(summary)

        logger.info(
            f"[NOTES] Reindex complete: {summary['indexed']} note(s) vectorized, "
            f"{summary['removed']} deleted note(s) pruned, "
            f"{len(summary['errors'])} error(s)"
        )
        return summary

    async def sync_entity_notes(self, entity_label: str) -> Dict[str, Any]:
        """
        Incrementally sync one entity's notes (private + shared) into the
        semantic mirror: hash each file against the content last vectorized,
        re-vectorize only changes and new files, and remove vectors for
        files that have disappeared from disk.

        Claude Code mode's notes bridge: sessions edit note files directly
        with Claude Code's file tools, bypassing the write-time
        vectorization the native notes tools do — so this runs in the
        background on every recorded prompt (and at session end, when one
        fires). Sessions can idle out without ever formally ending, so
        freshness must not depend on a session-end event; per-prompt hash
        checks are cheap and only actual diffs touch Pinecone.

        A sync already running for this entity is skipped, not queued — the
        next prompt syncs again anyway.
        """
        summary: Dict[str, Any] = {"indexed": 0, "removed": 0, "unchanged": 0, "errors": []}

        if not memory_service.is_configured():
            summary["errors"].append("Pinecone not configured")
            return summary

        lock = self._sync_locks.setdefault(entity_label, asyncio.Lock())
        if lock.locked():
            summary["skipped"] = True
            return summary

        async with lock:
            for shared in (False, True):
                scope_key = self._scope_key(entity_label, shared)
                listing = notes_service.list_notes(entity_label, shared=shared)
                if not listing.get("success"):
                    summary["errors"].append(f"{scope_key}: {listing.get('error')}")
                    continue

                current_files = set()
                for file_info in listing["files"]:
                    filename = file_info["filename"]
                    current_files.add(filename)
                    read = notes_service.read_note(
                        entity_label, filename, shared=shared, log_read=False
                    )
                    if not read.get("success"):
                        summary["errors"].append(f"{scope_key}/{filename}: {read.get('error')}")
                        continue
                    if self._synced_hashes.get((scope_key, filename)) == _content_hash(read["content"]):
                        summary["unchanged"] += 1
                        continue
                    ok = await self.vectorize_note(
                        entity_label, filename, read["content"], shared=shared,
                        log_result=False,
                    )
                    if ok:
                        summary["indexed"] += 1
                    else:
                        summary["errors"].append(f"{scope_key}/{filename}: vectorization failed")

                # Files vectorized before but no longer on disk
                stale = [
                    key for key in self._synced_hashes
                    if key[0] == scope_key and key[1] not in current_files
                ]
                for _, filename in stale:
                    if await self.remove_note_vectors(entity_label, filename, shared=shared):
                        summary["removed"] += 1
                    else:
                        summary["errors"].append(f"{scope_key}/{filename}: vector removal failed")

        if summary["indexed"] or summary["removed"] or summary["errors"]:
            logger.info(
                f"[NOTES] Sync for '{entity_label}': {summary['indexed']} indexed, "
                f"{summary['removed']} removed, {summary['unchanged']} unchanged, "
                f"{len(summary['errors'])} error(s)"
            )
            for error in summary["errors"]:
                logger.warning(f"[NOTES] Sync error for '{entity_label}': {error}")
        return summary


# Singleton instance
notes_vector_service = NotesVectorService()
