from typing import Optional, List, Dict, Any
from openai import AsyncOpenAI
from app.config import settings
import tiktoken


class OpenAIService:
    """Service for OpenAI API interactions."""

    def __init__(self):
        self.client = None
        self._encoder = None

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

        response = await client.chat.completions.create(
            model=model,
            messages=api_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Extract content
        content = response.choices[0].message.content or ""

        return {
            "content": content,
            "model": response.model,
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            },
            "stop_reason": response.choices[0].finish_reason,
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

        # If there are memories, create a memory block as the first user message
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
openai_service = OpenAIService()
