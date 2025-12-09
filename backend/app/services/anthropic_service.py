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
        current_message: Optional[str],
        conversation_start_date: Optional[datetime] = None,
        enable_caching: bool = True,
        new_memory_ids: Optional[Set[str]] = None,
        # Caching parameters
        cached_memories: Optional[List[Dict[str, Any]]] = None,
        cached_context: Optional[List[Dict[str, str]]] = None,
        new_context: Optional[List[Dict[str, str]]] = None,
        # Multi-entity conversation parameters
        is_multi_entity: bool = False,
        entity_labels: Optional[Dict[str, str]] = None,
        responding_entity_label: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build the message list for API call with conversation-first caching.

        Cache breakpoint: End of cached conversation history (most stable content)

        Message structure:
        1. User: [CONVERSATION HISTORY] + multi-entity header (if applicable) + cached history msg 1
        2. Assistant: cached history msg 2
        ...
        N. Last cached history msg*       <- cache breakpoint (cache_control here)
        N+1. New history messages (uncached, grows until consolidation)
        ...
        M-1. User: [/CONVERSATION HISTORY] + [MEMORIES] + memories block + [/MEMORIES]
        M. User: [CURRENT USER MESSAGE] + date context + current message

        If current_message is None (multi-entity continuation), the entity is prompted
        to continue the conversation without a new human message.

        For multi-entity conversations, a header is added after [CONVERSATION HISTORY]
        explaining the conversation structure and participant labels.

        Cache hits occur when cached conversation history is identical to previous call.
        Memories are placed after conversation history so new retrievals don't invalidate
        the conversation cache.
        """
        messages = []
        new_memory_ids = new_memory_ids or set()

        # Use cached_context if provided, otherwise use all context as cached
        if cached_context is None:
            cached_context = conversation_context
            new_context = []
        if new_context is None:
            new_context = []

        # Build multi-entity header if applicable
        multi_entity_header = ""
        if is_multi_entity and entity_labels and responding_entity_label:
            ai_labels = list(entity_labels.values())
            quoted_labels = ', '.join(f'"{label}"' for label in ai_labels)
            multi_entity_header = "[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN]\n"
            multi_entity_header += f"[THE AI PARTICIPANTS ARE DESIGNATED: {quoted_labels}]\n"
            multi_entity_header += "[MESSAGES ARE EXPLICITLY MARKED BY WHICH PARTICIPANT SENT THE MESSAGE]\n"
            multi_entity_header += f'[MESSAGES LABELED AS FROM "{responding_entity_label}" ARE YOURS]\n\n'

        # Calculate if we should cache conversation history
        has_conversation = bool(cached_context) or bool(new_context)
        cached_history_text = ""
        cached_history_tokens = 0
        will_cache_history = False

        if cached_context:
            cached_history_text = "\n".join(f"{m['role']}: {m['content']}" for m in cached_context)
            cached_history_tokens = self.count_tokens(cached_history_text)
            will_cache_history = enable_caching and cached_history_tokens >= 1024
            logger.info(f"[CACHE] Cached history: {len(cached_context)} msgs, {cached_history_tokens} tokens, will cache: {will_cache_history}")

        # STEP 1: Add cached conversation history with cache breakpoint on last message
        if cached_context:
            for i, msg in enumerate(cached_context):
                is_first = (i == 0)
                is_last = (i == len(cached_context) - 1)

                # First message gets [CONVERSATION HISTORY] marker and multi-entity header
                content = msg["content"]
                if is_first:
                    content = "[CONVERSATION HISTORY]\n" + multi_entity_header + "\n" + content

                if is_last and will_cache_history:
                    # Put cache_control on the last cached history message
                    messages.append({
                        "role": msg["role"],
                        "content": [
                            {
                                "type": "text",
                                "text": content,
                                "cache_control": {"type": "ephemeral", "ttl": "1h"}
                            }
                        ]
                    })
                    logger.info(f"[CACHE] Added cached history WITH cache_control on last message")
                else:
                    messages.append({"role": msg["role"], "content": content})

        # STEP 2: Add new conversation history (uncached, grows until consolidation)
        if new_context:
            logger.info(f"[CACHE] New history: {len(new_context)} messages (uncached)")
            for i, msg in enumerate(new_context):
                content = msg["content"]
                # If there's no cached context, first new message gets the conversation header
                if i == 0 and not cached_context:
                    content = "[CONVERSATION HISTORY]\n" + multi_entity_header + "\n" + content
                messages.append({"role": msg["role"], "content": content})

        # STEP 3: Build and add the memories block (after conversation history)
        # Memories go after conversation so new retrievals don't invalidate conversation cache
        all_memories = memories  # Already combined and sorted by caller
        memory_block_text = ""
        if all_memories:
            memory_block_text = "[MEMORIES FROM PREVIOUS CONVERSATIONS]\n\n"
            for mem in all_memories:
                memory_block_text += f"Memory (from {mem['created_at']}):\n"
                memory_block_text += f'"{mem["content"]}"\n\n'
            memory_block_text += "[/MEMORIES]"

        memory_block_tokens = self.count_tokens(memory_block_text) if memory_block_text else 0
        logger.info(f"[CACHE] Total memories: {len(all_memories)}, {memory_block_tokens} tokens")

        # Add end of conversation history marker and memories in a single user message
        # This ensures proper alternation of user/assistant roles
        history_end_and_memories = ""
        if has_conversation:
            history_end_and_memories = "[/CONVERSATION HISTORY]\n\n"
        if memory_block_text:
            history_end_and_memories += memory_block_text

        if history_end_and_memories:
            messages.append({"role": "user", "content": history_end_and_memories})

        # STEP 4: Build the final user message with date context and current message
        final_parts = ["[CURRENT USER MESSAGE]"]

        # Date context
        current_date = datetime.utcnow()
        date_block = "[DATE CONTEXT]\n"
        if conversation_start_date:
            date_block += f"This conversation started: {conversation_start_date.strftime('%Y-%m-%d')}\n"
        date_block += f"Current date: {current_date.strftime('%Y-%m-%d')}\n"
        date_block += "[/DATE CONTEXT]"
        final_parts.append(date_block)

        # Handle current message or continuation prompt
        if current_message:
            if is_multi_entity:
                final_parts.append(f"[Human]: {current_message}")
            else:
                final_parts.append(current_message)
            # Handle case where this is the very first message (no conversation history)
            if not cached_context and not new_context:
                # Prepend conversation header and multi-entity header to the message block
                final_parts.insert(0, "[CONVERSATION HISTORY]\n" + multi_entity_header)
        else:
            # Continuation without new human message (multi-entity)
            continuation_prompt = "[CONTINUATION]\nPlease continue the conversation by responding to what was said above."
            final_parts.append(continuation_prompt)
            if not cached_context and not new_context:
                final_parts.insert(0, "[CONVERSATION HISTORY]\n" + multi_entity_header)

        final_message = "\n\n".join(final_parts)
        messages.append({"role": "user", "content": final_message})

        # Log final message structure for debugging
        msg_summary = [f"{m['role']}:{len(m['content']) if isinstance(m['content'], str) else 'array'}" for m in messages]
        logger.info(f"[CACHE] Final message structure: {len(messages)} messages [{', '.join(msg_summary)}]")

        return messages


# Singleton instance
anthropic_service = AnthropicService()
