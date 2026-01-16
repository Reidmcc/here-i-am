"""
OGS (Online-Go Server) Event Listener.

Connects to OGS real-time API via socket.io to receive game events.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, Set

import socketio

from app.config import settings
from app.services.event_listeners.base import BaseEventListener

logger = logging.getLogger(__name__)


class OGSEventListener(BaseEventListener):
    """
    Event listener for OGS (Online-Go Server) real-time events.

    Connects to OGS socket.io server to receive:
    - Game move notifications
    - Phase changes (game end, stone removal)
    - Clock updates
    - Challenges
    """

    def __init__(self, entity_id: str, ogs_service: Any):
        """
        Initialize the OGS event listener.

        Args:
            entity_id: The AI entity this listener is for
            ogs_service: The OGS service for API interactions
        """
        super().__init__(name="OGS Listener", entity_id=entity_id)
        self._ogs_service = ogs_service
        self._sio: Optional[socketio.AsyncClient] = None
        self._subscribed_games: Set[int] = set()
        self._authenticated = False

    async def _connect(self) -> None:
        """Connect to OGS socket.io server."""
        logger.info(f"{self.name}: Connecting to OGS at {settings.ogs_socket_url}")

        # Create socket.io client
        self._sio = socketio.AsyncClient(
            logger=False,  # Suppress socketio internal logging
            engineio_logger=False,
            reconnection=False,  # We handle reconnection ourselves
        )

        # Register event handlers
        self._register_handlers()

        try:
            # Connect to OGS
            await self._sio.connect(
                settings.ogs_socket_url,
                transports=['websocket'],
                wait_timeout=10
            )

            # Authenticate
            await self._authenticate()

            # Subscribe to active games
            await self._subscribe_to_active_games()

            logger.info(f"{self.name}: Connected and authenticated")

        except Exception as e:
            if self._sio:
                try:
                    await self._sio.disconnect()
                except:
                    pass
                self._sio = None
            raise

    async def _disconnect(self) -> None:
        """Disconnect from OGS socket.io server."""
        if self._sio:
            try:
                # Unsubscribe from all games
                for game_id in list(self._subscribed_games):
                    await self._unsubscribe_game(game_id)

                await self._sio.disconnect()
            except Exception as e:
                logger.warning(f"{self.name}: Error during disconnect: {e}")
            finally:
                self._sio = None
                self._authenticated = False
                self._subscribed_games.clear()

    def _register_handlers(self) -> None:
        """Register socket.io event handlers."""
        if not self._sio:
            return

        @self._sio.event
        async def connect():
            logger.info(f"{self.name}: Socket connected")
            # Pass socket reference to OGS service for move submission
            self._ogs_service.set_socket_client(self._sio)

        @self._sio.event
        async def disconnect():
            logger.warning(f"{self.name}: Socket disconnected")
            self._authenticated = False
            self._ogs_service.set_socket_client(None)
            self._on_disconnect()

        @self._sio.event
        async def connect_error(data):
            logger.error(f"{self.name}: Connection error: {data}")

        # OGS-specific events
        @self._sio.on("game/*")
        async def on_game_event(data):
            """Catch-all for game events."""
            await self._handle_game_event(data)

        @self._sio.on("notification")
        async def on_notification(data):
            """Handle OGS notifications (challenges, etc)."""
            await self._handle_notification(data)

    async def _authenticate(self) -> None:
        """Authenticate with OGS socket server."""
        if not self._sio:
            raise RuntimeError("Not connected")

        # Initialize auth state in ogs_service
        success = await self._ogs_service.authenticate()
        if not success:
            raise RuntimeError("Failed to initialize OGS authentication")

        # Build authentication data based on auth method
        if self._ogs_service._using_api_key:
            # API key authentication format (per gtp2ogs):
            # {jwt: "", bot_username: ..., bot_apikey: ...}
            auth_data = {
                "jwt": "",
                "bot_username": settings.ogs_bot_username,
                "bot_apikey": self._ogs_service._access_token,
            }
            logger.info(f"{self.name}: Authenticating via API key for bot '{settings.ogs_bot_username}'")
        else:
            # OAuth token authentication format:
            # {auth: <token>, player_id: <id>}
            auth_data = {
                "auth": self._ogs_service._access_token,
                "player_id": self._ogs_service._user_id,
            }
            logger.info(f"{self.name}: Authenticating via OAuth token")

        # Use call() instead of emit() to get the response with user info
        try:
            response = await self._sio.call("authenticate", auth_data, timeout=10)
            logger.debug(f"{self.name}: Raw authentication response: {response}")

            if isinstance(response, dict):
                # API key auth returns user info in response
                # Try multiple possible field names for user ID
                user_id = (
                    response.get("id") or
                    response.get("user_id") or
                    response.get("bot_id") or
                    response.get("player_id")
                )
                username = response.get("username") or response.get("bot_username")

                if user_id:
                    self._ogs_service._user_id = user_id
                    logger.info(f"{self.name}: Authenticated as user {user_id} ({username})")
                elif "error" in response:
                    raise RuntimeError(f"Authentication failed: {response.get('error')}")
                else:
                    # Response might be empty on success for some auth types
                    # Log all keys to help debug
                    logger.warning(
                        f"{self.name}: Auth response has no recognized user_id field. "
                        f"Keys: {list(response.keys())}, Response: {response}"
                    )
            elif isinstance(response, (int, str)) and response:
                # Some auth systems might return just the user ID
                try:
                    self._ogs_service._user_id = int(response)
                    logger.info(f"{self.name}: Authenticated as user {response} (direct ID response)")
                except (ValueError, TypeError):
                    logger.warning(f"{self.name}: Unexpected auth response format: {response}")
            else:
                logger.warning(f"{self.name}: Authentication response type: {type(response)}, value: {response}")

            self._authenticated = True

        except asyncio.TimeoutError:
            raise RuntimeError("Socket authentication timed out")
        except Exception as e:
            if "error" in str(e).lower() or "fail" in str(e).lower():
                raise RuntimeError(f"Socket authentication failed: {e}")
            # If we got here without explicit error, might still be authenticated
            logger.warning(f"{self.name}: Authentication returned: {e}")
            self._authenticated = True

    async def _subscribe_to_active_games(self) -> None:
        """Subscribe to events for all active games."""
        games = await self._ogs_service.get_active_games()

        if games:
            for game in games:
                await self.subscribe_to_game(game.game_id)
            logger.info(f"{self.name}: Subscribed to {len(games)} active games")
        else:
            # No games found - this could be because:
            # 1. API key auth: REST API doesn't work with bot API keys
            # 2. No active games exist
            # 3. user_id wasn't set yet when get_active_games was called
            if self._ogs_service._user_id:
                logger.info(
                    f"{self.name}: No active games found for user {self._ogs_service._user_id}. "
                    "Will detect new games via socket notifications."
                )
            else:
                logger.warning(
                    f"{self.name}: No active games found and user_id not set. "
                    "Socket notifications may not work correctly."
                )

            # Request active games via socket notification subscription
            if self._sio:
                try:
                    # Subscribe to notifications for this user
                    await self._sio.emit("notification/connect", {
                        "player_id": self._ogs_service._user_id
                    })
                    logger.debug(f"{self.name}: Subscribed to notifications")
                except Exception as e:
                    logger.debug(f"{self.name}: Could not subscribe to notifications: {e}")

    async def subscribe_to_game(self, game_id: int) -> None:
        """Subscribe to events for a specific game."""
        if not self._sio or game_id in self._subscribed_games:
            return

        # OGS game subscription format
        await self._sio.emit("game/connect", {"game_id": game_id})
        self._subscribed_games.add(game_id)
        logger.debug(f"{self.name}: Subscribed to game {game_id}")

    async def _unsubscribe_game(self, game_id: int) -> None:
        """Unsubscribe from a game's events."""
        if not self._sio or game_id not in self._subscribed_games:
            return

        await self._sio.emit("game/disconnect", {"game_id": game_id})
        self._subscribed_games.discard(game_id)
        logger.debug(f"{self.name}: Unsubscribed from game {game_id}")

    async def _handle_game_event(self, data: Dict[str, Any]) -> None:
        """Handle a game event from OGS."""
        event_name = data.get("event", "unknown")
        game_id = data.get("game_id")

        if not game_id:
            logger.warning(f"{self.name}: Game event without game_id: {data}")
            return

        logger.debug(f"{self.name}: Game event {event_name} for game {game_id}")

        # Determine event type based on OGS event structure
        if "gamedata" in data or "width" in data:
            # This is gamedata - update the cache
            event_type = "game_data"
            gamedata = data.get("gamedata", data)
            self._ogs_service.update_game_from_socket(game_id, gamedata)
        elif "move" in data:
            event_type = "game_move"
        elif "phase" in data:
            event_type = "game_phase"
        elif "clock" in data:
            event_type = "game_clock"
        else:
            event_type = f"game_{event_name}"

        # Emit to our event system
        await self._emit_event(
            event_type=event_type,
            external_id=str(game_id),
            payload={
                "source": "ogs",
                **data
            }
        )

    async def _handle_notification(self, data: Dict[str, Any]) -> None:
        """Handle an OGS notification (challenge, yourMove, gameStarted, etc)."""
        notification_type = data.get("type", "unknown")
        logger.info(f"{self.name}: Received notification: {notification_type}")
        logger.debug(f"{self.name}: Notification data: {data}")

        if notification_type == "challenge":
            await self._emit_event(
                event_type="challenge",
                external_id=str(data.get("id", 0)),
                payload={
                    "source": "ogs",
                    **data
                }
            )
        elif notification_type == "gameStarted":
            # New game started - subscribe to it
            game_id = data.get("game", {}).get("id") or data.get("game_id")
            if game_id:
                logger.info(f"{self.name}: Game started notification for game {game_id}")
                await self.subscribe_to_game(game_id)
                await self._emit_event(
                    event_type="game_started",
                    external_id=str(game_id),
                    payload={
                        "source": "ogs",
                        **data
                    }
                )
        elif notification_type == "yourMove":
            # It's our turn in a game - subscribe if we haven't already
            game_id = data.get("game_id") or data.get("game", {}).get("id")
            if game_id:
                logger.info(f"{self.name}: Your move notification for game {game_id}")
                if game_id not in self._subscribed_games:
                    await self.subscribe_to_game(game_id)
                # Emit event so the game move handler processes it
                await self._emit_event(
                    event_type="game_move",
                    external_id=str(game_id),
                    payload={
                        "source": "ogs",
                        "game_id": game_id,
                        **data
                    }
                )
        elif notification_type == "game":
            # Generic game notification - might indicate an active game
            game_id = data.get("game_id") or data.get("id")
            if game_id and game_id not in self._subscribed_games:
                logger.info(f"{self.name}: Game notification for game {game_id}, subscribing")
                await self.subscribe_to_game(game_id)

    def to_dict(self) -> Dict[str, Any]:
        """Return listener status as a dictionary."""
        base = super().to_dict()
        base.update({
            "authenticated": self._authenticated,
            "subscribed_games": list(self._subscribed_games),
            "ogs_user_id": self._ogs_service._user_id,
            "bot_username": settings.ogs_bot_username,
        })
        return base
