"""Volume Profile analysis from OHLCV candle data.

Builds a Volume Profile by distributing each candle's tick_volume across
price bins spanning the session's high-low range.  Identifies Point of
Control (POC), Value Area High/Low (VAH/VAL), High Volume Nodes (HVN),
and Low Volume Nodes (LVN).

Uses tick_volume since MT5 does not provide real volume for forex/CFDs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.utils.logging import get_logger

log = get_logger("trading.volume_profile")

_DEFAULT_NUM_BINS = 50
_VALUE_AREA_PCT = 0.70  # 70% of total volume
_MIN_CANDLES = 20


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class VolumeProfile:
    poc: float  # Point of Control — price with highest volume
    vah: float  # Value Area High
    val: float  # Value Area Low
    hvn: list[float]  # High Volume Nodes (above average)
    lvn: list[float]  # Low Volume Nodes (below average * 0.5)
    profile: list[tuple[float, float]]  # [(price_level, volume), ...] sorted by price
    total_volume: float
    price_range: tuple[float, float]  # (low, high)


def _empty_profile() -> VolumeProfile:
    """Return an empty VolumeProfile for insufficient data cases."""
    return VolumeProfile(
        poc=0.0,
        vah=0.0,
        val=0.0,
        hvn=[],
        lvn=[],
        profile=[],
        total_volume=0.0,
        price_range=(0.0, 0.0),
    )


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------


def build_volume_profile(
    candles: list[dict[str, Any]],
    num_bins: int = _DEFAULT_NUM_BINS,
) -> VolumeProfile:
    """Build a Volume Profile from OHLCV candle data.

    Distributes each candle's tick_volume evenly across the price bins
    that its H-L range overlaps with.  Then derives POC, Value Area,
    HVN, and LVN from the resulting histogram.

    Parameters
    ----------
    candles:
        List of candle dicts with keys: time, open, high, low, close,
        tick_volume.
    num_bins:
        Number of horizontal price bins to divide the overall range into.
        Must be >= 1.

    Returns
    -------
    A populated :class:`VolumeProfile`.  Returns an empty profile when
    fewer than ``_MIN_CANDLES`` candles are provided.
    """
    if len(candles) < _MIN_CANDLES:
        log.warning(
            "insufficient_candles_for_volume_profile",
            count=len(candles),
            minimum=_MIN_CANDLES,
        )
        return _empty_profile()

    # Clamp num_bins to at least 1
    num_bins = max(1, num_bins)

    # --- Determine overall price range ---
    overall_low = min(c["low"] for c in candles)
    overall_high = max(c["high"] for c in candles)

    if overall_high <= overall_low:
        # All candles at the same price — degenerate case
        total_vol = sum(c.get("tick_volume", 0) for c in candles)
        mid = (overall_high + overall_low) / 2.0
        return VolumeProfile(
            poc=mid,
            vah=mid,
            val=mid,
            hvn=[mid],
            lvn=[],
            profile=[(mid, float(total_vol))],
            total_volume=float(total_vol),
            price_range=(overall_low, overall_high),
        )

    price_span = overall_high - overall_low
    bin_size = price_span / num_bins

    # --- Initialise bins ---
    # Each bin is identified by its index 0..num_bins-1.
    # Bin i covers [overall_low + i*bin_size, overall_low + (i+1)*bin_size).
    # The last bin is inclusive of overall_high.
    bins: list[float] = [0.0] * num_bins

    # --- Distribute volume ---
    for c in candles:
        c_low = c["low"]
        c_high = c["high"]
        vol = float(c.get("tick_volume", 0))

        if vol <= 0 or c_high <= c_low:
            continue

        # Find which bins this candle's H-L range overlaps
        first_bin = int((c_low - overall_low) / bin_size)
        last_bin = int((c_high - overall_low) / bin_size)

        # Clamp to valid range
        first_bin = max(0, min(first_bin, num_bins - 1))
        last_bin = max(0, min(last_bin, num_bins - 1))

        num_covered = last_bin - first_bin + 1
        vol_per_bin = vol / num_covered

        for b in range(first_bin, last_bin + 1):
            bins[b] += vol_per_bin

    total_volume = sum(bins)

    # --- Build profile list (sorted by price) ---
    profile: list[tuple[float, float]] = []
    for i in range(num_bins):
        midpoint = overall_low + (i + 0.5) * bin_size
        profile.append((round(midpoint, 8), round(bins[i], 8)))

    # --- POC: bin with max volume ---
    poc_idx = 0
    max_vol = bins[0]
    for i in range(1, num_bins):
        if bins[i] > max_vol:
            max_vol = bins[i]
            poc_idx = i

    poc_price = overall_low + (poc_idx + 0.5) * bin_size

    # --- Value Area: expand outward from POC until 70% covered ---
    va_volume = bins[poc_idx]
    va_low_idx = poc_idx
    va_high_idx = poc_idx

    target_volume = total_volume * _VALUE_AREA_PCT

    while va_volume < target_volume:
        can_go_down = va_low_idx > 0
        can_go_up = va_high_idx < num_bins - 1

        if not can_go_down and not can_go_up:
            break

        vol_below = bins[va_low_idx - 1] if can_go_down else -1.0
        vol_above = bins[va_high_idx + 1] if can_go_up else -1.0

        if vol_below >= vol_above:
            va_low_idx -= 1
            va_volume += bins[va_low_idx]
        else:
            va_high_idx += 1
            va_volume += bins[va_high_idx]

    # VAL = lower edge of lowest VA bin, VAH = upper edge of highest VA bin
    val_price = overall_low + va_low_idx * bin_size
    vah_price = overall_low + (va_high_idx + 1) * bin_size

    # --- HVN / LVN ---
    avg_volume = total_volume / num_bins if num_bins > 0 else 0.0

    hvn: list[float] = []
    lvn: list[float] = []

    for i in range(num_bins):
        midpoint = overall_low + (i + 0.5) * bin_size
        if bins[i] > avg_volume:
            hvn.append(round(midpoint, 8))
        elif bins[i] < avg_volume * 0.5:
            lvn.append(round(midpoint, 8))

    log.debug(
        "volume_profile_built",
        num_candles=len(candles),
        num_bins=num_bins,
        poc=round(poc_price, 4),
        vah=round(vah_price, 4),
        val=round(val_price, 4),
        hvn_count=len(hvn),
        lvn_count=len(lvn),
    )

    return VolumeProfile(
        poc=round(poc_price, 8),
        vah=round(vah_price, 8),
        val=round(val_price, 8),
        hvn=hvn,
        lvn=lvn,
        profile=profile,
        total_volume=round(total_volume, 8),
        price_range=(overall_low, overall_high),
    )


# ---------------------------------------------------------------------------
# Nearest-node helpers
# ---------------------------------------------------------------------------


def find_nearest_hvn(price: float, profile: VolumeProfile) -> float | None:
    """Find the HVN price level closest to *price*.

    Returns ``None`` if there are no HVN entries.
    """
    if not profile.hvn:
        return None

    best: float | None = None
    best_dist = float("inf")

    for level in profile.hvn:
        dist = abs(level - price)
        if dist < best_dist:
            best_dist = dist
            best = level

    return best


def find_nearest_lvn(price: float, profile: VolumeProfile) -> float | None:
    """Find the LVN price level closest to *price*.

    Returns ``None`` if there are no LVN entries.
    """
    if not profile.lvn:
        return None

    best: float | None = None
    best_dist = float("inf")

    for level in profile.lvn:
        dist = abs(level - price)
        if dist < best_dist:
            best_dist = dist
            best = level

    return best


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def profile_to_dict(profile: VolumeProfile) -> dict[str, Any]:
    """Convert a VolumeProfile to a JSON-serializable dictionary."""
    return {
        "poc": profile.poc,
        "vah": profile.vah,
        "val": profile.val,
        "hvn": list(profile.hvn),
        "lvn": list(profile.lvn),
        "profile": [{"price": p, "volume": v} for p, v in profile.profile],
        "total_volume": profile.total_volume,
        "price_range": {"low": profile.price_range[0], "high": profile.price_range[1]},
    }
