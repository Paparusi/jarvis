"""Tests for TradingPersistence module."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

# We need to monkeypatch BEFORE importing persistence (since it uses module-level _tables_initialized)
import src.trading.persistence as persistence_mod
from src.trading.persistence import TradingPersistence


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@dataclass
class FakeScenario:
    condition: str = ""
    action: str = ""
    new_bias: str = "neutral"

@dataclass
class FakePlan:
    session: str = "London"
    created_at: datetime = field(default_factory=lambda: datetime(2026, 3, 9, 8, 0, 0, tzinfo=timezone.utc))
    bias: str = "bearish"
    bias_reasoning: str = "Test reasoning"
    market_regime: str = "trending"
    key_levels: dict = field(default_factory=dict)
    alert_zones: list = field(default_factory=list)
    scenarios: list = field(default_factory=list)
    invalidation: str = "Test invalidation"
    risk_budget_pct: float = 2.0
    max_trades: int = 3
    trades_taken: int = 0

@dataclass
class FakePosition:
    ticket: int = 12345
    zone_id: str = "zone_1"
    symbol: str = "XAUUSD"
    direction: str = "sell"
    volume: float = 0.01
    entry_price: float = 2660.0
    sl: float = 2675.0
    tp1: float = 2645.0
    tp2: float = 2630.0
    original_sl: float = 2675.0
    be_moved: bool = False
    tp1_hit: bool = False
    remaining_volume: float = 0.01
    trail_sl: float = 0.0
    confluence_score: int = 85
    opened_at: datetime = field(default_factory=lambda: datetime(2026, 3, 9, 8, 30, 0, tzinfo=timezone.utc))


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use temp SQLite DB for all tests."""
    db_path = str(tmp_path / "test_jarvis.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Reset module-level flag
    persistence_mod._tables_initialized = False

    with patch.object(persistence_mod, 'get_connection', return_value=conn):
        yield conn

    conn.close()


@pytest.fixture
def store():
    return TradingPersistence()


# ---------------------------------------------------------------------------
# TestSavePlan
# ---------------------------------------------------------------------------

class TestSavePlan:
    def test_save_and_load(self, store):
        plan = FakePlan(scenarios=[FakeScenario(condition="Break 2670", action="Cancel sells", new_bias="bullish")])
        plan_id = store.save_plan(plan)
        assert plan_id > 0

        loaded = store.load_active_plan()
        assert loaded is not None
        assert loaded["session"] == "London"
        assert loaded["bias"] == "bearish"
        assert loaded["bias_reasoning"] == "Test reasoning"
        assert loaded["market_regime"] == "trending"
        assert len(loaded["scenarios"]) == 1
        assert loaded["scenarios"][0]["condition"] == "Break 2670"

    def test_deactivates_previous(self, store):
        plan1 = FakePlan(session="London")
        plan2 = FakePlan(session="New York")
        id1 = store.save_plan(plan1)
        id2 = store.save_plan(plan2)

        loaded = store.load_active_plan()
        assert loaded["session"] == "New York"
        assert loaded["id"] == id2

    def test_no_active_returns_none(self, store):
        loaded = store.load_active_plan()
        assert loaded is None


class TestUpdatePlan:
    def test_update_trades_taken(self, store):
        plan = FakePlan()
        plan_id = store.save_plan(plan)
        store.update_plan_trades(plan_id, 2)
        loaded = store.load_active_plan()
        assert loaded["trades_taken"] == 2

    def test_deactivate(self, store):
        plan = FakePlan()
        plan_id = store.save_plan(plan)
        store.deactivate_plan(plan_id)
        loaded = store.load_active_plan()
        assert loaded is None


class TestPositions:
    def test_save_and_load(self, store):
        pos = FakePosition()
        store.save_position(pos)
        loaded = store.load_open_positions()
        assert len(loaded) == 1
        assert loaded[0]["ticket"] == 12345
        assert loaded[0]["direction"] == "sell"
        assert loaded[0]["entry_price"] == 2660.0

    def test_update_fields(self, store):
        pos = FakePosition()
        store.save_position(pos)
        store.update_position(12345, sl=2665.0, be_moved=1)
        loaded = store.load_open_positions()
        assert loaded[0]["sl"] == 2665.0
        assert loaded[0]["be_moved"] == 1

    def test_close_record(self, store):
        pos = FakePosition()
        store.save_position(pos)
        store.close_position_record(12345, "tp1_hit", 15.50)
        loaded = store.load_open_positions()
        assert len(loaded) == 0  # closed positions not returned

    def test_load_only_open(self, store):
        pos1 = FakePosition(ticket=111)
        pos2 = FakePosition(ticket=222)
        store.save_position(pos1)
        store.save_position(pos2)
        store.close_position_record(111, "sl_hit", -10.0)
        loaded = store.load_open_positions()
        assert len(loaded) == 1
        assert loaded[0]["ticket"] == 222


class TestPending:
    def test_save_and_load(self, store):
        store.save_pending(1001, "zone_1", "XAUUSD", "sell_limit", 0.01, 2665.0, sl=2675.0, tp=2650.0)
        loaded = store.load_pending_orders()
        assert len(loaded) == 1
        assert loaded[0]["ticket"] == 1001
        assert loaded[0]["order_type"] == "sell_limit"

    def test_update_status(self, store):
        store.save_pending(1001, "zone_1", "XAUUSD", "sell_limit", 0.01, 2665.0)
        store.update_pending_status(1001, "filled")
        loaded = store.load_pending_orders()
        assert len(loaded) == 0  # filled not returned

    def test_cancel_all(self, store):
        store.save_pending(1001, "zone_1", "XAUUSD", "sell_limit", 0.01, 2665.0)
        store.save_pending(1002, "zone_2", "XAUUSD", "buy_limit", 0.01, 2640.0)
        count = store.cancel_all_pending()
        assert count == 2
        loaded = store.load_pending_orders()
        assert len(loaded) == 0

    def test_load_only_pending(self, store):
        store.save_pending(1001, "zone_1", "XAUUSD", "sell_limit", 0.01, 2665.0)
        store.save_pending(1002, "zone_2", "XAUUSD", "buy_limit", 0.01, 2640.0)
        store.update_pending_status(1001, "cancelled")
        loaded = store.load_pending_orders()
        assert len(loaded) == 1
        assert loaded[0]["ticket"] == 1002


class TestRecovery:
    def test_full_recovery_state(self, store):
        plan = FakePlan()
        store.save_plan(plan)
        pos = FakePosition()
        store.save_position(pos)
        store.save_pending(1001, "zone_1", "XAUUSD", "sell_limit", 0.01, 2665.0)

        state = store.get_recovery_state()
        assert state["plan"] is not None
        assert state["plan"]["session"] == "London"
        assert len(state["positions"]) == 1
        assert len(state["pending_orders"]) == 1

    def test_empty_recovery(self, store):
        state = store.get_recovery_state()
        assert state["plan"] is None
        assert state["positions"] == []
        assert state["pending_orders"] == []


class TestTradeApprovals:
    def test_save_and_get_approval(self, store):
        approval = {
            "id": "apr_test1", "plan_id": 1, "zone_id": "z1",
            "symbol": "XAUUSD", "direction": "buy", "order_type": "buy_limit",
            "price": 2340.50, "sl": 2338.00, "tp1": 2344.00, "tp2": 2347.00,
            "lot": 0.03, "risk_pct": 0.8, "risk_usd": 24.0,
            "confluence_score": 85, "analysis": "Bullish setup",
            "smc_summary": "OB + FVG", "status": "pending",
            "created_at": "2026-03-09T12:00:00",
        }
        store.save_approval(approval)
        result = store.get_approval("apr_test1")
        assert result is not None
        assert result["symbol"] == "XAUUSD"
        assert result["lot"] == 0.03

    def test_get_pending_approvals(self, store):
        for i in range(3):
            store.save_approval({
                "id": f"apr_{i}", "symbol": "XAUUSD", "direction": "buy",
                "order_type": "buy_limit", "price": 2340, "sl": 2338,
                "tp1": 2344, "tp2": 2347, "lot": 0.03, "risk_pct": 0.8,
                "risk_usd": 24, "status": "pending", "created_at": f"2026-03-09T12:0{i}:00",
            })
        store.update_approval("apr_0", status="approved")
        pending = store.get_pending_approvals()
        assert len(pending) == 2

    def test_update_approval(self, store):
        store.save_approval({
            "id": "apr_upd", "symbol": "XAUUSD", "direction": "buy",
            "order_type": "buy_limit", "price": 2340, "sl": 2338,
            "tp1": 2344, "tp2": 2347, "lot": 0.03, "risk_pct": 0.8,
            "risk_usd": 24, "status": "pending", "created_at": "2026-03-09T12:00:00",
        })
        store.update_approval("apr_upd", status="approved",
                                     responded_at="2026-03-09T12:01:00",
                                     responded_via="telegram", order_ticket=12345)
        result = store.get_approval("apr_upd")
        assert result["status"] == "approved"
        assert result["order_ticket"] == 12345

    def test_get_approval_not_found(self, store):
        result = store.get_approval("apr_nonexistent")
        assert result is None


class TestTableInit:
    def test_tables_created(self, store, _temp_db):
        """Tables are created on first access."""
        # Just saving should work (tables auto-created)
        plan = FakePlan()
        plan_id = store.save_plan(plan)
        assert plan_id > 0
