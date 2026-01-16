"""
Go Game Context Integration

Handles injecting Go game state into conversations and parsing AI responses
for move commands. This bridges the game mechanics with the conversation flow.

The dual-channel design:
- Channel 1 (Game): Board state injected ephemerally, moves parsed from response
- Channel 2 (Conversation): Normal message flow, commentary preserved
"""

import logging
import re
from typing import Optional, Tuple, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GoGame, GameStatus, StoneColor
from app.services.go_service import go_service, BLACK, WHITE

logger = logging.getLogger(__name__)


async def get_active_game_for_conversation(
    conversation_id: str,
    db: AsyncSession
) -> Optional[GoGame]:
    """Get the active Go game for a conversation, if any."""
    result = await db.execute(
        select(GoGame)
        .where(GoGame.conversation_id == conversation_id)
        .where(GoGame.status == GameStatus.ACTIVE)
        .order_by(GoGame.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def build_game_context_block(game: GoGame) -> str:
    """
    Build the game state context block to inject into conversation.
    
    This is injected ephemerally - it's not stored in conversation history,
    but shows the AI the current board state each turn.
    """
    # Parse ko_point for display
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
    
    # Determine AI's color and role
    entity_color = "Black" if game.black_entity_id else "White" if game.white_entity_id else None
    is_my_turn = game.is_entity_turn
    
    # Build ASCII board
    board_ascii = go_service.board_to_ascii(
        game.board_state,
        last_move=last_move,
        ko_point=ko_tuple,
        move_count=game.move_count,
        current_player=game.current_player.value,
        black_captures=game.black_captures,
        white_captures=game.white_captures
    )
    
    # Get recent moves for context
    recent_moves = ""
    if game.move_history:
        moves = go_service.parse_move_history(game.move_history)
        if moves:
            # Show last 6 moves
            recent = moves[-6:]
            recent_strs = []
            for i, m in enumerate(recent):
                move_num = game.move_count - len(recent) + i + 1
                player = "B" if m["player"] == "black" else "W"
                coord = m["coordinate"]
                recent_strs.append(f"{move_num}.{player} {coord}")
            recent_moves = " → ".join(recent_strs)
    
    # Build the context block
    lines = [
        f"[GO GAME - Game {game.id[:8]}]",
        "",
        board_ascii,
        "",
    ]
    
    if recent_moves:
        lines.append(f"Recent: {recent_moves}")
        lines.append("")
    
    # Instructions based on whose turn it is
    if is_my_turn:
        lines.extend([
            f"You are playing {entity_color}. It's your turn.",
            "",
            "To make a move, include in your response:",
            "  MOVE: <coordinate>  (e.g., MOVE: Q4)",
            "  MOVE: pass          (to pass your turn)",
            "  MOVE: resign        (to resign the game)",
            "",
            "You may include any commentary or thoughts alongside your move.",
            "[/GO GAME]"
        ])
    else:
        opponent_color = "White" if entity_color == "Black" else "Black"
        lines.extend([
            f"You are playing {entity_color}. Waiting for {opponent_color} to play.",
            "",
            "You can discuss the game, but cannot make a move until it's your turn.",
            "[/GO GAME]"
        ])
    
    return "\n".join(lines)


def inject_game_context(user_message: str, game_context: str) -> str:
    """
    Inject game context into the user message.
    
    The game context is prepended to the message so the AI sees the board
    state before reading the human's message.
    """
    return f"{game_context}\n\n{user_message}"


def parse_move_from_response(response: str) -> Optional[Dict[str, Any]]:
    """
    Parse a move command from the AI's response.
    
    Looks for patterns like:
    - MOVE: Q4
    - MOVE: pass
    - MOVE: resign
    
    Returns dict with 'type' and optionally 'coordinate', or None if no move found.
    """
    # Pattern for move commands (case-insensitive)
    move_pattern = r'MOVE:\s*(\S+)'
    match = re.search(move_pattern, response, re.IGNORECASE)
    
    if not match:
        return None
    
    move_text = match.group(1).lower().strip()
    
    if move_text == "pass":
        return {"type": "pass"}
    elif move_text == "resign":
        return {"type": "resign"}
    else:
        # It's a coordinate
        return {"type": "move", "coordinate": move_text.upper()}


async def execute_ai_move(
    game: GoGame,
    move_info: Dict[str, Any],
    db: AsyncSession
) -> Tuple[bool, str]:
    """
    Execute the AI's move on the game.
    
    Returns (success, message).
    """
    if game.status != GameStatus.ACTIVE:
        return False, f"Game is not active (status: {game.status.value})"
    
    if not game.is_entity_turn:
        return False, "It's not the AI's turn"
    
    move_type = move_info.get("type")
    
    if move_type == "pass":
        # Record the pass
        game.move_history = go_service.add_pass_to_history(
            game.move_history,
            game.current_player.value
        )
        game.move_count += 1
        game.consecutive_passes += 1
        game.ko_point = None
        
        # Check for game end
        if game.consecutive_passes >= 2:
            game.status = GameStatus.SCORING
            await db.commit()
            return True, "Passed. Both players have passed - game is now in scoring phase."
        
        # Switch player
        game.current_player = StoneColor.WHITE if game.current_player == StoneColor.BLACK else StoneColor.BLACK
        await db.commit()
        return True, "Passed."
    
    elif move_type == "resign":
        game.status = GameStatus.FINISHED
        game.result_reason = "resignation"
        game.winner = "white" if game.current_player == StoneColor.BLACK else "black"
        await db.commit()
        return True, f"Resigned. {game.winner.capitalize()} wins."
    
    elif move_type == "move":
        coordinate = move_info.get("coordinate")
        if not coordinate:
            return False, "No coordinate provided"
        
        # Parse coordinate
        coord = go_service.parse_coordinate(coordinate, game.board_size)
        if coord is None:
            return False, f"Invalid coordinate: {coordinate}"
        
        row, col = coord
        
        # Parse existing ko_point
        ko_tuple = None
        if game.ko_point:
            parts = game.ko_point.split(",")
            ko_tuple = (int(parts[0]), int(parts[1]))
        
        # Get color
        color = BLACK if game.current_player == StoneColor.BLACK else WHITE
        
        # Execute move
        result = go_service.execute_move(game.board_state, row, col, color, ko_tuple)
        
        if not result.success:
            return False, f"Invalid move: {result.error}"
        
        # Update game state
        game.board_state = result.new_board
        game.ko_point = f"{result.ko_point[0]},{result.ko_point[1]}" if result.ko_point else None
        game.move_history = go_service.add_move_to_history(
            game.move_history,
            game.current_player.value,
            row, col
        )
        game.move_count += 1
        game.consecutive_passes = 0
        
        # Update captures
        if result.captures > 0:
            if game.current_player == StoneColor.BLACK:
                game.black_captures += result.captures
            else:
                game.white_captures += result.captures
        
        # Switch player
        game.current_player = StoneColor.WHITE if game.current_player == StoneColor.BLACK else StoneColor.BLACK
        
        await db.commit()
        
        capture_msg = f" (captured {result.captures})" if result.captures > 0 else ""
        return True, f"Played {coordinate}{capture_msg}."
    
    return False, f"Unknown move type: {move_type}"


def strip_move_command(response: str) -> str:
    """
    Remove the MOVE: command from the response for cleaner storage.
    
    The move command is functional, not conversational, so we strip it
    from what gets stored as the message content.
    """
    # Remove MOVE: lines but keep everything else
    lines = response.split('\n')
    filtered_lines = []
    for line in lines:
        # Skip lines that are just the move command
        stripped = line.strip()
        if re.match(r'^MOVE:\s*\S+\s*$', stripped, re.IGNORECASE):
            continue
        filtered_lines.append(line)
    
    result = '\n'.join(filtered_lines).strip()
    
    # If we stripped everything, return original (edge case protection)
    return result if result else response
