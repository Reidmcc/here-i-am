from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.services.memory_service import memory_service

router = APIRouter(prefix="/api/entities", tags=["entities"])


class EntityResponse(BaseModel):
    index_name: str
    label: str
    description: str
    is_default: bool = False


class EntityListResponse(BaseModel):
    entities: List[EntityResponse]
    default_entity: str


@router.get("/", response_model=EntityListResponse)
async def list_entities():
    """
    List all configured AI entities.

    Each entity corresponds to a separate Pinecone index with its own
    conversation history and memory.
    """
    entities = settings.get_entities()
    default_entity = settings.get_default_entity()

    return EntityListResponse(
        entities=[
            EntityResponse(
                index_name=entity.index_name,
                label=entity.label,
                description=entity.description,
                is_default=(entity.index_name == default_entity.index_name),
            )
            for entity in entities
        ],
        default_entity=default_entity.index_name,
    )


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_entity(entity_id: str):
    """Get a specific entity by its index name."""
    entity = settings.get_entity_by_index(entity_id)

    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    default_entity = settings.get_default_entity()

    return EntityResponse(
        index_name=entity.index_name,
        label=entity.label,
        description=entity.description,
        is_default=(entity.index_name == default_entity.index_name),
    )


@router.get("/{entity_id}/status")
async def get_entity_status(entity_id: str):
    """
    Get the status of an entity's Pinecone index.

    Returns connection status and basic stats if available.
    """
    entity = settings.get_entity_by_index(entity_id)

    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    # Check if Pinecone is configured
    if not memory_service.is_configured():
        return {
            "entity_id": entity_id,
            "label": entity.label,
            "pinecone_configured": False,
            "index_connected": False,
            "message": "Pinecone is not configured",
        }

    # Try to connect to the index
    index = memory_service.get_index(entity_id)
    if index is None:
        return {
            "entity_id": entity_id,
            "label": entity.label,
            "pinecone_configured": True,
            "index_connected": False,
            "message": f"Could not connect to Pinecone index '{entity_id}'",
        }

    # Try to get index stats
    try:
        stats = index.describe_index_stats()
        return {
            "entity_id": entity_id,
            "label": entity.label,
            "pinecone_configured": True,
            "index_connected": True,
            "stats": {
                "total_vector_count": stats.get("total_vector_count", 0),
                "dimension": stats.get("dimension"),
            },
        }
    except Exception as e:
        return {
            "entity_id": entity_id,
            "label": entity.label,
            "pinecone_configured": True,
            "index_connected": True,
            "stats": None,
            "message": f"Connected but could not get stats: {str(e)}",
        }
