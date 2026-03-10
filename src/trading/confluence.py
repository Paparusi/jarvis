"""Confluence Scoring — merge SMC, session, and volume signals into scored trade zones.

Collects factors from all analysis sources, clusters them by price proximity,
scores each cluster, and populates SL/TP/R:R for actionable trade setups.

Designed for XAUUSD (gold) but works with any instrument given appropriate
proximity and ATR values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.utils.logging import get_logger

log = get_logger("trading.confluence")

_PROXIMITY_PCT = 0.15  # 0.15% ~ $4 at $2650


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ConfluenceFactor:
    name: str           # "H1 Bearish OB", "Fib 0.618", "PDH"
    category: str       # "smc", "session", "volume", "fib", "round"
    price: float        # Price level or zone midpoint
    weight: int         # Points contribution (5-25)
    direction: str      # "buy", "sell", "both"


@dataclass
class ScoredZone:
    zone_id: str
    direction: str              # "buy" / "sell"
    price_high: float
    price_low: float
    confluence_score: int       # 0-100
    factors: list[ConfluenceFactor]
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    rr_ratio: float = 0.0
    reasoning: str = ""
    invalidation: str = ""


# ---------------------------------------------------------------------------
# Weight lookup tables
# ---------------------------------------------------------------------------

_SMC_WEIGHTS = {
    "order_block": 25,
    "fvg": 20,
    "supply_demand": 20,
    "liquidity_sweep": 20,
    "structure": 15,
}

_SESSION_WEIGHTS = {
    "pdh": 15,
    "pdl": 15,
    "asian_high": 12,
    "asian_low": 12,
    "pdo": 10,
    "pdc": 10,
}

_VOLUME_WEIGHTS = {
    "poc": 15,
    "vah": 12,
    "val": 12,
    "hvn": 10,
}

_FIB_WEIGHTS = {
    "0.618": 20,
    "0.705": 18,
    "0.786": 15,
    "0.5": 12,
    "0.382": 10,
    "0.236": 8,
}

_ROUND_WEIGHT = 8
_HTF_BIAS_WEIGHT = 10


# ---------------------------------------------------------------------------
# Factor collection
# ---------------------------------------------------------------------------


def collect_all_factors(
    smc_data: dict,
    session_levels: dict,
    volume_profile: dict,
    current_price: float,
    htf_bias: str,
) -> list[ConfluenceFactor]:
    """Collect all factors from all analysis sources.

    Weight guide:
    SMC: OB=25, FVG=20, S/D=20, liquidity_sweep=20, structure=15
    Session: PDH/PDL=15, Asian H/L=12, PDO/PDC=10
    Volume: POC=15, VAH/VAL=12, HVN=10
    Fib: 0.618=20, 0.705=18, 0.786=15, 0.5=12, 0.382=10
    Round: 8
    HTF bias alignment: 10

    Parameters
    ----------
    smc_data:
        Dict from smc_to_dict() with keys: order_blocks, fvg (or
        fair_value_gaps), supply_demand_zones, liquidity_sweeps,
        bos_choch (or structure_events).
    session_levels:
        Dict from levels_to_dict() with keys: previous_day, asian_session,
        round_numbers, fibonacci.
    volume_profile:
        Dict from profile_to_dict() with keys: poc, vah, val, hvn, lvn.
    current_price:
        Latest market price.
    htf_bias:
        Higher time-frame bias: "bullish", "bearish", or "neutral".

    Returns
    -------
    List of ConfluenceFactor instances collected from all sources.
    """
    factors: list[ConfluenceFactor] = []

    # --- SMC: Order Blocks ---
    for ob in smc_data.get("order_blocks", []):
        ob_type = ob.get("type", "")
        mitigated = ob.get("mitigated", True)
        if mitigated:
            continue
        zone = ob.get("zone", [])
        if len(zone) >= 2:
            midpoint = (zone[0] + zone[1]) / 2.0
        elif "high" in ob and "low" in ob:
            midpoint = (ob["high"] + ob["low"]) / 2.0
        else:
            continue

        direction = "buy" if ob_type == "bullish" else "sell"
        factors.append(ConfluenceFactor(
            name=f"{ob_type.title()} OB",
            category="smc",
            price=midpoint,
            weight=_SMC_WEIGHTS["order_block"],
            direction=direction,
        ))

    # --- SMC: Fair Value Gaps ---
    fvg_key = "fair_value_gaps" if "fair_value_gaps" in smc_data else "fvg"
    for fvg in smc_data.get(fvg_key, []):
        fvg_type = fvg.get("type", "")
        mitigated = fvg.get("mitigated", True)
        if mitigated:
            continue
        zone = fvg.get("zone", [])
        if len(zone) >= 2:
            midpoint = (zone[0] + zone[1]) / 2.0
        elif "high" in fvg and "low" in fvg:
            midpoint = (fvg["high"] + fvg["low"]) / 2.0
        else:
            continue

        direction = "buy" if fvg_type == "bullish" else "sell"
        factors.append(ConfluenceFactor(
            name=f"{fvg_type.title()} FVG",
            category="smc",
            price=midpoint,
            weight=_SMC_WEIGHTS["fvg"],
            direction=direction,
        ))

    # --- SMC: Supply/Demand Zones ---
    for sd in smc_data.get("supply_demand_zones", []):
        sd_type = sd.get("type", "")
        zone = sd.get("zone", [])
        if len(zone) >= 2:
            midpoint = (zone[0] + zone[1]) / 2.0
        elif "high" in sd and "low" in sd:
            midpoint = (sd["high"] + sd["low"]) / 2.0
        else:
            continue

        direction = "buy" if sd_type == "demand" else "sell"
        factors.append(ConfluenceFactor(
            name=f"{sd_type.title()} Zone",
            category="smc",
            price=midpoint,
            weight=_SMC_WEIGHTS["supply_demand"],
            direction=direction,
        ))

    # --- SMC: Liquidity Sweeps ---
    for sweep in smc_data.get("liquidity_sweeps", []):
        sweep_type = sweep.get("type", sweep.get("direction", ""))
        price = sweep.get("price", sweep.get("swept_level", 0.0))
        if price <= 0:
            continue

        # Sweep above = sell-side liquidity grabbed = buy signal
        # Sweep below = buy-side liquidity grabbed = sell signal
        if sweep_type in ("buy_side", "above"):
            direction = "buy"
        elif sweep_type in ("sell_side", "below"):
            direction = "sell"
        else:
            direction = "both"

        factors.append(ConfluenceFactor(
            name=f"Liquidity Sweep {sweep_type}",
            category="smc",
            price=price,
            weight=_SMC_WEIGHTS["liquidity_sweep"],
            direction=direction,
        ))

    # --- SMC: Structure Events (BOS/ChoCH) ---
    structure_key = "bos_choch" if "bos_choch" in smc_data else "structure_events"
    for evt in smc_data.get(structure_key, []):
        evt_type = evt.get("type", evt.get("event_type", ""))
        evt_dir = evt.get("direction", "")
        price = evt.get("price", evt.get("break_price", 0.0))
        if price <= 0:
            continue

        direction = "buy" if evt_dir == "bullish" else "sell"
        factors.append(ConfluenceFactor(
            name=f"{evt_type} {evt_dir}",
            category="smc",
            price=price,
            weight=_SMC_WEIGHTS["structure"],
            direction=direction,
        ))

    # --- Session: Previous Day ---
    pd = session_levels.get("previous_day", {})
    if pd.get("high", 0.0) > 0:
        factors.append(ConfluenceFactor(
            name="PDH",
            category="session",
            price=pd["high"],
            weight=_SESSION_WEIGHTS["pdh"],
            direction="sell",
        ))
    if pd.get("low", 0.0) > 0:
        factors.append(ConfluenceFactor(
            name="PDL",
            category="session",
            price=pd["low"],
            weight=_SESSION_WEIGHTS["pdl"],
            direction="buy",
        ))
    if pd.get("open", 0.0) > 0:
        factors.append(ConfluenceFactor(
            name="PDO",
            category="session",
            price=pd["open"],
            weight=_SESSION_WEIGHTS["pdo"],
            direction="both",
        ))
    if pd.get("close", 0.0) > 0:
        factors.append(ConfluenceFactor(
            name="PDC",
            category="session",
            price=pd["close"],
            weight=_SESSION_WEIGHTS["pdc"],
            direction="both",
        ))

    # --- Session: Asian Range ---
    asian = session_levels.get("asian_session", {})
    if asian.get("high", 0.0) > 0:
        factors.append(ConfluenceFactor(
            name="Asian High",
            category="session",
            price=asian["high"],
            weight=_SESSION_WEIGHTS["asian_high"],
            direction="sell",
        ))
    if asian.get("low", 0.0) > 0:
        factors.append(ConfluenceFactor(
            name="Asian Low",
            category="session",
            price=asian["low"],
            weight=_SESSION_WEIGHTS["asian_low"],
            direction="buy",
        ))

    # --- Session: Round Numbers ---
    for rn in session_levels.get("round_numbers", []):
        if rn > 0:
            factors.append(ConfluenceFactor(
                name=f"Round {rn:.0f}",
                category="round",
                price=rn,
                weight=_ROUND_WEIGHT,
                direction="both",
            ))

    # --- Session: Fibonacci ---
    fib_data = session_levels.get("fibonacci", {})
    fib_levels = fib_data.get("levels", {})
    for level_str, price in fib_levels.items():
        if price <= 0:
            continue
        weight = _FIB_WEIGHTS.get(level_str, 10)
        factors.append(ConfluenceFactor(
            name=f"Fib {level_str}",
            category="fib",
            price=price,
            weight=weight,
            direction="both",
        ))

    # --- Volume Profile ---
    if volume_profile.get("poc", 0.0) > 0:
        factors.append(ConfluenceFactor(
            name="POC",
            category="volume",
            price=volume_profile["poc"],
            weight=_VOLUME_WEIGHTS["poc"],
            direction="both",
        ))
    if volume_profile.get("vah", 0.0) > 0:
        factors.append(ConfluenceFactor(
            name="VAH",
            category="volume",
            price=volume_profile["vah"],
            weight=_VOLUME_WEIGHTS["vah"],
            direction="sell",
        ))
    if volume_profile.get("val", 0.0) > 0:
        factors.append(ConfluenceFactor(
            name="VAL",
            category="volume",
            price=volume_profile["val"],
            weight=_VOLUME_WEIGHTS["val"],
            direction="buy",
        ))
    for hvn_price in volume_profile.get("hvn", []):
        if hvn_price > 0:
            factors.append(ConfluenceFactor(
                name=f"HVN {hvn_price:.2f}",
                category="volume",
                price=hvn_price,
                weight=_VOLUME_WEIGHTS["hvn"],
                direction="both",
            ))

    # --- HTF Bias Alignment ---
    if htf_bias in ("bullish", "bearish"):
        # Add a synthetic factor at current price to boost zones aligned
        # with the higher time-frame bias
        direction = "buy" if htf_bias == "bullish" else "sell"
        factors.append(ConfluenceFactor(
            name=f"HTF Bias {htf_bias}",
            category="smc",
            price=current_price,
            weight=_HTF_BIAS_WEIGHT,
            direction=direction,
        ))

    log.debug(
        "factors_collected",
        count=len(factors),
        categories={f.category for f in factors},
    )

    return factors


# ---------------------------------------------------------------------------
# Zone scoring
# ---------------------------------------------------------------------------


def score_zones(
    factors: list[ConfluenceFactor],
    proximity_pct: float = _PROXIMITY_PCT,
) -> list[ScoredZone]:
    """Cluster factors by price proximity, score each cluster.

    Algorithm:
    1. Sort factors by price.
    2. Cluster: iterate factors; if next factor is within proximity_pct
       of the cluster midpoint, add to cluster.
    3. For each cluster:
       - zone_id = f"zone_{i+1}"
       - price_high = max price in cluster + small buffer
       - price_low = min price in cluster - small buffer
       - confluence_score = sum of weights, capped at 100
       - direction = majority vote of directional factors (buy/sell/both)
    4. Return sorted by confluence_score descending.
    """
    if not factors:
        return []

    sorted_factors = sorted(factors, key=lambda f: f.price)

    # --- Build clusters ---
    clusters: list[list[ConfluenceFactor]] = []
    current_cluster: list[ConfluenceFactor] = [sorted_factors[0]]

    for f in sorted_factors[1:]:
        # Compute cluster midpoint
        cluster_prices = [cf.price for cf in current_cluster]
        cluster_mid = sum(cluster_prices) / len(cluster_prices)

        if cluster_mid > 0 and abs(f.price - cluster_mid) / cluster_mid <= proximity_pct / 100.0:
            current_cluster.append(f)
        else:
            clusters.append(current_cluster)
            current_cluster = [f]

    # Don't forget the last cluster
    clusters.append(current_cluster)

    # --- Score each cluster ---
    zones: list[ScoredZone] = []

    for i, cluster in enumerate(clusters):
        prices = [f.price for f in cluster]
        min_price = min(prices)
        max_price = max(prices)

        # Small buffer: 0.01% of midpoint
        midpoint = (min_price + max_price) / 2.0
        buffer = midpoint * 0.0001 if midpoint > 0 else 0.01

        price_high = max_price + buffer
        price_low = min_price - buffer

        # Sum weights, capped at 100
        total_weight = sum(f.weight for f in cluster)
        score = min(100, total_weight)

        # Direction: majority vote among directional factors
        buy_count = sum(1 for f in cluster if f.direction == "buy")
        sell_count = sum(1 for f in cluster if f.direction == "sell")

        if buy_count > sell_count:
            direction = "buy"
        elif sell_count > buy_count:
            direction = "sell"
        else:
            direction = "both"

        # Build reasoning
        factor_names = [f.name for f in cluster]
        reasoning = f"Confluence of {len(cluster)} factors: {', '.join(factor_names)}"

        zones.append(ScoredZone(
            zone_id=f"zone_{i + 1}",
            direction=direction,
            price_high=round(price_high, 2),
            price_low=round(price_low, 2),
            confluence_score=score,
            factors=list(cluster),
            reasoning=reasoning,
        ))

    # Sort by score descending
    zones.sort(key=lambda z: z.confluence_score, reverse=True)

    log.debug(
        "zones_scored",
        total_zones=len(zones),
        top_score=zones[0].confluence_score if zones else 0,
    )

    return zones


# ---------------------------------------------------------------------------
# Trade setup calculation
# ---------------------------------------------------------------------------


def calculate_trade_setup(
    zone: ScoredZone,
    current_price: float,
    atr: float,
    balance: float = 0.0,
    risk_pct: float = 1.0,
) -> ScoredZone:
    """Populate SL/TP1/TP2/R:R on a zone.

    For buy zones:
      SL = zone.price_low - 0.5 * ATR
      Entry ~= zone midpoint
      TP1 = entry + (entry - SL) * 1.0  (1:1 R:R)
      TP2 = entry + (entry - SL) * 2.5  (1:2.5 R:R)

    For sell zones:
      SL = zone.price_high + 0.5 * ATR
      Entry ~= zone midpoint
      TP1 = entry - (SL - entry) * 1.0
      TP2 = entry - (SL - entry) * 2.5

    rr_ratio = distance to TP2 / risk distance.
    Returns the modified zone (same object).
    """
    entry = (zone.price_high + zone.price_low) / 2.0

    if zone.direction == "sell":
        sl = zone.price_high + 0.5 * atr
        risk = sl - entry
        if risk <= 0:
            risk = 0.5 * atr  # fallback
        tp1 = entry - risk * 1.0
        tp2 = entry - risk * 2.5
        rr = (entry - tp2) / risk if risk > 0 else 0.0
    else:
        # buy (or both — default to buy setup)
        sl = zone.price_low - 0.5 * atr
        risk = entry - sl
        if risk <= 0:
            risk = 0.5 * atr  # fallback
        tp1 = entry + risk * 1.0
        tp2 = entry + risk * 2.5
        rr = (tp2 - entry) / risk if risk > 0 else 0.0

    zone.sl_price = round(sl, 2)
    zone.tp1_price = round(tp1, 2)
    zone.tp2_price = round(tp2, 2)
    zone.rr_ratio = round(rr, 2)

    # Generate invalidation text
    zone.invalidation = generate_invalidation(zone, atr)

    log.debug(
        "trade_setup_calculated",
        zone_id=zone.zone_id,
        direction=zone.direction,
        entry=round(entry, 2),
        sl=zone.sl_price,
        tp1=zone.tp1_price,
        tp2=zone.tp2_price,
        rr=zone.rr_ratio,
    )

    return zone


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def generate_invalidation(zone: ScoredZone, atr: float) -> str:
    """Generate invalidation text.

    For sell zones: 'Zone invalid if price closes above {zone.price_high + atr}'
    For buy zones:  'Zone invalid if price closes below {zone.price_low - atr}'
    """
    if zone.direction == "sell":
        level = zone.price_high + atr
        return f"Zone invalid if price closes above {level:.2f}"
    else:
        level = zone.price_low - atr
        return f"Zone invalid if price closes below {level:.2f}"


# ---------------------------------------------------------------------------
# Ranking and filtering
# ---------------------------------------------------------------------------


def rank_and_filter(
    zones: list[ScoredZone],
    min_score: int = 40,
    max_zones: int = 5,
) -> list[ScoredZone]:
    """Filter below min_score, return top max_zones sorted by score desc."""
    filtered = [z for z in zones if z.confluence_score >= min_score]
    filtered.sort(key=lambda z: z.confluence_score, reverse=True)
    return filtered[:max_zones]
