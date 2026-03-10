"""Event Bus — Internal pub/sub for decoupled communication between layers.

Events flow through the bus, allowing components to react without direct coupling.
For example, DataCollector subscribes to 'response_sent' instead of being called directly.

Usage:
    bus = EventBus()
    bus.subscribe("message_received", my_handler)
    await bus.publish("message_received", {"user_id": "123", "text": "hello"})
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from src.utils.logging import get_logger

log = get_logger("gateway.event_bus")


class EventType(str, Enum):
    """Standard event types in JARVIS."""

    # Gateway events
    MESSAGE_RECEIVED = "message_received"
    RESPONSE_SENT = "response_sent"

    # Intelligence events
    ROUTE_STARTED = "route_started"
    ROUTE_COMPLETED = "route_completed"
    LOCAL_ESCALATED = "local_escalated"

    # Tool events
    TOOL_CALLED = "tool_called"
    TOOL_COMPLETED = "tool_completed"

    # Skill events
    SKILL_MATCHED = "skill_matched"
    SKILL_CREATED = "skill_created"

    # Memory events
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"

    # Swarm events
    SWARM_STARTED = "swarm_started"
    SWARM_COMPLETED = "swarm_completed"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"

    # Company events
    COMPANY_CEO_ROUTE = "company_ceo_route"
    COMPANY_DEPT_ASSIGN = "company_dept_assign"
    COMPANY_DEPT_DIRECT = "company_dept_direct"
    COMPANY_WORKER_BUSY = "company_worker_busy"
    COMPANY_WORKER_DONE = "company_worker_done"
    COMPANY_WORKER_FAIL = "company_worker_fail"

    # System events
    ERROR = "error"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"


@dataclass
class Event:
    """An event published to the bus."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = ""


# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Async event bus with publish/subscribe pattern."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._event_history: list[Event] = []
        self._max_history = 500

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe a handler to an event type."""
        self._subscribers[event_type].append(handler)
        log.debug("event_subscribed", event_type=event_type, handler=handler.__name__)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a handler from an event type."""
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event_type: str, data: dict[str, Any] | None = None, source: str = "") -> None:
        """Publish an event to all subscribers.

        Handlers run concurrently. Errors in one handler don't affect others.
        """
        event = Event(type=event_type, data=data or {}, source=source)

        # Store in history
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]

        handlers = self._subscribers.get(event_type, [])
        if not handlers:
            return

        # Run handlers concurrently
        tasks = []
        for handler in handlers:
            tasks.append(self._safe_call(handler, event))

        if tasks:
            await asyncio.gather(*tasks)

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:
        """Call a handler safely — catch and log errors."""
        try:
            await handler(event)
        except Exception as e:
            log.error(
                "event_handler_error",
                event_type=event.type,
                handler=handler.__name__,
                error=str(e),
            )

    def get_recent_events(self, event_type: str | None = None, limit: int = 20) -> list[Event]:
        """Get recent events, optionally filtered by type."""
        events = self._event_history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]

    def get_stats(self) -> dict[str, Any]:
        """Get event bus statistics."""
        type_counts: dict[str, int] = defaultdict(int)
        for event in self._event_history:
            type_counts[event.type] += 1

        return {
            "total_events": len(self._event_history),
            "subscribers": {k: len(v) for k, v in self._subscribers.items()},
            "event_counts": dict(type_counts),
        }


# Singleton instance
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global event bus singleton."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
