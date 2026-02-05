"""
Unified LLM Service

Provides a single interface for interacting with multiple LLM providers
(Anthropic Claude, OpenAI GPT). Routes requests based on model provider
configuration.
"""

from typing import Optional, List, Dict, Any, AsyncIterator, Set
from datetime import datetime
from enum import Enum

from app.services import anthropic_service, openai_service
from app.services.openai_service import OpenAIService
from app.services.google_service import google_service, GoogleService
from app.config import settings


class ModelProvider(str, Enum):
    """Supported LLM providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"


# Model to provider mapping
MODEL_PROVIDER_MAP = {
    # Anthropic Claude 4.5 models
    "claude-sonnet-4-5-20250929": ModelProvider.ANTHROPIC,
    "claude-opus-4-5-20251101": ModelProvider.ANTHROPIC,
    "claude-haiku-4-5-20251001": ModelProvider.ANTHROPIC,
    # Anthropic Claude 4.6 models
    "claude-opus-4-6": ModelProvider.ANTHROPIC,
    # Anthropic Claude 4 models
    "claude-sonnet-4-20250514": ModelProvider.ANTHROPIC,
    "claude-opus-4-20250514": ModelProvider.ANTHROPIC,
    # OpenAI GPT-4 models
    "gpt-4o": ModelProvider.OPENAI,
    "gpt-4o-mini": ModelProvider.OPENAI,
    "gpt-4-turbo": ModelProvider.OPENAI,
    "gpt-4": ModelProvider.OPENAI,
    "gpt-3.5-turbo": ModelProvider.OPENAI,
    # OpenAI GPT-5 models
    "gpt-5.1": ModelProvider.OPENAI,
    "gpt-5.2": ModelProvider.OPENAI,
    "gpt-5-mini": ModelProvider.OPENAI,
    "gpt-5.1-chat-latest": ModelProvider.OPENAI,
    # OpenAI o-series models
    "o1": ModelProvider.OPENAI,
    "o1-mini": ModelProvider.OPENAI,
    "o1-preview": ModelProvider.OPENAI,
    "o3": ModelProvider.OPENAI,
    "o3-mini": ModelProvider.OPENAI,
    "o4-mini": ModelProvider.OPENAI,
    # Google Gemini 3 models
    "gemini-3.0-flash": ModelProvider.GOOGLE,
    "gemini-3.0-pro": ModelProvider.GOOGLE,
    # Google Gemini 2.x models
    "gemini-2.5-pro": ModelProvider.GOOGLE,
    "gemini-2.5-flash": ModelProvider.GOOGLE,
    "gemini-2.0-flash": ModelProvider.GOOGLE,
    "gemini-2.0-flash-lite": ModelProvider.GOOGLE,
}


# Available models by provider
AVAILABLE_MODELS = {
    ModelProvider.ANTHROPIC: [
        {"id": "claude-opus-4-6", "name": "Claude Opus 4.6"},
        {"id": "claude-sonnet-4-5-20250929", "name": "Claude Sonnet 4.5"},
        {"id": "claude-opus-4-5-20251101", "name": "Claude Opus 4.5"},
        {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
        {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
        {"id": "claude-opus-4-20250514", "name": "Claude Opus 4"},
    ],
    ModelProvider.OPENAI: [
        {"id": "gpt-5.2", "name": "GPT-5.2"},
        {"id": "gpt-5.1", "name": "GPT-5.1"},
        {"id": "gpt-5-mini", "name": "GPT-5 Mini"},
        {"id": "gpt-5.1-chat-latest", "name": "GPT-5.1 Chat (Latest)"},
        {"id": "gpt-4o", "name": "GPT-4o"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
        {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
        {"id": "o4-mini", "name": "o4 Mini"},
        {"id": "o3", "name": "o3"},
        {"id": "o3-mini", "name": "o3 Mini"},
        {"id": "o1", "name": "o1"},
        {"id": "o1-mini", "name": "o1 Mini"},
    ],
    ModelProvider.GOOGLE: [
        {"id": "gemini-3.0-pro", "name": "Gemini 3.0 Pro"},
        {"id": "gemini-3.0-flash", "name": "Gemini 3.0 Flash"},
        {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
        {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
        {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
        {"id": "gemini-2.0-flash-lite", "name": "Gemini 2.0 Flash Lite"},
    ],
}


class LLMService:
    """
    Unified LLM service that routes requests to the appropriate provider.
    """

    def get_provider_for_model(self, model: str) -> Optional[ModelProvider]:
        """Determine the provider for a given model ID."""
        return MODEL_PROVIDER_MAP.get(model)

    def is_provider_configured(self, provider: ModelProvider) -> bool:
        """Check if a provider is configured with API keys."""
        if provider == ModelProvider.ANTHROPIC:
            return bool(settings.anthropic_api_key)
        elif provider == ModelProvider.OPENAI:
            return openai_service.is_configured()
        elif provider == ModelProvider.GOOGLE:
            return google_service.is_configured()
        return False

    def get_available_providers(self) -> List[Dict[str, Any]]:
        """Get list of configured providers with their available models."""
        providers = []

        if self.is_provider_configured(ModelProvider.ANTHROPIC):
            providers.append({
                "id": ModelProvider.ANTHROPIC.value,
                "name": "Anthropic",
                "models": AVAILABLE_MODELS[ModelProvider.ANTHROPIC],
                "default_model": settings.default_model,
            })

        if self.is_provider_configured(ModelProvider.OPENAI):
            providers.append({
                "id": ModelProvider.OPENAI.value,
                "name": "OpenAI",
                "models": AVAILABLE_MODELS[ModelProvider.OPENAI],
                "default_model": settings.default_openai_model,
            })

        if self.is_provider_configured(ModelProvider.GOOGLE):
            providers.append({
                "id": ModelProvider.GOOGLE.value,
                "name": "Google",
                "models": AVAILABLE_MODELS[ModelProvider.GOOGLE],
                "default_model": settings.default_google_model,
            })

        return providers

    def get_all_available_models(self) -> List[Dict[str, Any]]:
        """Get flat list of all available models from configured providers."""
        models = []
        for provider_info in self.get_available_providers():
            for model in provider_info["models"]:
                # Check if model supports temperature
                # All Anthropic and Google models support temperature
                # OpenAI models check against MODELS_WITHOUT_TEMPERATURE
                if provider_info["id"] == ModelProvider.OPENAI.value:
                    supports_temp = model["id"] not in OpenAIService.MODELS_WITHOUT_TEMPERATURE
                    supports_verbosity = model["id"] in OpenAIService.MODELS_WITH_VERBOSITY
                elif provider_info["id"] == ModelProvider.GOOGLE.value:
                    supports_temp = True  # All Gemini models support temperature
                    supports_verbosity = False
                else:
                    supports_temp = True
                    supports_verbosity = False

                models.append({
                    **model,
                    "provider": provider_info["id"],
                    "provider_name": provider_info["name"],
                    "temperature_supported": supports_temp,
                    "verbosity_supported": supports_verbosity,
                })
        return models

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        """Count tokens for a text string. Uses tiktoken for both providers."""
        # Both services use tiktoken with cl100k_base
        return anthropic_service.count_tokens(text)

    async def send_message(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        enable_caching: bool = True,
        verbosity: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to the appropriate LLM provider based on model.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model ID to use (determines provider)
            system_prompt: Optional system prompt
            temperature: Temperature setting
            max_tokens: Max tokens in response
            enable_caching: Enable Anthropic prompt caching (default True, ignored for OpenAI)
            verbosity: Verbosity level for gpt-5.1 models (low, medium, high)
            tools: Optional list of tool definitions (Anthropic format, converted for OpenAI)

        Returns:
            Dict with 'content', 'model', 'usage', 'stop_reason' keys.
            For Anthropic with caching, usage may include cache_creation_input_tokens
            and cache_read_input_tokens.
            For Anthropic with tools, response may include 'content_blocks' and 'tool_use'.

        Raises:
            ValueError: If model provider not configured or unknown model
        """
        provider = self.get_provider_for_model(model)

        if provider is None:
            # Unknown model - try to infer from name pattern
            if model.startswith("claude"):
                provider = ModelProvider.ANTHROPIC
            elif model.startswith("gpt") or model.startswith("o"):
                provider = ModelProvider.OPENAI
            elif model.startswith("gemini"):
                provider = ModelProvider.GOOGLE
            else:
                raise ValueError(f"Unknown model: {model}")

        if not self.is_provider_configured(provider):
            raise ValueError(f"Provider {provider.value} is not configured (missing API key)")

        if provider == ModelProvider.ANTHROPIC:
            return await anthropic_service.send_message(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_caching=enable_caching,
                tools=tools,
            )
        elif provider == ModelProvider.OPENAI:
            return await openai_service.send_message(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                verbosity=verbosity,
                tools=tools,
            )
        elif provider == ModelProvider.GOOGLE:
            # Tool use not currently supported for Google in this implementation
            return await google_service.send_message(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    async def send_message_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        enable_caching: bool = True,
        verbosity: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message to the appropriate LLM provider with streaming response.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "tool_use_start", "tool_use": dict} - Start of a tool use block (Anthropic only)
        - {"type": "done", "content": str, "content_blocks": list, "tool_use": list|None, "model": str, "usage": dict, "stop_reason": str}
        - {"type": "error", "error": str}

        For Anthropic with caching enabled, the "done" event's usage dict may include:
        - cache_creation_input_tokens: Tokens written to cache
        - cache_read_input_tokens: Tokens read from cache (90% cost reduction)
        """
        provider = self.get_provider_for_model(model)

        if provider is None:
            if model.startswith("claude"):
                provider = ModelProvider.ANTHROPIC
            elif model.startswith("gpt") or model.startswith("o"):
                provider = ModelProvider.OPENAI
            elif model.startswith("gemini"):
                provider = ModelProvider.GOOGLE
            else:
                yield {"type": "error", "error": f"Unknown model: {model}"}
                return

        if not self.is_provider_configured(provider):
            yield {"type": "error", "error": f"Provider {provider.value} is not configured (missing API key)"}
            return

        if provider == ModelProvider.ANTHROPIC:
            async for event in anthropic_service.send_message_stream(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_caching=enable_caching,
                tools=tools,
            ):
                yield event
        elif provider == ModelProvider.OPENAI:
            async for event in openai_service.send_message_stream(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                verbosity=verbosity,
                tools=tools,
            ):
                yield event
        elif provider == ModelProvider.GOOGLE:
            # Tool use not currently supported for Google in this implementation
            async for event in google_service.send_message_stream(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield event
        else:
            yield {"type": "error", "error": f"Unsupported provider: {provider}"}

    def build_messages_with_memories(
        self,
        memories: List[Dict[str, Any]],
        conversation_context: List[Dict[str, str]],
        current_message: Optional[str],
        model: Optional[str] = None,
        conversation_start_date: Optional[datetime] = None,
        enable_caching: bool = True,
        # Cache-aware parameters for proper cache hits
        cached_context: Optional[List[Dict[str, str]]] = None,
        new_context: Optional[List[Dict[str, str]]] = None,
        # Multi-entity conversation parameters
        is_multi_entity: bool = False,
        entity_labels: Optional[Dict[str, str]] = None,
        responding_entity_label: Optional[str] = None,
        # Custom role labels for context formatting
        user_display_name: Optional[str] = None,
        # Attachments (images and files) - ephemeral, not stored
        attachments: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build the message list for API call with memory injection.

        Uses the appropriate service based on model/provider.
        When enable_caching=True and using Anthropic, adds cache_control markers
        to optimize caching.

        Cache structure (conversation-first):
        1. Cached conversation history (with cache breakpoint at end)
        2. New conversation history (uncached)
        3. Memories (after conversation, so retrievals don't invalidate cache)
        4. Current user message (with optional attachments for multimodal)

        For cache hits, the cached_context must be IDENTICAL to the previous call.

        For multi-entity conversations, a header is added explaining the conversation
        structure and participant labels.

        Attachments (images and files) are ephemeral - they are included in the
        current message for multimodal models but NOT stored in conversation
        history or memories. The AI's response becomes the persisted context.

        Args:
            memories: List of memory dicts to inject
            conversation_context: Previous messages in conversation
            current_message: The current user message (None for multi-entity continuation)
            model: Model ID (used to determine provider)
            conversation_start_date: When the conversation started
            enable_caching: Enable Anthropic prompt caching (default True)
            cached_context: Context messages that were cached in the previous call
            new_context: New context messages added since last cache
            is_multi_entity: True if this is a multi-entity conversation
            entity_labels: Mapping of entity_id to display label
            responding_entity_label: Label of the entity receiving this context
            user_display_name: Custom display name for the user/researcher
            attachments: Optional dict with "images" and "files" lists

        Returns:
            List of message dicts formatted for the LLM API
        """
        # Determine if we should use caching based on provider
        provider = self.get_provider_for_model(model) if model else ModelProvider.ANTHROPIC
        use_caching = enable_caching and provider == ModelProvider.ANTHROPIC

        return anthropic_service.build_messages_with_memories(
            memories=memories,
            conversation_context=conversation_context,
            current_message=current_message,
            conversation_start_date=conversation_start_date,
            enable_caching=use_caching,
            cached_context=cached_context,
            new_context=new_context,
            is_multi_entity=is_multi_entity,
            entity_labels=entity_labels,
            responding_entity_label=responding_entity_label,
            user_display_name=user_display_name,
            attachments=attachments,
            provider=provider,
        )


# Singleton instance
llm_service = LLMService()
