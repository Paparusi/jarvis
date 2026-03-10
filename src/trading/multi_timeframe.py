"""Multi-Timeframe Analysis — Analyze multiple timeframes for confluent signals.

Fetches candles across M15/H1/H4/D1 simultaneously, runs existing
analyze_candles() on each, and computes alignment/divergence scores.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.trading.analysis import analyze_candles, get_market_session
from src.trading.mt5_client import MT5Client
from src.utils.logging import get_logger

log = get_logger("trading.multi_timeframe")

# Higher timeframe = more weight
_TIMEFRAME_WEIGHTS: dict[str, float] = {
    "M15": 0.10,
    "H1": 0.20,
    "H4": 0.30,
    "D1": 0.40,
}

_DEFAULT_TIMEFRAMES = ["M15", "H1", "H4", "D1"]

_MIN_CANDLES: dict[str, int] = {
    "M15": 100,
    "H1": 100,
    "H4": 60,
    "D1": 60,
}


async def analyze_multi_timeframe(
    client: MT5Client,
    symbol: str = "XAUUSD",
    timeframes: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch and analyze candles across multiple timeframes.

    Returns:
        per_timeframe, alignment_score, dominant_signal, divergences,
        weighted_score, session, errors
    """
    tfs = timeframes or _DEFAULT_TIMEFRAMES

    # Fetch all timeframes in parallel
    tasks = {
        tf: client.get_rates(symbol, tf, _MIN_CANDLES.get(tf, 100))
        for tf in tfs
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    per_timeframe: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for tf, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            errors.append(f"{tf}: {result}")
            continue
        if not result or len(result) < 30:
            errors.append(
                f"{tf}: insufficient data ({len(result) if result else 0} candles)"
            )
            continue
        analysis = analyze_candles(result)
        per_timeframe[tf] = analysis

    if not per_timeframe:
        return {"error": "Không thể phân tích: " + "; ".join(errors)}

    alignment = _compute_alignment(per_timeframe, tfs)
    session = get_market_session()

    return {
        "symbol": symbol,
        "per_timeframe": per_timeframe,
        "alignment_score": alignment["alignment_score"],
        "dominant_signal": alignment["dominant_signal"],
        "divergences": alignment["divergences"],
        "weighted_score": alignment["weighted_score"],
        "per_tf_signals": alignment["per_tf_signals"],
        "per_tf_trends": alignment["per_tf_trends"],
        "timeframes_analyzed": list(per_timeframe.keys()),
        "errors": errors,
        "session": session,
    }


def _compute_alignment(
    per_tf: dict[str, dict[str, Any]],
    timeframes: list[str],
) -> dict[str, Any]:
    """Compute alignment score from per-timeframe analyses."""

    signals: dict[str, str] = {}
    trends: dict[str, str] = {}

    for tf, analysis in per_tf.items():
        signals[tf] = analysis.get("signal_summary", "Neutral")
        trends[tf] = analysis.get("trend", "Sideways")

    # Weighted signal scoring: Bullish=+1, Bearish=-1, Neutral=0
    signal_map = {"Bullish": 1.0, "Bearish": -1.0, "Neutral": 0.0}
    weighted_sum = 0.0
    total_weight = 0.0
    for tf, sig in signals.items():
        w = _TIMEFRAME_WEIGHTS.get(tf, 0.1)
        weighted_sum += signal_map.get(sig, 0.0) * w
        total_weight += w

    weighted_score = weighted_sum / total_weight if total_weight > 0 else 0.0

    # Alignment: fraction of timeframes that agree with majority
    signal_values = list(signals.values())
    bullish_count = signal_values.count("Bullish")
    bearish_count = signal_values.count("Bearish")
    neutral_count = signal_values.count("Neutral")
    total = len(signal_values)

    majority = max(bullish_count, bearish_count, neutral_count)
    alignment_score = majority / total if total > 0 else 0.0

    # Dominant signal
    if weighted_score > 0.2:
        dominant = "Bullish"
    elif weighted_score < -0.2:
        dominant = "Bearish"
    else:
        dominant = "Neutral"

    # Detect divergences (opposing signals between timeframes)
    divergences: list[str] = []
    tf_list = [tf for tf in timeframes if tf in per_tf]
    for i in range(len(tf_list)):
        for j in range(i + 1, len(tf_list)):
            tf_a, tf_b = tf_list[i], tf_list[j]
            sig_a = signals.get(tf_a, "Neutral")
            sig_b = signals.get(tf_b, "Neutral")
            if (sig_a == "Bullish" and sig_b == "Bearish") or \
               (sig_a == "Bearish" and sig_b == "Bullish"):
                divergences.append(f"{tf_a} ({sig_a}) vs {tf_b} ({sig_b})")

    return {
        "alignment_score": round(alignment_score, 2),
        "dominant_signal": dominant,
        "weighted_score": round(weighted_score, 3),
        "divergences": divergences,
        "per_tf_signals": signals,
        "per_tf_trends": trends,
    }
