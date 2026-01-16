"""
Go Game Database Model

Stores Go game state linked to a conversation for the dual-channel design:
- Channel 1 (Game): Board state, moves, captures, scoring
- Channel 2 (Conversation): The linked conversation for discussion
"""

import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Integer, Text, DateTime, JSON, ForeignKey, Enum as SQLEnum, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
import enum


class GameStatus(str, enum.Enum):
    """Status of a Go game."""
    ACTIVE = "active"
    SCORING = "scoring"  # Both players passed, marking dead stones
    FINISHED = "finished"


class StoneColor(str, enum.Enum):
    """Stone colors in Go."""
    BLACK = "black"
    WHITE = "white"


class GoGame(Base):
    """
    Database model for a Go game.
    
    The game is linked to a conversation, enabling the dual-channel design:
    - The game tracks mechanical state (board, moves, captures)
    - The conversation tracks relational content (discussion, commentary)
    
    When processing messages in a linked conversation, the current board
    state is injected ephemerally into context.
    """
    __tablename__ = "go_games"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=datetime.utcnow, nullable=True)
    
    # Link to conversation for the relational channel
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"))
    
    # Game configuration
    board_size: Mapped[int] = mapped_column(Integer, default=19)  # 9, 13, or 19
    komi: Mapped[float] = mapped_column(Float, default=6.5)  # Points compensation for white
    
    # Game state
    # Board stored as JSON: 2D array where 0=empty, 1=black, 2=white
    board_state: Mapped[dict] = mapped_column(JSON)
    current_player: Mapped[StoneColor] = mapped_column(
        SQLEnum(StoneColor), default=StoneColor.BLACK
    )
    status: Mapped[GameStatus] = mapped_column(
        SQLEnum(GameStatus), default=GameStatus.ACTIVE
    )
    
    # Move tracking - SGF format: ";B[pd];W[dd];B[pq]..."
    move_history: Mapped[str] = mapped_column(Text, default="")
    move_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Captures (stones captured BY each color)
    black_captures: Mapped[int] = mapped_column(Integer, default=0)
    white_captures: Mapped[int] = mapped_column(Integer, default=0)
    
    # Ko rule tracking - position forbidden by ko, stored as "row,col" or null
    ko_point: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    
    # Pass tracking for game end (two consecutive passes)
    consecutive_passes: Mapped[int] = mapped_column(Integer, default=0)
    
    # Game result (filled when game ends)
    winner: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # "black", "white"
    black_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    white_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    result_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "resignation", "score", "timeout"
    
    # Which entity plays which color (entity_id from config)
    # If null, that color is played by the human
    black_entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    white_entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="go_games")
    
    @property
    def entity_color(self) -> Optional[StoneColor]:
        """Get the color played by the AI entity, if any."""
        if self.black_entity_id:
            return StoneColor.BLACK
        elif self.white_entity_id:
            return StoneColor.WHITE
        return None
    
    @property
    def is_entity_turn(self) -> bool:
        """Check if it's the AI entity's turn to play."""
        if self.current_player == StoneColor.BLACK and self.black_entity_id:
            return True
        if self.current_player == StoneColor.WHITE and self.white_entity_id:
            return True
        return False
    
    @property
    def current_entity_id(self) -> Optional[str]:
        """Get the entity_id whose turn it is, or None if human's turn."""
        if self.current_player == StoneColor.BLACK:
            return self.black_entity_id
        return self.white_entity_id
