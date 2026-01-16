"""
Base class for external event listeners.

Event listeners connect to external services (like OGS) and receive
real-time events that may trigger AI responses.
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable, Awaitable, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class EventListenerState(str, Enum):
    """Connection state of an event listener."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass
class ConnectionStats:
    """Statistics about the listener's connection."""
    connected_at: Optional[datetime] = None
    disconnected_at: Optional[datetime] = None
    reconnect_attempts: int = 0
    total_events_received: int = 0
    last_event_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None


class BaseEventListener(ABC):
    """
    Abstract base class for external event listeners.

    Provides common functionality for:
    - Connection lifecycle management (startup, shutdown)
    - Automatic reconnection with exponential backoff
    - Event routing to handlers
    - Connection state tracking
    """

    # Reconnection settings
    INITIAL_RECONNECT_DELAY = 1.0  # seconds
    MAX_RECONNECT_DELAY = 300.0  # 5 minutes
    RECONNECT_BACKOFF_FACTOR = 2.0

    # Startup retry settings
    MAX_STARTUP_RETRIES = 5  # Max connection attempts during startup
    STARTUP_RETRY_DELAY = 2.0  # seconds between startup retries

    def __init__(self, name: str, entity_id: str):
        """
        Initialize the event listener.

        Args:
            name: Human-readable name for logging
            entity_id: The AI entity this listener is associated with
        """
        self.name = name
        self.entity_id = entity_id
        self._state = EventListenerState.DISCONNECTED
        self._stats = ConnectionStats()
        self._should_run = False
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
        self._reconnect_task: Optional[asyncio.Task] = None
        self._event_handler: Optional[Callable[[str, str, Dict[str, Any]], Awaitable[None]]] = None
        self._startup_failed = False  # True if startup retries exhausted

    @property
    def state(self) -> EventListenerState:
        """Get the current connection state."""
        return self._state

    @property
    def stats(self) -> ConnectionStats:
        """Get connection statistics."""
        return self._stats

    @property
    def is_connected(self) -> bool:
        """Check if the listener is connected."""
        return self._state == EventListenerState.CONNECTED

    @property
    def startup_failed(self) -> bool:
        """Check if startup connection attempts were exhausted."""
        return self._startup_failed

    def set_event_handler(
        self,
        handler: Callable[[str, str, Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """
        Set the handler for received events.

        The handler receives:
        - event_type: Type of event (e.g., "game_move")
        - external_id: ID of the external resource (e.g., game ID)
        - payload: Event data
        """
        self._event_handler = handler

    async def start(self) -> bool:
        """
        Start the event listener with startup retries.

        During startup, will retry connection up to MAX_STARTUP_RETRIES times.
        If all startup attempts fail, the listener is marked as failed and
        no background reconnection is scheduled.

        Returns:
            True if connected successfully, False if startup retries exhausted
        """
        if self._should_run:
            logger.warning(f"{self.name}: Already running")
            return self.is_connected

        logger.info(f"{self.name}: Starting event listener for entity {self.entity_id}")
        self._should_run = True
        self._startup_failed = False
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY

        # Startup retry loop
        retry_delay = self.STARTUP_RETRY_DELAY
        for attempt in range(1, self.MAX_STARTUP_RETRIES + 1):
            self._state = EventListenerState.CONNECTING
            self._stats.reconnect_attempts = attempt - 1

            try:
                await self._connect()
                self._state = EventListenerState.CONNECTED
                self._stats.connected_at = datetime.utcnow()
                self._stats.reconnect_attempts = 0
                logger.info(f"{self.name}: Connected successfully")
                return True
            except Exception as e:
                logger.error(
                    f"{self.name}: Connection attempt {attempt}/{self.MAX_STARTUP_RETRIES} failed: {e}"
                )
                self._stats.last_error = str(e)
                self._stats.last_error_at = datetime.utcnow()

                if attempt < self.MAX_STARTUP_RETRIES:
                    logger.info(f"{self.name}: Retrying in {retry_delay:.1f}s...")
                    await asyncio.sleep(retry_delay)
                    # Exponential backoff for startup retries
                    retry_delay = min(retry_delay * self.RECONNECT_BACKOFF_FACTOR, 30.0)

        # All startup retries exhausted
        logger.error(
            f"{self.name}: All {self.MAX_STARTUP_RETRIES} startup connection attempts failed. "
            "Disabling listener."
        )
        self._state = EventListenerState.ERROR
        self._startup_failed = True
        self._should_run = False
        return False

    async def stop(self) -> None:
        """Stop the event listener."""
        logger.info(f"{self.name}: Stopping event listener")
        self._should_run = False
        self._state = EventListenerState.STOPPED

        # Cancel reconnection if pending
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        try:
            await self._disconnect()
        except Exception as e:
            logger.error(f"{self.name}: Error during disconnect: {e}")

        self._stats.disconnected_at = datetime.utcnow()
        logger.info(f"{self.name}: Stopped")

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if not self._should_run:
            return

        self._state = EventListenerState.RECONNECTING
        self._stats.reconnect_attempts += 1

        logger.info(
            f"{self.name}: Scheduling reconnect in {self._reconnect_delay:.1f}s "
            f"(attempt {self._stats.reconnect_attempts})"
        )

        self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Attempt to reconnect after a delay."""
        await asyncio.sleep(self._reconnect_delay)

        if not self._should_run:
            return

        # Increase delay for next attempt (exponential backoff)
        self._reconnect_delay = min(
            self._reconnect_delay * self.RECONNECT_BACKOFF_FACTOR,
            self.MAX_RECONNECT_DELAY
        )

        try:
            self._state = EventListenerState.CONNECTING
            await self._connect()
            self._state = EventListenerState.CONNECTED
            self._stats.connected_at = datetime.utcnow()
            self._reconnect_delay = self.INITIAL_RECONNECT_DELAY  # Reset on success
            logger.info(f"{self.name}: Reconnected successfully")
        except Exception as e:
            logger.error(f"{self.name}: Reconnection failed: {e}")
            self._state = EventListenerState.ERROR
            self._stats.last_error = str(e)
            self._stats.last_error_at = datetime.utcnow()
            # Try again
            self._schedule_reconnect()

    async def _emit_event(
        self,
        event_type: str,
        external_id: str,
        payload: Dict[str, Any]
    ) -> None:
        """
        Emit an event to the registered handler.

        Called by subclasses when an event is received.
        """
        self._stats.total_events_received += 1
        self._stats.last_event_at = datetime.utcnow()

        if self._event_handler:
            try:
                await self._event_handler(event_type, external_id, payload)
            except Exception as e:
                logger.error(
                    f"{self.name}: Error in event handler for {event_type}: {e}",
                    exc_info=True
                )
        else:
            logger.warning(f"{self.name}: No event handler registered, dropping event {event_type}")

    def _on_disconnect(self) -> None:
        """
        Called when the connection is lost unexpectedly.

        Subclasses should call this when they detect a disconnection.
        """
        if not self._should_run:
            return

        logger.warning(f"{self.name}: Connection lost, will attempt to reconnect")
        self._stats.disconnected_at = datetime.utcnow()
        self._schedule_reconnect()

    @abstractmethod
    async def _connect(self) -> None:
        """
        Establish connection to the external service.

        Subclasses must implement this method.
        Should raise an exception if connection fails.
        """
        pass

    @abstractmethod
    async def _disconnect(self) -> None:
        """
        Disconnect from the external service.

        Subclasses must implement this method.
        Should clean up any resources.
        """
        pass

    def to_dict(self) -> Dict[str, Any]:
        """Return listener status as a dictionary."""
        return {
            "name": self.name,
            "entity_id": self.entity_id,
            "state": self._state.value,
            "startup_failed": self._startup_failed,
            "connected_at": self._stats.connected_at.isoformat() if self._stats.connected_at else None,
            "disconnected_at": self._stats.disconnected_at.isoformat() if self._stats.disconnected_at else None,
            "reconnect_attempts": self._stats.reconnect_attempts,
            "total_events_received": self._stats.total_events_received,
            "last_event_at": self._stats.last_event_at.isoformat() if self._stats.last_event_at else None,
            "last_error": self._stats.last_error,
            "last_error_at": self._stats.last_error_at.isoformat() if self._stats.last_error_at else None,
        }
