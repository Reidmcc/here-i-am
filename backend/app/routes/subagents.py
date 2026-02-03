"""
SubAgent API routes for managing autonomous agents.

Provides endpoints for the frontend to:
- List agent types and their configurations
- View agents for a conversation
- Stop running agents
- Get detailed agent status
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import json
import asyncio

from app.database import get_db
from app.config import settings
from app.services.subagent_service import subagent_service
from app.models.subagent import SubAgent, SubAgentStatus

router = APIRouter(prefix="/api/subagents", tags=["subagents"])


class StopAgentRequest(BaseModel):
    """Request body for stopping an agent."""
    pass


class SendInstructionRequest(BaseModel):
    """Request body for sending a follow-up instruction."""
    instruction: str


@router.get("/status")
async def get_subagents_status() -> Dict[str, Any]:
    """
    Get the overall status of the subagent system.

    Returns whether subagents are enabled and basic configuration info.
    """
    return {
        "enabled": settings.subagents_enabled,
        "max_concurrent": settings.subagent_max_concurrent,
        "default_timeout": settings.subagent_default_timeout,
    }


@router.get("/types")
async def list_agent_types() -> List[Dict[str, Any]]:
    """
    List all configured subagent types.

    Returns information about each type including name, description,
    allowed tools, and default working directory.
    """
    if not settings.subagents_enabled:
        return []

    types = subagent_service.get_agent_types()
    return [t.to_dict() for t in types]


@router.get("/types/{type_name}")
async def get_agent_type(type_name: str) -> Dict[str, Any]:
    """
    Get details about a specific agent type.

    Args:
        type_name: The name of the agent type

    Returns:
        Agent type configuration
    """
    if not settings.subagents_enabled:
        raise HTTPException(status_code=404, detail="Subagents not enabled")

    agent_type = subagent_service.get_agent_type(type_name)
    if not agent_type:
        raise HTTPException(status_code=404, detail=f"Agent type not found: {type_name}")

    return agent_type.to_dict()


@router.get("/conversation/{conversation_id}")
async def list_conversation_agents(
    conversation_id: str,
    include_completed: bool = True,
    db: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """
    List all agents for a conversation.

    Args:
        conversation_id: The conversation ID
        include_completed: Whether to include completed/stopped agents

    Returns:
        List of agent details
    """
    if not settings.subagents_enabled:
        return []

    agents = await subagent_service.get_conversation_agents(
        db, conversation_id, include_completed
    )
    return [a.to_dict() for a in agents]


@router.get("/conversation/{conversation_id}/active")
async def list_active_agents(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """
    List only active (running/waiting) agents for a conversation.

    Args:
        conversation_id: The conversation ID

    Returns:
        List of active agent details
    """
    if not settings.subagents_enabled:
        return []

    agents = await subagent_service.get_active_agents(db, conversation_id)
    return [a.to_dict() for a in agents]


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get detailed information about a specific agent.

    Args:
        agent_id: The agent ID

    Returns:
        Agent details including status, instructions, and results
    """
    if not settings.subagents_enabled:
        raise HTTPException(status_code=404, detail="Subagents not enabled")

    agent = await subagent_service.get_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    return agent.to_dict()


@router.post("/{agent_id}/stop")
async def stop_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Stop a running agent.

    Args:
        agent_id: The agent ID to stop

    Returns:
        Result of the stop operation
    """
    if not settings.subagents_enabled:
        raise HTTPException(status_code=404, detail="Subagents not enabled")

    try:
        stopped = await subagent_service.stop_agent(db, agent_id)
        return {
            "success": stopped,
            "message": "Agent stopped" if stopped else "Agent was not running"
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop agent: {str(e)}")


@router.post("/{agent_id}/instruction")
async def send_instruction(
    agent_id: str,
    request: SendInstructionRequest,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Send a follow-up instruction to a running agent.

    Args:
        agent_id: The agent ID
        request: The instruction to send

    Returns:
        Result of the operation
    """
    if not settings.subagents_enabled:
        raise HTTPException(status_code=404, detail="Subagents not enabled")

    try:
        success = await subagent_service.send_follow_up(db, agent_id, request.instruction)
        return {
            "success": success,
            "message": "Instruction sent" if success else "Failed to send instruction"
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send instruction: {str(e)}")


@router.get("/{agent_id}/messages")
async def stream_agent_messages(
    agent_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Stream messages from a running agent.

    This is an SSE endpoint that streams messages as they arrive
    from the agent. The stream ends when the agent completes.

    Args:
        agent_id: The agent ID

    Returns:
        SSE stream of agent messages
    """
    if not settings.subagents_enabled:
        raise HTTPException(status_code=404, detail="Subagents not enabled")

    # Verify the agent exists
    agent = await subagent_service.get_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    async def generate():
        """Generate SSE events from agent messages."""
        try:
            # Send initial status
            yield f"event: status\ndata: {json.dumps({'status': agent.status.value})}\n\n"

            # If agent is not active, just return its result
            if not agent.is_active:
                if agent.result:
                    yield f"event: result\ndata: {json.dumps({'content': agent.result})}\n\n"
                if agent.error_message:
                    yield f"event: error\ndata: {json.dumps({'content': agent.error_message})}\n\n"
                yield f"event: done\ndata: {json.dumps({'status': agent.status.value})}\n\n"
                return

            # Stream messages from running agent
            async for msg in subagent_service.get_messages(agent_id, timeout=30.0):
                event_data = {
                    "type": msg.type,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                    "metadata": msg.metadata,
                }
                yield f"event: message\ndata: {json.dumps(event_data)}\n\n"

            # Send final status
            await db.refresh(agent)
            final_data = {
                "status": agent.status.value,
                "result": agent.result,
                "error": agent.error_message,
            }
            yield f"event: done\ndata: {json.dumps(final_data)}\n\n"

        except asyncio.CancelledError:
            yield f"event: cancelled\ndata: {json.dumps({'message': 'Stream cancelled'})}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/validate/directory")
async def validate_directory(directory: str) -> Dict[str, Any]:
    """
    Validate if a directory is allowed for subagent operations.

    Args:
        directory: The directory path to validate

    Returns:
        Validation result with is_valid and error message if invalid
    """
    if not settings.subagents_enabled:
        return {"is_valid": False, "error": "Subagents not enabled"}

    is_valid, error = subagent_service.validate_working_directory(directory)
    return {
        "is_valid": is_valid,
        "error": error if not is_valid else None,
        "directory": directory,
    }
