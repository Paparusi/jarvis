"""Position Manager — Trailing SL, partial TP, break-even, emergency exit.

Deterministic rules engine for managing open positions. NO LLM involvement.
Runs as a background async loop (10s tick), checking:
- Break-even move (every tick)
- TP1 partial close (every tick)
- TP2 full close (every tick)
- ATR-based trailing SL (every 60s)
- Emergency ChoCH/news exit (every 300s)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.utils.logging import get_logger

log = get_logger("trading.position_manager")

NotifyCallback = Callable[[str], Awaitable[None]]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ManagedPosition:
    ticket: int
    symbol: str
    direction: str          # "buy" / "sell"
    volume: float
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    original_sl: float
    be_moved: bool = False
    tp1_hit: bool = False
    remaining_volume: float = 0.0
    trail_sl: float = 0.0
    zone_id: str = ""
    confluence_score: int = 0
    strategy: str = ""
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BE_OFFSET = 0.01          # 1 pip for gold (XAUUSD)
_BE_THRESHOLD_PCT = 0.50   # Move to BE when price >= 50% toward TP1
_TP1_CLOSE_PCT = 0.50      # Close 50% at TP1
_TRAIL_ATR_MULT = 1.5      # Trail SL at 1.5x ATR from price
_TRAIL_ATR_PERIOD = 14     # ATR period for trailing
_TRAIL_CANDLE_COUNT = 20   # M15 candles to fetch for ATR
_EMERGENCY_CANDLE_COUNT = 20  # H1 candles for structure check
_LOOP_INTERVAL = 10        # seconds between ticks
_TRAIL_TICK_INTERVAL = 6   # every 6th tick (~60s)
_EMERGENCY_TICK_INTERVAL = 30  # every 30th tick (~300s)


# ---------------------------------------------------------------------------
# PositionManager
# ---------------------------------------------------------------------------


class PositionManager:
    """Trailing SL, partial TP, break-even, emergency exit. NO LLM -- deterministic rules."""

    def __init__(self, mt5_client: Any, risk_guard: Any = None, journal: Any = None) -> None:
        self._mt5 = mt5_client
        self._risk_guard = risk_guard
        self._journal = journal
        self._positions: list[ManagedPosition] = []
        self._notify_cb: NotifyCallback | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._tick_count = 0  # Track loop iterations for interval-based checks

    # -- Public API ----------------------------------------------------------

    def on_notify(self, callback: NotifyCallback) -> None:
        """Register notification callback."""
        self._notify_cb = callback

    async def add_position(self, pos: ManagedPosition) -> None:
        """Add position to manage. Set remaining_volume = volume if not set."""
        if pos.remaining_volume <= 0:
            pos.remaining_volume = pos.volume
        self._positions.append(pos)
        log.info("position_added", ticket=pos.ticket, direction=pos.direction)

    async def start(self) -> None:
        """Start position management loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def emergency_close_all(self, reason: str = "kill_switch") -> int:
        """Close all managed positions. Return count closed."""
        count = 0
        for pos in list(self._positions):
            try:
                await self._close_position(pos, reason)
                count += 1
            except Exception as exc:
                log.error("emergency_close_failed", ticket=pos.ticket, error=str(exc))
        return count

    @property
    def active_positions(self) -> list[ManagedPosition]:
        return list(self._positions)

    @property
    def position_count(self) -> int:
        return len(self._positions)

    # -- Main loop -----------------------------------------------------------

    async def _loop(self) -> None:
        """Main loop: every 10s check BE/TP1/TP2. Every 6th tick (60s) trailing. Every 30th (300s) emergency."""
        while self._running:
            try:
                for pos in list(self._positions):
                    tick = await self._mt5.get_tick(pos.symbol)
                    price = tick.get("bid", 0.0) if pos.direction == "sell" else tick.get("ask", 0.0)

                    # Every tick (10s): check BE, TP1, TP2
                    await self._check_breakeven(pos, price)
                    await self._check_tp1(pos, price)
                    await self._check_tp2(pos, price)

                    # Every 6th tick (~60s): trailing
                    if self._tick_count % _TRAIL_TICK_INTERVAL == 0:
                        await self._check_trailing(pos)

                    # Every 30th tick (~300s): emergency
                    if self._tick_count % _EMERGENCY_TICK_INTERVAL == 0:
                        await self._check_emergency(pos)

                self._tick_count += 1
                await asyncio.sleep(_LOOP_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("position_manager_error", error=str(exc))
                await asyncio.sleep(_LOOP_INTERVAL)

    # -- Break-even ----------------------------------------------------------

    async def _check_breakeven(self, pos: ManagedPosition, price: float) -> None:
        """Move SL to entry + 1 pip when price >= 50% toward TP1.

        For buy: price >= entry + 0.5 * (tp1 - entry)
        For sell: price <= entry - 0.5 * (entry - tp1)
        Only if be_moved is False.
        """
        if pos.be_moved:
            return

        if pos.direction == "buy":
            distance_to_tp1 = pos.tp1 - pos.entry_price
            threshold = pos.entry_price + _BE_THRESHOLD_PCT * distance_to_tp1
            if price >= threshold:
                new_sl = pos.entry_price + _BE_OFFSET
                await self._mt5.modify_position(pos.ticket, sl=new_sl)
                pos.sl = new_sl
                pos.be_moved = True
                log.info("breakeven_moved", ticket=pos.ticket, new_sl=new_sl)
                if self._notify_cb:
                    await self._notify_cb(
                        f"Position {pos.ticket} BE moved: SL → {new_sl}"
                    )
        else:  # sell
            distance_to_tp1 = pos.entry_price - pos.tp1
            threshold = pos.entry_price - _BE_THRESHOLD_PCT * distance_to_tp1
            if price <= threshold:
                new_sl = pos.entry_price - _BE_OFFSET
                await self._mt5.modify_position(pos.ticket, sl=new_sl)
                pos.sl = new_sl
                pos.be_moved = True
                log.info("breakeven_moved", ticket=pos.ticket, new_sl=new_sl)
                if self._notify_cb:
                    await self._notify_cb(
                        f"Position {pos.ticket} BE moved: SL → {new_sl}"
                    )

    # -- TP1 partial close ---------------------------------------------------

    async def _check_tp1(self, pos: ManagedPosition, price: float) -> None:
        """Partial close 50% at TP1.

        For buy: price >= tp1
        For sell: price <= tp1
        Close 50% volume, update remaining_volume, move SL to entry, set tp1_hit.
        """
        if pos.tp1_hit:
            return

        triggered = False
        if pos.direction == "buy" and price >= pos.tp1:
            triggered = True
        elif pos.direction == "sell" and price <= pos.tp1:
            triggered = True

        if not triggered:
            return

        close_volume = round(pos.volume * _TP1_CLOSE_PCT, 2)
        await self._mt5.close_position(pos.ticket, volume=close_volume)
        pos.remaining_volume = round(pos.volume - close_volume, 2)

        # Move SL to entry price
        await self._mt5.modify_position(pos.ticket, sl=pos.entry_price)
        pos.sl = pos.entry_price
        pos.tp1_hit = True

        log.info(
            "tp1_hit",
            ticket=pos.ticket,
            closed_volume=close_volume,
            remaining=pos.remaining_volume,
        )
        if self._notify_cb:
            await self._notify_cb(
                f"Position {pos.ticket} TP1 hit: closed {close_volume} lots, "
                f"remaining {pos.remaining_volume}"
            )

    # -- TP2 full close ------------------------------------------------------

    async def _check_tp2(self, pos: ManagedPosition, price: float) -> None:
        """Full close at TP2.

        For buy: price >= tp2
        For sell: price <= tp2
        Close remaining volume. Remove from positions. Log to journal.
        """
        triggered = False
        if pos.direction == "buy" and price >= pos.tp2:
            triggered = True
        elif pos.direction == "sell" and price <= pos.tp2:
            triggered = True

        if not triggered:
            return

        await self._close_position(pos, "tp2_hit")

    # -- Trailing SL ---------------------------------------------------------

    async def _check_trailing(self, pos: ManagedPosition) -> None:
        """Trail SL using ATR-based trailing (1.5x ATR from current price).

        Fetch M15 candles (20 bars), calculate ATR.
        For buy: new_sl = current_price - 1.5 * atr; only move if new_sl > current sl
        For sell: new_sl = current_price + 1.5 * atr; only move if new_sl < current sl
        Only applies after tp1_hit (position is in profit).
        """
        if not pos.tp1_hit:
            return

        try:
            candles = await self._mt5.get_rates(pos.symbol, "M15", _TRAIL_CANDLE_COUNT)
            if not candles or len(candles) < _TRAIL_ATR_PERIOD + 1:
                return

            # Calculate ATR inline to avoid importing full analysis module
            atr = self._calc_atr(candles, _TRAIL_ATR_PERIOD)
            if atr is None or atr <= 0:
                return

            # Get current price
            tick = await self._mt5.get_tick(pos.symbol)
            current_price = (
                tick.get("bid", 0.0) if pos.direction == "sell"
                else tick.get("ask", 0.0)
            )

            if pos.direction == "buy":
                new_sl = round(current_price - _TRAIL_ATR_MULT * atr, 2)
                if new_sl > pos.sl:
                    await self._mt5.modify_position(pos.ticket, sl=new_sl)
                    pos.sl = new_sl
                    pos.trail_sl = new_sl
                    log.info("trailing_sl_moved", ticket=pos.ticket, new_sl=new_sl)
            else:  # sell
                new_sl = round(current_price + _TRAIL_ATR_MULT * atr, 2)
                if new_sl < pos.sl:
                    await self._mt5.modify_position(pos.ticket, sl=new_sl)
                    pos.sl = new_sl
                    pos.trail_sl = new_sl
                    log.info("trailing_sl_moved", ticket=pos.ticket, new_sl=new_sl)

        except Exception as exc:
            log.warning("trailing_check_failed", ticket=pos.ticket, error=str(exc))

    # -- Emergency check -----------------------------------------------------

    async def _check_emergency(self, pos: ManagedPosition) -> None:
        """Check H1 structure for ChoCH reversal.

        Fetch H1 candles (20 bars), run analyze_smc.
        For buy: if bearish ChoCH detected -> close position
        For sell: if bullish ChoCH detected -> close position
        Also check risk_guard.should_flatten_for_news if risk_guard available.
        """
        try:
            # News flattening check
            if self._risk_guard:
                if self._risk_guard.should_flatten_for_news([]):
                    await self._close_position(pos, "news_flatten")
                    return

            candles = await self._mt5.get_rates(pos.symbol, "H1", _EMERGENCY_CANDLE_COUNT)
            if not candles or len(candles) < _EMERGENCY_CANDLE_COUNT:
                return

            from src.trading.smc import analyze_smc
            smc = analyze_smc(candles)

            # Check for ChoCH reversal against our position
            for event in smc.structure_events:
                if event.event_type != "ChoCH":
                    continue
                if pos.direction == "buy" and event.direction == "bearish":
                    await self._close_position(pos, "choch_reversal_bearish")
                    return
                if pos.direction == "sell" and event.direction == "bullish":
                    await self._close_position(pos, "choch_reversal_bullish")
                    return

        except Exception as exc:
            log.warning("emergency_check_failed", ticket=pos.ticket, error=str(exc))

    # -- Close position helper -----------------------------------------------

    async def _close_position(self, pos: ManagedPosition, reason: str) -> None:
        """Close position via MT5, log to journal, notify, remove from list.

        1. mt5.close_position(ticket, remaining_volume)
        2. Calculate approximate P/L
        3. If journal: log_trade({...})
        4. If risk_guard: record_trade_result(pnl, equity)
        5. If notify_cb: send notification
        6. Remove from self._positions
        """
        result = await self._mt5.close_position(pos.ticket, pos.remaining_volume)

        # Get close price for P/L calculation
        tick = await self._mt5.get_tick(pos.symbol)
        close_price = (
            tick.get("bid", 0.0) if pos.direction == "buy"
            else tick.get("ask", 0.0)
        )

        # Approximate P/L: (close - entry) * volume * 100 for gold (1 lot = 100 oz)
        if pos.direction == "buy":
            pnl = (close_price - pos.entry_price) * pos.remaining_volume * 100
        else:
            pnl = (pos.entry_price - close_price) * pos.remaining_volume * 100

        log.info(
            "position_closed",
            ticket=pos.ticket,
            reason=reason,
            pnl=round(pnl, 2),
        )

        # Journal logging
        if self._journal:
            try:
                trade_data = {
                    "ticket": pos.ticket,
                    "symbol": pos.symbol,
                    "side": pos.direction,
                    "volume": pos.volume,
                    "entry_price": pos.entry_price,
                    "exit_price": close_price,
                    "sl": pos.original_sl,
                    "tp": pos.tp2,
                    "profit": round(pnl, 2),
                    "strategy_name": pos.strategy,
                    "notes": f"Closed: {reason}",
                    "opened_at": pos.opened_at.isoformat(),
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                }
                await self._journal.log_trade(trade_data)
            except Exception as exc:
                log.warning("journal_log_failed", ticket=pos.ticket, error=str(exc))

        # Risk guard update
        if self._risk_guard:
            try:
                equity = tick.get("equity", 10000.0)
                self._risk_guard.record_trade_result(round(pnl, 2), equity)
            except Exception as exc:
                log.warning("risk_guard_update_failed", ticket=pos.ticket, error=str(exc))

        # Notification
        if self._notify_cb:
            await self._notify_cb(
                f"Position {pos.ticket} closed: {reason} (P/L: {round(pnl, 2)})"
            )

        # Remove from managed list
        if pos in self._positions:
            self._positions.remove(pos)

    # -- ATR helper ----------------------------------------------------------

    @staticmethod
    def _calc_atr(candles: list[dict[str, Any]], period: int = 14) -> float | None:
        """Calculate ATR from candle data. Returns latest ATR value or None."""
        if len(candles) < period + 1:
            return None

        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        closes = [c["close"] for c in candles]

        # True Range
        tr: list[float] = [highs[0] - lows[0]]
        for i in range(1, len(candles)):
            tr.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )

        if len(tr) < period:
            return None

        # Wilder's smoothed ATR
        atr = sum(tr[:period]) / period
        for i in range(period, len(tr)):
            atr = (atr * (period - 1) + tr[i]) / period

        return atr
