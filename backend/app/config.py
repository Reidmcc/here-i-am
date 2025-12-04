from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List
import json


class EntityConfig:
    """Configuration for a single AI entity (Pinecone index)."""
    def __init__(self, index_name: str, label: str, description: str = ""):
        self.index_name = index_name
        self.label = label
        self.description = description

    def to_dict(self):
        return {
            "index_name": self.index_name,
            "label": self.label,
            "description": self.description,
        }


class Settings(BaseSettings):
    # API Keys
    anthropic_api_key: str = ""
    pinecone_api_key: str = ""
    pinecone_index_name: str = "memories"  # Default/fallback index

    # Multiple Pinecone indexes (JSON array of objects with index_name, label, description)
    # Example: '[{"index_name": "claude", "label": "Claude", "description": "Primary AI entity"}]'
    pinecone_indexes: str = ""

    # Database
    here_i_am_database_url: str = Field(
        default="sqlite+aiosqlite:///./here_i_am.db",
        alias="HERE_I_AM_DATABASE_URL"
    )

    # Application
    debug: bool = True

    # Memory retrieval defaults
    retrieval_top_k: int = 10
    similarity_threshold: float = 0.7

    # Significance calculation
    recency_boost_strength: float = 1.0
    age_decay_rate: float = 0.01
    significance_floor: float = 0.0

    # Reflection mode
    reflection_seed_count: int = 7
    reflection_exclude_recent_conversations: int = 0

    # API defaults
    default_model: str = "claude-sonnet-4-20250514"
    default_temperature: float = 1.0
    default_max_tokens: int = 4096

    class Config:
        env_file = ".env"
        extra = "ignore"

    def get_entities(self) -> List[EntityConfig]:
        """
        Parse and return the list of configured entities.

        If PINECONE_INDEXES is set, parse it as JSON.
        Otherwise, create a default entity from PINECONE_INDEX_NAME.
        """
        if self.pinecone_indexes:
            try:
                indexes_data = json.loads(self.pinecone_indexes)
                return [
                    EntityConfig(
                        index_name=idx.get("index_name", "memories"),
                        label=idx.get("label", idx.get("index_name", "Default")),
                        description=idx.get("description", ""),
                    )
                    for idx in indexes_data
                ]
            except json.JSONDecodeError:
                pass

        # Fallback to single index from pinecone_index_name
        return [
            EntityConfig(
                index_name=self.pinecone_index_name,
                label="Default",
                description="Default AI entity",
            )
        ]

    def get_entity_by_index(self, index_name: str) -> Optional[EntityConfig]:
        """Get an entity configuration by its index name."""
        entities = self.get_entities()
        for entity in entities:
            if entity.index_name == index_name:
                return entity
        return None

    def get_default_entity(self) -> EntityConfig:
        """Get the first (default) entity."""
        entities = self.get_entities()
        return entities[0] if entities else EntityConfig(
            index_name=self.pinecone_index_name,
            label="Default",
            description="Default AI entity",
        )


settings = Settings()
