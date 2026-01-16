"""
Event Service for managing external event listeners.

This service coordinates external event listeners (like OGS for Go games),
handles event routing, and manages the lifecycle of all listeners.
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.models.external_event import ExternalEvent, EventStatus
from app.services.event_listeners.base import BaseEventListener, EventListenerState

logger = logging.getLogger(__name__)


class EventService:
    """
    Central service for managing external event listeners.

    Responsibilities:
    - Manage lifecycle of event listeners (start/stop)
    - Route incoming events to appropriate handlers
    - Track events in database for auditing/debugging
    - Provide health status for all listeners
    """

    def __init__(self):
        self._listeners: Dict[str, BaseEventListener] = {}
        self._event_handlers: Dict[str, Any] = {}  # source -> handler
        self._running = False
        self._startup_complete = False

    @property
    def is_running(self) -> bool:
        """Check if the event service is running."""
        return self._running

    def register_listener(self, listener_id: str, listener: BaseEventListener) -> None:
        """
        Register an event listener.

        Args:
            listener_id: Unique identifier for the listener
            listener: The listener instance
        """
        if listener_id in self._listeners:
            logger.warning(f"EventService: Replacing existing listener {listener_id}")

        self._listeners[listener_id] = listener
        # Set up event routing
        listener.set_event_handler(self._handle_event)
        logger.info(f"EventService: Registered listener {listener_id} ({listener.name})")

    def unregister_listener(self, listener_id: str) -> None:
        """
        Unregister an event listener.

        Args:
            listener_id: ID of the listener to remove
        """
        if listener_id in self._listeners:
            del self._listeners[listener_id]
            logger.info(f"EventService: Unregistered listener {listener_id}")

    def register_event_handler(self, source: str, handler: Any) -> None:
        """
        Register a handler for events from a specific source.

        The handler should be an async callable that processes events.

        Args:
            source: Event source (e.g., "ogs")
            handler: Handler object with process_event method
        """
        self._event_handlers[source] = handler
        logger.info(f"EventService: Registered event handler for source '{source}'")

    async def start(self) -> None:
        """Start the event service and all registered listeners."""
        if self._running:
            logger.warning("EventService: Already running")
            return

        listener_count = len(self._listeners)
        logger.info(f"EventService: Starting with {listener_count} listener(s)")
        self._running = True

        # Start all listeners concurrently
        # Note: We copy items() to list to avoid modification during iteration
        start_tasks = [
            self._start_listener(listener_id, listener)
            for listener_id, listener in list(self._listeners.items())
        ]

        if start_tasks:
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            # Count successes (True values, excluding exceptions)
            succeeded = sum(1 for r in results if r is True)
            failed = listener_count - succeeded
            if failed > 0:
                logger.warning(
                    f"EventService: {failed} listener(s) failed to start and were disabled"
                )
            logger.info(f"EventService: {succeeded}/{listener_count} listener(s) started successfully")

        self._startup_complete = True
        logger.info("EventService: Startup complete")

    async def _start_listener(self, listener_id: str, listener: BaseEventListener) -> bool:
        """
        Start a single listener with error handling.

        Returns:
            True if listener started successfully, False if startup failed
        """
        try:
            success = await listener.start()
            if success:
                logger.info(f"EventService: Started listener {listener_id}")
                return True
            else:
                # Startup retries exhausted - unregister the listener
                logger.warning(
                    f"EventService: Listener {listener_id} failed to connect after retries. "
                    "Unregistering and continuing without it."
                )
                self.unregister_listener(listener_id)
                # Also remove the event handler for this source
                if listener_id in self._event_handlers:
                    del self._event_handlers[listener_id]
                return False
        except Exception as e:
            logger.error(f"EventService: Failed to start listener {listener_id}: {e}")
            return False

    async def stop(self) -> None:
        """Stop the event service and all listeners."""
        if not self._running:
            return

        logger.info("EventService: Stopping all listeners")
        self._running = False

        # Stop all listeners concurrently
        stop_tasks = [
            self._stop_listener(listener_id, listener)
            for listener_id, listener in self._listeners.items()
        ]

        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        logger.info("EventService: Shutdown complete")

    async def _stop_listener(self, listener_id: str, listener: BaseEventListener) -> None:
        """Stop a single listener with error handling."""
        try:
            await listener.stop()
            logger.info(f"EventService: Stopped listener {listener_id}")
        except Exception as e:
            logger.error(f"EventService: Error stopping listener {listener_id}: {e}")

    async def _handle_event(
        self,
        event_type: str,
        external_id: str,
        payload: Dict[str, Any]
    ) -> None:
        """
        Handle an incoming event from a listener.

        This method is called by listeners when they receive events.
        It routes the event to the appropriate handler and tracks it in the database.
        """
        # Determine the source from the event type or payload
        source = payload.get("source", "unknown")

        logger.info(f"EventService: Received event {event_type} from {source} for {external_id}")

        # Create event record in database
        event_id = await self._record_event(
            source=source,
            event_type=event_type,
            external_id=external_id,
            payload=payload
        )

        # Route to appropriate handler
        handler = self._event_handlers.get(source)
        if handler:
            try:
                await self._process_event(handler, event_id, event_type, external_id, payload)
            except Exception as e:
                logger.error(f"EventService: Error processing event {event_id}: {e}", exc_info=True)
                await self._mark_event_failed(event_id, str(e))
        else:
            logger.warning(f"EventService: No handler registered for source '{source}'")
            await self._mark_event_skipped(event_id, f"No handler for source '{source}'")

    async def _record_event(
        self,
        source: str,
        event_type: str,
        external_id: str,
        payload: Dict[str, Any]
    ) -> str:
        """Record an event in the database and return its ID."""
        async with async_session_maker() as session:
            event = ExternalEvent(
                source=source,
                event_type=event_type,
                external_id=external_id,
                payload=payload,
                status=EventStatus.PENDING
            )
            session.add(event)
            await session.commit()
            return event.id

    async def _process_event(
        self,
        handler: Any,
        event_id: str,
        event_type: str,
        external_id: str,
        payload: Dict[str, Any]
    ) -> None:
        """Process an event through its handler."""
        # Mark as processing
        async with async_session_maker() as session:
            result = await session.execute(
                select(ExternalEvent).where(ExternalEvent.id == event_id)
            )
            event = result.scalar_one_or_none()
            if event:
                event.status = EventStatus.PROCESSING
                await session.commit()

        # Call the handler
        response_data = await handler.process_event(event_type, external_id, payload)

        # Mark as completed
        async with async_session_maker() as session:
            result = await session.execute(
                select(ExternalEvent).where(ExternalEvent.id == event_id)
            )
            event = result.scalar_one_or_none()
            if event:
                event.status = EventStatus.COMPLETED
                event.processed_at = datetime.utcnow()
                if response_data:
                    event.response_message_id = response_data.get("message_id")
                    event.conversation_id = response_data.get("conversation_id")
                    event.entity_id = response_data.get("entity_id")
                await session.commit()

    async def _mark_event_failed(self, event_id: str, error_message: str) -> None:
        """Mark an event as failed."""
        async with async_session_maker() as session:
            result = await session.execute(
                select(ExternalEvent).where(ExternalEvent.id == event_id)
            )
            event = result.scalar_one_or_none()
            if event:
                event.status = EventStatus.FAILED
                event.error_message = error_message
                event.processed_at = datetime.utcnow()
                await session.commit()

    async def _mark_event_skipped(self, event_id: str, reason: str) -> None:
        """Mark an event as skipped."""
        async with async_session_maker() as session:
            result = await session.execute(
                select(ExternalEvent).where(ExternalEvent.id == event_id)
            )
            event = result.scalar_one_or_none()
            if event:
                event.status = EventStatus.SKIPPED
                event.error_message = reason
                event.processed_at = datetime.utcnow()
                await session.commit()

    def get_status(self) -> Dict[str, Any]:
        """Get the status of the event service and all listeners."""
        return {
            "running": self._running,
            "startup_complete": self._startup_complete,
            "listeners": {
                listener_id: listener.to_dict()
                for listener_id, listener in self._listeners.items()
            },
            "registered_handlers": list(self._event_handlers.keys()),
        }

    def get_listener(self, listener_id: str) -> Optional[BaseEventListener]:
        """Get a specific listener by ID."""
        return self._listeners.get(listener_id)

    def get_all_listeners(self) -> List[BaseEventListener]:
        """Get all registered listeners."""
        return list(self._listeners.values())

    async def get_recent_events(
        self,
        limit: int = 50,
        source: Optional[str] = None,
        external_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get recent events from the database."""
        async with async_session_maker() as session:
            query = select(ExternalEvent).order_by(ExternalEvent.created_at.desc()).limit(limit)

            if source:
                query = query.where(ExternalEvent.source == source)
            if external_id:
                query = query.where(ExternalEvent.external_id == external_id)

            result = await session.execute(query)
            events = result.scalars().all()

            return [
                {
                    "id": e.id,
                    "created_at": e.created_at.isoformat(),
                    "processed_at": e.processed_at.isoformat() if e.processed_at else None,
                    "source": e.source,
                    "event_type": e.event_type,
                    "external_id": e.external_id,
                    "status": e.status.value,
                    "error_message": e.error_message,
                    "conversation_id": e.conversation_id,
                    "entity_id": e.entity_id,
                }
                for e in events
            ]


# Singleton instance
event_service = EventService()
