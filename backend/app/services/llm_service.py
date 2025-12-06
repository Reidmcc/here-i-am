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
from app.config import settings


class ModelProvider(str, Enum):
    """Supported LLM providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


# Model to provider mapping
MODEL_PROVIDER_MAP = {
    # Anthropic Claude 4.5 models
    "claude-sonnet-4-5-20250929": ModelProvider.ANTHROPIC,
    "claude-opus-4-5-20251101": ModelProvider.ANTHROPIC,
    "claude-haiku-4-5-20251001": ModelProvider.ANTHROPIC,
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
    "gpt-5-mini": ModelProvider.OPENAI,
    "gpt-5.1-chat-latest": ModelProvider.OPENAI,
    # OpenAI o-series models
    "o1": ModelProvider.OPENAI,
    "o1-mini": ModelProvider.OPENAI,
    "o1-preview": ModelProvider.OPENAI,
    "o3": ModelProvider.OPENAI,
    "o3-mini": ModelProvider.OPENAI,
    "o4-mini": ModelProvider.OPENAI,
}


# Available models by provider
AVAILABLE_MODELS = {
    ModelProvider.ANTHROPIC: [
        {"id": "claude-sonnet-4-5-20250929", "name": "Claude Sonnet 4.5"},
        {"id": "claude-opus-4-5-20251101", "name": "Claude Opus 4.5"},
        {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
        {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
        {"id": "claude-opus-4-20250514", "name": "Claude Opus 4"},
    ],
    ModelProvider.OPENAI: [
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

        return providers

    def get_all_available_models(self) -> List[Dict[str, Any]]:
        """Get flat list of all available models from configured providers."""
        models = []
        for provider_info in self.get_available_providers():
            for model in provider_info["models"]:
                # Check if model supports temperature
                # All Anthropic models support temperature
                # OpenAI models check against MODELS_WITHOUT_TEMPERATURE
                if provider_info["id"] == ModelProvider.OPENAI.value:
                    supports_temp = model["id"] not in OpenAIService.MODELS_WITHOUT_TEMPERATURE
                else:
                    supports_temp = True

                models.append({
                    **model,
                    "provider": provider_info["id"],
                    "provider_name": provider_info["name"],
                    "temperature_supported": supports_temp,
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

        Returns:
            Dict with 'content', 'model', 'usage', 'stop_reason' keys.
            For Anthropic with caching, usage may include cache_creation_input_tokens
            and cache_read_input_tokens.

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
            )
        elif provider == ModelProvider.OPENAI:
            return await openai_service.send_message(
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
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message to the appropriate LLM provider with streaming response.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "done", "content": str, "model": str, "usage": dict, "stop_reason": str}
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
            ):
                yield event
        elif provider == ModelProvider.OPENAI:
            async for event in openai_service.send_message_stream(
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
        current_message: str,
        model: Optional[str] = None,
        conversation_start_date: Optional[datetime] = None,
        enable_caching: bool = True,
        new_memory_ids: Optional[Set[str]] = None,
        # Cache-aware parameters for proper cache hits
        cached_memories: Optional[List[Dict[str, Any]]] = None,
        cached_context: Optional[List[Dict[str, str]]] = None,
        new_context: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build the message list for API call with memory injection.

        Uses the appropriate service based on model/provider.
        When enable_caching=True and using Anthropic, adds cache_control markers
        to optimize caching.

        For cache hits, the cached_memories and cached_context must be IDENTICAL
        to the previous call. New content goes in new_context and is added after
        the cache breakpoints.

        Args:
            memories: List of memory dicts to inject
            conversation_context: Previous messages in conversation
            current_message: The current user message
            model: Model ID (used to determine provider)
            conversation_start_date: When the conversation started
            enable_caching: Enable Anthropic prompt caching (default True)
            new_memory_ids: Set of memory IDs just retrieved this turn
            cached_memories: Memories that were cached in the previous call
            cached_context: Context messages that were cached in the previous call
            new_context: New context messages added since last cache

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
            new_memory_ids=new_memory_ids,
            cached_memories=cached_memories,
            cached_context=cached_context,
            new_context=new_context,
        )


# Singleton instance
llm_service = LLMService()
