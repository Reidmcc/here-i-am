"""
Notes routes - researcher-facing notes maintenance.

Entity notes are managed by the entities themselves through the notes tools;
this router only exposes maintenance operations, currently reindexing the
notes vector store (backfill for notes written before notes_search existed,
or recovery after a Pinecone issue).
"""

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.memory_service import memory_service
from app.services.notes_vector_service import notes_vector_service

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.post("/reindex")
async def reindex_notes():
    """
    Re-vectorize all note files (every entity's private notes plus shared
    notes) into the "notes" namespace of each entity's Pinecone index.

    Idempotent: existing chunks for each file are replaced.
    """
    if not settings.notes_enabled:
        raise HTTPException(status_code=503, detail="Notes feature is not enabled")
    if not memory_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Memory system not configured. Set PINECONE_API_KEY in environment."
        )

    summary = await notes_vector_service.reindex_all()
    return summary
