"""Tests for Confluence Scoring module.

Covers factor collection from SMC/session/volume sources, zone clustering
and scoring, trade setup SL/TP/R:R calculations, and rank/filter logic.
"""

from __future__ import annotations

import pytest

from src.trading.confluence import (
    ConfluenceFactor,
    ScoredZone,
    collect_all_factors,
    score_zones,
    calculate_trade_setup,
    generate_invalidation,
    rank_and_filter,
    _PROXIMITY_PCT,
)


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_smc_data(
    obs: list[dict] | None = None,
    fvgs: list[dict] | None = None,
    sd_zones: list[dict] | None = None,
    sweeps: list[dict] | None = None,
    events: list[dict] | None = None,
) -> dict:
    """Build an smc_data dict matching smc_to_dict() output structure."""
    return {
        "order_blocks": obs or [],
        "fair_value_gaps": fvgs or [],
        "supply_demand_zones": sd_zones or [],
        "liquidity_sweeps": sweeps or [],
        "structure_events": events or [],
        "current_trend": "bullish",
    }


def _make_session_levels(
    pdh: float = 2685.0,
    pdl: float = 2645.0,
    pdo: float = 2660.0,
    pdc: float = 2670.0,
    asian_high: float = 2680.0,
    asian_low: float = 2650.0,
    round_numbers: list[float] | None = None,
    fib_levels: dict[str, float] | None = None,
) -> dict:
    """Build a session_levels dict matching levels_to_dict() output."""
    return {
        "previous_day": {
            "high": pdh,
            "low": pdl,
            "open": pdo,
            "close": pdc,
        },
        "asian_session": {
            "high": asian_high,
            "low": asian_low,
            "close": 2665.0,
            "swept_high": False,
            "swept_low": False,
        },
        "round_numbers": round_numbers or [2640.0, 2650.0, 2660.0, 2670.0, 2680.0],
        "fibonacci": {
            "levels": fib_levels or {
                "0.236": 2623.6,
                "0.382": 2638.2,
                "0.5": 2650.0,
                "0.618": 2661.8,
                "0.705": 2670.5,
                "0.786": 2678.6,
            },
            "swing_high": 2700.0,
            "swing_low": 2600.0,
        },
    }


def _make_volume_profile(
    poc: float = 2660.0,
    vah: float = 2675.0,
    val: float = 2645.0,
    hvn: list[float] | None = None,
) -> dict:
    """Build a volume_profile dict matching profile_to_dict() output."""
    return {
        "poc": poc,
        "vah": vah,
        "val": val,
        "hvn": hvn or [2655.0, 2665.0],
        "lvn": [2640.0],
    }


# ---------------------------------------------------------------------------
# TestCollectFactors
# ---------------------------------------------------------------------------


class TestCollectFactors:
    """Tests for collect_all_factors."""

    def test_smc_order_blocks_extracted(self):
        """OBs from smc_data create factors with weight 25."""
        smc = _make_smc_data(obs=[
            {"type": "bearish", "zone": [2670.0, 2680.0], "mitigated": False, "strength": 0.8},
            {"type": "bullish", "zone": [2640.0, 2650.0], "mitigated": False, "strength": 0.9},
            # Mitigated OB should be excluded
            {"type": "bearish", "zone": [2690.0, 2700.0], "mitigated": True, "strength": 0.5},
        ])
        factors = collect_all_factors(smc, {}, {}, 2660.0, "neutral")

        ob_factors = [f for f in factors if "OB" in f.name]
        assert len(ob_factors) == 2  # Only unmitigated
        assert all(f.weight == 25 for f in ob_factors)
        assert all(f.category == "smc" for f in ob_factors)

        # Bearish OB → sell, Bullish OB → buy
        bearish_obs = [f for f in ob_factors if f.direction == "sell"]
        bullish_obs = [f for f in ob_factors if f.direction == "buy"]
        assert len(bearish_obs) == 1
        assert len(bullish_obs) == 1

        # Price should be midpoint of zone
        assert bearish_obs[0].price == pytest.approx(2675.0)
        assert bullish_obs[0].price == pytest.approx(2645.0)

    def test_session_levels_extracted(self):
        """PDH/PDL create factors with weight 15."""
        session = _make_session_levels(pdh=2685.0, pdl=2645.0)
        factors = collect_all_factors({}, session, {}, 2660.0, "neutral")

        pdh_factors = [f for f in factors if f.name == "PDH"]
        pdl_factors = [f for f in factors if f.name == "PDL"]

        assert len(pdh_factors) == 1
        assert pdh_factors[0].weight == 15
        assert pdh_factors[0].price == 2685.0
        assert pdh_factors[0].direction == "sell"
        assert pdh_factors[0].category == "session"

        assert len(pdl_factors) == 1
        assert pdl_factors[0].weight == 15
        assert pdl_factors[0].price == 2645.0
        assert pdl_factors[0].direction == "buy"

    def test_volume_profile_extracted(self):
        """POC creates factor with weight 15."""
        vol = _make_volume_profile(poc=2660.0, vah=2675.0, val=2645.0)
        factors = collect_all_factors({}, {}, vol, 2660.0, "neutral")

        poc_factors = [f for f in factors if f.name == "POC"]
        assert len(poc_factors) == 1
        assert poc_factors[0].weight == 15
        assert poc_factors[0].price == 2660.0
        assert poc_factors[0].category == "volume"
        assert poc_factors[0].direction == "both"

        # VAH and VAL should also be present
        vah_factors = [f for f in factors if f.name == "VAH"]
        val_factors = [f for f in factors if f.name == "VAL"]
        assert len(vah_factors) == 1
        assert vah_factors[0].weight == 12
        assert len(val_factors) == 1
        assert val_factors[0].weight == 12

    def test_fib_levels_extracted(self):
        """Fib 0.618 creates factor with weight 20."""
        session = _make_session_levels(
            pdh=0.0, pdl=0.0, pdo=0.0, pdc=0.0,
            asian_high=0.0, asian_low=0.0,
            round_numbers=[],
            fib_levels={"0.618": 2661.8, "0.5": 2650.0},
        )
        factors = collect_all_factors({}, session, {}, 2660.0, "neutral")

        fib_618 = [f for f in factors if f.name == "Fib 0.618"]
        fib_500 = [f for f in factors if f.name == "Fib 0.5"]

        assert len(fib_618) == 1
        assert fib_618[0].weight == 20
        assert fib_618[0].price == 2661.8
        assert fib_618[0].category == "fib"

        assert len(fib_500) == 1
        assert fib_500[0].weight == 12

    def test_empty_inputs(self):
        """Empty dicts return empty list."""
        factors = collect_all_factors({}, {}, {}, 2660.0, "neutral")
        assert factors == []


# ---------------------------------------------------------------------------
# TestScoreZones
# ---------------------------------------------------------------------------


class TestScoreZones:
    """Tests for score_zones."""

    def test_single_cluster(self):
        """Factors at similar prices form one zone."""
        factors = [
            ConfluenceFactor("Bearish OB", "smc", 2675.0, 25, "sell"),
            ConfluenceFactor("Fib 0.618", "fib", 2675.5, 20, "both"),
            ConfluenceFactor("VAH", "volume", 2676.0, 12, "sell"),
        ]
        zones = score_zones(factors, proximity_pct=_PROXIMITY_PCT)

        assert len(zones) == 1
        assert zones[0].confluence_score == 25 + 20 + 12
        assert len(zones[0].factors) == 3
        assert zones[0].price_low < 2675.0
        assert zones[0].price_high > 2676.0

    def test_multiple_clusters(self):
        """Distant factors form separate zones."""
        factors = [
            ConfluenceFactor("Bearish OB", "smc", 2700.0, 25, "sell"),
            ConfluenceFactor("PDL", "session", 2600.0, 15, "buy"),
        ]
        zones = score_zones(factors, proximity_pct=_PROXIMITY_PCT)

        assert len(zones) == 2
        # Should be sorted by score descending
        assert zones[0].confluence_score >= zones[1].confluence_score

    def test_score_capped_at_100(self):
        """Many factors don't exceed score of 100."""
        factors = [
            ConfluenceFactor("OB", "smc", 2660.0, 25, "buy"),
            ConfluenceFactor("FVG", "smc", 2660.1, 20, "buy"),
            ConfluenceFactor("SD", "smc", 2660.2, 20, "buy"),
            ConfluenceFactor("Fib", "fib", 2660.3, 20, "both"),
            ConfluenceFactor("POC", "volume", 2660.0, 15, "both"),
            ConfluenceFactor("PDH", "session", 2660.1, 15, "sell"),
        ]
        zones = score_zones(factors, proximity_pct=_PROXIMITY_PCT)

        # Total raw weight = 25+20+20+20+15+15 = 115, but capped at 100
        assert len(zones) == 1
        assert zones[0].confluence_score == 100

    def test_direction_majority(self):
        """Buy factors outnumber sell = buy zone."""
        factors = [
            ConfluenceFactor("Bullish OB", "smc", 2650.0, 25, "buy"),
            ConfluenceFactor("PDL", "session", 2650.2, 15, "buy"),
            ConfluenceFactor("Fib 0.618", "fib", 2650.1, 20, "both"),
            ConfluenceFactor("VAL", "volume", 2650.3, 12, "buy"),
            ConfluenceFactor("Asian Low", "session", 2650.0, 12, "sell"),  # minority
        ]
        zones = score_zones(factors, proximity_pct=_PROXIMITY_PCT)

        assert len(zones) == 1
        # 3 buy vs 1 sell → buy
        assert zones[0].direction == "buy"


# ---------------------------------------------------------------------------
# TestTradeSetup
# ---------------------------------------------------------------------------


class TestTradeSetup:
    """Tests for calculate_trade_setup."""

    def test_buy_zone_sl_tp(self):
        """SL below zone, TP1 at 1:1, TP2 at 2.5:1 for buy zones."""
        zone = ScoredZone(
            zone_id="zone_1",
            direction="buy",
            price_high=2652.0,
            price_low=2648.0,
            confluence_score=60,
            factors=[],
        )
        atr = 10.0
        result = calculate_trade_setup(zone, current_price=2660.0, atr=atr)

        entry = (2652.0 + 2648.0) / 2.0  # 2650.0
        expected_sl = 2648.0 - 0.5 * 10.0  # 2643.0
        risk = entry - expected_sl  # 7.0
        expected_tp1 = entry + risk * 1.0  # 2657.0
        expected_tp2 = entry + risk * 2.5  # 2667.5

        assert result.sl_price == pytest.approx(expected_sl, abs=0.01)
        assert result.tp1_price == pytest.approx(expected_tp1, abs=0.01)
        assert result.tp2_price == pytest.approx(expected_tp2, abs=0.01)
        assert result.sl_price < zone.price_low

    def test_sell_zone_sl_tp(self):
        """SL above zone, TP1/TP2 below for sell zones."""
        zone = ScoredZone(
            zone_id="zone_1",
            direction="sell",
            price_high=2682.0,
            price_low=2678.0,
            confluence_score=55,
            factors=[],
        )
        atr = 10.0
        result = calculate_trade_setup(zone, current_price=2670.0, atr=atr)

        entry = (2682.0 + 2678.0) / 2.0  # 2680.0
        expected_sl = 2682.0 + 0.5 * 10.0  # 2687.0
        risk = expected_sl - entry  # 7.0
        expected_tp1 = entry - risk * 1.0  # 2673.0
        expected_tp2 = entry - risk * 2.5  # 2662.5

        assert result.sl_price == pytest.approx(expected_sl, abs=0.01)
        assert result.tp1_price == pytest.approx(expected_tp1, abs=0.01)
        assert result.tp2_price == pytest.approx(expected_tp2, abs=0.01)
        assert result.sl_price > zone.price_high

    def test_rr_ratio_calculated(self):
        """R:R matches expected 2.5:1 for TP2."""
        zone = ScoredZone(
            zone_id="zone_1",
            direction="buy",
            price_high=2652.0,
            price_low=2648.0,
            confluence_score=60,
            factors=[],
        )
        result = calculate_trade_setup(zone, current_price=2660.0, atr=10.0)

        # R:R should be 2.5 for TP2
        assert result.rr_ratio == pytest.approx(2.5, abs=0.01)


# ---------------------------------------------------------------------------
# TestInvalidation
# ---------------------------------------------------------------------------


class TestInvalidation:
    """Tests for generate_invalidation."""

    def test_buy_invalidation(self):
        """Buy zone invalidation is below the zone."""
        zone = ScoredZone(
            zone_id="zone_1", direction="buy",
            price_high=2652.0, price_low=2648.0,
            confluence_score=60, factors=[],
        )
        text = generate_invalidation(zone, atr=10.0)
        assert "below" in text
        assert "2638.00" in text  # 2648 - 10

    def test_sell_invalidation(self):
        """Sell zone invalidation is above the zone."""
        zone = ScoredZone(
            zone_id="zone_1", direction="sell",
            price_high=2682.0, price_low=2678.0,
            confluence_score=55, factors=[],
        )
        text = generate_invalidation(zone, atr=10.0)
        assert "above" in text
        assert "2692.00" in text  # 2682 + 10


# ---------------------------------------------------------------------------
# TestRankFilter
# ---------------------------------------------------------------------------


class TestRankFilter:
    """Tests for rank_and_filter."""

    def _make_zones(self, scores: list[int]) -> list[ScoredZone]:
        """Create zones with the given scores for testing."""
        return [
            ScoredZone(
                zone_id=f"zone_{i + 1}",
                direction="buy",
                price_high=2650.0 + i * 10,
                price_low=2648.0 + i * 10,
                confluence_score=s,
                factors=[],
            )
            for i, s in enumerate(scores)
        ]

    def test_filters_below_min(self):
        """Zones below min_score=40 are removed."""
        zones = self._make_zones([80, 55, 35, 20, 10])
        result = rank_and_filter(zones, min_score=40)

        assert len(result) == 2
        assert all(z.confluence_score >= 40 for z in result)

    def test_limits_max_zones(self):
        """Only top N returned."""
        zones = self._make_zones([90, 80, 70, 60, 50, 45])
        result = rank_and_filter(zones, min_score=40, max_zones=3)

        assert len(result) == 3
        assert result[0].confluence_score == 90
        assert result[1].confluence_score == 80
        assert result[2].confluence_score == 70

    def test_sorted_by_score(self):
        """Highest score first."""
        zones = self._make_zones([45, 90, 60, 75, 50])
        result = rank_and_filter(zones, min_score=40)

        scores = [z.confluence_score for z in result]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == 90
