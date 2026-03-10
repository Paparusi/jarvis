"""Signal Scoring Engine — Quantitative scoring of trade setups.

Produces a score 0-100 from multi-timeframe analysis, session quality,
calendar risk, and Smart Money Concepts confluence. Higher = stronger signal.

Components (total 100):
  - Trend alignment (0-20): HTF trend matches LTF entry direction
  - Momentum (0-15): RSI zone + MACD histogram direction
  - Volatility (0-12): ATR-based, Bollinger squeeze detection
  - Support/Resistance (0-13): proximity to key levels
  - Session quality (0-15): kill zone bonus, dead zone penalty
  - Calendar risk (0-10): high-impact event proximity penalty
  - SMC confluence (0-15): structure, OB, FVG, liquidity, S/D zone alignment
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.utils.logging import get_logger

log = get_logger("trading.signals")

_STRONG_BUY = 80
_BUY = 65
_NEUTRAL_HIGH = 50
_SELL = 35
_STRONG_SELL = 20

# Kill zone hours (UTC)
_KILL_ZONES = {
    (7, 10): "London Open",
    (12, 16): "London+NY Overlap",
}


def score_signal(
    multi_tf: dict[str, Any],
    session: dict[str, Any] | None = None,
    calendar_warnings: list[str] | None = None,
    current_price: float | None = None,
    smc_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a trading signal from multi-timeframe analysis.

    Returns:
        score, direction, recommendation, confidence, breakdown, max_scores
    """
    if "error" in multi_tf:
        return {"score": 0, "error": multi_tf["error"], "recommendation": "NO_DATA"}

    per_tf = multi_tf.get("per_timeframe", {})
    session = session or multi_tf.get("session", {})
    warnings = calendar_warnings or []

    # Reference analysis (H1 primary, fallback to first available)
    ref_tf = per_tf.get("H1") or per_tf.get("M15") or next(iter(per_tf.values()), {})

    if current_price is None:
        current_price = ref_tf.get("current_price", 0)

    # Compute direction first (needed for SMC scoring)
    weighted = multi_tf.get("weighted_score", 0)
    if weighted > 0.15:
        direction = "BUY"
    elif weighted < -0.15:
        direction = "SELL"
    else:
        direction = "NEUTRAL"

    # Score all components (re-weighted for SMC integration)
    trend_score = min(20, _score_trend_alignment(multi_tf, per_tf))
    momentum_score = min(15, _score_momentum(ref_tf))
    volatility_score = min(12, _score_volatility(ref_tf))
    sr_score = min(13, _score_support_resistance(ref_tf, current_price))
    session_score = _score_session(session)
    calendar_score = _score_calendar(warnings)
    smc_score = _score_smc(smc_data, current_price, direction)

    total = (trend_score + momentum_score + volatility_score + sr_score
             + session_score + calendar_score + smc_score)
    total = max(0, min(100, total))

    recommendation = _get_recommendation(total, direction)

    if total >= 70:
        confidence = "Strong"
    elif total >= 50:
        confidence = "Moderate"
    else:
        confidence = "Weak"

    return {
        "score": total,
        "direction": direction,
        "recommendation": recommendation,
        "confidence": confidence,
        "breakdown": {
            "trend_alignment": trend_score,
            "momentum": momentum_score,
            "volatility": volatility_score,
            "support_resistance": sr_score,
            "session_quality": session_score,
            "calendar_risk": calendar_score,
            "smc": smc_score,
        },
        "max_scores": {
            "trend_alignment": 20,
            "momentum": 15,
            "volatility": 12,
            "support_resistance": 13,
            "session_quality": 15,
            "calendar_risk": 10,
            "smc": 15,
        },
        "alignment_score": multi_tf.get("alignment_score", 0),
        "dominant_signal": multi_tf.get("dominant_signal", "Neutral"),
        "divergences": multi_tf.get("divergences", []),
    }


def _score_trend_alignment(multi_tf: dict[str, Any], per_tf: dict[str, Any]) -> int:
    """0-25: Higher timeframes agree with lower timeframes."""
    alignment = multi_tf.get("alignment_score", 0)
    weighted = abs(multi_tf.get("weighted_score", 0))
    divergences = len(multi_tf.get("divergences", []))

    score = int(alignment * 15)
    score += int(weighted * 10)
    score -= divergences * 3

    return max(0, min(25, score))


def _score_momentum(analysis: dict[str, Any]) -> int:
    """0-20: RSI zone + MACD histogram direction."""
    score = 0
    rsi = analysis.get("rsi_14")

    # RSI scoring (0-10)
    if rsi is not None:
        if 40 <= rsi <= 60:
            score += 5
        elif 30 <= rsi < 40 or 60 < rsi <= 70:
            score += 8
        elif rsi < 30 or rsi > 70:
            score += 10

    # MACD histogram (0-10)
    macd_hist = analysis.get("macd_histogram")
    if macd_hist is not None:
        if abs(macd_hist) > 0:
            score += 5
            macd_line = analysis.get("macd_line", 0) or 0
            macd_signal = analysis.get("macd_signal", 0) or 0
            if (macd_line > macd_signal and macd_hist > 0) or \
               (macd_line < macd_signal and macd_hist < 0):
                score += 5

    return max(0, min(20, score))


def _score_volatility(analysis: dict[str, Any]) -> int:
    """0-15: ATR-based and Bollinger squeeze detection."""
    score = 0
    atr = analysis.get("atr_14")
    bb_upper = analysis.get("bollinger_upper")
    bb_lower = analysis.get("bollinger_lower")
    bb_middle = analysis.get("bollinger_middle")

    if atr is not None and atr > 0:
        score += 5

    if bb_upper is not None and bb_lower is not None and bb_middle is not None:
        band_width = (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 0
        if band_width < 0.005:
            score += 10
        elif band_width < 0.01:
            score += 7
        elif band_width < 0.02:
            score += 5
        else:
            score += 3

    return max(0, min(15, score))


def _score_support_resistance(analysis: dict[str, Any], current_price: float) -> int:
    """0-15: Proximity to key S/R levels."""
    if not current_price or current_price <= 0:
        return 0

    score = 0
    supports = analysis.get("support", [])
    resistances = analysis.get("resistance", [])

    for s in supports:
        distance_pct = abs(current_price - s) / current_price * 100
        if distance_pct < 0.1:
            score += 10
            break
        elif distance_pct < 0.3:
            score += 7
            break
        elif distance_pct < 0.5:
            score += 4
            break

    for r in resistances:
        distance_pct = abs(r - current_price) / current_price * 100
        if distance_pct < 0.1:
            score += 5
            break
        elif distance_pct < 0.3:
            score += 3
            break

    return max(0, min(15, score))


def _score_session(session: dict[str, Any]) -> int:
    """0-15: Session quality and kill zone bonus."""
    if session.get("is_dead_zone"):
        return 0

    volatility = session.get("volatility", "low")
    vol_scores = {
        "very-high": 10,
        "high": 8,
        "medium-high": 6,
        "medium": 4,
        "medium-low": 3,
        "low-medium": 3,
        "low": 2,
        "very-low": 0,
    }
    score = vol_scores.get(volatility, 2)

    now_hour = datetime.now(tz=timezone.utc).hour
    for (start, end), _label in _KILL_ZONES.items():
        if start <= now_hour < end:
            score += 5
            break

    return max(0, min(15, score))


def _score_calendar(warnings: list[str]) -> int:
    """0-10: Penalty for high-impact events nearby."""
    if not warnings:
        return 10
    deduction = len(warnings) * 3
    return max(0, 10 - deduction)


def _zone_distance_pct(price: float, zone_low: float, zone_high: float) -> float:
    """Distance from price to nearest zone edge as percentage. 0.0 if inside."""
    if zone_low <= price <= zone_high:
        return 0.0
    nearest = min(abs(price - zone_low), abs(price - zone_high))
    return nearest / price * 100 if price > 0 else 999.0


def _score_smc(
    smc_data: dict[str, Any] | None,
    current_price: float,
    direction: str,
) -> int:
    """0-15: Smart Money Concepts confluence score.

    Components:
      - Structure trend matches direction: +4 (ChoCH=+4, BOS=+3)
      - Price at unmitigated OB in direction: +4
      - Price at unfilled FVG in direction: +3
      - Recent liquidity sweep opposite direction: +2
      - Price at fresh S/D zone in direction: +2
    """
    if not smc_data or direction == "NEUTRAL" or current_price <= 0:
        return 0

    score = 0

    # 1. Structure alignment (0-4)
    trend = smc_data.get("current_trend", "undefined")
    last_break = smc_data.get("last_structure_break")
    if direction == "BUY" and trend == "bullish":
        if last_break and last_break.get("event_type") == "ChoCH":
            score += 4
        else:
            score += 3
    elif direction == "SELL" and trend == "bearish":
        if last_break and last_break.get("event_type") == "ChoCH":
            score += 4
        else:
            score += 3

    # 2. Order Block proximity (0-4)
    for ob in smc_data.get("order_blocks", []):
        if ob.get("mitigated"):
            continue
        zone = ob.get("zone", [0, 0])
        if len(zone) < 2:
            continue
        dist = _zone_distance_pct(current_price, zone[0], zone[1])
        if direction == "BUY" and ob.get("type") == "bullish" and dist < 0.3:
            score += min(4, int(4 * ob.get("strength", 1.0)))
            break
        elif direction == "SELL" and ob.get("type") == "bearish" and dist < 0.3:
            score += min(4, int(4 * ob.get("strength", 1.0)))
            break

    # 3. FVG proximity (0-3)
    for fvg in smc_data.get("fair_value_gaps", []):
        zone = fvg.get("zone", [0, 0])
        if len(zone) < 2:
            continue
        dist = _zone_distance_pct(current_price, zone[0], zone[1])
        if direction == "BUY" and fvg.get("type") == "bullish" and dist < 0.3:
            score += 3
            break
        elif direction == "SELL" and fvg.get("type") == "bearish" and dist < 0.3:
            score += 3
            break

    # 4. Liquidity sweep (0-2)
    for ls in smc_data.get("liquidity_sweeps", []):
        if direction == "BUY" and ls.get("direction") == "below":
            score += 2
            break
        elif direction == "SELL" and ls.get("direction") == "above":
            score += 2
            break

    # 5. Supply/Demand zone (0-2)
    for zone_item in smc_data.get("supply_demand_zones", []):
        zone = zone_item.get("zone", [0, 0])
        if len(zone) < 2:
            continue
        dist = _zone_distance_pct(current_price, zone[0], zone[1])
        if direction == "BUY" and zone_item.get("type") == "demand" and dist < 0.3:
            score += min(2, int(2 * zone_item.get("strength", 1.0)))
            break
        elif direction == "SELL" and zone_item.get("type") == "supply" and dist < 0.3:
            score += min(2, int(2 * zone_item.get("strength", 1.0)))
            break

    return max(0, min(15, score))


def _get_recommendation(score: int, direction: str) -> str:
    """Map score + direction to recommendation."""
    if direction == "NEUTRAL":
        return "NEUTRAL"

    if score >= _STRONG_BUY:
        return f"STRONG_{direction}"
    elif score >= _BUY:
        return direction
    elif score >= _NEUTRAL_HIGH:
        return "NEUTRAL"
    elif score >= _SELL:
        return "NEUTRAL"
    else:
        opposite = "SELL" if direction == "BUY" else "BUY"
        return f"STRONG_{opposite}" if score <= _STRONG_SELL else opposite
