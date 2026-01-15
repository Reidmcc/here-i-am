"""
Go game service for managing Go game logic and state.

Implements standard Go rules including:
- Stone placement and capture
- Ko rule (superko not implemented)
- Suicide rule
- Territory scoring (Japanese) and area scoring (Chinese)
- SGF move history tracking
"""

import logging
from typing import Dict, Any, List, Optional, Tuple, Set
from dataclasses import dataclass
from copy import deepcopy

logger = logging.getLogger(__name__)

# Constants for board state
EMPTY = 0
BLACK = 1
WHITE = 2

# Coordinate conversion for SGF format (a-s for 19x19)
SGF_COORDS = "abcdefghijklmnopqrs"


@dataclass
class MoveResult:
    """Result of attempting a move."""
    success: bool
    error: Optional[str] = None
    captures: int = 0
    ko_point: Optional[Tuple[int, int]] = None
    board_state: Optional[List[List[int]]] = None


@dataclass
class ScoreResult:
    """Result of scoring a game."""
    black_territory: int
    white_territory: int
    black_captures: int
    white_captures: int
    black_stones: int  # For area scoring
    white_stones: int  # For area scoring
    komi: float
    black_score: float
    white_score: float
    winner: str  # "black", "white", or "draw"


class GoGameService:
    """Service for managing Go game logic and state."""

    def __init__(self):
        logger.info("GoGameService initialized")

    def create_empty_board(self, size: int) -> List[List[int]]:
        """Create an empty board of the given size."""
        if size not in (9, 13, 19):
            raise ValueError(f"Invalid board size: {size}. Must be 9, 13, or 19.")
        return [[EMPTY for _ in range(size)] for _ in range(size)]

    def get_stone(self, board: List[List[int]], row: int, col: int) -> int:
        """Get the stone at a position (0=empty, 1=black, 2=white)."""
        size = len(board)
        if 0 <= row < size and 0 <= col < size:
            return board[row][col]
        return -1  # Out of bounds

    def set_stone(self, board: List[List[int]], row: int, col: int, color: int) -> bool:
        """Set a stone at a position. Returns True if successful."""
        size = len(board)
        if 0 <= row < size and 0 <= col < size:
            board[row][col] = color
            return True
        return False

    def get_neighbors(self, size: int, row: int, col: int) -> List[Tuple[int, int]]:
        """Get orthogonal neighbors of a position."""
        neighbors = []
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = row + dr, col + dc
            if 0 <= nr < size and 0 <= nc < size:
                neighbors.append((nr, nc))
        return neighbors

    def find_group(self, board: List[List[int]], row: int, col: int) -> Tuple[Set[Tuple[int, int]], Set[Tuple[int, int]]]:
        """
        Find all stones connected to the stone at (row, col) and their liberties.

        Returns:
            Tuple of (group_stones, liberties) as sets of (row, col) tuples
        """
        size = len(board)
        color = board[row][col]
        if color == EMPTY:
            return set(), set()

        group = set()
        liberties = set()
        to_check = [(row, col)]

        while to_check:
            r, c = to_check.pop()
            if (r, c) in group:
                continue
            group.add((r, c))

            for nr, nc in self.get_neighbors(size, r, c):
                neighbor_color = board[nr][nc]
                if neighbor_color == EMPTY:
                    liberties.add((nr, nc))
                elif neighbor_color == color and (nr, nc) not in group:
                    to_check.append((nr, nc))

        return group, liberties

    def remove_captured_stones(self, board: List[List[int]], row: int, col: int) -> List[Tuple[int, int]]:
        """
        Remove a group of stones if it has no liberties.

        Returns list of captured stone positions.
        """
        group, liberties = self.find_group(board, row, col)
        if not liberties:
            for r, c in group:
                board[r][c] = EMPTY
            return list(group)
        return []

    def check_captures(self, board: List[List[int]], row: int, col: int, player_color: int) -> Tuple[List[Tuple[int, int]], Optional[Tuple[int, int]]]:
        """
        Check and remove opponent captures after placing a stone.

        Returns:
            Tuple of (captured_stones, ko_point)
            ko_point is set if exactly one stone was captured and the capturing
            stone has exactly one liberty (potential ko)
        """
        size = len(board)
        opponent = WHITE if player_color == BLACK else BLACK
        captured = []
        ko_point = None

        # Check each neighbor for opponent groups with no liberties
        for nr, nc in self.get_neighbors(size, row, col):
            if board[nr][nc] == opponent:
                removed = self.remove_captured_stones(board, nr, nc)
                captured.extend(removed)

        # Check for ko: exactly one capture, capturing stone has one liberty
        if len(captured) == 1:
            _, liberties = self.find_group(board, row, col)
            if len(liberties) == 1:
                ko_point = captured[0]

        return captured, ko_point

    def is_suicide(self, board: List[List[int]], row: int, col: int, color: int) -> bool:
        """
        Check if placing a stone would be suicide (no liberties after placement).

        Note: This should be called AFTER checking captures, as a move that
        captures opponent stones is not suicide even if the group would
        otherwise have no liberties.
        """
        _, liberties = self.find_group(board, row, col)
        return len(liberties) == 0

    def validate_move(
        self,
        board: List[List[int]],
        row: int,
        col: int,
        color: int,
        ko_point: Optional[Tuple[int, int]] = None
    ) -> MoveResult:
        """
        Validate and execute a move.

        Args:
            board: Current board state (will be modified if move is valid)
            row: Row to place stone
            col: Column to place stone
            color: Stone color (BLACK=1, WHITE=2)
            ko_point: Position forbidden by ko rule

        Returns:
            MoveResult with success status and updated board state
        """
        size = len(board)

        # Check bounds
        if not (0 <= row < size and 0 <= col < size):
            return MoveResult(success=False, error="Position out of bounds")

        # Check if position is occupied
        if board[row][col] != EMPTY:
            return MoveResult(success=False, error="Position is occupied")

        # Check ko rule
        if ko_point and (row, col) == ko_point:
            return MoveResult(success=False, error="Ko rule violation")

        # Make a copy for validation
        test_board = deepcopy(board)
        test_board[row][col] = color

        # Check captures first
        captured, new_ko = self.check_captures(test_board, row, col, color)

        # Check for suicide (only invalid if no captures occurred)
        if not captured and self.is_suicide(test_board, row, col, color):
            return MoveResult(success=False, error="Suicide move (no liberties)")

        # Move is valid - update the actual board
        board[row][col] = color
        self.check_captures(board, row, col, color)

        return MoveResult(
            success=True,
            captures=len(captured),
            ko_point=new_ko,
            board_state=board
        )

    def make_move(
        self,
        board: List[List[int]],
        row: int,
        col: int,
        current_player: str,
        ko_point: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        High-level move function using string player colors.

        Args:
            board: Current board state
            row: Row (0-indexed)
            col: Column (0-indexed)
            current_player: "black" or "white"
            ko_point: Ko point as "row,col" string or None

        Returns:
            Dict with success, error, captures, new_ko_point, board_state
        """
        color = BLACK if current_player == "black" else WHITE
        ko_tuple = None
        if ko_point:
            parts = ko_point.split(",")
            ko_tuple = (int(parts[0]), int(parts[1]))

        result = self.validate_move(board, row, col, color, ko_tuple)

        if not result.success:
            return {
                "success": False,
                "error": result.error
            }

        new_ko = None
        if result.ko_point:
            new_ko = f"{result.ko_point[0]},{result.ko_point[1]}"

        return {
            "success": True,
            "captures": result.captures,
            "ko_point": new_ko,
            "board_state": board
        }

    def coord_to_sgf(self, row: int, col: int) -> str:
        """Convert row, col to SGF coordinate (e.g., 'pd')."""
        return SGF_COORDS[col] + SGF_COORDS[row]

    def sgf_to_coord(self, sgf: str) -> Tuple[int, int]:
        """Convert SGF coordinate to row, col."""
        col = SGF_COORDS.index(sgf[0])
        row = SGF_COORDS.index(sgf[1])
        return row, col

    def add_move_to_history(self, history: str, player: str, row: int, col: int) -> str:
        """Add a move to SGF history."""
        color = "B" if player == "black" else "W"
        coord = self.coord_to_sgf(row, col)
        return history + f";{color}[{coord}]"

    def add_pass_to_history(self, history: str, player: str) -> str:
        """Add a pass to SGF history."""
        color = "B" if player == "black" else "W"
        return history + f";{color}[]"

    def parse_sgf_history(self, history: str) -> List[Dict[str, Any]]:
        """
        Parse SGF history into list of moves.

        Returns list of dicts with 'player', 'row', 'col', 'pass' fields.
        """
        moves = []
        if not history:
            return moves

        # Split by semicolon and filter empty
        parts = [p for p in history.split(";") if p]

        for part in parts:
            if not part:
                continue

            # Parse move like "B[pd]" or "W[]" for pass
            player = "black" if part[0] == "B" else "white"
            coord_start = part.find("[")
            coord_end = part.find("]")

            if coord_start == -1 or coord_end == -1:
                continue

            coord = part[coord_start + 1:coord_end]
            if coord:
                row, col = self.sgf_to_coord(coord)
                moves.append({"player": player, "row": row, "col": col, "pass": False})
            else:
                moves.append({"player": player, "row": None, "col": None, "pass": True})

        return moves

    def board_to_ascii(self, board: List[List[int]], last_move: Optional[Tuple[int, int]] = None, ko_point: Optional[Tuple[int, int]] = None) -> str:
        """
        Convert board to ASCII representation for AI consumption.

        Uses:
        - '.' for empty
        - 'X' for black
        - 'O' for white
        - '(' ')' around last move
        - '*' for ko point

        Includes coordinates (A-T for columns, 1-19 for rows).
        """
        size = len(board)
        lines = []

        # Column headers (skip 'I' in traditional Go notation)
        col_labels = "ABCDEFGHJKLMNOPQRST"[:size]
        lines.append("   " + " ".join(col_labels))

        for row in range(size):
            row_num = size - row  # Go boards are numbered from bottom
            row_str = f"{row_num:2d} "

            for col in range(size):
                stone = board[row][col]

                # Determine character
                if stone == EMPTY:
                    char = "."
                elif stone == BLACK:
                    char = "X"
                else:
                    char = "O"

                # Mark last move
                if last_move and (row, col) == last_move:
                    char = f"({char})"
                # Mark ko point
                elif ko_point and (row, col) == ko_point:
                    char = "*"
                else:
                    char = f" {char}"

                row_str += char

            row_str += f" {row_num}"
            lines.append(row_str)

        lines.append("   " + " ".join(col_labels))

        return "\n".join(lines)

    def count_territory(self, board: List[List[int]]) -> Tuple[int, int, Set[Tuple[int, int]], Set[Tuple[int, int]]]:
        """
        Count territory for each player using flood fill.

        An empty region belongs to a player if it's surrounded only by that
        player's stones. Neutral regions (touching both colors) count for neither.

        Returns:
            Tuple of (black_territory, white_territory, black_region, white_region)
        """
        size = len(board)
        visited = set()
        black_territory = 0
        white_territory = 0
        black_region = set()
        white_region = set()

        for row in range(size):
            for col in range(size):
                if (row, col) in visited or board[row][col] != EMPTY:
                    continue

                # Flood fill to find connected empty region
                region = set()
                borders_black = False
                borders_white = False
                to_check = [(row, col)]

                while to_check:
                    r, c = to_check.pop()
                    if (r, c) in region:
                        continue

                    if board[r][c] == BLACK:
                        borders_black = True
                        continue
                    elif board[r][c] == WHITE:
                        borders_white = True
                        continue

                    region.add((r, c))
                    visited.add((r, c))

                    for nr, nc in self.get_neighbors(size, r, c):
                        if (nr, nc) not in region:
                            to_check.append((nr, nc))

                # Assign territory if only one color borders
                if borders_black and not borders_white:
                    black_territory += len(region)
                    black_region.update(region)
                elif borders_white and not borders_black:
                    white_territory += len(region)
                    white_region.update(region)

        return black_territory, white_territory, black_region, white_region

    def count_stones(self, board: List[List[int]]) -> Tuple[int, int]:
        """Count stones of each color on the board."""
        black_stones = 0
        white_stones = 0
        for row in board:
            for cell in row:
                if cell == BLACK:
                    black_stones += 1
                elif cell == WHITE:
                    white_stones += 1
        return black_stones, white_stones

    def calculate_score(
        self,
        board: List[List[int]],
        black_captures: int,
        white_captures: int,
        komi: float,
        scoring_method: str = "japanese"
    ) -> ScoreResult:
        """
        Calculate final score.

        Japanese scoring: territory + captures + komi
        Chinese scoring: territory + stones + komi (captures implicit)

        Args:
            board: Final board state
            black_captures: Stones captured by black
            white_captures: Stones captured by white
            komi: Points added to white's score
            scoring_method: "japanese" or "chinese"

        Returns:
            ScoreResult with all scoring details
        """
        black_territory, white_territory, _, _ = self.count_territory(board)
        black_stones, white_stones = self.count_stones(board)

        if scoring_method == "japanese":
            # Territory + captures
            black_score = black_territory + black_captures
            white_score = white_territory + white_captures + komi
        else:
            # Chinese: territory + stones on board
            black_score = black_territory + black_stones
            white_score = white_territory + white_stones + komi

        if black_score > white_score:
            winner = "black"
        elif white_score > black_score:
            winner = "white"
        else:
            winner = "draw"

        return ScoreResult(
            black_territory=black_territory,
            white_territory=white_territory,
            black_captures=black_captures,
            white_captures=white_captures,
            black_stones=black_stones,
            white_stones=white_stones,
            komi=komi,
            black_score=black_score,
            white_score=white_score,
            winner=winner
        )

    def get_next_player(self, current: str) -> str:
        """Get the next player's color."""
        return "white" if current == "black" else "black"

    def parse_coordinate_input(self, coord_str: str, board_size: int) -> Optional[Tuple[int, int]]:
        """
        Parse a coordinate string like 'D4' or 'd4' to (row, col).

        Uses standard Go notation where columns are A-T (skipping I)
        and rows are 1-19 from bottom.

        Returns None if invalid.
        """
        coord_str = coord_str.strip().upper()
        if len(coord_str) < 2:
            return None

        # Column letter (A-T, skipping I)
        col_letter = coord_str[0]
        col_labels = "ABCDEFGHJKLMNOPQRST"[:board_size]
        if col_letter not in col_labels:
            return None
        col = col_labels.index(col_letter)

        # Row number
        try:
            row_num = int(coord_str[1:])
            if row_num < 1 or row_num > board_size:
                return None
            # Convert from 1-indexed bottom-origin to 0-indexed top-origin
            row = board_size - row_num
        except ValueError:
            return None

        return row, col

    def format_coordinate(self, row: int, col: int, board_size: int) -> str:
        """Format (row, col) as standard Go notation like 'D4'."""
        col_labels = "ABCDEFGHJKLMNOPQRST"[:board_size]
        row_num = board_size - row
        return f"{col_labels[col]}{row_num}"


# Singleton instance
go_service = GoGameService()
