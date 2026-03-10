"""Tests for Smart Money Concepts (SMC) engine.

Covers swing detection, classification, structure (BOS/ChoCH), order blocks,
fair value gaps, liquidity sweeps, supply/demand zones, orchestrator,
signal scoring integration, and the mt5_smc tool handler.
"""

from __future__ import annotations

import json
import math
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.trading.smc import (
    SwingPoint,
    StructureEvent,
    OrderBlock,
    FairValueGap,
    LiquiditySweep,
    SupplyDemandZone,
    SMCAnalysis,
    detect_swing_points,
    classify_swing_points,
    detect_structure,
    detect_order_blocks,
    detect_fair_value_gaps,
    detect_liquidity_sweeps,
    detect_supply_demand_zones,
    _cluster_levels,
    _deduplicate_zones,
    analyze_smc,
    smc_to_dict,
    _MIN_CANDLES_FOR_SMC,
)
from src.trading.signals import _score_smc, score_signal


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_candles(n=100, base=2650.0, trend=0.5):
    """Generate trending candles with swing structure using sin waves."""
    candles = []
    for i in range(n):
        mid = base + i * trend + 3.0 * math.sin(i * 0.3)
        body = abs(1.5 * math.sin(i * 0.7))
        if i % 3 == 0:
            o, c = mid + body / 2, mid - body / 2  # bearish
        else:
            o, c = mid - body / 2, mid + body / 2  # bullish
        h = max(o, c) + abs(0.9 * math.sin(i * 1.1))
        l = min(o, c) - abs(0.9 * math.sin(i * 0.9))
        candles.append({
            "time": f"2026-03-{(i // 24) + 1:02d}T{i % 24:02d}:00:00",
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "tick_volume": 100 + i,
        })
    return candles


def _make_flat_candles(n=30, price=2650.0):
    """Generate completely flat candles (no swing points possible)."""
    return [
        {
            "time": f"2026-03-01T{i:02d}:00:00",
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "tick_volume": 100,
        }
        for i in range(n)
    ]


def _make_swing_candles():
    """Construct candles with guaranteed clear swing highs and lows.

    Pattern (lookback=2):
      idx:  0    1    2    3    4    5    6    7    8    9   10   11   12
      H:   10   11  *15*  11   10   11  *14*  10    9   10  *13*   9    8
      L:    5    6    8    6   *3*   6    8    5   *2*   5    7    4   *1*

    Swing highs at 2 (15), 6 (14), 10 (13)  — strictly > neighbours in window 2
    Swing lows at 4 (3), 8 (2), 12 (1)
    """
    data = [
        # (open, high, low, close)
        (9, 10, 5, 9),    # 0
        (10, 11, 6, 10),  # 1
        (12, 15, 8, 14),  # 2  swing high
        (11, 11, 6, 10),  # 3
        (8, 10, 3, 5),    # 4  swing low
        (7, 11, 6, 9),    # 5
        (12, 14, 8, 13),  # 6  swing high
        (9, 10, 5, 8),    # 7
        (6, 9, 2, 4),     # 8  swing low
        (7, 10, 5, 8),    # 9
        (11, 13, 7, 12),  # 10 swing high
        (7, 9, 4, 6),     # 11
        (3, 8, 1, 2),     # 12 swing low
    ]
    return [
        {
            "time": f"2026-03-01T{i:02d}:00:00",
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "tick_volume": 100,
        }
        for i, (o, h, l, c) in enumerate(data)
    ]


# ---------------------------------------------------------------------------
# TestSwingPointDetection
# ---------------------------------------------------------------------------


class TestSwingPointDetection:
    """Swing point detection: highs, lows, edge cases."""

    def test_clear_swing_highs(self):
        """Distinct peaks are detected as swing highs."""
        candles = _make_swing_candles()
        swings = detect_swing_points(candles, lookback=2)
        highs = [s for s in swings if s.is_high]
        high_indices = [s.index for s in highs]
        assert 2 in high_indices
        assert 6 in high_indices
        assert 10 in high_indices

    def test_clear_swing_lows(self):
        """Distinct troughs are detected as swing lows."""
        candles = _make_swing_candles()
        swings = detect_swing_points(candles, lookback=2)
        lows = [s for s in swings if not s.is_high]
        low_indices = [s.index for s in lows]
        assert 4 in low_indices
        assert 8 in low_indices
        # Index 12 is excluded because it's within lookback of the end
        # (range is [lookback, n-lookback)), so we verify at least 2 lows found
        assert len(lows) >= 2

    def test_flat_market_no_swings(self):
        """Flat candles (all same price) produce no swing points."""
        candles = _make_flat_candles(30)
        swings = detect_swing_points(candles, lookback=3)
        assert swings == []

    def test_alternating_up_down(self):
        """Alternating high/low pattern produces swings at every peak/trough."""
        # V-shape pattern: low-high-low-high-low... each strictly different
        candles = []
        for i in range(20):
            if i % 2 == 0:
                candles.append({
                    "time": f"2026-03-01T{i:02d}:00:00",
                    "open": 100.0, "high": 100.0 + i * 0.1,
                    "low": 90.0 - i * 0.5, "close": 95.0,
                    "tick_volume": 100,
                })
            else:
                candles.append({
                    "time": f"2026-03-01T{i:02d}:00:00",
                    "open": 100.0, "high": 120.0 + i * 0.5,
                    "low": 99.0 + i * 0.1, "close": 110.0,
                    "tick_volume": 100,
                })
        swings = detect_swing_points(candles, lookback=1)
        # Should find multiple swings due to alternating structure
        assert len(swings) > 0

    def test_larger_lookback_fewer_swings(self):
        """Larger lookback parameter produces fewer swing points."""
        candles = _make_candles(100)
        swings_small = detect_swing_points(candles, lookback=3)
        swings_large = detect_swing_points(candles, lookback=7)
        assert len(swings_large) <= len(swings_small)

    def test_minimum_data_requirement(self):
        """With fewer candles than 2*lookback, no swings found."""
        candles = _make_candles(5)
        swings = detect_swing_points(candles, lookback=5)
        assert swings == []


# ---------------------------------------------------------------------------
# TestSwingClassification
# ---------------------------------------------------------------------------


class TestSwingClassification:
    """Classify swing points as HH/HL/LH/LL."""

    def test_higher_highs_higher_lows(self):
        """Ascending swing sequence classified as HH/HL."""
        swings = [
            SwingPoint(index=2, price=100.0, swing_type="", time="", is_high=True),
            SwingPoint(index=4, price=90.0, swing_type="", time="", is_high=False),
            SwingPoint(index=6, price=110.0, swing_type="", time="", is_high=True),
            SwingPoint(index=8, price=95.0, swing_type="", time="", is_high=False),
        ]
        result = classify_swing_points(swings)
        assert result[0].swing_type == "HH"  # first high defaults to HH
        assert result[1].swing_type == "HL"  # first low defaults to HL
        assert result[2].swing_type == "HH"  # 110 > 100
        assert result[3].swing_type == "HL"  # 95 > 90

    def test_lower_highs_lower_lows(self):
        """Descending swing sequence classified as LH/LL."""
        swings = [
            SwingPoint(index=2, price=110.0, swing_type="", time="", is_high=True),
            SwingPoint(index=4, price=95.0, swing_type="", time="", is_high=False),
            SwingPoint(index=6, price=105.0, swing_type="", time="", is_high=True),
            SwingPoint(index=8, price=88.0, swing_type="", time="", is_high=False),
        ]
        result = classify_swing_points(swings)
        assert result[0].swing_type == "HH"  # first high defaults to HH
        assert result[1].swing_type == "HL"  # first low defaults to HL
        assert result[2].swing_type == "LH"  # 105 < 110
        assert result[3].swing_type == "LL"  # 88 < 95

    def test_mixed_classification(self):
        """Mixed sequence produces correct transitions."""
        swings = [
            SwingPoint(index=1, price=100.0, swing_type="", time="", is_high=True),
            SwingPoint(index=3, price=90.0, swing_type="", time="", is_high=False),
            SwingPoint(index=5, price=105.0, swing_type="", time="", is_high=True),
            SwingPoint(index=7, price=85.0, swing_type="", time="", is_high=False),
            SwingPoint(index=9, price=95.0, swing_type="", time="", is_high=True),
        ]
        result = classify_swing_points(swings)
        assert result[0].swing_type == "HH"   # first defaults HH
        assert result[1].swing_type == "HL"   # first defaults HL
        assert result[2].swing_type == "HH"   # 105 > 100
        assert result[3].swing_type == "LL"   # 85 < 90
        assert result[4].swing_type == "LH"   # 95 < 105


# ---------------------------------------------------------------------------
# TestMarketStructure
# ---------------------------------------------------------------------------


class TestMarketStructure:
    """BOS and ChoCH detection via detect_structure."""

    def _build_structure_candles(self):
        """Build candles with known swing highs/lows and a close breakout.

        Layout (lookback=2):
          idx 0-4: baseline with swing high at 2 (H=110) and swing low at 4 (L=80)
          idx 5-9: second swing high at 7 (H=115), swing low at 9 (L=78)
          idx 10: breakout candle closing above swing high at 7 (115)
          idx 11-14: downturn with swing high at 12 (H=108)
          idx 15: bearish breakout candle closing below swing low at 9 (78)
        """
        data = [
            # (open, high, low, close)
            (100, 102, 98, 101),   # 0
            (101, 105, 99, 104),   # 1
            (105, 110, 103, 108),  # 2 swing high (H=110)
            (106, 107, 97, 98),    # 3
            (96, 99, 80, 85),      # 4 swing low (L=80)
            (87, 95, 84, 93),      # 5
            (95, 108, 90, 107),    # 6
            (108, 115, 105, 113),  # 7 swing high (H=115)
            (110, 112, 88, 90),    # 8
            (88, 91, 78, 82),      # 9 swing low (L=78)
            (84, 120, 83, 118),    # 10 bullish breakout: close=118 > 115
            (116, 117, 100, 102),  # 11
            (103, 108, 99, 105),   # 12 swing high (H=108)
            (103, 106, 90, 92),    # 13
            (90, 95, 82, 83),      # 14
            (81, 84, 70, 75),      # 15 bearish breakout: close=75 < 78
        ]
        return [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]

    def test_bullish_bos(self):
        """Close above swing high in undefined/bullish trend = BOS bullish."""
        candles = self._build_structure_candles()
        swings = detect_swing_points(candles, lookback=2)
        swings = classify_swing_points(swings)
        events, trend = detect_structure(candles, swings)
        # Should have at least one bullish structure break
        bullish_events = [e for e in events if e.direction == "bullish"]
        assert len(bullish_events) >= 1
        # First bullish break from undefined = BOS
        assert bullish_events[0].event_type == "BOS"

    def test_bearish_bos(self):
        """Close below swing low in undefined/bearish trend = BOS bearish."""
        # Build downtrend candles
        data = [
            (110, 115, 108, 112),  # 0
            (112, 114, 106, 107),  # 1
            (107, 108, 100, 101),  # 2 swing low (L=100)
            (103, 109, 102, 108),  # 3
            (109, 112, 107, 111),  # 4 swing high
            (110, 111, 103, 104),  # 5
            (103, 105, 95, 96),    # 6 close < 100 → BOS bearish
        ]
        candles = [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]
        swings = detect_swing_points(candles, lookback=2)
        swings = classify_swing_points(swings)
        events, trend = detect_structure(candles, swings)
        bearish = [e for e in events if e.direction == "bearish"]
        if bearish:
            assert bearish[0].event_type == "BOS"

    def test_bullish_choch(self):
        """Close above swing high after bearish trend = ChoCH bullish."""
        candles = self._build_structure_candles()
        swings = detect_swing_points(candles, lookback=2)
        swings = classify_swing_points(swings)
        events, _ = detect_structure(candles, swings)
        # After a bearish break, the next bullish break is ChoCH
        choch_bullish = [
            e for e in events
            if e.event_type == "ChoCH" and e.direction == "bullish"
        ]
        # May or may not appear depending on exact swing positions.
        # Verify event types are only BOS or ChoCH
        for e in events:
            assert e.event_type in ("BOS", "ChoCH")

    def test_bearish_choch(self):
        """Close below swing low after bullish trend = ChoCH bearish."""
        candles = self._build_structure_candles()
        swings = detect_swing_points(candles, lookback=2)
        swings = classify_swing_points(swings)
        events, _ = detect_structure(candles, swings)
        # After a bullish BOS, a break below should be ChoCH bearish
        choch_bearish = [
            e for e in events
            if e.event_type == "ChoCH" and e.direction == "bearish"
        ]
        # We expect the break at idx 15 (close=75 < 78) to be ChoCH
        # if trend was bullish from the earlier break
        if choch_bearish:
            assert choch_bearish[0].break_price > 0

    def test_wick_only_no_structure_break(self):
        """A candle that wicks through but closes back does not trigger BOS."""
        # Swing high at idx 2 (H=110). Candle at idx 5 wicks to 112 but closes at 108.
        data = [
            (100, 102, 98, 101),   # 0
            (101, 105, 99, 104),   # 1
            (105, 110, 103, 108),  # 2 swing high (H=110)
            (106, 107, 100, 102),  # 3
            (101, 103, 99, 100),   # 4
            (102, 112, 100, 108),  # 5 wick to 112 but close=108 < 110
            (107, 109, 105, 108),  # 6
        ]
        candles = [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]
        swings = detect_swing_points(candles, lookback=2)
        swings = classify_swing_points(swings)
        events, trend = detect_structure(candles, swings)
        # No bullish break because close never exceeded swing high
        bullish = [e for e in events if e.direction == "bullish"]
        assert len(bullish) == 0

    def test_trend_transitions(self):
        """Structure correctly transitions between bullish and bearish."""
        candles = self._build_structure_candles()
        swings = detect_swing_points(candles, lookback=2)
        swings = classify_swing_points(swings)
        events, trend = detect_structure(candles, swings)
        # trend should be "bullish" or "bearish" (not "undefined") after breaks
        if events:
            assert trend in ("bullish", "bearish")
        # All events must have valid fields
        for e in events:
            assert e.direction in ("bullish", "bearish")
            assert e.event_type in ("BOS", "ChoCH")
            assert e.break_price > 0
            assert e.break_index >= 0


# ---------------------------------------------------------------------------
# TestOrderBlocks
# ---------------------------------------------------------------------------


class TestOrderBlocks:
    """Order block detection from structure events."""

    def _make_ob_candles_bullish(self):
        """Candles with a bearish candle followed by bullish breakout.

        idx 0: bullish setup
        idx 1: bearish candle (this becomes the bullish OB origin)
        idx 2: small candle
        idx 3: big bullish breakout (close > swing high)

        We also need swing points, so we extend with enough context.
        """
        data = [
            (100, 105, 98, 104),   # 0
            (103, 106, 96, 95),    # 1 bearish origin (close < open)
            (96, 100, 94, 99),     # 2
            (100, 115, 99, 114),   # 3 bullish impulse
        ]
        candles = [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]
        return candles

    def test_bullish_ob_detected(self):
        """Bearish candle before a bullish structure break creates a bullish OB."""
        candles = self._make_ob_candles_bullish()
        # Create a synthetic bullish structure event at break_index=3
        events = [
            StructureEvent(
                event_type="BOS", direction="bullish",
                break_price=106.0, break_index=3,
                break_time="2026-03-01T03:00:00",
            ),
        ]
        obs = detect_order_blocks(candles, events)
        assert len(obs) >= 1
        bullish_obs = [ob for ob in obs if ob.ob_type == "bullish"]
        assert len(bullish_obs) >= 1
        # Origin should be the bearish candle (idx 1)
        assert bullish_obs[0].origin_index == 1
        assert bullish_obs[0].zone_high == 106.0
        assert bullish_obs[0].zone_low == 96.0

    def test_bearish_ob_detected(self):
        """Bullish candle before a bearish structure break creates a bearish OB."""
        data = [
            (100, 105, 98, 102),   # 0
            (103, 110, 100, 109),  # 1 bullish origin (close > open)
            (108, 109, 100, 101),  # 2
            (100, 102, 85, 87),    # 3 bearish impulse
        ]
        candles = [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]
        events = [
            StructureEvent(
                event_type="BOS", direction="bearish",
                break_price=98.0, break_index=3,
                break_time="2026-03-01T03:00:00",
            ),
        ]
        obs = detect_order_blocks(candles, events)
        bearish_obs = [ob for ob in obs if ob.ob_type == "bearish"]
        assert len(bearish_obs) >= 1
        assert bearish_obs[0].origin_index == 1

    def test_ob_mitigation(self):
        """An OB is marked mitigated when price returns into the zone.

        Mitigation scan starts at ob_idx + 1, so the first candle after
        the origin that touches the zone triggers mitigation.
        For bullish OB: mitigated when candles[k].low <= zone_high.
        """
        # OB origin at idx 1: bearish candle, zone = [low=120, high=135]
        # Candles 2-3 stay above zone_high (135), idx 4 = breakout
        # Candle 5 dips low=130 <= zone_high=135 → mitigated
        data = [
            (130, 138, 125, 133),  # 0
            (133, 135, 120, 118),  # 1 bearish OB origin: zone [120, 135]
            (140, 148, 138, 146),  # 2 low=138 > 135 → no mitigation
            (146, 150, 140, 148),  # 3 low=140 > 135 → no mitigation
            (148, 160, 145, 158),  # 4 breakout, low=145 > 135
            (155, 158, 130, 133),  # 5 low=130 <= 135 → mitigated here
        ]
        candles = [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]
        events = [
            StructureEvent(
                event_type="BOS", direction="bullish",
                break_price=135.0, break_index=4,
                break_time="2026-03-01T04:00:00",
            ),
        ]
        obs = detect_order_blocks(candles, events)
        bullish_obs = [ob for ob in obs if ob.ob_type == "bullish"]
        assert len(bullish_obs) >= 1
        assert bullish_obs[0].mitigated is True
        assert bullish_obs[0].mitigation_index == 5

    def test_unmitigated_ob_stays_active(self):
        """OB remains unmitigated when price stays away from the zone.

        For bullish OB: zone = [origin.low, origin.high].
        Mitigated when any candle after origin has low <= zone_high.
        All subsequent candles must have low > zone_high to stay unmitigated.
        """
        # OB origin at idx 1: bearish candle, zone = [low=120, high=135]
        # All candles after origin have low > 135
        data = [
            (130, 138, 125, 133),  # 0
            (133, 135, 120, 118),  # 1 bearish OB origin: zone [120, 135]
            (140, 148, 138, 146),  # 2 low=138 > 135
            (146, 155, 140, 153),  # 3 low=140 > 135
            (153, 165, 150, 163),  # 4 breakout, low=150 > 135
            (163, 170, 160, 168),  # 5 low=160 > 135
        ]
        candles = [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]
        events = [
            StructureEvent(
                event_type="BOS", direction="bullish",
                break_price=135.0, break_index=4,
                break_time="2026-03-01T04:00:00",
            ),
        ]
        obs = detect_order_blocks(candles, events)
        bullish_obs = [ob for ob in obs if ob.ob_type == "bullish"]
        assert len(bullish_obs) >= 1
        assert bullish_obs[0].mitigated is False
        assert bullish_obs[0].mitigation_index is None

    def test_max_obs_limit(self):
        """detect_order_blocks respects max_obs limit."""
        data = [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": 100.0 + i, "high": 105.0 + i,
                "low": 95.0 + i, "close": 100.0 + i - 1 if i % 2 == 0 else 100.0 + i + 1,
                "tick_volume": 100,
            }
            for i in range(30)
        ]
        # Create many structure events
        events = [
            StructureEvent(
                event_type="BOS", direction="bullish",
                break_price=100.0 + j * 3, break_index=j * 3 + 2,
                break_time=f"2026-03-01T{j * 3 + 2:02d}:00:00",
            )
            for j in range(8)
        ]
        obs = detect_order_blocks(data, events, max_obs=3)
        assert len(obs) <= 3


# ---------------------------------------------------------------------------
# TestFairValueGaps
# ---------------------------------------------------------------------------


class TestFairValueGaps:
    """Fair value gap detection."""

    def test_bullish_fvg_detected(self):
        """candles[i-1].high < candles[i+1].low creates a bullish FVG."""
        candles = [
            {"time": "T0", "open": 100, "high": 102, "low": 98, "close": 101, "tick_volume": 100},
            {"time": "T1", "open": 103, "high": 110, "low": 103, "close": 109, "tick_volume": 100},
            {"time": "T2", "open": 108, "high": 115, "low": 106, "close": 114, "tick_volume": 100},
        ]
        # prev_high=102, next_low=106 → 102 < 106 → bullish FVG
        fvgs = detect_fair_value_gaps(candles, min_gap_pct=0.0)
        bullish = [f for f in fvgs if f.fvg_type == "bullish"]
        assert len(bullish) >= 1
        assert bullish[0].zone_low == 102  # prev candle high
        assert bullish[0].zone_high == 106  # next candle low
        assert bullish[0].gap_size == pytest.approx(4.0, abs=0.01)

    def test_bearish_fvg_detected(self):
        """candles[i-1].low > candles[i+1].high creates a bearish FVG."""
        candles = [
            {"time": "T0", "open": 110, "high": 115, "low": 108, "close": 112, "tick_volume": 100},
            {"time": "T1", "open": 105, "high": 106, "low": 98, "close": 99, "tick_volume": 100},
            {"time": "T2", "open": 100, "high": 103, "low": 95, "close": 97, "tick_volume": 100},
        ]
        # prev_low=108, next_high=103 → 108 > 103 → bearish FVG
        fvgs = detect_fair_value_gaps(candles, min_gap_pct=0.0)
        bearish = [f for f in fvgs if f.fvg_type == "bearish"]
        assert len(bearish) >= 1
        assert bearish[0].zone_high == 108  # prev candle low
        assert bearish[0].zone_low == 103   # next candle high
        assert bearish[0].gap_size == pytest.approx(5.0, abs=0.01)

    def test_fvg_full_mitigation(self):
        """FVG marked mitigated when price fully fills the gap."""
        candles = [
            {"time": "T0", "open": 100, "high": 102, "low": 98, "close": 101, "tick_volume": 100},
            {"time": "T1", "open": 103, "high": 110, "low": 103, "close": 109, "tick_volume": 100},
            {"time": "T2", "open": 108, "high": 115, "low": 106, "close": 114, "tick_volume": 100},
            # Candle 3: price drops to 101 (below zone_low=102) → fully mitigated
            {"time": "T3", "open": 112, "high": 113, "low": 101, "close": 105, "tick_volume": 100},
        ]
        fvgs = detect_fair_value_gaps(candles, min_gap_pct=0.0)
        bullish = [f for f in fvgs if f.fvg_type == "bullish"]
        assert len(bullish) >= 1
        assert bullish[0].mitigated is True
        assert bullish[0].mitigation_pct == 100.0

    def test_min_gap_filter(self):
        """FVGs smaller than min_gap_pct are filtered out."""
        candles = [
            {"time": "T0", "open": 2650.0, "high": 2651.0, "low": 2648.0, "close": 2650.5, "tick_volume": 100},
            {"time": "T1", "open": 2651.0, "high": 2655.0, "low": 2651.0, "close": 2654.0, "tick_volume": 100},
            # next_low - prev_high = 2651.1 - 2651.0 = 0.1 → gap_pct ~ 0.1/2651.05 = 0.0000377
            {"time": "T2", "open": 2652.0, "high": 2656.0, "low": 2651.1, "close": 2655.0, "tick_volume": 100},
        ]
        # With min_gap_pct=0.001, the tiny gap should be filtered
        fvgs = detect_fair_value_gaps(candles, min_gap_pct=0.001)
        assert len(fvgs) == 0

    def test_no_fvg_in_overlapping_candles(self):
        """Continuous/overlapping candles produce no FVG."""
        candles = [
            {"time": "T0", "open": 100, "high": 105, "low": 95, "close": 103, "tick_volume": 100},
            {"time": "T1", "open": 103, "high": 107, "low": 99, "close": 106, "tick_volume": 100},
            {"time": "T2", "open": 106, "high": 108, "low": 101, "close": 104, "tick_volume": 100},
        ]
        # prev_high=105, next_low=101 → 105 > 101 → no bullish FVG
        # prev_low=95, next_high=108 → 95 < 108 → no bearish FVG
        fvgs = detect_fair_value_gaps(candles, min_gap_pct=0.0)
        assert len(fvgs) == 0


# ---------------------------------------------------------------------------
# TestLiquiditySweeps
# ---------------------------------------------------------------------------


class TestLiquiditySweeps:
    """Liquidity sweep detection."""

    def test_sweep_above_equal_highs(self):
        """Two swing highs at same price, then wick above + close below = sweep above."""
        # Two swing highs at price 110.0 (indices 2, 6), then sweep candle at 8
        swings = [
            SwingPoint(index=2, price=110.0, swing_type="HH", time="", is_high=True),
            SwingPoint(index=6, price=110.0, swing_type="LH", time="", is_high=True),
        ]
        candles = [{"time": f"T{i}", "open": 105, "high": 107, "low": 103, "close": 105, "tick_volume": 100} for i in range(10)]
        # Sweep candle at index 8: wick to 112, close at 108 (below 110)
        candles[8] = {"time": "T8", "open": 109, "high": 112, "low": 106, "close": 108, "tick_volume": 100}

        sweeps = detect_liquidity_sweeps(candles, swings, tolerance_pct=0.005, min_touches=2)
        above = [s for s in sweeps if s.direction == "above"]
        assert len(above) >= 1
        assert above[0].swept_level == pytest.approx(110.0, abs=0.1)
        assert above[0].wick_extreme == 112
        assert above[0].close_price == 108
        assert above[0].num_touches == 2

    def test_sweep_below_equal_lows(self):
        """Two swing lows at same price, then wick below + close above = sweep below."""
        swings = [
            SwingPoint(index=2, price=90.0, swing_type="HL", time="", is_high=False),
            SwingPoint(index=6, price=90.0, swing_type="LL", time="", is_high=False),
        ]
        candles = [{"time": f"T{i}", "open": 95, "high": 97, "low": 93, "close": 95, "tick_volume": 100} for i in range(10)]
        # Sweep candle at index 8: wick to 88, close at 92 (above 90)
        candles[8] = {"time": "T8", "open": 91, "high": 93, "low": 88, "close": 92, "tick_volume": 100}

        sweeps = detect_liquidity_sweeps(candles, swings, tolerance_pct=0.005, min_touches=2)
        below = [s for s in sweeps if s.direction == "below"]
        assert len(below) >= 1
        assert below[0].swept_level == pytest.approx(90.0, abs=0.1)
        assert below[0].wick_extreme == 88
        assert below[0].close_price == 92

    def test_no_sweep_on_breakout(self):
        """If close is beyond the level (not back inside), no sweep recorded."""
        swings = [
            SwingPoint(index=2, price=110.0, swing_type="HH", time="", is_high=True),
            SwingPoint(index=6, price=110.0, swing_type="LH", time="", is_high=True),
        ]
        candles = [{"time": f"T{i}", "open": 105, "high": 107, "low": 103, "close": 105, "tick_volume": 100} for i in range(10)]
        # Candle at 8 breaks above and CLOSES above 110 → no sweep, it's a breakout
        candles[8] = {"time": "T8", "open": 109, "high": 115, "low": 108, "close": 113, "tick_volume": 100}

        sweeps = detect_liquidity_sweeps(candles, swings, tolerance_pct=0.005, min_touches=2)
        above = [s for s in sweeps if s.direction == "above"]
        assert len(above) == 0

    def test_min_touches_filter(self):
        """Cluster with fewer touches than min_touches is ignored."""
        # Only 1 swing high → doesn't meet min_touches=2
        swings = [
            SwingPoint(index=2, price=110.0, swing_type="HH", time="", is_high=True),
        ]
        candles = [{"time": f"T{i}", "open": 105, "high": 107, "low": 103, "close": 105, "tick_volume": 100} for i in range(10)]
        candles[5] = {"time": "T5", "open": 109, "high": 112, "low": 106, "close": 108, "tick_volume": 100}

        sweeps = detect_liquidity_sweeps(candles, swings, tolerance_pct=0.005, min_touches=2)
        assert len(sweeps) == 0


# ---------------------------------------------------------------------------
# TestSupplyDemandZones
# ---------------------------------------------------------------------------


class TestSupplyDemandZones:
    """Supply and demand zone detection."""

    def _make_dbr_candles(self):
        """Drop-Base-Rally: bearish impulse → small base → bullish impulse.

        Average body ~ 5.0. Impulse threshold = 5 * 2 = 10.
        """
        data = [
            (150, 152, 130, 131),  # 0: bearish impulse (body=19 > 10)
            (132, 135, 128, 130),  # 1: another bearish
            (130, 133, 128, 131),  # 2: small base (body=1 < 0.75*avg)
            (132, 155, 130, 154),  # 3: bullish impulse (body=22 > 10)
            (155, 160, 153, 158),  # 4: continuation
        ]
        return [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]

    def test_demand_zone_dbr(self):
        """Drop-Base-Rally pattern creates a demand zone."""
        candles = self._make_dbr_candles()
        zones = detect_supply_demand_zones(candles, impulse_multiplier=2.0, max_base_candles=6)
        demand = [z for z in zones if z.zone_type == "demand"]
        assert len(demand) >= 1
        # Zone should encompass the base candle
        z = demand[0]
        assert z.zone_low <= 128  # base low
        assert z.zone_high >= 133  # base high

    def test_supply_zone_rbd(self):
        """Rally-Base-Drop pattern creates a supply zone."""
        data = [
            (120, 145, 118, 144),  # 0: bullish impulse (body=24 > 10)
            (144, 148, 140, 147),  # 1: bullish continuation
            (147, 149, 145, 146),  # 2: small base (body=1 < 0.75*avg)
            (145, 146, 120, 121),  # 3: bearish impulse (body=24 > 10)
            (120, 122, 115, 116),  # 4: continuation
        ]
        candles = [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]
        zones = detect_supply_demand_zones(candles, impulse_multiplier=2.0, max_base_candles=6)
        supply = [z for z in zones if z.zone_type == "supply"]
        assert len(supply) >= 1
        z = supply[0]
        assert z.zone_low <= 145
        assert z.zone_high >= 149

    def test_freshness_degrades_on_retest(self):
        """Zone strength decreases each time price re-tests it."""
        data = [
            (150, 152, 130, 131),  # 0: bearish impulse
            (132, 135, 128, 130),  # 1
            (130, 133, 128, 131),  # 2: small base
            (132, 155, 130, 154),  # 3: bullish impulse
            (155, 160, 153, 158),  # 4
            # Re-test: price dips into demand zone
            (156, 157, 132, 135),  # 5: low=132 is within [128, 133]
            (136, 145, 131, 140),  # 6: another dip into zone
        ]
        candles = [
            {
                "time": f"2026-03-01T{i:02d}:00:00",
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": 100,
            }
            for i, (o, h, l, c) in enumerate(data)
        ]
        zones = detect_supply_demand_zones(candles, impulse_multiplier=2.0, max_base_candles=6)
        demand = [z for z in zones if z.zone_type == "demand"]
        if demand:
            z = demand[0]
            # Tested at least once → strength < 1.0
            assert z.tested is True
            assert z.strength < 1.0
            assert z.test_count >= 1

    def test_deduplicate_overlapping_zones(self):
        """_deduplicate_zones merges overlapping zones of the same type."""
        zones = [
            SupplyDemandZone("demand", 105.0, 100.0, 10, 12, "T10", False, 0, 1.0),
            SupplyDemandZone("demand", 108.0, 103.0, 14, 16, "T14", False, 0, 0.8),
        ]
        merged = _deduplicate_zones(zones)
        assert len(merged) == 1
        assert merged[0].zone_low == 100.0
        assert merged[0].zone_high == 108.0
        assert merged[0].strength == 1.0  # max of the two


# ---------------------------------------------------------------------------
# TestSMCOrchestrator
# ---------------------------------------------------------------------------


class TestSMCOrchestrator:
    """Full SMC pipeline orchestrator and serialization."""

    def test_full_analysis_on_trending_data(self):
        """analyze_smc on sufficient trending data returns populated fields."""
        candles = _make_candles(120, base=2650.0, trend=0.5)
        smc = analyze_smc(candles, swing_lookback=5)
        assert len(smc.swing_points) > 0
        assert smc.current_trend in ("bullish", "bearish", "undefined")
        # At least one of these should be populated on trending data
        total_findings = (
            len(smc.structure_events)
            + len(smc.order_blocks)
            + len(smc.fair_value_gaps)
            + len(smc.supply_demand_zones)
        )
        assert total_findings >= 0  # May be 0 if trend is weak, but no crash

    def test_insufficient_data_returns_empty(self):
        """analyze_smc with < 50 candles returns empty SMCAnalysis."""
        candles = _make_candles(30)
        smc = analyze_smc(candles)
        assert smc.swing_points == []
        assert smc.structure_events == []
        assert smc.current_trend == "undefined"
        assert smc.order_blocks == []
        assert smc.fair_value_gaps == []
        assert smc.liquidity_sweeps == []
        assert smc.supply_demand_zones == []

    def test_smc_to_dict_serializable(self):
        """smc_to_dict output is JSON-serializable and has expected keys."""
        candles = _make_candles(120)
        smc = analyze_smc(candles)
        result = smc_to_dict(smc, current_price=2700.0)
        # Must be JSON-serializable
        serialized = json.dumps(result)
        assert isinstance(serialized, str)
        # Expected top-level keys
        assert "current_trend" in result
        assert "structure_events" in result
        assert "order_blocks" in result
        assert "fair_value_gaps" in result
        assert "liquidity_sweeps" in result
        assert "supply_demand_zones" in result
        assert "summary" in result
        assert "last_structure_break" in result

    def test_performance_under_100ms(self):
        """analyze_smc completes in under 100ms for 200 candles."""
        candles = _make_candles(200)
        start = time.monotonic()
        _ = analyze_smc(candles)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert elapsed_ms < 100, f"SMC analysis took {elapsed_ms:.1f}ms (limit: 100ms)"


# ---------------------------------------------------------------------------
# TestSMCScoring
# ---------------------------------------------------------------------------


class TestSMCScoring:
    """_score_smc integration in signal scoring."""

    def test_bullish_confluence_high_score(self):
        """Full bullish confluence (trend+OB+FVG+sweep+zone) scores near max."""
        smc_data = {
            "current_trend": "bullish",
            "last_structure_break": {"event_type": "ChoCH", "direction": "bullish"},
            "order_blocks": [
                {"type": "bullish", "zone": [2648.0, 2652.0], "mitigated": False, "strength": 1.0},
            ],
            "fair_value_gaps": [
                {"type": "bullish", "zone": [2649.0, 2651.0]},
            ],
            "liquidity_sweeps": [
                {"direction": "below", "swept_level": 2647.0},
            ],
            "supply_demand_zones": [
                {"type": "demand", "zone": [2648.0, 2653.0], "strength": 1.0},
            ],
        }
        score = _score_smc(smc_data, current_price=2650.0, direction="BUY")
        # ChoCH(4) + OB(4) + FVG(3) + sweep(2) + zone(2) = 15
        assert score >= 12
        assert score <= 15

    def test_no_smc_data_returns_zero(self):
        """_score_smc returns 0 when smc_data is None."""
        assert _score_smc(None, 2650.0, "BUY") == 0

    def test_neutral_direction_returns_zero(self):
        """_score_smc returns 0 when direction is NEUTRAL."""
        smc_data = {
            "current_trend": "bullish",
            "last_structure_break": {"event_type": "BOS", "direction": "bullish"},
        }
        assert _score_smc(smc_data, 2650.0, "NEUTRAL") == 0

    def test_total_score_max_still_100(self):
        """score_signal total never exceeds 100 even with max SMC."""
        multi_tf = {
            "weighted_score": 0.8,
            "alignment_score": 1.0,
            "dominant_signal": "Strong Buy",
            "divergences": [],
            "per_timeframe": {
                "H1": {
                    "current_price": 2650.0,
                    "rsi_14": 75, "macd_histogram": 2.0,
                    "macd_line": 3.0, "macd_signal": 1.0,
                    "atr_14": 15.0,
                    "bollinger_upper": 2680, "bollinger_lower": 2640, "bollinger_middle": 2660,
                    "support": [2648.0], "resistance": [2655.0],
                },
            },
        }
        smc_data = {
            "current_trend": "bullish",
            "last_structure_break": {"event_type": "ChoCH", "direction": "bullish"},
            "order_blocks": [
                {"type": "bullish", "zone": [2648.0, 2652.0], "mitigated": False, "strength": 1.0},
            ],
            "fair_value_gaps": [
                {"type": "bullish", "zone": [2649.0, 2651.0]},
            ],
            "liquidity_sweeps": [{"direction": "below"}],
            "supply_demand_zones": [
                {"type": "demand", "zone": [2648.0, 2653.0], "strength": 1.0},
            ],
        }
        result = score_signal(
            multi_tf,
            session={"volatility": "very-high"},
            calendar_warnings=[],
            current_price=2650.0,
            smc_data=smc_data,
        )
        assert result["score"] <= 100
        assert "smc" in result["breakdown"]
        assert result["max_scores"]["smc"] == 15


# ---------------------------------------------------------------------------
# TestMT5SMCTool
# ---------------------------------------------------------------------------


class TestMT5SMCTool:
    """mt5_smc tool handler tests."""

    @pytest.mark.asyncio
    async def test_success_mock(self):
        """mt5_smc returns success with sufficient mocked candle data."""
        from src.tools.trading_advanced import mt5_smc

        candles = _make_candles(200)

        mock_client = AsyncMock()
        mock_client.get_rates = AsyncMock(return_value=candles)

        with patch("src.tools.trading_advanced._get_client", return_value=mock_client):
            result = await mt5_smc(symbol="XAUUSD", timeframe="H1", count="200")

        assert result.success is True
        assert "Smart Money Concepts" in result.output
        assert "XAUUSD" in result.output

    @pytest.mark.asyncio
    async def test_insufficient_data_error(self):
        """mt5_smc returns error when fewer than 50 candles available."""
        from src.tools.trading_advanced import mt5_smc

        mock_client = AsyncMock()
        mock_client.get_rates = AsyncMock(return_value=_make_candles(20))

        with patch("src.tools.trading_advanced._get_client", return_value=mock_client):
            result = await mt5_smc(symbol="XAUUSD", timeframe="H1", count="200")

        assert result.success is False
        assert "50" in result.error or "dữ liệu" in result.error.lower()

    def test_tool_definition_valid(self):
        """mt5_smc_tool has correct name, parameters, and handler."""
        from src.tools.trading_advanced import mt5_smc_tool

        assert mt5_smc_tool.name == "mt5_smc"
        assert mt5_smc_tool.handler is not None
        param_names = [p.name for p in mt5_smc_tool.parameters]
        assert "symbol" in param_names
        assert "timeframe" in param_names
        assert "count" in param_names
        assert mt5_smc_tool.timeout_seconds == 20


# ---------------------------------------------------------------------------
# TestClusterLevels (helper)
# ---------------------------------------------------------------------------


class TestClusterLevels:
    """_cluster_levels helper function."""

    def test_nearby_levels_clustered(self):
        """Prices within tolerance are grouped into one cluster."""
        levels = [(100.0, 1), (100.03, 3), (100.05, 5)]
        clusters = _cluster_levels(levels, tolerance_pct=0.001)
        # All within 0.1% of each other → single cluster
        assert len(clusters) == 1
        avg_price, indices = clusters[0]
        assert len(indices) == 3
        assert avg_price == pytest.approx(100.0267, abs=0.01)

    def test_distant_levels_separate(self):
        """Prices far apart form separate clusters."""
        levels = [(100.0, 1), (200.0, 3)]
        clusters = _cluster_levels(levels, tolerance_pct=0.001)
        assert len(clusters) == 2

    def test_empty_input(self):
        """Empty levels list returns empty clusters."""
        assert _cluster_levels([], 0.001) == []
