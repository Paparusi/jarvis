"""PendingOrderManager — Manage pending limit/stop order lifecycle.

Places limit orders at confluence zones after LLM pre-approval,
tracks fills, handles cancellation on invalidation.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.trading.persistence import TradingPersistence
from src.trading.risk import calculate_lot_from_risk
from src.utils.logging import get_logger

log = get_logger("trading.pending_orders")

NotifyCallback = Callable[[str], Awaitable[None]]


class PendingOrderManager:
    """Manage pending orders lifecycle: place, track, modify, cancel."""

    def __init__(
        self,
        mt5_client: Any,
        risk_guard: Any,
        persistence: TradingPersistence | None = None,
    ) -> None:
        self._mt5 = mt5_client
        self._risk_guard = risk_guard
        self._persistence = persistence
        self._orders: dict[int, dict] = {}  # ticket -> order info
        self._zone_tickets: dict[str, int] = {}  # zone_id -> ticket
        self._notify_cb: NotifyCallback | None = None
        self._symbol = os.environ.get("TRADING_SYMBOL", "XAUUSD")

    def on_notify(self, callback: NotifyCallback) -> None:
        self._notify_cb = callback

    async def place_zone_orders(
        self,
        zones: list[dict],
        equity: float,
        risk_pct: float = 1.0,
    ) -> list[int]:
        """For each zone: calculate lot -> determine order type -> place pending.

        Order type logic:
        - BUY zone with price below current -> buy_limit
        - BUY zone with price above current -> buy_stop
        - SELL zone with price above current -> sell_limit
        - SELL zone with price below current -> sell_stop

        Returns list of placed tickets.
        """
        placed: list[int] = []

        try:
            tick = await self._mt5.get_tick(self._symbol)
            current_price = tick.get("bid", 0.0)
        except Exception as e:
            log.error("tick_fetch_failed", error=str(e))
            return placed

        for zone in zones:
            zone_id = zone.get("zone_id", "")
            direction = zone.get("direction", "buy")
            zone_mid = (zone.get("price_high", 0) + zone.get("price_low", 0)) / 2
            sl_price = zone.get("sl_price", 0.0)

            if zone_mid == 0 or sl_price == 0:
                log.warning("skip_zone_no_price", zone_id=zone_id)
                continue

            # Calculate lot size
            max_lot = 0.10
            if self._risk_guard and hasattr(self._risk_guard, 'config') and hasattr(self._risk_guard.config, 'max_lot_size'):
                max_lot = self._risk_guard.config.max_lot_size
            lot = calculate_lot_from_risk(
                equity, risk_pct, zone_mid, sl_price, max_lot=max_lot,
            )

            # Determine order type
            if direction == "buy":
                order_type = "buy_limit" if zone_mid < current_price else "buy_stop"
            else:
                order_type = "sell_limit" if zone_mid > current_price else "sell_stop"

            tp1 = zone.get("tp1_price", 0.0)

            try:
                result = await self._mt5.place_pending(
                    symbol=self._symbol,
                    order_type=order_type,
                    volume=lot,
                    price=round(zone_mid, 2),
                    sl=round(sl_price, 2),
                    tp=round(tp1, 2) if tp1 else None,
                    comment=f"J-{zone_id[:8]}",
                )
                ticket = result.get("order", 0)
                if ticket:
                    order_info = {
                        "ticket": ticket,
                        "zone_id": zone_id,
                        "order_type": order_type,
                        "volume": lot,
                        "price": zone_mid,
                        "sl": sl_price,
                        "tp1": tp1,
                        "tp2": zone.get("tp2_price", 0.0),
                        "direction": direction,
                        "confluence_score": zone.get("confluence_score", 0),
                    }
                    self._orders[ticket] = order_info
                    self._zone_tickets[zone_id] = ticket

                    if self._persistence:
                        self._persistence.save_pending(
                            ticket, zone_id, self._symbol, order_type,
                            lot, zone_mid, sl_price, tp1,
                        )

                    placed.append(ticket)
                    log.info("pending_placed", ticket=ticket, zone_id=zone_id,
                             order_type=order_type, lot=lot, price=zone_mid)

            except Exception as e:
                log.error("pending_place_failed", zone_id=zone_id, error=str(e))

        return placed

    async def cancel_zone(self, zone_id: str) -> bool:
        """Cancel pending order for a zone. Returns True if cancelled."""
        ticket = self._zone_tickets.get(zone_id)
        if not ticket:
            return False
        return await self.cancel_order(ticket)

    async def cancel_order(self, ticket: int) -> bool:
        """Cancel a specific pending order."""
        try:
            await self._mt5.cancel_order(ticket)
            self._orders.pop(ticket, None)
            self._zone_tickets = {
                zid: t for zid, t in self._zone_tickets.items() if t != ticket
            }
            if self._persistence:
                self._persistence.update_pending_status(ticket, "cancelled")
            log.info("pending_cancelled", ticket=ticket)
            return True
        except Exception as e:
            log.error("cancel_failed", ticket=ticket, error=str(e))
            return False

    async def check_fills(self) -> list[dict]:
        """Check if any pending orders have been filled.

        Compare local pending list with MT5 pending orders.
        If a local order is NOT in MT5 pending -> it was filled (or expired).
        Returns list of filled order_info dicts.
        """
        if not self._orders:
            return []

        try:
            mt5_pending = await self._mt5.get_pending_orders(self._symbol)
        except Exception as e:
            log.warning("check_fills_error", error=str(e))
            return []

        mt5_tickets = {o.get("ticket") for o in mt5_pending}
        filled = []

        for ticket, order_info in list(self._orders.items()):
            if ticket not in mt5_tickets:
                filled.append(order_info)
                del self._orders[ticket]
                zone_id = order_info.get("zone_id", "")
                self._zone_tickets.pop(zone_id, None)
                if self._persistence:
                    self._persistence.update_pending_status(ticket, "filled")
                log.info("pending_filled", ticket=ticket, zone_id=zone_id)

        return filled

    async def cancel_all(self) -> int:
        """Cancel all pending orders. Returns count cancelled."""
        count = 0
        for ticket in list(self._orders.keys()):
            if await self.cancel_order(ticket):
                count += 1
        return count

    async def sync_from_mt5(self) -> None:
        """Sync local state with MT5 pending orders on startup."""
        try:
            mt5_orders = await self._mt5.get_pending_orders(self._symbol)
        except Exception:
            return
        for order in mt5_orders:
            ticket = order.get("ticket", 0)
            if ticket and ticket not in self._orders:
                comment = order.get("comment", "")
                if comment.startswith("J-"):
                    self._orders[ticket] = {
                        "ticket": ticket,
                        "zone_id": comment[2:],
                        "order_type": "",
                        "volume": order.get("volume_current", 0),
                        "price": order.get("price_open", 0),
                        "sl": order.get("sl", 0),
                        "direction": "buy" if order.get("type", 0) in (2, 4) else "sell",
                    }
                    log.info("pending_synced", ticket=ticket)

    @property
    def active_orders(self) -> dict[int, dict]:
        return dict(self._orders)

    @property
    def order_count(self) -> int:
        return len(self._orders)
