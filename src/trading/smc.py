"""Smart Money Concepts (SMC) engine for institutional order flow analysis.

Detects market structure (BOS/ChoCH), order blocks, fair value gaps,
liquidity sweeps, and supply/demand zones from OHLC candle data.

Part 1: Swing points, structure classification, and BOS/ChoCH detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.utils.logging import get_logger

log = get_logger("trading.smc")

_EQUAL_LEVEL_TOLERANCE_PCT = 0.0005  # 0.05% ~ $1.3 at $2650
_IMPULSE_MULTIPLIER = 2.0
_MIN_CANDLES_FOR_SMC = 50


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SwingPoint:
    index: int
    price: float
    swing_type: str  # "HH" / "HL" / "LH" / "LL"
    time: str
    is_high: bool


@dataclass
class StructureEvent:
    event_type: str  # "BOS" / "ChoCH"
    direction: str  # "bullish" / "bearish"
    break_price: float
    break_index: int
    break_time: str


@dataclass
class OrderBlock:
    ob_type: str  # "bullish" / "bearish"
    zone_high: float
    zone_low: float
    origin_index: int
    origin_time: str
    mitigated: bool
    mitigation_index: int | None = None
    strength: float = 1.0


@dataclass
class FairValueGap:
    fvg_type: str  # "bullish" / "bearish"
    zone_high: float
    zone_low: float
    gap_size: float
    candle_index: int
    candle_time: str
    mitigated: bool
    mitigation_pct: float = 0.0
    mitigation_index: int | None = None


@dataclass
class LiquiditySweep:
    direction: str  # "above" / "below"
    swept_level: float
    sweep_index: int
    sweep_time: str
    wick_extreme: float
    close_price: float
    num_touches: int


@dataclass
class SupplyDemandZone:
    zone_type: str  # "supply" / "demand"
    zone_high: float
    zone_low: float
    base_start: int
    base_end: int
    time: str
    tested: bool
    test_count: int = 0
    strength: float = 1.0


@dataclass
class SMCAnalysis:
    swing_points: list[SwingPoint] = field(default_factory=list)
    structure_events: list[StructureEvent] = field(default_factory=list)
    current_trend: str = "undefined"
    order_blocks: list[OrderBlock] = field(default_factory=list)
    fair_value_gaps: list[FairValueGap] = field(default_factory=list)
    liquidity_sweeps: list[LiquiditySweep] = field(default_factory=list)
    supply_demand_zones: list[SupplyDemandZone] = field(default_factory=list)
    active_bullish_obs: int = 0
    active_bearish_obs: int = 0
    active_bullish_fvgs: int = 0
    active_bearish_fvgs: int = 0
    last_structure_break: StructureEvent | None = None


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _average_body_size(
    candles: list[dict[str, Any]], start: int, end: int
) -> float:
    """Average |close - open| over range [start, end). Returns 0.0 if empty."""
    bodies: list[float] = []
    for i in range(max(0, start), min(len(candles), end)):
        bodies.append(abs(candles[i]["close"] - candles[i]["open"]))
    return sum(bodies) / len(bodies) if bodies else 0.0


# ---------------------------------------------------------------------------
# Swing point detection & classification
# ---------------------------------------------------------------------------


def detect_swing_points(
    candles: list[dict[str, Any]], lookback: int = 5
) -> list[SwingPoint]:
    """Detect swing highs and swing lows using a lookback window.

    A swing high at index *i*: ``candles[i].high`` is the **highest** high in
    the window ``[i - lookback, i + lookback]``.  The value must be *strictly*
    greater than every other high in the window (no plateau edges accepted).

    A swing low at index *i*: ``candles[i].low`` is the **lowest** low in the
    same window, again strictly less than every other low.

    A candle can be both a swing high and a swing low (different prices).

    Returns a list of :class:`SwingPoint` sorted by index.  ``swing_type`` is
    set to a placeholder (``"HH"`` for highs, ``"HL"`` for lows) — the real
    classification happens in :func:`classify_swing_points`.
    """
    if not candles or lookback < 1:
        return []

    n = len(candles)
    points: list[SwingPoint] = []

    for i in range(lookback, n - lookback):
        # --- Swing high check ---
        high_i = candles[i]["high"]
        is_swing_high = True
        for j in range(i - lookback, i + lookback + 1):
            if j == i:
                continue
            if candles[j]["high"] >= high_i:
                is_swing_high = False
                break

        if is_swing_high:
            points.append(
                SwingPoint(
                    index=i,
                    price=high_i,
                    swing_type="HH",  # placeholder
                    time=candles[i].get("time", ""),
                    is_high=True,
                )
            )

        # --- Swing low check ---
        low_i = candles[i]["low"]
        is_swing_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j == i:
                continue
            if candles[j]["low"] <= low_i:
                is_swing_low = False
                break

        if is_swing_low:
            points.append(
                SwingPoint(
                    index=i,
                    price=low_i,
                    swing_type="HL",  # placeholder
                    time=candles[i].get("time", ""),
                    is_high=False,
                )
            )

    points.sort(key=lambda p: (p.index, not p.is_high))
    return points


def classify_swing_points(swings: list[SwingPoint]) -> list[SwingPoint]:
    """Classify swing points as HH / HL / LH / LL.

    Compares each swing to the most recent swing of the **same** type
    (high vs. low):

    * Swing highs: higher than previous swing high -> ``"HH"``, otherwise
      ``"LH"``.
    * Swing lows: higher than previous swing low -> ``"HL"``, otherwise
      ``"LL"``.

    The first swing high defaults to ``"HH"`` and the first swing low
    defaults to ``"HL"``.

    Mutates the list in-place and returns it.
    """
    last_high_price: float | None = None
    last_low_price: float | None = None

    for sp in swings:
        if sp.is_high:
            if last_high_price is None:
                sp.swing_type = "HH"
            elif sp.price > last_high_price:
                sp.swing_type = "HH"
            else:
                sp.swing_type = "LH"
            last_high_price = sp.price
        else:
            if last_low_price is None:
                sp.swing_type = "HL"
            elif sp.price > last_low_price:
                sp.swing_type = "HL"
            else:
                sp.swing_type = "LL"
            last_low_price = sp.price

    return swings


# ---------------------------------------------------------------------------
# Structure detection (BOS / ChoCH)
# ---------------------------------------------------------------------------


def detect_structure(
    candles: list[dict[str, Any]],
    swings: list[SwingPoint],
) -> tuple[list[StructureEvent], str]:
    """Detect Break of Structure (BOS) and Change of Character (ChoCH).

    Algorithm
    ---------
    1. Separate *swings* into ordered lists of swing highs and swing lows.
    2. Maintain a ``trend`` state (``"undefined"`` / ``"bullish"`` /
       ``"bearish"``).
    3. Walk through candles sequentially.  For each candle update which swing
       high / swing low is the current "active" level (the most recent swing
       point whose index is strictly *before* the current candle).
    4. On a candle whose **close** breaks through the active level:

       * Close **above** the active swing high:
         - trend was bullish or undefined -> **BOS bullish**
         - trend was bearish -> **ChoCH bullish**
         - set trend = bullish, consume that swing high

       * Close **below** the active swing low:
         - trend was bearish or undefined -> **BOS bearish**
         - trend was bullish -> **ChoCH bearish**
         - set trend = bearish, consume that swing low

    Returns ``(events, current_trend)``.
    """
    if not candles or not swings:
        return [], "undefined"

    # Separate highs and lows, each sorted by index
    swing_highs = [s for s in swings if s.is_high]
    swing_lows = [s for s in swings if not s.is_high]

    if not swing_highs or not swing_lows:
        return [], "undefined"

    events: list[StructureEvent] = []
    trend = "undefined"

    # Pointers into the swing lists — point to the *next* swing to become
    # active.  The "active" swing is the one just before the pointer.
    hi_ptr = 0  # next swing high index into swing_highs
    lo_ptr = 0  # next swing low index into swing_lows

    active_high: SwingPoint | None = None
    active_low: SwingPoint | None = None

    # Track whether the active level has already been "consumed" (broken).
    # Once consumed we don't fire another event until a new swing replaces it.
    high_consumed = False
    low_consumed = False

    for ci in range(len(candles)):
        c = candles[ci]

        # Advance swing high pointer — any swing high whose index < ci
        # becomes the new active level.
        while hi_ptr < len(swing_highs) and swing_highs[hi_ptr].index < ci:
            active_high = swing_highs[hi_ptr]
            high_consumed = False
            hi_ptr += 1

        # Advance swing low pointer
        while lo_ptr < len(swing_lows) and swing_lows[lo_ptr].index < ci:
            active_low = swing_lows[lo_ptr]
            low_consumed = False
            lo_ptr += 1

        close = c["close"]

        # --- Check bullish break (close above active swing high) ---
        if active_high is not None and not high_consumed:
            if close > active_high.price:
                if trend == "bearish":
                    etype = "ChoCH"
                else:
                    etype = "BOS"
                events.append(
                    StructureEvent(
                        event_type=etype,
                        direction="bullish",
                        break_price=active_high.price,
                        break_index=ci,
                        break_time=c.get("time", ""),
                    )
                )
                trend = "bullish"
                high_consumed = True

        # --- Check bearish break (close below active swing low) ---
        if active_low is not None and not low_consumed:
            if close < active_low.price:
                if trend == "bullish":
                    etype = "ChoCH"
                else:
                    etype = "BOS"
                events.append(
                    StructureEvent(
                        event_type=etype,
                        direction="bearish",
                        break_price=active_low.price,
                        break_index=ci,
                        break_time=c.get("time", ""),
                    )
                )
                trend = "bearish"
                low_consumed = True

    return events, trend


# ---------------------------------------------------------------------------
# Order Block detection
# ---------------------------------------------------------------------------


def detect_order_blocks(
    candles: list[dict[str, Any]],
    structure_events: list[StructureEvent],
    max_obs: int = 10,
) -> list[OrderBlock]:
    """Detect order blocks from structure break events.

    For each structure event, scans backward from the break candle to find
    the "origin" candle — the last opposing candle before the impulse move.

    * Bullish OB: last bearish candle (close < open) before a bullish break.
    * Bearish OB: last bullish candle (close > open) before a bearish break.

    Checks mitigation (price returning into the OB zone) on subsequent candles.

    Returns at most *max_obs* order blocks sorted by origin_index descending
    (most recent first).
    """
    if not candles or not structure_events:
        return []

    obs: list[OrderBlock] = []

    for evt in structure_events:
        break_idx = evt.break_index
        direction = evt.direction

        # Scan backward (max 20 candles) to find origin candle
        ob_idx: int | None = None
        scan_start = max(0, break_idx - 20)

        for j in range(break_idx - 1, scan_start - 1, -1):
            c = candles[j]
            if direction == "bullish" and c["close"] < c["open"]:
                # Last bearish candle → bullish OB
                ob_idx = j
                break
            elif direction == "bearish" and c["close"] > c["open"]:
                # Last bullish candle → bearish OB
                ob_idx = j
                break

        if ob_idx is None:
            continue

        origin = candles[ob_idx]
        zone_high = origin["high"]
        zone_low = origin["low"]

        # Strength based on impulse size relative to average body
        impulse_size = abs(candles[break_idx]["close"] - candles[ob_idx]["close"])
        avg_body = _average_body_size(candles, max(0, ob_idx - 20), ob_idx)
        if avg_body > 0:
            strength = min(1.0, impulse_size / (avg_body * _IMPULSE_MULTIPLIER * 3))
        else:
            strength = 1.0

        ob_type = "bullish" if direction == "bullish" else "bearish"

        # Check mitigation — scan forward from origin
        mitigated = False
        mitigation_index: int | None = None

        for k in range(ob_idx + 1, len(candles)):
            if ob_type == "bullish":
                # Bullish OB mitigated when price returns into the zone
                if candles[k]["low"] <= zone_high:
                    mitigated = True
                    mitigation_index = k
                    break
            else:
                # Bearish OB mitigated when price returns into the zone
                if candles[k]["high"] >= zone_low:
                    mitigated = True
                    mitigation_index = k
                    break

        obs.append(
            OrderBlock(
                ob_type=ob_type,
                zone_high=zone_high,
                zone_low=zone_low,
                origin_index=ob_idx,
                origin_time=origin.get("time", ""),
                mitigated=mitigated,
                mitigation_index=mitigation_index,
                strength=round(strength, 4),
            )
        )

    # Most recent first, limited to max_obs
    obs.sort(key=lambda o: o.origin_index, reverse=True)
    return obs[:max_obs]


# ---------------------------------------------------------------------------
# Fair Value Gap detection
# ---------------------------------------------------------------------------


def detect_fair_value_gaps(
    candles: list[dict[str, Any]],
    min_gap_pct: float = 0.0002,
    max_fvgs: int = 15,
) -> list[FairValueGap]:
    """Detect Fair Value Gaps (imbalances) in price action.

    A FVG is a three-candle pattern where the middle candle's body leaves a
    gap between the first and third candle's wicks:

    * **Bullish FVG**: candles[i-1].high < candles[i+1].low (gap up)
    * **Bearish FVG**: candles[i-1].low > candles[i+1].high (gap down)

    Gaps smaller than *min_gap_pct* of mid-price are filtered out.
    Mitigation is checked by scanning forward from the gap.

    Returns at most *max_fvgs* FVGs sorted by candle_index descending.
    """
    if len(candles) < 3:
        return []

    fvgs: list[FairValueGap] = []

    for i in range(1, len(candles) - 1):
        prev_high = candles[i - 1]["high"]
        prev_low = candles[i - 1]["low"]
        next_high = candles[i + 1]["high"]
        next_low = candles[i + 1]["low"]

        fvg_type: str | None = None
        zone_high = 0.0
        zone_low = 0.0

        # Bullish FVG: gap between prev candle high and next candle low
        if prev_high < next_low:
            fvg_type = "bullish"
            zone_low = prev_high
            zone_high = next_low

        # Bearish FVG: gap between prev candle low and next candle high
        elif prev_low > next_high:
            fvg_type = "bearish"
            zone_high = prev_low
            zone_low = next_high

        if fvg_type is None:
            continue

        gap_size = zone_high - zone_low
        mid_price = (zone_high + zone_low) / 2.0
        if mid_price <= 0:
            continue

        # Filter by minimum gap percentage
        if gap_size / mid_price < min_gap_pct:
            continue

        # Check mitigation by scanning forward
        mitigated = False
        mitigation_pct = 0.0
        mitigation_index: int | None = None

        for k in range(i + 2, len(candles)):
            if fvg_type == "bullish":
                # Fully mitigated when price drops to or below zone_low
                if candles[k]["low"] <= zone_low:
                    mitigated = True
                    mitigation_pct = 100.0
                    mitigation_index = k
                    break
                # Partial mitigation — price enters the zone
                elif candles[k]["low"] <= zone_high:
                    filled = zone_high - candles[k]["low"]
                    pct = (filled / gap_size) * 100.0 if gap_size > 0 else 0.0
                    if pct > mitigation_pct:
                        mitigation_pct = round(pct, 2)
                        mitigation_index = k
            else:  # bearish
                # Fully mitigated when price rises to or above zone_high
                if candles[k]["high"] >= zone_high:
                    mitigated = True
                    mitigation_pct = 100.0
                    mitigation_index = k
                    break
                # Partial mitigation — price enters the zone
                elif candles[k]["high"] >= zone_low:
                    filled = candles[k]["high"] - zone_low
                    pct = (filled / gap_size) * 100.0 if gap_size > 0 else 0.0
                    if pct > mitigation_pct:
                        mitigation_pct = round(pct, 2)
                        mitigation_index = k

        fvgs.append(
            FairValueGap(
                fvg_type=fvg_type,
                zone_high=zone_high,
                zone_low=zone_low,
                gap_size=round(gap_size, 8),
                candle_index=i,
                candle_time=candles[i].get("time", ""),
                mitigated=mitigated,
                mitigation_pct=mitigation_pct,
                mitigation_index=mitigation_index,
            )
        )

    # Most recent first, limited to max_fvgs
    fvgs.sort(key=lambda f: f.candle_index, reverse=True)
    return fvgs[:max_fvgs]


# ---------------------------------------------------------------------------
# Level clustering helper
# ---------------------------------------------------------------------------


def _cluster_levels(
    levels: list[tuple[float, int]],
    tolerance_pct: float,
) -> list[tuple[float, list[int]]]:
    """Cluster nearby price levels within *tolerance_pct* of cluster average.

    Parameters
    ----------
    levels:
        List of ``(price, candle_index)`` tuples.
    tolerance_pct:
        Maximum distance (as fraction of average price) for two levels to be
        in the same cluster.

    Returns
    -------
    List of ``(average_price, [candle_indices])`` tuples.
    """
    if not levels:
        return []

    # Sort by price
    sorted_levels = sorted(levels, key=lambda x: x[0])

    clusters: list[tuple[float, list[int]]] = []
    cluster_prices: list[float] = [sorted_levels[0][0]]
    cluster_indices: list[int] = [sorted_levels[0][1]]

    for price, idx in sorted_levels[1:]:
        cluster_avg = sum(cluster_prices) / len(cluster_prices)
        if cluster_avg > 0 and abs(price - cluster_avg) / cluster_avg <= tolerance_pct:
            cluster_prices.append(price)
            cluster_indices.append(idx)
        else:
            # Finalize current cluster
            avg = sum(cluster_prices) / len(cluster_prices)
            clusters.append((round(avg, 8), list(cluster_indices)))
            # Start new cluster
            cluster_prices = [price]
            cluster_indices = [idx]

    # Finalize last cluster
    if cluster_prices:
        avg = sum(cluster_prices) / len(cluster_prices)
        clusters.append((round(avg, 8), list(cluster_indices)))

    return clusters


# ---------------------------------------------------------------------------
# Liquidity sweep detection
# ---------------------------------------------------------------------------


def detect_liquidity_sweeps(
    candles: list[dict[str, Any]],
    swings: list[SwingPoint],
    tolerance_pct: float = _EQUAL_LEVEL_TOLERANCE_PCT,
    min_touches: int = 2,
) -> list[LiquiditySweep]:
    """Detect liquidity sweeps — price briefly pierces a level then reverses.

    1. Clusters equal swing highs and equal swing lows via
       :func:`_cluster_levels`.
    2. For each cluster with >= *min_touches* touches:

       * **Sweep above**: after the last touch, a candle's high exceeds the
         level but the close is back below it — institutions grabbed buy-side
         liquidity.
       * **Sweep below**: a candle's low dips below the level but closes above
         it — sell-side liquidity swept.

    Only the first sweep per cluster is recorded.
    """
    if not candles or not swings:
        return []

    # Separate swing highs and lows
    high_levels: list[tuple[float, int]] = [
        (s.price, s.index) for s in swings if s.is_high
    ]
    low_levels: list[tuple[float, int]] = [
        (s.price, s.index) for s in swings if not s.is_high
    ]

    sweeps: list[LiquiditySweep] = []

    # Check sweep above equal highs
    high_clusters = _cluster_levels(high_levels, tolerance_pct)
    for level, indices in high_clusters:
        if len(indices) < min_touches:
            continue
        last_touch = max(indices)
        for k in range(last_touch + 1, len(candles)):
            c = candles[k]
            if c["high"] > level and c["close"] < level:
                sweeps.append(
                    LiquiditySweep(
                        direction="above",
                        swept_level=round(level, 8),
                        sweep_index=k,
                        sweep_time=c.get("time", ""),
                        wick_extreme=c["high"],
                        close_price=c["close"],
                        num_touches=len(indices),
                    )
                )
                break  # Only first sweep per cluster

    # Check sweep below equal lows
    low_clusters = _cluster_levels(low_levels, tolerance_pct)
    for level, indices in low_clusters:
        if len(indices) < min_touches:
            continue
        last_touch = max(indices)
        for k in range(last_touch + 1, len(candles)):
            c = candles[k]
            if c["low"] < level and c["close"] > level:
                sweeps.append(
                    LiquiditySweep(
                        direction="below",
                        swept_level=round(level, 8),
                        sweep_index=k,
                        sweep_time=c.get("time", ""),
                        wick_extreme=c["low"],
                        close_price=c["close"],
                        num_touches=len(indices),
                    )
                )
                break  # Only first sweep per cluster

    sweeps.sort(key=lambda s: s.sweep_index)
    return sweeps


# ---------------------------------------------------------------------------
# Supply / Demand zone detection
# ---------------------------------------------------------------------------


def _deduplicate_zones(zones: list[SupplyDemandZone]) -> list[SupplyDemandZone]:
    """Merge overlapping zones of the same type.

    Sorts by zone_low and merges consecutive overlapping zones of the same
    type into a single zone spanning the combined range.
    """
    if not zones:
        return []

    sorted_zones = sorted(zones, key=lambda z: z.zone_low)
    merged: list[SupplyDemandZone] = [sorted_zones[0]]

    for z in sorted_zones[1:]:
        prev = merged[-1]
        # Check overlap and same type
        if z.zone_type == prev.zone_type and z.zone_low <= prev.zone_high:
            # Merge — expand the previous zone
            prev.zone_high = max(prev.zone_high, z.zone_high)
            prev.zone_low = min(prev.zone_low, z.zone_low)
            prev.base_start = min(prev.base_start, z.base_start)
            prev.base_end = max(prev.base_end, z.base_end)
            prev.strength = max(prev.strength, z.strength)
            prev.test_count += z.test_count
        else:
            merged.append(z)

    return merged


def detect_supply_demand_zones(
    candles: list[dict[str, Any]],
    impulse_multiplier: float = _IMPULSE_MULTIPLIER,
    max_base_candles: int = 6,
    max_zones: int = 10,
) -> list[SupplyDemandZone]:
    """Detect supply and demand zones using the Drop-Base-Rally / Rally-Base-Drop pattern.

    A **demand zone** (Drop-Base-Rally):
      - Bearish impulse candle(s) within *max_base_candles* before a small
        "base" candle, followed by bullish impulse candle(s) after.

    A **supply zone** (Rally-Base-Drop):
      - Bullish impulse candle(s) before a small base, followed by bearish
        impulse candle(s) after.

    Zone boundaries are defined by the base candle's high/low, expanded to
    include adjacent small candles.  Freshness is reduced each time price
    re-tests the zone.

    Returns at most *max_zones* zones sorted by base_start descending.
    """
    if len(candles) < 5:
        return []

    avg_body = _average_body_size(candles, 0, len(candles))
    if avg_body <= 0:
        return []

    impulse_threshold = avg_body * impulse_multiplier
    zones: list[SupplyDemandZone] = []

    for i in range(2, len(candles) - 2):
        c = candles[i]
        body = abs(c["close"] - c["open"])

        # Base candle: small body
        if body > avg_body * 0.75:
            continue

        # Check for impulse before (within max_base_candles)
        bearish_impulse_before = False
        bullish_impulse_before = False
        for j in range(max(0, i - max_base_candles), i):
            jc = candles[j]
            j_body = abs(jc["close"] - jc["open"])
            if j_body >= impulse_threshold:
                if jc["close"] < jc["open"]:
                    bearish_impulse_before = True
                elif jc["close"] > jc["open"]:
                    bullish_impulse_before = True

        # Check for impulse after (within max_base_candles)
        bearish_impulse_after = False
        bullish_impulse_after = False
        for j in range(i + 1, min(len(candles), i + 1 + max_base_candles)):
            jc = candles[j]
            j_body = abs(jc["close"] - jc["open"])
            if j_body >= impulse_threshold:
                if jc["close"] < jc["open"]:
                    bearish_impulse_after = True
                elif jc["close"] > jc["open"]:
                    bullish_impulse_after = True

        zone_type: str | None = None

        # Drop-Base-Rally → demand zone
        if bearish_impulse_before and bullish_impulse_after:
            zone_type = "demand"
        # Rally-Base-Drop → supply zone
        elif bullish_impulse_before and bearish_impulse_after:
            zone_type = "supply"

        if zone_type is None:
            continue

        # Expand zone to adjacent small candles backward
        zone_high = c["high"]
        zone_low = c["low"]
        base_start = i
        base_end = i

        for j in range(i - 1, max(0, i - max_base_candles) - 1, -1):
            jc = candles[j]
            j_body = abs(jc["close"] - jc["open"])
            if j_body <= avg_body * 0.75:
                zone_high = max(zone_high, jc["high"])
                zone_low = min(zone_low, jc["low"])
                base_start = j
            else:
                break

        # Test freshness — scan forward for re-tests
        strength = 1.0
        tested = False
        test_count = 0

        for k in range(base_end + 1, len(candles)):
            kc = candles[k]
            if zone_type == "demand":
                # Price dips into demand zone
                if kc["low"] <= zone_high and kc["low"] >= zone_low:
                    tested = True
                    test_count += 1
                    strength = max(0.1, strength - 0.25)
            else:  # supply
                # Price rises into supply zone
                if kc["high"] >= zone_low and kc["high"] <= zone_high:
                    tested = True
                    test_count += 1
                    strength = max(0.1, strength - 0.25)

        zones.append(
            SupplyDemandZone(
                zone_type=zone_type,
                zone_high=round(zone_high, 8),
                zone_low=round(zone_low, 8),
                base_start=base_start,
                base_end=base_end,
                time=candles[base_start].get("time", ""),
                tested=tested,
                test_count=test_count,
                strength=round(strength, 4),
            )
        )

    # Deduplicate overlapping zones
    zones = _deduplicate_zones(zones)

    # Most recent first, limited to max_zones
    zones.sort(key=lambda z: z.base_start, reverse=True)
    return zones[:max_zones]


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def analyze_smc(
    candles: list[dict[str, Any]],
    swing_lookback: int = 5,
) -> SMCAnalysis:
    """Run the full Smart Money Concepts analysis pipeline.

    Chains all detectors in sequence:
    1. Swing point detection
    2. Swing classification (HH/HL/LH/LL)
    3. Structure detection (BOS/ChoCH)
    4. Order block detection
    5. Fair value gap detection
    6. Liquidity sweep detection
    7. Supply/demand zone detection

    Returns a complete :class:`SMCAnalysis` with all findings.
    If fewer than *_MIN_CANDLES_FOR_SMC* candles are provided, returns an
    empty analysis.
    """
    if len(candles) < _MIN_CANDLES_FOR_SMC:
        log.warning(
            "insufficient_candles",
            count=len(candles),
            minimum=_MIN_CANDLES_FOR_SMC,
        )
        return SMCAnalysis()

    # 1 & 2. Swing points
    swings = detect_swing_points(candles, lookback=swing_lookback)
    swings = classify_swing_points(swings)

    # 3. Structure
    structure_events, current_trend = detect_structure(candles, swings)

    # 4. Order blocks
    order_blocks = detect_order_blocks(candles, structure_events)

    # 5. Fair value gaps
    fair_value_gaps = detect_fair_value_gaps(candles)

    # 6. Liquidity sweeps
    liquidity_sweeps = detect_liquidity_sweeps(candles, swings)

    # 7. Supply/demand zones
    supply_demand_zones = detect_supply_demand_zones(candles)

    # Count active (unmitigated) OBs and FVGs per direction
    active_bullish_obs = sum(
        1 for ob in order_blocks if not ob.mitigated and ob.ob_type == "bullish"
    )
    active_bearish_obs = sum(
        1 for ob in order_blocks if not ob.mitigated and ob.ob_type == "bearish"
    )
    active_bullish_fvgs = sum(
        1 for fvg in fair_value_gaps if not fvg.mitigated and fvg.fvg_type == "bullish"
    )
    active_bearish_fvgs = sum(
        1 for fvg in fair_value_gaps if not fvg.mitigated and fvg.fvg_type == "bearish"
    )

    last_break = structure_events[-1] if structure_events else None

    return SMCAnalysis(
        swing_points=swings,
        structure_events=structure_events,
        current_trend=current_trend,
        order_blocks=order_blocks,
        fair_value_gaps=fair_value_gaps,
        liquidity_sweeps=liquidity_sweeps,
        supply_demand_zones=supply_demand_zones,
        active_bullish_obs=active_bullish_obs,
        active_bearish_obs=active_bearish_obs,
        active_bullish_fvgs=active_bullish_fvgs,
        active_bearish_fvgs=active_bearish_fvgs,
        last_structure_break=last_break,
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def smc_to_dict(smc: SMCAnalysis, current_price: float = 0.0) -> dict[str, Any]:
    """Convert SMC analysis to a JSON-serializable dictionary.

    Filters to active (unmitigated) order blocks and FVGs, sorts by proximity
    to *current_price*, and returns a summary suitable for API responses or
    LLM context injection.
    """

    def _ob_to_dict(ob: OrderBlock) -> dict[str, Any]:
        return {
            "type": ob.ob_type,
            "zone": [ob.zone_low, ob.zone_high],
            "origin_index": ob.origin_index,
            "origin_time": ob.origin_time,
            "mitigated": ob.mitigated,
            "strength": ob.strength,
        }

    def _fvg_to_dict(fvg: FairValueGap) -> dict[str, Any]:
        return {
            "type": fvg.fvg_type,
            "zone": [fvg.zone_low, fvg.zone_high],
            "gap_size": fvg.gap_size,
            "candle_index": fvg.candle_index,
            "candle_time": fvg.candle_time,
            "mitigated": fvg.mitigated,
            "mitigation_pct": fvg.mitigation_pct,
        }

    def _sweep_to_dict(sweep: LiquiditySweep) -> dict[str, Any]:
        return {
            "direction": sweep.direction,
            "swept_level": sweep.swept_level,
            "sweep_index": sweep.sweep_index,
            "sweep_time": sweep.sweep_time,
            "wick_extreme": sweep.wick_extreme,
            "close_price": sweep.close_price,
            "num_touches": sweep.num_touches,
        }

    def _zone_to_dict(zone: SupplyDemandZone) -> dict[str, Any]:
        return {
            "type": zone.zone_type,
            "zone": [zone.zone_low, zone.zone_high],
            "base_range": [zone.base_start, zone.base_end],
            "time": zone.time,
            "tested": zone.tested,
            "test_count": zone.test_count,
            "strength": zone.strength,
        }

    def _event_to_dict(evt: StructureEvent) -> dict[str, Any]:
        return {
            "event_type": evt.event_type,
            "direction": evt.direction,
            "break_price": evt.break_price,
            "break_index": evt.break_index,
            "break_time": evt.break_time,
        }

    # Filter active (unmitigated) OBs and FVGs
    active_obs = [ob for ob in smc.order_blocks if not ob.mitigated]
    active_fvgs = [fvg for fvg in smc.fair_value_gaps if not fvg.mitigated]

    # Sort by proximity to current price
    if current_price > 0:
        active_obs.sort(
            key=lambda ob: abs((ob.zone_high + ob.zone_low) / 2 - current_price)
        )
        active_fvgs.sort(
            key=lambda fvg: abs((fvg.zone_high + fvg.zone_low) / 2 - current_price)
        )

    # Filter S/D zones: not tested or test_count < 3
    active_zones = [
        z for z in smc.supply_demand_zones if not z.tested or z.test_count < 3
    ]

    # Last structure break
    last_break = None
    if smc.last_structure_break:
        last_break = _event_to_dict(smc.last_structure_break)

    return {
        "current_trend": smc.current_trend,
        "structure_events": [_event_to_dict(e) for e in smc.structure_events[-5:]],
        "last_structure_break": last_break,
        "order_blocks": [_ob_to_dict(ob) for ob in active_obs[:5]],
        "fair_value_gaps": [_fvg_to_dict(fvg) for fvg in active_fvgs[:5]],
        "liquidity_sweeps": [_sweep_to_dict(s) for s in smc.liquidity_sweeps[-5:]],
        "supply_demand_zones": [_zone_to_dict(z) for z in active_zones[:5]],
        "summary": {
            "total_swing_points": len(smc.swing_points),
            "total_structure_events": len(smc.structure_events),
            "active_bullish_obs": smc.active_bullish_obs,
            "active_bearish_obs": smc.active_bearish_obs,
            "active_bullish_fvgs": smc.active_bullish_fvgs,
            "active_bearish_fvgs": smc.active_bearish_fvgs,
            "total_liquidity_sweeps": len(smc.liquidity_sweeps),
            "total_sd_zones": len(smc.supply_demand_zones),
        },
    }
