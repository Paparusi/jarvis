"""Tests for Phase 2 Trading: risk, journal, multi-TF, signals, advanced tools."""

from __future__ import annotations

import sqlite3

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.base import ToolRegistry


# ═══════════════════════════════════════════════════════════════════════
# Risk Calculator Tests
# ═══════════════════════════════════════════════════════════════════════


class TestRiskCalculator:

    def test_position_sizing_basic(self):
        from src.trading.risk import calculate_position_size

        pos = calculate_position_size(balance=10000, risk_pct=1.0, sl_distance=5.0)
        assert pos.risk_amount == 100.0
        assert pos.sl_pips == 500.0  # 5.0 / 0.01
        assert pos.lot_size > 0
        assert pos.lot_size == 0.2  # 100 / (500 * 1.0) = 0.2

    def test_position_sizing_zero_inputs(self):
        from src.trading.risk import calculate_position_size

        pos = calculate_position_size(balance=0, risk_pct=1.0, sl_distance=5.0)
        assert pos.lot_size == 0.0
        assert pos.risk_amount == 0.0

        pos2 = calculate_position_size(balance=10000, risk_pct=0, sl_distance=5.0)
        assert pos2.lot_size == 0.0

        pos3 = calculate_position_size(balance=10000, risk_pct=1.0, sl_distance=0)
        assert pos3.lot_size == 0.0

    def test_rr_ratio_buy(self):
        from src.trading.risk import calculate_risk_reward

        rr = calculate_risk_reward(entry=2650.0, sl=2645.0, tp=2660.0)
        assert rr.side == "buy"
        assert rr.sl_distance == 5.0
        assert rr.tp_distance == 10.0
        assert rr.rr_ratio == 2.0

    def test_rr_ratio_sell(self):
        from src.trading.risk import calculate_risk_reward

        rr = calculate_risk_reward(entry=2650.0, sl=2655.0, tp=2640.0)
        assert rr.side == "sell"
        assert rr.sl_distance == 5.0
        assert rr.tp_distance == 10.0
        assert rr.rr_ratio == 2.0

    def test_pip_value(self):
        from src.trading.risk import calculate_pip_value

        assert calculate_pip_value(1.0, "XAUUSD") == 1.0
        assert calculate_pip_value(0.01, "XAUUSD") == 0.01
        assert calculate_pip_value(0.1, "XAUUSD") == 0.1

    def test_max_position_allowed(self):
        from src.trading.risk import check_max_position

        result = check_max_position(free_margin=10000, lot_size=0.1)
        assert result["allowed"] is True

    def test_max_position_rejected(self):
        from src.trading.risk import check_max_position

        result = check_max_position(free_margin=100, lot_size=5.0)
        assert result["allowed"] is False

    def test_drawdown_tracker(self):
        from src.trading.risk import DrawdownTracker

        tracker = DrawdownTracker()
        tracker.update(10000)
        tracker.update(10500)
        dd = tracker.update(10200)
        assert dd["current_drawdown"] == 300
        assert dd["peak_equity"] == 10500
        assert dd["max_drawdown"] == 300
        assert dd["max_drawdown_pct"] > 0

        stats = tracker.get_stats()
        assert stats["samples"] == 3
        assert stats["peak_equity"] == 10500


# ═══════════════════════════════════════════════════════════════════════
# Trade Journal Tests
# ═══════════════════════════════════════════════════════════════════════


def _create_memory_db():
    """Create an in-memory SQLite DB with row_factory."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class TestTradeJournal:

    def test_log_and_retrieve(self):
        from src.trading.journal import _init_journal_tables, log_trade, get_trade

        conn = _create_memory_db()
        _init_journal_tables(conn)

        with patch("src.trading.journal.get_connection", return_value=conn), \
             patch("src.trading.journal._journal_initialized", False):
            row_id = log_trade({
                "ticket": 12345,
                "symbol": "XAUUSD",
                "side": "buy",
                "volume": 0.01,
                "entry_price": 2650.0,
                "profit": 15.0,
                "notes": "test trade",
            })
            assert row_id > 0

            trade = get_trade(12345)
            assert trade is not None
            assert trade["symbol"] == "XAUUSD"
            assert trade["side"] == "buy"
            assert trade["profit"] == 15.0

    def test_stats_calculation(self):
        from src.trading.journal import _init_journal_tables, calculate_stats
        from datetime import datetime, timezone

        conn = _create_memory_db()
        _init_journal_tables(conn)

        now = datetime.now(tz=timezone.utc).isoformat()
        for i, profit in enumerate([10, 20, -5, -15, 30]):
            conn.execute(
                """INSERT INTO trade_journal
                   (ticket, symbol, side, volume, entry_price, profit, closed_at)
                   VALUES (?, 'XAUUSD', 'buy', 0.01, 2650, ?, ?)""",
                (100 + i, profit, now),
            )
        conn.commit()

        with patch("src.trading.journal.get_connection", return_value=conn), \
             patch("src.trading.journal._journal_initialized", False):
            stats = calculate_stats(days=30)
            assert stats["total_trades"] == 5
            assert stats["wins"] == 3
            assert stats["losses"] == 2
            assert stats["win_rate"] == 60.0
            assert stats["total_pnl"] == 40.0

    def test_sync_from_mt5(self):
        from src.trading.journal import _init_journal_tables, sync_from_mt5

        conn = _create_memory_db()
        _init_journal_tables(conn)

        deals = [
            {"ticket": 1001, "symbol": "XAUUSD", "type": 0, "volume": 0.01,
             "price": 2650.0, "profit": 10.0, "time": 1709900000},
            {"ticket": 1002, "symbol": "XAUUSD", "type": 1, "volume": 0.02,
             "price": 2660.0, "profit": -5.0, "time": 1709903600},
        ]

        with patch("src.trading.journal.get_connection", return_value=conn), \
             patch("src.trading.journal._journal_initialized", False):
            result = sync_from_mt5(deals)
            assert result["synced"] == 2
            assert result["skipped"] == 0
            assert result["errors"] == 0

    def test_sync_skip_duplicates(self):
        from src.trading.journal import _init_journal_tables, sync_from_mt5

        conn = _create_memory_db()
        _init_journal_tables(conn)

        deals = [{"ticket": 2001, "symbol": "XAUUSD", "type": 0,
                   "volume": 0.01, "price": 2650.0, "profit": 5.0, "time": 1709900000}]

        with patch("src.trading.journal.get_connection", return_value=conn), \
             patch("src.trading.journal._journal_initialized", False):
            r1 = sync_from_mt5(deals)
            assert r1["synced"] == 1

            r2 = sync_from_mt5(deals)
            assert r2["synced"] == 0
            assert r2["skipped"] == 1


# ═══════════════════════════════════════════════════════════════════════
# Multi-Timeframe Analysis Tests
# ═══════════════════════════════════════════════════════════════════════


class TestMultiTimeframe:

    def _make_candles(self, n=100, base=2650.0, trend=1.0):
        return [
            {"time": 1709900000 + i * 3600, "open": base + i * trend,
             "high": base + i * trend + 5, "low": base + i * trend - 5,
             "close": base + i * trend + 2}
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_alignment_all_bullish(self):
        from src.trading.multi_timeframe import analyze_multi_timeframe

        client = MagicMock()
        candles = self._make_candles(100, trend=1.0)
        client.get_rates = AsyncMock(return_value=candles)

        result = await analyze_multi_timeframe(client, "XAUUSD", ["H1", "H4"])
        assert "error" not in result
        assert result["alignment_score"] >= 0.5
        assert len(result["per_timeframe"]) == 2

    @pytest.mark.asyncio
    async def test_divergence_detection(self):
        from src.trading.multi_timeframe import analyze_multi_timeframe

        client = MagicMock()
        bull_candles = self._make_candles(100, trend=1.0)
        bear_candles = self._make_candles(100, trend=-1.0)

        async def mock_rates(symbol, tf, count):
            return bull_candles if tf == "H1" else bear_candles

        client.get_rates = AsyncMock(side_effect=mock_rates)
        result = await analyze_multi_timeframe(client, "XAUUSD", ["H1", "H4"])
        assert isinstance(result.get("divergences"), list)

    def test_weight_values(self):
        from src.trading.multi_timeframe import _TIMEFRAME_WEIGHTS

        assert _TIMEFRAME_WEIGHTS["D1"] > _TIMEFRAME_WEIGHTS["H4"]
        assert _TIMEFRAME_WEIGHTS["H4"] > _TIMEFRAME_WEIGHTS["H1"]
        assert _TIMEFRAME_WEIGHTS["H1"] > _TIMEFRAME_WEIGHTS["M15"]
        total = sum(_TIMEFRAME_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_empty_data_error(self):
        from src.trading.multi_timeframe import analyze_multi_timeframe

        client = MagicMock()
        client.get_rates = AsyncMock(return_value=[])
        result = await analyze_multi_timeframe(client, "XAUUSD")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_partial_failure_graceful(self):
        from src.trading.multi_timeframe import analyze_multi_timeframe

        client = MagicMock()
        candles = self._make_candles(100, trend=0.5)

        async def mock_rates(symbol, tf, count):
            if tf == "D1":
                raise Exception("timeout")
            return candles

        client.get_rates = AsyncMock(side_effect=mock_rates)
        result = await analyze_multi_timeframe(client, "XAUUSD", ["H1", "H4", "D1"])
        assert "error" not in result
        assert len(result["errors"]) == 1
        assert "D1" in result["errors"][0]


# ═══════════════════════════════════════════════════════════════════════
# Signal Scoring Tests
# ═══════════════════════════════════════════════════════════════════════


class TestSignalScoring:

    def _make_multi_tf(self, signal="Bullish", trend="Uptrend",
                       alignment=1.0, weighted=0.8):
        analysis = {
            "current_price": 2650.0, "signal_summary": signal, "trend": trend,
            "rsi_14": 55.0, "macd_histogram": 0.5, "macd_line": 1.0,
            "macd_signal": 0.5, "atr_14": 10.0,
            "bollinger_upper": 2660.0, "bollinger_lower": 2640.0,
            "bollinger_middle": 2650.0,
            "support": [2640.0, 2630.0], "resistance": [2660.0, 2670.0],
        }
        return {
            "per_timeframe": {"H1": analysis, "H4": analysis},
            "alignment_score": alignment,
            "weighted_score": weighted,
            "dominant_signal": signal,
            "divergences": [],
            "session": {
                "session": "London+NY", "volatility": "very-high",
                "is_dead_zone": False,
            },
        }

    def test_all_components_present(self):
        from src.trading.signals import score_signal

        result = score_signal(self._make_multi_tf())
        assert "score" in result
        assert "breakdown" in result
        assert "recommendation" in result
        assert "confidence" in result
        for key in ["trend_alignment", "momentum", "volatility",
                     "support_resistance", "session_quality", "calendar_risk",
                     "smc"]:
            assert key in result["breakdown"]

    def test_dead_zone_zero_session_score(self):
        from src.trading.signals import score_signal

        multi = self._make_multi_tf()
        multi["session"] = {
            "session": "Dead Zone", "volatility": "very-low",
            "is_dead_zone": True,
        }
        result = score_signal(multi)
        assert result["breakdown"]["session_quality"] == 0

    def test_score_in_range(self):
        from src.trading.signals import score_signal

        result = score_signal(self._make_multi_tf())
        assert 0 <= result["score"] <= 100

    def test_strong_signal_high_score(self):
        from src.trading.signals import score_signal

        result = score_signal(
            self._make_multi_tf(alignment=1.0, weighted=0.9),
            calendar_warnings=[],
        )
        assert result["score"] >= 40

    def test_error_input(self):
        from src.trading.signals import score_signal

        result = score_signal({"error": "No data"})
        assert result["score"] == 0
        assert result["recommendation"] == "NO_DATA"

    def test_calendar_warnings_deduction(self):
        from src.trading.signals import score_signal

        no_warnings = score_signal(self._make_multi_tf(), calendar_warnings=[])
        with_warnings = score_signal(
            self._make_multi_tf(),
            calendar_warnings=["FOMC", "NFP"],
        )
        assert no_warnings["breakdown"]["calendar_risk"] > with_warnings["breakdown"]["calendar_risk"]


# ═══════════════════════════════════════════════════════════════════════
# Advanced Tool Handler Tests
# ═══════════════════════════════════════════════════════════════════════


class TestAdvancedToolHandlers:

    @pytest.mark.asyncio
    async def test_mt5_analyze_bridge_offline(self):
        from src.tools.trading_advanced import mt5_analyze
        import httpx

        with patch("src.tools.trading_advanced._get_client") as mock:
            client = MagicMock()
            client.get_rates = AsyncMock(
                side_effect=httpx.ConnectError("refused")
            )
            mock.return_value = client
            result = await mt5_analyze()
            assert not result.success

    @pytest.mark.asyncio
    async def test_mt5_signal_success(self):
        from src.tools.trading_advanced import mt5_signal

        def _make_candles(n=100):
            return [
                {"time": 1709900000 + i * 3600,
                 "open": 2650 + i, "high": 2655 + i,
                 "low": 2645 + i, "close": 2652 + i}
                for i in range(n)
            ]

        with patch("src.tools.trading_advanced._get_client") as mock:
            client = MagicMock()
            client.get_rates = AsyncMock(return_value=_make_candles())
            mock.return_value = client
            result = await mt5_signal("XAUUSD")
            assert result.success
            assert "Score" in result.output
            assert "Recommendation" in result.output

    @pytest.mark.asyncio
    async def test_mt5_risk_auto_balance(self):
        from src.tools.trading_advanced import mt5_risk

        with patch("src.tools.trading_advanced._get_client") as mock:
            client = MagicMock()
            client.get_account = AsyncMock(
                return_value={"balance": 10000, "equity": 10000}
            )
            mock.return_value = client
            result = await mt5_risk(balance="0", risk_pct="1.0", sl_distance="5.0")
            assert result.success
            assert "Lot Size" in result.output

    @pytest.mark.asyncio
    async def test_mt5_risk_invalid_params(self):
        from src.tools.trading_advanced import mt5_risk

        result = await mt5_risk(balance="abc")
        assert not result.success
        assert "không hợp lệ" in result.error

    @pytest.mark.asyncio
    async def test_mt5_journal_stats_empty(self):
        from src.tools.trading_advanced import mt5_journal_stats

        conn = _create_memory_db()
        from src.trading.journal import _init_journal_tables
        _init_journal_tables(conn)

        with patch("src.trading.journal.get_connection", return_value=conn), \
             patch("src.trading.journal._journal_initialized", False):
            result = await mt5_journal_stats(days="30")
            assert result.success
            assert "Không có giao dịch" in result.output

    @pytest.mark.asyncio
    async def test_mt5_journal_sync(self):
        from src.tools.trading_advanced import mt5_journal_sync

        conn = _create_memory_db()
        from src.trading.journal import _init_journal_tables
        _init_journal_tables(conn)

        with patch("src.tools.trading_advanced._get_client") as mock, \
             patch("src.trading.journal.get_connection", return_value=conn), \
             patch("src.trading.journal._journal_initialized", False):
            client = MagicMock()
            client.get_history = AsyncMock(return_value=[
                {"ticket": 5001, "symbol": "XAUUSD", "type": 0,
                 "volume": 0.01, "price": 2650, "profit": 10, "time": 1709900000},
            ])
            mock.return_value = client
            result = await mt5_journal_sync(days="7")
            assert result.success
            assert "Synced: 1" in result.output


# ═══════════════════════════════════════════════════════════════════════
# Tool Definition Tests
# ═══════════════════════════════════════════════════════════════════════


class TestAdvancedToolDefinitions:

    def test_all_tools_exist(self):
        from src.tools.trading_advanced import (
            mt5_analyze_tool, mt5_signal_tool, mt5_risk_tool,
            mt5_journal_log_tool, mt5_journal_stats_tool, mt5_journal_sync_tool,
        )
        assert mt5_analyze_tool.name == "mt5_analyze"
        assert mt5_signal_tool.name == "mt5_signal"
        assert mt5_risk_tool.name == "mt5_risk"
        assert mt5_journal_log_tool.name == "mt5_journal_log"
        assert mt5_journal_stats_tool.name == "mt5_journal_stats"
        assert mt5_journal_sync_tool.name == "mt5_journal_sync"

    def test_no_confirmation_required(self):
        from src.tools.trading_advanced import (
            mt5_analyze_tool, mt5_signal_tool, mt5_risk_tool,
            mt5_journal_log_tool, mt5_journal_stats_tool, mt5_journal_sync_tool,
        )
        for tool in [mt5_analyze_tool, mt5_signal_tool, mt5_risk_tool,
                      mt5_journal_log_tool, mt5_journal_stats_tool,
                      mt5_journal_sync_tool]:
            assert tool.requires_confirmation is False

    def test_all_schemas_valid(self):
        from src.tools.trading_advanced import (
            mt5_analyze_tool, mt5_signal_tool, mt5_risk_tool,
            mt5_journal_log_tool, mt5_journal_stats_tool, mt5_journal_sync_tool,
        )
        for tool in [mt5_analyze_tool, mt5_signal_tool, mt5_risk_tool,
                      mt5_journal_log_tool, mt5_journal_stats_tool,
                      mt5_journal_sync_tool]:
            schema = tool.to_openai_schema()
            assert schema["type"] == "function"
            assert "parameters" in schema["function"]

    def test_tools_register_in_registry(self):
        from src.tools.trading_advanced import (
            mt5_analyze_tool, mt5_signal_tool, mt5_risk_tool,
            mt5_journal_log_tool, mt5_journal_stats_tool, mt5_journal_sync_tool,
        )
        registry = ToolRegistry()
        for t in [mt5_analyze_tool, mt5_signal_tool, mt5_risk_tool,
                   mt5_journal_log_tool, mt5_journal_stats_tool,
                   mt5_journal_sync_tool]:
            registry.register(t)
        assert len(registry.get_all()) == 6
