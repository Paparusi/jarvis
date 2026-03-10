"""Tests for PendingOrderManager module."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.trading.pending_orders import PendingOrderManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mock_mt5(current_price=2655.0):
    mt5 = AsyncMock()
    mt5.get_tick.return_value = {"bid": current_price, "ask": current_price + 0.5}
    mt5.place_pending.return_value = {"retcode": 10009, "order": 0, "volume": 0.01}
    mt5.cancel_order.return_value = {"retcode": 10009}
    mt5.get_pending_orders.return_value = []
    return mt5


def _mock_risk_guard(max_lot=0.10):
    rg = MagicMock()
    rg.config = MagicMock()
    rg.config.max_lot_size = max_lot
    return rg


def _mock_persistence():
    return MagicMock()


def _sample_zone(zone_id="zone_1", direction="sell", price_high=2665.0, price_low=2660.0,
                 sl_price=2675.0, tp1_price=2645.0, tp2_price=2630.0, confluence_score=85):
    return {
        "zone_id": zone_id,
        "direction": direction,
        "price_high": price_high,
        "price_low": price_low,
        "sl_price": sl_price,
        "tp1_price": tp1_price,
        "tp2_price": tp2_price,
        "confluence_score": confluence_score,
    }


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.delenv("TRADING_SYMBOL", raising=False)
    mt5 = _mock_mt5()
    rg = _mock_risk_guard()
    persist = _mock_persistence()
    mgr = PendingOrderManager(mt5, rg, persist)
    return mgr, mt5, rg, persist


# ---------------------------------------------------------------------------
# TestPlaceZoneOrders
# ---------------------------------------------------------------------------

class TestPlaceZoneOrders:
    @pytest.mark.asyncio
    async def test_place_sell_limit(self, manager):
        """Sell zone above current price -> sell_limit."""
        mgr, mt5, rg, persist = manager
        # Zone mid = 2662.5, current = 2655, zone above -> sell_limit
        mt5.place_pending.return_value = {"retcode": 10009, "order": 1001}
        zone = _sample_zone(direction="sell", price_high=2665.0, price_low=2660.0)

        placed = await mgr.place_zone_orders([zone], equity=10000.0, risk_pct=1.0)

        assert len(placed) == 1
        assert placed[0] == 1001
        assert mgr.order_count == 1
        mt5.place_pending.assert_called_once()
        call_kwargs = mt5.place_pending.call_args[1]
        assert call_kwargs["order_type"] == "sell_limit"

    @pytest.mark.asyncio
    async def test_place_buy_limit(self, manager):
        """Buy zone below current price -> buy_limit."""
        mgr, mt5, rg, persist = manager
        mt5.place_pending.return_value = {"retcode": 10009, "order": 1002}
        zone = _sample_zone(zone_id="zone_2", direction="buy", price_high=2645.0, price_low=2640.0,
                           sl_price=2630.0, tp1_price=2660.0)

        placed = await mgr.place_zone_orders([zone], equity=10000.0)

        assert len(placed) == 1
        call_kwargs = mt5.place_pending.call_args[1]
        assert call_kwargs["order_type"] == "buy_limit"

    @pytest.mark.asyncio
    async def test_buy_stop_above_price(self, manager):
        """Buy zone above current price -> buy_stop."""
        mgr, mt5, rg, persist = manager
        mt5.place_pending.return_value = {"retcode": 10009, "order": 1003}
        zone = _sample_zone(zone_id="zone_3", direction="buy", price_high=2670.0, price_low=2665.0,
                           sl_price=2655.0, tp1_price=2685.0)

        placed = await mgr.place_zone_orders([zone], equity=10000.0)

        assert len(placed) == 1
        call_kwargs = mt5.place_pending.call_args[1]
        assert call_kwargs["order_type"] == "buy_stop"

    @pytest.mark.asyncio
    async def test_skip_bad_zone(self, manager):
        """Zone with zero prices is skipped."""
        mgr, mt5, rg, persist = manager
        zone = {"zone_id": "bad", "direction": "sell", "price_high": 0, "price_low": 0, "sl_price": 0}

        placed = await mgr.place_zone_orders([zone], equity=10000.0)

        assert len(placed) == 0
        mt5.place_pending.assert_not_called()

    @pytest.mark.asyncio
    async def test_persistence_called(self, manager):
        """Persistence save_pending is called on successful placement."""
        mgr, mt5, rg, persist = manager
        mt5.place_pending.return_value = {"retcode": 10009, "order": 1004}
        zone = _sample_zone()

        await mgr.place_zone_orders([zone], equity=10000.0)

        persist.save_pending.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_fetch_failure(self, manager):
        """If tick fetch fails, return empty list."""
        mgr, mt5, rg, persist = manager
        mt5.get_tick.side_effect = Exception("Connection refused")
        zone = _sample_zone()

        placed = await mgr.place_zone_orders([zone], equity=10000.0)

        assert len(placed) == 0


class TestCancelZone:
    @pytest.mark.asyncio
    async def test_cancel_by_zone_id(self, manager):
        mgr, mt5, rg, persist = manager
        # Set up internal state as if order was placed
        mgr._orders[1001] = {"ticket": 1001, "zone_id": "zone_1"}
        mgr._zone_tickets["zone_1"] = 1001

        result = await mgr.cancel_zone("zone_1")

        assert result is True
        assert mgr.order_count == 0
        mt5.cancel_order.assert_called_once_with(1001)
        persist.update_pending_status.assert_called_once_with(1001, "cancelled")

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, manager):
        mgr, mt5, rg, persist = manager

        result = await mgr.cancel_zone("nonexistent")

        assert result is False
        mt5.cancel_order.assert_not_called()


class TestCheckFills:
    @pytest.mark.asyncio
    async def test_detect_filled_order(self, manager):
        """Order in local state but not in MT5 -> filled."""
        mgr, mt5, rg, persist = manager
        mgr._orders[1001] = {"ticket": 1001, "zone_id": "zone_1", "direction": "sell"}
        mgr._zone_tickets["zone_1"] = 1001
        # MT5 returns empty pending list -> order was filled
        mt5.get_pending_orders.return_value = []

        filled = await mgr.check_fills()

        assert len(filled) == 1
        assert filled[0]["ticket"] == 1001
        assert mgr.order_count == 0
        persist.update_pending_status.assert_called_once_with(1001, "filled")

    @pytest.mark.asyncio
    async def test_no_fills(self, manager):
        """Order still in MT5 -> not filled."""
        mgr, mt5, rg, persist = manager
        mgr._orders[1001] = {"ticket": 1001, "zone_id": "zone_1"}
        mt5.get_pending_orders.return_value = [{"ticket": 1001}]

        filled = await mgr.check_fills()

        assert len(filled) == 0
        assert mgr.order_count == 1

    @pytest.mark.asyncio
    async def test_empty_orders(self, manager):
        """No local orders -> empty result, no MT5 call."""
        mgr, mt5, rg, persist = manager

        filled = await mgr.check_fills()

        assert len(filled) == 0
        mt5.get_pending_orders.assert_not_called()


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancel_all_orders(self, manager):
        mgr, mt5, rg, persist = manager
        mgr._orders[1001] = {"ticket": 1001, "zone_id": "zone_1"}
        mgr._orders[1002] = {"ticket": 1002, "zone_id": "zone_2"}
        mgr._zone_tickets["zone_1"] = 1001
        mgr._zone_tickets["zone_2"] = 1002

        count = await mgr.cancel_all()

        assert count == 2
        assert mgr.order_count == 0


class TestSync:
    @pytest.mark.asyncio
    async def test_sync_from_mt5(self, manager):
        """Sync adopts JARVIS orders from MT5."""
        mgr, mt5, rg, persist = manager
        mt5.get_pending_orders.return_value = [
            {"ticket": 2001, "comment": "J-zone_1", "type": 3, "volume_current": 0.01, "price_open": 2665.0, "sl": 2675.0},
        ]

        await mgr.sync_from_mt5()

        assert mgr.order_count == 1
        assert 2001 in mgr.active_orders

    @pytest.mark.asyncio
    async def test_ignore_non_jarvis_orders(self, manager):
        """Non-JARVIS orders (no J- prefix) are ignored."""
        mgr, mt5, rg, persist = manager
        mt5.get_pending_orders.return_value = [
            {"ticket": 2002, "comment": "Manual order", "type": 2, "volume_current": 0.05, "price_open": 2640.0, "sl": 0},
        ]

        await mgr.sync_from_mt5()

        assert mgr.order_count == 0
