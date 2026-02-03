"""
SubAgent Service for managing autonomous agent instances.

This service provides:
- Agent creation using the Claude Agent SDK
- Lifecycle management (start, stop, status)
- Security validation (directory, command restrictions)
- Async execution with result tracking
- Integration with the database for persistence
"""

import asyncio
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncIterator, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings, AgentTypeConfig
from app.models.subagent import SubAgent, SubAgentStatus

logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    """A message from or about an agent."""
    type: str  # "status", "output", "result", "error", "tool_use", "tool_result"
    content: str
    timestamp: datetime = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
        if self.metadata is None:
            self.metadata = {}


class SubAgentService:
    """
    Service for managing subagent instances.

    This service uses the Claude Agent SDK to spawn autonomous agents that can
    perform tasks within their configured working directories. It implements
    security restrictions via PreToolUse hooks and manages the full lifecycle
    of agent instances.
    """

    def __init__(self):
        self._running_agents: Dict[str, asyncio.Task] = {}
        self._agent_queues: Dict[str, asyncio.Queue] = {}
        self._stop_events: Dict[str, asyncio.Event] = {}
        logger.info("SubAgentService initialized")

    def is_enabled(self) -> bool:
        """Check if subagent functionality is enabled."""
        return settings.subagents_enabled

    def get_agent_types(self) -> List[AgentTypeConfig]:
        """Get all configured agent types."""
        return settings.get_subagent_types()

    def get_agent_type(self, name: str) -> Optional[AgentTypeConfig]:
        """Get a specific agent type by name."""
        return settings.get_subagent_type_by_name(name)

    def validate_working_directory(self, directory: str) -> tuple[bool, str]:
        """
        Validate that a working directory is allowed.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not directory:
            return False, "Working directory is required"

        # Check if directory exists
        path = Path(directory)
        if not path.exists():
            return False, f"Directory does not exist: {directory}"

        if not path.is_dir():
            return False, f"Path is not a directory: {directory}"

        # Check against blocked directories
        if not settings.is_subagent_directory_allowed(directory):
            return False, f"Directory is not allowed for subagent operations: {directory}"

        return True, ""

    def validate_command(self, command: str, agent_type: AgentTypeConfig) -> tuple[bool, str]:
        """
        Validate that a command is allowed for the given agent type.

        Returns:
            Tuple of (is_allowed, reason)
        """
        for pattern in agent_type.blocked_commands:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"Command matches blocked pattern: {pattern}"
        return True, ""

    def create_security_hooks(
        self,
        agent_type: AgentTypeConfig,
        working_directory: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Create security hooks for an agent instance.

        These hooks enforce:
        - Working directory restrictions for file operations
        - Command blocking for Bash operations
        """
        async def validate_file_path(input_data, tool_use_id, context):
            """Validate that file operations stay within the working directory."""
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            # Get the file path from the tool input
            file_path = tool_input.get("file_path") or tool_input.get("path") or ""

            if file_path:
                # Resolve the path
                resolved = Path(working_directory).joinpath(file_path).resolve()
                working_dir_resolved = Path(working_directory).resolve()

                # Check if the resolved path is within the working directory
                try:
                    resolved.relative_to(working_dir_resolved)
                except ValueError:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": input_data.get("hook_event_name"),
                            "permissionDecision": "deny",
                            "permissionDecisionReason": f"File operation outside working directory: {file_path}"
                        }
                    }

            return {}

        async def validate_bash_command(input_data, tool_use_id, context):
            """Validate that Bash commands are allowed."""
            tool_input = input_data.get("tool_input", {})
            command = tool_input.get("command", "")

            if command:
                is_allowed, reason = self.validate_command(command, agent_type)
                if not is_allowed:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": input_data.get("hook_event_name"),
                            "permissionDecision": "deny",
                            "permissionDecisionReason": reason
                        }
                    }

                # Also check for directory escape attempts in commands
                dangerous_patterns = [
                    r"\.\./",  # Parent directory traversal
                    r"cd\s+/",  # Absolute path cd
                    r"cd\s+~",  # Home directory cd
                ]
                for pattern in dangerous_patterns:
                    if re.search(pattern, command):
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": input_data.get("hook_event_name"),
                                "permissionDecision": "deny",
                                "permissionDecisionReason": f"Command attempts to escape working directory"
                            }
                        }

            return {}

        # Import HookMatcher dynamically to avoid import errors if SDK not installed
        try:
            from claude_agent_sdk import HookMatcher
            return {
                "PreToolUse": [
                    HookMatcher(matcher="Read|Write|Edit|Glob|Grep", hooks=[validate_file_path]),
                    HookMatcher(matcher="Bash", hooks=[validate_bash_command]),
                ]
            }
        except ImportError:
            logger.warning("Claude Agent SDK not installed, returning empty hooks")
            return {}

    async def create_agent(
        self,
        db: AsyncSession,
        conversation_id: str,
        agent_type_name: str,
        instructions: str,
        working_directory: Optional[str] = None,
        spawning_entity_id: Optional[str] = None,
    ) -> SubAgent:
        """
        Create a new subagent instance.

        This creates the database record but does not start the agent.
        Use start_agent() to begin execution.
        """
        agent_type = self.get_agent_type(agent_type_name)
        if not agent_type:
            raise ValueError(f"Unknown agent type: {agent_type_name}")

        # Use the agent type's working directory if not specified
        work_dir = working_directory or agent_type.working_directory
        if not work_dir:
            raise ValueError("Working directory is required")

        # Validate the working directory
        is_valid, error = self.validate_working_directory(work_dir)
        if not is_valid:
            raise ValueError(error)

        # Check concurrent agent limit
        active_count = await self._count_active_agents(db, conversation_id)
        if active_count >= settings.subagent_max_concurrent:
            raise ValueError(
                f"Maximum concurrent agents ({settings.subagent_max_concurrent}) reached for this conversation"
            )

        # Create the agent record
        agent = SubAgent(
            conversation_id=conversation_id,
            agent_type=agent_type_name,
            status=SubAgentStatus.PENDING,
            working_directory=work_dir,
            instructions=instructions,
            model=agent_type.model,
            spawning_entity_id=spawning_entity_id,
        )

        db.add(agent)
        await db.commit()
        await db.refresh(agent)

        logger.info(f"Created subagent {agent.id} of type {agent_type_name}")
        return agent

    async def start_agent(
        self,
        db: AsyncSession,
        agent_id: str,
        message_callback: Optional[Callable[[AgentMessage], None]] = None,
    ) -> None:
        """
        Start a subagent's execution.

        This runs the agent in the background using the Claude Agent SDK.
        Messages are sent to the callback if provided, and also queued for
        later retrieval.
        """
        agent = await self.get_agent(db, agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")

        if agent.status != SubAgentStatus.PENDING:
            raise ValueError(f"Agent is not in pending state: {agent.status}")

        agent_type = self.get_agent_type(agent.agent_type)
        if not agent_type:
            raise ValueError(f"Agent type not found: {agent.agent_type}")

        # Create message queue and stop event
        self._agent_queues[agent_id] = asyncio.Queue()
        self._stop_events[agent_id] = asyncio.Event()

        # Update status to running
        agent.status = SubAgentStatus.RUNNING
        agent.started_at = datetime.utcnow()
        await db.commit()

        # Start the agent execution task
        task = asyncio.create_task(
            self._run_agent(db, agent, agent_type, message_callback)
        )
        self._running_agents[agent_id] = task

        logger.info(f"Started subagent {agent_id}")

    async def _run_agent(
        self,
        db: AsyncSession,
        agent: SubAgent,
        agent_type: AgentTypeConfig,
        message_callback: Optional[Callable[[AgentMessage], None]] = None,
    ) -> None:
        """
        Internal method to run the agent using Claude Agent SDK.

        This method handles the actual agent execution and result collection.
        """
        agent_id = agent.id

        try:
            # Import Claude Agent SDK
            from claude_agent_sdk import query, ClaudeAgentOptions

            # Build the options
            options = ClaudeAgentOptions(
                allowed_tools=agent_type.allowed_tools,
                cwd=agent.working_directory,
                max_turns=agent_type.max_turns,
                permission_mode="acceptEdits",  # Auto-accept since we have hooks
                hooks=self.create_security_hooks(agent_type, agent.working_directory),
            )

            # Add system prompt if configured
            if agent_type.system_prompt:
                options.system_prompt = agent_type.system_prompt

            # Collect all output
            result_parts = []
            stop_event = self._stop_events.get(agent_id)

            async for message in query(prompt=agent.instructions, options=options):
                # Check for stop request
                if stop_event and stop_event.is_set():
                    logger.info(f"Agent {agent_id} stop requested")
                    break

                # Process the message
                msg = self._process_sdk_message(message)
                if msg:
                    # Queue the message
                    queue = self._agent_queues.get(agent_id)
                    if queue:
                        await queue.put(msg)

                    # Call the callback if provided
                    if message_callback:
                        try:
                            message_callback(msg)
                        except Exception as e:
                            logger.error(f"Error in message callback: {e}")

                    # Collect result parts
                    if msg.type == "result":
                        result_parts.append(msg.content)

                # Capture session ID for potential resumption
                if hasattr(message, "session_id"):
                    agent.sdk_session_id = message.session_id

            # Store the result
            agent.result = "\n".join(result_parts) if result_parts else None
            agent.status = SubAgentStatus.COMPLETED
            agent.completed_at = datetime.utcnow()

        except asyncio.CancelledError:
            agent.status = SubAgentStatus.STOPPED
            agent.completed_at = datetime.utcnow()
            logger.info(f"Agent {agent_id} cancelled")

        except Exception as e:
            agent.status = SubAgentStatus.FAILED
            agent.error_message = str(e)
            agent.completed_at = datetime.utcnow()
            logger.error(f"Agent {agent_id} failed: {e}")

            # Send error message
            error_msg = AgentMessage(type="error", content=str(e))
            queue = self._agent_queues.get(agent_id)
            if queue:
                await queue.put(error_msg)
            if message_callback:
                try:
                    message_callback(error_msg)
                except Exception:
                    pass

        finally:
            # Commit the final status
            try:
                await db.commit()
            except Exception as e:
                logger.error(f"Error committing agent status: {e}")

            # Clean up
            self._running_agents.pop(agent_id, None)
            self._stop_events.pop(agent_id, None)

            # Signal completion to queue consumers
            queue = self._agent_queues.get(agent_id)
            if queue:
                await queue.put(None)  # Sentinel for completion

    def _process_sdk_message(self, message: Any) -> Optional[AgentMessage]:
        """Process a message from the Claude Agent SDK into our format."""
        try:
            # Check for result message
            if hasattr(message, "result"):
                return AgentMessage(type="result", content=message.result)

            # Check for content blocks (tool use, text, etc.)
            if hasattr(message, "content") and message.content:
                for block in message.content:
                    block_type = getattr(block, "type", None)

                    if block_type == "text":
                        return AgentMessage(
                            type="output",
                            content=getattr(block, "text", str(block))
                        )

                    if block_type == "tool_use":
                        return AgentMessage(
                            type="tool_use",
                            content=f"Using tool: {getattr(block, 'name', 'unknown')}",
                            metadata={
                                "tool_name": getattr(block, "name", None),
                                "tool_input": getattr(block, "input", {}),
                            }
                        )

                    if block_type == "tool_result":
                        return AgentMessage(
                            type="tool_result",
                            content=str(getattr(block, "content", "")),
                            metadata={
                                "tool_use_id": getattr(block, "tool_use_id", None),
                            }
                        )

            # Check for system messages
            if hasattr(message, "type") and message.type == "system":
                subtype = getattr(message, "subtype", "")
                return AgentMessage(
                    type="status",
                    content=f"System: {subtype}",
                    metadata={"subtype": subtype}
                )

        except Exception as e:
            logger.warning(f"Error processing SDK message: {e}")

        return None

    async def stop_agent(self, db: AsyncSession, agent_id: str) -> bool:
        """
        Stop a running agent.

        Returns True if the agent was stopped, False if it wasn't running.
        """
        agent = await self.get_agent(db, agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")

        if not agent.is_active:
            return False

        # Signal the agent to stop
        stop_event = self._stop_events.get(agent_id)
        if stop_event:
            stop_event.set()

        # Cancel the task if running
        task = self._running_agents.get(agent_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Update status
        agent.status = SubAgentStatus.STOPPED
        agent.completed_at = datetime.utcnow()
        await db.commit()

        logger.info(f"Stopped agent {agent_id}")
        return True

    async def send_follow_up(
        self,
        db: AsyncSession,
        agent_id: str,
        instruction: str
    ) -> bool:
        """
        Send a follow-up instruction to a running or waiting agent.

        This can be used by the main AI to provide additional guidance
        or by the user to interact with the agent.
        """
        agent = await self.get_agent(db, agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")

        if not agent.is_active:
            return False

        # Store the follow-up instruction
        agent.add_follow_up(instruction)
        await db.commit()

        # TODO: Implement actual follow-up sending via SDK session resumption
        # This would require resuming the session and sending the new instruction
        logger.info(f"Recorded follow-up for agent {agent_id}: {instruction[:50]}...")

        return True

    async def get_agent(self, db: AsyncSession, agent_id: str) -> Optional[SubAgent]:
        """Get a subagent by ID."""
        result = await db.execute(
            select(SubAgent).where(SubAgent.id == agent_id)
        )
        return result.scalar_one_or_none()

    async def get_conversation_agents(
        self,
        db: AsyncSession,
        conversation_id: str,
        include_completed: bool = True
    ) -> List[SubAgent]:
        """Get all agents for a conversation."""
        query = select(SubAgent).where(SubAgent.conversation_id == conversation_id)

        if not include_completed:
            query = query.where(
                SubAgent.status.in_([
                    SubAgentStatus.PENDING,
                    SubAgentStatus.RUNNING,
                    SubAgentStatus.WAITING
                ])
            )

        query = query.order_by(SubAgent.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_active_agents(self, db: AsyncSession, conversation_id: str) -> List[SubAgent]:
        """Get only active (non-terminal) agents for a conversation."""
        return await self.get_conversation_agents(db, conversation_id, include_completed=False)

    async def _count_active_agents(self, db: AsyncSession, conversation_id: str) -> int:
        """Count active agents for a conversation."""
        agents = await self.get_active_agents(db, conversation_id)
        return len(agents)

    async def get_messages(
        self,
        agent_id: str,
        timeout: float = 0.0
    ) -> AsyncIterator[AgentMessage]:
        """
        Get messages from a running agent.

        This is an async iterator that yields messages as they arrive.
        If timeout is 0, it returns immediately with any queued messages.
        If timeout > 0, it waits up to that many seconds for messages.
        """
        queue = self._agent_queues.get(agent_id)
        if not queue:
            return

        try:
            while True:
                try:
                    if timeout > 0:
                        msg = await asyncio.wait_for(queue.get(), timeout=timeout)
                    else:
                        msg = queue.get_nowait()
                except asyncio.TimeoutError:
                    break
                except asyncio.QueueEmpty:
                    break

                if msg is None:  # Sentinel for completion
                    break

                yield msg

        except Exception as e:
            logger.error(f"Error getting messages for agent {agent_id}: {e}")

    def get_agents_context_summary(self, agents: List[SubAgent]) -> str:
        """
        Generate a context summary of agents for the main AI.

        This summary is included in the main AI's context so it's aware
        of running agents and their status.
        """
        if not agents:
            return ""

        lines = ["[SUBAGENT STATUS]"]
        for agent in agents:
            lines.append("-" * 40)
            lines.append(agent.to_context_summary())
        lines.append("[/SUBAGENT STATUS]")

        return "\n".join(lines)


# Singleton instance
subagent_service = SubAgentService()
