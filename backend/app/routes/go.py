"""
Go Game API Routes

Provides endpoints for:
- Game lifecycle (create, get, list, delete)
- Gameplay (moves, passes, resignation)
- AI turn triggering
- Scoring
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.database import get_db
from app.models import Conversation, GoGame, GameStatus, StoneColor
from app.services.go_service import go_service, BLACK, WHITE

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/go", tags=["go"])


# === Request/Response Models ===

class CreateGameRequest(BaseModel):
    """Request to create a new Go game."""
    conversation_id: str
    board_size: int = Field(default=19, description="Board size: 9, 13, or 19")
    komi: float = Field(default=6.5, description="Komi (compensation points for white)")
    entity_color: str = Field(default="black", description="Color for the AI entity: 'black' or 'white'")
    entity_id: Optional[str] = Field(default=None, description="Entity ID to play (defaults to conversation entity)")


class GameResponse(BaseModel):
    """Response containing game state."""
    id: str
    conversation_id: str
    created_at: str
    board_size: int
    komi: float
    board_state: List[List[int]]
    board_ascii: str
    current_player: str
    status: str
    move_count: int
    move_history: str
    black_captures: int
    white_captures: int
    ko_point: Optional[str]
    consecutive_passes: int
    winner: Optional[str]
    black_score: Optional[float]
    white_score: Optional[float]
    result_reason: Optional[str]
    black_entity_id: Optional[str]
    white_entity_id: Optional[str]
    is_entity_turn: bool

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
    game: Optional[GameResponse] = None


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
    game: Optional[GameResponse] = None


# === Helper Functions ===

def game_to_response(game: GoGame) -> GameResponse:
    """Convert GoGame model to response."""
    # Parse ko_point for ASCII display
    ko_tuple = None
    if game.ko_point:
        parts = game.ko_point.split(",")
        ko_tuple = (int(parts[0]), int(parts[1]))
    
    # Get last move for highlighting
    last_move = None
    if game.move_history:
        moves = go_service.parse_move_history(game.move_history)
        if moves and not moves[-1]["is_pass"]:
            last_move = (moves[-1]["row"], moves[-1]["col"])
    
    board_ascii = go_service.board_to_ascii(
        game.board_state,
        last_move=last_move,
        ko_point=ko_tuple,
        move_count=game.move_count,
        current_player=game.current_player.value,
        black_captures=game.black_captures,
        white_captures=game.white_captures
    )
    
    return GameResponse(
        id=game.id,
        conversation_id=game.conversation_id,
        created_at=game.created_at.isoformat(),
        board_size=game.board_size,
        komi=game.komi,
        board_state=game.board_state,
        board_ascii=board_ascii,
        current_player=game.current_player.value,
        status=game.status.value,
        move_count=game.move_count,
        move_history=game.move_history,
        black_captures=game.black_captures,
        white_captures=game.white_captures,
        ko_point=game.ko_point,
        consecutive_passes=game.consecutive_passes,
        winner=game.winner,
        black_score=game.black_score,
        white_score=game.white_score,
        result_reason=game.result_reason,
        black_entity_id=game.black_entity_id,
        white_entity_id=game.white_entity_id,
        is_entity_turn=game.is_entity_turn
    )


# === Endpoints ===

@router.post("/games", response_model=GameResponse)
async def create_game(data: CreateGameRequest, db: AsyncSession = Depends(get_db)):
    """
    Create a new Go game linked to a conversation.
    
    The game starts with an empty board and black to play.
    The entity_color parameter determines which color the AI plays.
    """
    # Validate board size
    if data.board_size not in (9, 13, 19):
        raise HTTPException(status_code=400, detail="Board size must be 9, 13, or 19")
    
    # Validate entity color
    if data.entity_color not in ("black", "white"):
        raise HTTPException(status_code=400, detail="entity_color must be 'black' or 'white'")
    
    # Verify conversation exists and get entity_id
    result = await db.execute(
        select(Conversation).where(Conversation.id == data.conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail=f"Conversation '{data.conversation_id}' not found")
    
    # Use provided entity_id or fall back to conversation's entity
    entity_id = data.entity_id or conversation.entity_id
    
    # Create empty board
    board = go_service.create_empty_board(data.board_size)
    
    # Assign entity to chosen color
    black_entity = entity_id if data.entity_color == "black" else None
    white_entity = entity_id if data.entity_color == "white" else None
    
    # Create game
    game = GoGame(
        conversation_id=data.conversation_id,
        board_size=data.board_size,
        komi=data.komi,
        board_state=board,
        current_player=StoneColor.BLACK,
        status=GameStatus.ACTIVE,
        move_history="",
        move_count=0,
        black_captures=0,
        white_captures=0,
        consecutive_passes=0,
        black_entity_id=black_entity,
        white_entity_id=white_entity,
    )
    
    db.add(game)
    await db.commit()
    await db.refresh(game)
    
    logger.info(f"Created Go game {game.id} ({data.board_size}x{data.board_size}) for conversation {data.conversation_id}")
    return game_to_response(game)


@router.get("/games", response_model=List[GameResponse])
async def list_games(
    conversation_id: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """List Go games, optionally filtered by conversation or status."""
    query = select(GoGame)
    
    if conversation_id:
        query = query.where(GoGame.conversation_id == conversation_id)
    if status:
        try:
            game_status = GameStatus(status)
            query = query.where(GoGame.status == game_status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    
    query = query.order_by(GoGame.created_at.desc())
    result = await db.execute(query)
    games = result.scalars().all()
    
    return [game_to_response(game) for game in games]


@router.get("/games/{game_id}", response_model=GameResponse)
async def get_game(game_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific Go game by ID."""
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()
    
    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
    
    return game_to_response(game)


@router.get("/conversation/{conversation_id}/active", response_model=Optional[GameResponse])
async def get_active_game_for_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    """Get the active Go game for a conversation, if any."""
    result = await db.execute(
        select(GoGame)
        .where(GoGame.conversation_id == conversation_id)
        .where(GoGame.status == GameStatus.ACTIVE)
        .order_by(GoGame.created_at.desc())
        .limit(1)
    )
    game = result.scalar_one_or_none()
    
    if not game:
        return None
    
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
    Make a move in a Go game (human player).
    
    Coordinate format: standard Go notation like 'D4', 'Q16', etc.
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()
    
    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
    
    if game.status != GameStatus.ACTIVE:
        return MoveResponse(
            success=False,
            error=f"Game is not active (status: {game.status.value})"
        )
    
    # Parse coordinate
    coord = go_service.parse_coordinate(data.coordinate, game.board_size)
    if coord is None:
        return MoveResponse(success=False, error=f"Invalid coordinate: {data.coordinate}")
    
    row, col = coord
    
    # Parse ko_point
    ko_tuple = None
    if game.ko_point:
        parts = game.ko_point.split(",")
        ko_tuple = (int(parts[0]), int(parts[1]))
    
    # Get current player color as int
    color = BLACK if game.current_player == StoneColor.BLACK else WHITE
    
    # Execute move
    move_result = go_service.execute_move(game.board_state, row, col, color, ko_tuple)
    
    if not move_result.success:
        return MoveResponse(success=False, error=move_result.error)
    
    # Update game state
    game.board_state = move_result.new_board
    game.ko_point = f"{move_result.ko_point[0]},{move_result.ko_point[1]}" if move_result.ko_point else None
    game.move_history = go_service.add_move_to_history(
        game.move_history,
        game.current_player.value,
        row, col
    )
    game.move_count += 1
    game.consecutive_passes = 0
    
    # Update captures
    if move_result.captures > 0:
        if game.current_player == StoneColor.BLACK:
            game.black_captures += move_result.captures
        else:
            game.white_captures += move_result.captures
    
    # Switch player
    game.current_player = StoneColor.WHITE if game.current_player == StoneColor.BLACK else StoneColor.BLACK
    
    await db.commit()
    await db.refresh(game)
    
    logger.info(f"Move {data.coordinate} in game {game_id}, captures: {move_result.captures}")
    return MoveResponse(
        success=True,
        captures=move_result.captures,
        game=game_to_response(game)
    )


@router.post("/games/{game_id}/pass", response_model=MoveResponse)
async def pass_turn(game_id: str, db: AsyncSession = Depends(get_db)):
    """
    Pass the turn. Two consecutive passes end the game.
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()
    
    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
    
    if game.status != GameStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Game is not active (status: {game.status.value})"
        )
    
    # Record the pass
    game.move_history = go_service.add_pass_to_history(
        game.move_history,
        game.current_player.value
    )
    game.move_count += 1
    game.consecutive_passes += 1
    game.ko_point = None  # Ko resets on pass
    
    # Check for game end
    game_ended = False
    if game.consecutive_passes >= 2:
        game.status = GameStatus.SCORING
        game_ended = True
        logger.info(f"Game {game_id} ended - both players passed")
    
    # Switch player
    game.current_player = StoneColor.WHITE if game.current_player == StoneColor.BLACK else StoneColor.BLACK
    
    await db.commit()
    await db.refresh(game)
    
    return MoveResponse(
        success=True,
        captures=0,
        game=game_to_response(game)
    )


@router.post("/games/{game_id}/resign", response_model=MoveResponse)
async def resign(game_id: str, db: AsyncSession = Depends(get_db)):
    """
    Resign from the game. The current player loses.
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()
    
    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
    
    if game.status != GameStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Game is not active (status: {game.status.value})"
        )
    
    # Current player resigns, opponent wins
    game.status = GameStatus.FINISHED
    game.result_reason = "resignation"
    game.winner = "white" if game.current_player == StoneColor.BLACK else "black"
    
    await db.commit()
    await db.refresh(game)
    
    logger.info(f"Game {game_id}: {game.current_player.value} resigned, {game.winner} wins")
    return MoveResponse(
        success=True,
        captures=0,
        game=game_to_response(game)
    )


@router.post("/games/{game_id}/score", response_model=ScoreResponse)
async def score_game(game_id: str, finalize: bool = True, db: AsyncSession = Depends(get_db)):
    """
    Calculate the score of a game.
    
    Args:
        finalize: If True, mark the game as finished with the score.
                  If False, just return the score without changing game state.
    """
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()
    
    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
    
    # Calculate score
    score = go_service.calculate_score(
        game.board_state,
        game.black_captures,
        game.white_captures,
        game.komi
    )
    
    # Optionally finalize
    if finalize and game.status != GameStatus.FINISHED:
        game.status = GameStatus.FINISHED
        game.result_reason = "score"
        game.black_score = score.black_score
        game.white_score = score.white_score
        game.winner = score.winner
        
        await db.commit()
        await db.refresh(game)
    
    return ScoreResponse(
        success=True,
        black_territory=score.black_territory,
        white_territory=score.white_territory,
        black_captures=score.black_captures,
        white_captures=score.white_captures,
        black_stones=score.black_stones,
        white_stones=score.white_stones,
        komi=score.komi,
        black_score=score.black_score,
        white_score=score.white_score,
        winner=score.winner,
        game=game_to_response(game)
    )


@router.get("/games/{game_id}/history")
async def get_move_history(game_id: str, db: AsyncSession = Depends(get_db)):
    """Get the move history of a game in human-readable format."""
    result = await db.execute(select(GoGame).where(GoGame.id == game_id))
    game = result.scalar_one_or_none()
    
    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
    
    moves = go_service.parse_move_history(game.move_history)
    
    # Update coordinate format for actual board size
    for move in moves:
        if not move["is_pass"] and move["row"] is not None:
            move["coordinate"] = go_service.format_coordinate(
                move["row"], move["col"], game.board_size
            )
    
    return {
        "game_id": game_id,
        "sgf": game.move_history,
        "move_count": game.move_count,
        "moves": moves
    }
