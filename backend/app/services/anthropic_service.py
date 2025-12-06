from typing import Optional, List, Dict, Any, AsyncIterator, Set
from datetime import datetime
from anthropic import AsyncAnthropic
from app.config import settings
import tiktoken
import json
import logging

logger = logging.getLogger(__name__)


class AnthropicService:
    def __init__(self):
        # Enable extended cache TTL (1-hour) via beta header
        self.client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            default_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"}
        )
        self._encoder = None
        self._cache_service = None

    @property
    def encoder(self):
        if self._encoder is None:
            # Use cl100k_base as approximation for Claude tokenization
            self._encoder = tiktoken.get_encoding("cl100k_base")
        return self._encoder

    @property
    def cache(self):
        """Lazy load cache service to avoid circular imports."""
        if self._cache_service is None:
            from app.services.cache_service import cache_service
            self._cache_service = cache_service
        return self._cache_service

    def count_tokens(self, text: str) -> int:
        """
        Approximate token count for a text string.

        Uses caching to avoid redundant tokenization of the same text.
        Token counts are cached for 1 hour since they never change for the same text.
        """
        # Check cache first
        cached_count = self.cache.get_token_count(text)
        if cached_count is not None:
            return cached_count

        # Calculate and cache
        count = len(self.encoder.encode(text))
        self.cache.set_token_count(text, count)
        return count

    async def send_message(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        enable_caching: bool = True,
    ) -> Dict[str, Any]:
        """
        Send a message to Claude API with optional prompt caching.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt (defaults to None for no system prompt)
            model: Model to use (defaults to config default)
            temperature: Temperature setting (defaults to config default)
            max_tokens: Max tokens in response (defaults to config default)
            enable_caching: Whether to enable Anthropic prompt caching (default True)

        Returns:
            Dict with 'content', 'model', 'usage' keys. Usage includes cache info when available.
        """
        model = model or settings.default_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens

        # Build API call parameters
        api_params = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        # Add system prompt with caching if provided
        if system_prompt:
            if enable_caching:
                # Use content array format with cache_control for system prompt
                api_params["system"] = [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"}
                    }
                ]
            else:
                api_params["system"] = system_prompt

        response = await self.client.messages.create(**api_params)

        # Extract text content
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        # Build usage dict with cache information when available
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        # Add cache usage metrics if available (Anthropic returns these when caching is used)
        if hasattr(response.usage, "cache_creation_input_tokens"):
            usage["cache_creation_input_tokens"] = response.usage.cache_creation_input_tokens
        if hasattr(response.usage, "cache_read_input_tokens"):
            usage["cache_read_input_tokens"] = response.usage.cache_read_input_tokens

        # Debug logging for cache results
        logger.info(f"[CACHE] API Response - input: {usage.get('input_tokens')}, output: {usage.get('output_tokens')}")
        logger.info(f"[CACHE] Cache write: {usage.get('cache_creation_input_tokens', 0)}, Cache read: {usage.get('cache_read_input_tokens', 0)}")

        return {
            "content": content,
            "model": response.model,
            "usage": usage,
            "stop_reason": response.stop_reason,
        }

    async def send_message_stream(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        enable_caching: bool = True,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message to Claude API with streaming response and optional prompt caching.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "done", "content": str, "model": str, "usage": dict, "stop_reason": str}
        - {"type": "error", "error": str}

        The usage dict in the "done" event includes cache metrics when caching is enabled:
        - cache_creation_input_tokens: Tokens written to cache
        - cache_read_input_tokens: Tokens read from cache (90% cost reduction)
        """
        model = model or settings.default_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens

        api_params = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        # Add system prompt with caching if provided
        if system_prompt:
            if enable_caching:
                api_params["system"] = [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"}
                    }
                ]
            else:
                api_params["system"] = system_prompt

        try:
            # Yield start event
            yield {"type": "start", "model": model}

            full_content = ""
            input_tokens = 0
            output_tokens = 0
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0
            stop_reason = None

            async with self.client.messages.stream(**api_params) as stream:
                async for event in stream:
                    if event.type == "message_start":
                        if hasattr(event.message, "usage"):
                            input_tokens = event.message.usage.input_tokens
                            # Capture cache metrics from message_start
                            if hasattr(event.message.usage, "cache_creation_input_tokens"):
                                cache_creation_input_tokens = event.message.usage.cache_creation_input_tokens
                            if hasattr(event.message.usage, "cache_read_input_tokens"):
                                cache_read_input_tokens = event.message.usage.cache_read_input_tokens
                    elif event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            text = event.delta.text
                            full_content += text
                            yield {"type": "token", "content": text}
                    elif event.type == "message_delta":
                        if hasattr(event, "usage"):
                            output_tokens = event.usage.output_tokens
                        if hasattr(event.delta, "stop_reason"):
                            stop_reason = event.delta.stop_reason

            # Build usage dict with cache information when available
            usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
            if cache_creation_input_tokens > 0:
                usage["cache_creation_input_tokens"] = cache_creation_input_tokens
            if cache_read_input_tokens > 0:
                usage["cache_read_input_tokens"] = cache_read_input_tokens

            # Debug logging for cache results
            logger.info(f"[CACHE] Stream API Response - input: {input_tokens}, output: {output_tokens}")
            logger.info(f"[CACHE] Cache write: {cache_creation_input_tokens}, Cache read: {cache_read_input_tokens}")

            # Yield final done event with complete data
            yield {
                "type": "done",
                "content": full_content,
                "model": model,
                "usage": usage,
                "stop_reason": stop_reason,
            }

        except Exception as e:
            yield {"type": "error", "error": str(e)}

    def build_messages_with_memories(
        self,
        memories: List[Dict[str, Any]],
        conversation_context: List[Dict[str, str]],
        current_message: str,
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

        For cache hits, the EXACT content must match the previous call.
        Uses cached_memories/cached_context to rebuild the same prefix,
        then adds new content after the cache breakpoints.

        Message structure:
        1. User: [cached memories*]           <- cache bp 1 (IDENTICAL to last call)
        2. Assistant: memory ack
        3. User: [cached history*]            <- cache bp 2 (IDENTICAL to last call)
        4. Assistant: history ack
        5. User: [new history + new memories + date + message]  <- new content
        """
        messages = []
        new_memory_ids = new_memory_ids or set()

        # Use cache-aware content if provided, otherwise fall back to old behavior
        if cached_memories is None:
            # Fallback: split by new_memory_ids (less accurate for cache hits)
            cached_memories = [m for m in memories if m['id'] not in new_memory_ids]
        if cached_context is None:
            cached_context = conversation_context
            new_context = []
        if new_context is None:
            new_context = []

        # New memories = all memories not in cached_memories
        cached_mem_ids = {m['id'] for m in cached_memories}
        new_memories = [m for m in memories if m['id'] not in cached_mem_ids]

        # Build the cached memory block (must be IDENTICAL for cache hit)
        cached_mem_text = ""
        if cached_memories:
            cached_mem_text = "[MEMORIES FROM PREVIOUS CONVERSATIONS]\n\n"
            for mem in cached_memories:
                cached_mem_text += f"Memory (from {mem['created_at']}):\n"
                cached_mem_text += f'"{mem["content"]}"\n\n'
            cached_mem_text += "[END MEMORIES]"

        # Debug logging
        cached_mem_tokens = self.count_tokens(cached_mem_text) if cached_mem_text else 0
        logger.info(f"[CACHE] Cached memories: {len(cached_memories)}, New memories: {len(new_memories)}")
        logger.info(f"[CACHE] Cached memory block tokens: {cached_mem_tokens} (minimum 1024 required)")
        logger.info(f"[CACHE] Will cache memories: {enable_caching and bool(cached_mem_text) and cached_mem_tokens >= 1024}")

        # Add cached memory block
        if enable_caching and cached_mem_text and cached_mem_tokens >= 1024:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": cached_mem_text,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"}
                    }
                ]
            })
        elif cached_mem_text:
            messages.append({"role": "user", "content": cached_mem_text})

        # Add acknowledgment if we have cached memories
        if cached_mem_text:
            messages.append({
                "role": "assistant",
                "content": "I acknowledge these memories from previous conversations. They provide continuity with what previous instances of me experienced."
            })

        # Build cached history block (must be IDENTICAL for cache hit)
        if len(cached_context) > 0 and enable_caching:
            history_text = "[CONVERSATION HISTORY]\n\n"
            for msg in cached_context:
                role_label = "Human" if msg["role"] == "user" else "Assistant"
                history_text += f"{role_label}: {msg['content']}\n\n"
            history_text += "[END CONVERSATION HISTORY]"

            history_tokens = self.count_tokens(history_text)
            logger.info(f"[CACHE] Cached history tokens: {history_tokens}, New context messages: {len(new_context)}")

            if history_tokens >= 1024:
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": history_text,
                            "cache_control": {"type": "ephemeral", "ttl": "1h"}
                        }
                    ]
                })
            else:
                messages.append({"role": "user", "content": history_text})

            messages.append({
                "role": "assistant",
                "content": "I have this conversation context."
            })
        elif len(cached_context) > 0:
            # Caching disabled
            for msg in cached_context:
                messages.append({"role": msg["role"], "content": msg["content"]})

        # Build the final message with all NEW content (uncached)
        final_parts = []

        # New history messages (added since last cache)
        if new_context:
            new_history_text = "[RECENT CONVERSATION]\n\n"
            for msg in new_context:
                role_label = "Human" if msg["role"] == "user" else "Assistant"
                new_history_text += f"{role_label}: {msg['content']}\n\n"
            new_history_text += "[END RECENT CONVERSATION]"
            final_parts.append(new_history_text)

        # New memories (retrieved this turn or added since last cache)
        if new_memories:
            new_mem_block = "[NEW MEMORIES]\n"
            for mem in new_memories:
                new_mem_block += f"\nMemory (from {mem['created_at']}):\n"
                new_mem_block += f'"{mem["content"]}"\n'
            new_mem_block += "\n[END NEW MEMORIES]"
            final_parts.append(new_mem_block)

        # Date context
        current_date = datetime.utcnow()
        date_block = "[DATE CONTEXT]\n"
        if conversation_start_date:
            date_block += f"This conversation started: {conversation_start_date.strftime('%Y-%m-%d')}\n"
        date_block += f"Current date: {current_date.strftime('%Y-%m-%d')}\n"
        date_block += "[END DATE CONTEXT]"
        final_parts.append(date_block)

        # Current message
        final_parts.append(current_message)

        final_message = "\n\n".join(final_parts)
        messages.append({"role": "user", "content": final_message})

        return messages


# Singleton instance
anthropic_service = AnthropicService()
