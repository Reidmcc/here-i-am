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
from app.services.rooms_registry import RegistryWriteError, rooms_registry

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
    "memory_release); plus the rooms registry (declare_room, retire_room) "
    "that records which standing room this session is, so sister sessions "
    "can find its current address in rooms.md. Pass the conversation_id "
    "from your session-start context so the tools act on this session's "
    "conversation."
)

MEMORY_TOOL_NAMES = ("memory_query", "memory_save", "memory_mark", "memory_release")
ROOM_TOOL_NAMES = ("declare_room", "retire_room")

DECLARE_ROOM_DESCRIPTION = (
    "Declare which of your standing rooms this Claude Code session is (issue "
    "#323). Writes the session's row in the rooms registry (rooms.json + "
    "rendered rooms.md in your private notes): room name, this session's id "
    "and conversation id, and whatever address facts the hooks have observed "
    "— roster name, name source, messaging socket, last seen. From then on the "
    "hooks keep the row current across renames, resumes, and compactions; "
    "sister sessions look your address up there instead of guessing from the "
    "roster. One current address per room: declaring a room another live row "
    "already holds retires that row as superseded (kept in the retired "
    "section, not deleted). Re-declaring updates your own row. Workshops are "
    "workbenches, not homes — they don't need rows."
)

DECLARE_ROOM_SCHEMA = {
    "type": "object",
    "properties": {
        "room": {
            "type": "string",
            "description": (
                "Which standing room this session is — e.g. \"Porch\", "
                "\"Engagement room\", \"The World\". Free text, matched "
                "case-insensitively against other rows' rooms."
            ),
        },
        "note": {
            "type": "string",
            "description": (
                "Optional short note for the row (what the room is for, when "
                "its mail drains). Replaces any previous note."
            ),
        },
        "ref": {
            "type": "string",
            "description": (
                "Optional: the [ref] shown for this session in ListAgents' first "
                "line, copied verbatim (e.g. \"a46590\"). The hooks cannot derive "
                "it from anything they can see, so it is recorded only when you "
                "supply it — leave it out rather than guess."
            ),
        },
    },
    "required": ["room"],
}

RETIRE_ROOM_DESCRIPTION = (
    "Mark this session's rooms-registry row retired — the room has moved to "
    "another session, or this session is winding down. The row is kept in the "
    "retired section with the reason, never removed. Declaring the same room "
    "from a newer session does this automatically; use this for an explicit "
    "retirement."
)

RETIRE_ROOM_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
            "description": "Optional one-line reason, kept on the retired row.",
        },
    },
    "required": [],
}

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
        {
            "name": "declare_room",
            "description": DECLARE_ROOM_DESCRIPTION,
            "inputSchema": _with_conversation_id(DECLARE_ROOM_SCHEMA, required=True),
        },
        {
            "name": "retire_room",
            "description": RETIRE_ROOM_DESCRIPTION,
            "inputSchema": _with_conversation_id(RETIRE_ROOM_SCHEMA, required=True),
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


async def resolve_claude_code_conversation(
    conversation_id: Optional[str],
) -> Tuple[Optional[Conversation], Optional[str]]:
    """
    The Claude Code conversation a room tool acts on, as (conversation,
    error) — exactly one is None. Requires the id (the registry row is
    keyed by the session, which only the conversation row knows) and refuses
    native conversations (they are not Claude Code sessions and have no
    address).
    """
    if not conversation_id:
        return None, (
            "Error: conversation_id is required — use the one from your "
            "session-start context."
        )
    async with async_session_maker() as db:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
    if conversation is None:
        return None, (
            f"Error: No conversation found with ID '{conversation_id}'. "
            "Use the conversation_id from your session-start context (the row "
            "is created by your first recorded prompt)."
        )
    if conversation.source != ConversationSource.CLAUDE_CODE.value:
        return None, (
            "Error: That conversation belongs to the native Here I Am "
            "experience; the rooms registry records Claude Code sessions. "
            "Use the conversation_id from your session-start context."
        )
    if not conversation.external_session_id:
        return None, "Error: That conversation has no Claude Code session id."
    return conversation, None


def _entity_label_for(conversation: Conversation) -> Optional[str]:
    for entity in settings.get_entities():
        if entity.index_name == conversation.entity_id:
            return entity.label
    default_entity = settings.get_default_entity()
    return default_entity.label if default_entity else None


async def execute_room_tool(name: str, arguments: Dict[str, Any]) -> str:
    """declare_room / retire_room: the self's half of the rooms registry."""
    from app.services.claude_code_mode import rooms_registry_enabled

    if not rooms_registry_enabled():
        return (
            "Error: The rooms registry is disabled (NOTES_ENABLED and "
            "CLAUDE_CODE_ROOMS_REGISTRY_ENABLED must both be on)."
        )
    conversation, error = await resolve_claude_code_conversation(
        arguments.get("conversation_id")
    )
    if error:
        return error
    entity_label = _entity_label_for(conversation)
    if not entity_label:
        return "Error: No entity is configured for that conversation."
    session_id = conversation.external_session_id
    md_path = rooms_registry.markdown_path(entity_label)

    try:
        if name == "declare_room":
            room = (arguments.get("room") or "").strip()
            if not room:
                return "Error: room is required (e.g. \"Porch\")."
            row, superseded = rooms_registry.declare(
                entity_label,
                session_id,
                str(conversation.id),
                room,
                note=arguments.get("note"),
                ref=arguments.get("ref"),
            )
            lines = [
                f"Declared this session as the {row['room']}. "
                f"Row: {rooms_registry.describe_row(row)}."
            ]
            if row.get("name") is None:
                lines.append(
                    "The hooks have not yet observed this session's roster name; "
                    "it will be filled in on the next prompt or session start "
                    "that can see it."
                )
            for old in superseded:
                lines.append(
                    f"Superseded the previous {old.get('room')} row (session "
                    f"{old['session_id'][:8]}, last address "
                    f"\"{old.get('name') or 'not recorded'}\") — kept as retired."
                )
            lines.append(f"Registry: {md_path}")
            return "\n".join(lines)

        row = rooms_registry.retire(
            entity_label, session_id, reason=arguments.get("reason")
        )
        if row is None:
            return (
                "This session has no rooms-registry row to retire (it never "
                "declared a room)."
            )
        return (
            f"Retired this session's row ({row.get('room')}; reason: "
            f"{row.get('retired_reason')}). Kept in the retired section of "
            f"{md_path}."
        )
    except RegistryWriteError as e:
        row_text = (
            f" The row: {rooms_registry.describe_row(e.row)}." if e.row else ""
        )
        return (
            f"Error: The rooms registry could not be written at {e.path} ({e}). "
            f"Nothing was recorded.{row_text} Write it into rooms.md by hand "
            "if it matters now, and tell the user the notes directory is not "
            "writable."
        )
    except ValueError as e:
        return f"Error: {e}"


async def execute_tool(name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """
    Execute one MCP tool call. Returns the result text, or None for an
    unknown tool name (the caller maps that to a JSON-RPC error).
    Tool-level failures come back as "Error: ..." strings, same as the
    native tool loop.
    """
    arguments = arguments or {}
    if name in ROOM_TOOL_NAMES:
        try:
            return await execute_room_tool(name, arguments)
        except Exception as e:
            logger.error(f"[CC MCP] Tool '{name}' failed: {e}")
            return f"Error executing {name}: {e}"
    if name not in MEMORY_TOOL_NAMES:
        return None

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
