"""Session Levels — Key trading levels for XAUUSD analysis.

Calculates Previous Day High/Low/Open/Close, Asian session range,
round numbers, and Fibonacci retracement levels from OHLC candle data.

Pure Python, no external libraries required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.utils.logging import get_logger

log = get_logger("trading.session_levels")

_ROUND_NUMBER_STEP = 10.0  # $10 intervals for gold
_FIB_LEVELS = [0.236, 0.382, 0.5, 0.618, 0.705, 0.786]

# Asian session hours in UTC (22:00 - 07:00, wraps midnight)
_ASIAN_START_HOUR = 22
_ASIAN_END_HOUR = 7


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SessionLevels:
    """Container for all key session-based trading levels."""

    pdh: float = 0.0  # Previous Day High
    pdl: float = 0.0  # Previous Day Low
    pdo: float = 0.0  # Previous Day Open
    pdc: float = 0.0  # Previous Day Close
    asian_high: float = 0.0
    asian_low: float = 0.0
    asian_close: float = 0.0
    asian_swept_high: bool = False
    asian_swept_low: bool = False
    round_numbers: list[float] = field(default_factory=list)
    fib_levels: dict[float, float] = field(default_factory=dict)  # {0.618: 2648.50}
    fib_swing_high: float = 0.0
    fib_swing_low: float = 0.0


# ---------------------------------------------------------------------------
# Previous Day levels
# ---------------------------------------------------------------------------


def calculate_previous_day(candles_d1: list[dict[str, Any]]) -> dict[str, float]:
    """Extract Previous Day High/Low/Open/Close from D1 candles.

    Uses the second-to-last candle since the last D1 candle is the
    current (incomplete) trading day.

    Parameters
    ----------
    candles_d1:
        List of daily (D1) OHLCV candle dicts.  Requires at least 2.

    Returns
    -------
    Dict with keys ``pdh``, ``pdl``, ``pdo``, ``pdc``.
    All values default to 0.0 if insufficient data.
    """
    if len(candles_d1) < 2:
        log.warning("insufficient_d1_candles", count=len(candles_d1), required=2)
        return {"pdh": 0.0, "pdl": 0.0, "pdo": 0.0, "pdc": 0.0}

    prev = candles_d1[-2]
    return {
        "pdh": float(prev["high"]),
        "pdl": float(prev["low"]),
        "pdo": float(prev["open"]),
        "pdc": float(prev["close"]),
    }


# ---------------------------------------------------------------------------
# Asian session range
# ---------------------------------------------------------------------------


def _is_asian_hour(hour: int) -> bool:
    """Check whether a UTC hour falls within the Asian session (22:00-07:00)."""
    return hour >= _ASIAN_START_HOUR or hour < _ASIAN_END_HOUR


def calculate_asian_range(candles_h1: list[dict[str, Any]]) -> dict[str, float]:
    """Calculate the Asian session high, low, and close from H1 candles.

    Asian session = 22:00 - 07:00 UTC (wraps around midnight).
    Scans all H1 candles, filters by hour parsed from the ``"time"`` field.

    Parameters
    ----------
    candles_h1:
        List of H1 OHLCV candle dicts with ISO-format ``"time"`` field.

    Returns
    -------
    Dict with ``asian_high``, ``asian_low``, ``asian_close``.
    All values default to 0.0 if no Asian candles are found.
    """
    asian_candles: list[dict[str, Any]] = []

    for candle in candles_h1:
        time_str = candle.get("time", "")
        if not time_str:
            continue
        try:
            dt = datetime.fromisoformat(time_str)
            hour = dt.hour
        except (ValueError, TypeError):
            continue

        if _is_asian_hour(hour):
            asian_candles.append(candle)

    if not asian_candles:
        log.debug("no_asian_candles_found", total_candles=len(candles_h1))
        return {"asian_high": 0.0, "asian_low": 0.0, "asian_close": 0.0}

    asian_high = max(c["high"] for c in asian_candles)
    asian_low = min(c["low"] for c in asian_candles)
    asian_close = asian_candles[-1]["close"]

    return {
        "asian_high": float(asian_high),
        "asian_low": float(asian_low),
        "asian_close": float(asian_close),
    }


# ---------------------------------------------------------------------------
# Round numbers
# ---------------------------------------------------------------------------


def calculate_round_numbers(
    current_price: float, count: int = 5
) -> list[float]:
    """Find nearest round numbers at ``_ROUND_NUMBER_STEP`` intervals.

    For XAUUSD gold the step is $10.  The function centers a window of
    *count* round numbers around the current price.

    Parameters
    ----------
    current_price:
        Latest market price.
    count:
        Number of round levels to return.

    Returns
    -------
    Sorted list of round-number price levels.

    Example
    -------
    >>> calculate_round_numbers(2653.0, count=5)
    [2640.0, 2650.0, 2660.0, 2670.0, 2680.0]
    """
    if count <= 0 or current_price <= 0:
        return []

    step = _ROUND_NUMBER_STEP
    # Nearest round number below
    base = (current_price // step) * step

    # Build a window that centres around the price
    half = count // 2
    start = base - half * step
    levels = [start + i * step for i in range(count)]

    # If the price sits exactly on a round number, shift so it stays centred
    # Ensure we always return *count* values centred around price
    while levels and levels[-1] < current_price:
        levels.pop(0)
        levels.append(levels[-1] + step)

    return sorted(levels[:count])


# ---------------------------------------------------------------------------
# Fibonacci retracement
# ---------------------------------------------------------------------------


def calculate_fibonacci(
    swing_high: float, swing_low: float
) -> dict[float, float]:
    """Calculate Fibonacci retracement levels between swing extremes.

    ``level_price = swing_low + (swing_high - swing_low) * fib_ratio``

    Parameters
    ----------
    swing_high:
        The higher swing price.
    swing_low:
        The lower swing price.

    Returns
    -------
    Dict mapping each Fibonacci ratio to its price level.
    Empty dict if swing_high <= swing_low.
    """
    if swing_high <= swing_low:
        log.debug(
            "invalid_fib_range",
            swing_high=swing_high,
            swing_low=swing_low,
        )
        return {}

    diff = swing_high - swing_low
    return {
        level: round(swing_low + diff * level, 2) for level in _FIB_LEVELS
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def calculate_all_levels(
    candles_d1: list[dict[str, Any]],
    candles_h1: list[dict[str, Any]],
    current_price: float,
    swing_high: float = 0.0,
    swing_low: float = 0.0,
) -> SessionLevels:
    """Calculate all session-based levels in one call.

    If *swing_high* / *swing_low* are not provided (or zero), the function
    derives them from the recent D1 candle highs and lows.

    Parameters
    ----------
    candles_d1:
        Daily OHLCV candles (>= 2 recommended).
    candles_h1:
        H1 OHLCV candles covering the Asian session.
    current_price:
        Latest market price.
    swing_high:
        Optional explicit swing high for Fibonacci calculation.
    swing_low:
        Optional explicit swing low for Fibonacci calculation.

    Returns
    -------
    Populated :class:`SessionLevels` dataclass.
    """
    # Previous day
    pd = calculate_previous_day(candles_d1)

    # Asian range
    asian = calculate_asian_range(candles_h1)

    # Round numbers
    round_numbers = calculate_round_numbers(current_price, count=5)

    # Derive swing high/low from D1 data when not explicitly provided
    if swing_high == 0.0 and swing_low == 0.0 and len(candles_d1) >= 2:
        recent = candles_d1[-5:] if len(candles_d1) >= 5 else candles_d1
        swing_high = max(c["high"] for c in recent)
        swing_low = min(c["low"] for c in recent)

    # Fibonacci
    fib = calculate_fibonacci(swing_high, swing_low)

    levels = SessionLevels(
        pdh=pd["pdh"],
        pdl=pd["pdl"],
        pdo=pd["pdo"],
        pdc=pd["pdc"],
        asian_high=asian["asian_high"],
        asian_low=asian["asian_low"],
        asian_close=asian["asian_close"],
        round_numbers=round_numbers,
        fib_levels=fib,
        fib_swing_high=swing_high,
        fib_swing_low=swing_low,
    )

    log.debug(
        "session_levels_calculated",
        pdh=levels.pdh,
        pdl=levels.pdl,
        asian_high=levels.asian_high,
        asian_low=levels.asian_low,
        fib_count=len(fib),
    )

    return levels


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def levels_to_dict(levels: SessionLevels) -> dict[str, Any]:
    """Serialize a :class:`SessionLevels` to a JSON-safe dictionary."""
    return {
        "previous_day": {
            "high": levels.pdh,
            "low": levels.pdl,
            "open": levels.pdo,
            "close": levels.pdc,
        },
        "asian_session": {
            "high": levels.asian_high,
            "low": levels.asian_low,
            "close": levels.asian_close,
            "swept_high": levels.asian_swept_high,
            "swept_low": levels.asian_swept_low,
        },
        "round_numbers": levels.round_numbers,
        "fibonacci": {
            "levels": {str(k): v for k, v in levels.fib_levels.items()},
            "swing_high": levels.fib_swing_high,
            "swing_low": levels.fib_swing_low,
        },
    }
