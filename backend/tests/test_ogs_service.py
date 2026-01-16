"""
Tests for the OGS (Online-Go Server) socket.io-only integration.

Tests cover:
- Service initialization and configuration
- Authentication via API key
- Game management (cache-based)
- Socket gamedata parsing
- Move submission via socket.io
- Game chat via socket.io
- Challenge acceptance via socket.io
- Event processing
- Conversation linking
"""
import pytest
import asyncio
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, List
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

# Import directly to avoid loading other services
sys.path.insert(0, "/home/user/here-i-am/backend")
from app.services.ogs_service import OGSService, OGSGame
from app.config import Settings


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def ogs_settings():
    """Create test settings with OGS configured."""
    return Settings(
        anthropic_api_key="test-anthropic-key",
        pinecone_api_key="test-pinecone-key",
        pinecone_indexes='[{"index_name": "test-entity", "label": "Test", "llm_provider": "anthropic"}]',
        here_i_am_database_url="sqlite+aiosqlite:///:memory:",
        ogs_enabled=True,
        ogs_api_key="test-api-key",
        ogs_bot_username="test-bot",
        ogs_entity_id="test-entity",
        ogs_socket_url="https://online-go.com",
        ogs_auto_accept_challenges=True,
        ogs_accepted_board_sizes="9,13,19",
        ogs_accepted_time_controls="live,correspondence,blitz",
    )


@pytest.fixture
def ogs_settings_no_api_key():
    """Create test settings without OGS API key."""
    return Settings(
        anthropic_api_key="test-anthropic-key",
        pinecone_api_key="test-pinecone-key",
        pinecone_indexes='[{"index_name": "test-entity", "label": "Test", "llm_provider": "anthropic"}]',
        here_i_am_database_url="sqlite+aiosqlite:///:memory:",
        ogs_enabled=True,
        ogs_api_key="",
        ogs_bot_username="",
        ogs_entity_id="test-entity",
    )


@pytest.fixture
def ogs_settings_disabled():
    """Create test settings with OGS disabled."""
    return Settings(
        anthropic_api_key="test-anthropic-key",
        pinecone_api_key="test-pinecone-key",
        pinecone_indexes='[{"index_name": "test-entity", "label": "Test", "llm_provider": "anthropic"}]',
        here_i_am_database_url="sqlite+aiosqlite:///:memory:",
        ogs_enabled=False,
        ogs_api_key="test-api-key",
        ogs_bot_username="test-bot",
        ogs_entity_id="test-entity",
    )


@pytest.fixture
def ogs_service():
    """Create a fresh OGS service instance."""
    return OGSService()


@pytest.fixture
def mock_socket_client():
    """Create a mock socket.io client."""
    mock_sio = MagicMock()
    mock_sio.emit = AsyncMock()
    return mock_sio


@pytest.fixture
def sample_gamedata():
    """Create sample game data from socket.io event."""
    return {
        "game_id": 12345,
        "width": 19,
        "height": 19,
        "black": {"id": 1001, "username": "test-bot"},
        "white": {"id": 2002, "username": "opponent"},
        "moves": [[3, 3], [15, 15], [3, 15]],
        "phase": "play",
        "score": {
            "black": {"prisoners": 2},
            "white": {"prisoners": 1}
        },
        "time_control": {"system": "byoyomi"},
        "clock": {"black_time": 300, "white_time": 300},
        "game_name": "Test Game",
        "started": "2025-01-16T10:00:00Z",
        "rules": "japanese",
        "komi": 6.5,
    }


@pytest.fixture
def sample_game():
    """Create a sample OGSGame object."""
    return OGSGame(
        game_id=12345,
        opponent_username="opponent",
        our_color="black",
        board_size=19,
        time_control="byoyomi",
        phase="play",
        our_turn=True,
        moves=[(3, 3), (15, 15), (3, 15)],
        board_state=[[0] * 19 for _ in range(19)],
        captures={"black": 2, "white": 1},
        time_left={"black_time": 300, "white_time": 300},
        metadata={"rules": "japanese", "komi": 6.5}
    )


# =============================================================================
# Configuration Tests
# =============================================================================

class TestOGSServiceConfiguration:
    """Tests for OGS service configuration."""

    def test_service_initialization(self, ogs_service):
        """Test OGS service initializes with correct defaults."""
        assert ogs_service._access_token is None
        assert ogs_service._token_expires_at is None
        assert ogs_service._user_id is None
        assert ogs_service._active_games == {}
        assert ogs_service._socket_client is None

    def test_set_socket_client(self, ogs_service, mock_socket_client):
        """Test setting socket.io client."""
        ogs_service.set_socket_client(mock_socket_client)
        assert ogs_service._socket_client is mock_socket_client

    def test_is_configured_with_api_key(self, ogs_service, ogs_settings):
        """Test is_configured returns True when properly configured."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            assert ogs_service.is_configured is True

    def test_is_configured_without_api_key(self, ogs_service, ogs_settings_no_api_key):
        """Test is_configured returns False without API key."""
        with patch('app.services.ogs_service.settings', ogs_settings_no_api_key):
            assert ogs_service.is_configured is False

    def test_is_configured_when_disabled(self, ogs_service, ogs_settings_disabled):
        """Test is_configured returns False when OGS is disabled."""
        with patch('app.services.ogs_service.settings', ogs_settings_disabled):
            assert ogs_service.is_configured is False


# =============================================================================
# Authentication Tests
# =============================================================================

class TestOGSAuthentication:
    """Tests for OGS authentication."""

    @pytest.mark.asyncio
    async def test_authenticate_success(self, ogs_service, ogs_settings):
        """Test successful authentication with API key."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            result = await ogs_service.authenticate()

            assert result is True
            assert ogs_service._access_token == "test-api-key"
            assert ogs_service._token_expires_at is not None
            # Token should be valid for a long time (API keys don't expire)
            assert ogs_service._token_expires_at > datetime.utcnow()

    @pytest.mark.asyncio
    async def test_authenticate_not_configured(self, ogs_service, ogs_settings_no_api_key):
        """Test authentication fails when not configured."""
        with patch('app.services.ogs_service.settings', ogs_settings_no_api_key):
            result = await ogs_service.authenticate()

            assert result is False
            assert ogs_service._access_token is None

    @pytest.mark.asyncio
    async def test_ensure_authenticated_calls_authenticate(self, ogs_service, ogs_settings):
        """Test _ensure_authenticated calls authenticate when needed."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            # No token yet
            assert ogs_service._access_token is None

            result = await ogs_service._ensure_authenticated()

            assert result is True
            assert ogs_service._access_token == "test-api-key"

    @pytest.mark.asyncio
    async def test_ensure_authenticated_returns_true_when_authenticated(self, ogs_service, ogs_settings):
        """Test _ensure_authenticated returns True when already authenticated."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service._access_token = "existing-token"
            ogs_service._token_expires_at = datetime.utcnow() + timedelta(days=365)

            result = await ogs_service._ensure_authenticated()

            assert result is True
            # Token should not change
            assert ogs_service._access_token == "existing-token"


# =============================================================================
# Game Management Tests
# =============================================================================

class TestOGSGameManagement:
    """Tests for OGS game management."""

    def test_get_active_games_empty(self, ogs_service):
        """Test get_active_games returns empty list when no games."""
        games = ogs_service.get_active_games()
        assert games == []

    def test_get_active_games_returns_cached(self, ogs_service, sample_game):
        """Test get_active_games returns cached games."""
        ogs_service._active_games[12345] = sample_game

        games = ogs_service.get_active_games()

        assert len(games) == 1
        assert games[0].game_id == 12345
        assert games[0].opponent_username == "opponent"

    def test_get_game_returns_none_when_not_cached(self, ogs_service):
        """Test get_game returns None when game not in cache."""
        game = ogs_service.get_game(99999)
        assert game is None

    def test_get_game_returns_cached_game(self, ogs_service, sample_game):
        """Test get_game returns cached game."""
        ogs_service._active_games[12345] = sample_game

        game = ogs_service.get_game(12345)

        assert game is not None
        assert game.game_id == 12345

    @pytest.mark.asyncio
    async def test_request_game_data_no_socket(self, ogs_service):
        """Test request_game_data fails without socket client."""
        result = await ogs_service.request_game_data(12345)
        assert result is False

    @pytest.mark.asyncio
    async def test_request_game_data_with_socket(self, ogs_service, mock_socket_client):
        """Test request_game_data emits socket event."""
        ogs_service.set_socket_client(mock_socket_client)

        result = await ogs_service.request_game_data(12345)

        assert result is True
        mock_socket_client.emit.assert_called_once_with(
            "game/connect",
            {"game_id": 12345}
        )


# =============================================================================
# Socket Gamedata Parsing Tests
# =============================================================================

class TestSocketGamedataParsing:
    """Tests for parsing game data from socket.io events."""

    def test_update_game_from_socket(self, ogs_service, sample_gamedata):
        """Test updating game from socket gamedata."""
        ogs_service._user_id = 1001  # We are black

        game = ogs_service.update_game_from_socket(12345, sample_gamedata)

        assert game is not None
        assert game.game_id == 12345
        assert game.our_color == "black"
        assert game.opponent_username == "opponent"
        assert game.board_size == 19
        assert game.time_control == "byoyomi"
        assert game.phase == "play"
        assert len(game.moves) == 3
        assert game.captures == {"black": 2, "white": 1}

    def test_update_game_determines_our_turn_black(self, ogs_service, sample_gamedata):
        """Test our_turn is correctly determined when we are black."""
        ogs_service._user_id = 1001  # We are black
        # 3 moves means it's white's turn (move 0=black, 1=white, 2=black)
        sample_gamedata["moves"] = [[3, 3], [15, 15], [3, 15]]

        game = ogs_service.update_game_from_socket(12345, sample_gamedata)

        assert game.our_color == "black"
        # After 3 moves, it's white's turn (index 3 % 2 = 1 = white)
        assert game.our_turn is False

    def test_update_game_determines_our_turn_white(self, ogs_service, sample_gamedata):
        """Test our_turn is correctly determined when we are white."""
        ogs_service._user_id = 2002  # We are white
        # 3 moves means it's white's turn
        sample_gamedata["moves"] = [[3, 3], [15, 15], [3, 15]]

        game = ogs_service.update_game_from_socket(12345, sample_gamedata)

        assert game.our_color == "white"
        assert game.our_turn is True

    def test_update_game_caches_result(self, ogs_service, sample_gamedata):
        """Test game is cached after parsing."""
        ogs_service._user_id = 1001

        game = ogs_service.update_game_from_socket(12345, sample_gamedata)

        assert 12345 in ogs_service._active_games
        assert ogs_service._active_games[12345] == game

    def test_parse_socket_gamedata_with_players_format(self, ogs_service):
        """Test parsing gamedata with players nested format."""
        ogs_service._user_id = 1001
        gamedata = {
            "players": {
                "black": {"id": 1001, "username": "test-bot"},
                "white": {"id": 2002, "username": "opponent"}
            },
            "width": 9,
            "moves": [],
            "phase": "play",
            "score": {},
            "time_control": {"system": "simple"},
        }

        game = ogs_service._parse_socket_gamedata(12345, gamedata)

        assert game is not None
        assert game.our_color == "black"
        assert game.opponent_username == "opponent"


# =============================================================================
# Move Submission Tests
# =============================================================================

class TestMoveSubmission:
    """Tests for submitting moves via socket.io."""

    @pytest.mark.asyncio
    async def test_submit_move_no_socket(self, ogs_service, ogs_settings, sample_game):
        """Test submit_move fails without socket client."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service._access_token = "test-token"
            ogs_service._active_games[12345] = sample_game

            result = await ogs_service.submit_move(12345, "D4")

            assert result is False

    @pytest.mark.asyncio
    async def test_submit_move_game_not_found(self, ogs_service, ogs_settings, mock_socket_client):
        """Test submit_move fails when game not in cache."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service._access_token = "test-token"
            ogs_service.set_socket_client(mock_socket_client)

            result = await ogs_service.submit_move(99999, "D4")

            assert result is False

    @pytest.mark.asyncio
    async def test_submit_move_regular_move(self, ogs_service, ogs_settings, mock_socket_client, sample_game):
        """Test submitting a regular move via socket."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service._access_token = "test-token"
            ogs_service.set_socket_client(mock_socket_client)
            ogs_service._active_games[12345] = sample_game

            result = await ogs_service.submit_move(12345, "D4")

            assert result is True
            mock_socket_client.emit.assert_called_once()
            call_args = mock_socket_client.emit.call_args
            assert call_args[0][0] == "game/move"
            assert call_args[0][1]["game_id"] == 12345
            # D4 on 19x19 board: D=3, 4 means row 4 from bottom = row 15 (19-4)
            assert call_args[0][1]["move"] == "d4"

    @pytest.mark.asyncio
    async def test_submit_move_pass(self, ogs_service, ogs_settings, mock_socket_client, sample_game):
        """Test submitting a pass move via socket."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service._access_token = "test-token"
            ogs_service.set_socket_client(mock_socket_client)
            ogs_service._active_games[12345] = sample_game

            result = await ogs_service.submit_move(12345, "pass")

            assert result is True
            mock_socket_client.emit.assert_called_once_with(
                "game/move",
                {"game_id": 12345, "move": ".."}
            )

    @pytest.mark.asyncio
    async def test_submit_move_resign(self, ogs_service, ogs_settings, mock_socket_client, sample_game):
        """Test submitting a resign via socket."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service._access_token = "test-token"
            ogs_service.set_socket_client(mock_socket_client)
            ogs_service._active_games[12345] = sample_game

            result = await ogs_service.submit_move(12345, "resign")

            assert result is True
            mock_socket_client.emit.assert_called_once_with(
                "game/resign",
                {"game_id": 12345}
            )


# =============================================================================
# Game Chat Tests
# =============================================================================

class TestGameChat:
    """Tests for sending game chat via socket.io."""

    @pytest.mark.asyncio
    async def test_send_game_chat_no_socket(self, ogs_service):
        """Test send_game_chat fails without socket client."""
        result = await ogs_service.send_game_chat(12345, "Hello!")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_game_chat_success(self, ogs_service, mock_socket_client):
        """Test sending game chat via socket."""
        ogs_service.set_socket_client(mock_socket_client)

        result = await ogs_service.send_game_chat(12345, "Good game!")

        assert result is True
        mock_socket_client.emit.assert_called_once_with(
            "game/chat",
            {
                "game_id": 12345,
                "body": "Good game!",
                "type": "main"
            }
        )

    @pytest.mark.asyncio
    async def test_send_game_chat_with_move_number(self, ogs_service, mock_socket_client):
        """Test sending game chat with move number."""
        ogs_service.set_socket_client(mock_socket_client)

        result = await ogs_service.send_game_chat(12345, "Nice move!", move_number=42)

        assert result is True
        mock_socket_client.emit.assert_called_once_with(
            "game/chat",
            {
                "game_id": 12345,
                "body": "Nice move!",
                "type": "main",
                "move_number": 42
            }
        )


# =============================================================================
# Challenge Acceptance Tests
# =============================================================================

class TestChallengeAcceptance:
    """Tests for accepting challenges via socket.io."""

    @pytest.mark.asyncio
    async def test_accept_challenge_no_socket(self, ogs_service):
        """Test _accept_challenge fails without socket client."""
        result = await ogs_service._accept_challenge(999)
        assert result is False

    @pytest.mark.asyncio
    async def test_accept_challenge_success(self, ogs_service, mock_socket_client):
        """Test accepting a challenge via socket."""
        ogs_service.set_socket_client(mock_socket_client)

        result = await ogs_service._accept_challenge(999)

        assert result is True
        mock_socket_client.emit.assert_called_once_with(
            "challenge/accept",
            {"challenge_id": 999}
        )


# =============================================================================
# Move Parsing Tests
# =============================================================================

class TestMoveParsing:
    """Tests for parsing moves from LLM responses."""

    def test_parse_move_from_response_valid(self, ogs_service):
        """Test parsing a valid move from response."""
        response = "I think D4 is a good opening. MOVE: D4"

        move, commentary = ogs_service.parse_move_from_response(response)

        assert move == "D4"
        assert commentary == "I think D4 is a good opening."

    def test_parse_move_from_response_pass(self, ogs_service):
        """Test parsing a pass move from response."""
        response = "I'll pass this turn. MOVE: pass"

        move, commentary = ogs_service.parse_move_from_response(response)

        assert move == "PASS"
        assert commentary == "I'll pass this turn."

    def test_parse_move_from_response_resign(self, ogs_service):
        """Test parsing a resign from response."""
        response = "I have no good moves left. MOVE: resign"

        move, commentary = ogs_service.parse_move_from_response(response)

        assert move == "RESIGN"
        assert commentary == "I have no good moves left."

    def test_parse_move_from_response_no_move(self, ogs_service):
        """Test parsing when no move is present."""
        response = "I'm thinking about the position..."

        move, commentary = ogs_service.parse_move_from_response(response)

        assert move is None
        assert commentary is None

    def test_parse_move_from_response_no_commentary(self, ogs_service):
        """Test parsing when there's no commentary."""
        response = "MOVE: Q16"

        move, commentary = ogs_service.parse_move_from_response(response)

        assert move == "Q16"
        assert commentary is None


# =============================================================================
# Board ASCII Representation Tests
# =============================================================================

class TestBoardASCII:
    """Tests for board ASCII representation."""

    def test_board_to_ascii_empty_19x19(self, ogs_service):
        """Test ASCII representation of empty 19x19 board."""
        game = OGSGame(
            game_id=1,
            opponent_username="test",
            our_color="black",
            board_size=19,
            time_control="byoyomi",
            phase="play",
            our_turn=True,
            moves=[],
            board_state=[[0] * 19 for _ in range(19)],
            captures={"black": 0, "white": 0},
        )

        ascii_board = ogs_service.board_to_ascii(game)

        assert "A B C D E F G H J K L M N O P Q R S T" in ascii_board
        assert "19" in ascii_board
        assert "1" in ascii_board
        # Should have dots for empty intersections
        assert "." in ascii_board

    def test_board_to_ascii_with_stones(self, ogs_service):
        """Test ASCII representation with stones on board."""
        board_state = [[0] * 19 for _ in range(19)]
        board_state[3][3] = 1  # Black stone
        board_state[15][15] = 2  # White stone

        game = OGSGame(
            game_id=1,
            opponent_username="test",
            our_color="black",
            board_size=19,
            time_control="byoyomi",
            phase="play",
            our_turn=True,
            moves=[(3, 3), (15, 15)],
            board_state=board_state,
            captures={"black": 0, "white": 0},
        )

        ascii_board = ogs_service.board_to_ascii(game)

        assert "X" in ascii_board  # Black stone
        assert "O" in ascii_board  # White stone

    def test_format_game_context(self, ogs_service, sample_game):
        """Test formatting full game context."""
        context = ogs_service.format_game_context(sample_game)

        assert "=== GO GAME STATUS ===" in context
        assert "Game ID: 12345" in context
        assert "Opponent: opponent" in context
        assert "BLACK" in context
        assert "19x19" in context
        assert "MOVE:" in context


# =============================================================================
# Coordinate Conversion Tests
# =============================================================================

class TestCoordinateConversion:
    """Tests for coordinate conversion."""

    def test_coords_to_notation(self, ogs_service):
        """Test converting coordinates to notation."""
        # D4 on 19x19: x=3, y=15 (19-4)
        result = ogs_service._coords_to_notation(3, 15, 19)
        assert result == "D4"

        # A1 on 19x19: x=0, y=18
        result = ogs_service._coords_to_notation(0, 18, 19)
        assert result == "A1"

        # T19 on 19x19: x=18, y=0
        result = ogs_service._coords_to_notation(18, 0, 19)
        assert result == "T19"

    def test_coords_to_notation_pass(self, ogs_service):
        """Test converting pass coordinates."""
        result = ogs_service._coords_to_notation(-1, -1, 19)
        assert result == "pass"

    def test_notation_to_coords(self, ogs_service):
        """Test converting notation to coordinates."""
        # D4 on 19x19
        x, y = ogs_service._notation_to_coords("D4", 19)
        assert x == 3
        assert y == 15  # 19 - 4 = 15

        # A1 on 19x19
        x, y = ogs_service._notation_to_coords("A1", 19)
        assert x == 0
        assert y == 18

    def test_notation_to_coords_pass(self, ogs_service):
        """Test converting pass notation."""
        x, y = ogs_service._notation_to_coords("pass", 19)
        assert x == -1
        assert y == -1

    def test_notation_to_coords_invalid(self, ogs_service):
        """Test converting invalid notation raises error."""
        with pytest.raises(ValueError):
            ogs_service._notation_to_coords("Z99", 19)


# =============================================================================
# Event Processing Tests
# =============================================================================

class TestEventProcessing:
    """Tests for OGS event processing."""

    @pytest.mark.asyncio
    async def test_process_event_game_move(self, ogs_service, ogs_settings, sample_game):
        """Test processing a game move event."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service._active_games[12345] = sample_game
            sample_game.our_turn = False  # Not our turn

            result = await ogs_service.process_event(
                "game_move",
                "12345",
                {"move": [10, 10]}
            )

            # Should return None since it's not our turn
            assert result is None

    @pytest.mark.asyncio
    async def test_process_event_game_phase(self, ogs_service, ogs_settings, sample_game):
        """Test processing a game phase event."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service._active_games[12345] = sample_game

            result = await ogs_service.process_event(
                "game_phase",
                "12345",
                {"phase": "finished"}
            )

            assert result is None  # Phase change doesn't generate moves

    @pytest.mark.asyncio
    async def test_process_event_challenge_auto_accept(self, ogs_service, ogs_settings, mock_socket_client):
        """Test processing a challenge event with auto-accept."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service.set_socket_client(mock_socket_client)

            result = await ogs_service.process_event(
                "challenge",
                "challenge-999",
                {
                    "id": 999,
                    "width": 19,
                    "time_control": {"speed": "live", "system": "byoyomi"}
                }
            )

            # Should have accepted the challenge
            mock_socket_client.emit.assert_called_once_with(
                "challenge/accept",
                {"challenge_id": 999}
            )

    @pytest.mark.asyncio
    async def test_process_event_challenge_rejected_board_size(self, ogs_service, ogs_settings, mock_socket_client):
        """Test challenge rejected due to board size."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            ogs_service.set_socket_client(mock_socket_client)

            result = await ogs_service.process_event(
                "challenge",
                "challenge-999",
                {
                    "id": 999,
                    "width": 7,  # Not in accepted sizes
                    "time_control": {"system": "byoyomi"}
                }
            )

            # Should NOT have accepted
            mock_socket_client.emit.assert_not_called()


# =============================================================================
# Conversation Linking Tests
# =============================================================================

class TestConversationLinking:
    """Tests for linking games to conversations."""

    @pytest.mark.asyncio
    async def test_link_game_to_conversation_game_not_found(self, ogs_service):
        """Test linking fails when game not in cache."""
        result = await ogs_service.link_game_to_conversation(99999, "conv-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_link_game_updates_game_object(self, ogs_service, sample_game):
        """Test linking updates the game's conversation_id."""
        ogs_service._active_games[12345] = sample_game
        assert sample_game.conversation_id is None

        # Mock the database operation
        with patch('app.services.ogs_service.async_session_maker') as mock_session_maker:
            mock_session = AsyncMock()
            mock_session_maker.return_value.__aenter__.return_value = mock_session
            mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

            await ogs_service.link_game_to_conversation(12345, "conv-123")

        # Game object should be updated regardless of DB result
        assert sample_game.conversation_id == "conv-123"


# =============================================================================
# Integration Tests
# =============================================================================

class TestOGSIntegration:
    """Integration tests for OGS service."""

    @pytest.mark.asyncio
    async def test_full_move_flow(self, ogs_service, ogs_settings, mock_socket_client, sample_gamedata):
        """Test the full flow of receiving game data and submitting a move."""
        with patch('app.services.ogs_service.settings', ogs_settings):
            # Set up service
            ogs_service._user_id = 1001  # We are black
            ogs_service._access_token = "test-token"
            ogs_service.set_socket_client(mock_socket_client)

            # Receive game data from socket
            game = ogs_service.update_game_from_socket(12345, sample_gamedata)
            assert game is not None
            assert game.game_id == 12345

            # Check game is cached
            cached_game = ogs_service.get_game(12345)
            assert cached_game is not None
            assert cached_game.game_id == 12345

            # Submit a move
            result = await ogs_service.submit_move(12345, "Q16")
            assert result is True

            # Verify socket was called
            mock_socket_client.emit.assert_called()
