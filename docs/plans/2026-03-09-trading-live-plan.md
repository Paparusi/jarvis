# Trading Live Mode Implementation Plan

> **For Claude:** Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add approval gate to Trading Brain so Bi must approve every trade entry via Telegram or Dashboard before order placement.

**Architecture:** New ApprovalManager module intercepts EntryConfirmer ENTER decisions, persists to DB, sends rich alerts to Telegram (inline buttons) + Dashboard (WebSocket), waits for approve/reject response, then places or skips the order.

**Tech Stack:** Python (asyncio), SQLite, python-telegram-bot (InlineKeyboardButton), FastAPI WebSocket, Next.js React.

---

## Task 0: Persistence — Add trade_approvals Table

**Files:**
- Modify: `src/trading/persistence.py`
- Test: `tests/unit/test_trading_persistence.py`

**Step 1: Add table creation**

In `_init_trading_tables()`, after the `pending_orders` CREATE TABLE, add:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_approvals (
                id TEXT PRIMARY KEY,
                plan_id INTEGER,
                zone_id TEXT,
                symbol TEXT,
                direction TEXT,
                order_type TEXT,
                price REAL,
                sl REAL,
                tp1 REAL,
                tp2 REAL,
                lot REAL,
                risk_pct REAL,
                risk_usd REAL,
                confluence_score REAL,
                analysis TEXT,
                smc_summary TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                responded_at TEXT,
                responded_via TEXT,
                reject_reason TEXT,
                order_ticket INTEGER
            )
        """)
```

**Step 2: Add CRUD methods**

Add to `TradingPersistence` class:

```python
def save_approval(self, approval: dict) -> None:
    conn = self._get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO trade_approvals
           (id, plan_id, zone_id, symbol, direction, order_type,
            price, sl, tp1, tp2, lot, risk_pct, risk_usd,
            confluence_score, analysis, smc_summary, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            approval["id"], approval.get("plan_id"), approval.get("zone_id"),
            approval["symbol"], approval["direction"], approval["order_type"],
            approval["price"], approval["sl"], approval["tp1"], approval["tp2"],
            approval["lot"], approval["risk_pct"], approval["risk_usd"],
            approval.get("confluence_score", 0), approval.get("analysis", ""),
            approval.get("smc_summary", ""), approval.get("status", "pending"),
            approval.get("created_at", ""),
        ),
    )
    conn.commit()

def update_approval(self, approval_id: str, **kwargs) -> None:
    conn = self._get_conn()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [approval_id]
    conn.execute(f"UPDATE trade_approvals SET {sets} WHERE id = ?", vals)
    conn.commit()

def get_pending_approvals(self) -> list[dict]:
    conn = self._get_conn()
    rows = conn.execute(
        "SELECT * FROM trade_approvals WHERE status = 'pending' ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]

def get_approval(self, approval_id: str) -> dict | None:
    conn = self._get_conn()
    row = conn.execute(
        "SELECT * FROM trade_approvals WHERE id = ?", (approval_id,)
    ).fetchone()
    return dict(row) if row else None
```

**Step 3: Write tests**

Add to `tests/unit/test_trading_persistence.py`:

```python
class TestTradeApprovals:
    def test_save_and_get_approval(self, persistence):
        approval = {
            "id": "apr_test1",
            "plan_id": 1,
            "zone_id": "z1",
            "symbol": "XAUUSD",
            "direction": "buy",
            "order_type": "buy_limit",
            "price": 2340.50,
            "sl": 2338.00,
            "tp1": 2344.00,
            "tp2": 2347.00,
            "lot": 0.03,
            "risk_pct": 0.8,
            "risk_usd": 24.0,
            "confluence_score": 85,
            "analysis": "Bullish setup",
            "smc_summary": "OB + FVG",
            "status": "pending",
            "created_at": "2026-03-09T12:00:00",
        }
        persistence.save_approval(approval)
        result = persistence.get_approval("apr_test1")
        assert result is not None
        assert result["symbol"] == "XAUUSD"
        assert result["lot"] == 0.03

    def test_get_pending_approvals(self, persistence):
        for i in range(3):
            persistence.save_approval({
                "id": f"apr_{i}", "symbol": "XAUUSD", "direction": "buy",
                "order_type": "buy_limit", "price": 2340, "sl": 2338,
                "tp1": 2344, "tp2": 2347, "lot": 0.03, "risk_pct": 0.8,
                "risk_usd": 24, "status": "pending", "created_at": f"2026-03-09T12:0{i}:00",
            })
        persistence.update_approval("apr_0", status="approved")
        pending = persistence.get_pending_approvals()
        assert len(pending) == 2

    def test_update_approval(self, persistence):
        persistence.save_approval({
            "id": "apr_upd", "symbol": "XAUUSD", "direction": "buy",
            "order_type": "buy_limit", "price": 2340, "sl": 2338,
            "tp1": 2344, "tp2": 2347, "lot": 0.03, "risk_pct": 0.8,
            "risk_usd": 24, "status": "pending", "created_at": "2026-03-09T12:00:00",
        })
        persistence.update_approval("apr_upd", status="approved",
                                     responded_at="2026-03-09T12:01:00",
                                     responded_via="telegram", order_ticket=12345)
        result = persistence.get_approval("apr_upd")
        assert result["status"] == "approved"
        assert result["order_ticket"] == 12345
```

**Step 4: Run tests**

```bash
python -m pytest tests/unit/test_trading_persistence.py -v -x 2>&1 | tail -20
```

**Step 5: Commit**

```bash
git add src/trading/persistence.py tests/unit/test_trading_persistence.py
git commit -m "feat: add trade_approvals table to persistence"
```

---

## Task 1: ApprovalManager Module

**Files:**
- Create: `src/trading/approval_manager.py`
- Create: `tests/unit/test_approval_manager.py`

**Step 1: Create ApprovalManager**

```python
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
```

**Step 2: Write tests**

Create `tests/unit/test_approval_manager.py`:

```python
"""Tests for ApprovalManager."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from src.trading.approval_manager import ApprovalManager


@dataclass
class MockEntryDecision:
    action: str = "ENTER"
    direction: str = "buy"
    reasoning: str = "Bullish OB + FVG"
    lot_size: float = 0.03
    sl: float = 2338.0
    tp1: float = 2344.0
    tp2: float = 2347.0
    risk_approved: bool = True
    risk_pct: float = 0.8
    risk_usd: float = 24.0
    smc_summary: str = "OB + FVG overlap"


@dataclass
class MockVetoResult:
    approved: bool = True
    reason: str = ""
    rule: str = ""


@pytest.fixture
def mock_persistence():
    p = MagicMock()
    p.save_approval = MagicMock()
    p.get_approval = MagicMock(return_value={
        "id": "apr_test", "status": "pending", "symbol": "XAUUSD",
        "direction": "buy", "lot": 0.03, "price": 2340.5,
        "sl": 2338.0, "tp1": 2344.0, "risk_pct": 0.8,
    })
    p.update_approval = MagicMock()
    p.get_pending_approvals = MagicMock(return_value=[])
    return p


@pytest.fixture
def mock_mt5():
    m = AsyncMock()
    m.place_order = AsyncMock(return_value={"ticket": 12345, "retcode": 10009})
    m.get_tick = AsyncMock(return_value={"bid": 2340.5, "ask": 2340.8})
    return m


@pytest.fixture
def mock_risk_guard():
    rg = MagicMock()
    rg.check_entry = MagicMock(return_value=MockVetoResult(approved=True))
    return rg


@pytest.fixture
def manager(mock_persistence, mock_mt5, mock_risk_guard):
    return ApprovalManager(mock_persistence, mock_mt5, mock_risk_guard)


class TestCreateApproval:
    @pytest.mark.asyncio
    async def test_creates_and_persists(self, manager, mock_persistence):
        decision = MockEntryDecision()
        zone = {"price_high": 2341.0, "price_low": 2340.0, "direction": "buy", "score": 85}
        result = await manager.create_approval(decision, zone, plan_id=1, current_price=2342.0)

        assert result["id"].startswith("apr_")
        assert result["direction"] == "buy"
        assert result["lot"] == 0.03
        assert result["status"] == "pending"
        mock_persistence.save_approval.assert_called_once()

    @pytest.mark.asyncio
    async def test_notifies_callbacks(self, manager):
        cb = AsyncMock()
        manager.on_notify(cb)
        decision = MockEntryDecision()
        zone = {"price_high": 2341.0, "price_low": 2340.0, "score": 85}
        await manager.create_approval(decision, zone, plan_id=1)
        cb.assert_called_once()
        assert cb.call_args[0][0] == "approval"

    @pytest.mark.asyncio
    async def test_pushes_ws_event(self, manager):
        ws_cb = AsyncMock()
        manager.on_ws_event(ws_cb)
        decision = MockEntryDecision()
        zone = {"price_high": 2341.0, "price_low": 2340.0, "score": 85}
        await manager.create_approval(decision, zone, plan_id=1)
        ws_cb.assert_called_once()
        event = ws_cb.call_args[0][0]
        assert event["type"] == "approval"


class TestApprove:
    @pytest.mark.asyncio
    async def test_approve_places_order(self, manager, mock_mt5, mock_persistence):
        result = await manager.approve("apr_test", via="telegram")
        assert result["status"] == "approved"
        assert result["ticket"] == 12345
        mock_mt5.place_order.assert_called_once()
        mock_persistence.update_approval.assert_called()

    @pytest.mark.asyncio
    async def test_approve_risk_veto(self, manager, mock_risk_guard, mock_persistence):
        mock_risk_guard.check_entry.return_value = MockVetoResult(
            approved=False, reason="Daily loss limit", rule="max_daily_loss"
        )
        result = await manager.approve("apr_test")
        assert "error" in result
        assert "RiskGuard" in result["error"]

    @pytest.mark.asyncio
    async def test_approve_not_found(self, manager, mock_persistence):
        mock_persistence.get_approval.return_value = None
        result = await manager.approve("apr_missing")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_approve_already_approved(self, manager, mock_persistence):
        mock_persistence.get_approval.return_value = {"id": "apr_test", "status": "approved"}
        result = await manager.approve("apr_test")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_approve_price_moved(self, manager, mock_mt5, mock_persistence):
        mock_mt5.get_tick.return_value = {"bid": 2350.0, "ask": 2350.3}
        result = await manager.approve("apr_test")
        assert "error" in result
        assert "Price moved" in result["error"]


class TestReject:
    @pytest.mark.asyncio
    async def test_reject(self, manager, mock_persistence):
        result = await manager.reject("apr_test", via="dashboard", reason="Not confident")
        assert result["status"] == "rejected"
        mock_persistence.update_approval.assert_called()

    @pytest.mark.asyncio
    async def test_reject_not_found(self, manager, mock_persistence):
        mock_persistence.get_approval.return_value = None
        result = await manager.reject("apr_missing")
        assert "error" in result


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancel_all(self, manager, mock_persistence):
        mock_persistence.get_pending_approvals.return_value = [
            {"id": "apr_1"}, {"id": "apr_2"},
        ]
        count = await manager.cancel_all("Brain stopped")
        assert count == 2
        assert mock_persistence.update_approval.call_count == 2


class TestCancelZone:
    @pytest.mark.asyncio
    async def test_cancel_zone(self, manager, mock_persistence):
        mock_persistence.get_pending_approvals.return_value = [
            {"id": "apr_1", "zone_id": "z1"},
            {"id": "apr_2", "zone_id": "z2"},
        ]
        await manager.cancel_zone("z1")
        mock_persistence.update_approval.assert_called_once()


class TestRecover:
    def test_recover_loads_pending(self, manager, mock_persistence):
        mock_persistence.get_pending_approvals.return_value = [
            {"id": "apr_1"}, {"id": "apr_2"},
        ]
        result = manager.recover()
        assert len(result) == 2
        assert "apr_1" in manager._pending


class TestDetermineOrderType:
    def test_buy_below_price(self):
        assert ApprovalManager._determine_order_type("buy", 2340, 2345) == "buy_limit"

    def test_buy_above_price(self):
        assert ApprovalManager._determine_order_type("buy", 2340, 2335) == "buy_stop"

    def test_sell_above_price(self):
        assert ApprovalManager._determine_order_type("sell", 2340, 2335) == "sell_limit"

    def test_sell_below_price(self):
        assert ApprovalManager._determine_order_type("sell", 2340, 2345) == "sell_stop"
```

**Step 3: Run tests**

```bash
python -m pytest tests/unit/test_approval_manager.py -v -x 2>&1 | tail -25
```

**Step 4: Commit**

```bash
git add src/trading/approval_manager.py tests/unit/test_approval_manager.py
git commit -m "feat: add ApprovalManager with approval gate lifecycle"
```

---

## Task 2: Wire ApprovalManager into TradingBrain

**Files:**
- Modify: `src/trading/trading_brain.py`

**Step 1: Add ApprovalManager to TradingBrain.__init__**

After `self.pending_manager = ...`, add:

```python
from src.trading.approval_manager import ApprovalManager

self.approval_manager = ApprovalManager(
    persistence=self._persistence,
    mt5_client=self._mt5,
    risk_guard=self._risk_guard,
)
```

**Step 2: Modify `_on_zone_alert()` — replace direct order with approval**

Find the section where `decision.action == "ENTER"` leads to `self._mt5.place_order()`. Replace the order placement block with:

```python
if decision.action == "ENTER":
    # Create approval instead of placing order directly
    approval = await self.approval_manager.create_approval(
        entry_decision=decision,
        zone=zone,
        plan_id=self._plan_id,
        symbol=self._symbol,
        current_price=price,
    )
    log.info("trade_approval_requested", approval_id=approval["id"],
             direction=decision.direction, price=approval["price"])
    # Notify user
    if self._notify_cb:
        msg = self._format_approval_alert(approval)
        await self._notify_cb(msg)
    return
```

Keep the existing code for SKIP and WAIT unchanged.

**Step 3: Add approval response handler**

Add method to TradingBrain:

```python
async def handle_approval_response(self, approval_id: str, action: str, via: str = "telegram") -> dict:
    """Handle approve/reject from Telegram or Dashboard."""
    if action == "approve":
        result = await self.approval_manager.approve(approval_id, via=via)
        if result.get("ticket"):
            # Create ManagedPosition from the approval
            approval = self._persistence.get_approval(approval_id)
            if approval:
                from src.trading.position_manager import ManagedPosition
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
                    self._persistence.update_plan(self._plan_id, trades_taken=self.current_plan.trades_taken)
        return result
    elif action == "reject":
        return await self.approval_manager.reject(approval_id, via=via)
    return {"error": f"Unknown action: {action}"}
```

**Step 4: Add `_format_approval_alert()` helper**

```python
def _format_approval_alert(self, approval: dict) -> str:
    direction_emoji = "🟢" if approval["direction"] == "buy" else "🔴"
    return (
        f"🔔 TRADE SIGNAL — {approval['symbol']}\n\n"
        f"📊 Phân tích:\n{approval.get('analysis', 'N/A')}\n\n"
        f"📈 Entry Plan:\n"
        f"• Type: {direction_emoji} {approval['order_type'].upper()} @ {approval['price']}\n"
        f"• SL: {approval['sl']}\n"
        f"• TP1: {approval['tp1']}\n"
        f"• TP2: {approval.get('tp2', '—')}\n"
        f"• Lot: {approval['lot']} (risk {approval.get('risk_pct', 0)}% = ${approval.get('risk_usd', 0):.0f})\n"
        f"• Confluence: {approval.get('confluence_score', 0)}/100\n"
    )
```

**Step 5: Update `cancel_all` on brain stop/new plan**

In `stop()` method, add:
```python
await self.approval_manager.cancel_all("Brain stopped")
```

In `plan_now()`, before creating new plan, add:
```python
await self.approval_manager.cancel_all("New plan created")
```

**Step 6: Update `_recover_state()`**

Add after existing recovery:
```python
# Recover pending approvals
recovered_approvals = self.approval_manager.recover()
if recovered_approvals:
    log.info("approvals_recovered", count=len(recovered_approvals))
```

**Step 7: Run tests**

```bash
python -m pytest tests/unit/test_trading_brain.py -v -x 2>&1 | tail -20
python -m pytest tests/unit/ -x -q 2>&1 | tail -5
```

**Step 8: Commit**

```bash
git add src/trading/trading_brain.py
git commit -m "feat: wire ApprovalManager into TradingBrain — approval gate active"
```

---

## Task 3: Telegram Approval Handlers

**Files:**
- Modify: `src/gateway/channels/telegram.py`

**Step 1: Update `_send_trading_notification()` to handle approval type**

Replace or extend `_send_trading_notification()` to detect approval messages and add Approve/Reject buttons:

```python
async def _send_trading_notification(self, event_type: str, data: Any = None) -> None:
    owner_id = self._owner_id
    if not owner_id:
        return

    if event_type == "approval" and isinstance(data, dict):
        # Rich approval alert with buttons
        approval_id = data["id"]
        direction_emoji = "🟢" if data["direction"] == "buy" else "🔴"
        text = (
            f"🔔 TRADE SIGNAL — {data['symbol']}\n\n"
            f"📊 Phân tích:\n{data.get('analysis', 'N/A')[:500]}\n\n"
            f"📈 Entry Plan:\n"
            f"• {direction_emoji} {data['order_type'].upper()} @ {data['price']}\n"
            f"• SL: {data['sl']} | TP1: {data['tp1']} | TP2: {data.get('tp2', '—')}\n"
            f"• Lot: {data['lot']} (risk {data.get('risk_pct', 0)}% ≈ ${data.get('risk_usd', 0):.0f})\n"
            f"• Confluence: {data.get('confluence_score', 0)}/100\n\n"
            f"⚖️ Risk Guard: ✅ PASS"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"trade_approve:{approval_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"trade_reject:{approval_id}"),
            ],
            [
                InlineKeyboardButton("📊 Chi tiết", callback_data=f"trade_detail:{approval_id}"),
            ],
        ])
        await self._app_tg.bot.send_message(
            chat_id=int(owner_id), text=text,
            reply_markup=keyboard, parse_mode=None,
        )
        return

    # Existing notification handling (non-approval)
    message = data if isinstance(data, str) else str(data)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Phân tích", callback_data="menu:analyze"),
            InlineKeyboardButton("📋 Positions", callback_data="menu:positions"),
        ],
    ])
    await self._app_tg.bot.send_message(
        chat_id=int(owner_id), text=message,
        reply_markup=keyboard, parse_mode=None,
    )
```

**Step 2: Wire approval_manager notify callback**

In `__init__` or `start()`, after wiring `self._trading_brain.on_notify(self._send_trading_notification)`, add:

```python
if self._trading_brain and self._trading_brain.approval_manager:
    self._trading_brain.approval_manager.on_notify(self._send_trading_notification)
```

**Step 3: Add trade approval callback handlers in `_handle_feedback()`**

Add to the callback handler (before existing `elif data.startswith("quick:")`):

```python
elif data.startswith("trade_approve:"):
    approval_id = data.split(":", 1)[1]
    result = await self._trading_brain.handle_approval_response(
        approval_id, "approve", via="telegram"
    )
    if result.get("ticket"):
        await query.edit_message_text(
            f"✅ Approved — Order placed #{result['ticket']}\n\n"
            f"(Original message truncated)",
        )
    elif result.get("error"):
        await query.edit_message_text(
            f"⚠️ Cannot approve: {result['error']}\n\n"
            f"(Original message truncated)",
        )
    return

elif data.startswith("trade_reject:"):
    approval_id = data.split(":", 1)[1]
    result = await self._trading_brain.handle_approval_response(
        approval_id, "reject", via="telegram"
    )
    await query.edit_message_text("❌ Trade rejected by Bi")
    return

elif data.startswith("trade_detail:"):
    approval_id = data.split(":", 1)[1]
    approval = self._trading_brain.approval_manager._db.get_approval(approval_id)
    if approval:
        detail = approval.get("analysis", "No analysis available")
        smc = approval.get("smc_summary", "")
        text = f"📊 Full Analysis:\n\n{detail}"
        if smc:
            text += f"\n\n🔍 SMC: {smc}"
        await query.answer()
        await self._app_tg.bot.send_message(
            chat_id=query.message.chat_id, text=text[:4000],
        )
    return
```

**Step 4: Run tests**

```bash
python -m pytest tests/unit/ -x -q 2>&1 | tail -5
```

**Step 5: Commit**

```bash
git add src/gateway/channels/telegram.py
git commit -m "feat: Telegram approval buttons — approve/reject/detail callbacks"
```

---

## Task 4: Dashboard WebSocket + API Endpoints

**Files:**
- Modify: `src/gateway/channels/web.py`

**Step 1: Add WebSocket `/ws/trading` endpoint**

In `create_app()`, after the existing `/ws/chat` WebSocket:

```python
# Track connected trading WS clients
trading_ws_clients: list = []

@fastapi_app.websocket("/ws/trading")
async def websocket_trading(websocket):
    await websocket.accept()
    trading_ws_clients.append(websocket)
    log.info("trading_ws_connected")
    try:
        while True:
            # Keep alive — client can send ping
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except Exception:
        pass
    finally:
        trading_ws_clients.remove(websocket)
        log.info("trading_ws_disconnected")

# Wire WS push to approval manager
async def _push_trading_ws(event: dict):
    dead = []
    for ws in trading_ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        trading_ws_clients.remove(ws)

if adapter._app and adapter._app.trading_brain:
    adapter._app.trading_brain.approval_manager.on_ws_event(_push_trading_ws)
```

**Step 2: Add approval REST endpoints**

```python
@fastapi_app.get("/api/trading/approvals")
async def api_trading_approvals():
    if not adapter._app or not adapter._app.trading_brain:
        return JSONResponse({"approvals": []})
    approvals = adapter._app.trading_brain.approval_manager.get_pending()
    return JSONResponse({"approvals": approvals})

@fastapi_app.post("/api/trading/approve/{approval_id}")
async def api_trading_approve(approval_id: str):
    if not adapter._app or not adapter._app.trading_brain:
        return JSONResponse({"error": "Trading brain unavailable"})
    result = await adapter._app.trading_brain.handle_approval_response(
        approval_id, "approve", via="dashboard"
    )
    return JSONResponse(result)

@fastapi_app.post("/api/trading/reject/{approval_id}")
async def api_trading_reject(approval_id: str, request: dict = {}):
    if not adapter._app or not adapter._app.trading_brain:
        return JSONResponse({"error": "Trading brain unavailable"})
    result = await adapter._app.trading_brain.handle_approval_response(
        approval_id, "reject", via="dashboard"
    )
    return JSONResponse(result)

@fastapi_app.get("/api/trading/candles")
async def api_trading_candles(symbol: str = "XAUUSD", timeframe: str = "H1", count: int = 100):
    try:
        from src.trading.mt5_client import MT5Client
        client = MT5Client()
        candles = await client.get_rates(symbol, timeframe, count)
        return JSONResponse({"candles": candles})
    except Exception as e:
        return JSONResponse({"candles": [], "error": str(e)})
```

**Step 3: Run tests**

```bash
python -m pytest tests/unit/ -x -q 2>&1 | tail -5
```

**Step 4: Commit**

```bash
git add src/gateway/channels/web.py
git commit -m "feat: WebSocket /ws/trading + approval/candles API endpoints"
```

---

## Task 5: Dashboard Trading Page Upgrade

**Files:**
- Modify: `dashboard/src/app/trading/page.tsx`
- Create: `dashboard/src/components/ApprovalCard.tsx`
- Modify: `dashboard/src/hooks/useWebSocket.ts`

**Step 1: Create ApprovalCard component**

Create `dashboard/src/components/ApprovalCard.tsx`:

```tsx
"use client";

interface Approval {
  id: string;
  symbol: string;
  direction: string;
  order_type: string;
  price: number;
  sl: number;
  tp1: number;
  tp2?: number;
  lot: number;
  risk_pct: number;
  risk_usd: number;
  confluence_score: number;
  analysis: string;
  smc_summary?: string;
  created_at: string;
}

interface Props {
  approval: Approval;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}

export default function ApprovalCard({ approval, onApprove, onReject }: Props) {
  const isBuy = approval.direction === "buy";
  const [showDetail, setShowDetail] = useState(false);

  return (
    <div className={`border rounded-xl p-4 ${
      isBuy ? "border-green-500/30 bg-green-500/5" : "border-red-500/30 bg-red-500/5"
    }`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={`text-lg font-bold ${isBuy ? "text-green-400" : "text-red-400"}`}>
            {approval.order_type.toUpperCase()}
          </span>
          <span className="text-gray-400">{approval.symbol}</span>
        </div>
        <span className="text-xs text-gray-500">
          Score: {approval.confluence_score}/100
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 text-sm mb-3">
        <div><span className="text-gray-500">Entry:</span> {approval.price}</div>
        <div><span className="text-gray-500">Lot:</span> {approval.lot}</div>
        <div className="text-red-400"><span className="text-gray-500">SL:</span> {approval.sl}</div>
        <div className="text-green-400"><span className="text-gray-500">TP1:</span> {approval.tp1}</div>
        <div><span className="text-gray-500">Risk:</span> {approval.risk_pct}% (${approval.risk_usd.toFixed(0)})</div>
        {approval.tp2 && <div className="text-green-400"><span className="text-gray-500">TP2:</span> {approval.tp2}</div>}
      </div>

      <p className="text-xs text-gray-400 mb-3 line-clamp-3">{approval.analysis}</p>

      <div className="flex gap-2">
        <button onClick={() => onApprove(approval.id)}
          className="flex-1 bg-green-600 hover:bg-green-700 rounded-lg py-2 text-sm font-medium">
          ✅ Approve
        </button>
        <button onClick={() => onReject(approval.id)}
          className="flex-1 bg-red-600 hover:bg-red-700 rounded-lg py-2 text-sm font-medium">
          ❌ Reject
        </button>
        <button onClick={() => setShowDetail(!showDetail)}
          className="bg-[#1a1a2e] border border-[#2a2a3e] rounded-lg px-3 py-2 text-sm">
          📊
        </button>
      </div>

      {showDetail && (
        <div className="mt-3 p-3 bg-[#0a0a0f] rounded-lg text-xs text-gray-400 whitespace-pre-wrap">
          {approval.analysis}
          {approval.smc_summary && `\n\nSMC: ${approval.smc_summary}`}
        </div>
      )}
    </div>
  );
}

import { useState } from "react";
```

**Step 2: Update Trading page with approvals + live chart**

Replace `dashboard/src/app/trading/page.tsx`:

```tsx
"use client";

import { useState, useEffect } from "react";
import useSWR, { mutate } from "swr";
import { fetcher } from "@/lib/api";
import { Play, Square, RefreshCw } from "lucide-react";
import TradingChart from "@/components/TradingChart";
import PositionsTable from "@/components/PositionsTable";
import ApprovalCard from "@/components/ApprovalCard";
import { useWebSocket } from "@/hooks/useWebSocket";

export default function TradingPage() {
  const { data: status } = useSWR("/api/trading/status", fetcher, { refreshInterval: 5000 });
  const { data: positions } = useSWR("/api/trading/positions", fetcher, { refreshInterval: 5000 });
  const { data: pnl } = useSWR("/api/trading/pnl", fetcher, { refreshInterval: 10000 });
  const { data: zones } = useSWR("/api/trading/zones", fetcher, { refreshInterval: 30000 });
  const { data: approvals, mutate: refreshApprovals } = useSWR("/api/trading/approvals", fetcher, { refreshInterval: 5000 });
  const { data: candleData } = useSWR("/api/trading/candles?symbol=XAUUSD&timeframe=H1&count=200", fetcher, { refreshInterval: 60000 });

  const { messages } = useWebSocket("/ws/trading");
  const [liveBid, setLiveBid] = useState<number | null>(null);

  // Handle WebSocket events
  useEffect(() => {
    if (messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (last.type === "tick") {
      setLiveBid(last.bid);
    } else if (last.type === "approval" || last.type === "approval_update") {
      refreshApprovals();
    }
  }, [messages, refreshApprovals]);

  const handleControl = async (action: string) => {
    await fetch("/api/trading/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  };

  const handleApprove = async (id: string) => {
    await fetch(`/api/trading/approve/${id}`, { method: "POST" });
    refreshApprovals();
  };

  const handleReject = async (id: string) => {
    await fetch(`/api/trading/reject/${id}`, { method: "POST" });
    refreshApprovals();
  };

  const running = status?.running ?? false;
  const pendingApprovals = approvals?.approvals ?? [];

  // Transform candle data for chart
  const chartData = (candleData?.candles ?? []).map((c: any) => ({
    time: c.time,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">Trading</h1>
          {liveBid && (
            <span className="text-lg font-mono text-yellow-400">{liveBid.toFixed(2)}</span>
          )}
        </div>
        <div className="flex gap-2">
          {!running ? (
            <button onClick={() => handleControl("start")}
              className="flex items-center gap-2 bg-green-600 hover:bg-green-700 px-4 py-2 rounded-lg text-sm">
              <Play size={14} /> Start Brain
            </button>
          ) : (
            <button onClick={() => handleControl("stop")}
              className="flex items-center gap-2 bg-red-600 hover:bg-red-700 px-4 py-2 rounded-lg text-sm">
              <Square size={14} /> Stop Brain
            </button>
          )}
          <button onClick={() => handleControl("plan")}
            className="flex items-center gap-2 bg-[#1a1a2e] border border-[#2a2a3e] hover:bg-[#2a2a3e] px-4 py-2 rounded-lg text-sm">
            <RefreshCw size={14} /> New Plan
          </button>
        </div>
      </div>

      {/* Pending Approvals */}
      {pendingApprovals.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-yellow-400">
            🔔 Pending Approvals ({pendingApprovals.length})
          </h2>
          {pendingApprovals.map((a: any) => (
            <ApprovalCard key={a.id} approval={a} onApprove={handleApprove} onReject={handleReject} />
          ))}
        </div>
      )}

      {/* Brain Status */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4">
        <div className="flex items-center gap-3 mb-3">
          <span className={`w-2.5 h-2.5 rounded-full ${running ? "bg-green-400 animate-pulse" : "bg-gray-600"}`} />
          <span className="font-medium">{running ? "Brain Active" : "Brain Stopped"}</span>
          {status?.session && <span className="text-gray-500 text-sm">Session: {status.session}</span>}
        </div>
        {status?.current_plan && (
          <div className="text-sm text-gray-400">
            <span className="text-gray-500">Bias:</span> {status.current_plan.bias} |
            <span className="text-gray-500 ml-2">Zones:</span> {status.current_plan.zones_count ?? 0} |
            <span className="text-gray-500 ml-2">Trades:</span> {status.current_plan.trades_taken ?? 0}/{status.current_plan.max_trades ?? 3}
          </div>
        )}
      </div>

      {/* PnL Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: "Daily PnL", value: pnl?.daily_pnl ?? 0, pct: pnl?.daily_pnl_pct ?? 0 },
          { label: "Weekly PnL", value: pnl?.weekly_pnl ?? 0, pct: pnl?.weekly_pnl_pct ?? 0 },
          { label: "Trades Today", value: pnl?.daily_trades ?? 0 },
          { label: "Loss Streak", value: pnl?.consecutive_losses ?? 0 },
        ].map(({ label, value, pct }) => (
          <div key={label} className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4">
            <div className="text-xs text-gray-500">{label}</div>
            <div className={`text-xl font-bold ${
              typeof pct === "number" ? (value >= 0 ? "text-green-400" : "text-red-400") : ""
            }`}>
              {typeof pct === "number" ? `$${Number(value).toFixed(2)}` : value}
            </div>
            {typeof pct === "number" && (
              <div className={`text-xs ${value >= 0 ? "text-green-600" : "text-red-600"}`}>
                {value >= 0 ? "+" : ""}{Number(pct).toFixed(2)}%
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Chart with real data */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4">
        <h2 className="text-lg font-semibold mb-3">XAUUSD Chart</h2>
        <TradingChart data={chartData} zones={zones?.zones ?? []} />
      </div>

      {/* Positions */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-3">Open Positions</h2>
        <PositionsTable positions={positions?.positions ?? []} />
      </div>
    </div>
  );
}
```

**Step 3: Build and verify**

```bash
cd ~/projects/jarvis/dashboard && npm run build 2>&1 | tail -15
```

**Step 4: Commit**

```bash
git add dashboard/src/
git commit -m "feat: dashboard approval cards, live chart data, WebSocket trading events"
```

---

## Task 6: Integration + Init in app.py

**Files:**
- Modify: `src/app.py`

**Step 1: Ensure approval_manager is accessible**

In `init_trading_brain()`, after brain is created, the ApprovalManager is already created inside TradingBrain.__init__ (from Task 2). Just verify access path:

```python
# In JarvisApp, after init_trading_brain():
# self.trading_brain.approval_manager is available
```

No code change needed if Task 2 wired correctly. Just verify.

**Step 2: Run full test suite**

```bash
python -m pytest tests/unit/ -x -q 2>&1 | tail -5
```

**Step 3: Run full stack test**

```bash
# Kill existing JARVIS
pkill -f "python -m src.main" || true
sleep 2

# Restart
cd ~/projects/jarvis && nohup python -m src.main > /tmp/jarvis.log 2>&1 &
sleep 30

# Check logs
grep -E "approval|web_server|jarvis_ready" /tmp/jarvis.log | tail -10

# Check ports
ss -tlnp | grep -E "3000|8000"

# Test approval endpoint
curl -s http://localhost:8000/api/trading/approvals | python3 -m json.tool
```

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: Trading Live Mode complete — semi-auto with approval gate"
```

---

## Execution Order

```
Task 0 (Persistence table)      — no deps
Task 1 (ApprovalManager)        — after T0
Task 2 (Wire into Brain)        — after T1
Task 3 (Telegram handlers)      — after T2
Task 4 (Dashboard WebSocket+API) — after T2
Task 5 (Dashboard UI upgrade)   — after T4
Task 6 (Integration test)       — after all
```

**Parallelizable:** T3 + T4 (independent, both depend on T2). T5 after T4.
