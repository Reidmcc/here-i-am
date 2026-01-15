"""
Go Game Service

Implements standard Go game rules:
- Stone placement with legality checking
- Capture logic (remove groups with no liberties)
- Ko rule (prevent immediate recapture)
- Suicide rule (prevent self-capture)
- Pass mechanism and game end detection
- Territory scoring

This is the game logic layer - database operations are in the routes.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple, Set
from dataclasses import dataclass
from copy import deepcopy

logger = logging.getLogger(__name__)

# Board cell values
EMPTY = 0
BLACK = 1
WHITE = 2

# Coordinate labels for standard Go notation (A-T, skipping I)
COL_LABELS = "ABCDEFGHJKLMNOPQRST"

# SGF coordinate letters (a-s for 19x19)
SGF_COORDS = "abcdefghijklmnopqrs"


@dataclass
class MoveResult:
    """Result of attempting a move."""
    success: bool
    error: Optional[str] = None
    captures: int = 0
    ko_point: Optional[Tuple[int, int]] = None
    new_board: Optional[List[List[int]]] = None


@dataclass
class ScoreResult:
    """Result of scoring a game."""
    black_territory: int
    white_territory: int
    black_captures: int
    white_captures: int
    black_stones: int
    white_stones: int
    komi: float
    black_score: float
    white_score: float
    winner: str  # "black" or "white"


class GoGameService:
    """Service for Go game logic and board operations."""
    
    def __init__(self):
        logger.info("GoGameService initialized")
    
    # === Board Creation ===
    
    def create_empty_board(self, size: int) -> List[List[int]]:
        """Create an empty board of the given size."""
        if size not in (9, 13, 19):
            raise ValueError(f"Invalid board size: {size}. Must be 9, 13, or 19.")
        return [[EMPTY for _ in range(size)] for _ in range(size)]
    
    # === Basic Board Operations ===
    
    def get_neighbors(self, size: int, row: int, col: int) -> List[Tuple[int, int]]:
        """Get orthogonal neighbors of a position (up, down, left, right)."""
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
    
    # === Move Validation and Execution ===
    
    def is_valid_move(
        self,
        board: List[List[int]],
        row: int,
        col: int,
        color: int,
        ko_point: Optional[Tuple[int, int]] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a move is valid without executing it.
        
        Returns:
            (is_valid, error_message)
        """
        size = len(board)
        
        # Check bounds
        if not (0 <= row < size and 0 <= col < size):
            return False, "Position out of bounds"
        
        # Check if occupied
        if board[row][col] != EMPTY:
            return False, "Position is already occupied"
        
        # Check ko rule
        if ko_point and (row, col) == ko_point:
            return False, "Ko rule violation - cannot recapture immediately"
        
        # Test the move to check for suicide
        test_board = deepcopy(board)
        test_board[row][col] = color
        
        # Check for captures first
        opponent = WHITE if color == BLACK else BLACK
        captures_something = False
        for nr, nc in self.get_neighbors(size, row, col):
            if test_board[nr][nc] == opponent:
                _, liberties = self.find_group(test_board, nr, nc)
                if not liberties:
                    captures_something = True
                    break
        
        # If no captures, check if self has liberties (suicide check)
        if not captures_something:
            _, liberties = self.find_group(test_board, row, col)
            if not liberties:
                return False, "Suicide - move would leave your group with no liberties"
        
        return True, None
    
    def execute_move(
        self,
        board: List[List[int]],
        row: int,
        col: int,
        color: int,
        ko_point: Optional[Tuple[int, int]] = None
    ) -> MoveResult:
        """
        Execute a move on the board.
        
        Args:
            board: Current board state (will NOT be modified)
            row: Row to place stone (0-indexed from top)
            col: Column to place stone (0-indexed from left)
            color: BLACK (1) or WHITE (2)
            ko_point: Position forbidden by ko rule
        
        Returns:
            MoveResult with new board state if successful
        """
        # Validate first
        is_valid, error = self.is_valid_move(board, row, col, color, ko_point)
        if not is_valid:
            return MoveResult(success=False, error=error)
        
        # Execute the move
        size = len(board)
        new_board = deepcopy(board)
        new_board[row][col] = color
        
        # Remove captured stones
        opponent = WHITE if color == BLACK else BLACK
        captured_count = 0
        captured_positions = []
        
        for nr, nc in self.get_neighbors(size, row, col):
            if new_board[nr][nc] == opponent:
                group, liberties = self.find_group(new_board, nr, nc)
                if not liberties:
                    for gr, gc in group:
                        new_board[gr][gc] = EMPTY
                    captured_positions.extend(group)
                    captured_count += len(group)
        
        # Determine ko point: exactly one stone captured and capturing stone has one liberty
        new_ko = None
        if captured_count == 1:
            _, my_liberties = self.find_group(new_board, row, col)
            if len(my_liberties) == 1:
                new_ko = captured_positions[0]
        
        return MoveResult(
            success=True,
            captures=captured_count,
            ko_point=new_ko,
            new_board=new_board
        )
    
    # === Coordinate Conversion ===
    
    def parse_coordinate(self, coord_str: str, board_size: int) -> Optional[Tuple[int, int]]:
        """
        Parse a coordinate string like 'D4' or 'q16' to (row, col).
        
        Uses standard Go notation:
        - Columns: A-T (skipping I)
        - Rows: 1-19 from bottom
        
        Returns None if invalid.
        """
        coord_str = coord_str.strip().upper()
        if len(coord_str) < 2:
            return None
        
        # Parse column letter
        col_letter = coord_str[0]
        col_labels = COL_LABELS[:board_size]
        if col_letter not in col_labels:
            return None
        col = col_labels.index(col_letter)
        
        # Parse row number
        try:
            row_num = int(coord_str[1:])
            if row_num < 1 or row_num > board_size:
                return None
            # Convert: row 1 is at bottom, which is index (size-1)
            row = board_size - row_num
        except ValueError:
            return None
        
        return row, col
    
    def format_coordinate(self, row: int, col: int, board_size: int) -> str:
        """Format (row, col) as standard Go notation like 'D4'."""
        col_labels = COL_LABELS[:board_size]
        row_num = board_size - row  # Convert back to 1-indexed from bottom
        return f"{col_labels[col]}{row_num}"
    
    def coord_to_sgf(self, row: int, col: int) -> str:
        """Convert row, col to SGF coordinate (e.g., 'pd')."""
        return SGF_COORDS[col] + SGF_COORDS[row]
    
    def sgf_to_coord(self, sgf: str) -> Tuple[int, int]:
        """Convert SGF coordinate to row, col."""
        col = SGF_COORDS.index(sgf[0])
        row = SGF_COORDS.index(sgf[1])
        return row, col
    
    # === Move History (SGF format) ===
    
    def add_move_to_history(self, history: str, player: str, row: int, col: int) -> str:
        """Add a move to SGF history."""
        color = "B" if player == "black" else "W"
        coord = self.coord_to_sgf(row, col)
        return history + f";{color}[{coord}]"
    
    def add_pass_to_history(self, history: str, player: str) -> str:
        """Add a pass to SGF history."""
        color = "B" if player == "black" else "W"
        return history + f";{color}[]"
    
    def parse_move_history(self, history: str) -> List[Dict[str, Any]]:
        """
        Parse SGF history into list of moves.
        
        Returns list of dicts with 'player', 'row', 'col', 'is_pass' fields.
        """
        moves = []
        if not history:
            return moves
        
        parts = [p for p in history.split(";") if p]
        
        for part in parts:
            if not part:
                continue
            
            player = "black" if part[0] == "B" else "white"
            bracket_start = part.find("[")
            bracket_end = part.find("]")
            
            if bracket_start == -1 or bracket_end == -1:
                continue
            
            coord = part[bracket_start + 1:bracket_end]
            if coord:
                row, col = self.sgf_to_coord(coord)
                moves.append({
                    "player": player,
                    "row": row,
                    "col": col,
                    "is_pass": False,
                    "coordinate": self.format_coordinate(row, col, 19)  # Assume 19x19 for display
                })
            else:
                moves.append({
                    "player": player,
                    "row": None,
                    "col": None,
                    "is_pass": True,
                    "coordinate": "pass"
                })
        
        return moves
    
    # === Board Visualization ===
    
    def board_to_ascii(
        self,
        board: List[List[int]],
        last_move: Optional[Tuple[int, int]] = None,
        ko_point: Optional[Tuple[int, int]] = None,
        move_count: int = 0,
        current_player: str = "black",
        black_captures: int = 0,
        white_captures: int = 0
    ) -> str:
        """
        Convert board to ASCII representation for AI context.
        
        Format:
        - '.' for empty intersections
        - 'X' for black stones
        - 'O' for white stones
        - Marks last move with parentheses
        - Shows coordinates on all sides
        """
        size = len(board)
        col_labels = COL_LABELS[:size]
        lines = []
        
        # Header with game info
        lines.append(f"Board: {size}x{size} | Move: {move_count} | To play: {current_player.capitalize()}")
        lines.append(f"Captures - Black: {black_captures}, White: {white_captures}")
        if ko_point:
            ko_coord = self.format_coordinate(ko_point[0], ko_point[1], size)
            lines.append(f"Ko point: {ko_coord} (cannot play here)")
        lines.append("")
        
        # Column headers
        lines.append("   " + " ".join(col_labels))
        
        for row in range(size):
            row_num = size - row  # Go boards numbered from bottom
            row_chars = []
            
            for col in range(size):
                cell = board[row][col]
                
                if cell == EMPTY:
                    char = "."
                elif cell == BLACK:
                    char = "X"
                else:
                    char = "O"
                
                # Mark last move
                if last_move and (row, col) == last_move:
                    char = f"({char})"
                elif ko_point and (row, col) == ko_point:
                    char = " *"  # Ko point marker
                else:
                    char = f" {char}"
                
                row_chars.append(char)
            
            lines.append(f"{row_num:2d}" + "".join(row_chars) + f" {row_num}")
        
        # Column footers
        lines.append("   " + " ".join(col_labels))
        
        return "\n".join(lines)
    
    # === Scoring ===
    
    def count_territory(self, board: List[List[int]]) -> Tuple[int, int, Set[Tuple[int, int]], Set[Tuple[int, int]]]:
        """
        Count territory for each player using flood fill.
        
        An empty region belongs to a player if it's surrounded only by that
        player's stones. Neutral regions (touching both colors) count for neither.
        
        Returns:
            (black_territory, white_territory, black_region_points, white_region_points)
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
        komi: float
    ) -> ScoreResult:
        """
        Calculate final score using territory scoring (Japanese rules).
        
        Score = territory + captures + komi (for white)
        """
        black_territory, white_territory, _, _ = self.count_territory(board)
        black_stones, white_stones = self.count_stones(board)
        
        black_score = float(black_territory + black_captures)
        white_score = float(white_territory + white_captures + komi)
        
        winner = "black" if black_score > white_score else "white"
        
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
    
    # === Utility ===
    
    def get_opponent_color(self, color: str) -> str:
        """Get the opponent's color."""
        return "white" if color == "black" else "black"
    
    def color_to_int(self, color: str) -> int:
        """Convert color string to integer."""
        return BLACK if color == "black" else WHITE


# Singleton instance
go_service = GoGameService()
