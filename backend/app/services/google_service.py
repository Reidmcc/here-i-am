from typing import Optional, List, Dict, Any, AsyncIterator
from google import genai
from google.genai import types
from app.config import settings
from app.services.session_helpers import get_message_content_text
from app.services.thinking_effort import resolve_effort
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

    # Model families that think. Matched as prefixes so new point releases in
    # these families are covered without a code change.
    THINKING_MODEL_PREFIXES = ("gemini-3", "gemini-2.5")

    # Gemini 3 exposes thinking depth as a two-value level.
    THINKING_LEVEL_MAP = {
        "low": "low",
        "medium": "low",
        "high": "high",
        "xhigh": "high",
        "max": "high",
    }

    # Gemini 2.5 exposes it as a token budget instead. These sit inside the
    # budget range every 2.5 model accepts (the tightest ceiling is Flash's
    # 24576), so one table covers Flash and Pro alike.
    THINKING_BUDGET_MAP = {
        "low": 2048,
        "medium": 8192,
        "high": 16384,
        "xhigh": 20480,
        "max": 24576,
    }

    def __init__(self):
        self._client = None
        self._encoder = None

    def _ensure_client(self) -> genai.Client:
        """Lazily initialize the Google AI client."""
        if self._client is None:
            if settings.google_api_key:
                self._client = genai.Client(api_key=settings.google_api_key)
            else:
                raise ValueError("Google API key not configured")
        return self._client

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

    def supports_thinking(self, model: str) -> bool:
        """
        Whether the model thinks and takes a thinking configuration.

        Gemini 2.5 and 3 do; the 2.0 generation does not.
        """
        return model.startswith(self.THINKING_MODEL_PREFIXES)

    def _build_thinking_config(
        self,
        model: str,
        thinking_effort: Optional[str],
    ) -> Optional["types.ThinkingConfig"]:
        """
        Translate a canonical effort level into a Gemini thinking config.

        Google has moved this control twice, and the project's floor
        (google-genai>=1.0.0) predates both spellings, so the field set is
        probed at runtime rather than assumed: `thinking_level` (Gemini 3) is
        preferred, `thinking_budget` (Gemini 2.5) is the fallback, and an SDK
        with neither gets no thinking config at all — the model still thinks,
        just at its own default depth.
        """
        if not self.supports_thinking(model):
            return None

        fields = getattr(types.ThinkingConfig, "model_fields", {})
        effort = resolve_effort(thinking_effort)

        if "thinking_level" in fields:
            return types.ThinkingConfig(thinking_level=self.THINKING_LEVEL_MAP[effort])
        if "thinking_budget" in fields:
            return types.ThinkingConfig(thinking_budget=self.THINKING_BUDGET_MAP[effort])

        logger.info(
            "[THINKING] Installed google-genai exposes no thinking level/budget field; "
            "using the model's default thinking depth"
        )
        return None

    def _apply_thinking_config(
        self,
        config: "types.GenerateContentConfig",
        model: str,
        thinking_effort: Optional[str],
    ) -> None:
        """Attach a thinking config to the request when one applies."""
        try:
            thinking_config = self._build_thinking_config(model, thinking_effort)
        except Exception as e:
            # A field the installed SDK exposes but the API rejects should cost
            # a log line, not the turn.
            logger.warning(f"[THINKING] Could not build Gemini thinking config: {e}")
            return

        if thinking_config is not None:
            config.thinking_config = thinking_config
            logger.info(f"[THINKING] Gemini thinking config {thinking_config} for model {model}")

    def _convert_messages_to_contents(
        self,
        messages: List[Dict[str, str]],
    ) -> List[types.Content]:
        """
        Convert messages from standard format to Gemini Content format.

        Returns list of Content objects.
        """
        contents = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            # Handle array format (from Anthropic cache_control or replayed
            # tool exchanges, whose blocks may include thinking/tool_use/
            # tool_result) - render to text; indexing content[0]["text"]
            # would KeyError on non-text leading blocks
            if isinstance(content, list):
                content = get_message_content_text(content)
                logger.warning(f"[GOOGLE] Converted array content to string for role={role} (len={len(content)})")

            # Map roles: 'user' stays 'user', 'assistant' becomes 'model'
            gemini_role = "model" if role == "assistant" else "user"

            contents.append(types.Content(
                role=gemini_role,
                parts=[types.Part.from_text(text=content)]
            ))

        return contents

    async def send_message(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to Google AI (Gemini) API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt (system instruction)
            model: Model to use (defaults to gemini-2.5-flash)
            temperature: Temperature setting (defaults to 1.0)
            max_tokens: Max tokens in response (defaults to 4096)
            thinking_effort: Optional thinking effort level (None uses the configured default)

        Returns:
            Dict with 'content', 'model', 'usage' keys
        """
        client = self._ensure_client()

        model_name = model or settings.default_google_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens

        # Convert messages to Gemini format
        contents = self._convert_messages_to_contents(messages)

        # Build generation config
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        # Add system instruction if provided
        if system_prompt:
            config.system_instruction = system_prompt

        self._apply_thinking_config(config, model_name, thinking_effort)

        logger.info(f"[GOOGLE] Sending {len(contents)} messages to API with model={model_name}")

        # Use async client
        response = await client.aio.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )

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

        if response.usage_metadata:
            usage["input_tokens"] = response.usage_metadata.prompt_token_count or 0
            usage["output_tokens"] = response.usage_metadata.candidates_token_count or 0
            # Include cached tokens if available
            if hasattr(response.usage_metadata, 'cached_content_token_count') and response.usage_metadata.cached_content_token_count:
                usage["cached_tokens"] = response.usage_metadata.cached_content_token_count

        # Determine stop reason
        stop_reason = "end_turn"
        if response.candidates and len(response.candidates) > 0:
            finish_reason = response.candidates[0].finish_reason
            if finish_reason:
                # Map Gemini finish reasons to standard format
                finish_reason_str = str(finish_reason).lower()
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
        thinking_effort: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message to Google AI (Gemini) API with streaming response.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "done", "content": str, "model": str, "usage": dict, "stop_reason": str}
        - {"type": "error", "error": str}
        """
        client = self._ensure_client()

        model_name = model or settings.default_google_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens

        # Convert messages to Gemini format
        contents = self._convert_messages_to_contents(messages)

        # Build generation config
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        # Add system instruction if provided
        if system_prompt:
            config.system_instruction = system_prompt

        self._apply_thinking_config(config, model_name, thinking_effort)

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
            async for chunk in await client.aio.models.generate_content_stream(
                model=model_name,
                contents=contents,
                config=config,
            ):
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
                        finish_reason_str = str(candidate.finish_reason).lower()
                        if "max_tokens" in finish_reason_str or "length" in finish_reason_str:
                            stop_reason = "max_tokens"
                        elif "safety" in finish_reason_str:
                            stop_reason = "safety"
                        elif "stop" in finish_reason_str:
                            stop_reason = "end_turn"

                # Extract usage metadata from final chunk
                if chunk.usage_metadata:
                    usage["input_tokens"] = chunk.usage_metadata.prompt_token_count or 0
                    usage["output_tokens"] = chunk.usage_metadata.candidates_token_count or 0
                    if hasattr(chunk.usage_metadata, 'cached_content_token_count') and chunk.usage_metadata.cached_content_token_count:
                        usage["cached_tokens"] = chunk.usage_metadata.cached_content_token_count

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
