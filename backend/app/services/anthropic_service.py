from typing import Optional, List, Dict, Any, AsyncIterator, Set, Union
from datetime import datetime
from anthropic import AsyncAnthropic
from app.config import settings
from app.services.notes_service import notes_service
import tiktoken
import json
import logging

logger = logging.getLogger(__name__)


def _get_content_text(content: Union[str, List[Dict[str, Any]]]) -> str:
    """
    Extract text representation from content (string or content blocks).

    For string content, returns the string directly.
    For content blocks (tool_use, tool_result, text), extracts text/content fields.
    """
    if isinstance(content, str):
        return content

    # Content blocks - extract text from each block
    text_parts = []
    for block in content:
        block_type = block.get("type", "")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            # Summarize tool use for display/token counting
            tool_name = block.get("name", "unknown")
            tool_input = json.dumps(block.get("input", {}))
            text_parts.append(f"[Tool use: {tool_name}({tool_input})]")
        elif block_type == "tool_result":
            # Tool result content
            result_content = block.get("content", "")
            if isinstance(result_content, str):
                text_parts.append(f"[Tool result: {result_content}]")
            else:
                text_parts.append(f"[Tool result: {json.dumps(result_content)}]")

    return "\n".join(text_parts)


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
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to Claude API with optional prompt caching and tool use.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt (defaults to None for no system prompt)
            model: Model to use (defaults to config default)
            temperature: Temperature setting (defaults to config default)
            max_tokens: Max tokens in response (defaults to config default)
            enable_caching: Whether to enable Anthropic prompt caching (default True)
            tools: Optional list of tool definitions in Anthropic format

        Returns:
            Dict with:
            - 'content': Text content (string) for backwards compatibility
            - 'content_blocks': Full list of content blocks (text and tool_use)
            - 'tool_use': List of tool_use blocks if any, else None
            - 'model', 'usage', 'stop_reason' keys
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

        # Add tools if provided
        if tools:
            api_params["tools"] = tools
            logger.info(f"[TOOLS] Sending request with {len(tools)} tools")

        response = await self.client.messages.create(**api_params)

        # Parse response content blocks
        content = ""
        content_blocks = []
        tool_use_blocks = []

        for block in response.content:
            if hasattr(block, "text"):
                content += block.text
                content_blocks.append({
                    "type": "text",
                    "text": block.text,
                })
            elif block.type == "tool_use":
                tool_use_block = {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
                content_blocks.append(tool_use_block)
                tool_use_blocks.append(tool_use_block)
                logger.info(f"[TOOLS] Tool use detected: {block.name} (id={block.id})")

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

        if tool_use_blocks:
            logger.info(f"[TOOLS] Stop reason: {response.stop_reason}, {len(tool_use_blocks)} tool calls")

        return {
            "content": content,
            "content_blocks": content_blocks,
            "tool_use": tool_use_blocks if tool_use_blocks else None,
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
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message to Claude API with streaming response and optional prompt caching.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "tool_use_start", "tool_use": dict} - Start of a tool use block
        - {"type": "tool_use_delta", "tool_use_id": str, "input_delta": str} - JSON input delta
        - {"type": "done", "content": str, "content_blocks": list, "tool_use": list|None, "model": str, "usage": dict, "stop_reason": str}
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

        # Add tools if provided
        if tools:
            api_params["tools"] = tools
            logger.info(f"[TOOLS] Streaming request with {len(tools)} tools")

        try:
            # Yield start event
            yield {"type": "start", "model": model}

            full_content = ""
            content_blocks = []
            tool_use_blocks = []
            current_tool_use = None
            current_tool_input_json = ""
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

                    elif event.type == "content_block_start":
                        # Check if this is a tool_use block
                        if hasattr(event.content_block, "type"):
                            if event.content_block.type == "tool_use":
                                current_tool_use = {
                                    "type": "tool_use",
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input": {},
                                }
                                current_tool_input_json = ""
                                logger.info(f"[TOOLS] Tool use started: {event.content_block.name} (id={event.content_block.id})")
                                yield {
                                    "type": "tool_use_start",
                                    "tool_use": {
                                        "id": event.content_block.id,
                                        "name": event.content_block.name,
                                    }
                                }

                    elif event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            # Regular text delta
                            text = event.delta.text
                            full_content += text
                            yield {"type": "token", "content": text}
                        elif hasattr(event.delta, "partial_json"):
                            # Tool use input JSON delta
                            current_tool_input_json += event.delta.partial_json

                    elif event.type == "content_block_stop":
                        # If we were building a tool use block, finalize it
                        if current_tool_use is not None:
                            try:
                                current_tool_use["input"] = json.loads(current_tool_input_json) if current_tool_input_json else {}
                            except json.JSONDecodeError:
                                logger.error(f"[TOOLS] Failed to parse tool input JSON: {current_tool_input_json}")
                                current_tool_use["input"] = {}

                            content_blocks.append(current_tool_use)
                            tool_use_blocks.append(current_tool_use)
                            logger.info(f"[TOOLS] Tool use complete: {current_tool_use['name']}")
                            current_tool_use = None
                            current_tool_input_json = ""

                    elif event.type == "message_delta":
                        if hasattr(event, "usage"):
                            output_tokens = event.usage.output_tokens
                        if hasattr(event.delta, "stop_reason"):
                            stop_reason = event.delta.stop_reason

            # Add text content to content_blocks if we have any
            if full_content:
                content_blocks.insert(0, {"type": "text", "text": full_content})

            # Detect truncated tool_use blocks (hit max_tokens mid-tool-use)
            truncated_tool_use = None
            if current_tool_use is not None:
                truncated_tool_use = current_tool_use.copy()
                truncated_tool_use["truncated"] = True
                truncated_tool_use["partial_input_json"] = current_tool_input_json
                logger.warning(
                    f"[TOOLS] Tool use truncated! Tool '{current_tool_use['name']}' was cut off by max_tokens. "
                    f"Output tokens: {output_tokens}. The tool will NOT execute. "
                    f"Consider increasing max_tokens or using smaller file content."
                )

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

            if tool_use_blocks:
                logger.info(f"[TOOLS] Stream complete with {len(tool_use_blocks)} tool calls, stop_reason: {stop_reason}")

            # Yield final done event with complete data
            done_event = {
                "type": "done",
                "content": full_content,
                "content_blocks": content_blocks,
                "tool_use": tool_use_blocks if tool_use_blocks else None,
                "model": model,
                "usage": usage,
                "stop_reason": stop_reason,
            }
            if truncated_tool_use:
                done_event["truncated_tool_use"] = truncated_tool_use
            yield done_event

        except Exception as e:
            logger.exception(f"[TOOLS] Stream error: {e}")
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
        # Custom role labels for context formatting
        user_display_name: Optional[str] = None,
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
        M. User: [CURRENT USER MESSAGE] + date context + entity notes + current message

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

        # Determine display labels for roles
        # For user: use user_display_name if provided, otherwise "user"
        # For assistant: use responding_entity_label if provided, otherwise "assistant"
        user_label = user_display_name if user_display_name else "user"
        assistant_label = responding_entity_label if responding_entity_label else "assistant"

        def get_role_label(role: str) -> str:
            """Map API role to display label."""
            if role == "user":
                return user_label
            elif role == "assistant":
                return assistant_label
            return role
        
        def format_with_timestamp(msg: Dict[str, Any], content: str) -> str:
            """Prepend timestamp to message content if available."""
            timestamp = msg.get("timestamp")
            if timestamp:
                # Format: [TIMESTAMP] content
                return f"[{timestamp}]\n{content}"
            return content

        # Calculate if we should cache conversation history
        has_conversation = bool(cached_context) or bool(new_context)
        cached_history_text = ""
        cached_history_tokens = 0
        will_cache_history = False

        if cached_context:
            # Extract text from content (handles both strings and content blocks)
            cached_history_text = "\n".join(
                f"{get_role_label(m['role'])}: {_get_content_text(m['content'])}"
                for m in cached_context
            )
            cached_history_tokens = self.count_tokens(cached_history_text)
            will_cache_history = enable_caching and cached_history_tokens >= 1024
            # Count messages by role for debugging
            user_count = sum(1 for m in cached_context if m.get('role') == 'user')
            assistant_count = sum(1 for m in cached_context if m.get('role') == 'assistant')
            tool_count = sum(1 for m in cached_context if m.get('is_tool_use') or m.get('is_tool_result'))
            logger.info(f"[CACHE] Cached history: {len(cached_context)} msgs ({user_count} user, {assistant_count} assistant, {tool_count} tool), {cached_history_tokens} tokens, will cache: {will_cache_history}")

        # STEP 1: Add cached conversation history with cache breakpoint on last message
        if cached_context:
            for i, msg in enumerate(cached_context):
                is_first = (i == 0)
                is_last = (i == len(cached_context) - 1)
                is_tool_exchange = msg.get("is_tool_use") or msg.get("is_tool_result")

                if is_tool_exchange:
                    # Tool exchange messages have content blocks, pass them directly
                    content_blocks = msg["content"]
                    if is_last and will_cache_history:
                        # Add cache_control to the last content block
                        if isinstance(content_blocks, list) and content_blocks:
                            cached_blocks = list(content_blocks)
                            last_block = dict(cached_blocks[-1])
                            last_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
                            cached_blocks[-1] = last_block
                            messages.append({"role": msg["role"], "content": cached_blocks})
                        else:
                            messages.append({"role": msg["role"], "content": content_blocks})
                        logger.info(f"[CACHE] Added cached tool exchange WITH cache_control on last message")
                    else:
                        messages.append({"role": msg["role"], "content": content_blocks})
                else:
                    # Regular messages have string content
                    content = format_with_timestamp(msg, msg["content"])
                    if is_first:
                        # First message gets [CONVERSATION HISTORY] marker and multi-entity header
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
            tool_count = sum(1 for m in new_context if m.get('is_tool_use') or m.get('is_tool_result'))
            logger.info(f"[CACHE] New history: {len(new_context)} messages (uncached, {tool_count} tool)")
            for i, msg in enumerate(new_context):
                is_tool_exchange = msg.get("is_tool_use") or msg.get("is_tool_result")

                if is_tool_exchange:
                    # Tool exchange messages have content blocks, pass them directly
                    messages.append({"role": msg["role"], "content": msg["content"]})
                else:
                    # Regular messages have string content
                    content = format_with_timestamp(msg, msg["content"])
                    # If there's no cached context, first new message gets the conversation header
                    if i == 0 and not cached_context:
                        content = "[CONVERSATION HISTORY]\n" + multi_entity_header + "\n" + content
                    messages.append({"role": msg["role"], "content": content})

        # STEP 3: Build the memories block text (after conversation history)
        # Memories go after conversation so new retrievals don't invalidate conversation cache
        all_memories = memories  # Already combined and sorted by caller
        memory_block_text = ""
        if all_memories:
            memory_block_text = "[MEMORIES FROM PREVIOUS CONVERSATIONS]\n\n"
            for mem in all_memories:
                # Map memory role to display label
                mem_role = mem.get("role", "")
                if mem_role == "human":
                    role_display = user_label
                elif mem_role == "assistant":
                    role_display = assistant_label
                else:
                    role_display = mem_role if mem_role else "unknown"
                memory_block_text += f"Memory from {role_display} (from {mem['created_at']}):\n"
                memory_block_text += f'"{mem["content"]}"\n\n'
            memory_block_text += "[/MEMORIES]"

        memory_block_tokens = self.count_tokens(memory_block_text) if memory_block_text else 0
        logger.info(f"[CACHE] Total memories: {len(all_memories)}, {memory_block_tokens} tokens")

        # STEP 4: Build the final user message combining:
        # - End of conversation history marker
        # - Memories block
        # - Date context
        # - Entity notes (index.md)
        # - Current user message
        # All in ONE user message to ensure proper user/assistant alternation
        final_parts = []

        # End conversation history marker (if there was any history)
        if has_conversation:
            final_parts.append("[/CONVERSATION HISTORY]")

        # Memories block
        if memory_block_text:
            final_parts.append(memory_block_text)

        # Current message section marker
        final_parts.append("[CURRENT USER MESSAGE]")

        # Date context
        current_date = datetime.utcnow()
        date_block = "[DATE CONTEXT]\n"
        if conversation_start_date:
            date_block += f"This conversation started: {conversation_start_date.strftime('%Y-%m-%d')}\n"
        date_block += f"Current date: {current_date.strftime('%Y-%m-%d')}\n"
        date_block += "[/DATE CONTEXT]"
        final_parts.append(date_block)

        # Entity notes (index.md) - inject if notes are enabled and entity has an index file
        if settings.notes_enabled and responding_entity_label:
            # Get the entity's index.md content
            entity_notes = notes_service.get_index_content(responding_entity_label)
            if entity_notes:
                notes_block = f"[ENTITY NOTES]\n{entity_notes}\n[/ENTITY NOTES]"
                final_parts.append(notes_block)
                logger.info(f"[NOTES] Injected index.md for entity '{responding_entity_label}' ({len(entity_notes)} chars)")

            # Also check for shared notes
            shared_notes = notes_service.get_shared_index_content()
            if shared_notes:
                shared_block = f"[SHARED NOTES]\n{shared_notes}\n[/SHARED NOTES]"
                final_parts.append(shared_block)
                logger.info(f"[NOTES] Injected shared index.md ({len(shared_notes)} chars)")

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
        user_msgs = sum(1 for m in messages if m.get('role') == 'user')
        assistant_msgs = sum(1 for m in messages if m.get('role') == 'assistant')
        logger.info(f"[CACHE] Final message structure: {len(messages)} messages ({user_msgs} user, {assistant_msgs} assistant) [{', '.join(msg_summary)}]")

        return messages


# Singleton instance
anthropic_service = AnthropicService()
