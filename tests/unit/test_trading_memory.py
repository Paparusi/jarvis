"""Tests for TradingMemory module."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import src.trading.trading_memory as tm_mod
from src.trading.trading_memory import TradingMemory


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use temp SQLite DB for all tests."""
    db_path = str(tmp_path / "test_tm.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tm_mod._tm_initialized = False

    # Also init trade_plans + managed_positions for cross-reference queries
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session TEXT, created_at TEXT, bias TEXT, bias_reasoning TEXT,
            market_regime TEXT, key_levels TEXT DEFAULT '{}',
            alert_zones TEXT DEFAULT '[]', scenarios TEXT DEFAULT '[]',
            invalidation TEXT DEFAULT '', risk_budget_pct REAL DEFAULT 2.0,
            max_trades INTEGER DEFAULT 3, trades_taken INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS managed_positions (
            ticket INTEGER PRIMARY KEY, zone_id TEXT, symbol TEXT,
            direction TEXT, volume REAL, entry_price REAL, sl REAL,
            tp1 REAL, tp2 REAL, original_sl REAL, be_moved INTEGER DEFAULT 0,
            tp1_hit INTEGER DEFAULT 0, remaining_volume REAL, trail_sl REAL DEFAULT 0,
            confluence_score INTEGER DEFAULT 0, opened_at TEXT, closed_at TEXT,
            close_reason TEXT, pnl REAL
        );
    """)
    conn.commit()

    with patch.object(tm_mod, "get_connection", return_value=conn):
        yield conn
    conn.close()


@pytest.fixture
def tm():
    return TradingMemory()


class TestLogEvent:
    def test_basic_log(self, tm):
        eid = tm.log_event(event_type="plan", summary="Bearish bias, 3 zones")
        assert eid > 0

    def test_all_fields(self, tm):
        eid = tm.log_event(
            event_type="position_open",
            summary="OPEN SELL XAUUSD @ 2660",
            details={"direction": "sell", "volume": 0.01},
            symbol="XAUUSD", session="London", price_at_event=2660.0,
            plan_id=1, ticket=12345, source="brain",
        )
        assert eid > 0

    def test_incrementing_ids(self, tm):
        id1 = tm.log_event("plan", "First")
        id2 = tm.log_event("plan", "Second")
        assert id2 > id1


class TestGetRecentEvents:
    def test_returns_recent(self, tm):
        tm.log_event("plan", "Event 1")
        tm.log_event("zone_alert", "Event 2")
        events = tm.get_recent_events(hours=1)
        assert len(events) == 2

    def test_filter_by_type(self, tm):
        tm.log_event("plan", "Plan 1")
        tm.log_event("zone_alert", "Zone 1")
        tm.log_event("plan", "Plan 2")
        events = tm.get_recent_events(event_types=["plan"])
        assert len(events) == 2
        assert all(e["event_type"] == "plan" for e in events)

    def test_limit(self, tm):
        for i in range(10):
            tm.log_event("plan", f"Event {i}")
        events = tm.get_recent_events(limit=3)
        assert len(events) == 3

    def test_empty(self, tm):
        events = tm.get_recent_events()
        assert events == []


class TestGetEventsForPlan:
    def test_returns_plan_events(self, tm):
        tm.log_event("plan", "Plan 1 event", plan_id=1)
        tm.log_event("zone_alert", "Zone for plan 1", plan_id=1)
        tm.log_event("plan", "Plan 2 event", plan_id=2)
        events = tm.get_events_for_plan(1)
        assert len(events) == 2


class TestGetEventsForTicket:
    def test_returns_ticket_events(self, tm):
        tm.log_event("position_open", "Open", ticket=12345)
        tm.log_event("position_close", "Close", ticket=12345)
        tm.log_event("position_open", "Other", ticket=99999)
        events = tm.get_events_for_ticket(12345)
        assert len(events) == 2


class TestGetTodayPnl:
    def test_calculates_pnl(self, tm):
        tm.log_event("position_close", "Win", details={"pnl": 25.50})
        tm.log_event("position_close", "Loss", details={"pnl": -10.00})
        tm.log_event("position_close", "Win", details={"pnl": 15.00})
        pnl = tm.get_today_pnl()
        assert pnl["total_pnl"] == 30.50
        assert pnl["trades"] == 3
        assert pnl["wins"] == 2
        assert pnl["win_rate"] == pytest.approx(66.7, abs=0.1)

    def test_empty_pnl(self, tm):
        pnl = tm.get_today_pnl()
        assert pnl["total_pnl"] == 0
        assert pnl["trades"] == 0


class TestBuildContext:
    def test_empty_context(self, tm):
        ctx = tm.build_context()
        assert ctx == ""

    def test_includes_events(self, tm):
        tm.log_event("plan", "Bearish bias", price_at_event=2660)
        ctx = tm.build_context()
        assert "PLAN" in ctx
        assert "Bearish bias" in ctx

    def test_includes_pnl(self, tm):
        tm.log_event("position_close", "Win", details={"pnl": 25.0})
        ctx = tm.build_context()
        assert "25.00" in ctx
        assert "Today P&L" in ctx


class TestCleanup:
    def test_cleanup_old(self, tm, _temp_db):
        conn = _temp_db
        # Log a new event first to trigger table creation
        tm.log_event("plan", "New event")
        # Now insert an old event directly via raw SQL
        old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        conn.execute(
            "INSERT INTO trading_events (event_type, summary, created_at) VALUES (?, ?, ?)",
            ("plan", "Old event", old_time),
        )
        conn.commit()
        deleted = tm.cleanup_old_events(days=30)
        assert deleted == 1
        remaining = tm.get_recent_events(hours=24*365)
        assert len(remaining) == 1
        assert remaining[0]["summary"] == "New event"
