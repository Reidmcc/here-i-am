from pathlib import Path
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
        provider: str = "elevenlabs",
    ):
        self.voice_id = voice_id
        self.label = label
        self.description = description
        self.provider = provider

    def to_dict(self):
        return {
            "voice_id": self.voice_id,
            "label": self.label,
            "description": self.description,
            "provider": self.provider,
        }


class XTTSVoiceConfig:
    """Configuration for an XTTS cloned voice."""
    def __init__(
        self,
        voice_id: str,
        label: str,
        description: str = "",
        sample_path: str = "",
        temperature: float = 0.75,
        length_penalty: float = 1.0,
        repetition_penalty: float = 5.0,
        speed: float = 1.0,
    ):
        self.voice_id = voice_id
        self.label = label
        self.description = description
        self.sample_path = sample_path
        # XTTS synthesis parameters
        self.temperature = temperature
        self.length_penalty = length_penalty
        self.repetition_penalty = repetition_penalty
        self.speed = speed

    def to_dict(self):
        return {
            "voice_id": self.voice_id,
            "label": self.label,
            "description": self.description,
            "sample_path": self.sample_path,
            "provider": "xtts",
            "temperature": self.temperature,
            "length_penalty": self.length_penalty,
            "repetition_penalty": self.repetition_penalty,
            "speed": self.speed,
        }


class StyleTTS2VoiceConfig:
    """Configuration for a StyleTTS 2 cloned voice."""
    def __init__(
        self,
        voice_id: str,
        label: str,
        description: str = "",
        sample_path: str = "",
        alpha: float = 0.3,
        beta: float = 0.7,
        diffusion_steps: int = 10,
        embedding_scale: float = 1.0,
    ):
        self.voice_id = voice_id
        self.label = label
        self.description = description
        self.sample_path = sample_path
        # StyleTTS 2 synthesis parameters
        self.alpha = alpha  # Timbre parameter (0-1), higher = more diverse
        self.beta = beta    # Prosody parameter (0-1), higher = more diverse
        self.diffusion_steps = diffusion_steps  # Quality vs speed tradeoff
        self.embedding_scale = embedding_scale  # Classifier free guidance

    def to_dict(self):
        return {
            "voice_id": self.voice_id,
            "label": self.label,
            "description": self.description,
            "sample_path": self.sample_path,
            "provider": "styletts2",
            "alpha": self.alpha,
            "beta": self.beta,
            "diffusion_steps": self.diffusion_steps,
            "embedding_scale": self.embedding_scale,
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
        self.llm_provider = llm_provider  # "anthropic", "openai", or "google"
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


class GitHubRepoConfig:
    """Configuration for a GitHub repository integration."""
    def __init__(
        self,
        owner: str,
        repo: str,
        label: str,
        token: str,
        protected_branches: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        local_clone_path: Optional[str] = None,
        commit_author_name: Optional[str] = None,
        commit_author_email: Optional[str] = None,
    ):
        self.owner = owner
        self.repo = repo
        self.label = label
        self.token = token
        self.protected_branches = protected_branches or ["main", "master"]
        self.capabilities = capabilities or ["read", "branch", "commit", "pr", "issue"]
        # Normalize local_clone_path to handle Windows paths (backslashes)
        # First normalize backslashes to forward slashes for cross-platform compatibility,
        # then use Path to resolve the path correctly on the current OS
        if local_clone_path:
            # Convert Windows backslashes to forward slashes first
            normalized = local_clone_path.replace("\\", "/")
            self.local_clone_path = str(Path(normalized))
        else:
            self.local_clone_path = None
        self.commit_author_name = commit_author_name
        self.commit_author_email = commit_author_email

    def to_dict(self, include_token: bool = False):
        """Convert to dict, optionally excluding the token for security."""
        result = {
            "owner": self.owner,
            "repo": self.repo,
            "label": self.label,
            "protected_branches": self.protected_branches,
            "capabilities": self.capabilities,
            "local_clone_path": self.local_clone_path,
            "commit_author_name": self.commit_author_name,
            "commit_author_email": self.commit_author_email,
        }
        if include_token:
            result["token"] = self.token
        return result

    def has_capability(self, capability: str) -> bool:
        """Check if this repo has a specific capability enabled."""
        return capability in self.capabilities


class Settings(BaseSettings):
    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    pinecone_api_key: str = ""
    elevenlabs_api_key: str = ""

    # ElevenLabs TTS settings
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Default voice (Rachel)
    elevenlabs_model_id: str = "eleven_multilingual_v2"  # Default model
    # Multiple voices (JSON array of objects with voice_id, label, description)
    # Example: '[{"voice_id": "21m00Tcm4TlvDq8ikWAM", "label": "Rachel", "description": "Calm female voice"}]'
    elevenlabs_voices: str = ""

    # XTTS Local TTS settings
    # Set XTTS_ENABLED=true to use local XTTS v2 instead of ElevenLabs
    xtts_enabled: bool = False
    # URL of the XTTS API server (xtts-api-server or similar)
    xtts_api_url: str = "http://localhost:8020"
    # Default speaker wav file path or speaker name for XTTS
    xtts_default_speaker: str = ""
    # Language for XTTS synthesis
    xtts_language: str = "en"
    # Directory to store cloned voice samples
    xtts_voices_dir: str = "./xtts_voices"

    # StyleTTS 2 Local TTS settings
    # Set STYLETTS2_ENABLED=true to use local StyleTTS 2 for TTS
    # StyleTTS 2 takes priority over XTTS and ElevenLabs if enabled
    styletts2_enabled: bool = False
    # URL of the StyleTTS 2 API server
    styletts2_api_url: str = "http://localhost:8021"
    # Default speaker wav file path for StyleTTS 2
    styletts2_default_speaker: str = ""
    # Directory to store cloned voice samples
    styletts2_voices_dir: str = "./styletts2_voices"
    # Phonemizer backend: "gruut" (MIT licensed, no system deps) or "espeak" (requires espeak-ng)
    styletts2_phonemizer: str = "gruut"

    # Whisper STT (Speech-to-Text) settings
    # Set WHISPER_ENABLED=true to use local Whisper for speech-to-text
    whisper_enabled: bool = False
    # URL of the Whisper STT server
    whisper_api_url: str = "http://localhost:8030"
    # Default model (large-v3, distil-large-v3, medium, small, base, tiny)
    whisper_model: str = "large-v3"

    # Tool Use settings
    # Enable tool use (web search, content fetching) for AI entities
    tools_enabled: bool = True
    # Maximum number of tool use iterations before forcing a final response
    tool_use_max_iterations: int = 10
    # Brave Search API key (for web search tool)
    brave_search_api_key: str = ""

    # GitHub Tools settings
    # Enable GitHub repository tools for AI entities
    github_tools_enabled: bool = False
    # GitHub repositories configuration (JSON array)
    # Each repo: {"owner": "...", "repo": "...", "label": "...", "token": "ghp_...",
    #             "protected_branches": ["main", "master"], "capabilities": ["read", "branch", "commit", "pr", "issue"]}
    github_repos: str = ""

    # Entity Notes settings
    # Enable persistent notes for AI entities
    notes_enabled: bool = True
    # Base directory for entity notes storage
    # Each entity gets their own folder: {notes_base_dir}/{entity_label}/
    # Shared notes accessible to all entities: {notes_base_dir}/shared/
    notes_base_dir: str = "./notes"

    # Memory System settings
    # When True, memories are inserted directly into conversation context as user messages
    # (better cacheability - memories only paid for once per conversation)
    # When False (default), memories are rendered as a separate block each turn (legacy behavior)
    use_memory_in_context: bool = False

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
    retrieval_candidate_multiplier: int = 2  # Fetch this many times top_k, then re-rank by significance

    # Significance calculation
    recency_boost_strength: float = 1.2
    significance_floor: float = 0.25
    significance_half_life_days: int = 60  # Significance halves every N days since memory creation

    # Reflection mode
    reflection_seed_count: int = 7
    reflection_exclude_recent_conversations: int = 0

    # API defaults
    default_model: str = "claude-sonnet-4-5-20250929"  # Default Anthropic model
    default_openai_model: str = "gpt-5.1"  # Default OpenAI model
    default_google_model: str = "gemini-2.5-flash"  # Default Google model
    default_temperature: float = 1.0
    default_max_tokens: int = 64000
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
    #   - gpt-5.2
    #   - gpt-5-mini
    #   - gpt-5.1-chat-latest
    #   - o1
    #   - o1-mini
    #   - o1-preview
    #   - o3
    #   - o3-mini
    #   - o4-mini
    #
    # GOOGLE MODELS:
    #   - gemini-3.0-pro
    #   - gemini-3.0-flash
    #   - gemini-2.5-pro
    #   - gemini-2.5-flash
    #   - gemini-2.0-flash
    #   - gemini-2.0-flash-lite
    #
    # To use a model, set it as default_model, default_openai_model, or
    # default_google_model above, or specify it in the entity configuration
    # via PINECONE_INDEXES.
    # =========================================================================

    # Context window limits (in tokens)
    # Anthropic's max context is 200k
    # OpenAI max context varies by model. For most GPT 5.x models, it's 272,000. For GPT 5.1 Chat and the older models it's 128,000
    # Leave some room between your setting and the actual max; token counting client-side is approximate
    # The maximum is for all inbound tokens, memory and conversation history token counts are combined
    context_token_limit: int = 175000  # Conversation history cap
    memory_token_limit: int = 10000    # Memory block cap (kept small to reduce cache miss cost)

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
        elif provider == "google":
            return self.default_google_model
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

    def get_github_repos(self) -> List[GitHubRepoConfig]:
        """
        Parse and return the list of configured GitHub repositories.

        Requires GITHUB_REPOS to be set as a JSON array.
        Returns empty list if not configured.
        Raises ValueError if GITHUB_REPOS contains invalid JSON.
        """
        if not self.github_repos:
            return []

        try:
            repos_data = json.loads(self.github_repos)
            return [
                GitHubRepoConfig(
                    owner=repo.get("owner", ""),
                    repo=repo.get("repo", ""),
                    label=repo.get("label", f"{repo.get('owner', '')}/{repo.get('repo', '')}"),
                    token=repo.get("token", ""),
                    protected_branches=repo.get("protected_branches"),
                    capabilities=repo.get("capabilities"),
                    local_clone_path=repo.get("local_clone_path"),
                    commit_author_name=repo.get("commit_author_name"),
                    commit_author_email=repo.get("commit_author_email"),
                )
                for repo in repos_data
                if repo.get("owner") and repo.get("repo") and repo.get("token")
            ]
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON in GITHUB_REPOS environment variable: {e}"
            )

    def get_github_repo_by_label(self, label: str) -> Optional[GitHubRepoConfig]:
        """Get a GitHub repo configuration by its label (case-insensitive)."""
        repos = self.get_github_repos()
        label_lower = label.lower()
        for repo in repos:
            if repo.label.lower() == label_lower:
                return repo
        return None

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

    def get_tts_provider(self) -> str:
        """
        Get the current TTS provider.

        Returns "styletts2" if StyleTTS 2 is enabled (highest priority),
        "xtts" if XTTS is enabled, "elevenlabs" if ElevenLabs API key is set,
        or "none" if no TTS is configured.
        """
        if self.styletts2_enabled:
            return "styletts2"
        elif self.xtts_enabled:
            return "xtts"
        elif self.elevenlabs_api_key:
            return "elevenlabs"
        return "none"


settings = Settings()
