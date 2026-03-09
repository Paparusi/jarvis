"""Trade approval gate — requires human confirmation before order placement."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.trading.persistence import TradingPersistence
from src.trading.mt5_client import MT5Client
from src.trading.risk_guard import RiskGuard
from src.utils.logging import get_logger

log = get_logger("trading.approval_manager")


class ApprovalManager:
    """Manages trade approval lifecycle.

    Flow: create_approval() → notify Telegram + Dashboard → approve/reject.
    """

    def __init__(
        self,
        persistence: TradingPersistence,
        mt5_client: MT5Client,
        risk_guard: RiskGuard,
    ) -> None:
        self._db = persistence
        self._mt5 = mt5_client
        self._risk_guard = risk_guard
        self._pending: dict[str, dict] = {}  # in-memory cache
        self._notify_callbacks: list[Callable] = []
        self._ws_callbacks: list[Callable] = []

    def on_notify(self, callback: Callable) -> None:
        """Register Telegram notification callback."""
        self._notify_callbacks.append(callback)

    def on_ws_event(self, callback: Callable) -> None:
        """Register WebSocket event callback."""
        self._ws_callbacks.append(callback)

    async def create_approval(
        self,
        entry_decision: Any,
        zone: dict,
        plan_id: int | None,
        symbol: str = "XAUUSD",
        current_price: float = 0,
    ) -> dict:
        """Create a pending approval from entry decision.

        Args:
            entry_decision: EntryDecision from EntryConfirmer
            zone: Zone dict with price_high, price_low, direction, score, etc.
            plan_id: Current trade plan ID
            symbol: Trading symbol
            current_price: Current market price

        Returns:
            Approval dict with id and all trade details.
        """
        approval_id = f"apr_{uuid.uuid4().hex[:12]}"
        zone_mid = (zone.get("price_high", 0) + zone.get("price_low", 0)) / 2

        approval = {
            "id": approval_id,
            "plan_id": plan_id,
            "zone_id": zone.get("id", ""),
            "symbol": symbol,
            "direction": entry_decision.direction,
            "order_type": self._determine_order_type(
                entry_decision.direction, zone_mid, current_price
            ),
            "price": round(zone_mid, 2),
            "sl": round(entry_decision.sl, 2),
            "tp1": round(entry_decision.tp1, 2),
            "tp2": round(entry_decision.tp2, 2) if entry_decision.tp2 else None,
            "lot": entry_decision.lot_size,
            "risk_pct": getattr(entry_decision, "risk_pct", 0.8),
            "risk_usd": getattr(entry_decision, "risk_usd", 0),
            "confluence_score": zone.get("score", 0),
            "analysis": entry_decision.reasoning,
            "smc_summary": getattr(entry_decision, "smc_summary", ""),
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Persist
        self._db.save_approval(approval)
        self._pending[approval_id] = approval

        # Notify Telegram
        for cb in self._notify_callbacks:
            try:
                await cb("approval", approval)
            except Exception as e:
                log.warning("notify_failed", error=str(e))

        # Push to WebSocket
        for cb in self._ws_callbacks:
            try:
                await cb({"type": "approval", "data": approval})
            except Exception as e:
                log.warning("ws_push_failed", error=str(e))

        log.info(
            "approval_created",
            id=approval_id,
            direction=approval["direction"],
            price=approval["price"],
            lot=approval["lot"],
        )
        return approval

    async def approve(self, approval_id: str, via: str = "telegram") -> dict:
        """Approve a pending trade.

        Re-validates with RiskGuard before placing order.

        Returns:
            dict with status and order details, or error.
        """
        approval = self._db.get_approval(approval_id)
        if not approval:
            return {"error": f"Approval {approval_id} not found"}
        if approval["status"] != "pending":
            return {"error": f"Approval already {approval['status']}"}

        # Re-validate with RiskGuard
        rr_ratio = 0
        sl_dist = abs(approval["price"] - approval["sl"])
        if sl_dist > 0 and approval.get("tp1"):
            tp_dist = abs(approval["tp1"] - approval["price"])
            rr_ratio = tp_dist / sl_dist

        veto = self._risk_guard.check_entry(
            risk_pct=approval.get("risk_pct", 1.0),
            lot_size=approval["lot"],
            rr_ratio=rr_ratio,
            direction=approval["direction"],
        )
        if not veto.approved:
            self._db.update_approval(
                approval_id,
                status="rejected",
                responded_at=datetime.now(timezone.utc).isoformat(),
                responded_via="risk_guard",
                reject_reason=veto.reason,
            )
            log.warning("approval_risk_vetoed", id=approval_id, reason=veto.reason)
            await self._broadcast_update(approval_id, "rejected", veto.reason)
            return {"error": f"RiskGuard veto: {veto.reason}"}

        # Price validation — check if price moved too far
        try:
            tick = await self._mt5.get_tick(approval["symbol"])
            current = tick.get("bid", 0) if approval["direction"] == "buy" else tick.get("ask", 0)
            zone_width = abs(approval.get("tp1", approval["price"]) - approval["sl"])
            price_distance = abs(current - approval["price"])
            if zone_width > 0 and price_distance > zone_width * 0.5:
                reason = f"Price moved too far: {current} vs entry {approval['price']}"
                self._db.update_approval(
                    approval_id, status="rejected",
                    responded_at=datetime.now(timezone.utc).isoformat(),
                    responded_via="price_check", reject_reason=reason,
                )
                log.warning("approval_price_moved", id=approval_id, current=current)
                await self._broadcast_update(approval_id, "rejected", reason)
                return {"error": reason}
        except Exception as e:
            log.warning("price_check_failed", error=str(e))

        # Place order
        try:
            side = "buy" if approval["direction"] == "buy" else "sell"
            result = await self._mt5.place_order(
                approval["symbol"], side, approval["lot"],
                sl=approval["sl"], tp=approval.get("tp1"),
            )
            ticket = result.get("ticket", 0)

            self._db.update_approval(
                approval_id,
                status="approved",
                responded_at=datetime.now(timezone.utc).isoformat(),
                responded_via=via,
                order_ticket=ticket,
            )
            self._pending.pop(approval_id, None)

            log.info("approval_approved", id=approval_id, ticket=ticket, via=via)
            await self._broadcast_update(approval_id, "approved")
            return {"status": "approved", "ticket": ticket, "order": result}

        except Exception as e:
            log.error("approval_order_failed", id=approval_id, error=str(e))
            return {"error": f"Order failed: {e}"}

    async def reject(
        self, approval_id: str, via: str = "telegram", reason: str = ""
    ) -> dict:
        """Reject a pending trade."""
        approval = self._db.get_approval(approval_id)
        if not approval:
            return {"error": f"Approval {approval_id} not found"}
        if approval["status"] != "pending":
            return {"error": f"Approval already {approval['status']}"}

        self._db.update_approval(
            approval_id,
            status="rejected",
            responded_at=datetime.now(timezone.utc).isoformat(),
            responded_via=via,
            reject_reason=reason or "Rejected by user",
        )
        self._pending.pop(approval_id, None)

        log.info("approval_rejected", id=approval_id, via=via, reason=reason)
        await self._broadcast_update(approval_id, "rejected", reason)
        return {"status": "rejected"}

    def get_pending(self) -> list[dict]:
        """Get all pending approvals."""
        return self._db.get_pending_approvals()

    async def cancel_all(self, reason: str = "Plan changed") -> int:
        """Cancel all pending approvals (on brain stop / new plan)."""
        pending = self._db.get_pending_approvals()
        for a in pending:
            self._db.update_approval(
                a["id"], status="cancelled",
                responded_at=datetime.now(timezone.utc).isoformat(),
                reject_reason=reason,
            )
            await self._broadcast_update(a["id"], "cancelled", reason)
        self._pending.clear()
        log.info("approvals_cancelled", count=len(pending), reason=reason)
        return len(pending)

    async def cancel_zone(self, zone_id: str) -> None:
        """Cancel approval for a specific zone (zone invalidated)."""
        pending = self._db.get_pending_approvals()
        for a in pending:
            if a.get("zone_id") == zone_id:
                self._db.update_approval(
                    a["id"], status="cancelled",
                    responded_at=datetime.now(timezone.utc).isoformat(),
                    reject_reason="Zone invalidated",
                )
                self._pending.pop(a["id"], None)
                await self._broadcast_update(a["id"], "cancelled", "Zone invalidated")
                log.info("approval_zone_cancelled", id=a["id"], zone_id=zone_id)

    def recover(self) -> list[dict]:
        """Load pending approvals from DB on restart."""
        pending = self._db.get_pending_approvals()
        for a in pending:
            self._pending[a["id"]] = a
        log.info("approvals_recovered", count=len(pending))
        return pending

    async def _broadcast_update(
        self, approval_id: str, status: str, reason: str = ""
    ) -> None:
        """Push status update to all channels."""
        event = {
            "type": "approval_update",
            "id": approval_id,
            "status": status,
            "reason": reason,
        }
        for cb in self._ws_callbacks:
            try:
                await cb(event)
            except Exception:
                pass

    @staticmethod
    def _determine_order_type(direction: str, zone_price: float, current: float) -> str:
        if direction == "buy":
            return "buy_limit" if zone_price < current else "buy_stop"
        return "sell_limit" if zone_price > current else "sell_stop"
