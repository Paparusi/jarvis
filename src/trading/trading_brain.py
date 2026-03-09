"""TradingBrain — Top-level orchestrator wiring all trading modules together.

Lifecycle: start -> schedule plans -> monitor -> confirm -> manage -> stop

Wires:
- TradePlanner (creates session plans)
- PriceMonitor (lightweight price watcher)
- EntryConfirmer (LLM confirmation on alerts)
- PositionManager (deterministic position management)
- RiskGuard (circuit breaker)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.trading.approval_manager import ApprovalManager
from src.trading.entry_confirmer import EntryConfirmer, EntryDecision
from src.trading.pending_orders import PendingOrderManager
from src.trading.persistence import TradingPersistence
from src.trading.position_manager import ManagedPosition, PositionManager
from src.trading.price_monitor import PriceMonitor
from src.trading.trade_planner import TradePlan, TradePlanner
from src.utils.logging import get_logger

log = get_logger("trading.trading_brain")

NotifyCallback = Callable[[str], Awaitable[None]]


class TradingBrain:
    """Top-level orchestrator. Wires all modules together.

    Lifecycle: start -> schedule plans -> monitor -> confirm -> manage -> stop
    """

    def __init__(
        self,
        mt5_client: Any,
        risk_guard: Any = None,
        journal: Any = None,
        symbol: str = "",
    ) -> None:
        """Create all sub-modules and wire callbacks.

        - Creates TradePlanner(mt5_client, risk_guard)
        - Creates PriceMonitor(mt5_client, poll_interval=10)
        - Creates EntryConfirmer(mt5_client, risk_guard)
        - Creates PositionManager(mt5_client, risk_guard, journal)
        - Wires callbacks: monitor.on_alert -> _on_zone_alert,
          monitor.on_invalidate -> _on_zone_invalidate
        - Sets PositionManager.on_notify callback to _notify
        """
        self._mt5 = mt5_client
        self._risk_guard = risk_guard
        self._journal = journal
        self._symbol = symbol or __import__("os").environ.get("TRADING_SYMBOL", "XAUUSD")

        # Sub-modules
        self.planner = TradePlanner(mt5_client, risk_guard)
        self.monitor = PriceMonitor(mt5_client, poll_interval=10)
        self.confirmer = EntryConfirmer(mt5_client, risk_guard)
        self.position_manager = PositionManager(mt5_client, risk_guard, journal)

        # v2: Persistence + Pending Orders
        self.persistence = TradingPersistence()
        self.pending_manager = PendingOrderManager(mt5_client, risk_guard, self.persistence)
        self.pending_manager.on_notify(self._notify)

        # v3: Approval Gate (semi-auto trading)
        self.approval_manager = ApprovalManager(self.persistence, mt5_client, risk_guard)


        # Wire callbacks
        self.monitor.on_alert(self._on_zone_alert)
        self.monitor.on_invalidate(self._on_zone_invalidate)
        self.position_manager.on_notify(self._notify)

        # State
        self.current_plan: TradePlan | None = None
        self._notify_cb: NotifyCallback | None = None
        self._running: bool = False
        self._scheduler_task: asyncio.Task | None = None
        self._last_plan_hour: int = -1
        self._plan_id: int | None = None  # DB row id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_notify(self, callback: NotifyCallback) -> None:
        """Register notification callback (e.g., send Telegram message)."""
        self._notify_cb = callback

    async def start(self) -> None:
        """Start brain: start position manager loop + session scheduler task."""
        if self._running:
            return
        self._running = True

        # Recover state from DB + MT5
        await self._recover_state()

        await self.position_manager.start()
        self._scheduler_task = asyncio.create_task(self._session_scheduler())
        log.info("trading_brain_started")

    async def stop(self) -> None:
        """Stop brain: stop monitor + manager + cancel approvals + cancel scheduler."""
        self._running = False

        # Cancel pending approvals
        await self.approval_manager.cancel_all("Brain stopped")

        # Stop monitor
        await self.monitor.stop()

        # Stop position manager
        await self.position_manager.stop()

        # Cancel scheduler
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

        log.info("trading_brain_stopped")

    async def plan_now(self, session: str = "") -> str:
        """Force create plan for given session (or auto-detect).

        1. Call planner.create_plan(session)
        2. Store as current_plan + persist to DB
        3. Cancel old pending orders + place new ones at zones
        4. Pass plan zones to monitor via monitor.set_plan(plan_dict)
        5. Start monitor if not running
        6. Notify with formatted plan
        7. Return formatted plan text (format_plan_telegram)
        """
        if not session:
            session = self._detect_session()

        # Cancel pending approvals from previous plan
        await self.approval_manager.cancel_all("New plan created")

        plan = await self.planner.create_plan(session)
        self.current_plan = plan

        # Save plan to DB
        try:
            self._plan_id = self.persistence.save_plan(plan)
        except Exception as exc:
            log.warning("plan_save_failed", error=str(exc))

        # Cancel old pending orders
        await self.pending_manager.cancel_all()

        # Place pending orders at approved zones
        try:
            account = await self._mt5.get_account()
            equity = account.get("equity", account.get("balance", 0))
        except Exception:
            equity = 0

        if equity > 0 and plan.alert_zones:
            risk_pct = 1.0
            if self._risk_guard and hasattr(self._risk_guard, 'config') and hasattr(self._risk_guard.config, 'max_risk_per_trade_pct'):
                risk_pct = self._risk_guard.config.max_risk_per_trade_pct
            placed = await self.pending_manager.place_zone_orders(
                plan.alert_zones, equity, risk_pct
            )
            log.info("pending_orders_placed", count=len(placed))

        # Set up monitor (for invalidation tracking)
        plan_dict = {
            "alert_zones": plan.alert_zones,
            "scenarios": [
                {"condition": s.condition, "action": s.action, "new_bias": s.new_bias}
                for s in plan.scenarios
            ],
        }
        self.monitor.set_plan(plan_dict)
        if not self.monitor.is_running:
            await self.monitor.start()

        formatted = self.planner.format_plan_telegram(plan)
        # Add pending order info
        if self.pending_manager.order_count > 0:
            formatted += f"\n\nPending orders: {self.pending_manager.order_count}"
        await self._notify(formatted)

        log.info("plan_created", session=session, zones=len(plan.alert_zones))
        return formatted

    async def kill(self) -> str:
        """Emergency: close all positions + cancel pending orders + stop everything.

        1. position_manager.emergency_close_all("kill_switch")
        2. pending_manager.cancel_all()
        3. monitor.stop()
        4. Return summary of how many positions closed + pending cancelled
        """
        count = await self.position_manager.emergency_close_all("kill_switch")
        pending_cancelled = await self.pending_manager.cancel_all()
        await self.monitor.stop()

        summary = (
            f"KILL SWITCH: {count} position(s) closed, "
            f"{pending_cancelled} pending order(s) cancelled, monitor stopped."
        )
        await self._notify(summary)
        log.warning("kill_switch_activated", positions_closed=count, pending_cancelled=pending_cancelled)
        return summary

    def get_status(self) -> dict:
        """Full status: plan summary, active zones, active positions, risk state.

        Return dict with keys:
        - running: bool
        - plan: plan short summary or "No plan"
        - active_zones: count from monitor
        - active_positions: count from position_manager
        - risk: risk_guard.get_status() if available
        - trades_taken: from current_plan
        """
        plan_summary = "No plan"
        trades_taken = 0
        if self.current_plan:
            plan_summary = self.planner.format_plan_short(self.current_plan)
            trades_taken = self.current_plan.trades_taken

        risk_status = {}
        if self._risk_guard:
            try:
                risk_status = self._risk_guard.get_status()
            except Exception:
                risk_status = {}

        return {
            "running": self._running,
            "plan": plan_summary,
            "active_zones": len(self.monitor.active_zones),
            "active_positions": self.position_manager.position_count,
            "pending_orders": self.pending_manager.order_count,
            "risk": risk_status,
            "trades_taken": trades_taken,
        }

    # ------------------------------------------------------------------
    # Background scheduler
    # ------------------------------------------------------------------

    async def _session_scheduler(self) -> None:
        """Background task: check every 60s for session changes.

        - 07:00 UTC -> auto-plan "London"
        - 12:30 UTC -> auto-plan "New York"
        - 16:00 UTC -> cleanup: stop monitor, generate daily summary
        - Use datetime.now(timezone.utc).hour and .minute for checks
        - Track _last_plan_hour to avoid re-planning in same hour
        """
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                hour = now.hour
                minute = now.minute

                # 07:00 UTC -> London plan
                if hour == 7 and minute < 5 and self._last_plan_hour != 7:
                    self._last_plan_hour = 7
                    await self.plan_now("London")

                # 12:30 UTC -> New York plan
                elif hour == 12 and 30 <= minute < 35 and self._last_plan_hour != 12:
                    self._last_plan_hour = 12
                    await self.plan_now("New York")

                # 16:00 UTC -> cleanup
                elif hour == 16 and minute < 5 and self._last_plan_hour != 16:
                    self._last_plan_hour = 16
                    await self.monitor.stop()
                    summary = await self._generate_daily_summary()
                    await self._notify(summary)

                # Check for filled pending orders every cycle
                filled = await self.pending_manager.check_fills()
                for order_info in filled:
                    await self._on_pending_filled(order_info)

                await asyncio.sleep(60)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("scheduler_error", error=str(exc))
                await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Alert callbacks
    # ------------------------------------------------------------------

    async def _on_zone_alert(self, zone: dict, price: float) -> None:
        """Alert callback from PriceMonitor.

        1. Notify: "Price {price} entered zone {zone_id}"
        2. Get open positions from MT5
        3. Call confirmer.confirm(zone, price, positions)
        4. If ENTER:
           - Place order via MT5 (mt5_client.place_order)
           - Create ManagedPosition from order result
           - Add to position_manager
           - Increment current_plan.trades_taken
           - Notify with entry details
        5. If SKIP: Notify skip reason, remove zone from monitor
        6. If WAIT: Notify wait, keep zone active
        """
        zone_id = zone.get("zone_id", "unknown")
        await self._notify(f"Price {price} entered zone {zone_id}")

        # Get current open positions
        try:
            positions = await self._mt5.get_positions()
        except Exception:
            positions = []

        # LLM confirmation
        decision: EntryDecision = await self.confirmer.confirm(
            zone, price, positions
        )

        if decision.action == "ENTER":
            # Create approval instead of placing order directly
            approval = await self.approval_manager.create_approval(
                entry_decision=decision,
                zone=zone,
                plan_id=self._plan_id,
                symbol=self._symbol,
                current_price=price,
            )
            log.info(
                "trade_approval_requested",
                approval_id=approval["id"],
                direction=decision.direction,
                price=approval["price"],
            )
            # Notify user with formatted alert
            if self._notify_cb:
                msg = self._format_approval_alert(approval)
                await self._notify(msg)

        elif decision.action == "SKIP":
            skip_msg = f"SKIP zone {zone_id}: {decision.reasoning}"
            await self._notify(skip_msg)
            self.monitor.remove_zone(zone_id)
            log.info("zone_skipped", zone_id=zone_id, reason=decision.reasoning)

        elif decision.action == "WAIT":
            wait_msg = f"WAIT zone {zone_id}: {decision.reasoning}"
            await self._notify(wait_msg)
            log.info("zone_wait", zone_id=zone_id, reason=decision.reasoning)

    async def _on_zone_invalidate(self, zone: dict, reason: str) -> None:
        """Zone invalidation callback. Cancel pending order + approval + notify."""
        zone_id = zone.get("zone_id", "unknown")
        # Cancel pending order for this zone
        cancelled = await self.pending_manager.cancel_zone(zone_id)
        # Cancel pending approval for this zone
        await self.approval_manager.cancel_zone(zone_id)
        cancel_note = " (pending order cancelled)" if cancelled else ""
        msg = f"Zone {zone_id} invalidated: {reason}{cancel_note}"
        await self._notify(msg)
        log.info("zone_invalidated", zone_id=zone_id, reason=reason, cancelled=cancelled)

    async def _on_pending_filled(self, order_info: dict) -> None:
        """Handle a pending order that was filled."""
        ticket = order_info.get("ticket", 0)
        zone_id = order_info.get("zone_id", "")
        direction = order_info.get("direction", "buy")
        volume = order_info.get("volume", 0.01)
        price = order_info.get("price", 0)
        sl = order_info.get("sl", 0)
        tp1 = order_info.get("tp1", 0)
        tp2 = order_info.get("tp2", 0)

        managed = ManagedPosition(
            ticket=ticket,
            symbol=self._symbol,
            direction=direction,
            volume=volume,
            entry_price=price,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            original_sl=sl,
            zone_id=zone_id,
            confluence_score=order_info.get("confluence_score", 0),
        )
        await self.position_manager.add_position(managed)

        try:
            self.persistence.save_position(managed)
        except Exception as exc:
            log.warning("position_save_failed", error=str(exc))

        if self.current_plan:
            self.current_plan.trades_taken += 1
            if self._plan_id:
                try:
                    self.persistence.update_plan_trades(self._plan_id, self.current_plan.trades_taken)
                except Exception:
                    pass

        msg = (
            f"FILLED: {direction.upper()} {self._symbol} @ {price}\n"
            f"Ticket: {ticket} | Vol: {volume}\n"
            f"SL: {sl} | TP1: {tp1} | TP2: {tp2}\n"
            f"Zone: {zone_id}"
        )
        await self._notify(msg)
        log.info("pending_filled_managed", ticket=ticket, zone_id=zone_id)

    # ------------------------------------------------------------------
    # State recovery
    # ------------------------------------------------------------------

    async def _recover_state(self) -> None:
        """On startup: load plan + positions from DB, sync pending orders with MT5."""
        try:
            state = self.persistence.get_recovery_state()
        except Exception as exc:
            log.warning("recovery_failed", error=str(exc))
            return

        # 1. Recover plan
        plan_dict = state.get("plan")
        if plan_dict:
            from src.trading.trade_planner import Scenario as _Scenario
            scenarios = [
                _Scenario(
                    condition=s.get("condition", ""),
                    action=s.get("action", ""),
                    new_bias=s.get("new_bias", "neutral"),
                )
                for s in plan_dict.get("scenarios", [])
                if isinstance(s, dict)
            ]
            self.current_plan = TradePlan(
                session=plan_dict["session"],
                created_at=datetime.fromisoformat(plan_dict["created_at"]),
                bias=plan_dict.get("bias", "neutral"),
                bias_reasoning=plan_dict.get("bias_reasoning", ""),
                market_regime=plan_dict.get("market_regime", ""),
                key_levels=plan_dict.get("key_levels", {}),
                alert_zones=plan_dict.get("alert_zones", []),
                scenarios=scenarios,
                risk_budget_pct=plan_dict.get("risk_budget_pct", 2.0),
                max_trades=plan_dict.get("max_trades", 3),
                trades_taken=plan_dict.get("trades_taken", 0),
                invalidation=plan_dict.get("invalidation", ""),
                active=True,
            )
            self._plan_id = plan_dict.get("id")
            log.info("plan_recovered", session=self.current_plan.session,
                     zones=len(self.current_plan.alert_zones))

            # Resume price monitor with recovered plan
            plan_monitor_dict = {
                "alert_zones": self.current_plan.alert_zones,
                "scenarios": [
                    {"condition": s.condition, "action": s.action, "new_bias": s.new_bias}
                    for s in self.current_plan.scenarios
                ],
            }
            self.monitor.set_plan(plan_monitor_dict)
            if not self.monitor.is_running:
                await self.monitor.start()

        # 2. Recover managed positions
        saved_positions = state.get("positions", [])
        if saved_positions:
            try:
                mt5_positions = await self._mt5.get_positions()
            except Exception:
                mt5_positions = []

            mt5_tickets = {p.get("ticket") for p in mt5_positions}

            for saved in saved_positions:
                ticket = saved.get("ticket")
                if ticket in mt5_tickets:
                    managed = ManagedPosition(
                        ticket=ticket,
                        symbol=saved.get("symbol", self._symbol),
                        direction=saved.get("direction", "buy"),
                        volume=saved.get("volume", 0.01),
                        entry_price=saved.get("entry_price", 0),
                        sl=saved.get("sl", 0),
                        tp1=saved.get("tp1", 0),
                        tp2=saved.get("tp2", 0),
                        original_sl=saved.get("original_sl", 0),
                        be_moved=bool(saved.get("be_moved", 0)),
                        tp1_hit=bool(saved.get("tp1_hit", 0)),
                        remaining_volume=saved.get("remaining_volume", 0),
                        trail_sl=saved.get("trail_sl", 0),
                        zone_id=saved.get("zone_id", ""),
                        confluence_score=saved.get("confluence_score", 0),
                    )
                    await self.position_manager.add_position(managed)
                    log.info("position_recovered", ticket=ticket)
                else:
                    self.persistence.close_position_record(ticket, "closed_offline", 0)
                    log.info("position_closed_offline", ticket=ticket)

        # 3. Recover/sync pending orders
        await self.pending_manager.sync_from_mt5()
        synced = self.pending_manager.order_count
        if synced:
            log.info("pending_orders_synced", count=synced)

        # 4. Recover pending approvals
        recovered_approvals = self.approval_manager.recover()
        if recovered_approvals:
            log.info("approvals_recovered", count=len(recovered_approvals))

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    async def _generate_daily_summary(self) -> str:
        """Generate end-of-day summary.

        Include: trades taken, P/L from risk_guard state, win rate approximation,
        risk budget used, plan accuracy.
        """
        trades_taken = 0
        max_trades = 0
        session = ""
        if self.current_plan:
            trades_taken = self.current_plan.trades_taken
            max_trades = self.current_plan.max_trades
            session = self.current_plan.session

        daily_pnl = 0.0
        daily_trades = 0
        consecutive_losses = 0
        if self._risk_guard:
            try:
                status = self._risk_guard.get_status()
                state = status.get("state", {})
                daily_pnl = state.get("daily_pnl", 0.0)
                daily_trades = state.get("daily_trades", 0)
                consecutive_losses = state.get("consecutive_losses", 0)
            except Exception:
                pass

        # Win rate approximation
        wins = max(0, daily_trades - consecutive_losses)
        win_rate = (wins / daily_trades * 100) if daily_trades > 0 else 0.0

        lines = [
            "DAILY SUMMARY",
            f"Session: {session or 'N/A'}",
            f"Trades taken: {trades_taken}/{max_trades}",
            f"Total trades: {daily_trades}",
            f"P/L: {daily_pnl:+.2f}",
            f"Win rate: ~{win_rate:.0f}%",
            f"Consecutive losses: {consecutive_losses}",
        ]

        summary = "\n".join(lines)
        log.info("daily_summary_generated", trades=daily_trades, pnl=daily_pnl)
        return summary

    # ------------------------------------------------------------------
    # Approval response handler
    # ------------------------------------------------------------------

    async def handle_approval_response(
        self, approval_id: str, action: str, via: str = "telegram"
    ) -> dict:
        """Handle approve/reject from Telegram or Dashboard."""
        if action == "approve":
            result = await self.approval_manager.approve(approval_id, via=via)
            if result.get("ticket"):
                # Create ManagedPosition from the approval
                approval = self.persistence.get_approval(approval_id)
                if approval:
                    managed = ManagedPosition(
                        ticket=result["ticket"],
                        zone_id=approval.get("zone_id", ""),
                        symbol=approval["symbol"],
                        direction=approval["direction"],
                        volume=approval["lot"],
                        entry_price=approval["price"],
                        sl=approval["sl"],
                        tp1=approval["tp1"],
                        tp2=approval.get("tp2"),
                        original_sl=approval["sl"],
                        confluence_score=approval.get("confluence_score", 0),
                    )
                    await self.position_manager.add_position(managed)
                    if self.current_plan:
                        self.current_plan.trades_taken += 1
                        if self._plan_id:
                            try:
                                self.persistence.update_plan_trades(
                                    self._plan_id, self.current_plan.trades_taken
                                )
                            except Exception:
                                pass
            return result
        elif action == "reject":
            return await self.approval_manager.reject(approval_id, via=via)
        return {"error": f"Unknown action: {action}"}

    def _format_approval_alert(self, approval: dict) -> str:
        """Format approval alert for notification."""
        direction_emoji = "\U0001f7e2" if approval["direction"] == "buy" else "\U0001f534"
        return (
            f"\U0001f514 TRADE SIGNAL \u2014 {approval['symbol']}\n\n"
            f"\U0001f4ca Ph\u00e2n t\u00edch:\n{approval.get('analysis', 'N/A')}\n\n"
            f"\U0001f4c8 Entry Plan:\n"
            f"\u2022 Type: {direction_emoji} {approval['order_type'].upper()} @ {approval['price']}\n"
            f"\u2022 SL: {approval['sl']}\n"
            f"\u2022 TP1: {approval['tp1']}\n"
            f"\u2022 TP2: {approval.get('tp2', '\u2014')}\n"
            f"\u2022 Lot: {approval['lot']} (risk {approval.get('risk_pct', 0)}% = ${approval.get('risk_usd', 0):.0f})\n"
            f"\u2022 Confluence: {approval.get('confluence_score', 0)}/100\n"
        )

    # ------------------------------------------------------------------
    # Notification helper
    # ------------------------------------------------------------------

    async def _notify(self, message: str) -> None:
        """Send notification via registered callback."""
        if self._notify_cb:
            try:
                await self._notify_cb(message)
            except Exception as exc:
                log.warning("notify_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Session detection helper
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_session() -> str:
        """Auto-detect current trading session based on UTC time."""
        now = datetime.now(timezone.utc)
        hour = now.hour
        if 7 <= hour < 12:
            return "London"
        elif 12 <= hour < 17:
            return "New York"
        else:
            return "London"  # default fallback
