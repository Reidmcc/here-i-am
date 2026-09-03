"""
MCP server for Claude Code mode: the entity's deliberate memory tools.

Exposes memory_query / memory_save / memory_mark / memory_release over the
MCP streamable-HTTP transport (POST /mcp, wired in routes/claude_code.py),
so an entity operating from a Claude Code session can act on its memory
deliberately — the hooks cover the automatics (retrieval and formation),
this covers intent.

The transport is implemented directly as a stateless JSON-RPC handler
rather than via the MCP SDK: the server holds four static tools, needs no
server-initiated streams, sampling, or sessions, and the SDK's dependency
floor conflicts with this repo's pinned FastAPI/starlette/httpx versions.
Stateless JSON responses are a compliant subset of the streamable-HTTP
transport (a server MAY return a single application/json response per POST
and MAY omit session management).

Tool calls carry an optional conversation_id (the SessionStart hook tells
the entity its conversation's ID): the entity is resolved from that
conversation, query results are deduplicated against the conversation's
ConversationMemoryLink set, and — for Claude Code conversations only —
query results are linked so automatic retrieval won't re-surface them.
Without it, tools fall back to the default entity with no
conversation-level dedup.

Notes and git tools are deliberately not exposed: Claude Code's native
tools cover them.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from app.config import settings
from app.database import async_session_maker
from app.models import Conversation, ConversationSource
from app.services import memory_tools
from app.services.memory_service import memory_service
from app.services.memory_tools import MemoryToolContext

logger = logging.getLogger(__name__)

SERVER_NAME = "here-i-am"
SERVER_VERSION = "1.0.0"

# Protocol versions this server can speak, newest first. Initialization
# echoes the client's version when supported, otherwise offers the newest.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

SERVER_INSTRUCTIONS = (
    "Here I Am memory tools for the entity operating in this Claude Code "
    "session: deliberate memory recall (memory_query), self-authored "
    "memories (memory_save), and memory curation (memory_mark, "
    "memory_release). Pass the conversation_id from your session-start "
    "context so the tools act on this session's conversation."
)

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def _conversation_id_property() -> Dict[str, Any]:
    return {
        "type": "string",
        "description": (
            "Your Here I Am conversation ID for this Claude Code session, "
            "as given in your session-start context."
        ),
    }


def _with_conversation_id(schema: Dict[str, Any], required: bool = False) -> Dict[str, Any]:
    """Extend a native tool schema with the MCP-only conversation_id parameter."""
    extended = {
        **schema,
        "properties": {**schema["properties"], "conversation_id": _conversation_id_property()},
    }
    if required:
        extended["required"] = list(schema.get("required", [])) + ["conversation_id"]
    return extended


def get_tool_listing() -> List[Dict[str, Any]]:
    """The MCP tools/list payload (name, description, inputSchema)."""
    return [
        {
            "name": "memory_query",
            "description": memory_tools.MEMORY_QUERY_DESCRIPTION,
            "inputSchema": _with_conversation_id(memory_tools.MEMORY_QUERY_SCHEMA),
        },
        {
            "name": "memory_save",
            "description": memory_tools.MEMORY_SAVE_DESCRIPTION,
            "inputSchema": _with_conversation_id(memory_tools.MEMORY_SAVE_SCHEMA, required=True),
        },
        {
            "name": "memory_mark",
            "description": memory_tools.MEMORY_MARK_DESCRIPTION,
            "inputSchema": _with_conversation_id(memory_tools.MEMORY_MARK_SCHEMA),
        },
        {
            "name": "memory_release",
            "description": memory_tools.MEMORY_RELEASE_DESCRIPTION,
            "inputSchema": _with_conversation_id(memory_tools.MEMORY_RELEASE_SCHEMA),
        },
    ]


async def build_tool_context(
    conversation_id: Optional[str],
) -> Tuple[Optional[MemoryToolContext], Optional[str]]:
    """
    Build a per-request MemoryToolContext for an MCP tool call.

    With a conversation_id: the conversation must exist and belong to Claude
    Code mode (reflections and query links must not land on native
    conversations, whose reload/cache invariants they would break); the
    entity is the conversation's, and the conversation's memory-link set
    becomes the exclusion set. Without one: default entity, no
    conversation-level state.

    Returns (context, error) — exactly one is None.
    """
    if not conversation_id:
        default_entity = settings.get_default_entity()
        return MemoryToolContext(
            entity_id=default_entity.index_name if default_entity else None
        ), None

    async with async_session_maker() as db:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            return None, (
                f"Error: No conversation found with ID '{conversation_id}'. "
                "Use the conversation_id from your session-start context."
            )
        if conversation.source != ConversationSource.CLAUDE_CODE.value:
            return None, (
                "Error: That conversation belongs to the native Here I Am "
                "experience; these tools act on Claude Code conversations. "
                "Use the conversation_id from your session-start context."
            )

        entity_id = conversation.entity_id
        if entity_id is None:
            default_entity = settings.get_default_entity()
            entity_id = default_entity.index_name if default_entity else None

        # After a compaction only post-compaction links still represent
        # in-context content, and only the post-compaction slice of this
        # conversation is excluded from queries — everything older survives
        # only as a paraphrased summary and is fair recall again
        exclude_ids = await memory_service.get_retrieved_ids_for_conversation(
            conversation.id, db, entity_id=entity_id,
            linked_after=conversation.last_compacted_at,
        )
        last_compacted_at = conversation.last_compacted_at

    return MemoryToolContext(
        entity_id=entity_id,
        conversation_id=str(conversation.id),
        extra_exclude_ids=exclude_ids,
        # Claude Code conversations are never rebuilt into context, so links
        # are safe here and make automatic retrieval skip query results
        link_query_results=True,
        exclude_conversation_after=last_compacted_at,
    ), None


async def execute_tool(name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """
    Execute one MCP tool call. Returns the result text, or None for an
    unknown tool name (the caller maps that to a JSON-RPC error).
    Tool-level failures come back as "Error: ..." strings, same as the
    native tool loop.
    """
    if name not in ("memory_query", "memory_save", "memory_mark", "memory_release"):
        return None

    arguments = arguments or {}
    ctx, error = await build_tool_context(arguments.get("conversation_id"))
    if error:
        return error

    try:
        if name == "memory_query":
            return await memory_tools.query_memories(
                ctx,
                query=arguments.get("query", ""),
                num_results=arguments.get("num_results", 5),
                source=arguments.get("source"),
                mode=arguments.get("mode"),
                since=arguments.get("since"),
                include_model=bool(arguments.get("include_model", False)),
            )
        if name == "memory_save":
            return await memory_tools.save_memory(ctx, arguments.get("content", ""))
        if name == "memory_mark":
            return await memory_tools.mark_memory(
                ctx, arguments.get("memory_id", ""), undo=bool(arguments.get("undo", False))
            )
        return await memory_tools.release_memory(
            ctx, arguments.get("memory_id", ""), undo=bool(arguments.get("undo", False))
        )
    except Exception as e:
        logger.error(f"[CC MCP] Tool '{name}' failed: {e}")
        return f"Error executing {name}: {e}"


def _result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def handle_jsonrpc_message(message: Any) -> Optional[Dict[str, Any]]:
    """
    Handle one JSON-RPC message. Returns the response object, or None for
    notifications (which get no response body).
    """
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "Invalid JSON-RPC request")

    method = message.get("method")
    request_id = message.get("id")

    # Notifications (no id): acknowledge silently
    if request_id is None:
        return None

    if method == "initialize":
        params = message.get("params") or {}
        client_version = params.get("protocolVersion")
        negotiated = (
            client_version
            if client_version in SUPPORTED_PROTOCOL_VERSIONS
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        )
        return _result(request_id, {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        })

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": get_tool_listing()})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        text = await execute_tool(name, params.get("arguments") or {})
        if text is None:
            return _error(request_id, INVALID_PARAMS, f"Unknown tool: {name}")
        return _result(request_id, {
            "content": [{"type": "text", "text": text}],
            "isError": text.startswith("Error"),
        })

    return _error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")
