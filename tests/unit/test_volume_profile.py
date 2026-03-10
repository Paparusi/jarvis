"""Tests for Volume Profile module.

Covers profile building (trending/ranging data, edge cases), HVN/LVN
identification, nearest-node lookup, and serialization round-trip.
"""

from __future__ import annotations

import json
import math

import pytest

from src.trading.volume_profile import (
    VolumeProfile,
    build_volume_profile,
    find_nearest_hvn,
    find_nearest_lvn,
    profile_to_dict,
    _MIN_CANDLES,
    _VALUE_AREA_PCT,
    _empty_profile,
)


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_candles(
    n: int = 60,
    base: float = 2650.0,
    trend: float = 0.5,
    volume_base: int = 100,
) -> list[dict]:
    """Generate trending candles with tick_volume using sin waves.

    Similar to the helper in test_smc.py but includes tick_volume variation.
    """
    candles = []
    for i in range(n):
        mid = base + i * trend + 3.0 * math.sin(i * 0.3)
        body = abs(1.5 * math.sin(i * 0.7))
        if i % 3 == 0:
            o, c = mid + body / 2, mid - body / 2  # bearish
        else:
            o, c = mid - body / 2, mid + body / 2  # bullish
        h = max(o, c) + abs(0.9 * math.sin(i * 1.1))
        low = min(o, c) - abs(0.9 * math.sin(i * 0.9))
        candles.append({
            "time": f"2026-03-{(i // 24) + 1:02d}T{i % 24:02d}:00:00",
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(low, 2),
            "close": round(c, 2),
            "tick_volume": volume_base + i * 2,
        })
    return candles


def _make_ranging_candles(
    n: int = 60,
    center: float = 2650.0,
    amplitude: float = 5.0,
    volume_base: int = 100,
) -> list[dict]:
    """Generate ranging (sideways) candles oscillating around *center*.

    Most volume concentrates around the center price, producing a clear
    POC near *center*.
    """
    candles = []
    for i in range(n):
        offset = amplitude * math.sin(i * 0.2)
        mid = center + offset
        body = abs(1.0 * math.sin(i * 0.5))
        if i % 2 == 0:
            o, c = mid + body / 2, mid - body / 2
        else:
            o, c = mid - body / 2, mid + body / 2
        h = max(o, c) + abs(0.6 * math.sin(i * 0.9))
        low = min(o, c) - abs(0.6 * math.sin(i * 0.7))
        # Higher volume near center, lower at extremes
        vol = volume_base + int(50 * (1.0 - abs(offset) / amplitude))
        candles.append({
            "time": f"2026-03-{(i // 24) + 1:02d}T{i % 24:02d}:00:00",
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(low, 2),
            "close": round(c, 2),
            "tick_volume": vol,
        })
    return candles


def _make_bimodal_candles(
    n: int = 60,
    low_center: float = 2640.0,
    high_center: float = 2660.0,
    volume_base: int = 100,
) -> list[dict]:
    """Generate candles with two volume clusters (bimodal distribution).

    First half oscillates near *low_center*, second half near *high_center*,
    with a low-volume gap in between to produce distinct LVN.
    """
    candles = []
    half = n // 2
    for i in range(n):
        if i < half:
            center = low_center
        else:
            center = high_center
        offset = 2.0 * math.sin(i * 0.4)
        mid = center + offset
        body = abs(0.8 * math.sin(i * 0.6))
        if i % 2 == 0:
            o, c = mid + body / 2, mid - body / 2
        else:
            o, c = mid - body / 2, mid + body / 2
        h = max(o, c) + 0.5
        low = min(o, c) - 0.5
        candles.append({
            "time": f"2026-03-{(i // 24) + 1:02d}T{i % 24:02d}:00:00",
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(low, 2),
            "close": round(c, 2),
            "tick_volume": volume_base + i,
        })
    return candles


# ---------------------------------------------------------------------------
# TestBuildVolumeProfile
# ---------------------------------------------------------------------------


class TestBuildVolumeProfile:
    """Volume profile construction from candle data."""

    def test_trending_data_poc(self):
        """POC of trending data falls within the overall price range."""
        candles = _make_candles(60, base=2650.0, trend=0.5)
        vp = build_volume_profile(candles, num_bins=50)

        assert vp.total_volume > 0
        assert vp.price_range[0] < vp.price_range[1]
        # POC must be within the price range
        assert vp.price_range[0] <= vp.poc <= vp.price_range[1]
        # Profile should have 50 bins
        assert len(vp.profile) == 50

    def test_ranging_data_poc(self):
        """POC of ranging data is near the center of oscillation."""
        center = 2650.0
        candles = _make_ranging_candles(60, center=center, amplitude=5.0)
        vp = build_volume_profile(candles, num_bins=50)

        assert vp.total_volume > 0
        # POC should be near center (within amplitude)
        assert abs(vp.poc - center) < 6.0, (
            f"POC {vp.poc} too far from center {center}"
        )

    def test_vah_above_val(self):
        """VAH is always >= VAL."""
        candles = _make_candles(60)
        vp = build_volume_profile(candles, num_bins=50)

        assert vp.vah >= vp.val
        # VAH and VAL should be within the price range
        assert vp.val >= vp.price_range[0]
        assert vp.vah <= vp.price_range[1]

    def test_value_area_covers_70pct(self):
        """The Value Area bins contain at least 70% of total volume."""
        candles = _make_candles(80, base=2650.0, trend=0.3)
        vp = build_volume_profile(candles, num_bins=50)

        # Sum volume of bins whose midpoints fall within [VAL, VAH]
        va_volume = 0.0
        for price, vol in vp.profile:
            if vp.val <= price <= vp.vah:
                va_volume += vol

        assert vp.total_volume > 0
        va_pct = va_volume / vp.total_volume
        assert va_pct >= _VALUE_AREA_PCT - 0.01, (
            f"Value Area covers {va_pct:.2%} — expected >= {_VALUE_AREA_PCT:.0%}"
        )

    def test_insufficient_data_returns_empty(self):
        """Fewer than _MIN_CANDLES candles returns an empty profile."""
        candles = _make_candles(10)
        vp = build_volume_profile(candles, num_bins=50)

        assert vp.poc == 0.0
        assert vp.vah == 0.0
        assert vp.val == 0.0
        assert vp.hvn == []
        assert vp.lvn == []
        assert vp.profile == []
        assert vp.total_volume == 0.0
        assert vp.price_range == (0.0, 0.0)

    def test_single_bin_edge_case(self):
        """num_bins=1 produces a valid profile with a single bin."""
        candles = _make_candles(30)
        vp = build_volume_profile(candles, num_bins=1)

        assert len(vp.profile) == 1
        assert vp.total_volume > 0
        # With a single bin, POC = that bin's midpoint
        assert vp.poc == vp.profile[0][0]
        # VAH and VAL span the full range
        assert vp.val == vp.price_range[0]
        assert vp.vah == vp.price_range[1]


# ---------------------------------------------------------------------------
# TestHVNLVN
# ---------------------------------------------------------------------------


class TestHVNLVN:
    """High Volume Node and Low Volume Node identification."""

    def test_hvn_at_high_volume_prices(self):
        """HVN entries have volume above the average bin volume."""
        candles = _make_ranging_candles(60, center=2650.0)
        vp = build_volume_profile(candles, num_bins=50)

        assert len(vp.hvn) > 0, "Expected at least one HVN in ranging data"
        # All HVN prices must be within the price range
        for price in vp.hvn:
            assert vp.price_range[0] <= price <= vp.price_range[1]

    def test_lvn_at_low_volume_prices(self):
        """LVN entries have volume below half the average bin volume."""
        candles = _make_bimodal_candles(60, low_center=2640.0, high_center=2660.0)
        vp = build_volume_profile(candles, num_bins=50)

        # Bimodal distribution should produce LVN in the gap between clusters
        assert len(vp.lvn) > 0, "Expected at least one LVN in bimodal data"
        for price in vp.lvn:
            assert vp.price_range[0] <= price <= vp.price_range[1]

    def test_find_nearest_hvn(self):
        """find_nearest_hvn returns the closest HVN to a given price."""
        candles = _make_ranging_candles(60, center=2650.0)
        vp = build_volume_profile(candles, num_bins=50)

        assert len(vp.hvn) > 0

        # Query at POC — should find a nearby HVN
        nearest = find_nearest_hvn(vp.poc, vp)
        assert nearest is not None
        assert isinstance(nearest, float)
        # The nearest HVN should be in the hvn list
        assert nearest in vp.hvn

    def test_find_nearest_lvn(self):
        """find_nearest_lvn returns the closest LVN to a given price."""
        candles = _make_bimodal_candles(60, low_center=2640.0, high_center=2660.0)
        vp = build_volume_profile(candles, num_bins=50)

        assert len(vp.lvn) > 0

        # Query at the midpoint between the two clusters
        mid_gap = 2650.0
        nearest = find_nearest_lvn(mid_gap, vp)
        assert nearest is not None
        assert isinstance(nearest, float)
        assert nearest in vp.lvn

    def test_find_nearest_hvn_empty(self):
        """find_nearest_hvn returns None when HVN list is empty."""
        vp = _empty_profile()
        assert find_nearest_hvn(2650.0, vp) is None

    def test_find_nearest_lvn_empty(self):
        """find_nearest_lvn returns None when LVN list is empty."""
        vp = _empty_profile()
        assert find_nearest_lvn(2650.0, vp) is None


# ---------------------------------------------------------------------------
# TestSerialization
# ---------------------------------------------------------------------------


class TestSerialization:
    """Volume profile serialization to dict and round-trip."""

    def test_profile_to_dict_keys(self):
        """profile_to_dict output has all expected keys and is JSON-serializable."""
        candles = _make_candles(60)
        vp = build_volume_profile(candles, num_bins=50)
        d = profile_to_dict(vp)

        # Check all expected keys
        assert "poc" in d
        assert "vah" in d
        assert "val" in d
        assert "hvn" in d
        assert "lvn" in d
        assert "profile" in d
        assert "total_volume" in d
        assert "price_range" in d

        # Sub-structure checks
        assert "low" in d["price_range"]
        assert "high" in d["price_range"]
        assert isinstance(d["profile"], list)
        if d["profile"]:
            assert "price" in d["profile"][0]
            assert "volume" in d["profile"][0]

        # Must be JSON-serializable
        serialized = json.dumps(d)
        assert isinstance(serialized, str)

    def test_roundtrip(self):
        """Serialized profile preserves key values through JSON round-trip."""
        candles = _make_candles(60)
        vp = build_volume_profile(candles, num_bins=50)
        d = profile_to_dict(vp)

        # Serialize and deserialize
        serialized = json.dumps(d)
        restored = json.loads(serialized)

        # Verify key values survived the round-trip
        assert restored["poc"] == pytest.approx(vp.poc, abs=1e-6)
        assert restored["vah"] == pytest.approx(vp.vah, abs=1e-6)
        assert restored["val"] == pytest.approx(vp.val, abs=1e-6)
        assert restored["total_volume"] == pytest.approx(vp.total_volume, abs=1e-6)
        assert restored["price_range"]["low"] == pytest.approx(vp.price_range[0], abs=1e-6)
        assert restored["price_range"]["high"] == pytest.approx(vp.price_range[1], abs=1e-6)
        assert len(restored["profile"]) == len(vp.profile)
        assert len(restored["hvn"]) == len(vp.hvn)
        assert len(restored["lvn"]) == len(vp.lvn)
