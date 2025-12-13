from typing import Optional, List, Dict, Any, AsyncIterator
import google.generativeai as genai
from google.generativeai.types import GenerationConfig, ContentDict
from app.config import settings
import tiktoken
import logging

logger = logging.getLogger(__name__)


class GoogleService:
    """Service for Google AI (Gemini) API interactions."""

    # Gemini 3 and 2.x models
    SUPPORTED_MODELS = {
        # Gemini 3 models
        "gemini-3.0-flash",
        "gemini-3.0-pro",
        # Gemini 2.x models
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    }

    # Models that support temperature parameter (all current models do)
    MODELS_WITH_TEMPERATURE = SUPPORTED_MODELS

    def __init__(self):
        self._configured = False
        self._encoder = None

    def _ensure_configured(self):
        """Ensure the API is configured with the API key."""
        if not self._configured:
            if settings.google_api_key:
                genai.configure(api_key=settings.google_api_key)
                self._configured = True
            else:
                raise ValueError("Google API key not configured")

    def is_configured(self) -> bool:
        """Check if Google AI is configured with an API key."""
        return bool(settings.google_api_key)

    @property
    def encoder(self):
        if self._encoder is None:
            # Use cl100k_base encoding for approximate token counting
            self._encoder = tiktoken.get_encoding("cl100k_base")
        return self._encoder

    def count_tokens(self, text: str) -> int:
        """Approximate token count for a text string."""
        return len(self.encoder.encode(text))

    def _supports_temperature(self, model: str) -> bool:
        """Check if model supports the temperature parameter."""
        # All Gemini models support temperature
        return True

    def _convert_messages_to_contents(
        self,
        messages: List[Dict[str, str]],
    ) -> List[ContentDict]:
        """
        Convert messages from standard format to Gemini Content format.

        Returns list of content dicts.
        """
        contents = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            # Handle array format (from Anthropic cache_control) - extract text
            if isinstance(content, list):
                content = content[0]["text"] if content else ""
                logger.warning(f"[GOOGLE] Converted array content to string for role={role} (len={len(content)})")

            # Map roles: 'user' stays 'user', 'assistant' becomes 'model'
            gemini_role = "model" if role == "assistant" else "user"

            contents.append({
                "role": gemini_role,
                "parts": [{"text": content}]
            })

        return contents

    async def send_message(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to Google AI (Gemini) API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt (system instruction)
            model: Model to use (defaults to gemini-2.5-flash)
            temperature: Temperature setting (defaults to 1.0)
            max_tokens: Max tokens in response (defaults to 4096)

        Returns:
            Dict with 'content', 'model', 'usage' keys
        """
        self._ensure_configured()

        model_name = model or settings.default_google_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens

        # Convert messages to Gemini format
        contents = self._convert_messages_to_contents(messages)

        # Build generation config
        generation_config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        # Create model with optional system instruction
        model_kwargs = {"model_name": model_name, "generation_config": generation_config}
        if system_prompt:
            model_kwargs["system_instruction"] = system_prompt

        gemini_model = genai.GenerativeModel(**model_kwargs)

        logger.info(f"[GOOGLE] Sending {len(contents)} messages to API with model={model_name}")

        # Use async generation
        response = await gemini_model.generate_content_async(contents)

        # Extract content from response
        content = ""
        if response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                content = "".join(part.text for part in candidate.content.parts if hasattr(part, 'text') and part.text)

        # Build usage dict
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
        }

        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            usage["input_tokens"] = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
            usage["output_tokens"] = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0
            # Include cached tokens if available
            cached = getattr(response.usage_metadata, 'cached_content_token_count', None)
            if cached:
                usage["cached_tokens"] = cached

        # Determine stop reason
        stop_reason = "end_turn"
        if response.candidates and len(response.candidates) > 0:
            finish_reason = response.candidates[0].finish_reason
            if finish_reason:
                # Map Gemini finish reasons to standard format
                finish_reason_name = finish_reason.name if hasattr(finish_reason, 'name') else str(finish_reason)
                finish_reason_str = finish_reason_name.lower()
                if "max_tokens" in finish_reason_str or "length" in finish_reason_str:
                    stop_reason = "max_tokens"
                elif "safety" in finish_reason_str:
                    stop_reason = "safety"
                elif "stop" in finish_reason_str:
                    stop_reason = "end_turn"

        logger.info(f"[GOOGLE] API Response - input: {usage.get('input_tokens')}, output: {usage.get('output_tokens')}")

        return {
            "content": content,
            "model": model_name,
            "usage": usage,
            "stop_reason": stop_reason,
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
        Send a message to Google AI (Gemini) API with streaming response.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "done", "content": str, "model": str, "usage": dict, "stop_reason": str}
        - {"type": "error", "error": str}
        """
        self._ensure_configured()

        model_name = model or settings.default_google_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens

        # Convert messages to Gemini format
        contents = self._convert_messages_to_contents(messages)

        # Build generation config
        generation_config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        # Create model with optional system instruction
        model_kwargs = {"model_name": model_name, "generation_config": generation_config}
        if system_prompt:
            model_kwargs["system_instruction"] = system_prompt

        gemini_model = genai.GenerativeModel(**model_kwargs)

        logger.info(f"[GOOGLE] Streaming {len(contents)} messages to API with model={model_name}")

        try:
            # Yield start event
            yield {"type": "start", "model": model_name}

            full_content = ""
            stop_reason = "end_turn"
            usage = {
                "input_tokens": 0,
                "output_tokens": 0,
            }

            # Use async streaming
            response = await gemini_model.generate_content_async(contents, stream=True)

            async for chunk in response:
                # Extract text from chunk
                if chunk.candidates and len(chunk.candidates) > 0:
                    candidate = chunk.candidates[0]
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, 'text') and part.text:
                                full_content += part.text
                                yield {"type": "token", "content": part.text}

                    # Check finish reason
                    if candidate.finish_reason:
                        finish_reason_name = candidate.finish_reason.name if hasattr(candidate.finish_reason, 'name') else str(candidate.finish_reason)
                        finish_reason_str = finish_reason_name.lower()
                        if "max_tokens" in finish_reason_str or "length" in finish_reason_str:
                            stop_reason = "max_tokens"
                        elif "safety" in finish_reason_str:
                            stop_reason = "safety"
                        elif "stop" in finish_reason_str:
                            stop_reason = "end_turn"

                # Extract usage metadata from chunks (usually in final chunk)
                if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                    usage["input_tokens"] = getattr(chunk.usage_metadata, 'prompt_token_count', 0) or 0
                    usage["output_tokens"] = getattr(chunk.usage_metadata, 'candidates_token_count', 0) or 0
                    cached = getattr(chunk.usage_metadata, 'cached_content_token_count', None)
                    if cached:
                        usage["cached_tokens"] = cached

            logger.info(f"[GOOGLE] Stream API Response - input: {usage.get('input_tokens')}, output: {usage.get('output_tokens')}")

            # Yield final done event
            yield {
                "type": "done",
                "content": full_content,
                "model": model_name,
                "usage": usage,
                "stop_reason": stop_reason,
            }

        except Exception as e:
            logger.error(f"[GOOGLE] Streaming error: {e}")
            yield {"type": "error", "error": str(e)}


# Singleton instance
google_service = GoogleService()
