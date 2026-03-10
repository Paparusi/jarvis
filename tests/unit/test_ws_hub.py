"""Tests for WebSocketHub."""

import pytest

from src.gateway.ws_hub import VALID_CHANNELS, WebSocketHub, get_ws_hub


class MockWS:
    """Fake WebSocket for testing."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data: dict) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(data)


class TestConnect:
    def setup_method(self) -> None:
        self.hub = WebSocketHub()

    def test_connect_adds(self) -> None:
        ws = MockWS()
        self.hub.connect(ws)
        assert self.hub.connection_count == 1

    def test_disconnect_removes(self) -> None:
        ws = MockWS()
        self.hub.connect(ws)
        self.hub.disconnect(ws)
        assert self.hub.connection_count == 0

    def test_disconnect_unknown_ok(self) -> None:
        ws = MockWS()
        self.hub.disconnect(ws)
        assert self.hub.connection_count == 0

    def test_connection_count(self) -> None:
        ws1, ws2, ws3 = MockWS(), MockWS(), MockWS()
        self.hub.connect(ws1)
        self.hub.connect(ws2)
        self.hub.connect(ws3)
        assert self.hub.connection_count == 3
        self.hub.disconnect(ws2)
        assert self.hub.connection_count == 2


class TestSubscribe:
    def setup_method(self) -> None:
        self.hub = WebSocketHub()
        self.ws = MockWS()
        self.hub.connect(self.ws)

    def test_subscribe_valid(self) -> None:
        result = self.hub.subscribe(self.ws, ["company"])
        assert result == ["company"]

    def test_subscribe_invalid_channel(self) -> None:
        result = self.hub.subscribe(self.ws, ["bogus", "nonsense"])
        assert result == []

    def test_subscribe_multiple(self) -> None:
        result = self.hub.subscribe(self.ws, ["company", "trading", "bogus"])
        assert set(result) == {"company", "trading"}

    def test_subscribe_not_connected(self) -> None:
        stranger = MockWS()
        result = self.hub.subscribe(stranger, ["company"])
        assert result == []

    def test_unsubscribe(self) -> None:
        self.hub.subscribe(self.ws, ["company", "trading"])
        self.hub.unsubscribe(self.ws, ["trading"])
        subs = self.hub.get_subscriptions(self.ws)
        assert subs == {"company"}

    def test_unsubscribe_not_connected(self) -> None:
        stranger = MockWS()
        self.hub.unsubscribe(stranger, ["company"])

    def test_get_subscriptions(self) -> None:
        self.hub.subscribe(self.ws, ["company", "chat"])
        subs = self.hub.get_subscriptions(self.ws)
        assert subs == {"company", "chat"}

    def test_get_subscriptions_unknown_ws(self) -> None:
        stranger = MockWS()
        subs = self.hub.get_subscriptions(stranger)
        assert subs == set()

    def test_get_subscriptions_returns_copy(self) -> None:
        self.hub.subscribe(self.ws, ["company"])
        subs = self.hub.get_subscriptions(self.ws)
        subs.add("hacked")
        assert self.hub.get_subscriptions(self.ws) == {"company"}


class TestBroadcast:
    def setup_method(self) -> None:
        self.hub = WebSocketHub()

    async def test_broadcast_to_subscribers(self) -> None:
        ws1, ws2 = MockWS(), MockWS()
        self.hub.connect(ws1)
        self.hub.connect(ws2)
        self.hub.subscribe(ws1, ["company"])
        self.hub.subscribe(ws2, ["company"])

        sent = await self.hub.broadcast("company", "ceo_route", {"msg": "hi"})
        assert sent == 2
        assert ws1.sent[0]["channel"] == "company"
        assert ws1.sent[0]["event"] == "ceo_route"
        assert ws1.sent[0]["data"] == {"msg": "hi"}
        assert "ts" in ws1.sent[0]

    async def test_broadcast_skips_unsubscribed(self) -> None:
        ws1, ws2 = MockWS(), MockWS()
        self.hub.connect(ws1)
        self.hub.connect(ws2)
        self.hub.subscribe(ws1, ["company"])
        self.hub.subscribe(ws2, ["trading"])

        sent = await self.hub.broadcast("company", "update", {})
        assert sent == 1
        assert len(ws1.sent) == 1
        assert len(ws2.sent) == 0

    async def test_broadcast_invalid_channel(self) -> None:
        ws = MockWS()
        self.hub.connect(ws)
        self.hub.subscribe(ws, ["company"])

        sent = await self.hub.broadcast("bogus", "event", {})
        assert sent == 0

    async def test_broadcast_removes_dead(self) -> None:
        ws_alive = MockWS()
        ws_dead = MockWS()
        ws_dead.closed = True

        self.hub.connect(ws_alive)
        self.hub.connect(ws_dead)
        self.hub.subscribe(ws_alive, ["company"])
        self.hub.subscribe(ws_dead, ["company"])

        sent = await self.hub.broadcast("company", "event", {})
        assert sent == 1
        assert self.hub.connection_count == 1

    async def test_broadcast_returns_count(self) -> None:
        for _ in range(5):
            ws = MockWS()
            self.hub.connect(ws)
            self.hub.subscribe(ws, ["system"])

        sent = await self.hub.broadcast("system", "ping", {})
        assert sent == 5

    async def test_broadcast_empty_data(self) -> None:
        ws = MockWS()
        self.hub.connect(ws)
        self.hub.subscribe(ws, ["chat"])

        sent = await self.hub.broadcast("chat", "msg")
        assert sent == 1
        assert ws.sent[0]["data"] == {}

    async def test_broadcast_no_subscribers(self) -> None:
        sent = await self.hub.broadcast("company", "event", {"x": 1})
        assert sent == 0


class TestHandleMessage:
    def setup_method(self) -> None:
        self.hub = WebSocketHub()
        self.ws = MockWS()
        self.hub.connect(self.ws)

    async def test_ping_pong(self) -> None:
        result = await self.hub.handle_client_message(self.ws, {"type": "ping"})
        assert result == {"type": "pong"}

    async def test_subscribe_action(self) -> None:
        result = await self.hub.handle_client_message(
            self.ws, {"action": "subscribe", "channels": ["company", "trading"]}
        )
        assert result == {"type": "subscribed", "channels": ["company", "trading"]}
        assert self.hub.get_subscriptions(self.ws) == {"company", "trading"}

    async def test_unsubscribe_action(self) -> None:
        self.hub.subscribe(self.ws, ["company", "trading"])
        result = await self.hub.handle_client_message(
            self.ws, {"action": "unsubscribe", "channels": ["trading"]}
        )
        assert result == {"type": "unsubscribed", "channels": ["trading"]}
        assert self.hub.get_subscriptions(self.ws) == {"company"}

    async def test_unknown_returns_none(self) -> None:
        result = await self.hub.handle_client_message(self.ws, {"action": "explode"})
        assert result is None

    async def test_empty_message_returns_none(self) -> None:
        result = await self.hub.handle_client_message(self.ws, {})
        assert result is None


class TestSingleton:
    def test_get_ws_hub_returns_same(self) -> None:
        import src.gateway.ws_hub as mod

        mod._hub = None
        hub1 = get_ws_hub()
        hub2 = get_ws_hub()
        assert hub1 is hub2
        mod._hub = None


class TestValidChannels:
    def test_valid_channels_are_frozenset(self) -> None:
        assert isinstance(VALID_CHANNELS, frozenset)

    def test_expected_channels(self) -> None:
        assert VALID_CHANNELS == {"company", "trading", "chat", "system"}
