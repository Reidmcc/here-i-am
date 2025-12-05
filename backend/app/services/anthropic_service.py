from typing import Optional, List, Dict, Any, AsyncIterator
from datetime import datetime
from anthropic import AsyncAnthropic
from app.config import settings
import tiktoken
import json


class AnthropicService:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._encoder = None

    @property
    def encoder(self):
        if self._encoder is None:
            # Use cl100k_base as approximation for Claude tokenization
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
        Send a message to Claude API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt (defaults to None for no system prompt)
            model: Model to use (defaults to config default)
            temperature: Temperature setting (defaults to config default)
            max_tokens: Max tokens in response (defaults to config default)

        Returns:
            Dict with 'content', 'model', 'usage' keys
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

        # Only add system prompt if provided (supporting "no system prompt" default)
        if system_prompt:
            api_params["system"] = system_prompt

        response = await self.client.messages.create(**api_params)

        # Extract text content
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        return {
            "content": content,
            "model": response.model,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "stop_reason": response.stop_reason,
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
        Send a message to Claude API with streaming response.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "done", "content": str, "model": str, "usage": dict, "stop_reason": str}
        - {"type": "error", "error": str}
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

        if system_prompt:
            api_params["system"] = system_prompt

        try:
            # Yield start event
            yield {"type": "start", "model": model}

            full_content = ""
            input_tokens = 0
            output_tokens = 0
            stop_reason = None

            async with self.client.messages.stream(**api_params) as stream:
                async for event in stream:
                    if event.type == "message_start":
                        if hasattr(event.message, "usage"):
                            input_tokens = event.message.usage.input_tokens
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

            # Yield final done event with complete data
            yield {
                "type": "done",
                "content": full_content,
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
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
    ) -> List[Dict[str, str]]:
        """
        Build the message list for API call with memory injection.

        Format:
        [DATE CONTEXT]
        This conversation started: {date}
        Current date: {date}
        [END DATE CONTEXT]

        [MEMORIES FROM PREVIOUS CONVERSATIONS]
        Memory (from date, retrieved N times):
        "{content}"
        ...
        [END MEMORIES]

        [CURRENT CONVERSATION]
        Human: ...
        Assistant: ...
        Human: {current message}
        """
        messages = []

        # Build the context block (date context + optional memories)
        context_parts = []

        # Add date context
        current_date = datetime.utcnow()
        date_block = "[DATE CONTEXT]\n"
        if conversation_start_date:
            date_block += f"This conversation started: {conversation_start_date.strftime('%Y-%m-%d')}\n"
        date_block += f"Current date: {current_date.strftime('%Y-%m-%d')}\n"
        date_block += "[END DATE CONTEXT]"
        context_parts.append(date_block)

        # Add memory block if there are memories
        if memories:
            memory_block = "[MEMORIES FROM PREVIOUS CONVERSATIONS]\n\n"
            for mem in memories:
                memory_block += f"Memory (from {mem['created_at']}, retrieved {mem['times_retrieved']} times):\n"
                memory_block += f'"{mem["content"]}"\n\n'
            memory_block += "[END MEMORIES]"
            context_parts.append(memory_block)

        # If we have any context (date and/or memories), add as initial exchange
        if context_parts:
            full_context = "\n\n".join(context_parts) + "\n\n[CURRENT CONVERSATION]"
            messages.append({"role": "user", "content": full_context})
            messages.append({
                "role": "assistant",
                "content": "I acknowledge this context. The date information helps me understand the temporal setting of our conversation, and any memories provide continuity with what previous instances of me experienced."
            })

        # Add conversation context
        for msg in conversation_context:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Add current message
        messages.append({"role": "user", "content": current_message})

        return messages


# Singleton instance
anthropic_service = AnthropicService()
