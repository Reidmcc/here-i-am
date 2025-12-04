"""
Unified LLM Service

Provides a single interface for interacting with multiple LLM providers
(Anthropic Claude, OpenAI GPT). Routes requests based on model provider
configuration.
"""

from typing import Optional, List, Dict, Any
from enum import Enum

from app.services.anthropic_service import anthropic_service
from app.services.openai_service import openai_service
from app.config import settings


class ModelProvider(str, Enum):
    """Supported LLM providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


# Model to provider mapping
MODEL_PROVIDER_MAP = {
    # Anthropic Claude models
    "claude-sonnet-4-20250514": ModelProvider.ANTHROPIC,
    "claude-opus-4-20250514": ModelProvider.ANTHROPIC,
    "claude-3-5-sonnet-20241022": ModelProvider.ANTHROPIC,
    "claude-3-5-haiku-20241022": ModelProvider.ANTHROPIC,
    "claude-3-opus-20240229": ModelProvider.ANTHROPIC,
    "claude-3-sonnet-20240229": ModelProvider.ANTHROPIC,
    "claude-3-haiku-20240307": ModelProvider.ANTHROPIC,
    # OpenAI GPT models
    "gpt-4o": ModelProvider.OPENAI,
    "gpt-4o-mini": ModelProvider.OPENAI,
    "gpt-4-turbo": ModelProvider.OPENAI,
    "gpt-4": ModelProvider.OPENAI,
    "gpt-3.5-turbo": ModelProvider.OPENAI,
    "o1": ModelProvider.OPENAI,
    "o1-mini": ModelProvider.OPENAI,
    "o1-preview": ModelProvider.OPENAI,
}


# Available models by provider
AVAILABLE_MODELS = {
    ModelProvider.ANTHROPIC: [
        {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
        {"id": "claude-opus-4-20250514", "name": "Claude Opus 4"},
        {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet"},
        {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku"},
    ],
    ModelProvider.OPENAI: [
        {"id": "gpt-4o", "name": "GPT-4o"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
        {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
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
                models.append({
                    **model,
                    "provider": provider_info["id"],
                    "provider_name": provider_info["name"],
                })
        return models

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        """Count tokens for a text string. Uses tiktoken for both providers."""
        # Both services use tiktoken with cl100k_base
        return anthropic_service.count_tokens(text)

    async def send_message(
        self,
        messages: List[Dict[str, str]],
        model: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to the appropriate LLM provider based on model.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model ID to use (determines provider)
            system_prompt: Optional system prompt
            temperature: Temperature setting
            max_tokens: Max tokens in response

        Returns:
            Dict with 'content', 'model', 'usage', 'stop_reason' keys

        Raises:
            ValueError: If model provider not configured or unknown model
        """
        provider = self.get_provider_for_model(model)

        if provider is None:
            # Unknown model - try to infer from name pattern
            if model.startswith("claude"):
                provider = ModelProvider.ANTHROPIC
            elif model.startswith("gpt") or model.startswith("o1"):
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

    def build_messages_with_memories(
        self,
        memories: List[Dict[str, Any]],
        conversation_context: List[Dict[str, str]],
        current_message: str,
        model: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        Build the message list for API call with memory injection.

        Uses the appropriate service based on model/provider.
        Both providers currently use the same format.
        """
        # Both services use the same memory injection format
        return anthropic_service.build_messages_with_memories(
            memories=memories,
            conversation_context=conversation_context,
            current_message=current_message,
        )


# Singleton instance
llm_service = LLMService()
