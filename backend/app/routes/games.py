"""
API routes for game management (OGS Go games).

Provides endpoints for:
- Listing active games
- Getting game details with board state
- Linking/unlinking games to conversations
- Getting ephemeral board state for conversations
"""
import logging
import uuid
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.models import Conversation, Message, MessageRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/games", tags=["games"])


# =============================================================================
# Request/Response Models
# =============================================================================

class GameResponse(BaseModel):
    """Response model for a game."""
    game_id: int
    opponent_username: str
    our_color: str
    board_size: int
    time_control: str
    phase: str
    our_turn: bool
    move_count: int
    captures: dict
    conversation_id: Optional[str] = None
    metadata: dict = {}


class GameDetailResponse(GameResponse):
    """Detailed game response including board state."""
    board_ascii: str
    game_context: str  # Full context for LLM


class LinkGameRequest(BaseModel):
    """Request to link a game to a conversation."""
    conversation_id: Optional[str] = None  # If None, create new conversation


class LinkGameResponse(BaseModel):
    """Response after linking a game."""
    game_id: int
    conversation_id: str
    created_new_conversation: bool


class ConversationBoardState(BaseModel):
    """Ephemeral board state for a conversation."""
    game_id: int
    board_ascii: str
    our_color: str
    our_turn: bool
    move_count: int
    opponent_username: str


# =============================================================================
# Helper Functions
# =============================================================================

def _get_ogs_service():
    """Get the OGS service, raising if not configured."""
    if not settings.ogs_enabled:
        raise HTTPException(
            status_code=503,
            detail="OGS integration is not enabled"
        )

    from app.services.ogs_service import ogs_service
    return ogs_service


# =============================================================================
# Game Listing and Details
# =============================================================================

@router.get("/", response_model=List[GameResponse])
async def list_games(
    entity_id: Optional[str] = Query(None, description="Filter by entity ID")
):
    """
    List all active OGS games.

    If entity_id is provided, only games for that entity are returned.
    Currently, all games belong to the configured OGS entity.

    Games are discovered via socket.io events (yourMove, gameStarted notifications).
    """
    ogs_service = _get_ogs_service()

    # Check if filtering by entity matches our configured entity
    if entity_id and entity_id != settings.ogs_entity_id:
        return []

    # Get games from cache (populated by socket.io events)
    games = ogs_service.get_active_games()

    return [
        GameResponse(
            game_id=game.game_id,
            opponent_username=game.opponent_username,
            our_color=game.our_color,
            board_size=game.board_size,
            time_control=game.time_control,
            phase=game.phase,
            our_turn=game.our_turn,
            move_count=len(game.moves),
            captures=game.captures,
            conversation_id=game.conversation_id,
            metadata=game.metadata,
        )
        for game in games
    ]


@router.get("/{game_id}", response_model=GameDetailResponse)
async def get_game(game_id: int):
    """
    Get detailed information about a specific game.

    Includes ASCII board representation and full game context.
    Game data is populated via socket.io events.
    """
    ogs_service = _get_ogs_service()

    game = ogs_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found in cache")

    return GameDetailResponse(
        game_id=game.game_id,
        opponent_username=game.opponent_username,
        our_color=game.our_color,
        board_size=game.board_size,
        time_control=game.time_control,
        phase=game.phase,
        our_turn=game.our_turn,
        move_count=len(game.moves),
        captures=game.captures,
        conversation_id=game.conversation_id,
        metadata=game.metadata,
        board_ascii=ogs_service.board_to_ascii(game),
        game_context=ogs_service.format_game_context(game),
    )


# =============================================================================
# Game-Conversation Linking
# =============================================================================

@router.post("/{game_id}/link", response_model=LinkGameResponse)
async def link_game(
    game_id: int,
    request: LinkGameRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Link an OGS game to a conversation.

    If conversation_id is provided, links to that existing conversation.
    If conversation_id is None, creates a new conversation for the game.
    """
    ogs_service = _get_ogs_service()

    # Get the game
    game = ogs_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    created_new = False

    if request.conversation_id:
        # Link to existing conversation
        result = await db.execute(
            select(Conversation).where(Conversation.id == request.conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Check if it's already linked to something else
        if conversation.external_link_type and conversation.external_link_id != str(game_id):
            raise HTTPException(
                status_code=400,
                detail="Conversation is already linked to another external resource"
            )

        conversation_id = conversation.id
    else:
        # Create new conversation for the game
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title=f"Go Game vs {game.opponent_username}",
            entity_id=settings.ogs_entity_id,
            external_link_type="ogs_game",
            external_link_id=str(game_id),
            external_link_metadata={
                "opponent": game.opponent_username,
                "our_color": game.our_color,
                "board_size": game.board_size,
            },
        )
        db.add(conversation)
        await db.commit()
        conversation_id = conversation.id
        created_new = True

        logger.info(f"Created conversation {conversation_id} for game {game_id}")

    # Link in OGS service
    success = await ogs_service.link_game_to_conversation(game_id, conversation_id)
    if not success and not created_new:
        raise HTTPException(status_code=500, detail="Failed to link game")

    return LinkGameResponse(
        game_id=game_id,
        conversation_id=conversation_id,
        created_new_conversation=created_new,
    )


@router.delete("/{game_id}/link")
async def unlink_game(
    game_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Unlink an OGS game from its conversation.

    The conversation is preserved but the external link is removed.
    """
    ogs_service = _get_ogs_service()

    game = ogs_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if not game.conversation_id:
        raise HTTPException(status_code=400, detail="Game is not linked to a conversation")

    # Update conversation
    result = await db.execute(
        select(Conversation).where(Conversation.id == game.conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if conversation:
        conversation.external_link_type = None
        conversation.external_link_id = None
        conversation.external_link_metadata = None
        await db.commit()

    # Update OGS service cache
    game.conversation_id = None

    return {"status": "unlinked", "game_id": game_id}


@router.get("/{game_id}/conversation")
async def get_game_conversation(
    game_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Get the conversation linked to a game.

    Returns conversation details if linked, 404 if not linked.
    """
    ogs_service = _get_ogs_service()

    game = ogs_service.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if not game.conversation_id:
        raise HTTPException(status_code=404, detail="Game is not linked to a conversation")

    result = await db.execute(
        select(Conversation).where(Conversation.id == game.conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Linked conversation not found")

    return {
        "conversation_id": conversation.id,
        "title": conversation.title,
        "entity_id": conversation.entity_id,
        "created_at": conversation.created_at.isoformat(),
        "external_link_metadata": conversation.external_link_metadata,
    }


# =============================================================================
# Ephemeral Board State for Conversations
# =============================================================================

@router.get("/conversation/{conversation_id}/board", response_model=ConversationBoardState)
async def get_conversation_board_state(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get the current board state for a conversation linked to a game.

    This is ephemeral - fetched fresh from OGS, not stored.
    Used for injecting current game state into conversation context.
    """
    ogs_service = _get_ogs_service()

    # Get conversation
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.external_link_type != "ogs_game":
        raise HTTPException(status_code=400, detail="Conversation is not linked to an OGS game")

    game_id = int(conversation.external_link_id)
    game = ogs_service.get_game(game_id)

    if not game:
        raise HTTPException(status_code=404, detail="Linked game not found")

    return ConversationBoardState(
        game_id=game.game_id,
        board_ascii=ogs_service.board_to_ascii(game),
        our_color=game.our_color,
        our_turn=game.our_turn,
        move_count=len(game.moves),
        opponent_username=game.opponent_username,
    )
