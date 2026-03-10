"""Tests for PriceMonitor — zone alerts, invalidation, plan management, loop callbacks."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.trading.price_monitor import PriceMonitor


# ── Helpers ──────────────────────────────────────────────────────────

def _make_zone(
    zone_id: str = "Z1",
    price_low: float = 2663.0,
    price_high: float = 2667.0,
    direction: str = "BUY",
    invalidation: str = "",
) -> dict:
    """Create a minimal zone dict for testing."""
    return {
        "zone_id": zone_id,
        "direction": direction,
        "price_low": price_low,
        "price_high": price_high,
        "confluence_score": 0.8,
        "sl_price": price_low - 10,
        "tp1_price": price_high + 20,
        "tp2_price": price_high + 40,
        "invalidation": invalidation,
    }


def _make_mt5_mock(bid: float = 2665.0) -> AsyncMock:
    """Create a mock MT5Client that returns a fixed bid price."""
    mt5 = AsyncMock()
    mt5.get_tick = AsyncMock(return_value={"bid": bid, "ask": bid + 0.5})
    return mt5


# ── TestPriceInZone ──────────────────────────────────────────────────


class TestPriceInZone:
    """Tests for _price_in_zone static check."""

    def setup_method(self) -> None:
        self.monitor = PriceMonitor(_make_mt5_mock(), poll_interval=10)
        self.zone = _make_zone(price_low=2663.0, price_high=2667.0)

    def test_price_inside(self) -> None:
        """Price 2665 is inside [2663, 2667]."""
        assert self.monitor._price_in_zone(2665.0, self.zone) is True

    def test_price_outside_above(self) -> None:
        """Price 2670 is above zone [2663, 2667]."""
        assert self.monitor._price_in_zone(2670.0, self.zone) is False

    def test_price_outside_below(self) -> None:
        """Price 2660 is below zone [2663, 2667]."""
        assert self.monitor._price_in_zone(2660.0, self.zone) is False

    def test_price_on_boundary_low(self) -> None:
        """Price exactly at price_low (2663) is inclusive."""
        assert self.monitor._price_in_zone(2663.0, self.zone) is True

    def test_price_on_boundary_high(self) -> None:
        """Price exactly at price_high (2667) is inclusive."""
        assert self.monitor._price_in_zone(2667.0, self.zone) is True


# ── TestCheckInvalidation ────────────────────────────────────────────


class TestCheckInvalidation:
    """Tests for _check_invalidation string parsing."""

    def setup_method(self) -> None:
        self.monitor = PriceMonitor(_make_mt5_mock(), poll_interval=10)

    def test_invalidation_above(self) -> None:
        """Price 2673 invalidates 'above 2672.00'."""
        text = "Zone invalid if price closes above 2672.00"
        assert self.monitor._check_invalidation(2673.0, text) is True

    def test_invalidation_below(self) -> None:
        """Price 2637 invalidates 'below 2638.00'."""
        text = "Zone invalid if price closes below 2638.00"
        assert self.monitor._check_invalidation(2637.0, text) is True

    def test_invalidation_not_met(self) -> None:
        """Price 2665 does NOT invalidate 'above 2672.00'."""
        text = "Zone invalid if price closes above 2672.00"
        assert self.monitor._check_invalidation(2665.0, text) is False

    def test_invalidation_unparseable(self) -> None:
        """Random text without above/below pattern returns False."""
        assert self.monitor._check_invalidation(2665.0, "some random text") is False


# ── TestSetPlan ──────────────────────────────────────────────────────


class TestSetPlan:
    """Tests for set_plan loading and clearing."""

    def setup_method(self) -> None:
        self.monitor = PriceMonitor(_make_mt5_mock(), poll_interval=10)

    def test_set_plan_loads_zones(self) -> None:
        """set_plan with 2 zones populates active_zones."""
        plan = {
            "alert_zones": [_make_zone("Z1"), _make_zone("Z2")],
            "scenarios": [{"condition": "c", "action": "a", "new_bias": "bullish"}],
        }
        self.monitor.set_plan(plan)
        assert len(self.monitor.active_zones) == 2
        assert self.monitor.active_zones[0]["zone_id"] == "Z1"
        assert self.monitor.active_zones[1]["zone_id"] == "Z2"

    def test_set_plan_clears_previous(self) -> None:
        """Calling set_plan twice replaces zones entirely."""
        plan1 = {"alert_zones": [_make_zone("Z1"), _make_zone("Z2")], "scenarios": []}
        plan2 = {"alert_zones": [_make_zone("Z3")], "scenarios": []}
        self.monitor.set_plan(plan1)
        assert len(self.monitor.active_zones) == 2
        self.monitor.set_plan(plan2)
        assert len(self.monitor.active_zones) == 1
        assert self.monitor.active_zones[0]["zone_id"] == "Z3"


# ── TestZoneManagement ───────────────────────────────────────────────


class TestZoneManagement:
    """Tests for add_zone / remove_zone."""

    def setup_method(self) -> None:
        self.monitor = PriceMonitor(_make_mt5_mock(), poll_interval=10)

    def test_add_zone(self) -> None:
        """add_zone increases active_zones by 1."""
        assert len(self.monitor.active_zones) == 0
        self.monitor.add_zone(_make_zone("Z1"))
        assert len(self.monitor.active_zones) == 1
        assert self.monitor.active_zones[0]["zone_id"] == "Z1"

    def test_remove_zone(self) -> None:
        """remove_zone removes the zone from active_zones."""
        self.monitor.add_zone(_make_zone("Z1"))
        self.monitor.add_zone(_make_zone("Z2"))
        assert len(self.monitor.active_zones) == 2
        self.monitor.remove_zone("Z1")
        assert len(self.monitor.active_zones) == 1
        assert self.monitor.active_zones[0]["zone_id"] == "Z2"


# ── TestLoop ─────────────────────────────────────────────────────────


class TestLoop:
    """Tests for the background polling loop and callback firing."""

    @pytest.mark.asyncio
    async def test_alert_callback_fired(self) -> None:
        """When price is inside a zone, alert callback fires with zone and price."""
        mt5 = _make_mt5_mock(bid=2665.0)
        monitor = PriceMonitor(mt5, poll_interval=0)

        zone = _make_zone("Z1", price_low=2663.0, price_high=2667.0)
        monitor.set_plan({"alert_zones": [zone], "scenarios": []})

        alert_cb = AsyncMock()
        monitor.on_alert(alert_cb)

        await monitor.start()
        assert monitor.is_running is True
        await asyncio.sleep(0.1)
        await monitor.stop()

        alert_cb.assert_called()
        call_args = alert_cb.call_args
        called_zone = call_args[0][0]
        called_price = call_args[0][1]
        assert called_zone["zone_id"] == "Z1"
        assert called_price == 2665.0

    @pytest.mark.asyncio
    async def test_invalidation_removes_zone(self) -> None:
        """When price exceeds invalidation level, zone is removed and callback fires."""
        # Price 2675 > invalidation above 2672 => invalidated
        mt5 = _make_mt5_mock(bid=2675.0)
        monitor = PriceMonitor(mt5, poll_interval=0)

        zone = _make_zone(
            "Z1",
            price_low=2663.0,
            price_high=2667.0,
            invalidation="Zone invalid if price closes above 2672.00",
        )
        monitor.set_plan({"alert_zones": [zone], "scenarios": []})

        invalidate_cb = AsyncMock()
        monitor.on_invalidate(invalidate_cb)

        await monitor.start()
        await asyncio.sleep(0.1)
        await monitor.stop()

        # Zone should be removed
        assert len(monitor.active_zones) == 0

        # Invalidation callback should have been called
        invalidate_cb.assert_called()
        call_args = invalidate_cb.call_args
        called_zone = call_args[0][0]
        called_reason = call_args[0][1]
        assert called_zone["zone_id"] == "Z1"
        assert "2675" in called_reason
        assert "invalidated" in called_reason.lower()

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self) -> None:
        """Monitor can start and stop cleanly."""
        mt5 = _make_mt5_mock(bid=2650.0)
        monitor = PriceMonitor(mt5, poll_interval=0)

        assert monitor.is_running is False
        await monitor.start()
        assert monitor.is_running is True

        # Double start should be no-op
        await monitor.start()
        assert monitor.is_running is True

        await monitor.stop()
        assert monitor.is_running is False

    @pytest.mark.asyncio
    async def test_zone_not_retriggered(self) -> None:
        """Once a zone triggers an alert, it should not trigger again."""
        mt5 = _make_mt5_mock(bid=2665.0)
        monitor = PriceMonitor(mt5, poll_interval=0)

        zone = _make_zone("Z1", price_low=2663.0, price_high=2667.0)
        monitor.set_plan({"alert_zones": [zone], "scenarios": []})

        alert_cb = AsyncMock()
        monitor.on_alert(alert_cb)

        await monitor.start()
        await asyncio.sleep(0.15)  # multiple poll cycles
        await monitor.stop()

        # Should only be called once despite multiple poll cycles
        assert alert_cb.call_count == 1
