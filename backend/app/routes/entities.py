from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.models.entity import Entity
from app.services import memory_service

router = APIRouter(prefix="/api/entities", tags=["entities"])


class EntityResponse(BaseModel):
    index_name: str
    label: str
    description: str
    llm_provider: str = "anthropic"
    default_model: Optional[str] = None
    is_default: bool = False
    is_from_env: bool = False  # True if configured via environment variables


class EntityListResponse(BaseModel):
    entities: List[EntityResponse]
    default_entity: str


class EntityCreate(BaseModel):
    index_name: str
    label: str
    description: Optional[str] = ""
    llm_provider: str = "anthropic"
    default_model: Optional[str] = None
    is_default: bool = False


class EntityUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    llm_provider: Optional[str] = None
    default_model: Optional[str] = None
    is_default: Optional[bool] = None


async def get_all_entities(db: AsyncSession) -> tuple[List[EntityResponse], str]:
    """
    Get all entities from both database and environment configuration.
    Database entities take precedence over env-configured ones with the same index_name.
    Returns (entities, default_entity_index_name).
    """
    # Get entities from database
    result = await db.execute(select(Entity).where(Entity.is_active == True))
    db_entities = result.scalars().all()

    # Get entities from environment config
    env_entities = settings.get_entities()

    # Build response, with database entities taking precedence
    entities_map = {}
    default_entity = None

    # First add env entities
    for env_entity in env_entities:
        entities_map[env_entity.index_name] = EntityResponse(
            index_name=env_entity.index_name,
            label=env_entity.label,
            description=env_entity.description or "",
            llm_provider=env_entity.llm_provider,
            default_model=env_entity.default_model,
            is_default=False,
            is_from_env=True,
        )

    # Then override with database entities (they take precedence)
    for db_entity in db_entities:
        entities_map[db_entity.index_name] = EntityResponse(
            index_name=db_entity.index_name,
            label=db_entity.label,
            description=db_entity.description or "",
            llm_provider=db_entity.llm_provider,
            default_model=db_entity.default_model,
            is_default=db_entity.is_default,
            is_from_env=False,
        )
        if db_entity.is_default:
            default_entity = db_entity.index_name

    entities = list(entities_map.values())

    # If no default set in database, use first entity
    if not default_entity and entities:
        default_entity = entities[0].index_name
        entities[0].is_default = True

    return entities, default_entity or ""


@router.get("/", response_model=EntityListResponse)
async def list_entities(db: AsyncSession = Depends(get_db)):
    """
    List all configured AI entities.

    Each entity corresponds to a separate Pinecone index with its own
    conversation history and memory, and can have its own model provider/model.
    Entities can be configured via environment variables or created dynamically.
    """
    entities, default_entity = await get_all_entities(db)

    return EntityListResponse(
        entities=entities,
        default_entity=default_entity,
    )


@router.post("/", response_model=EntityResponse)
async def create_entity(entity: EntityCreate, db: AsyncSession = Depends(get_db)):
    """
    Create a new AI entity.

    The index_name should correspond to a Pinecone index (which must be created
    separately in Pinecone with dimension=1024).
    """
    # Check if entity with this index_name already exists in database
    result = await db.execute(select(Entity).where(Entity.index_name == entity.index_name))
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Entity with index_name '{entity.index_name}' already exists"
        )

    # If this entity should be default, unset other defaults
    if entity.is_default:
        await db.execute(
            Entity.__table__.update().values(is_default=False)
        )

    # Create new entity
    new_entity = Entity(
        index_name=entity.index_name,
        label=entity.label,
        description=entity.description or "",
        llm_provider=entity.llm_provider,
        default_model=entity.default_model,
        is_default=entity.is_default,
    )

    db.add(new_entity)
    await db.commit()
    await db.refresh(new_entity)

    return EntityResponse(
        index_name=new_entity.index_name,
        label=new_entity.label,
        description=new_entity.description or "",
        llm_provider=new_entity.llm_provider,
        default_model=new_entity.default_model,
        is_default=new_entity.is_default,
        is_from_env=False,
    )


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_entity(entity_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific entity by its index name."""
    # First check database
    result = await db.execute(select(Entity).where(Entity.index_name == entity_id))
    db_entity = result.scalar_one_or_none()

    if db_entity:
        return EntityResponse(
            index_name=db_entity.index_name,
            label=db_entity.label,
            description=db_entity.description or "",
            llm_provider=db_entity.llm_provider,
            default_model=db_entity.default_model,
            is_default=db_entity.is_default,
            is_from_env=False,
        )

    # Check env config
    env_entity = settings.get_entity_by_index(entity_id)
    if env_entity:
        return EntityResponse(
            index_name=env_entity.index_name,
            label=env_entity.label,
            description=env_entity.description or "",
            llm_provider=env_entity.llm_provider,
            default_model=env_entity.default_model,
            is_default=False,
            is_from_env=True,
        )

    raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")


@router.patch("/{entity_id}", response_model=EntityResponse)
async def update_entity(entity_id: str, update: EntityUpdate, db: AsyncSession = Depends(get_db)):
    """
    Update an entity's configuration.

    Note: Entities configured via environment variables cannot be updated.
    They must first be "adopted" by creating a database entity with the same index_name.
    """
    result = await db.execute(select(Entity).where(Entity.index_name == entity_id))
    entity = result.scalar_one_or_none()

    if not entity:
        # Check if it's an env entity
        env_entity = settings.get_entity_by_index(entity_id)
        if env_entity:
            raise HTTPException(
                status_code=400,
                detail="Cannot update environment-configured entity. Create a database entity with the same index_name to override it."
            )
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    # If setting this as default, unset other defaults
    if update.is_default:
        await db.execute(
            Entity.__table__.update().where(Entity.index_name != entity_id).values(is_default=False)
        )

    # Update fields
    if update.label is not None:
        entity.label = update.label
    if update.description is not None:
        entity.description = update.description
    if update.llm_provider is not None:
        entity.llm_provider = update.llm_provider
    if update.default_model is not None:
        entity.default_model = update.default_model
    if update.is_default is not None:
        entity.is_default = update.is_default

    await db.commit()
    await db.refresh(entity)

    return EntityResponse(
        index_name=entity.index_name,
        label=entity.label,
        description=entity.description or "",
        llm_provider=entity.llm_provider,
        default_model=entity.default_model,
        is_default=entity.is_default,
        is_from_env=False,
    )


@router.delete("/{entity_id}")
async def delete_entity(entity_id: str, db: AsyncSession = Depends(get_db)):
    """
    Delete an entity.

    Note: This only deletes database-stored entities. Environment-configured
    entities cannot be deleted (they must be removed from the environment).
    This does NOT delete the associated Pinecone index or conversations.
    """
    result = await db.execute(select(Entity).where(Entity.index_name == entity_id))
    entity = result.scalar_one_or_none()

    if not entity:
        env_entity = settings.get_entity_by_index(entity_id)
        if env_entity:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete environment-configured entity. Remove it from your environment configuration."
            )
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    await db.delete(entity)
    await db.commit()

    return {"message": f"Entity '{entity_id}' deleted successfully"}


@router.get("/{entity_id}/status")
async def get_entity_status(entity_id: str, db: AsyncSession = Depends(get_db)):
    """
    Get the status of an entity's Pinecone index.

    Returns connection status and basic stats if available.
    """
    # Check if entity exists
    result = await db.execute(select(Entity).where(Entity.index_name == entity_id))
    db_entity = result.scalar_one_or_none()

    env_entity = settings.get_entity_by_index(entity_id)

    if not db_entity and not env_entity:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    label = db_entity.label if db_entity else (env_entity.label if env_entity else entity_id)

    # Check if Pinecone is configured
    if not memory_service.is_configured():
        return {
            "entity_id": entity_id,
            "label": label,
            "pinecone_configured": False,
            "index_connected": False,
            "message": "Pinecone is not configured",
        }

    # Try to connect to the index
    index = memory_service.get_index(entity_id)
    if index is None:
        return {
            "entity_id": entity_id,
            "label": label,
            "pinecone_configured": True,
            "index_connected": False,
            "message": f"Could not connect to Pinecone index '{entity_id}'",
        }

    # Try to get index stats
    try:
        stats = index.describe_index_stats()
        return {
            "entity_id": entity_id,
            "label": label,
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
            "label": label,
            "pinecone_configured": True,
            "index_connected": True,
            "stats": None,
            "message": f"Connected but could not get stats: {str(e)}",
        }
