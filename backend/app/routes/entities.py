from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import EntitySetting
from app.services import memory_service
from app.services.thinking_effort import EFFORT_LEVELS, normalize_effort, resolve_effort

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
    # Persisted per-entity thinking effort (None means "use the global default").
    thinking_effort: Optional[str] = None


class EntityListResponse(BaseModel):
    entities: List[EntityResponse]
    default_entity: Optional[str] = None
    # The effort an entity gets when it has none of its own, so the UI can
    # label the "Default" option with the level it actually resolves to.
    default_thinking_effort: str = "high"
    # The levels the UI may offer, in ascending order.
    thinking_effort_levels: List[str] = list(EFFORT_LEVELS)


class SystemPromptUpdate(BaseModel):
    # None / empty clears the entity's system prompt.
    system_prompt: Optional[str] = None


class SystemPromptResponse(BaseModel):
    entity_id: str
    system_prompt: Optional[str] = None


class ThinkingEffortUpdate(BaseModel):
    # None / empty clears the override, falling back to the global default.
    thinking_effort: Optional[str] = None


class ThinkingEffortResponse(BaseModel):
    entity_id: str
    thinking_effort: Optional[str] = None
    # The level that will actually be sent (the override, or the global default)
    effective_thinking_effort: str


async def _load_entity_settings(db: AsyncSession) -> Dict[str, EntitySetting]:
    """Load all persisted per-entity settings rows, keyed by entity_id."""
    result = await db.execute(select(EntitySetting))
    return {row.entity_id: row for row in result.scalars().all()}


@router.get("/", response_model=EntityListResponse)
async def list_entities(db: AsyncSession = Depends(get_db)):
    """
    List all configured AI entities.

    Each entity corresponds to a separate Pinecone index with its own
    conversation history and memory, and can have its own model provider/model.
    Each entity also carries its persisted default system prompt and thinking
    effort so the UI can render them without keeping any client-side copy.
    """
    entities = settings.get_entities()
    default_entity = settings.get_default_entity()

    # Handle case when no entities are configured
    if not entities or not default_entity:
        return EntityListResponse(
            entities=[],
            default_entity=None,
            default_thinking_effort=resolve_effort(None),
        )

    entity_settings = await _load_entity_settings(db)

    return EntityListResponse(
        entities=[
            EntityResponse(
                index_name=entity.index_name,
                label=entity.label,
                description=entity.description,
                llm_provider=entity.llm_provider,
                default_model=entity.default_model,
                is_default=(entity.index_name == default_entity.index_name),
                system_prompt=getattr(entity_settings.get(entity.index_name), "system_prompt", None),
                thinking_effort=getattr(entity_settings.get(entity.index_name), "thinking_effort", None),
            )
            for entity in entities
        ],
        default_entity=default_entity.index_name,
        # Resolved rather than read raw, so the UI is told the level that will
        # actually be sent even if THINKING_EFFORT is set to something unknown.
        default_thinking_effort=resolve_effort(None),
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
        thinking_effort=setting.thinking_effort if setting else None,
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


@router.put("/{entity_id}/thinking-effort", response_model=ThinkingEffortResponse)
async def update_entity_thinking_effort(
    entity_id: str,
    payload: ThinkingEffortUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Set (or clear) how hard this entity's model should think.

    Levels are low, medium, high, xhigh, max. Clearing the value (null/empty)
    falls the entity back to DEFAULT_THINKING_EFFORT ("high"). Levels above a
    model's ceiling are clamped at request time, and models with no thinking
    control ignore the setting — so any level is accepted for any entity.
    """
    if not settings.get_entity_by_index(entity_id):
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    raw = payload.thinking_effort
    effort = normalize_effort(raw)
    # Distinguish "clear it" from "that isn't a level": an unrecognized value
    # is a client bug worth surfacing, not a silent fallback to the default.
    if effort is None and isinstance(raw, str) and raw.strip():
        raise HTTPException(
            status_code=422,
            detail=f"Invalid thinking effort '{raw}'. Expected one of: {', '.join(EFFORT_LEVELS)}",
        )

    setting = await db.get(EntitySetting, entity_id)
    if setting is None:
        setting = EntitySetting(entity_id=entity_id, thinking_effort=effort)
        db.add(setting)
    else:
        setting.thinking_effort = effort

    await db.commit()

    return ThinkingEffortResponse(
        entity_id=entity_id,
        thinking_effort=effort,
        effective_thinking_effort=resolve_effort(effort),
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
