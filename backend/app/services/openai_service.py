from typing import Optional, List, Dict, Any, AsyncIterator
from openai import AsyncOpenAI
from app.config import settings
import tiktoken
import logging

logger = logging.getLogger(__name__)


class OpenAIService:
    """Service for OpenAI API interactions."""

    # Models that use max_completion_tokens instead of max_tokens
    MODELS_WITH_COMPLETION_TOKENS = {
        "o1", "o1-mini", "o1-preview",
        "o3", "o3-mini",
        "o4-mini",
        "gpt-5.1", "gpt-5-mini", "gpt-5.1-chat-latest",
    }

    # Models that don't support the temperature parameter
    MODELS_WITHOUT_TEMPERATURE = {
        "o1", "o1-mini", "o1-preview",
        "o3", "o3-mini",
        "o4-mini",
    }

    # Models that don't support stream_options
    MODELS_WITHOUT_STREAM_OPTIONS = {
        "o1", "o1-mini", "o1-preview",
    }

    def __init__(self):
        self.client = None
        self._encoder = None

    def _uses_completion_tokens(self, model: str) -> bool:
        """Check if model uses max_completion_tokens instead of max_tokens."""
        return model in self.MODELS_WITH_COMPLETION_TOKENS

    def _supports_temperature(self, model: str) -> bool:
        """Check if model supports the temperature parameter."""
        return model not in self.MODELS_WITHOUT_TEMPERATURE

    def _supports_stream_options(self, model: str) -> bool:
        """Check if model supports stream_options parameter."""
        return model not in self.MODELS_WITHOUT_STREAM_OPTIONS

    def _ensure_client(self):
        """Lazily initialize the OpenAI client."""
        if self.client is None:
            if settings.openai_api_key:
                self.client = AsyncOpenAI(api_key=settings.openai_api_key)
            else:
                raise ValueError("OpenAI API key not configured")
        return self.client

    def is_configured(self) -> bool:
        """Check if OpenAI is configured with an API key."""
        return bool(settings.openai_api_key)

    @property
    def encoder(self):
        if self._encoder is None:
            # Use cl100k_base encoding (used by GPT-4, GPT-3.5-turbo)
            self._encoder = tiktoken.get_encoding("cl100k_base")
        return self._encoder

    def count_tokens(self, text: str) -> int:
        """Approximate token count for a text string."""
        return len(self.encoder.encode(text))

    async def send_message(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to OpenAI API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt
            model: Model to use (defaults to gpt-4o)
            temperature: Temperature setting (defaults to 1.0)
            max_tokens: Max tokens in response (defaults to 4096)

        Returns:
            Dict with 'content', 'model', 'usage' keys
        """
        client = self._ensure_client()

        model = model or settings.default_openai_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens

        # Build messages list with optional system prompt
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        # Map message roles (OpenAI uses 'user' and 'assistant')
        for msg in messages:
            role = msg["role"]
            # OpenAI accepts 'user' and 'assistant' directly
            api_messages.append({"role": role, "content": msg["content"]})

        # Build API parameters based on model capabilities
        api_params = {
            "model": model,
            "messages": api_messages,
        }

        # Use max_completion_tokens for newer models, max_tokens for older ones
        if self._uses_completion_tokens(model):
            api_params["max_completion_tokens"] = max_tokens
        else:
            api_params["max_tokens"] = max_tokens

        # Only include temperature for models that support it
        if self._supports_temperature(model):
            api_params["temperature"] = temperature

        response = await client.chat.completions.create(**api_params)

        # Extract content
        content = response.choices[0].message.content or ""

        # Build usage dict with cache information when available
        usage = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
        }

        # Extract cached_tokens from prompt_tokens_details if available
        # OpenAI automatically caches prompts >= 1024 tokens
        if hasattr(response.usage, "prompt_tokens_details") and response.usage.prompt_tokens_details:
            cached_tokens = getattr(response.usage.prompt_tokens_details, "cached_tokens", None)
            if cached_tokens is not None:
                usage["cached_tokens"] = cached_tokens

        # Debug logging for cache results
        logger.info(f"[CACHE] OpenAI API Response - input: {usage.get('input_tokens')}, output: {usage.get('output_tokens')}")
        logger.info(f"[CACHE] OpenAI cached tokens: {usage.get('cached_tokens', 0)}")

        return {
            "content": content,
            "model": response.model,
            "usage": usage,
            "stop_reason": response.choices[0].finish_reason,
        }

    async def send_message_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message to OpenAI API with streaming response.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "done", "content": str, "model": str, "usage": dict, "stop_reason": str}
        - {"type": "error", "error": str}
        """
        client = self._ensure_client()

        model = model or settings.default_openai_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens

        # Build messages list with optional system prompt
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            # Yield start event
            yield {"type": "start", "model": model}

            full_content = ""
            stop_reason = None

            # Build API parameters based on model capabilities
            api_params = {
                "model": model,
                "messages": api_messages,
                "stream": True,
            }

            # Use max_completion_tokens for newer models, max_tokens for older ones
            if self._uses_completion_tokens(model):
                api_params["max_completion_tokens"] = max_tokens
            else:
                api_params["max_tokens"] = max_tokens

            # Only include temperature for models that support it
            if self._supports_temperature(model):
                api_params["temperature"] = temperature

            # Only include stream_options for models that support it
            if self._supports_stream_options(model):
                api_params["stream_options"] = {"include_usage": True}

            stream = await client.chat.completions.create(**api_params)

            input_tokens = 0
            output_tokens = 0
            cached_tokens = 0

            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_content += delta.content
                        yield {"type": "token", "content": delta.content}
                    if chunk.choices[0].finish_reason:
                        stop_reason = chunk.choices[0].finish_reason

                # Usage info comes in the final chunk
                if chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens
                    output_tokens = chunk.usage.completion_tokens
                    # Extract cached_tokens from prompt_tokens_details if available
                    if hasattr(chunk.usage, "prompt_tokens_details") and chunk.usage.prompt_tokens_details:
                        cached_tokens = getattr(chunk.usage.prompt_tokens_details, "cached_tokens", 0) or 0

            # Build usage dict with cache information when available
            usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
            if cached_tokens > 0:
                usage["cached_tokens"] = cached_tokens

            # Debug logging for cache results
            logger.info(f"[CACHE] OpenAI Stream API Response - input: {input_tokens}, output: {output_tokens}")
            logger.info(f"[CACHE] OpenAI cached tokens: {cached_tokens}")

            # Yield final done event
            yield {
                "type": "done",
                "content": full_content,
                "model": model,
                "usage": usage,
                "stop_reason": stop_reason,
            }

        except Exception as e:
            yield {"type": "error", "error": str(e)}


# Singleton instance
openai_service = OpenAIService()
