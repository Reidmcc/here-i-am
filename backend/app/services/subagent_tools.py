"""
SubAgent Tools - Tool definitions for spawning and managing subagents.

These tools allow the main AI entity to:
- Spawn autonomous subagents to perform tasks
- Check the status of running agents
- Send follow-up instructions to agents
- Stop running agents

Subagents run asynchronously and can perform file operations, run commands,
and execute complex workflows within their configured working directories.

Tools are registered via register_subagent_tools() called from services/__init__.py.
"""

import logging
import json
from typing import Optional
from datetime import datetime

from app.services.tool_service import ToolCategory, ToolService
from app.config import settings

logger = logging.getLogger(__name__)


# Track conversation context for subagent tools (set by session manager before tool execution)
_current_conversation_id: Optional[str] = None
_current_entity_id: Optional[str] = None
_db_session_maker = None  # Set during registration


def set_subagent_tool_context(conversation_id: str, entity_id: str) -> None:
    """Set the conversation context for subagent tool execution."""
    global _current_conversation_id, _current_entity_id
    _current_conversation_id = conversation_id
    _current_entity_id = entity_id
    logger.debug(f"Subagent tools: context set to conversation_id='{conversation_id}', entity_id='{entity_id}'")


def get_subagent_tool_context() -> tuple[Optional[str], Optional[str]]:
    """Get the current conversation and entity context for tool execution."""
    return _current_conversation_id, _current_entity_id


async def _create_subagent(
    agent_type: str,
    instructions: str,
    working_directory: Optional[str] = None,
) -> str:
    """
    Create and start a new subagent to perform autonomous tasks.

    Subagents are autonomous Claude instances that can read files, run commands,
    and perform complex tasks within their configured working directory. They run
    asynchronously and will complete their task without further interaction.

    Args:
        agent_type: The type of agent to create (must be configured in the system)
        instructions: Detailed instructions for what the agent should do
        working_directory: Optional working directory override (must be allowed by configuration)

    Returns:
        JSON object with agent ID and status information
    """
    from app.services.subagent_service import subagent_service
    from app.database import async_session_maker

    conversation_id, entity_id = get_subagent_tool_context()

    if not conversation_id:
        return json.dumps({
            "success": False,
            "error": "No conversation context available for subagent creation"
        })

    if not subagent_service.is_enabled():
        return json.dumps({
            "success": False,
            "error": "Subagent functionality is not enabled"
        })

    # Validate agent type
    agent_type_config = subagent_service.get_agent_type(agent_type)
    if not agent_type_config:
        available = [t.name for t in subagent_service.get_agent_types()]
        return json.dumps({
            "success": False,
            "error": f"Unknown agent type: {agent_type}",
            "available_types": available
        })

    try:
        async with async_session_maker() as db:
            # Create the agent
            agent = await subagent_service.create_agent(
                db=db,
                conversation_id=conversation_id,
                agent_type_name=agent_type,
                instructions=instructions,
                working_directory=working_directory,
                spawning_entity_id=entity_id,
            )

            # Start the agent asynchronously
            await subagent_service.start_agent(db=db, agent_id=agent.id)

            return json.dumps({
                "success": True,
                "agent_id": agent.id,
                "agent_type": agent.agent_type,
                "status": agent.status.value,
                "working_directory": agent.working_directory,
                "message": f"Subagent created and started. It will execute autonomously."
            })

    except ValueError as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        })
    except Exception as e:
        logger.error(f"Error creating subagent: {e}")
        return json.dumps({
            "success": False,
            "error": f"Failed to create subagent: {str(e)}"
        })


async def _get_agent_status(agent_id: Optional[str] = None) -> str:
    """
    Get the status of subagents.

    If agent_id is provided, returns detailed status for that specific agent.
    Otherwise, returns a summary of all agents in the current conversation.

    Args:
        agent_id: Optional specific agent ID to check

    Returns:
        JSON object with agent status information
    """
    from app.services.subagent_service import subagent_service
    from app.database import async_session_maker

    conversation_id, _ = get_subagent_tool_context()

    if not conversation_id:
        return json.dumps({
            "success": False,
            "error": "No conversation context available"
        })

    if not subagent_service.is_enabled():
        return json.dumps({
            "success": False,
            "error": "Subagent functionality is not enabled"
        })

    try:
        async with async_session_maker() as db:
            if agent_id:
                # Get specific agent
                agent = await subagent_service.get_agent(db, agent_id)
                if not agent:
                    return json.dumps({
                        "success": False,
                        "error": f"Agent not found: {agent_id}"
                    })

                # Verify it belongs to this conversation
                if agent.conversation_id != conversation_id:
                    return json.dumps({
                        "success": False,
                        "error": "Agent does not belong to this conversation"
                    })

                return json.dumps({
                    "success": True,
                    "agent": agent.to_dict()
                })
            else:
                # Get all agents for conversation
                agents = await subagent_service.get_conversation_agents(db, conversation_id)

                return json.dumps({
                    "success": True,
                    "total_agents": len(agents),
                    "active_agents": sum(1 for a in agents if a.is_active),
                    "agents": [a.to_dict() for a in agents]
                })

    except Exception as e:
        logger.error(f"Error getting agent status: {e}")
        return json.dumps({
            "success": False,
            "error": f"Failed to get agent status: {str(e)}"
        })


async def _send_agent_instruction(agent_id: str, instruction: str) -> str:
    """
    Send a follow-up instruction to a running or waiting agent.

    This allows you to provide additional guidance to an agent that is
    actively working on a task. The agent will incorporate this instruction
    into its ongoing work.

    Args:
        agent_id: The ID of the agent to send the instruction to
        instruction: The follow-up instruction or guidance

    Returns:
        JSON object indicating success or failure
    """
    from app.services.subagent_service import subagent_service
    from app.database import async_session_maker

    conversation_id, _ = get_subagent_tool_context()

    if not conversation_id:
        return json.dumps({
            "success": False,
            "error": "No conversation context available"
        })

    if not subagent_service.is_enabled():
        return json.dumps({
            "success": False,
            "error": "Subagent functionality is not enabled"
        })

    try:
        async with async_session_maker() as db:
            # Verify agent exists and belongs to this conversation
            agent = await subagent_service.get_agent(db, agent_id)
            if not agent:
                return json.dumps({
                    "success": False,
                    "error": f"Agent not found: {agent_id}"
                })

            if agent.conversation_id != conversation_id:
                return json.dumps({
                    "success": False,
                    "error": "Agent does not belong to this conversation"
                })

            if not agent.is_active:
                return json.dumps({
                    "success": False,
                    "error": f"Agent is not active (status: {agent.status.value})"
                })

            # Send the follow-up
            success = await subagent_service.send_follow_up(db, agent_id, instruction)

            if success:
                return json.dumps({
                    "success": True,
                    "message": "Follow-up instruction sent to agent"
                })
            else:
                return json.dumps({
                    "success": False,
                    "error": "Failed to send follow-up instruction"
                })

    except Exception as e:
        logger.error(f"Error sending agent instruction: {e}")
        return json.dumps({
            "success": False,
            "error": f"Failed to send instruction: {str(e)}"
        })


async def _stop_agent(agent_id: str) -> str:
    """
    Stop a running subagent.

    This immediately stops the agent's execution. Any partial results
    will be preserved.

    Args:
        agent_id: The ID of the agent to stop

    Returns:
        JSON object indicating success or failure
    """
    from app.services.subagent_service import subagent_service
    from app.database import async_session_maker

    conversation_id, _ = get_subagent_tool_context()

    if not conversation_id:
        return json.dumps({
            "success": False,
            "error": "No conversation context available"
        })

    if not subagent_service.is_enabled():
        return json.dumps({
            "success": False,
            "error": "Subagent functionality is not enabled"
        })

    try:
        async with async_session_maker() as db:
            # Verify agent exists and belongs to this conversation
            agent = await subagent_service.get_agent(db, agent_id)
            if not agent:
                return json.dumps({
                    "success": False,
                    "error": f"Agent not found: {agent_id}"
                })

            if agent.conversation_id != conversation_id:
                return json.dumps({
                    "success": False,
                    "error": "Agent does not belong to this conversation"
                })

            # Stop the agent
            stopped = await subagent_service.stop_agent(db, agent_id)

            if stopped:
                return json.dumps({
                    "success": True,
                    "message": "Agent stopped successfully"
                })
            else:
                return json.dumps({
                    "success": False,
                    "error": f"Agent was not running (status: {agent.status.value})"
                })

    except Exception as e:
        logger.error(f"Error stopping agent: {e}")
        return json.dumps({
            "success": False,
            "error": f"Failed to stop agent: {str(e)}"
        })


async def _list_agent_types() -> str:
    """
    List all available subagent types that can be created.

    Returns information about each configured agent type including
    its name, description, allowed tools, and working directory.

    Returns:
        JSON object with list of available agent types
    """
    from app.services.subagent_service import subagent_service

    if not subagent_service.is_enabled():
        return json.dumps({
            "success": False,
            "error": "Subagent functionality is not enabled"
        })

    agent_types = subagent_service.get_agent_types()

    if not agent_types:
        return json.dumps({
            "success": True,
            "message": "No agent types configured",
            "types": []
        })

    return json.dumps({
        "success": True,
        "types": [
            {
                "name": t.name,
                "label": t.label,
                "description": t.description,
                "allowed_tools": t.allowed_tools,
                "working_directory": t.working_directory,
                "model": t.model,
            }
            for t in agent_types
        ]
    })


def register_subagent_tools(tool_service: ToolService) -> None:
    """Register all subagent tools with the tool service."""

    # Only register if subagents are enabled
    if not settings.subagents_enabled:
        logger.info("Subagent tools not registered (subagents not enabled)")
        return

    # create_subagent
    tool_service.register_tool(
        name="create_subagent",
        description=(
            "Create and start a new autonomous subagent to perform tasks. "
            "Subagents are independent Claude instances that can read files, run commands, "
            "and perform complex workflows within their configured working directory. "
            "They run asynchronously and complete their task without requiring further interaction. "
            "Use this when you need to delegate a task that can be performed independently, "
            "such as code analysis, file processing, or running build/test commands."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "description": (
                        "The type of agent to create. Use list_agent_types to see "
                        "available types with their capabilities and restrictions."
                    )
                },
                "instructions": {
                    "type": "string",
                    "description": (
                        "Detailed instructions for what the agent should do. Be specific "
                        "about the task, expected output, and any constraints."
                    )
                },
                "working_directory": {
                    "type": "string",
                    "description": (
                        "Optional working directory override. If not provided, uses the "
                        "agent type's default directory. Must be an allowed directory."
                    )
                }
            },
            "required": ["agent_type", "instructions"]
        },
        executor=_create_subagent,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # get_agent_status
    tool_service.register_tool(
        name="get_agent_status",
        description=(
            "Get the status of subagents. If agent_id is provided, returns detailed "
            "status for that specific agent including its result if completed. "
            "Otherwise, returns a summary of all agents in the current conversation."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Optional specific agent ID to check. If omitted, returns all agents."
                }
            }
        },
        executor=_get_agent_status,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # send_agent_instruction
    tool_service.register_tool(
        name="send_agent_instruction",
        description=(
            "Send a follow-up instruction to a running or waiting agent. "
            "Use this to provide additional guidance or modify the agent's task "
            "while it is still executing."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The ID of the agent to send the instruction to"
                },
                "instruction": {
                    "type": "string",
                    "description": "The follow-up instruction or guidance to send"
                }
            },
            "required": ["agent_id", "instruction"]
        },
        executor=_send_agent_instruction,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # stop_agent
    tool_service.register_tool(
        name="stop_agent",
        description=(
            "Stop a running subagent immediately. "
            "Any partial results will be preserved and can be retrieved with get_agent_status."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The ID of the agent to stop"
                }
            },
            "required": ["agent_id"]
        },
        executor=_stop_agent,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # list_agent_types
    tool_service.register_tool(
        name="list_agent_types",
        description=(
            "List all available subagent types that can be created. "
            "Returns information about each type including its name, description, "
            "allowed tools, and default working directory."
        ),
        input_schema={
            "type": "object",
            "properties": {}
        },
        executor=_list_agent_types,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    logger.info("Subagent tools registered: create_subagent, get_agent_status, send_agent_instruction, stop_agent, list_agent_types")
