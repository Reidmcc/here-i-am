"""
OGS (Online-Go Server) Service for Go game integration.

This service handles:
- API key and OAuth authentication with OGS
- REST API interactions (game info, moves, chat)
- Board state conversion to ASCII representation
- Move parsing from LLM responses
- Processing game events (your turn notifications)

Authentication methods (in order of preference):
1. API Key - Recommended. Generated from bot profile after moderator approval.
2. OAuth client_credentials - Alternative. May have limited support.
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import httpx
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
    Service for interacting with OGS (Online-Go Server).

    Handles authentication, API calls, game state management,
    and coordinating with the LLM for move generation.
    """

    # Column labels for coordinate conversion
    COLUMN_LABELS = "ABCDEFGHJKLMNOPQRST"  # Note: 'I' is skipped in Go notation

    def __init__(self):
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._refresh_token: Optional[str] = None
        self._user_id: Optional[int] = None
        self._active_games: Dict[int, OGSGame] = {}
        self._http_client: Optional[httpx.AsyncClient] = None
        self._using_api_key: bool = False

    @property
    def is_configured(self) -> bool:
        """Check if OGS is properly configured."""
        if not settings.ogs_enabled or not settings.ogs_entity_id:
            return False
        # Either API key or OAuth credentials required
        has_api_key = bool(settings.ogs_api_key)
        has_oauth = bool(settings.ogs_client_id and settings.ogs_client_secret)
        return has_api_key or has_oauth

    @property
    def uses_api_key(self) -> bool:
        """Check if using API key authentication (preferred)."""
        return bool(settings.ogs_api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=settings.ogs_api_url,
                timeout=30.0
            )
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    # =========================================================================
    # Authentication
    # =========================================================================

    async def authenticate(self) -> bool:
        """
        Authenticate with OGS using API key or OAuth client credentials.

        Prefers API key if configured, falls back to OAuth.
        Returns True if authentication succeeded.
        """
        if not self.is_configured:
            logger.warning("OGS: Not configured, skipping authentication")
            return False

        # Prefer API key authentication
        if self.uses_api_key:
            return await self._authenticate_api_key()
        else:
            return await self._authenticate_oauth()

    async def _authenticate_api_key(self) -> bool:
        """Authenticate using API key (recommended method)."""
        logger.info("OGS: Authenticating with API key")

        # API key is used directly as bearer token
        self._access_token = settings.ogs_api_key
        self._using_api_key = True
        # API keys don't expire (set far future expiry)
        self._token_expires_at = datetime.utcnow() + timedelta(days=365)

        try:
            # Verify the API key by fetching user info
            await self._fetch_user_info()
            logger.info(f"OGS: Authenticated as user {self._user_id} (API key)")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"OGS: API key authentication failed: {e.response.status_code} - {e.response.text}")
            self._access_token = None
            self._using_api_key = False
            return False
        except Exception as e:
            logger.error(f"OGS: API key authentication error: {e}")
            self._access_token = None
            self._using_api_key = False
            return False

    async def _authenticate_oauth(self) -> bool:
        """Authenticate using OAuth client credentials (alternative method)."""
        logger.info("OGS: Authenticating with OAuth client credentials")

        client = await self._get_client()

        try:
            response = await client.post(
                "/oauth2/token/",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.ogs_client_id,
                    "client_secret": settings.ogs_client_secret,
                }
            )
            response.raise_for_status()

            data = response.json()
            self._access_token = data["access_token"]
            self._refresh_token = data.get("refresh_token")
            self._using_api_key = False
            # Set expiry with buffer
            expires_in = data.get("expires_in", 3600)
            self._token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 60)

            # Get user info
            await self._fetch_user_info()

            logger.info(f"OGS: Authenticated as user {self._user_id} (OAuth)")
            return True

        except httpx.HTTPStatusError as e:
            logger.error(f"OGS: OAuth authentication failed: {e.response.status_code} - {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"OGS: OAuth authentication error: {e}")
            return False

    async def _ensure_authenticated(self) -> bool:
        """Ensure we have a valid access token."""
        if not self._access_token or not self._token_expires_at:
            return await self.authenticate()

        # API keys don't expire, but OAuth tokens do
        if not self._using_api_key and datetime.utcnow() >= self._token_expires_at:
            logger.info("OGS: OAuth token expired, re-authenticating")
            return await self.authenticate()

        return True

    async def _fetch_user_info(self) -> None:
        """Fetch the authenticated user's info."""
        client = await self._get_client()
        response = await client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {self._access_token}"}
        )
        response.raise_for_status()
        data = response.json()
        self._user_id = data["id"]

    def _auth_headers(self) -> Dict[str, str]:
        """Get authorization headers."""
        return {"Authorization": f"Bearer {self._access_token}"}

    # =========================================================================
    # Game Management
    # =========================================================================

    async def get_active_games(self) -> List[OGSGame]:
        """Fetch all active games for the bot."""
        if not await self._ensure_authenticated():
            return []

        client = await self._get_client()

        try:
            response = await client.get(
                f"/api/v1/players/{self._user_id}/games",
                headers=self._auth_headers(),
                params={"ended__isnull": "true"}  # Only active games
            )
            response.raise_for_status()

            data = response.json()
            games = []

            for game_data in data.get("results", []):
                game = await self._parse_game_data(game_data)
                if game:
                    games.append(game)
                    self._active_games[game.game_id] = game

            logger.info(f"OGS: Found {len(games)} active games")
            return games

        except Exception as e:
            logger.error(f"OGS: Error fetching active games: {e}")
            return []

    async def get_game(self, game_id: int) -> Optional[OGSGame]:
        """Fetch a specific game's current state."""
        if not await self._ensure_authenticated():
            return None

        client = await self._get_client()

        try:
            response = await client.get(
                f"/api/v1/games/{game_id}",
                headers=self._auth_headers()
            )
            response.raise_for_status()

            game = await self._parse_game_data(response.json())
            if game:
                self._active_games[game_id] = game
            return game

        except Exception as e:
            logger.error(f"OGS: Error fetching game {game_id}: {e}")
            return None

    async def _parse_game_data(self, data: Dict[str, Any]) -> Optional[OGSGame]:
        """Parse game data from OGS API into an OGSGame object."""
        try:
            game_id = data["id"]
            black_player = data.get("black", {})
            white_player = data.get("white", {})

            # Determine our color
            if black_player.get("id") == self._user_id:
                our_color = "black"
                opponent_username = white_player.get("username", "Unknown")
            else:
                our_color = "white"
                opponent_username = black_player.get("username", "Unknown")

            # Parse game state from gamedata
            gamedata = data.get("gamedata", {})
            moves = self._parse_moves(gamedata.get("moves", []))
            board_size = data.get("width", 19)
            board_state = self._build_board_state(moves, board_size)

            # Determine whose turn it is
            current_player = len(moves) % 2  # 0 = black, 1 = white
            our_turn = (current_player == 0 and our_color == "black") or \
                      (current_player == 1 and our_color == "white")

            # Get captures
            captures = {
                "black": gamedata.get("score", {}).get("black", {}).get("prisoners", 0),
                "white": gamedata.get("score", {}).get("white", {}).get("prisoners", 0),
            }

            # Time control info
            time_control = data.get("time_control", {}).get("system", "unknown")

            return OGSGame(
                game_id=game_id,
                opponent_username=opponent_username,
                our_color=our_color,
                board_size=board_size,
                time_control=time_control,
                phase=data.get("phase", "play"),
                our_turn=our_turn,
                moves=moves,
                board_state=board_state,
                captures=captures,
                time_left=data.get("clock"),
                metadata={
                    "name": data.get("name", ""),
                    "started": data.get("started"),
                    "rules": gamedata.get("rules", "japanese"),
                    "komi": gamedata.get("komi", 6.5),
                }
            )

        except Exception as e:
            logger.error(f"OGS: Error parsing game data: {e}")
            return None

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
        Submit a move to OGS.

        Args:
            game_id: The OGS game ID
            move: Move in notation format (e.g., "D4", "pass", "resign")

        Returns True if the move was accepted.
        """
        if not await self._ensure_authenticated():
            return False

        game = self._active_games.get(game_id)
        if not game:
            game = await self.get_game(game_id)
        if not game:
            logger.error(f"OGS: Cannot submit move - game {game_id} not found")
            return False

        client = await self._get_client()

        move = move.upper()

        try:
            if move == "RESIGN":
                # Resign the game
                response = await client.post(
                    f"/api/v1/games/{game_id}/resign",
                    headers=self._auth_headers()
                )
            elif move == "PASS":
                # Pass
                response = await client.post(
                    f"/api/v1/games/{game_id}/move",
                    headers=self._auth_headers(),
                    json={"move": ".."}  # OGS pass notation
                )
            else:
                # Regular move
                x, y = self._notation_to_coords(move, game.board_size)
                response = await client.post(
                    f"/api/v1/games/{game_id}/move",
                    headers=self._auth_headers(),
                    json={"move": f"{self.COLUMN_LABELS[x].lower()}{game.board_size - y}"}
                )

            response.raise_for_status()
            logger.info(f"OGS: Move {move} submitted to game {game_id}")

            # Update our cached game state
            await self.get_game(game_id)

            return True

        except httpx.HTTPStatusError as e:
            logger.error(f"OGS: Move rejected: {e.response.status_code} - {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"OGS: Error submitting move: {e}")
            return False

    async def send_game_chat(self, game_id: int, message: str) -> bool:
        """Send a chat message to the game chat."""
        if not await self._ensure_authenticated():
            return False

        client = await self._get_client()

        try:
            response = await client.post(
                f"/api/v1/games/{game_id}/chat",
                headers=self._auth_headers(),
                json={"body": message}
            )
            response.raise_for_status()
            logger.info(f"OGS: Sent chat to game {game_id}")
            return True
        except Exception as e:
            logger.error(f"OGS: Error sending chat: {e}")
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
        logger.info(f"OGS: Processing event {event_type} for game {external_id}")

        game_id = int(external_id)

        if event_type == "game_move":
            return await self._handle_game_move(game_id, payload)
        elif event_type == "game_phase":
            return await self._handle_game_phase(game_id, payload)
        elif event_type == "challenge":
            return await self._handle_challenge(payload)
        else:
            logger.warning(f"OGS: Unknown event type: {event_type}")
            return None

    async def _handle_game_move(
        self,
        game_id: int,
        payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Handle a game move event (opponent played)."""
        # Refresh game state
        game = await self.get_game(game_id)
        if not game:
            logger.error(f"OGS: Game {game_id} not found after move event")
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

        # Refresh game state
        game = await self.get_game(game_id)

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
        time_control = payload.get("time_control", {}).get("system", "unknown")

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
        """Accept an incoming challenge."""
        if not await self._ensure_authenticated():
            return False

        client = await self._get_client()

        try:
            response = await client.post(
                f"/api/v1/challenges/{challenge_id}/accept",
                headers=self._auth_headers()
            )
            response.raise_for_status()
            logger.info(f"OGS: Challenge {challenge_id} accepted")
            return True
        except Exception as e:
            logger.error(f"OGS: Error accepting challenge: {e}")
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
        game = self._active_games.get(game_id) or await self.get_game(game_id)
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
                    return await self.get_game(game_id)
            except Exception as e:
                logger.error(f"OGS: Error getting game for conversation: {e}")

        return None


# Singleton instance
ogs_service = OGSService()
