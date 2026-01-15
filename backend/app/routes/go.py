"""
API routes for Go game management.

Provides endpoints for:
- Game lifecycle (create, retrieve, list, delete)
- Gameplay (moves, passes, resignation)
- Scoring
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel, Field

from app.database import get_db
from app.models import Conversation, GoGame, GameStatus, ScoringMethod, StoneColor
from app.services.go_service import go_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/go", tags=["go"])


# Request/Response Models

class GoGameCreate(BaseModel):
    """Request to create a new Go game."""
    conversation_id: str
    board_size: int = Field(default=19, description="Board size: 9, 13, or 19")
    scoring_method: str = Field(default="japanese", description="'japanese' or 'chinese'")
    komi: float = Field(default=6.5, description="Komi points for white")
    black_entity_id: Optional[str] = Field(default=None, description="Entity playing black")
    white_entity_id: Optional[str] = Field(default=None, description="Entity playing white")


class GoGameResponse(BaseModel):
    """Response with Go game details."""
    id: str
    conversation_id: str
    created_at: str
    board_size: int
    scoring_method: str
    komi: float
    board_state: List[List[int]]
    board_ascii: str
    current_player: str
    game_status: str
    move_count: int
    move_history: str
    black_captures: int
    white_captures: int
    ko_point: Optional[str]
    consecutive_passes: int
    winner: Optional[str]
    black_score: Optional[float]
    white_score: Optional[float]
    resignation_by: Optional[str]
    black_entity_id: Optional[str]
    white_entity_id: Optional[str]

    class Config:
        from_attributes = True


class MoveRequest(BaseModel):
    """Request to make a move."""
    coordinate: str = Field(description="Move coordinate like 'D4' or 'Q16'")


class MoveResponse(BaseModel):
    """Response after making a move."""
    success: bool
    error: Optional[str] = None
    captures: int = 0
    game: Optional[GoGameResponse] = None


class PassResponse(BaseModel):
    """Response after passing."""
    success: bool
    game_ended: bool = False
    game: Optional[GoGameResponse] = None


class ResignResponse(BaseModel):
    """Response after resignation."""
    success: bool
    winner: str
    game: Optional[GoGameResponse] = None


class ScoreRequest(BaseModel):
    """Request to score a game."""
    force: bool = Field(default=False, description="Force scoring even if game not ended")


class ScoreResponse(BaseModel):
    """Response with game score."""
    success: bool
    black_territory: int
    white_territory: int
    black_captures: int
    white_captures: int
    black_stones: int
    white_stones: int
    komi: float
    black_score: float
    white_score: float
    winner: str
    game: Optional[GoGameResponse] = None


# Helper functions

def game_to_response(game: GoGame) -> GoGameResponse:
    """Convert GoGame model to response."""
    # Parse ko_point for board ASCII
    ko_tuple = None
    if game.ko_point:
        parts = game.ko_point.split(",")
        ko_tuple = (int(parts[0]), int(parts[1]))

    # Get last move for highlighting
    last_move = None
    if game.move_history:
        moves = go_service.parse_sgf_history(game.move_history)
        if moves and not moves[-1]["pass"]:
            last_move = (moves[-1]["row"], moves[-1]["col"])

    board_ascii = go_service.board_to_ascii(
        game.board_state,
        last_move=last_move,
        ko_point=ko_tuple
    )

    return GoGameResponse(
        id=game.id,
        conversation_id=game.conversation_id,
        created_at=game.created_at.isoformat(),
        board_size=game.board_size,
        scoring_method=game.scoring_method.value,
        komi=game.get_komi_float(),
        board_state=game.board_state,
        board_ascii=board_ascii,
        current_player=game.current_player.value,
        game_status=game.game_status.value,
        move_count=game.move_count,
        move_history=game.move_history,
        black_captures=game.black_captures,
        white_captures=game.white_captures,
        ko_point=game.ko_point,
        consecutive_passes=game.consecutive_passes,
        winner=game.winner,
        black_score=game.get_black_score_float(),
        white_score=game.get_white_score_float(),
        resignation_by=game.resignation_by,
        black_entity_id=game.black_entity_id,
        white_entity_id=game.white_entity_id,
    )


# Endpoints

@router.post("/games", response_model=GoGameResponse)
async def create_game(data: GoGameCreate, db: AsyncSession = Depends(get_db)):
    """
    Create a new Go game linked to a conversation.

    The game starts with an empty board and black to play.
    """
    # Validate board size
    if data.board_size not in (9, 13, 19):
        raise HTTPException(status_code=400, detail="Board size must be 9, 13, or 19")

    # Validate scoring method
    try:
        scoring = ScoringMethod(data.scoring_method)
    except ValueError:
        raise HTTPException(status_code=400, detail="Scoring method must be 'japanese' or 'chinese'")

    # Verify conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == data.conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail=f"Conversation '{data.conversation_id}' not found")

    # Create empty board
    board = go_service.create_empty_board(data.board_size)

    # Create game
    game = GoGame(
        conversation_id=data.conversation_id,
        board_size=data.board_size,
        scoring_method=scoring,
        board_state=board,
        current_player=StoneColor.BLACK,
        game_status=GameStatus.IN_PROGRESS,
        move_history="",
        move_count=0,
        black_captures=0,
        white_captures=0,
        consecutive_passes=0,
        black_entity_id=data.black_entity_id,
        white_entity_id=data.white_entity_id,
    )
    game.set_komi_float(data.komi)

    db.add(game)
    await db.commit()
    await db.refresh(game)

    logger.info(f"Created Go game {game.id} for conversation {data.conversation_id}")
    return game_to_response(game)


@router.get("/games", response_model=List[GoGameResponse])
async def list_games(
    conversation_id: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    List Go games, optionally filtered by conversation or status.
    """
    query = select(GoGame)

    if conversation_id:
        query = query.where(GoGame.conversation_id == conversation_id)
    if status:
        try:
            game_status = GameStatus(status)
            query = query.where(GoGame.game_status == game_status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    query = query.order_by(GoGame.created_at.desc())
    result = await db.execute(query)
    games = result.scalars().all()

    return [game_to_response(game) for game in games]


@router.get("/games/{game_id}", response_model=GoGameResponse)
async def get_game(game_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific Go game by ID."""
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")

    return game_to_response(game)


@router.delete("/games/{game_id}")
async def delete_game(game_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a Go game."""
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")

    await db.delete(game)
    await db.commit()

    logger.info(f"Deleted Go game {game_id}")
    return {"success": True, "message": f"Game {game_id} deleted"}


@router.post("/games/{game_id}/move", response_model=MoveResponse)
async def make_move(game_id: str, data: MoveRequest, db: AsyncSession = Depends(get_db)):
    """
    Make a move in a Go game.

    Coordinate format: standard Go notation like 'D4', 'Q16', etc.
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")

    if game.game_status != GameStatus.IN_PROGRESS:
        return MoveResponse(
            success=False,
            error=f"Game is not in progress (status: {game.game_status.value})"
        )

    # Parse coordinate
    coord = go_service.parse_coordinate_input(data.coordinate, game.board_size)
    if coord is None:
        return MoveResponse(
            success=False,
            error=f"Invalid coordinate: {data.coordinate}"
        )

    row, col = coord

    # Attempt the move
    move_result = go_service.make_move(
        game.board_state,
        row,
        col,
        game.current_player.value,
        game.ko_point
    )

    if not move_result["success"]:
        return MoveResponse(success=False, error=move_result["error"])

    # Update game state
    game.board_state = move_result["board_state"]
    flag_modified(game, "board_state")  # Ensure SQLAlchemy detects the JSON mutation
    game.ko_point = move_result.get("ko_point")
    game.move_history = go_service.add_move_to_history(
        game.move_history,
        game.current_player.value,
        row,
        col
    )
    game.move_count += 1
    game.consecutive_passes = 0

    # Update captures
    if move_result["captures"] > 0:
        if game.current_player == StoneColor.BLACK:
            game.black_captures += move_result["captures"]
        else:
            game.white_captures += move_result["captures"]

    # Switch player
    game.current_player = StoneColor.WHITE if game.current_player == StoneColor.BLACK else StoneColor.BLACK

    await db.commit()
    await db.refresh(game)

    logger.info(f"Move {data.coordinate} in game {game_id}, captures: {move_result['captures']}")
    return MoveResponse(
        success=True,
        captures=move_result["captures"],
        game=game_to_response(game)
    )


@router.post("/games/{game_id}/pass", response_model=PassResponse)
async def pass_turn(game_id: str, db: AsyncSession = Depends(get_db)):
    """
    Pass the turn. Two consecutive passes end the game.
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")

    if game.game_status != GameStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail=f"Game is not in progress (status: {game.game_status.value})"
        )

    # Record the pass
    game.move_history = go_service.add_pass_to_history(
        game.move_history,
        game.current_player.value
    )
    game.consecutive_passes += 1
    game.ko_point = None  # Ko is reset on pass

    # Check for game end (two consecutive passes)
    game_ended = False
    if game.consecutive_passes >= 2:
        game.game_status = GameStatus.FINISHED_PASS
        game_ended = True
        logger.info(f"Game {game_id} ended by consecutive passes")

    # Switch player
    game.current_player = StoneColor.WHITE if game.current_player == StoneColor.BLACK else StoneColor.BLACK

    await db.commit()
    await db.refresh(game)

    return PassResponse(
        success=True,
        game_ended=game_ended,
        game=game_to_response(game)
    )


@router.post("/games/{game_id}/resign", response_model=ResignResponse)
async def resign(game_id: str, db: AsyncSession = Depends(get_db)):
    """
    Resign the game. The current player loses.
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")

    if game.game_status != GameStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail=f"Game is not in progress (status: {game.game_status.value})"
        )

    # Current player resigns, opponent wins
    game.game_status = GameStatus.FINISHED_RESIGNATION
    game.resignation_by = game.current_player.value
    game.winner = "white" if game.current_player == StoneColor.BLACK else "black"

    await db.commit()
    await db.refresh(game)

    logger.info(f"Game {game_id}: {game.resignation_by} resigned, {game.winner} wins")
    return ResignResponse(
        success=True,
        winner=game.winner,
        game=game_to_response(game)
    )


@router.post("/games/{game_id}/score", response_model=ScoreResponse)
async def score_game(game_id: str, data: ScoreRequest, db: AsyncSession = Depends(get_db)):
    """
    Calculate and record the final score.

    By default, only works if the game has ended. Use force=true to score
    a game still in progress (for analysis).
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")

    # Check if game can be scored
    if game.game_status == GameStatus.IN_PROGRESS and not data.force:
        raise HTTPException(
            status_code=400,
            detail="Game is still in progress. Use force=true to score anyway."
        )

    # Calculate score
    score_result = go_service.calculate_score(
        game.board_state,
        game.black_captures,
        game.white_captures,
        game.get_komi_float(),
        game.scoring_method.value
    )

    # Update game with final score (only if game has ended)
    if game.game_status != GameStatus.IN_PROGRESS:
        game.black_score = int(score_result.black_score * 2)
        game.white_score = int(score_result.white_score * 2)
        game.winner = score_result.winner
        if game.game_status == GameStatus.FINISHED_PASS:
            game.game_status = GameStatus.FINISHED_SCORED

        await db.commit()
        await db.refresh(game)

    return ScoreResponse(
        success=True,
        black_territory=score_result.black_territory,
        white_territory=score_result.white_territory,
        black_captures=score_result.black_captures,
        white_captures=score_result.white_captures,
        black_stones=score_result.black_stones,
        white_stones=score_result.white_stones,
        komi=score_result.komi,
        black_score=score_result.black_score,
        white_score=score_result.white_score,
        winner=score_result.winner,
        game=game_to_response(game)
    )


@router.get("/games/{game_id}/ascii")
async def get_board_ascii(game_id: str, db: AsyncSession = Depends(get_db)):
    """
    Get the current board state as ASCII art.

    Useful for AI entities to visualize the board.
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")

    # Parse ko_point
    ko_tuple = None
    if game.ko_point:
        parts = game.ko_point.split(",")
        ko_tuple = (int(parts[0]), int(parts[1]))

    # Get last move
    last_move = None
    if game.move_history:
        moves = go_service.parse_sgf_history(game.move_history)
        if moves and not moves[-1]["pass"]:
            last_move = (moves[-1]["row"], moves[-1]["col"])

    ascii_board = go_service.board_to_ascii(
        game.board_state,
        last_move=last_move,
        ko_point=ko_tuple
    )

    return {
        "game_id": game_id,
        "board_size": game.board_size,
        "current_player": game.current_player.value,
        "move_count": game.move_count,
        "ascii": ascii_board
    }


@router.get("/games/{game_id}/history")
async def get_move_history(game_id: str, db: AsyncSession = Depends(get_db)):
    """
    Get the move history of a game.

    Returns moves in both SGF format and parsed list format.
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")

    moves = go_service.parse_sgf_history(game.move_history)

    # Add formatted coordinates
    for move in moves:
        if not move["pass"]:
            move["coordinate"] = go_service.format_coordinate(
                move["row"],
                move["col"],
                game.board_size
            )
        else:
            move["coordinate"] = "pass"

    return {
        "game_id": game_id,
        "sgf": game.move_history,
        "move_count": game.move_count,
        "moves": moves
    }
