"""
Go Tools - Tool definitions for Go game interaction.

These tools allow AI entities to interact with Go games during conversations,
making moves, passing, resigning, and viewing the board state.

Tools are registered via register_go_tools() called from services/__init__.py.
"""

import logging
from typing import Optional

from app.services.tool_service import ToolCategory, ToolService
from app.services.go_service import go_service
from app.config import settings

logger = logging.getLogger(__name__)


# Track the current game context
# These get set by the session manager before tool execution
_current_game_id: Optional[str] = None
_current_db_session = None


def set_go_game_context(game_id: Optional[str], db_session) -> None:
    """Set the Go game context for the current tool execution."""
    global _current_game_id, _current_db_session
    _current_game_id = game_id
    _current_db_session = db_session
    logger.debug(f"Go tools: game context set to '{game_id}'")


def get_current_game_id() -> Optional[str]:
    """Get the current game ID for tool execution."""
    return _current_game_id


async def _get_game_from_db(game_id: str):
    """Helper to get a game from the database."""
    if _current_db_session is None:
        return None

    from sqlalchemy import select
    from app.models import GoGame

    result = await _current_db_session.execute(
        select(GoGame).where(GoGame.id == game_id)
    )
    return result.scalar_one_or_none()


async def go_get_board(game_id: Optional[str] = None) -> str:
    """
    Get the current board state of a Go game.

    Args:
        game_id: The ID of the game to view. If not provided, uses the current context game.

    Returns:
        ASCII representation of the board with game status information.
    """
    gid = game_id or _current_game_id
    if not gid:
        return "Error: No game ID provided and no game in current context"

    game = await _get_game_from_db(gid)
    if not game:
        return f"Error: Game '{gid}' not found"

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

    # Build status info
    lines = [
        f"Game: {game.id[:8]}...",
        f"Board: {game.board_size}x{game.board_size}",
        f"Status: {game.game_status.value}",
        f"Current player: {game.current_player.value}",
        f"Move count: {game.move_count}",
        f"Captures - Black: {game.black_captures}, White: {game.white_captures}",
        "",
        ascii_board,
        "",
        "Legend: X=Black, O=White, ()=Last move, *=Ko point",
    ]

    if game.ko_point:
        ko_parts = game.ko_point.split(",")
        ko_coord = go_service.format_coordinate(
            int(ko_parts[0]), int(ko_parts[1]), game.board_size
        )
        lines.append(f"Ko point at: {ko_coord}")

    return "\n".join(lines)


async def go_make_move(coordinate: str, game_id: Optional[str] = None) -> str:
    """
    Make a move in a Go game.

    Args:
        coordinate: The move coordinate in standard notation (e.g., 'D4', 'Q16')
        game_id: The ID of the game. If not provided, uses the current context game.

    Returns:
        Result of the move including the updated board state.
    """
    gid = game_id or _current_game_id
    if not gid:
        return "Error: No game ID provided and no game in current context"

    game = await _get_game_from_db(gid)
    if not game:
        return f"Error: Game '{gid}' not found"

    from app.models import GameStatus, StoneColor

    if game.game_status != GameStatus.IN_PROGRESS:
        return f"Error: Game is not in progress (status: {game.game_status.value})"

    # Parse coordinate
    coord = go_service.parse_coordinate_input(coordinate, game.board_size)
    if coord is None:
        return f"Error: Invalid coordinate '{coordinate}'"

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
        return f"Error: {move_result['error']}"

    # Update game state
    game.board_state = move_result["board_state"]
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
    captures = move_result["captures"]
    if captures > 0:
        if game.current_player == StoneColor.BLACK:
            game.black_captures += captures
        else:
            game.white_captures += captures

    # Record who played
    played_as = game.current_player.value

    # Switch player
    game.current_player = StoneColor.WHITE if game.current_player == StoneColor.BLACK else StoneColor.BLACK

    # Commit changes
    await _current_db_session.commit()
    await _current_db_session.refresh(game)

    # Return result with board
    result_lines = [
        f"Move played: {coordinate} ({played_as})",
    ]
    if captures > 0:
        result_lines.append(f"Captures: {captures}")
    result_lines.append(f"Next player: {game.current_player.value}")
    result_lines.append("")
    result_lines.append(await go_get_board(gid))

    return "\n".join(result_lines)


async def go_pass(game_id: Optional[str] = None) -> str:
    """
    Pass your turn in a Go game.

    Two consecutive passes end the game.

    Args:
        game_id: The ID of the game. If not provided, uses the current context game.

    Returns:
        Result of the pass including whether the game ended.
    """
    gid = game_id or _current_game_id
    if not gid:
        return "Error: No game ID provided and no game in current context"

    game = await _get_game_from_db(gid)
    if not game:
        return f"Error: Game '{gid}' not found"

    from app.models import GameStatus, StoneColor

    if game.game_status != GameStatus.IN_PROGRESS:
        return f"Error: Game is not in progress (status: {game.game_status.value})"

    # Record the pass
    passed_as = game.current_player.value
    game.move_history = go_service.add_pass_to_history(
        game.move_history,
        game.current_player.value
    )
    game.consecutive_passes += 1
    game.ko_point = None  # Ko is reset on pass

    # Check for game end
    game_ended = False
    if game.consecutive_passes >= 2:
        game.game_status = GameStatus.FINISHED_PASS
        game_ended = True

    # Switch player
    game.current_player = StoneColor.WHITE if game.current_player == StoneColor.BLACK else StoneColor.BLACK

    await _current_db_session.commit()
    await _current_db_session.refresh(game)

    result_lines = [f"{passed_as} passed"]

    if game_ended:
        result_lines.append("")
        result_lines.append("*** GAME ENDED - Both players passed ***")
        result_lines.append("The game should now be scored.")
    else:
        result_lines.append(f"Next player: {game.current_player.value}")
        result_lines.append(f"Consecutive passes: {game.consecutive_passes}")

    return "\n".join(result_lines)


async def go_resign(game_id: Optional[str] = None) -> str:
    """
    Resign from a Go game. The opponent wins.

    Args:
        game_id: The ID of the game. If not provided, uses the current context game.

    Returns:
        Result of the resignation.
    """
    gid = game_id or _current_game_id
    if not gid:
        return "Error: No game ID provided and no game in current context"

    game = await _get_game_from_db(gid)
    if not game:
        return f"Error: Game '{gid}' not found"

    from app.models import GameStatus, StoneColor

    if game.game_status != GameStatus.IN_PROGRESS:
        return f"Error: Game is not in progress (status: {game.game_status.value})"

    # Current player resigns
    resigned_as = game.current_player.value
    game.game_status = GameStatus.FINISHED_RESIGNATION
    game.resignation_by = resigned_as
    game.winner = "white" if game.current_player == StoneColor.BLACK else "black"

    await _current_db_session.commit()
    await _current_db_session.refresh(game)

    return f"{resigned_as} resigned. {game.winner} wins!"


async def go_get_moves(game_id: Optional[str] = None) -> str:
    """
    Get the move history of a Go game.

    Args:
        game_id: The ID of the game. If not provided, uses the current context game.

    Returns:
        List of all moves played in the game.
    """
    gid = game_id or _current_game_id
    if not gid:
        return "Error: No game ID provided and no game in current context"

    game = await _get_game_from_db(gid)
    if not game:
        return f"Error: Game '{gid}' not found"

    if not game.move_history:
        return "No moves played yet."

    moves = go_service.parse_sgf_history(game.move_history)

    lines = [f"Move history ({len(moves)} moves):"]
    for i, move in enumerate(moves, 1):
        player = move["player"].capitalize()
        if move["pass"]:
            lines.append(f"{i}. {player}: Pass")
        else:
            coord = go_service.format_coordinate(
                move["row"], move["col"], game.board_size
            )
            lines.append(f"{i}. {player}: {coord}")

    return "\n".join(lines)


async def go_analyze_position(game_id: Optional[str] = None) -> str:
    """
    Analyze the current position showing territory estimation and game status.

    Args:
        game_id: The ID of the game. If not provided, uses the current context game.

    Returns:
        Analysis of the current position.
    """
    gid = game_id or _current_game_id
    if not gid:
        return "Error: No game ID provided and no game in current context"

    game = await _get_game_from_db(gid)
    if not game:
        return f"Error: Game '{gid}' not found"

    # Count territory
    black_territory, white_territory, _, _ = go_service.count_territory(game.board_state)
    black_stones, white_stones = go_service.count_stones(game.board_state)

    # Calculate estimated scores
    komi = game.get_komi_float()

    if game.scoring_method.value == "japanese":
        black_est = black_territory + game.black_captures
        white_est = white_territory + game.white_captures + komi
        method = "Japanese (territory + captures)"
    else:
        black_est = black_territory + black_stones
        white_est = white_territory + white_stones + komi
        method = "Chinese (territory + stones)"

    lines = [
        "Position Analysis",
        "=" * 40,
        "",
        f"Scoring method: {method}",
        f"Komi: {komi}",
        "",
        "Territory estimation:",
        f"  Black: {black_territory}",
        f"  White: {white_territory}",
        "",
        "Stones on board:",
        f"  Black: {black_stones}",
        f"  White: {white_stones}",
        "",
        "Captures:",
        f"  By Black: {game.black_captures}",
        f"  By White: {game.white_captures}",
        "",
        "Estimated score:",
        f"  Black: {black_est}",
        f"  White: {white_est}",
        "",
    ]

    if black_est > white_est:
        lines.append(f"Black leads by ~{black_est - white_est:.1f} points")
    elif white_est > black_est:
        lines.append(f"White leads by ~{white_est - black_est:.1f} points")
    else:
        lines.append("Score is approximately even")

    return "\n".join(lines)


def register_go_tools(tool_service: ToolService) -> None:
    """Register all Go game tools with the tool service."""

    if not settings.go_tools_enabled:
        logger.info("Go game tools disabled (GO_TOOLS_ENABLED=false)")
        return

    logger.info("Registering Go game tools")

    # go_get_board
    tool_service.register_tool(
        name="go_get_board",
        description=(
            "Get the current board state of a Go game. "
            "Shows an ASCII representation of the board with coordinates, "
            "marks the last move and ko point, and displays game status information."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "description": "The ID of the game to view. Optional if a game is in context."
                }
            },
            "required": []
        },
        executor=go_get_board,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # go_make_move
    tool_service.register_tool(
        name="go_make_move",
        description=(
            "Make a move in a Go game. "
            "Specify the coordinate using standard Go notation (e.g., 'D4', 'Q16'). "
            "Columns are A-T (skipping I), rows are 1-19 from bottom."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "coordinate": {
                    "type": "string",
                    "description": "The move coordinate in standard notation (e.g., 'D4', 'Q16')"
                },
                "game_id": {
                    "type": "string",
                    "description": "The ID of the game. Optional if a game is in context."
                }
            },
            "required": ["coordinate"]
        },
        executor=go_make_move,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # go_pass
    tool_service.register_tool(
        name="go_pass",
        description=(
            "Pass your turn in a Go game. "
            "Two consecutive passes end the game and trigger scoring."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "description": "The ID of the game. Optional if a game is in context."
                }
            },
            "required": []
        },
        executor=go_pass,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # go_resign
    tool_service.register_tool(
        name="go_resign",
        description=(
            "Resign from a Go game. The opponent wins immediately."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "description": "The ID of the game. Optional if a game is in context."
                }
            },
            "required": []
        },
        executor=go_resign,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # go_get_moves
    tool_service.register_tool(
        name="go_get_moves",
        description=(
            "Get the move history of a Go game. "
            "Shows all moves played with their coordinates."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "description": "The ID of the game. Optional if a game is in context."
                }
            },
            "required": []
        },
        executor=go_get_moves,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    # go_analyze_position
    tool_service.register_tool(
        name="go_analyze_position",
        description=(
            "Analyze the current position of a Go game. "
            "Shows territory estimation, stone counts, captures, and score estimate."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "description": "The ID of the game. Optional if a game is in context."
                }
            },
            "required": []
        },
        executor=go_analyze_position,
        category=ToolCategory.UTILITY,
        enabled=True,
    )

    logger.info("Go tools registered: go_get_board, go_make_move, go_pass, go_resign, go_get_moves, go_analyze_position")
