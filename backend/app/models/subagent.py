"""
SubAgent model for tracking autonomous agent instances spawned by the main AI.

Each SubAgent represents a running or completed agent instance that was created
during a conversation. The model tracks:
- Agent configuration and status
- Link to the originating conversation
- Instructions given by the main AI
- Results and output from the agent
- Follow-up instructions sent during execution
"""

import uuid
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import String, Text, DateTime, Integer, ForeignKey, Enum as SQLEnum, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
import enum


class SubAgentStatus(str, enum.Enum):
    """Status of a subagent instance."""
    PENDING = "pending"        # Created but not yet started
    RUNNING = "running"        # Currently executing
    WAITING = "waiting"        # Waiting for additional instructions
    COMPLETED = "completed"    # Finished successfully
    FAILED = "failed"          # Finished with error
    STOPPED = "stopped"        # Manually stopped by user or main AI


class SubAgent(Base):
    """
    Tracks a subagent instance spawned by the main AI.

    SubAgents are autonomous agent instances that can read files, run commands,
    and perform other tasks within their configured working directory.
    """
    __tablename__ = "subagents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Link to the conversation that spawned this agent
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"))

    # Agent type (from configuration)
    agent_type: Mapped[str] = mapped_column(String(100))

    # Status tracking
    status: Mapped[SubAgentStatus] = mapped_column(SQLEnum(SubAgentStatus), default=SubAgentStatus.PENDING)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Working directory for this agent instance
    working_directory: Mapped[str] = mapped_column(String(500))

    # Initial instructions from the main AI
    instructions: Mapped[str] = mapped_column(Text)

    # Follow-up instructions (JSON array of {"timestamp": ..., "instruction": ...})
    follow_up_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Final result/output from the agent
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Error message if failed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Claude Agent SDK session ID for resumption
    sdk_session_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Claude Agent SDK agent ID (for tracking within the session)
    sdk_agent_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Model used for this agent
    model: Mapped[str] = mapped_column(String(50), default="sonnet")

    # Entity ID that spawned this agent (for multi-entity conversations)
    spawning_entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Relationship to conversation
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="subagents")

    @property
    def follow_up_list(self) -> List[Dict[str, Any]]:
        """Get follow-up instructions as a list of dicts."""
        if not self.follow_up_instructions:
            return []
        try:
            return json.loads(self.follow_up_instructions)
        except (json.JSONDecodeError, TypeError):
            return []

    def add_follow_up(self, instruction: str) -> None:
        """Add a follow-up instruction."""
        follow_ups = self.follow_up_list
        follow_ups.append({
            "timestamp": datetime.utcnow().isoformat(),
            "instruction": instruction
        })
        self.follow_up_instructions = json.dumps(follow_ups)

    @property
    def is_active(self) -> bool:
        """Check if the agent is currently active (running or waiting)."""
        return self.status in (SubAgentStatus.RUNNING, SubAgentStatus.WAITING, SubAgentStatus.PENDING)

    @property
    def is_terminal(self) -> bool:
        """Check if the agent has reached a terminal state."""
        return self.status in (SubAgentStatus.COMPLETED, SubAgentStatus.FAILED, SubAgentStatus.STOPPED)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "agent_type": self.agent_type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "working_directory": self.working_directory,
            "instructions": self.instructions,
            "follow_up_instructions": self.follow_up_list,
            "result": self.result,
            "error_message": self.error_message,
            "model": self.model,
            "spawning_entity_id": self.spawning_entity_id,
            "is_active": self.is_active,
            "is_terminal": self.is_terminal,
        }

    def to_context_summary(self) -> str:
        """
        Generate a summary suitable for inclusion in the main AI's context.

        This provides the main AI with awareness of the agent's current state.
        """
        status_emoji = {
            SubAgentStatus.PENDING: "[PENDING]",
            SubAgentStatus.RUNNING: "[RUNNING]",
            SubAgentStatus.WAITING: "[WAITING FOR INPUT]",
            SubAgentStatus.COMPLETED: "[COMPLETED]",
            SubAgentStatus.FAILED: "[FAILED]",
            SubAgentStatus.STOPPED: "[STOPPED]",
        }

        lines = [
            f"Agent ID: {self.id[:8]}...",
            f"Type: {self.agent_type}",
            f"Status: {status_emoji.get(self.status, str(self.status))}",
            f"Working Directory: {self.working_directory}",
            f"Instructions: {self.instructions[:200]}{'...' if len(self.instructions) > 200 else ''}",
        ]

        if self.follow_up_list:
            lines.append(f"Follow-up Instructions: {len(self.follow_up_list)} sent")

        if self.result:
            lines.append(f"Result: {self.result[:300]}{'...' if len(self.result) > 300 else ''}")

        if self.error_message:
            lines.append(f"Error: {self.error_message[:200]}{'...' if len(self.error_message) > 200 else ''}")

        return "\n".join(lines)
