"""Tests for session levels module.

Covers Previous Day levels, Asian session range, round numbers,
Fibonacci retracement, the orchestrator, and serialization.
"""

from __future__ import annotations

import pytest

from src.trading.session_levels import (
    SessionLevels,
    calculate_previous_day,
    calculate_asian_range,
    calculate_round_numbers,
    calculate_fibonacci,
    calculate_all_levels,
    levels_to_dict,
    _FIB_LEVELS,
    _ROUND_NUMBER_STEP,
)


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_d1_candles(n: int = 5, base: float = 2640.0) -> list[dict]:
    """Generate simple D1 candles with ascending prices."""
    candles = []
    for i in range(n):
        o = base + i * 10
        h = o + 15
        l = o - 5
        c = o + 8
        candles.append({
            "time": f"2026-03-{i + 1:02d}T00:00:00",
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "tick_volume": 5000 + i * 100,
        })
    return candles


def _make_h1_candles_with_asian() -> list[dict]:
    """Generate H1 candles including several Asian session hours.

    Asian session = 22:00-07:00 UTC.  Mix of Asian and non-Asian candles.
    """
    candles = []
    # Non-Asian candles (hours 10-21)
    for h in range(10, 22):
        candles.append({
            "time": f"2026-03-07T{h:02d}:00:00",
            "open": 2650.0,
            "high": 2655.0,
            "low": 2645.0,
            "close": 2652.0,
            "tick_volume": 200,
        })
    # Asian candles: 22:00, 23:00, 00:00-06:00 (9 candles)
    asian_hours = [22, 23, 0, 1, 2, 3, 4, 5, 6]
    for i, h in enumerate(asian_hours):
        day = "07" if h >= 22 else "08"
        candles.append({
            "time": f"2026-03-{day}T{h:02d}:00:00",
            "open": 2660.0 + i,
            "high": 2675.0 + i,  # high rises
            "low": 2655.0 - i,   # low drops
            "close": 2665.0 + i,
            "tick_volume": 150,
        })
    return candles


# ---------------------------------------------------------------------------
# TestPreviousDay
# ---------------------------------------------------------------------------


class TestPreviousDay:
    """Tests for calculate_previous_day."""

    def test_basic_pdh_pdl(self):
        """PDH/PDL/PDO/PDC correctly extracted from second-to-last D1 candle."""
        candles = _make_d1_candles(5)
        result = calculate_previous_day(candles)

        # Second-to-last candle (index 3): base=2640+30=2670
        # open=2670, high=2685, low=2665, close=2678
        assert result["pdh"] == 2685.0
        assert result["pdl"] == 2665.0
        assert result["pdo"] == 2670.0
        assert result["pdc"] == 2678.0

    def test_insufficient_data(self):
        """Returns all zeros when fewer than 2 D1 candles provided."""
        result_empty = calculate_previous_day([])
        assert result_empty == {"pdh": 0.0, "pdl": 0.0, "pdo": 0.0, "pdc": 0.0}

        result_one = calculate_previous_day([{
            "time": "2026-03-07T00:00:00",
            "open": 2650.0, "high": 2660.0, "low": 2640.0, "close": 2655.0,
        }])
        assert result_one == {"pdh": 0.0, "pdl": 0.0, "pdo": 0.0, "pdc": 0.0}

    def test_uses_second_to_last(self):
        """Specifically verify that the second-to-last candle is used, not the last."""
        candles = [
            {"time": "2026-03-06T00:00:00", "open": 2600.0, "high": 2620.0, "low": 2590.0, "close": 2610.0},
            {"time": "2026-03-07T00:00:00", "open": 2700.0, "high": 2720.0, "low": 2690.0, "close": 2710.0},
        ]
        result = calculate_previous_day(candles)
        # Should use candles[0] (second-to-last), not candles[1] (last/current)
        assert result["pdh"] == 2620.0
        assert result["pdl"] == 2590.0
        assert result["pdo"] == 2600.0
        assert result["pdc"] == 2610.0


# ---------------------------------------------------------------------------
# TestAsianRange
# ---------------------------------------------------------------------------


class TestAsianRange:
    """Tests for calculate_asian_range."""

    def test_normal_range(self):
        """Asian high/low/close correctly computed from Asian-session candles."""
        candles = _make_h1_candles_with_asian()
        result = calculate_asian_range(candles)

        # Asian candles have: high from 2675 to 2683, low from 2655 to 2647
        assert result["asian_high"] == 2683.0  # 2675 + 8 (last Asian candle)
        assert result["asian_low"] == 2647.0   # 2655 - 8 (last Asian candle)
        # Close of the last Asian candle (hour 06:00, i=8): 2665+8=2673
        assert result["asian_close"] == 2673.0

    def test_no_asian_candles(self):
        """Returns zeros when no candles fall in the Asian session."""
        # All candles at 12:00 UTC (London session)
        candles = [
            {
                "time": f"2026-03-07T12:00:00",
                "open": 2650.0, "high": 2660.0, "low": 2640.0, "close": 2655.0,
            }
            for _ in range(5)
        ]
        result = calculate_asian_range(candles)
        assert result["asian_high"] == 0.0
        assert result["asian_low"] == 0.0
        assert result["asian_close"] == 0.0

    def test_timezone_handling(self):
        """Correctly identifies Asian candles across midnight boundary."""
        candles = [
            # 22:00 UTC = Asian
            {"time": "2026-03-07T22:00:00", "open": 2650.0, "high": 2670.0, "low": 2640.0, "close": 2660.0},
            # 23:00 UTC = Asian
            {"time": "2026-03-07T23:00:00", "open": 2660.0, "high": 2680.0, "low": 2650.0, "close": 2670.0},
            # 00:00 UTC = Asian (next day)
            {"time": "2026-03-08T00:00:00", "open": 2670.0, "high": 2690.0, "low": 2660.0, "close": 2680.0},
            # 06:00 UTC = Asian
            {"time": "2026-03-08T06:00:00", "open": 2680.0, "high": 2695.0, "low": 2665.0, "close": 2685.0},
            # 07:00 UTC = NOT Asian (end boundary exclusive)
            {"time": "2026-03-08T07:00:00", "open": 2685.0, "high": 2700.0, "low": 2675.0, "close": 2690.0},
            # 10:00 UTC = NOT Asian
            {"time": "2026-03-08T10:00:00", "open": 2690.0, "high": 2710.0, "low": 2680.0, "close": 2700.0},
        ]
        result = calculate_asian_range(candles)
        # 4 Asian candles (22, 23, 00, 06), NOT 07 or 10
        assert result["asian_high"] == 2695.0  # max of 2670, 2680, 2690, 2695
        assert result["asian_low"] == 2640.0   # min of 2640, 2650, 2660, 2665
        assert result["asian_close"] == 2685.0  # close of 06:00 candle (last Asian)


# ---------------------------------------------------------------------------
# TestRoundNumbers
# ---------------------------------------------------------------------------


class TestRoundNumbers:
    """Tests for calculate_round_numbers."""

    def test_gold_10_intervals(self):
        """Round numbers at $10 intervals around gold price."""
        levels = calculate_round_numbers(2653.0, count=5)
        assert levels == [2630.0, 2640.0, 2650.0, 2660.0, 2670.0]

    def test_count_param(self):
        """Different count values return different number of levels."""
        levels_3 = calculate_round_numbers(2653.0, count=3)
        assert len(levels_3) == 3
        assert all(lev % _ROUND_NUMBER_STEP == 0 for lev in levels_3)

        levels_7 = calculate_round_numbers(2653.0, count=7)
        assert len(levels_7) == 7
        assert all(lev % _ROUND_NUMBER_STEP == 0 for lev in levels_7)

    def test_edge_on_round(self):
        """Price sitting exactly on a round number produces centred list."""
        levels = calculate_round_numbers(2650.0, count=5)
        assert len(levels) == 5
        assert 2650.0 in levels
        # Should be centred around 2650
        assert levels == sorted(levels)


# ---------------------------------------------------------------------------
# TestFibonacci
# ---------------------------------------------------------------------------


class TestFibonacci:
    """Tests for calculate_fibonacci."""

    def test_standard_levels(self):
        """Fibonacci levels computed correctly between swing points."""
        fib = calculate_fibonacci(swing_high=2700.0, swing_low=2600.0)
        diff = 100.0

        assert len(fib) == len(_FIB_LEVELS)
        # Check specific levels
        assert fib[0.236] == pytest.approx(2623.6, rel=1e-3)
        assert fib[0.382] == pytest.approx(2638.2, rel=1e-3)
        assert fib[0.5] == pytest.approx(2650.0, rel=1e-3)
        assert fib[0.618] == pytest.approx(2661.8, rel=1e-3)
        assert fib[0.786] == pytest.approx(2678.6, rel=1e-3)

    def test_inverted_swing(self):
        """Returns empty dict when swing_high <= swing_low."""
        assert calculate_fibonacci(2600.0, 2700.0) == {}
        assert calculate_fibonacci(2650.0, 2650.0) == {}

    def test_zero_range(self):
        """Returns empty dict when both swings are zero."""
        assert calculate_fibonacci(0.0, 0.0) == {}


# ---------------------------------------------------------------------------
# TestOrchestrator
# ---------------------------------------------------------------------------


class TestOrchestrator:
    """Tests for calculate_all_levels."""

    def test_all_levels_combined(self):
        """Orchestrator populates all fields of SessionLevels."""
        d1 = _make_d1_candles(5)
        h1 = _make_h1_candles_with_asian()
        price = 2660.0

        levels = calculate_all_levels(d1, h1, price)

        # Previous day populated
        assert levels.pdh > 0
        assert levels.pdl > 0
        assert levels.pdo > 0
        assert levels.pdc > 0

        # Asian range populated
        assert levels.asian_high > 0
        assert levels.asian_low > 0
        assert levels.asian_close > 0

        # Round numbers
        assert len(levels.round_numbers) == 5
        assert all(r % _ROUND_NUMBER_STEP == 0 for r in levels.round_numbers)

        # Fibonacci (derived from D1 swings)
        assert len(levels.fib_levels) == len(_FIB_LEVELS)
        assert levels.fib_swing_high > 0
        assert levels.fib_swing_low > 0
        assert levels.fib_swing_high > levels.fib_swing_low

    def test_explicit_swing_high_low(self):
        """Explicit swing_high/low override D1-derived values."""
        d1 = _make_d1_candles(5)
        h1 = _make_h1_candles_with_asian()

        levels = calculate_all_levels(d1, h1, 2660.0, swing_high=2800.0, swing_low=2500.0)

        assert levels.fib_swing_high == 2800.0
        assert levels.fib_swing_low == 2500.0
        assert 0.618 in levels.fib_levels

    def test_empty_inputs(self):
        """Handles empty candle lists gracefully."""
        levels = calculate_all_levels([], [], 2650.0)
        assert levels.pdh == 0.0
        assert levels.asian_high == 0.0
        assert len(levels.round_numbers) == 5
        assert len(levels.fib_levels) == 0  # No D1 data, no explicit swings


# ---------------------------------------------------------------------------
# TestSerialization
# ---------------------------------------------------------------------------


class TestSerialization:
    """Tests for levels_to_dict."""

    def test_levels_to_dict(self):
        """Serialized dict has expected structure and values."""
        levels = SessionLevels(
            pdh=2685.0, pdl=2665.0, pdo=2670.0, pdc=2678.0,
            asian_high=2680.0, asian_low=2655.0, asian_close=2670.0,
            asian_swept_high=True, asian_swept_low=False,
            round_numbers=[2640.0, 2650.0, 2660.0, 2670.0, 2680.0],
            fib_levels={0.236: 2623.6, 0.382: 2638.2, 0.5: 2650.0, 0.618: 2661.8},
            fib_swing_high=2700.0, fib_swing_low=2600.0,
        )

        d = levels_to_dict(levels)

        # Previous day section
        assert d["previous_day"]["high"] == 2685.0
        assert d["previous_day"]["low"] == 2665.0
        assert d["previous_day"]["open"] == 2670.0
        assert d["previous_day"]["close"] == 2678.0

        # Asian session section
        assert d["asian_session"]["high"] == 2680.0
        assert d["asian_session"]["low"] == 2655.0
        assert d["asian_session"]["close"] == 2670.0
        assert d["asian_session"]["swept_high"] is True
        assert d["asian_session"]["swept_low"] is False

        # Round numbers
        assert d["round_numbers"] == [2640.0, 2650.0, 2660.0, 2670.0, 2680.0]

        # Fibonacci
        assert d["fibonacci"]["swing_high"] == 2700.0
        assert d["fibonacci"]["swing_low"] == 2600.0
        assert "0.618" in d["fibonacci"]["levels"]
        assert d["fibonacci"]["levels"]["0.5"] == 2650.0
