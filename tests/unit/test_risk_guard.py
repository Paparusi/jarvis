"""Tests for RiskGuard — circuit breaker with veto power over all trades."""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src.trading.risk_guard import RiskConfig, RiskGuard, RiskState, VetoResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_guard(**config_overrides) -> RiskGuard:
    """Create a RiskGuard with optional config overrides."""
    cfg = RiskConfig(**config_overrides)
    return RiskGuard(config=cfg)


def _default_entry_kwargs(**overrides) -> dict:
    """Return default kwargs for check_entry that should pass all rules."""
    defaults = dict(
        risk_pct=0.5,
        lot_size=0.03,
        rr_ratio=2.0,
        direction="buy",
        session="London",
        open_positions=[],
        calendar_warnings=[],
    )
    defaults.update(overrides)
    return defaults


# ===========================================================================
# TestCheckEntry
# ===========================================================================

class TestCheckEntry:
    """Test all 13 veto rules + approval path."""

    def test_approved_all_pass(self):
        guard = _make_guard()
        result = guard.check_entry(**_default_entry_kwargs())
        assert result.approved is True
        assert result.reason == ""
        assert result.rule == ""

    def test_vetoed_kill_switch(self):
        guard = _make_guard(kill_switch=True)
        result = guard.check_entry(**_default_entry_kwargs())
        assert result.approved is False
        assert result.rule == "kill_switch"
        assert "Kill switch" in result.reason

    def test_vetoed_daily_loss(self):
        guard = _make_guard(max_daily_loss_pct=3.0)
        guard.state.daily_pnl_pct = -3.5
        result = guard.check_entry(**_default_entry_kwargs())
        assert result.approved is False
        assert result.rule == "max_daily_loss"
        assert "-3.0%" in result.reason

    def test_vetoed_weekly_loss(self):
        guard = _make_guard(max_weekly_loss_pct=8.0)
        guard.state.weekly_pnl_pct = -8.5
        result = guard.check_entry(**_default_entry_kwargs())
        assert result.approved is False
        assert result.rule == "max_weekly_loss"

    def test_vetoed_consecutive_losses(self):
        guard = _make_guard(max_consecutive_losses=3)
        guard.state.consecutive_losses = 3
        result = guard.check_entry(**_default_entry_kwargs())
        assert result.approved is False
        assert result.rule == "max_consecutive_losses"
        assert "3 consecutive losses" in result.reason

    def test_vetoed_max_trades(self):
        guard = _make_guard(max_daily_trades=5)
        guard.state.daily_trades = 5
        result = guard.check_entry(**_default_entry_kwargs())
        assert result.approved is False
        assert result.rule == "max_daily_trades"

    def test_vetoed_lot_size(self):
        guard = _make_guard(max_lot_size=0.1)
        result = guard.check_entry(**_default_entry_kwargs(lot_size=0.15))
        assert result.approved is False
        assert result.rule == "max_lot_size"
        assert "0.15" in result.reason

    def test_vetoed_risk_pct(self):
        guard = _make_guard(max_risk_per_trade_pct=1.0)
        result = guard.check_entry(**_default_entry_kwargs(risk_pct=1.5))
        assert result.approved is False
        assert result.rule == "max_risk_per_trade"
        assert "1.50%" in result.reason

    def test_vetoed_rr_ratio(self):
        guard = _make_guard(min_rr_ratio=1.5)
        result = guard.check_entry(**_default_entry_kwargs(rr_ratio=1.2))
        assert result.approved is False
        assert result.rule == "min_rr_ratio"
        assert "1.20" in result.reason

    def test_vetoed_max_positions(self):
        guard = _make_guard(max_open_positions=3)
        positions = [
            {"ticket": 1, "type": 0, "volume": 0.03},
            {"ticket": 2, "type": 1, "volume": 0.03},
            {"ticket": 3, "type": 0, "volume": 0.03},
        ]
        result = guard.check_entry(**_default_entry_kwargs(open_positions=positions))
        assert result.approved is False
        assert result.rule == "max_open_positions"

    def test_vetoed_same_direction(self):
        guard = _make_guard(max_same_direction=2)
        positions = [
            {"ticket": 1, "type": 0, "volume": 0.03},
            {"ticket": 2, "type": 0, "volume": 0.03},
        ]
        result = guard.check_entry(
            **_default_entry_kwargs(direction="buy", open_positions=positions)
        )
        assert result.approved is False
        assert result.rule == "max_same_direction"
        assert "buy" in result.reason

    def test_vetoed_correlated_exposure(self):
        guard = _make_guard(max_correlated_exposure_pct=3.0)
        guard.state.open_risk_pct = 2.5
        result = guard.check_entry(**_default_entry_kwargs(risk_pct=0.8))
        assert result.approved is False
        assert result.rule == "max_correlated_exposure"
        assert "3.30%" in result.reason

    def test_vetoed_session(self):
        guard = _make_guard(allowed_sessions=["London", "New York", "London+NY"])
        result = guard.check_entry(**_default_entry_kwargs(session="Sydney"))
        assert result.approved is False
        assert result.rule == "allowed_sessions"
        assert "Sydney" in result.reason

    def test_vetoed_news(self):
        guard = _make_guard()
        warnings = ["FOMC Rate Decision in 10 min"]
        result = guard.check_entry(**_default_entry_kwargs(calendar_warnings=warnings))
        assert result.approved is False
        assert result.rule == "stop_before_high_impact"
        assert "FOMC" in result.reason


# ===========================================================================
# TestRecordResult
# ===========================================================================

class TestRecordResult:
    """Test trade result recording and state updates."""

    def test_win_resets_consecutive(self):
        guard = _make_guard()
        guard.state.consecutive_losses = 2
        guard.record_trade_result(profit=50.0, equity=10000.0)
        assert guard.state.consecutive_losses == 0
        assert guard.state.daily_pnl == 50.0
        assert guard.state.daily_trades == 1

    def test_loss_increments(self):
        guard = _make_guard()
        guard.state.consecutive_losses = 1
        guard.record_trade_result(profit=-30.0, equity=10000.0)
        assert guard.state.consecutive_losses == 2
        assert guard.state.daily_pnl == -30.0
        assert guard.state.daily_pnl_pct == pytest.approx(-0.3, rel=1e-2)
        assert guard.state.weekly_pnl == -30.0

    def test_daily_reset_on_new_date(self):
        guard = _make_guard()
        guard.state.date = "2025-01-01"
        guard.state.daily_pnl = -100.0
        guard.state.daily_pnl_pct = -1.0
        guard.state.daily_trades = 3
        guard.state.consecutive_losses = 2
        guard.state.weekly_pnl = -200.0

        # Recording a trade with a stale date should trigger daily reset first
        guard.record_trade_result(profit=50.0, equity=10000.0)

        # Daily fields should have been reset before recording
        assert guard.state.date == str(date.today())
        assert guard.state.daily_pnl == 50.0  # Only the new trade
        assert guard.state.daily_trades == 1
        # Consecutive losses reset by the win (profit >= 0)
        assert guard.state.consecutive_losses == 0
        # Weekly survives the daily reset, plus the new profit
        assert guard.state.weekly_pnl == -200.0 + 50.0


# ===========================================================================
# TestConfig
# ===========================================================================

class TestConfig:
    """Test config save/load round-trip."""

    def test_save_load_roundtrip(self, tmp_path):
        cfg = RiskConfig(
            max_risk_per_trade_pct=0.5,
            max_lot_size=0.05,
            min_rr_ratio=2.0,
            max_daily_loss_pct=2.0,
            kill_switch=False,
            allowed_sessions=["London", "New York"],
        )
        path = str(tmp_path / "risk.json")

        RiskGuard.save_config(cfg, path=path)
        loaded = RiskGuard.load_config(path=path)

        assert loaded.max_risk_per_trade_pct == 0.5
        assert loaded.max_lot_size == 0.05
        assert loaded.min_rr_ratio == 2.0
        assert loaded.max_daily_loss_pct == 2.0
        assert loaded.allowed_sessions == ["London", "New York"]
        assert loaded.kill_switch is False

    def test_defaults_on_missing_file(self, tmp_path):
        path = str(tmp_path / "nonexistent" / "risk.json")
        loaded = RiskGuard.load_config(path=path)

        default = RiskConfig()
        assert loaded.max_risk_per_trade_pct == default.max_risk_per_trade_pct
        assert loaded.max_lot_size == default.max_lot_size
        assert loaded.max_daily_trades == default.max_daily_trades
        assert loaded.kill_switch is False


# ===========================================================================
# Additional edge-case tests
# ===========================================================================

class TestEdgeCases:
    """Extra coverage for update, flatten, status methods."""

    def test_update_config(self):
        guard = _make_guard()
        guard.update_config(max_daily_loss_pct=5.0, max_lot_size=0.2)
        assert guard.config.max_daily_loss_pct == 5.0
        assert guard.config.max_lot_size == 0.2

    def test_should_flatten_for_news_true(self):
        guard = _make_guard(flatten_before_news=True)
        assert guard.should_flatten_for_news(["NFP in 5 min"]) is True

    def test_should_flatten_for_news_false_disabled(self):
        guard = _make_guard(flatten_before_news=False)
        assert guard.should_flatten_for_news(["NFP in 5 min"]) is False

    def test_should_flatten_for_news_false_no_warnings(self):
        guard = _make_guard(flatten_before_news=True)
        assert guard.should_flatten_for_news([]) is False

    def test_get_status_shape(self):
        guard = _make_guard()
        status = guard.get_status()
        assert "state" in status
        assert "config" in status
        assert "daily_pnl" in status["state"]
        assert "max_lot_size" in status["config"]

    def test_update_open_risk(self):
        guard = _make_guard()
        positions = [
            {"ticket": 1, "type": 0, "volume": 0.03},
            {"ticket": 2, "type": 1, "volume": 0.05},
        ]
        guard.update_open_risk(positions, equity=10000.0)
        # 0.03 * 0.01 / 10000 * 100 * 100 = 0.0003
        # + 0.05 * 0.01 / 10000 * 100 * 100 = 0.0005
        # total = 0.0008
        assert guard.state.open_risk_pct == pytest.approx(0.0008, rel=1e-2)

    def test_update_open_risk_zero_equity(self):
        guard = _make_guard()
        guard.update_open_risk([{"ticket": 1, "type": 0, "volume": 0.1}], equity=0.0)
        assert guard.state.open_risk_pct == 0.0

    def test_reset_daily_preserves_weekly(self):
        guard = _make_guard()
        guard.state.weekly_pnl = -100.0
        guard.state.weekly_pnl_pct = -1.0
        guard.state.consecutive_losses = 2
        guard.state.daily_pnl = -50.0
        guard.state.daily_trades = 3

        guard.reset_daily()

        assert guard.state.daily_pnl == 0.0
        assert guard.state.daily_trades == 0
        assert guard.state.weekly_pnl == -100.0  # Preserved
        assert guard.state.consecutive_losses == 2  # Preserved

    def test_reset_weekly(self):
        guard = _make_guard()
        guard.state.weekly_pnl = -500.0
        guard.state.weekly_pnl_pct = -5.0
        guard.reset_weekly()
        assert guard.state.weekly_pnl == 0.0
        assert guard.state.weekly_pnl_pct == 0.0
