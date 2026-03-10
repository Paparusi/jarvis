"""Trading Analysis — Technical indicators and market session detection.

Pure-Python implementations that work on OHLCV data from MT5 bridge.
No external TA libraries required.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


# ── Session Detection ─────────────────────────────────────────────────

# Sessions defined in UTC hours (inclusive ranges)
_SESSIONS = [
    # (start_h, end_h, name, description, volatility)
    (22, 7, "Sydney", "Phiên Sydney — thanh khoản thấp, thường sideway", "low"),
    (0, 9, "Tokyo", "Phiên Tokyo — thanh khoản trung bình, range building", "low-medium"),
    (7, 16, "London", "Phiên London — thanh khoản cao nhất, breakout chính", "high"),
    (12, 21, "New York", "Phiên New York — volume cao, US data releases", "high"),
]

_OVERLAPS = {
    (0, 7): ("Tokyo+Sydney", "Overlap Tokyo-Sydney — range thường hẹp", "low"),
    (7, 9): ("London+Tokyo", "Overlap London-Tokyo — breakout bắt đầu", "medium-high"),
    (12, 16): ("London+NY", "Overlap London-New York — PEAK activity, best setups", "very-high"),
}

_DEAD_ZONE = (21, 0)  # 21:00-00:00 UTC


def get_market_session(utc_hour: int | None = None) -> dict[str, Any]:
    """Identify the active forex trading session(s) based on UTC hour.

    Returns dict with: session, description, volatility, is_overlap,
    is_dead_zone, recommendation
    """
    if utc_hour is None:
        utc_hour = datetime.now(tz=timezone.utc).hour

    # Dead zone check
    if 21 <= utc_hour or utc_hour < 0:
        if utc_hour >= 21:
            return {
                "session": "Dead Zone",
                "description": "Dead Zone (21:00-00:00 UTC) — spread cao, không nên trade",
                "volatility": "very-low",
                "is_overlap": False,
                "is_dead_zone": True,
                "recommendation": "KHÔNG trade. Chờ phiên Tokyo/London.",
            }

    # Check overlaps first (higher priority)
    for (start, end), (name, desc, vol) in _OVERLAPS.items():
        if start <= utc_hour < end:
            return {
                "session": name,
                "description": desc,
                "volatility": vol,
                "is_overlap": True,
                "is_dead_zone": False,
                "recommendation": _session_recommendation(name, utc_hour),
            }

    # Check individual sessions
    for start, end, name, desc, vol in _SESSIONS:
        if start <= end:
            # Normal range
            if start <= utc_hour < end:
                return {
                    "session": name,
                    "description": desc,
                    "volatility": vol,
                    "is_overlap": False,
                    "is_dead_zone": False,
                    "recommendation": _session_recommendation(name, utc_hour),
                }
        else:
            # Wraps around midnight (e.g., Sydney 22-07)
            if utc_hour >= start or utc_hour < end:
                return {
                    "session": name,
                    "description": desc,
                    "volatility": vol,
                    "is_overlap": False,
                    "is_dead_zone": False,
                    "recommendation": _session_recommendation(name, utc_hour),
                }

    # Fallback
    return {
        "session": "Off-hours",
        "description": "Ngoài giờ giao dịch chính",
        "volatility": "low",
        "is_overlap": False,
        "is_dead_zone": True,
        "recommendation": "Hạn chế giao dịch, spread có thể rộng.",
    }


def _session_recommendation(session: str, hour: int) -> str:
    """Return trading recommendation for the current session."""
    recs = {
        "Sydney": "Range hẹp. Đánh dấu Asian range (high/low) cho London breakout.",
        "Tokyo": "Mark Asian range. Chuẩn bị cho London session.",
        "London": "Phiên chính. Tìm breakout từ Asian range. Kill zone: 07:00-10:00 UTC.",
        "New York": "Continuation hoặc reversal. US data 12:30-14:00 UTC cẩn thận.",
        "London+Tokyo": "London vừa mở. Watch for Judas Swing (false breakout Asian range).",
        "London+NY": "PEAK session. Volume + liquidity cao nhất. Best setups ở đây.",
        "Tokyo+Sydney": "Low volume. Chờ London open.",
    }
    return recs.get(session, "Theo dõi price action.")


# ── Technical Indicators ──────────────────────────────────────────────


def calc_sma(closes: list[float], period: int) -> list[float | None]:
    """Simple Moving Average."""
    result: list[float | None] = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            window = closes[i - period + 1 : i + 1]
            result.append(sum(window) / period)
    return result


def calc_ema(closes: list[float], period: int) -> list[float | None]:
    """Exponential Moving Average."""
    if len(closes) < period:
        return [None] * len(closes)

    result: list[float | None] = [None] * (period - 1)
    # Seed with SMA
    sma = sum(closes[:period]) / period
    result.append(sma)
    multiplier = 2.0 / (period + 1)

    for i in range(period, len(closes)):
        prev = result[-1]
        ema = (closes[i] - prev) * multiplier + prev
        result.append(ema)

    return result


def calc_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Relative Strength Index (Wilder's smoothing)."""
    if len(closes) < period + 1:
        return [None] * len(closes)

    result: list[float | None] = [None] * period

    # Calculate initial average gain/loss
    gains = []
    losses = []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(100.0 - 100.0 / (1.0 + rs))

    # Subsequent values using Wilder's smoothing
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0)
        loss = max(-delta, 0)

        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100.0 - 100.0 / (1.0 + rs))

    return result


def calc_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float | None]:
    """Average True Range."""
    n = len(closes)
    if n < 2:
        return [None] * n

    # True Range
    tr: list[float] = [highs[0] - lows[0]]  # First TR = H-L
    for i in range(1, n):
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    # ATR = Wilder's smoothed average of TR
    result: list[float | None] = [None] * (period - 1)

    if len(tr) < period:
        return [None] * n

    atr = sum(tr[:period]) / period
    result.append(atr)

    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
        result.append(atr)

    return result


def calc_bollinger_bands(
    closes: list[float], period: int = 20, std_dev: float = 2.0
) -> dict[str, list[float | None]]:
    """Bollinger Bands: upper, middle, lower."""
    middle = calc_sma(closes, period)
    upper: list[float | None] = []
    lower: list[float | None] = []

    for i in range(len(closes)):
        if middle[i] is None:
            upper.append(None)
            lower.append(None)
        else:
            window = closes[i - period + 1 : i + 1]
            mean = middle[i]
            variance = sum((x - mean) ** 2 for x in window) / period
            sd = math.sqrt(variance)
            upper.append(mean + std_dev * sd)
            lower.append(mean - std_dev * sd)

    return {"upper": upper, "middle": middle, "lower": lower}


def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, list[float | None]]:
    """MACD: macd_line, signal_line, histogram."""
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)

    # MACD line = EMA(fast) - EMA(slow)
    macd_line: list[float | None] = []
    for ef, es in zip(ema_fast, ema_slow):
        if ef is None or es is None:
            macd_line.append(None)
        else:
            macd_line.append(ef - es)

    # Signal line = EMA of MACD line
    macd_values = [v for v in macd_line if v is not None]
    if len(macd_values) >= signal:
        signal_line_raw = calc_ema(macd_values, signal)
        # Pad with Nones to match original length
        none_count = len(macd_line) - len(macd_values)
        signal_line: list[float | None] = [None] * none_count + signal_line_raw
    else:
        signal_line = [None] * len(macd_line)

    # Histogram = MACD - Signal
    histogram: list[float | None] = []
    for m, s in zip(macd_line, signal_line):
        if m is None or s is None:
            histogram.append(None)
        else:
            histogram.append(m - s)

    return {"macd_line": macd_line, "signal_line": signal_line, "histogram": histogram}


# ── High-Level Analysis ───────────────────────────────────────────────


def detect_support_resistance(
    highs: list[float], lows: list[float], window: int = 20
) -> dict[str, list[float]]:
    """Detect recent support and resistance levels from swing points."""
    n = len(highs)
    resistances: list[float] = []
    supports: list[float] = []

    half = window // 2
    for i in range(half, n - half):
        # Swing high: highest in the window
        if highs[i] == max(highs[i - half : i + half + 1]):
            resistances.append(highs[i])
        # Swing low: lowest in the window
        if lows[i] == min(lows[i - half : i + half + 1]):
            supports.append(lows[i])

    # Keep only the most recent levels
    return {
        "resistance": sorted(set(resistances[-5:]), reverse=True),
        "support": sorted(set(supports[-5:])),
    }


def determine_trend(
    closes: list[float],
    sma_short: list[float | None],
    sma_long: list[float | None],
) -> str:
    """Determine trend: 'Uptrend', 'Downtrend', or 'Sideways'."""
    if not closes or len(closes) < 2:
        return "Sideways"

    short_val = sma_short[-1] if sma_short and sma_short[-1] is not None else None
    long_val = sma_long[-1] if sma_long and sma_long[-1] is not None else None
    current = closes[-1]

    if short_val is not None and long_val is not None:
        if short_val > long_val and current > short_val:
            return "Uptrend"
        elif short_val < long_val and current < short_val:
            return "Downtrend"

    # Fallback: check recent price direction
    recent = closes[-20:] if len(closes) >= 20 else closes
    first_half = sum(recent[: len(recent) // 2]) / max(len(recent) // 2, 1)
    second_half = sum(recent[len(recent) // 2 :]) / max(len(recent) - len(recent) // 2, 1)
    pct_change = (second_half - first_half) / first_half * 100 if first_half else 0

    if pct_change > 0.5:
        return "Uptrend"
    elif pct_change < -0.5:
        return "Downtrend"
    return "Sideways"


def analyze_candles(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Run all indicators on candle data and return structured analysis.

    Input: list of dicts with keys: time, open, high, low, close
    """
    if not candles:
        return {"error": "No candle data"}

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]

    current = closes[-1]
    prev_close = closes[-2] if len(closes) >= 2 else current

    # Indicators
    sma_20 = calc_sma(closes, 20)
    sma_50 = calc_sma(closes, 50)
    ema_12 = calc_ema(closes, 12)
    ema_26 = calc_ema(closes, 26)
    rsi_14 = calc_rsi(closes, 14)
    atr_14 = calc_atr(highs, lows, closes, 14)
    bb = calc_bollinger_bands(closes, 20, 2.0)
    macd = calc_macd(closes)
    sr = detect_support_resistance(highs, lows)
    trend = determine_trend(closes, sma_20, sma_50)

    # Signal summary
    signals: list[str] = []
    rsi_val = rsi_14[-1] if rsi_14[-1] is not None else 50
    if rsi_val > 70:
        signals.append("RSI overbought (>70)")
    elif rsi_val < 30:
        signals.append("RSI oversold (<30)")

    macd_hist = macd["histogram"][-1] if macd["histogram"][-1] is not None else 0
    if macd_hist > 0:
        signals.append("MACD bullish")
    elif macd_hist < 0:
        signals.append("MACD bearish")

    if trend == "Uptrend":
        signals.append("Trend: UP")
    elif trend == "Downtrend":
        signals.append("Trend: DOWN")

    bullish_count = sum(1 for s in signals if "bullish" in s.lower() or "up" in s.lower() or "oversold" in s.lower())
    bearish_count = sum(1 for s in signals if "bearish" in s.lower() or "down" in s.lower() or "overbought" in s.lower())

    if bullish_count > bearish_count:
        signal_summary = "Bullish"
    elif bearish_count > bullish_count:
        signal_summary = "Bearish"
    else:
        signal_summary = "Neutral"

    return {
        "current_price": current,
        "change": current - prev_close,
        "change_pct": (current - prev_close) / prev_close * 100 if prev_close else 0,
        "high_24": max(highs[-24:]) if len(highs) >= 24 else max(highs),
        "low_24": min(lows[-24:]) if len(lows) >= 24 else min(lows),
        "sma_20": sma_20[-1],
        "sma_50": sma_50[-1],
        "ema_12": ema_12[-1],
        "ema_26": ema_26[-1],
        "rsi_14": rsi_14[-1],
        "atr_14": atr_14[-1],
        "bollinger_upper": bb["upper"][-1],
        "bollinger_middle": bb["middle"][-1],
        "bollinger_lower": bb["lower"][-1],
        "macd_line": macd["macd_line"][-1],
        "macd_signal": macd["signal_line"][-1],
        "macd_histogram": macd["histogram"][-1],
        "trend": trend,
        "support": sr["support"],
        "resistance": sr["resistance"],
        "signals": signals,
        "signal_summary": signal_summary,
    }
