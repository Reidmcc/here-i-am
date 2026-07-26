from typing import Optional, List, Dict, Any, AsyncIterator, Tuple, Union
from datetime import datetime
from anthropic import AsyncAnthropic, APIConnectionError
from app.config import settings
from app.services.notes_service import notes_service
from app.services.thinking_effort import clamp_effort, resolve_effort
import asyncio
import httpx
import tiktoken
import json
import logging

logger = logging.getLogger(__name__)


def _describe_exception(exc: Exception) -> str:
    """
    Render an exception for display.

    Several transport-level exceptions carry no message at all — httpx.ReadError
    wrapping anyio.BrokenResourceError (a connection reset mid-stream) is the
    common one, and it previously surfaced in the UI as a bare "Error:". Always
    include the exception type so the class of failure is identifiable.
    """
    detail = str(exc)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


# Anthropic models that reject sampling parameters. Claude Opus 4.7 dropped
# temperature/top_p/top_k, and every model since (Opus 4.8, Opus 5, Sonnet 5,
# Fable 5, Mythos 5) does the same — sending temperature returns a 400.
MODELS_WITHOUT_TEMPERATURE = {
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
}


def supports_temperature(model: str) -> bool:
    """Whether the model accepts a temperature parameter."""
    return model not in MODELS_WITHOUT_TEMPERATURE


# Anthropic models that accept `thinking: {"type": "adaptive"}`. Older models
# (Sonnet 4.5, Haiku 4.5, and anything before) only understand the retired
# `{"type": "enabled", "budget_tokens": N}` form and return a 400 for adaptive,
# so the parameter must be gated. Matched as prefixes because configured model
# names may carry a date suffix (claude-sonnet-4-5-20250929). None of these is
# a prefix of a non-adaptive model, so prefix matching is safe here.
ADAPTIVE_THINKING_MODEL_PREFIXES = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-fable-5",
    "claude-mythos-5",
)


def supports_adaptive_thinking(model: str) -> bool:
    """Whether the model accepts the adaptive thinking parameter."""
    return model.startswith(ADAPTIVE_THINKING_MODEL_PREFIXES)


# Effort ladders by model, highest-precedence prefix first. `output_config.
# effort` is GA (no beta header) but the accepted levels differ by model:
# `xhigh` arrived with Opus 4.7, `max` with the 4.6 generation, and Opus 4.5
# only ever took low/medium/high. Sonnet 4.5, Haiku 4.5 and anything older
# reject the parameter outright, so they are absent here and get nothing sent.
# Matched as prefixes because configured model names may carry a date suffix.
EFFORT_MODEL_LADDERS = (
    (
        ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
         "claude-sonnet-5", "claude-fable-5", "claude-mythos-5"),
        ("low", "medium", "high", "xhigh", "max"),
    ),
    (
        ("claude-opus-4-6", "claude-sonnet-4-6"),
        ("low", "medium", "high", "max"),
    ),
    (
        ("claude-opus-4-5",),
        ("low", "medium", "high"),
    ),
)


def supported_effort_levels(model: str) -> Tuple[str, ...]:
    """The effort levels a model accepts, or () if it takes no effort param."""
    for prefixes, levels in EFFORT_MODEL_LADDERS:
        if model.startswith(prefixes):
            return levels
    return ()


def supports_thinking_effort(model: str) -> bool:
    """Whether the model accepts `output_config.effort`."""
    return bool(supported_effort_levels(model))


def resolve_effort_for_model(model: str, thinking_effort: Optional[str]) -> Optional[str]:
    """
    The effort level to send for this model, or None to send nothing.

    Requests above the model's ceiling are clamped down rather than dropped,
    so an entity set to `max` still thinks as hard as its model allows.
    """
    levels = supported_effort_levels(model)
    if not levels:
        return None
    return clamp_effort(resolve_effort(thinking_effort), levels)


def _apply_thinking_effort(
    api_params: Dict[str, Any],
    model: str,
    thinking_effort: Optional[str],
    provider: Optional[str],
) -> None:
    """
    Add `output_config.effort` to the request when the model accepts it.

    Sent through `extra_body` rather than a named argument: `output_config` is
    newer than the SDK floor this project pins (anthropic>=0.40.0), and an
    older client would reject the keyword before the request left the process.
    `extra_body` is merged into the request body by every SDK version, so this
    works on old and new clients alike.

    MiniMax speaks the Anthropic wire format but does not accept this
    parameter, so it is excluded — same carve-out as adaptive thinking.
    """
    if provider == "minimax":
        return
    effort = resolve_effort_for_model(model, thinking_effort)
    if not effort:
        return
    extra_body = api_params.setdefault("extra_body", {})
    output_config = extra_body.setdefault("output_config", {})
    output_config["effort"] = effort
    logger.info(f"[THINKING] Effort '{effort}' for model {model}")


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
        # thinking/redacted_thinking blocks are intentionally skipped: their
        # billed size (signature-reconstructed reasoning) isn't estimable
        # from the visible text, and the calibration ratio absorbs the drift

    return "\n".join(text_parts)


def _build_timeout() -> httpx.Timeout:
    """
    Timeout policy for Anthropic-compatible clients.

    The SDK default is a flat 10 minutes on every phase, which is both too
    generous for connecting (a dead host should fail fast) and, for streaming,
    not the safety net it appears to be: `read` is a per-chunk timeout that
    resets on every byte, so it only fires when a socket goes genuinely silent.
    A long reasoning phase keeps it alive indefinitely. It is still worth
    setting explicitly as a backstop against a half-dead connection that stops
    delivering without closing — but the read budget has to stay well above the
    gap between keepalive pings on a slow turn, or healthy streams get killed.
    """
    return httpx.Timeout(
        connect=settings.anthropic_connect_timeout,
        read=settings.anthropic_read_timeout,
        write=settings.anthropic_write_timeout,
        pool=settings.anthropic_connect_timeout,
    )


class AnthropicService:
    def __init__(self):
        # Enable extended cache TTL (1-hour) via beta header
        self.client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            default_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
            timeout=_build_timeout(),
        )
        self._minimax_client = None
        self._encoder = None
        self._cache_service = None

    @property
    def minimax_client(self):
        """Lazy-initialize MiniMax client (Anthropic-compatible API)."""
        if self._minimax_client is None and settings.minimax_api_key:
            self._minimax_client = AsyncAnthropic(
                api_key=settings.minimax_api_key,
                base_url="https://api.minimax.io/anthropic",
                timeout=_build_timeout(),
            )
        return self._minimax_client

    def _get_client(self, provider: Optional[str] = None) -> AsyncAnthropic:
        """Get the appropriate Anthropic-compatible client for the given provider."""
        if provider == "minimax" and self.minimax_client:
            return self.minimax_client
        return self.client

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
        provider: Optional[str] = None,
        thinking_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to an Anthropic-compatible API with optional prompt caching and tool use.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt (defaults to None for no system prompt)
            model: Model to use (defaults to config default)
            temperature: Temperature setting (defaults to config default)
            max_tokens: Max tokens in response (defaults to config default)
            enable_caching: Whether to enable Anthropic prompt caching (default True)
            tools: Optional list of tool definitions in Anthropic format
            provider: Optional provider hint ("anthropic" or "minimax") to select client
            thinking_effort: Optional thinking effort level (None uses the configured default)

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
        client = self._get_client(provider)

        # Build API call parameters
        api_params = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        # Newer Claude models reject temperature entirely (400)
        if supports_temperature(model):
            api_params["temperature"] = temperature

        _apply_thinking_effort(api_params, model, thinking_effort, provider)

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

        response = await client.messages.create(**api_params)

        # Parse response content blocks, preserving block order. Thinking
        # blocks are captured so tool-use continuations can echo them back
        # verbatim: on adaptive-thinking models the signature is the only
        # carrier of the model's reasoning (the raw chain of thought is
        # server-side, reconstructed from the signature on replay), so
        # dropping the block severs reasoning continuity for the rest of
        # the turn.
        content = ""
        content_blocks = []
        tool_use_blocks = []

        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "thinking":
                content_blocks.append({
                    "type": "thinking",
                    "thinking": getattr(block, "thinking", "") or "",
                    "signature": getattr(block, "signature", "") or "",
                })
            elif block_type == "redacted_thinking":
                content_blocks.append({
                    "type": "redacted_thinking",
                    "data": getattr(block, "data", "") or "",
                })
            elif hasattr(block, "text"):
                content += block.text
                content_blocks.append({
                    "type": "text",
                    "text": block.text,
                })
            elif block_type == "tool_use":
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
        # Guard against None values (some Anthropic-compatible APIs return None instead of omitting)
        if hasattr(response.usage, "cache_creation_input_tokens") and response.usage.cache_creation_input_tokens:
            usage["cache_creation_input_tokens"] = response.usage.cache_creation_input_tokens
        if hasattr(response.usage, "cache_read_input_tokens") and response.usage.cache_read_input_tokens:
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

    async def _stream_attempt(
        self,
        client: AsyncAnthropic,
        api_params: Dict[str, Any],
        model: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Run one streaming attempt, yielding events up to and including "done".

        Transport failures are raised, not caught, so the caller can decide
        whether the attempt is safe to retry. All per-attempt accumulator
        state is local, so a retry starts from a clean slate.
        """
        full_content = ""
        # Ordered content blocks as streamed. Thinking blocks (and their
        # signatures) are captured so the tool loop can echo the assistant
        # turn back verbatim: on adaptive-thinking models the signature is
        # the only carrier of the model's reasoning (the raw chain of
        # thought stays server-side and is reconstructed from the
        # signature on replay), so dropping the block severs reasoning
        # continuity for the rest of the turn. Block order must match the
        # original response — the API rejects rearranged or partially
        # dropped thinking sequences.
        #
        # This is separate from the thinking_* events yielded below, which are
        # a display channel for the UI. The blocks are what goes back to the
        # API; the events are what the user sees.
        content_blocks = []
        tool_use_blocks = []
        current_block = None  # text/thinking/redacted_thinking block being accumulated
        current_tool_use = None
        current_tool_input_json = ""
        in_thinking_block = False
        input_tokens = 0
        output_tokens = 0
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0
        stop_reason = None

        def flush_current_block():
            """Finalize the in-progress non-tool block into content_blocks."""
            nonlocal current_block
            if current_block is None:
                return
            block_type = current_block.get("type")
            keep = (
                (block_type == "text" and current_block.get("text"))
                # Keep thinking blocks with a signature even when the
                # thinking text is empty (display-omitted models return
                # empty text; the signature still carries the reasoning).
                or (block_type == "thinking"
                    and (current_block.get("thinking") or current_block.get("signature")))
                or (block_type == "redacted_thinking" and current_block.get("data"))
            )
            if keep:
                content_blocks.append(current_block)
            current_block = None

        async with client.messages.stream(**api_params) as stream:
            async for event in stream:
                if event.type == "message_start":
                    if hasattr(event.message, "usage"):
                        input_tokens = event.message.usage.input_tokens
                        # Capture cache metrics from message_start
                        # Guard against None values (some Anthropic-compatible APIs return None)
                        if hasattr(event.message.usage, "cache_creation_input_tokens") and event.message.usage.cache_creation_input_tokens is not None:
                            cache_creation_input_tokens = event.message.usage.cache_creation_input_tokens
                        if hasattr(event.message.usage, "cache_read_input_tokens") and event.message.usage.cache_read_input_tokens is not None:
                            cache_read_input_tokens = event.message.usage.cache_read_input_tokens

                elif event.type == "content_block_start":
                    block_type = getattr(event.content_block, "type", None)
                    # A new block is starting; finalize any non-tool block
                    # still in progress so stream order is preserved
                    flush_current_block()
                    if block_type == "tool_use":
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
                    elif block_type == "thinking":
                        current_block = {
                            "type": "thinking",
                            "thinking": "",
                            "signature": "",
                        }
                        in_thinking_block = True
                        logger.info("[THINKING] Thinking block started")
                        yield {"type": "thinking_start"}
                    elif block_type == "redacted_thinking":
                        # Redacted thinking arrives complete in the start
                        # event's data field; there are no deltas for it
                        current_block = {
                            "type": "redacted_thinking",
                            "data": getattr(event.content_block, "data", "") or "",
                        }
                        in_thinking_block = True
                        logger.info("[THINKING] Redacted thinking block started")
                        yield {"type": "thinking_start"}
                    elif block_type == "text":
                        current_block = {"type": "text", "text": ""}

                elif event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        # Regular text delta
                        text = event.delta.text
                        full_content += text
                        if current_block is None or current_block.get("type") != "text":
                            # Defensive: provider skipped content_block_start
                            flush_current_block()
                            current_block = {"type": "text", "text": ""}
                        current_block["text"] += text
                        yield {"type": "token", "content": text}
                    elif hasattr(event.delta, "partial_json"):
                        # Tool use input JSON delta
                        current_tool_input_json += event.delta.partial_json
                    elif hasattr(event.delta, "thinking"):
                        # Thinking text delta (summarized or raw-empty).
                        # Accumulated for the echo back to the API, and
                        # forwarded as a display event when non-empty —
                        # display "omitted" models send empty text, in which
                        # case there is nothing to show but the signature
                        # still matters.
                        if current_block is not None and current_block.get("type") == "thinking":
                            current_block["thinking"] += event.delta.thinking
                        if event.delta.thinking:
                            yield {"type": "thinking", "content": event.delta.thinking}
                    elif hasattr(event.delta, "signature"):
                        # Thinking signature — must be echoed back unchanged
                        if current_block is not None and current_block.get("type") == "thinking":
                            current_block["signature"] += event.delta.signature

                elif event.type == "content_block_stop":
                    if in_thinking_block:
                        in_thinking_block = False
                        yield {"type": "thinking_stop"}

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
                    else:
                        flush_current_block()

                elif event.type == "message_delta":
                    if hasattr(event, "usage"):
                        output_tokens = event.usage.output_tokens
                    if hasattr(event.delta, "stop_reason"):
                        stop_reason = event.delta.stop_reason

        # Flush any non-tool block left open by an interrupted stream
        # (e.g. hitting max_tokens mid-block). Completed blocks were
        # already appended in stream order at their content_block_stop.
        flush_current_block()

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

    async def send_message_stream(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        enable_caching: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        provider: Optional[str] = None,
        thinking_effort: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message to an Anthropic-compatible API with streaming response and optional prompt caching.

        Yields events with type and data:
        - {"type": "start", "model": str}
        - {"type": "token", "content": str}
        - {"type": "thinking_start"} - Start of a thinking block
        - {"type": "thinking", "content": str} - Summarized reasoning delta
        - {"type": "thinking_stop"} - End of a thinking block
        - {"type": "tool_use_start", "tool_use": dict} - Start of a tool use block
        - {"type": "tool_use_delta", "tool_use_id": str, "input_delta": str} - JSON input delta
        - {"type": "done", "content": str, "content_blocks": list, "tool_use": list|None, "model": str, "usage": dict, "stop_reason": str}
        - {"type": "error", "error": str}

        Thinking events are display-only: the text is never folded into the
        assistant content, never persisted, and never vectorized.

        The usage dict in the "done" event includes cache metrics when caching is enabled:
        - cache_creation_input_tokens: Tokens written to cache
        - cache_read_input_tokens: Tokens read from cache (90% cost reduction)
        """
        model = model or settings.default_model
        temperature = temperature if temperature is not None else settings.default_temperature
        max_tokens = max_tokens or settings.default_max_tokens
        client = self._get_client(provider)

        api_params = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        # Newer Claude models reject temperature entirely (400)
        if supports_temperature(model):
            api_params["temperature"] = temperature

        # Ask for summarized reasoning. Models from Claude 4.6 onward think
        # whether or not this is set (on Fable 5 and Opus 5 thinking cannot be
        # turned off at all), but the default display is "omitted", which
        # streams thinking blocks with empty text. During a long reasoning
        # phase that is indistinguishable from a hung connection — no tokens,
        # no log lines, nothing to render. Opting into summaries makes the
        # phase observable. Display does not change what the model does or how
        # it is billed. MiniMax is Anthropic-compatible but does not accept
        # this parameter, so it is excluded.
        if (
            settings.thinking_summaries_enabled
            and provider != "minimax"
            and supports_adaptive_thinking(model)
        ):
            api_params["thinking"] = {"type": "adaptive", "display": "summarized"}

        # Reasoning depth. Independent of the display setting above: effort
        # controls how much the model thinks, display only whether that
        # thinking comes back readable.
        _apply_thinking_effort(api_params, model, thinking_effort, provider)

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

        # The "start" event is emitted once, outside the retry loop, so a
        # reconnect does not look like a second turn to the caller.
        yield {"type": "start", "model": model}

        # The SDK does not retry a stream that dies mid-body: its max_retries
        # only covers establishing the request. Once the response body is
        # streaming, a connection reset (httpx.ReadError, typically an idle
        # intermediary reaping a long, quiet reasoning phase) kills the whole
        # turn — including everything already generated and billed. Retry here
        # instead. Prompt caching makes this cheap: the prefix comes back as a
        # cache read.
        #
        # Retrying is only safe while the attempt has produced nothing the
        # caller has already acted on. Once a token or a tool call has been
        # yielded, the caller has appended text or started rendering a tool, so
        # replaying the turn would duplicate it — at that point the error is
        # surfaced instead. Thinking is not a barrier: the events are transient
        # UI, and the thinking blocks the failed attempt accumulated die with
        # it (_stream_attempt keeps all block state local), so the replay
        # produces a fresh, self-consistent set. The open block is closed first
        # so the UI is not left with an unresolved spinner.
        max_attempts = max(1, settings.anthropic_stream_max_retries + 1)
        emitted_output = False
        in_thinking_block = False

        for attempt in range(1, max_attempts + 1):
            try:
                async for event in self._stream_attempt(client, api_params, model):
                    event_type = event.get("type")
                    if event_type in ("token", "tool_use_start"):
                        emitted_output = True
                    elif event_type == "thinking_start":
                        in_thinking_block = True
                    elif event_type == "thinking_stop":
                        in_thinking_block = False
                    yield event
                return

            except (httpx.TransportError, APIConnectionError) as e:
                retriable = not emitted_output and attempt < max_attempts
                if not retriable:
                    reason = (
                        "output already streamed to the caller"
                        if emitted_output
                        else f"no attempts left after {attempt}"
                    )
                    logger.exception(
                        f"[STREAM] Connection failed mid-stream and cannot be retried "
                        f"({reason}): {type(e).__name__}: {e}"
                    )
                    yield {"type": "error", "error": _describe_exception(e)}
                    return

                # Close a thinking block left open by the dead stream so the
                # caller is not left with a spinner that never resolves.
                if in_thinking_block:
                    in_thinking_block = False
                    yield {"type": "thinking_stop"}

                backoff = settings.anthropic_stream_retry_backoff * (2 ** (attempt - 1))
                logger.warning(
                    f"[STREAM] Connection failed mid-stream "
                    f"({type(e).__name__}: {e or 'no detail'}); "
                    f"retrying attempt {attempt + 1}/{max_attempts} in {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)

            except Exception as e:
                logger.exception(f"[STREAM] Stream error: {type(e).__name__}: {e}")
                yield {"type": "error", "error": _describe_exception(e)}
                return

    def build_messages(
        self,
        conversation_context: List[Dict[str, str]],
        current_message: Optional[str],
        conversation_start_date: Optional[datetime] = None,
        enable_caching: bool = True,
        # Caching parameters
        cached_context: Optional[List[Dict[str, str]]] = None,
        new_context: Optional[List[Dict[str, str]]] = None,
        # Multi-entity conversation parameters
        is_multi_entity: bool = False,
        entity_labels: Optional[Dict[str, str]] = None,
        responding_entity_label: Optional[str] = None,
        # Custom role labels for context formatting
        user_display_name: Optional[str] = None,
        # Attachments (images and files) - ephemeral, not stored
        attachments: Optional[Dict[str, Any]] = None,
        # Provider for formatting attachments correctly
        provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build the message list for API call with conversation-first caching.

        Cache breakpoint: End of cached conversation history (most stable content)

        Message structure:
        1. User: [CONVERSATION HISTORY] + multi-entity header (if applicable) + cached history msg 1
        2. Assistant: cached history msg 2
        ...
        N. Last cached history msg*       <- cache breakpoint (cache_control here)
        N+1. New history messages (added since the breakpoint was last advanced,
             e.g. memory insertions and context notices; sent uncached this
             turn, cached from the next turn onward)
        ...
        M. User: [/CONVERSATION HISTORY] + [CURRENT USER MESSAGE] + date context
           + entity notes + current message

        If current_message is None (multi-entity continuation), the entity is prompted
        to continue the conversation without a new human message.

        For multi-entity conversations, a header is added after [CONVERSATION HISTORY]
        explaining the conversation structure and participant labels.

        Cache hits occur when cached conversation history is identical to previous call.
        Retrieved memories are embedded in the conversation context as messages, so
        new retrievals extend the history instead of invalidating the cache.
        """
        messages = []

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
            multi_entity_header = "[THIS IS A CONVERSATION BETWEEN MULTIPLE AI AND ONE HUMAN. DO NOT WRITE FOR OTHER PARTICIPANTS. DO NOT LABEL YOUR MESSAGES WITH YOUR NAME.]"

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
            # 1024 is the minimum cacheable prefix for the default Sonnet
            # models; some models (e.g. Opus 4.x) have higher minimums, where
            # a too-small marker is silently ignored by the API (no error, no
            # write charge), so this check erring low is harmless.
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
                    content = msg["content"]
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

        # STEP 2: Add new conversation history (messages appended since the
        # last API call, e.g. memory-in-context insertions and context
        # notices). Sent uncached this turn; the cache breakpoint advances
        # over them after this turn's call, so they're written to the cache
        # on the next call.
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
                    content = msg["content"]
                    # If there's no cached context, first new message gets the conversation header
                    if i == 0 and not cached_context:
                        content = "[CONVERSATION HISTORY]\n" + multi_entity_header + "\n" + content
                    messages.append({"role": msg["role"], "content": content})

        # STEP 3: Build the final user message combining:
        # - End of conversation history marker
        # - Date context
        # - Entity notes (index.md)
        # - Current user message
        # All in ONE user message to ensure proper user/assistant alternation
        final_parts = []

        # End conversation history marker (if there was any history)
        if has_conversation:
            final_parts.append("[/CONVERSATION HISTORY]")

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

        # Entity notes (index.md):
        # For single-entity conversations the notes are injected ONCE at the front of
        # the cached conversation history (see SessionManager session build), so they
        # are paid for once and then read from cache instead of being re-sent uncached
        # in every turn's final message.
        #
        # For multi-entity conversations the responding entity — and therefore the
        # relevant notes — changes turn to turn, while the cached history is shared
        # across all participants. Pinning one entity's notes into that shared cached
        # block would be wrong, so multi-entity keeps notes in the per-turn message.
        if settings.notes_enabled and responding_entity_label and is_multi_entity:
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

        # Whether there's any human input this turn (text and/or an attachment).
        # An attachment-only message (file/image with no text) still counts as
        # human input, not a continuation.
        from app.services.attachment_service import attachment_service, has_attachments
        has_attachment_content = bool(attachments and has_attachments(attachments))

        # Handle current message or continuation prompt
        if current_message or has_attachment_content:
            if current_message:
                if is_multi_entity:
                    final_parts.append(f"[Human]: {current_message}")
                else:
                    final_parts.append(current_message)
            # When there's no text but an attachment is present, the
            # [CURRENT USER MESSAGE] marker plus the attachment content
            # (appended below) stand in for the human's message.
            # Handle case where this is the very first message (no conversation history)
            if not cached_context and not new_context:
                # Prepend conversation header and multi-entity header to the message block
                final_parts.insert(0, "[CONVERSATION HISTORY]\n" + multi_entity_header)
        else:
            # Continuation without new human input (multi-entity)
            continuation_prompt = "[CONTINUATION]\nPlease continue the conversation by responding to what was said above."
            final_parts.append(continuation_prompt)
            if not cached_context and not new_context:
                final_parts.insert(0, "[CONVERSATION HISTORY]\n" + multi_entity_header)

        final_text = "\n\n".join(final_parts)

        # Handle attachments for multimodal messages
        if has_attachment_content:
            # Process attachments for the appropriate provider
            provider_str = provider.value if hasattr(provider, 'value') else str(provider) if provider else "anthropic"
            multimodal_content = attachment_service.process_attachments_for_provider(
                text_content=final_text,
                attachments=attachments,
                provider=provider_str,
            )
            messages.append({"role": "user", "content": multimodal_content})
            logger.info(f"[ATTACHMENTS] Built multimodal message with {len(multimodal_content)} content blocks for {provider_str}")
        else:
            messages.append({"role": "user", "content": final_text})

        # Log final message structure for debugging
        msg_summary = [f"{m['role']}:{len(m['content']) if isinstance(m['content'], str) else 'array'}" for m in messages]
        user_msgs = sum(1 for m in messages if m.get('role') == 'user')
        assistant_msgs = sum(1 for m in messages if m.get('role') == 'assistant')
        logger.info(f"[CACHE] Final message structure: {len(messages)} messages ({user_msgs} user, {assistant_msgs} assistant) [{', '.join(msg_summary)}]")

        return messages


# Singleton instance
anthropic_service = AnthropicService()
