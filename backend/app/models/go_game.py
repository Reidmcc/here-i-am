import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Text, DateTime, JSON, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
import enum


class GameStatus(str, enum.Enum):
    """Status of a Go game."""
    IN_PROGRESS = "in_progress"
    FINISHED_RESIGNATION = "finished_resignation"
    FINISHED_PASS = "finished_pass"  # Both players passed consecutively
    FINISHED_SCORED = "finished_scored"


class ScoringMethod(str, enum.Enum):
    """Scoring method for Go games."""
    JAPANESE = "japanese"  # Territory scoring (default)
    CHINESE = "chinese"    # Area scoring


class StoneColor(str, enum.Enum):
    """Stone colors in Go."""
    BLACK = "black"
    WHITE = "white"


class GoGame(Base):
    """
    Database model for a Go game.

    Tracks full game state including board position, move history,
    captures, ko point, and game status.
    """
    __tablename__ = "go_games"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=datetime.utcnow, nullable=True)

    # Link to conversation for the relational channel
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"))

    # Game configuration
    board_size: Mapped[int] = mapped_column(Integer, default=19)  # 9, 13, or 19
    scoring_method: Mapped[ScoringMethod] = mapped_column(
        SQLEnum(ScoringMethod), default=ScoringMethod.JAPANESE
    )
    komi: Mapped[float] = mapped_column(Integer, default=6)  # 6.5 for Japanese, 7.5 for Chinese (stored as int * 2)

    # Game state
    # Board stored as JSON: 2D array where 0=empty, 1=black, 2=white
    board_state: Mapped[dict] = mapped_column(JSON)
    current_player: Mapped[StoneColor] = mapped_column(
        SQLEnum(StoneColor), default=StoneColor.BLACK
    )
    game_status: Mapped[GameStatus] = mapped_column(
        SQLEnum(GameStatus), default=GameStatus.IN_PROGRESS
    )

    # Move tracking
    # SGF format move history: e.g., ";B[pd];W[dd];B[pq]..."
    move_history: Mapped[str] = mapped_column(Text, default="")
    move_count: Mapped[int] = mapped_column(Integer, default=0)

    # Captures
    black_captures: Mapped[int] = mapped_column(Integer, default=0)  # Stones captured BY black
    white_captures: Mapped[int] = mapped_column(Integer, default=0)  # Stones captured BY white

    # Ko rule tracking
    # Ko point stored as "row,col" string or null if no ko
    ko_point: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Pass tracking for game end
    consecutive_passes: Mapped[int] = mapped_column(Integer, default=0)

    # Game result (filled when game ends)
    winner: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # "black", "white", or "draw"
    black_score: Mapped[Optional[float]] = mapped_column(Integer, nullable=True)  # Stored as int * 2 for half points
    white_score: Mapped[Optional[float]] = mapped_column(Integer, nullable=True)  # Stored as int * 2 for half points
    resignation_by: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # Who resigned, if applicable

    # Entity tracking - which AI entities are playing
    black_entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # AI entity playing black
    white_entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # AI entity playing white

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="go_games")

    def get_komi_float(self) -> float:
        """Get komi as a float value (stored as int * 2 for half points)."""
        return self.komi / 2.0 if self.komi else 0.0

    def set_komi_float(self, value: float):
        """Set komi from a float value."""
        self.komi = int(value * 2)

    def get_black_score_float(self) -> Optional[float]:
        """Get black score as float."""
        return self.black_score / 2.0 if self.black_score is not None else None

    def get_white_score_float(self) -> Optional[float]:
        """Get white score as float."""
        return self.white_score / 2.0 if self.white_score is not None else None
