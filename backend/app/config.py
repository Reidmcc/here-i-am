from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List
import json


class VoiceConfig:
    """Configuration for a TTS voice."""
    def __init__(
        self,
        voice_id: str,
        label: str,
        description: str = "",
    ):
        self.voice_id = voice_id
        self.label = label
        self.description = description

    def to_dict(self):
        return {
            "voice_id": self.voice_id,
            "label": self.label,
            "description": self.description,
        }


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
    elevenlabs_api_key: str = ""

    # ElevenLabs TTS settings
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Default voice (Rachel)
    elevenlabs_model_id: str = "eleven_multilingual_v2"  # Default model
    # Multiple voices (JSON array of objects with voice_id, label, description)
    # Example: '[{"voice_id": "21m00Tcm4TlvDq8ikWAM", "label": "Rachel", "description": "Calm female voice"}]'
    elevenlabs_voices: str = ""

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
    initial_retrieval_top_k: int = 5  # First retrieval in a conversation
    retrieval_top_k: int = 5  # Subsequent retrievals
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
    default_verbosity: str = "medium"  # Default verbosity for GPT-5.1 models (low, medium, high)

    # =========================================================================
    # SUPPORTED MODELS REFERENCE
    # =========================================================================
    # The following models are supported. Model names are passed directly to
    # the provider APIs, so new models should work automatically when released.
    #
    # ANTHROPIC MODELS:
    #   - claude-sonnet-4-5-20250929
    #   - claude-opus-4-5-20251101
    #   - claude-sonnet-4-20250514
    #
    # OPENAI MODELS:
    #   - gpt-4o
    #   - gpt-4o-mini
    #   - gpt-4-turbo
    #   - gpt-4
    #   - gpt-5.1
    #   - gpt-5-mini
    #   - gpt-5.1-chat-latest
    #   - o1
    #   - o1-mini
    #   - o1-preview
    #   - o3
    #   - o3-mini
    #   - o4-mini
    #
    # To use a model, set it as default_model or default_openai_model above,
    # or specify it in the entity configuration via PINECONE_INDEXES.
    # =========================================================================

    # Context window limits (in tokens)
    # Anthropic's max context is 200k
    # OpenAI max context varies by model. For most GPT 5.x models, it's 272,000. For GPT 5.1 Chat and the older models it's 128,000
    # Leave some room between your setting and the actual max; token counting client-side is approximate
    # The maximum is for all inbound tokens, memory and conversation history token counts are combined
    context_token_limit: int = 175000  # Conversation history cap
    memory_token_limit: int = 20000    # Memory block cap (kept small to reduce cache miss cost)

    class Config:
        env_file = ".env"
        extra = "ignore"

    def get_entities(self) -> List[EntityConfig]:
        """
        Parse and return the list of configured entities.

        Requires PINECONE_INDEXES to be set as a JSON array.
        Returns empty list if not configured.
        Raises ValueError if PINECONE_INDEXES contains invalid JSON.
        """
        if not self.pinecone_indexes:
            return []

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
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON in PINECONE_INDEXES environment variable: {e}"
            )

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

    def get_default_entity(self) -> Optional[EntityConfig]:
        """Get the first (default) entity, or None if no entities configured."""
        entities = self.get_entities()
        return entities[0] if entities else None

    def get_voices(self) -> List[VoiceConfig]:
        """
        Parse and return the list of configured TTS voices.

        If ELEVENLABS_VOICES is set, parse it as JSON.
        Otherwise, create a default voice from ELEVENLABS_VOICE_ID.
        """
        if self.elevenlabs_voices:
            try:
                voices_data = json.loads(self.elevenlabs_voices)
                return [
                    VoiceConfig(
                        voice_id=v.get("voice_id"),
                        label=v.get("label", v.get("voice_id", "Voice")),
                        description=v.get("description", ""),
                    )
                    for v in voices_data
                    if v.get("voice_id")
                ]
            except json.JSONDecodeError:
                pass

        # Fallback to single voice from elevenlabs_voice_id
        return [
            VoiceConfig(
                voice_id=self.elevenlabs_voice_id,
                label="Default",
                description="Default voice",
            )
        ]

    def get_default_voice(self) -> VoiceConfig:
        """Get the first (default) voice."""
        voices = self.get_voices()
        return voices[0] if voices else VoiceConfig(
            voice_id=self.elevenlabs_voice_id,
            label="Default",
            description="Default voice",
        )


settings = Settings()
