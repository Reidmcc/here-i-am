from typing import List, Optional, Dict
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.config import settings
from app.database import get_db
from app.models import EntitySetting
from app.services import memory_service

router = APIRouter(prefix="/api/entities", tags=["entities"])


class EntityResponse(BaseModel):
    index_name: str
    label: str
    description: str
    llm_provider: str = "anthropic"
    default_model: Optional[str] = None
    is_default: bool = False
    # Persisted per-entity default system prompt (None means no prompt).
    system_prompt: Optional[str] = None


class EntityListResponse(BaseModel):
    entities: List[EntityResponse]
    default_entity: Optional[str] = None


class SystemPromptUpdate(BaseModel):
    # None / empty clears the entity's system prompt.
    system_prompt: Optional[str] = None


class SystemPromptResponse(BaseModel):
    entity_id: str
    system_prompt: Optional[str] = None


async def _load_system_prompts(db: AsyncSession) -> Dict[str, Optional[str]]:
    """Load all persisted per-entity system prompts, keyed by entity_id."""
    result = await db.execute(select(EntitySetting))
    return {row.entity_id: row.system_prompt for row in result.scalars().all()}


@router.get("/", response_model=EntityListResponse)
async def list_entities(db: AsyncSession = Depends(get_db)):
    """
    List all configured AI entities.

    Each entity corresponds to a separate Pinecone index with its own
    conversation history and memory, and can have its own model provider/model.
    Each entity also carries its persisted default system prompt so the UI can
    render it without keeping any client-side copy.
    """
    entities = settings.get_entities()
    default_entity = settings.get_default_entity()

    # Handle case when no entities are configured
    if not entities or not default_entity:
        return EntityListResponse(
            entities=[],
            default_entity=None,
        )

    system_prompts = await _load_system_prompts(db)

    return EntityListResponse(
        entities=[
            EntityResponse(
                index_name=entity.index_name,
                label=entity.label,
                description=entity.description,
                llm_provider=entity.llm_provider,
                default_model=entity.default_model,
                is_default=(entity.index_name == default_entity.index_name),
                system_prompt=system_prompts.get(entity.index_name),
            )
            for entity in entities
        ],
        default_entity=default_entity.index_name,
    )


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_entity(entity_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific entity by its index name."""
    entity = settings.get_entity_by_index(entity_id)

    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    default_entity = settings.get_default_entity()
    setting = await db.get(EntitySetting, entity_id)

    return EntityResponse(
        index_name=entity.index_name,
        label=entity.label,
        description=entity.description,
        llm_provider=entity.llm_provider,
        default_model=entity.default_model,
        is_default=(entity.index_name == default_entity.index_name),
        system_prompt=setting.system_prompt if setting else None,
    )


@router.put("/{entity_id}/system-prompt", response_model=SystemPromptResponse)
async def update_entity_system_prompt(
    entity_id: str,
    payload: SystemPromptUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Set (or clear) the persisted default system prompt for an entity.

    This is the source of truth for an entity's system prompt — the UI only
    sends the text the researcher typed; persistence lives here so the prompt
    survives across browsers and sessions.
    """
    if not settings.get_entity_by_index(entity_id):
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    # Normalize empty strings to NULL so "no prompt" is represented one way.
    prompt = payload.system_prompt
    if prompt is not None and prompt.strip() == "":
        prompt = None

    setting = await db.get(EntitySetting, entity_id)
    if setting is None:
        setting = EntitySetting(entity_id=entity_id, system_prompt=prompt)
        db.add(setting)
    else:
        setting.system_prompt = prompt

    await db.commit()

    return SystemPromptResponse(entity_id=entity_id, system_prompt=prompt)


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
                "total_vector_count": stats.total_vector_count or 0,
                "dimension": stats.dimension,
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
