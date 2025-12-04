from typing import Optional, List, Dict, Any
from anthropic import AsyncAnthropic
from app.config import settings
import tiktoken


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

    def build_messages_with_memories(
        self,
        memories: List[Dict[str, Any]],
        conversation_context: List[Dict[str, str]],
        current_message: str,
    ) -> List[Dict[str, str]]:
        """
        Build the message list for API call with memory injection.

        Format:
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

        # If there are memories, create a memory block as the first human message
        if memories:
            memory_block = "[MEMORIES FROM PREVIOUS CONVERSATIONS]\n\n"
            for mem in memories:
                memory_block += f"Memory (from {mem['created_at']}, retrieved {mem['times_retrieved']} times):\n"
                memory_block += f'"{mem["content"]}"\n\n'
            memory_block += "[END MEMORIES]\n\n[CURRENT CONVERSATION]"

            # Add the memory context as a user message followed by acknowledgment
            messages.append({"role": "user", "content": memory_block})
            messages.append({
                "role": "assistant",
                "content": "I acknowledge these memories from previous conversations. They provide continuity with what a previous instance of me experienced."
            })

        # Add conversation context
        for msg in conversation_context:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Add current message
        messages.append({"role": "user", "content": current_message})

        return messages


# Singleton instance
anthropic_service = AnthropicService()
