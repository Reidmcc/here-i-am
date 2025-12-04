from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # API Keys
    anthropic_api_key: str = ""
    pinecone_api_key: str = ""
    pinecone_index_name: str = "memories"

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


settings = Settings()
