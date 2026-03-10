"""Risk Management — Position sizing, R:R calculator, drawdown tracking.

Pure-Python risk tools for XAUUSD (gold) trading on MT5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.utils.logging import get_logger

log = get_logger("trading.risk")

# XAUUSD: 1 standard lot = 100 oz gold
# 1 pip = $0.01 price movement
# Pip value: 0.01 lot = $0.01/pip, 0.1 lot = $0.10/pip, 1.0 lot = $1.00/pip
_XAUUSD_PIP_VALUE_PER_LOT = 1.0
_XAUUSD_PIP_SIZE = 0.01


@dataclass
class PositionSize:
    lot_size: float
    risk_amount: float
    sl_pips: float
    pip_value: float
    max_loss: float


@dataclass
class RiskReward:
    entry: float
    sl: float
    tp: float
    sl_distance: float
    tp_distance: float
    sl_pips: float
    tp_pips: float
    rr_ratio: float
    side: str


@dataclass
class DrawdownTracker:
    peak_equity: float = 0.0
    current_equity: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    _history: list[float] = field(default_factory=list)

    def update(self, equity: float) -> dict[str, float]:
        """Update with new equity value. Returns current drawdown info."""
        self._history.append(equity)
        self.current_equity = equity

        if equity > self.peak_equity:
            self.peak_equity = equity

        drawdown = self.peak_equity - equity
        drawdown_pct = (drawdown / self.peak_equity * 100) if self.peak_equity > 0 else 0.0

        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
        if drawdown_pct > self.max_drawdown_pct:
            self.max_drawdown_pct = drawdown_pct

        return {
            "current_drawdown": drawdown,
            "current_drawdown_pct": round(drawdown_pct, 2),
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "peak_equity": self.peak_equity,
        }

    def get_stats(self) -> dict[str, Any]:
        return {
            "peak_equity": self.peak_equity,
            "current_equity": self.current_equity,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "samples": len(self._history),
        }


def calculate_pip_value(lot_size: float, symbol: str = "XAUUSD") -> float:
    """Calculate dollar value per pip for given lot size."""
    if symbol.upper() == "XAUUSD":
        return lot_size * _XAUUSD_PIP_VALUE_PER_LOT
    return lot_size * 10.0


def calculate_position_size(
    balance: float,
    risk_pct: float = 1.0,
    sl_distance: float = 0.0,
    symbol: str = "XAUUSD",
) -> PositionSize:
    """Calculate position size given account balance, risk %, and SL distance in price."""
    if balance <= 0 or risk_pct <= 0 or sl_distance <= 0:
        return PositionSize(lot_size=0.0, risk_amount=0.0, sl_pips=0.0,
                            pip_value=0.0, max_loss=0.0)

    risk_amount = balance * (risk_pct / 100.0)
    sl_pips = sl_distance / _XAUUSD_PIP_SIZE

    pip_value_per_lot = _XAUUSD_PIP_VALUE_PER_LOT if symbol.upper() == "XAUUSD" else 10.0
    lot_size = risk_amount / (sl_pips * pip_value_per_lot)
    lot_size = max(0.01, round(lot_size, 2))

    actual_pip_value = calculate_pip_value(lot_size, symbol)

    return PositionSize(
        lot_size=lot_size,
        risk_amount=risk_amount,
        sl_pips=sl_pips,
        pip_value=actual_pip_value,
        max_loss=risk_amount,
    )


def calculate_lot_from_risk(
    equity: float,
    risk_pct: float,
    entry_price: float,
    sl_price: float,
    symbol: str = "XAUUSD",
    min_lot: float = 0.01,
    max_lot: float = 0.10,
) -> float:
    """Calculate lot size from risk percentage and SL distance.

    Formula: lot = risk_amount / (sl_pips * pip_value_per_lot)

    For XAUUSD: pip = $0.01, pip_value_per_lot = $1.00/pip/lot
    So: sl_pips = abs(entry - sl) / 0.01
        lot = (equity * risk_pct / 100) / (sl_pips * 1.0)

    Returns lot clamped to [min_lot, max_lot], rounded to 0.01.
    """
    if equity <= 0 or risk_pct <= 0 or entry_price == sl_price:
        return min_lot

    risk_amount = equity * (risk_pct / 100.0)
    sl_distance = abs(entry_price - sl_price)
    sl_pips = sl_distance / _XAUUSD_PIP_SIZE

    pip_value = _XAUUSD_PIP_VALUE_PER_LOT if symbol.upper().startswith("XAUUSD") else 10.0
    if sl_pips * pip_value == 0:
        return min_lot

    lot = risk_amount / (sl_pips * pip_value)
    lot = round(lot, 2)
    lot = max(min_lot, min(lot, max_lot))
    return lot


def calculate_risk_reward(entry: float, sl: float, tp: float) -> RiskReward:
    """Calculate Risk:Reward ratio. Infers side from SL position."""
    side = "buy" if sl < entry else "sell"
    sl_distance = abs(entry - sl)
    tp_distance = abs(tp - entry)
    sl_pips = sl_distance / _XAUUSD_PIP_SIZE
    tp_pips = tp_distance / _XAUUSD_PIP_SIZE
    rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0.0

    return RiskReward(
        entry=entry, sl=sl, tp=tp,
        sl_distance=sl_distance, tp_distance=tp_distance,
        sl_pips=sl_pips, tp_pips=tp_pips,
        rr_ratio=round(rr_ratio, 2), side=side,
    )


def check_max_position(
    free_margin: float,
    lot_size: float,
    max_margin_pct: float = 50.0,
    margin_per_lot: float = 1000.0,
) -> dict[str, Any]:
    """Check if proposed lot size exceeds margin limit."""
    required_margin = lot_size * margin_per_lot
    max_allowed_margin = free_margin * (max_margin_pct / 100.0)
    max_lots = max_allowed_margin / margin_per_lot if margin_per_lot > 0 else 0.0
    max_lots = round(max_lots, 2)

    return {
        "allowed": required_margin <= max_allowed_margin,
        "lot_size": lot_size,
        "required_margin": required_margin,
        "free_margin": free_margin,
        "max_margin_pct": max_margin_pct,
        "max_allowed_margin": max_allowed_margin,
        "max_allowed_lots": max_lots,
        "margin_usage_pct": round(required_margin / free_margin * 100, 2) if free_margin > 0 else 100.0,
    }
