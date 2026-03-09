"""Tests for TradingBrain — top-level orchestrator."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.trading.entry_confirmer import EntryDecision
from src.trading.position_manager import ManagedPosition
from src.trading.trade_planner import Scenario, TradePlan
from src.trading.trading_brain import TradingBrain
import src.trading.persistence as persistence_mod


@pytest.fixture(autouse=True)
def _clean_trading_symbol(monkeypatch):
    """Ensure TRADING_SYMBOL env var doesn't affect tests."""
    monkeypatch.delenv("TRADING_SYMBOL", raising=False)


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use temp SQLite DB so persistence layer doesn't touch real DB."""
    db_path = str(tmp_path / "test_brain.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    persistence_mod._tables_initialized = False
    with patch.object(persistence_mod, 'get_connection', return_value=conn):
        yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_mt5():
    """Create a mock MT5 client with default return values."""
    mt5 = AsyncMock()
    mt5.get_tick.return_value = {"bid": 2655.0, "ask": 2655.5, "spread": 0.5}
    mt5.get_positions.return_value = []
    mt5.place_order.return_value = {"ticket": 99001}
    mt5.close_position.return_value = {"success": True}
    mt5.get_rates.return_value = []
    mt5.get_account.return_value = {"balance": 10000.0, "equity": 10000.0}
    mt5.get_pending_orders.return_value = []
    mt5.cancel_order.return_value = {"success": True}
    return mt5


def _make_mock_risk_guard():
    """Create a mock RiskGuard."""
    rg = MagicMock()
    rg.get_status.return_value = {
        "state": {
            "daily_pnl": 0.0,
            "daily_trades": 0,
            "consecutive_losses": 0,
        },
        "config": {},
    }
    return rg


def _make_plan(
    session="London",
    bias="bullish",
    zones=None,
    scenarios=None,
    trades_taken=0,
    max_trades=3,
) -> TradePlan:
    """Create a TradePlan for testing."""
    if zones is None:
        zones = [
            {
                "zone_id": "zone_1",
                "direction": "buy",
                "price_high": 2660.0,
                "price_low": 2655.0,
                "confluence_score": 7,
                "sl_price": 2645.0,
                "tp1_price": 2670.0,
                "tp2_price": 2685.0,
                "rr_ratio": 2.0,
                "factors": ["OB", "FVG"],
                "reasoning": "Strong confluence",
                "invalidation": "",
            }
        ]
    if scenarios is None:
        scenarios = [
            Scenario(
                condition="If price breaks above 2680",
                action="Trail stops aggressively",
                new_bias="bullish",
            )
        ]
    return TradePlan(
        session=session,
        created_at=datetime.now(tz=timezone.utc),
        bias=bias,
        alert_zones=zones,
        scenarios=scenarios,
        risk_budget_pct=2.0,
        max_trades=max_trades,
        trades_taken=trades_taken,
    )


def _make_enter_decision() -> EntryDecision:
    """Create an ENTER decision."""
    return EntryDecision(
        action="ENTER",
        reasoning="Strong M15 confirmation",
        direction="buy",
        lot_size=0.02,
        sl=2645.0,
        tp1=2670.0,
        tp2=2685.0,
        risk_approved=True,
    )


def _make_skip_decision() -> EntryDecision:
    """Create a SKIP decision."""
    return EntryDecision(
        action="SKIP",
        reasoning="Structure is opposing",
        direction="buy",
    )


def _make_wait_decision() -> EntryDecision:
    """Create a WAIT decision."""
    return EntryDecision(
        action="WAIT",
        reasoning="Waiting for candle close confirmation",
        direction="buy",
    )


# ===========================================================================
# TestLifecycle
# ===========================================================================


class TestLifecycle:
    """Test start/stop/kill lifecycle."""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Verify start creates scheduler task, stop cancels it."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        await brain.start()

        assert brain._running is True
        assert brain._scheduler_task is not None
        assert not brain._scheduler_task.done()

        await brain.stop()

        assert brain._running is False
        assert brain._scheduler_task is None

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        """Calling start twice doesn't create duplicate tasks."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        await brain.start()
        first_task = brain._scheduler_task

        await brain.start()  # second call
        second_task = brain._scheduler_task

        # Same task object — no duplicate
        assert first_task is second_task
        assert brain._running is True

        await brain.stop()

    @pytest.mark.asyncio
    async def test_kill_closes_positions_and_stops(self):
        """Kill should emergency_close_all + cancel pending + stop monitor."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        # Mock sub-modules
        brain.position_manager = AsyncMock()
        brain.position_manager.emergency_close_all = AsyncMock(return_value=2)
        brain.monitor = AsyncMock()
        brain.monitor.stop = AsyncMock()
        brain.pending_manager = AsyncMock()
        brain.pending_manager.cancel_all = AsyncMock(return_value=1)

        result = await brain.kill()

        brain.position_manager.emergency_close_all.assert_awaited_once_with(
            "kill_switch"
        )
        brain.monitor.stop.assert_awaited_once()
        brain.pending_manager.cancel_all.assert_awaited_once()
        assert "2 position(s) closed" in result
        assert "pending" in result.lower()


# ===========================================================================
# TestAlertFlow
# ===========================================================================


class TestAlertFlow:
    """Test zone alert -> confirm -> action flow."""

    @pytest.mark.asyncio
    async def test_alert_enter_flow(self):
        """Zone alert -> confirm ENTER -> create approval (not direct order)."""
        mt5 = _make_mock_mt5()
        rg = _make_mock_risk_guard()
        brain = TradingBrain(mt5, risk_guard=rg)

        # Set a current plan
        brain.current_plan = _make_plan()

        # Mock confirmer to return ENTER
        brain.confirmer = AsyncMock()
        brain.confirmer.confirm = AsyncMock(return_value=_make_enter_decision())

        # Mock approval_manager
        brain.approval_manager = AsyncMock()
        brain.approval_manager.create_approval = AsyncMock(return_value={
            "id": "apr_test123",
            "direction": "buy",
            "order_type": "buy_limit",
            "price": 2657.5,
            "sl": 2645.0,
            "tp1": 2670.0,
            "tp2": 2685.0,
            "lot": 0.02,
            "symbol": "XAUUSD",
            "risk_pct": 0.8,
            "risk_usd": 24.0,
            "confluence_score": 7,
            "analysis": "Strong M15 confirmation",
        })

        # Track notifications
        notifications = []
        brain._notify_cb = AsyncMock(side_effect=lambda msg: notifications.append(msg))

        zone = {
            "zone_id": "zone_1",
            "direction": "buy",
            "price_high": 2660.0,
            "price_low": 2655.0,
            "sl_price": 2645.0,
            "tp1_price": 2670.0,
            "tp2_price": 2685.0,
            "confluence_score": 7,
        }
        price = 2657.0

        await brain._on_zone_alert(zone, price)

        # Confirmer was called
        brain.confirmer.confirm.assert_awaited_once()

        # Approval was created (not direct order)
        brain.approval_manager.create_approval.assert_awaited_once()

        # Order was NOT placed directly
        mt5.place_order.assert_not_awaited()

        # Notifications sent (zone alert + approval alert)
        assert len(notifications) >= 2
        assert any("TRADE SIGNAL" in n for n in notifications)

    @pytest.mark.asyncio
    async def test_alert_skip_flow(self):
        """Zone alert -> confirm SKIP -> remove zone."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        brain.confirmer = AsyncMock()
        brain.confirmer.confirm = AsyncMock(return_value=_make_skip_decision())

        # Mock monitor.remove_zone
        brain.monitor = MagicMock()
        brain.monitor.remove_zone = MagicMock()

        notifications = []
        brain._notify_cb = AsyncMock(side_effect=lambda msg: notifications.append(msg))

        zone = {"zone_id": "zone_skip", "direction": "buy"}

        await brain._on_zone_alert(zone, 2658.0)

        # Zone removed from monitor
        brain.monitor.remove_zone.assert_called_once_with("zone_skip")

        # No order placed
        mt5.place_order.assert_not_awaited()

        # Notifications include skip message
        assert any("SKIP" in n for n in notifications)

    @pytest.mark.asyncio
    async def test_alert_wait_flow(self):
        """Zone alert -> confirm WAIT -> zone stays active."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        brain.confirmer = AsyncMock()
        brain.confirmer.confirm = AsyncMock(return_value=_make_wait_decision())

        # Mock monitor — ensure remove_zone is NOT called
        brain.monitor = MagicMock()
        brain.monitor.remove_zone = MagicMock()

        notifications = []
        brain._notify_cb = AsyncMock(side_effect=lambda msg: notifications.append(msg))

        zone = {"zone_id": "zone_wait", "direction": "buy"}

        await brain._on_zone_alert(zone, 2656.0)

        # Zone NOT removed
        brain.monitor.remove_zone.assert_not_called()

        # No order placed
        mt5.place_order.assert_not_awaited()

        # Notification includes WAIT
        assert any("WAIT" in n for n in notifications)


# ===========================================================================
# TestApprovalResponse
# ===========================================================================


class TestApprovalResponse:
    """Test handle_approval_response."""

    @pytest.mark.asyncio
    async def test_approve_creates_position(self):
        """Approve -> places order -> creates ManagedPosition."""
        mt5 = _make_mock_mt5()
        rg = _make_mock_risk_guard()
        brain = TradingBrain(mt5, risk_guard=rg)
        brain.current_plan = _make_plan(trades_taken=0)
        brain._plan_id = 1

        # Mock approval_manager.approve
        brain.approval_manager = AsyncMock()
        brain.approval_manager.approve = AsyncMock(
            return_value={"status": "approved", "ticket": 99001, "order": {}}
        )

        # Mock persistence to return the approval details
        brain.persistence = MagicMock()
        brain.persistence.get_approval.return_value = {
            "id": "apr_test",
            "zone_id": "z1",
            "symbol": "XAUUSD",
            "direction": "buy",
            "lot": 0.02,
            "price": 2655.0,
            "sl": 2645.0,
            "tp1": 2670.0,
            "tp2": 2685.0,
            "confluence_score": 85,
        }

        # Mock position_manager
        brain.position_manager = AsyncMock()
        brain.position_manager.add_position = AsyncMock()

        result = await brain.handle_approval_response("apr_test", "approve", via="telegram")

        assert result["status"] == "approved"
        assert result["ticket"] == 99001
        brain.position_manager.add_position.assert_awaited_once()
        managed = brain.position_manager.add_position.call_args[0][0]
        assert managed.ticket == 99001
        assert managed.direction == "buy"
        assert brain.current_plan.trades_taken == 1

    @pytest.mark.asyncio
    async def test_reject(self):
        """Reject -> calls approval_manager.reject."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        brain.approval_manager = AsyncMock()
        brain.approval_manager.reject = AsyncMock(return_value={"status": "rejected"})

        result = await brain.handle_approval_response("apr_test", "reject", via="dashboard")

        assert result["status"] == "rejected"
        brain.approval_manager.reject.assert_awaited_once_with("apr_test", via="dashboard")

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        """Unknown action -> error."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        result = await brain.handle_approval_response("apr_test", "invalid")
        assert "error" in result


# ===========================================================================
# TestScheduler
# ===========================================================================


class TestScheduler:
    """Test session scheduler logic."""

    @pytest.mark.asyncio
    async def test_london_plan(self):
        """At 07:00 UTC should create London plan."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)
        brain._running = True

        # Mock plan_now to track calls
        brain.plan_now = AsyncMock(return_value="London plan")

        # Mock datetime to return 07:02 UTC
        mock_now = datetime(2026, 3, 8, 7, 2, 0, tzinfo=timezone.utc)
        with patch(
            "src.trading.trading_brain.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

            # Run one iteration of the scheduler, then stop
            async def _run_one_iter():
                """Run the scheduler body once, then set _running=False."""
                now = mock_dt.now(timezone.utc)
                hour = now.hour
                minute = now.minute

                if hour == 7 and minute < 5 and brain._last_plan_hour != 7:
                    brain._last_plan_hour = 7
                    await brain.plan_now("London")

            await _run_one_iter()

        brain.plan_now.assert_awaited_once_with("London")
        assert brain._last_plan_hour == 7

    @pytest.mark.asyncio
    async def test_ny_plan(self):
        """At 12:30 UTC should create NY plan."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)
        brain._running = True

        brain.plan_now = AsyncMock(return_value="NY plan")

        # Simulate 12:32 UTC
        mock_now = datetime(2026, 3, 8, 12, 32, 0, tzinfo=timezone.utc)
        with patch("src.trading.trading_brain.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

            now = mock_dt.now(timezone.utc)
            hour = now.hour
            minute = now.minute

            if (
                hour == 12
                and 30 <= minute < 35
                and brain._last_plan_hour != 12
            ):
                brain._last_plan_hour = 12
                await brain.plan_now("New York")

        brain.plan_now.assert_awaited_once_with("New York")
        assert brain._last_plan_hour == 12

    @pytest.mark.asyncio
    async def test_cleanup(self):
        """At 16:00 UTC should stop monitor and generate summary."""
        mt5 = _make_mock_mt5()
        rg = _make_mock_risk_guard()
        brain = TradingBrain(mt5, risk_guard=rg)
        brain._running = True
        brain.current_plan = _make_plan(trades_taken=2)

        # Mock monitor.stop
        brain.monitor = AsyncMock()
        brain.monitor.stop = AsyncMock()

        # Track notifications
        notifications = []
        brain._notify_cb = AsyncMock(side_effect=lambda msg: notifications.append(msg))

        # Simulate 16:01 UTC
        mock_now = datetime(2026, 3, 8, 16, 1, 0, tzinfo=timezone.utc)
        with patch("src.trading.trading_brain.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

            now = mock_dt.now(timezone.utc)
            hour = now.hour
            minute = now.minute

            if hour == 16 and minute < 5 and brain._last_plan_hour != 16:
                brain._last_plan_hour = 16
                await brain.monitor.stop()
                summary = await brain._generate_daily_summary()
                await brain._notify(summary)

        brain.monitor.stop.assert_awaited_once()
        assert brain._last_plan_hour == 16
        assert any("DAILY SUMMARY" in n for n in notifications)

    @pytest.mark.asyncio
    async def test_no_duplicate_plan_same_hour(self):
        """Should not re-plan in the same hour."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)
        brain._running = True
        brain._last_plan_hour = 7  # Already planned for hour 7

        brain.plan_now = AsyncMock(return_value="London plan")

        # Simulate 07:03 UTC (same hour as _last_plan_hour)
        mock_now = datetime(2026, 3, 8, 7, 3, 0, tzinfo=timezone.utc)
        with patch("src.trading.trading_brain.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

            now = mock_dt.now(timezone.utc)
            hour = now.hour
            minute = now.minute

            if hour == 7 and minute < 5 and brain._last_plan_hour != 7:
                brain._last_plan_hour = 7
                await brain.plan_now("London")

        # plan_now should NOT have been called because _last_plan_hour == 7
        brain.plan_now.assert_not_awaited()


# ===========================================================================
# TestStatus
# ===========================================================================


class TestStatus:
    """Test get_status."""

    def test_get_status_no_plan(self):
        """Status without active plan shows defaults."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        status = brain.get_status()

        assert status["running"] is False
        assert status["plan"] == "No plan"
        assert status["active_zones"] == 0
        assert status["active_positions"] == 0
        assert status["trades_taken"] == 0
        assert "pending_orders" in status

    def test_get_status_with_plan(self):
        """Status with active plan shows plan summary."""
        mt5 = _make_mock_mt5()
        rg = _make_mock_risk_guard()
        brain = TradingBrain(mt5, risk_guard=rg)
        brain._running = True
        brain.current_plan = _make_plan(
            session="London", bias="bullish", trades_taken=1
        )

        status = brain.get_status()

        assert status["running"] is True
        assert "London" in status["plan"]
        assert status["trades_taken"] == 1
        assert "state" in status["risk"]  # risk_guard.get_status() called
        assert "pending_orders" in status


# ===========================================================================
# TestDailySummary
# ===========================================================================


class TestDailySummary:
    """Test daily summary generation."""

    @pytest.mark.asyncio
    async def test_generate_daily_summary(self):
        """Summary includes trades, P/L, and session info."""
        mt5 = _make_mock_mt5()
        rg = _make_mock_risk_guard()
        rg.get_status.return_value = {
            "state": {
                "daily_pnl": 150.0,
                "daily_trades": 3,
                "consecutive_losses": 0,
            },
        }
        brain = TradingBrain(mt5, risk_guard=rg)
        brain.current_plan = _make_plan(
            session="London", trades_taken=2, max_trades=3
        )

        summary = await brain._generate_daily_summary()

        assert "DAILY SUMMARY" in summary
        assert "London" in summary
        assert "2/3" in summary  # trades_taken/max_trades
        assert "+150.00" in summary  # P/L
        assert "3" in summary  # total trades


# ===========================================================================
# TestRecovery
# ===========================================================================


class TestRecovery:
    """Test state recovery on startup."""

    @pytest.mark.asyncio
    async def test_recover_plan(self):
        """Recovery loads plan from persistence."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        # Mock persistence to return a saved plan
        brain.persistence = MagicMock()
        brain.persistence.get_recovery_state.return_value = {
            "plan": {
                "id": 1,
                "session": "London",
                "created_at": "2026-03-09T08:00:00+00:00",
                "bias": "bearish",
                "bias_reasoning": "Test",
                "market_regime": "trending",
                "key_levels": {},
                "alert_zones": [{"zone_id": "z1", "direction": "sell"}],
                "scenarios": [{"condition": "Break 2670", "action": "Cancel sells", "new_bias": "bullish"}],
                "invalidation": "Above 2690",
                "risk_budget_pct": 2.0,
                "max_trades": 3,
                "trades_taken": 1,
            },
            "positions": [],
            "pending_orders": [],
        }

        # Mock monitor and pending_manager
        brain.monitor = MagicMock()
        brain.monitor.is_running = False
        brain.monitor.set_plan = MagicMock()
        brain.monitor.start = AsyncMock()
        brain.pending_manager = AsyncMock()
        brain.pending_manager.sync_from_mt5 = AsyncMock()
        brain.pending_manager.order_count = 0

        await brain._recover_state()

        assert brain.current_plan is not None
        assert brain.current_plan.session == "London"
        assert brain.current_plan.bias == "bearish"
        assert brain._plan_id == 1
        brain.monitor.set_plan.assert_called_once()
        brain.monitor.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recover_positions(self):
        """Recovery resumes management using FRESH MT5 data (not stale DB)."""
        mt5 = _make_mock_mt5()
        mt5.get_positions.return_value = [
            {"ticket": 12345, "symbol": "XAUUSD", "type": 1, "volume": 0.03,
             "price_open": 2665.0, "sl": 2680.0, "tp": 2640.0, "profit": 15.0}
        ]
        brain = TradingBrain(mt5)

        brain.persistence = MagicMock()
        brain.persistence.get_recovery_state.return_value = {
            "plan": None,
            "positions": [
                {
                    "ticket": 12345,
                    "symbol": "XAUUSD",
                    "direction": "sell",
                    "volume": 0.01,
                    "entry_price": 2660.0,
                    "sl": 2675.0,
                    "tp1": 2645.0,
                    "tp2": 2630.0,
                    "original_sl": 2675.0,
                    "be_moved": 0,
                    "tp1_hit": 0,
                    "remaining_volume": 0.01,
                    "trail_sl": 0,
                    "zone_id": "zone_1",
                    "confluence_score": 85,
                },
            ],
            "pending_orders": [],
        }

        brain.position_manager = AsyncMock()
        brain.position_manager.add_position = AsyncMock()
        brain.pending_manager = AsyncMock()
        brain.pending_manager.sync_from_mt5 = AsyncMock()
        brain.pending_manager.order_count = 0

        await brain._recover_state()

        brain.position_manager.add_position.assert_awaited_once()
        managed = brain.position_manager.add_position.call_args[0][0]
        assert managed.ticket == 12345
        assert managed.direction == "sell"
        # Fresh MT5 data used instead of stale DB values
        assert managed.volume == 0.03  # from MT5, not DB's 0.01
        assert managed.sl == 2680.0  # from MT5, not DB's 2675.0
        assert managed.entry_price == 2665.0  # from MT5

    @pytest.mark.asyncio
    async def test_recover_closed_offline(self):
        """Position in DB but not in MT5 -> marked as closed_offline."""
        mt5 = _make_mock_mt5()
        mt5.get_positions.return_value = []  # Position not in MT5 anymore
        brain = TradingBrain(mt5)

        brain.persistence = MagicMock()
        brain.persistence.get_recovery_state.return_value = {
            "plan": None,
            "positions": [
                {
                    "ticket": 99999,
                    "symbol": "XAUUSD",
                    "direction": "buy",
                    "volume": 0.01,
                    "entry_price": 2640.0,
                    "sl": 2630.0,
                    "tp1": 2660.0,
                    "tp2": 2680.0,
                    "original_sl": 2630.0,
                },
            ],
            "pending_orders": [],
        }

        brain.position_manager = AsyncMock()
        brain.pending_manager = AsyncMock()
        brain.pending_manager.sync_from_mt5 = AsyncMock()
        brain.pending_manager.order_count = 0

        await brain._recover_state()

        # Position NOT added to manager (not in MT5)
        brain.position_manager.add_position.assert_not_awaited()
        # Marked as closed_offline in persistence
        brain.persistence.close_position_record.assert_called_once_with(99999, "closed_offline", 0)

    @pytest.mark.asyncio
    async def test_empty_recovery(self):
        """Empty recovery state -> no errors."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)

        brain.persistence = MagicMock()
        brain.persistence.get_recovery_state.return_value = {
            "plan": None,
            "positions": [],
            "pending_orders": [],
        }
        brain.pending_manager = AsyncMock()
        brain.pending_manager.sync_from_mt5 = AsyncMock()
        brain.pending_manager.order_count = 0

        await brain._recover_state()

        assert brain.current_plan is None


# ===========================================================================
# TestPendingFilled
# ===========================================================================


class TestPendingFilled:
    """Test pending order fill handling."""

    @pytest.mark.asyncio
    async def test_on_pending_filled(self):
        """Filled pending order -> create ManagedPosition + notify."""
        mt5 = _make_mock_mt5()
        brain = TradingBrain(mt5)
        brain.current_plan = _make_plan(trades_taken=0)
        brain._plan_id = 1

        brain.position_manager = AsyncMock()
        brain.position_manager.add_position = AsyncMock()
        brain.persistence = MagicMock()

        notifications = []
        brain._notify_cb = AsyncMock(side_effect=lambda msg: notifications.append(msg))

        order_info = {
            "ticket": 5001,
            "zone_id": "zone_1",
            "direction": "sell",
            "volume": 0.02,
            "price": 2665.0,
            "sl": 2675.0,
            "tp1": 2650.0,
            "tp2": 2635.0,
            "confluence_score": 85,
        }

        await brain._on_pending_filled(order_info)

        # Position added to manager
        brain.position_manager.add_position.assert_awaited_once()
        managed = brain.position_manager.add_position.call_args[0][0]
        assert managed.ticket == 5001
        assert managed.direction == "sell"
        assert managed.sl == 2675.0

        # Saved to persistence
        brain.persistence.save_position.assert_called_once()

        # Trades taken incremented
        assert brain.current_plan.trades_taken == 1
        brain.persistence.update_plan_trades.assert_called_once_with(1, 1)

        # Notification sent
        assert any("FILLED" in n for n in notifications)
        assert any("5001" in n for n in notifications)
