from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List
import json


class EntityConfig:
    """Configuration for a single AI entity (Pinecone index)."""
    def __init__(
        self,
        index_name: str,
        label: str,
        description: str = "",
        llm_provider: str = "anthropic",
        default_model: Optional[str] = None,
        host: Optional[str] = None,
    ):
        self.index_name = index_name
        self.label = label
        self.description = description
        self.llm_provider = llm_provider  # "anthropic" or "openai"
        self.default_model = default_model  # If None, uses global default for provider
        self.host = host  # Pinecone index host URL (required for serverless indexes)

    def to_dict(self):
        return {
            "index_name": self.index_name,
            "label": self.label,
            "description": self.description,
            "llm_provider": self.llm_provider,
            "default_model": self.default_model,
            "host": self.host,
        }


class Settings(BaseSettings):
    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    pinecone_api_key: str = ""
    pinecone_index_name: str = "memories"  # Default/fallback index

    # Multiple Pinecone indexes (JSON array of objects with index_name, label, description, llm_provider, default_model)
    # Example: '[{"index_name": "claude", "label": "Claude", "description": "Primary AI entity", "llm_provider": "anthropic", "default_model": "claude-sonnet-4-5-20250929"}]'
    pinecone_indexes: str = ""

    # Database
    here_i_am_database_url: str = Field(
        default="sqlite+aiosqlite:///./here_i_am.db",
        alias="HERE_I_AM_DATABASE_URL"
    )

    # Application
    debug: bool = True

    # Memory retrieval defaults
    retrieval_top_k: int = 5
    similarity_threshold: float = 0.3  # Tuned for llama-text-embed-v2

    # Significance calculation
    recency_boost_strength: float = 1.0
    age_decay_rate: float = 0.01
    significance_floor: float = 0.0

    # Reflection mode
    reflection_seed_count: int = 7
    reflection_exclude_recent_conversations: int = 0

    # API defaults
    default_model: str = "claude-sonnet-4-5-20250929"  # Default Anthropic model
    default_openai_model: str = "gpt-4o"  # Default OpenAI model
    default_temperature: float = 1.0
    default_max_tokens: int = 4096

    # Context window limits (in tokens)
    # Anthropic's max context is 200k; we cap at 150k to leave room for response
    context_token_limit: int = 150000  # Conversation history cap
    memory_token_limit: int = 20000    # Memory block cap (kept small to reduce cache miss cost)

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
                        llm_provider=idx.get("llm_provider", "anthropic"),
                        default_model=idx.get("default_model"),
                        host=idx.get("host"),
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
                llm_provider="anthropic",
                default_model=self.default_model,
            )
        ]

    def get_default_model_for_provider(self, provider: str) -> str:
        """Get the default model for a given provider."""
        if provider == "openai":
            return self.default_openai_model
        return self.default_model  # anthropic is the default

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
