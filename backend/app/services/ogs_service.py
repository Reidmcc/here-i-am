"""
OGS (Online-Go Server) Service for Go game integration.

This service handles:
- Socket.io communication with OGS for real-time game events
- Board state conversion to ASCII representation
- Move parsing from LLM responses
- Processing game events (your turn notifications)

All OGS communication is done via socket.io for reliability.
Game data is received via socket events and cached locally.

Authentication:
- API Key - Recommended. Generated from bot profile after moderator approval.
  Bot accounts must be flagged by OGS moderators.
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_maker
from app.models import Conversation, Message, MessageRole

logger = logging.getLogger(__name__)


@dataclass
class OGSGame:
    """Represents an active OGS game."""
    game_id: int
    opponent_username: str
    our_color: str  # "black" or "white"
    board_size: int
    time_control: str
    phase: str  # "play", "finished", "stone removal"
    our_turn: bool
    moves: List[Tuple[int, int]]  # List of (x, y) coordinates
    board_state: List[List[int]]  # 2D array: 0=empty, 1=black, 2=white
    captures: Dict[str, int]  # {"black": n, "white": m}
    time_left: Optional[Dict[str, Any]] = None
    conversation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class OGSService:
    """
    Service for interacting with OGS (Online-Go Server) via socket.io.

    All communication is done via socket.io for reliability and compatibility
    with OGS bot API keys (REST API doesn't work with bot API keys).

    Handles game state management and coordinating with the LLM for move generation.
    """

    # Column labels for coordinate conversion
    COLUMN_LABELS = "ABCDEFGHJKLMNOPQRST"  # Note: 'I' is skipped in Go notation

    def __init__(self):
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._user_id: Optional[int] = None
        self._active_games: Dict[int, OGSGame] = {}
        self._socket_client: Optional[Any] = None  # Socket.io client for game operations

    def set_socket_client(self, sio: Any) -> None:
        """Set the socket.io client for game operations."""
        self._socket_client = sio

    def update_game_from_socket(self, game_id: int, gamedata: Dict[str, Any]) -> Optional[OGSGame]:
        """
        Update a game's state from socket.io gamedata event.

        Called by the listener when gamedata is received via socket.
        """
        try:
            # Build game from socket data (slightly different format than REST API)
            game = self._parse_socket_gamedata(game_id, gamedata)
            if game:
                self._active_games[game_id] = game
                logger.debug(f"OGS: Updated game {game_id} from socket data")
            return game
        except Exception as e:
            logger.error(f"OGS: Error updating game from socket: {e}")
            return None

    def _parse_socket_gamedata(self, game_id: int, data: Dict[str, Any]) -> Optional[OGSGame]:
        """Parse gamedata from socket.io event into an OGSGame object."""
        try:
            black_player = data.get("black", {}) or data.get("players", {}).get("black", {})
            white_player = data.get("white", {}) or data.get("players", {}).get("white", {})

            # Determine our color
            black_id = black_player.get("id")
            white_id = white_player.get("id")

            if black_id == self._user_id:
                our_color = "black"
                opponent_username = white_player.get("username", "Unknown")
            else:
                our_color = "white"
                opponent_username = black_player.get("username", "Unknown")

            # Parse game state
            moves = self._parse_moves(data.get("moves", []))
            board_size = data.get("width", 19)
            board_state = self._build_board_state(moves, board_size)

            # Determine whose turn it is
            current_player = len(moves) % 2  # 0 = black, 1 = white
            our_turn = (current_player == 0 and our_color == "black") or \
                      (current_player == 1 and our_color == "white")

            # Get captures
            score = data.get("score", {})
            captures = {
                "black": score.get("black", {}).get("prisoners", 0) if isinstance(score.get("black"), dict) else 0,
                "white": score.get("white", {}).get("prisoners", 0) if isinstance(score.get("white"), dict) else 0,
            }

            # Time control info
            time_control = data.get("time_control", {})
            time_control_system = time_control.get("system", "unknown") if isinstance(time_control, dict) else "unknown"

            return OGSGame(
                game_id=game_id,
                opponent_username=opponent_username,
                our_color=our_color,
                board_size=board_size,
                time_control=time_control_system,
                phase=data.get("phase", "play"),
                our_turn=our_turn,
                moves=moves,
                board_state=board_state,
                captures=captures,
                time_left=data.get("clock"),
                metadata={
                    "name": data.get("game_name", ""),
                    "started": data.get("started"),
                    "rules": data.get("rules", "japanese"),
                    "komi": data.get("komi", 6.5),
                }
            )

        except Exception as e:
            logger.error(f"OGS: Error parsing socket gamedata: {e}")
            return None

    @property
    def is_configured(self) -> bool:
        """Check if OGS is properly configured with API key."""
        if not settings.ogs_enabled or not settings.ogs_entity_id:
            return False
        return bool(settings.ogs_api_key)

    # =========================================================================
    # Authentication
    # =========================================================================

    async def authenticate(self) -> bool:
        """
        Prepare API key authentication for socket.io.

        OGS bot API keys are designed for socket.io authentication.
        The actual verification happens during socket connection.
        Here we just store the key and mark as ready.

        Returns True if authentication succeeded.
        """
        if not self.is_configured:
            logger.warning("OGS: Not configured, skipping authentication")
            return False

        logger.info("OGS: Preparing API key authentication (will verify via socket)")

        # Store the API key for socket authentication
        self._access_token = settings.ogs_api_key
        # API keys don't expire (set far future expiry)
        self._token_expires_at = datetime.utcnow() + timedelta(days=365)

        logger.info("OGS: API key stored, will authenticate via socket connection")
        return True

    async def _ensure_authenticated(self) -> bool:
        """Ensure we have a valid access token."""
        if not self._access_token:
            return await self.authenticate()
        return True

    # =========================================================================
    # Game Management
    # =========================================================================

    def get_active_games(self) -> List[OGSGame]:
        """
        Get all active games from the cache.

        Games are discovered via socket.io notifications (yourMove, gameStarted).
        The cache is populated when we receive game events from OGS.

        Returns the list of cached active games.
        """
        games = list(self._active_games.values())
        logger.debug(f"OGS: Returning {len(games)} games from cache")
        return games

    def get_game(self, game_id: int) -> Optional[OGSGame]:
        """
        Get a specific game's current state from cache.

        Game data is populated via socket.io events (gamedata) when subscribed.
        If the game is not in cache, it means we haven't received data yet.
        Subscribe to the game via the listener to receive updates.

        Returns the cached game or None if not yet available.
        """
        return self._active_games.get(game_id)

    async def request_game_data(self, game_id: int) -> bool:
        """
        Request game data by subscribing to the game via socket.io.

        When subscribed, OGS will send gamedata which populates our cache.
        This is async - the data will arrive via the socket event handler.

        Returns True if the subscription request was sent.
        """
        if not self._socket_client:
            logger.error("OGS: Cannot request game data - no socket client available")
            return False

        try:
            await self._socket_client.emit("game/connect", {"game_id": game_id})
            logger.info(f"OGS: Subscribed to game {game_id} to receive gamedata")
            return True
        except Exception as e:
            logger.error(f"OGS: Error subscribing to game {game_id}: {e}")
            return False

    def _parse_moves(self, moves_data: List) -> List[Tuple[int, int]]:
        """Parse moves from OGS format to list of (x, y) coordinates."""
        moves = []
        for move in moves_data:
            if isinstance(move, list) and len(move) >= 2:
                x, y = move[0], move[1]
                moves.append((x, y))
            elif isinstance(move, dict):
                x = move.get("x", -1)
                y = move.get("y", -1)
                moves.append((x, y))
        return moves

    def _build_board_state(
        self,
        moves: List[Tuple[int, int]],
        board_size: int
    ) -> List[List[int]]:
        """
        Build the current board state from move history.

        This is a simplified version that doesn't handle captures.
        For a full implementation, you'd need to implement Go rules.
        """
        # 0 = empty, 1 = black, 2 = white
        board = [[0 for _ in range(board_size)] for _ in range(board_size)]

        for i, (x, y) in enumerate(moves):
            if x >= 0 and y >= 0 and x < board_size and y < board_size:
                color = 1 if i % 2 == 0 else 2  # Black moves first
                board[y][x] = color

        return board

    # =========================================================================
    # Board ASCII Representation
    # =========================================================================

    def board_to_ascii(self, game: OGSGame, include_coordinates: bool = True) -> str:
        """
        Convert a game's board state to ASCII representation.

        Uses:
        - . for empty intersections
        - X for black stones
        - O for white stones
        - + for star points (hoshi)

        Returns a string representation of the board.
        """
        board = game.board_state
        size = game.board_size
        lines = []

        # Star point positions for different board sizes
        star_points = self._get_star_points(size)

        if include_coordinates:
            # Column headers
            col_labels = self.COLUMN_LABELS[:size]
            lines.append("    " + " ".join(col_labels))

        for y in range(size):
            row = []
            for x in range(size):
                cell = board[y][x]
                if cell == 1:
                    row.append("X")  # Black
                elif cell == 2:
                    row.append("O")  # White
                elif (x, y) in star_points:
                    row.append("+")  # Star point
                else:
                    row.append(".")  # Empty

            row_str = " ".join(row)
            if include_coordinates:
                # Row numbers (Go uses 1-indexed, bottom-to-top traditionally)
                row_num = size - y
                lines.append(f"{row_num:2d}  {row_str}  {row_num}")

        if include_coordinates:
            lines.append("    " + " ".join(col_labels))

        return "\n".join(lines)

    def _get_star_points(self, size: int) -> set:
        """Get star point positions for a given board size."""
        if size == 19:
            points = [(3, 3), (3, 9), (3, 15),
                      (9, 3), (9, 9), (9, 15),
                      (15, 3), (15, 9), (15, 15)]
        elif size == 13:
            points = [(3, 3), (3, 9), (6, 6), (9, 3), (9, 9)]
        elif size == 9:
            points = [(2, 2), (2, 6), (4, 4), (6, 2), (6, 6)]
        else:
            points = []
        return set(points)

    def format_game_context(self, game: OGSGame) -> str:
        """
        Format complete game context for LLM prompt.

        Includes board state, game info, and move history.
        """
        lines = [
            f"=== GO GAME STATUS ===",
            f"Game ID: {game.game_id}",
            f"Opponent: {game.opponent_username}",
            f"You are playing: {game.our_color.upper()}",
            f"Board size: {game.board_size}x{game.board_size}",
            f"Rules: {game.metadata.get('rules', 'Japanese')} (Komi: {game.metadata.get('komi', 6.5)})",
            f"Move number: {len(game.moves)}",
            f"Captures - Black: {game.captures.get('black', 0)}, White: {game.captures.get('white', 0)}",
            "",
            "Current board position:",
            self.board_to_ascii(game),
            "",
        ]

        # Recent moves
        if game.moves:
            recent = game.moves[-5:] if len(game.moves) > 5 else game.moves
            recent_strs = []
            start_idx = len(game.moves) - len(recent) + 1
            for i, (x, y) in enumerate(recent):
                move_num = start_idx + i
                coord = self._coords_to_notation(x, y, game.board_size)
                color = "Black" if (move_num - 1) % 2 == 0 else "White"
                recent_strs.append(f"  {move_num}. {color}: {coord}")
            lines.append("Recent moves:")
            lines.extend(recent_strs)
            lines.append("")

        # Time info if available
        if game.time_left:
            lines.append("Time remaining (approximate):")
            # Format depends on time control type
            lines.append(f"  {game.time_left}")
            lines.append("")

        lines.append("Your move (respond with MOVE: <coordinate> like 'MOVE: D4' or 'MOVE: pass' or 'MOVE: resign'):")

        return "\n".join(lines)

    def _coords_to_notation(self, x: int, y: int, board_size: int) -> str:
        """Convert (x, y) coordinates to Go notation like 'D4'."""
        if x < 0 or y < 0:
            return "pass"
        col = self.COLUMN_LABELS[x] if x < len(self.COLUMN_LABELS) else "?"
        row = board_size - y  # Go rows are numbered from bottom
        return f"{col}{row}"

    def _notation_to_coords(self, notation: str, board_size: int) -> Tuple[int, int]:
        """Convert Go notation like 'D4' to (x, y) coordinates."""
        notation = notation.strip().upper()

        if notation in ("PASS", "-"):
            return (-1, -1)

        if len(notation) < 2:
            raise ValueError(f"Invalid notation: {notation}")

        col = notation[0]
        try:
            row = int(notation[1:])
        except ValueError:
            raise ValueError(f"Invalid notation: {notation}")

        if col not in self.COLUMN_LABELS:
            raise ValueError(f"Invalid column: {col}")

        x = self.COLUMN_LABELS.index(col)
        y = board_size - row

        if x < 0 or x >= board_size or y < 0 or y >= board_size:
            raise ValueError(f"Coordinates out of bounds: {notation}")

        return (x, y)

    # =========================================================================
    # Move Parsing and Submission
    # =========================================================================

    def parse_move_from_response(self, response_text: str, board_size: int = 19) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse a move and optional commentary from LLM response.

        Expected format: Response text containing "MOVE: <coordinate>" somewhere.
        The coordinate can be like "D4", "pass", or "resign".

        Returns: (move_notation, commentary)
        - move_notation: The parsed move (e.g., "D4", "pass", "resign") or None
        - commentary: Text before the MOVE command, if any
        """
        # Look for MOVE: pattern
        move_pattern = r'MOVE:\s*([A-Za-z]?\d+|pass|resign)'
        match = re.search(move_pattern, response_text, re.IGNORECASE)

        if not match:
            logger.warning(f"OGS: Could not parse move from response: {response_text[:200]}...")
            return (None, None)

        move = match.group(1).upper()

        # Validate the move
        if move not in ("PASS", "RESIGN"):
            try:
                self._notation_to_coords(move, board_size)
            except ValueError as e:
                logger.warning(f"OGS: Invalid move notation '{move}': {e}")
                return (None, None)

        # Extract commentary (text before MOVE:)
        commentary_end = match.start()
        commentary = response_text[:commentary_end].strip()

        # Clean up commentary
        if commentary:
            # Remove any trailing punctuation that might be awkward
            commentary = commentary.rstrip(":").strip()

        return (move, commentary if commentary else None)

    async def submit_move(self, game_id: int, move: str) -> bool:
        """
        Submit a move to OGS via socket.io.

        Args:
            game_id: The OGS game ID
            move: Move in notation format (e.g., "D4", "pass", "resign")

        Returns True if the move was accepted.
        """
        if not await self._ensure_authenticated():
            return False

        game = self._active_games.get(game_id)
        if not game:
            logger.error(f"OGS: Cannot submit move - game {game_id} not found in cache")
            return False

        move = move.upper()
        return await self._submit_move_socket(game_id, move, game)

    async def _submit_move_socket(self, game_id: int, move: str, game: OGSGame) -> bool:
        """Submit a move via socket.io."""
        if not self._socket_client:
            logger.error("OGS: Cannot submit move via socket - no socket client available")
            return False

        try:
            if move == "RESIGN":
                await self._socket_client.emit("game/resign", {"game_id": game_id})
                logger.info(f"OGS: Resign submitted via socket for game {game_id}")
            elif move == "PASS":
                await self._socket_client.emit("game/move", {
                    "game_id": game_id,
                    "move": ".."  # OGS pass notation
                })
                logger.info(f"OGS: Pass submitted via socket for game {game_id}")
            else:
                # Convert notation to OGS format
                x, y = self._notation_to_coords(move, game.board_size)
                # OGS socket uses simple [x, y] or "a1" style notation
                move_str = f"{self.COLUMN_LABELS[x].lower()}{game.board_size - y}"
                await self._socket_client.emit("game/move", {
                    "game_id": game_id,
                    "move": move_str
                })
                logger.info(f"OGS: Move {move} ({move_str}) submitted via socket for game {game_id}")

            return True

        except Exception as e:
            logger.error(f"OGS: Error submitting move via socket: {e}")
            return False

    async def send_game_chat(self, game_id: int, message: str, move_number: Optional[int] = None) -> bool:
        """
        Send a chat message to the game chat via socket.io.

        Args:
            game_id: The OGS game ID
            message: The chat message to send
            move_number: Optional move number to associate the chat with
        """
        if not self._socket_client:
            logger.error("OGS: Cannot send chat - no socket client available")
            return False

        try:
            # OGS game chat format
            chat_data = {
                "game_id": game_id,
                "body": message,
                "type": "main",  # main game chat
            }
            if move_number is not None:
                chat_data["move_number"] = move_number

            await self._socket_client.emit("game/chat", chat_data)
            logger.info(f"OGS: Sent chat to game {game_id}")
            return True
        except Exception as e:
            logger.error(f"OGS: Error sending chat via socket: {e}")
            return False

    # =========================================================================
    # Event Processing
    # =========================================================================

    async def process_event(
        self,
        event_type: str,
        external_id: str,
        payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Process an event from the OGS event listener.

        This is called by the EventService when an OGS event is received.

        Returns response data including message_id, conversation_id, entity_id
        if a response was generated.
        """
        logger.info(f"OGS: Processing event {event_type} for {external_id}")

        # Handle challenge events separately - they don't have numeric game IDs
        if event_type == "challenge":
            return await self._handle_challenge(payload)

        # For game events, parse the game_id from external_id
        # Handle format like "1941392:uuid" by taking just the numeric part
        try:
            game_id_str = external_id.split(":")[0] if ":" in external_id else external_id
            game_id = int(game_id_str)
        except ValueError:
            logger.error(f"OGS: Invalid game ID format: {external_id}")
            return None

        if event_type == "game_move":
            return await self._handle_game_move(game_id, payload)
        elif event_type == "game_phase":
            return await self._handle_game_phase(game_id, payload)
        elif event_type == "game_started":
            return await self._handle_game_started(game_id, payload)
        else:
            logger.warning(f"OGS: Unknown event type: {event_type}")
            return None

    async def _handle_game_started(
        self,
        game_id: int,
        payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Handle a game started event (new game or challenge accepted)."""
        logger.info(f"OGS: Handling game started for game {game_id}")

        # Get the game state from cache (should be populated by socket gamedata)
        game = self.get_game(game_id)
        if not game:
            # Try to parse from payload if not in cache
            game_data = payload.get("game", {})
            if game_data:
                game = self._parse_socket_gamedata(game_id, game_data)
                if game:
                    self._active_games[game_id] = game

        if game:
            logger.info(
                f"OGS: Game {game_id} started - playing {game.our_color} vs {game.opponent_username}, "
                f"our_turn={game.our_turn}"
            )
            # If it's our turn, generate a move
            if game.our_turn:
                return await self._generate_and_submit_move(game)
        else:
            logger.warning(f"OGS: Could not fetch game {game_id} details after game_started event")

        return None

    async def _handle_game_move(
        self,
        game_id: int,
        payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Handle a game move event (opponent played)."""
        # Get game state from cache (should be updated by socket gamedata)
        game = self.get_game(game_id)
        if not game:
            logger.error(f"OGS: Game {game_id} not found in cache after move event")
            return None

        # Check if it's our turn
        if not game.our_turn:
            logger.debug(f"OGS: Not our turn in game {game_id}")
            return None

        # Generate and submit our move
        return await self._generate_and_submit_move(game)

    async def _handle_game_phase(
        self,
        game_id: int,
        payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Handle a game phase change event."""
        phase = payload.get("phase", "")
        logger.info(f"OGS: Game {game_id} phase changed to: {phase}")

        # Get game state from cache
        game = self.get_game(game_id)

        if phase == "finished":
            # Game ended - could post commentary to conversation
            if game and game.conversation_id:
                # Post game result to conversation
                await self._post_game_result(game)

        return None

    async def _handle_challenge(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle an incoming challenge."""
        if not settings.ogs_auto_accept_challenges:
            logger.info("OGS: Challenge received but auto-accept is disabled")
            return None

        challenge_id = payload.get("id")
        board_size = payload.get("width", 19)
        time_control_data = payload.get("time_control", {})
        # OGS challenges have a "speed" field for the category (live, correspondence, blitz)
        # and a "system" field for the timing system (byoyomi, fischer, etc.)
        time_control = time_control_data.get("speed", time_control_data.get("system", "unknown"))

        # Check if we accept this challenge
        accepted_sizes = settings.get_ogs_accepted_board_sizes()
        accepted_times = settings.get_ogs_accepted_time_controls()

        if board_size not in accepted_sizes:
            logger.info(f"OGS: Declining challenge - board size {board_size} not accepted")
            return None

        if time_control.lower() not in accepted_times:
            logger.info(f"OGS: Declining challenge - time control {time_control} not accepted")
            return None

        # Accept the challenge
        logger.info(f"OGS: Accepting challenge {challenge_id}")
        await self._accept_challenge(challenge_id)

        return None

    async def _accept_challenge(self, challenge_id: int) -> bool:
        """Accept an incoming challenge via socket.io."""
        if not self._socket_client:
            logger.error("OGS: Cannot accept challenge - no socket client available")
            return False

        try:
            await self._socket_client.emit("challenge/accept", {"challenge_id": challenge_id})
            logger.info(f"OGS: Challenge {challenge_id} accepted via socket")
            return True
        except Exception as e:
            logger.error(f"OGS: Error accepting challenge via socket: {e}")
            return False

    async def _generate_and_submit_move(self, game: OGSGame) -> Optional[Dict[str, Any]]:
        """
        Generate a move using the LLM and submit it to OGS.

        This is the core integration point with the LLM.
        """
        from app.services import llm_service, session_manager, notes_service
        from app.config import settings as app_settings

        logger.info(f"OGS: Generating move for game {game.game_id}")

        # Get entity config
        entity = app_settings.get_entity_by_index(settings.ogs_entity_id)
        if not entity:
            logger.error(f"OGS: Entity {settings.ogs_entity_id} not found")
            return None

        # Build the game context for the LLM
        game_context = self.format_game_context(game)

        # Load Go learning notes if available
        go_notes = ""
        try:
            notes_content = await notes_service.read_note(entity.label, "go-notes.md")
            if notes_content:
                go_notes = f"\n=== YOUR GO LEARNING NOTES ===\n{notes_content}\n=== END NOTES ===\n"
        except Exception as e:
            logger.debug(f"OGS: No Go notes found for {entity.label}: {e}")

        # Build the prompt
        system_prompt = f"""You are playing a game of Go on Online-Go.com.
You are playing as {game.our_color.upper()}.

{go_notes}

Respond with your move in the format: MOVE: <coordinate>
- Use standard Go notation (e.g., MOVE: D4, MOVE: Q16)
- You can also respond with MOVE: pass or MOVE: resign
- You may include brief commentary before your move
- The column letters skip 'I' (A-H, J-T)

Focus on making a strong move. Consider:
- Territory and influence
- Groups' life and death
- Opponent's threats
- Overall game strategy"""

        # Build messages for LLM
        messages = [{"role": "user", "content": game_context}]

        try:
            # Call the LLM
            provider = entity.llm_provider
            model = entity.default_model or app_settings.get_default_model_for_provider(provider)

            response = await llm_service.send_message(
                messages=messages,
                model=model,
                provider=provider,
                system_prompt=system_prompt,
                temperature=0.7,  # Some creativity in move selection
                max_tokens=500,  # Short response expected
            )

            response_text = response.get("content", "")
            logger.debug(f"OGS: LLM response: {response_text}")

            # Parse the move
            move, commentary = self.parse_move_from_response(response_text, game.board_size)

            if not move:
                logger.error("OGS: Failed to parse move from LLM response")
                return None

            # Submit the move
            success = await self.submit_move(game.game_id, move)
            if not success:
                logger.error(f"OGS: Failed to submit move {move}")
                return None

            # Post commentary to linked conversation if exists
            if commentary and game.conversation_id:
                await self._post_move_commentary(game, move, commentary)

            return {
                "entity_id": settings.ogs_entity_id,
                "game_id": game.game_id,
                "move": move,
                "commentary": commentary,
            }

        except Exception as e:
            logger.error(f"OGS: Error generating move: {e}", exc_info=True)
            return None

    async def _post_move_commentary(
        self,
        game: OGSGame,
        move: str,
        commentary: str
    ) -> None:
        """Post move commentary to the linked conversation."""
        if not game.conversation_id:
            return

        async with async_session_maker() as session:
            try:
                # Create the commentary message
                move_notation = self._coords_to_notation(
                    *self._notation_to_coords(move, game.board_size),
                    game.board_size
                ) if move not in ("PASS", "RESIGN") else move.lower()

                content = f"[Move {len(game.moves) + 1}: {move_notation}]\n\n{commentary}"

                message = Message(
                    conversation_id=game.conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=content,
                    speaker_entity_id=settings.ogs_entity_id,
                )
                session.add(message)
                await session.commit()

                logger.info(f"OGS: Posted move commentary to conversation {game.conversation_id}")
            except Exception as e:
                logger.error(f"OGS: Error posting commentary: {e}")

    async def _post_game_result(self, game: OGSGame) -> None:
        """Post game result to the linked conversation."""
        if not game.conversation_id:
            return

        # This would post a summary of the game result
        # Implementation depends on what data is available from OGS
        pass

    # =========================================================================
    # Conversation Linking
    # =========================================================================

    async def link_game_to_conversation(
        self,
        game_id: int,
        conversation_id: str
    ) -> bool:
        """Link an OGS game to a conversation."""
        game = self.get_game(game_id)
        if not game:
            return False

        game.conversation_id = conversation_id

        # Update the conversation's external link
        async with async_session_maker() as session:
            try:
                result = await session.execute(
                    select(Conversation).where(Conversation.id == conversation_id)
                )
                conversation = result.scalar_one_or_none()
                if conversation:
                    conversation.external_link_type = "ogs_game"
                    conversation.external_link_id = str(game_id)
                    conversation.external_link_metadata = {
                        "opponent": game.opponent_username,
                        "our_color": game.our_color,
                        "board_size": game.board_size,
                    }
                    await session.commit()
                    logger.info(f"OGS: Linked game {game_id} to conversation {conversation_id}")
                    return True
            except Exception as e:
                logger.error(f"OGS: Error linking game: {e}")

        return False

    async def get_game_for_conversation(self, conversation_id: str) -> Optional[OGSGame]:
        """Get the OGS game linked to a conversation."""
        async with async_session_maker() as session:
            try:
                result = await session.execute(
                    select(Conversation).where(Conversation.id == conversation_id)
                )
                conversation = result.scalar_one_or_none()
                if conversation and conversation.external_link_type == "ogs_game":
                    game_id = int(conversation.external_link_id)
                    return self.get_game(game_id)
            except Exception as e:
                logger.error(f"OGS: Error getting game for conversation: {e}")

        return None


# Singleton instance
ogs_service = OGSService()
