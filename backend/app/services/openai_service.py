from typing import Optional, List, Dict, Any, AsyncIterator
from openai import AsyncOpenAI
from app.config import settings
import tiktoken
import logging
import json

logger = logging.getLogger(__name__)


class OpenAIService:
    """Service for OpenAI API interactions."""

    # Models that use max_completion_tokens instead of max_tokens
    MODELS_WITH_COMPLETION_TOKENS = {
        "o1", "o1-mini", "o1-preview",
        "o3", "o3-mini",
        "o4-mini",
        "gpt-5.1", "gpt-5-mini", "gpt-5.1-chat-latest",
        "gpt-5.2",
    }

    # Models that don't support the temperature parameter
    MODELS_WITHOUT_TEMPERATURE = {
        "o1", "o1-mini", "o1-preview",
        "o3", "o3-mini",
        "o4-mini",
        "gpt-5.1", "gpt-5.1-chat-latest",
        "gpt-5.2",
    }

    # Models that don't support stream_options
    MODELS_WITHOUT_STREAM_OPTIONS = {
        "o1", "o1-mini", "o1-preview",
    }

    # Models that support the verbosity parameter
    MODELS_WITH_VERBOSITY = {
        "gpt-5.1", "gpt-5.1-chat-latest",
        "gpt-5.2",
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

    def _supports_verbosity(self, model: str) -> bool:
        """Check if model supports the verbosity parameter."""
        return model in self.MODELS_WITH_VERBOSITY

    def _convert_tools_to_openai_format(
        self,
        tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert Anthropic-style tool schemas to OpenAI format.

        Anthropic format:
        {
            "name": "web_search",
            "description": "...",
            "input_schema": {"type": "object", "properties": {...}, "required": [...]}
        }

        OpenAI format:
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "...",
                "parameters": {"type": "object", "properties": {...}, "required": [...]}
            }
        }
        """
        openai_tools = []
        for tool in tools:
            openai_tool = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                }
            }
            openai_tools.append(openai_tool)
        return openai_tools

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
        verbosity: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to OpenAI API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt
            model: Model to use (defaults to gpt-4o)
            temperature: Temperature setting (defaults to 1.0)
            max_tokens: Max tokens in response (defaults to 4096)
            verbosity: Verbosity level for gpt-5.1 models (low, medium, high)
            tools: Optional list of tool definitions (Anthropic format, will be converted)

        Returns:
            Dict with 'content', 'model', 'usage', 'stop_reason' keys.
            If tools are used, also includes 'content_blocks' and 'tool_use'.
        """
        client = self._ensure_client()

        model = model or settings.default_openai_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens

        # Build messages list with optional system prompt
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        # Map message roles and handle different content formats
        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            # Handle tool result messages (from agentic loop)
            if role == "user" and isinstance(content, list):
                # Check if this is a tool_result message from Anthropic format
                if content and isinstance(content[0], dict) and content[0].get("type") == "tool_result":
                    # Convert Anthropic tool_result to OpenAI format
                    for result in content:
                        api_messages.append({
                            "role": "tool",
                            "tool_call_id": result["tool_use_id"],
                            "content": result.get("content", ""),
                        })
                    continue

            # Handle assistant messages with tool_use blocks
            if role == "assistant" and isinstance(content, list):
                # Check if this contains tool_use blocks
                text_content = ""
                tool_calls = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_content += block.get("text", "")
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input", {})),
                                }
                            })
                if tool_calls:
                    msg_dict = {"role": "assistant", "content": text_content or None}
                    msg_dict["tool_calls"] = tool_calls
                    api_messages.append(msg_dict)
                    continue
                else:
                    content = text_content

            # Handle Anthropic array format (with cache_control) - extract text
            if isinstance(content, list):
                content = content[0]["text"] if content else ""
                logger.warning(f"[OPENAI] Converted array content to string for role={role} (len={len(content)})")

            api_messages.append({"role": role, "content": content})

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

        # Only include verbosity for models that support it (use default if not specified)
        if self._supports_verbosity(model):
            api_params["verbosity"] = verbosity or settings.default_verbosity

        # Add tools if provided
        if tools:
            api_params["tools"] = self._convert_tools_to_openai_format(tools)
            logger.info(f"[TOOLS] OpenAI: Sending request with {len(tools)} tools")

        response = await client.chat.completions.create(**api_params)

        # Extract content and handle tool calls
        message = response.choices[0].message
        content = message.content or ""
        content_blocks = []
        tool_use_blocks = []

        # Add text content block if present
        if content:
            content_blocks.append({"type": "text", "text": content})

        # Check for tool calls
        if message.tool_calls:
            for tool_call in message.tool_calls:
                # Parse arguments from JSON string
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}
                    logger.error(f"[TOOLS] Failed to parse tool arguments: {tool_call.function.arguments}")

                tool_use_block = {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "input": arguments,
                }
                content_blocks.append(tool_use_block)
                tool_use_blocks.append(tool_use_block)
                logger.info(f"[TOOLS] OpenAI tool use detected: {tool_call.function.name} (id={tool_call.id})")

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

        # Map OpenAI finish_reason to match Anthropic's stop_reason for tool use
        finish_reason = response.choices[0].finish_reason
        if finish_reason == "tool_calls":
            finish_reason = "tool_use"  # Normalize for agentic loop

        result = {
            "content": content,
            "model": response.model,
            "usage": usage,
            "stop_reason": finish_reason,
        }

        # Include content_blocks and tool_use for tool calling support
        if content_blocks:
            result["content_blocks"] = content_blocks
        if tool_use_blocks:
            result["tool_use"] = tool_use_blocks

        return result

    async def send_message_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        verbosity: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message to OpenAI API with streaming response.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "tool_use_start", "tool_use": dict} - Start of a tool use block
        - {"type": "done", "content": str, "content_blocks": list, "tool_use": list|None, "model": str, "usage": dict, "stop_reason": str}
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
            role = msg["role"]
            content = msg["content"]

            # Handle tool result messages (from agentic loop)
            if role == "user" and isinstance(content, list):
                # Check if this is a tool_result message from Anthropic format
                if content and isinstance(content[0], dict) and content[0].get("type") == "tool_result":
                    # Convert Anthropic tool_result to OpenAI format
                    for result in content:
                        api_messages.append({
                            "role": "tool",
                            "tool_call_id": result["tool_use_id"],
                            "content": result.get("content", ""),
                        })
                    continue

            # Handle assistant messages with tool_use blocks
            if role == "assistant" and isinstance(content, list):
                # Check if this contains tool_use blocks
                text_content = ""
                tool_calls = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_content += block.get("text", "")
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input", {})),
                                }
                            })
                if tool_calls:
                    msg_dict = {"role": "assistant", "content": text_content or None}
                    msg_dict["tool_calls"] = tool_calls
                    api_messages.append(msg_dict)
                    continue
                else:
                    content = text_content

            # Handle Anthropic array format (with cache_control) - extract text
            if isinstance(content, list):
                # Array format: [{"type": "text", "text": "...", ...}]
                content = content[0]["text"] if content else ""
                logger.warning(f"[OPENAI] Converted array content to string for role={role} (len={len(content)})")
            api_messages.append({"role": role, "content": content})

        logger.info(f"[OPENAI] Sending {len(api_messages)} messages to API")

        try:
            # Yield start event
            yield {"type": "start", "model": model}

            full_content = ""
            stop_reason = None
            content_blocks = []
            tool_use_blocks = []

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

            # Only include verbosity for models that support it (use default if not specified)
            if self._supports_verbosity(model):
                api_params["verbosity"] = verbosity or settings.default_verbosity

            # Add tools if provided
            if tools:
                api_params["tools"] = self._convert_tools_to_openai_format(tools)
                logger.info(f"[TOOLS] OpenAI: Streaming request with {len(tools)} tools")

            stream = await client.chat.completions.create(**api_params)

            input_tokens = 0
            output_tokens = 0
            cached_tokens = 0

            # Track tool calls as they stream in
            # OpenAI streams tool calls as deltas with index
            current_tool_calls: Dict[int, Dict[str, Any]] = {}

            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta

                    # Handle text content
                    if delta.content:
                        full_content += delta.content
                        yield {"type": "token", "content": delta.content}

                    # Handle tool calls streaming
                    if delta.tool_calls:
                        for tool_call_delta in delta.tool_calls:
                            idx = tool_call_delta.index

                            # Initialize or update the tool call at this index
                            if idx not in current_tool_calls:
                                current_tool_calls[idx] = {
                                    "id": "",
                                    "name": "",
                                    "arguments": "",
                                }

                            tc = current_tool_calls[idx]

                            # Update tool call ID
                            if tool_call_delta.id:
                                tc["id"] = tool_call_delta.id

                            # Update function name and arguments
                            if tool_call_delta.function:
                                if tool_call_delta.function.name:
                                    tc["name"] = tool_call_delta.function.name
                                    # Emit tool_use_start when we get the name
                                    yield {
                                        "type": "tool_use_start",
                                        "tool_use": {
                                            "id": tc["id"],
                                            "name": tc["name"],
                                        }
                                    }
                                if tool_call_delta.function.arguments:
                                    tc["arguments"] += tool_call_delta.function.arguments

                    if chunk.choices[0].finish_reason:
                        stop_reason = chunk.choices[0].finish_reason

                # Usage info comes in the final chunk
                if chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens
                    output_tokens = chunk.usage.completion_tokens
                    # Extract cached_tokens from prompt_tokens_details if available
                    if hasattr(chunk.usage, "prompt_tokens_details") and chunk.usage.prompt_tokens_details:
                        cached_tokens = getattr(chunk.usage.prompt_tokens_details, "cached_tokens", 0) or 0

            # Build content blocks for text
            if full_content:
                content_blocks.append({"type": "text", "text": full_content})

            # Process accumulated tool calls
            for idx in sorted(current_tool_calls.keys()):
                tc = current_tool_calls[idx]
                if tc["id"] and tc["name"]:
                    try:
                        arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        arguments = {}
                        logger.error(f"[TOOLS] Failed to parse tool arguments: {tc['arguments']}")

                    tool_use_block = {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": arguments,
                    }
                    content_blocks.append(tool_use_block)
                    tool_use_blocks.append(tool_use_block)
                    logger.info(f"[TOOLS] OpenAI tool use complete: {tc['name']} (id={tc['id']})")

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

            # Map OpenAI finish_reason to match Anthropic's stop_reason for tool use
            if stop_reason == "tool_calls":
                stop_reason = "tool_use"  # Normalize for agentic loop

            # Yield final done event
            done_event = {
                "type": "done",
                "content": full_content,
                "model": model,
                "usage": usage,
                "stop_reason": stop_reason,
            }

            # Include content_blocks and tool_use for tool calling support
            if content_blocks:
                done_event["content_blocks"] = content_blocks
            if tool_use_blocks:
                done_event["tool_use"] = tool_use_blocks

            yield done_event

        except Exception as e:
            yield {"type": "error", "error": str(e)}


# Singleton instance
openai_service = OpenAIService()
