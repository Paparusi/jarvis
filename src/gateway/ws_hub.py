"""WebSocketHub -- Unified WebSocket with channel-based subscriptions.

Protocol:
    Client -> {"action": "subscribe", "channels": ["company", "trading"]}
    Client -> {"action": "unsubscribe", "channels": ["trading"]}
    Client -> {"type": "ping"}
    Server -> {"channel": "company", "event": "ceo_route", "data": {...}, "ts": "..."}
    Server -> {"type": "pong"}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.utils.logging import get_logger

log = get_logger("gateway.ws_hub")

VALID_CHANNELS = frozenset({"company", "trading", "chat", "system"})


class WebSocketHub:
    """Unified WebSocket hub -- manages connections and channel subscriptions."""

    def __init__(self) -> None:
        self._connections: dict[Any, set[str]] = {}

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def connect(self, ws: Any) -> None:
        """Register a new connection (no channels subscribed yet)."""
        self._connections[ws] = set()
        log.info("ws_hub_connect", total=len(self._connections))

    def disconnect(self, ws: Any) -> None:
        """Remove a connection."""
        self._connections.pop(ws, None)
        log.info("ws_hub_disconnect", total=len(self._connections))

    def subscribe(self, ws: Any, channels: list[str]) -> list[str]:
        """Subscribe a connection to channels. Returns actually subscribed list."""
        if ws not in self._connections:
            return []
        valid = [ch for ch in channels if ch in VALID_CHANNELS]
        self._connections[ws].update(valid)
        return valid

    def unsubscribe(self, ws: Any, channels: list[str]) -> None:
        """Unsubscribe a connection from channels."""
        if ws in self._connections:
            self._connections[ws] -= set(channels)

    def get_subscriptions(self, ws: Any) -> set[str]:
        """Get channels a connection is subscribed to."""
        return self._connections.get(ws, set()).copy()

    async def broadcast(
        self, channel: str, event: str, data: dict[str, Any] | None = None
    ) -> int:
        """Broadcast an event to all subscribers of a channel. Returns count sent."""
        if channel not in VALID_CHANNELS:
            return 0

        message = {
            "channel": channel,
            "event": event,
            "data": data or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        dead: list[Any] = []
        sent = 0
        for ws, channels in self._connections.items():
            if channel in channels:
                try:
                    await ws.send_json(message)
                    sent += 1
                except Exception:
                    dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

        return sent

    async def handle_client_message(self, ws: Any, raw: dict) -> dict | None:
        """Process a message from a client. Returns response dict or None."""
        msg_type = raw.get("type")
        if msg_type == "ping":
            return {"type": "pong"}

        action = raw.get("action")
        if action == "subscribe":
            channels = raw.get("channels", [])
            subscribed = self.subscribe(ws, channels)
            return {"type": "subscribed", "channels": subscribed}

        if action == "unsubscribe":
            channels = raw.get("channels", [])
            self.unsubscribe(ws, channels)
            return {"type": "unsubscribed", "channels": channels}

        return None


# Singleton
_hub: WebSocketHub | None = None


def get_ws_hub() -> WebSocketHub:
    """Get the global WebSocketHub singleton."""
    global _hub
    if _hub is None:
        _hub = WebSocketHub()
    return _hub
