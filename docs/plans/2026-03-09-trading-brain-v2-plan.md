# Trading Brain v2 — Smart Execution Implementation Plan

> **For Claude:** Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Replace reactive market-order execution (8-20s latency) with proactive pending limit/stop orders placed at confluence zones (0s fill latency), plus dynamic lot sizing, order modification, state persistence, and position recovery.

**Architecture:** Bridge gets 4 new endpoints (pending/modify/cancel/list). New `PendingOrderManager` places limit orders at plan time. New `TradingPersistence` saves plans/positions/orders to SQLite. On startup, `TradingBrain` recovers state from DB + MT5 and resumes management.

**Tech Stack:** Python 3.13 async, MetaTrader5 (Windows bridge), FastAPI, httpx, sqlite3, pytest + pytest-asyncio.

**Design doc:** `docs/plans/2026-03-09-trading-brain-v2-design.md`

**Existing patterns to follow:**
- Bridge endpoints: `scripts/mt5_bridge.py` — FastAPI, Pydantic models, `mt5.order_send()` with request dict
- Client methods: `src/trading/mt5_client.py` — async httpx, `_get_client()`, `_resolve_symbol()`, 10s timeout
- SQLite persistence: `src/memory/store.py:get_connection()` → `sqlite3.Connection` to `data/jarvis.db`
- Table init pattern: `src/trading/journal.py:_init_journal_tables(conn)` — `conn.executescript(CREATE TABLE IF NOT EXISTS ...)`
- Position sizing: `src/trading/risk.py:calculate_position_size(balance, risk_pct, sl_distance, symbol)`
- Risk guard: `src/trading/risk_guard.py:RiskGuard.check_entry()` → `VetoResult`
- TradingBrain: `src/trading/trading_brain.py` — wires sub-modules, callbacks, async loops
- Tool definitions: `src/tools/base.py:ToolDefinition` with `ToolParameter`, handler function
- Tests: `tests/unit/`, pytest-asyncio, AsyncMock, monkeypatch

---

## Task 1: MT5 Bridge v2 — Pending Order Endpoints

**Files:**
- Modify: `scripts/mt5_bridge.py`

**What to implement:**

Add 2 Pydantic models and 4 new endpoints to the existing bridge:

```python
# After existing CloseRequest model (~line 55):

class PendingOrderRequest(BaseModel):
    symbol: str
    order_type: str  # "buy_limit", "sell_limit", "buy_stop", "sell_stop"
    volume: float
    price: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: str = "JARVIS"

class ModifyRequest(BaseModel):
    sl: Optional[float] = None
    tp: Optional[float] = None
    price: Optional[float] = None  # only for pending orders

# Order type mapping
_ORDER_TYPE_MAP = {
    "buy_limit": mt5.ORDER_TYPE_BUY_LIMIT,
    "sell_limit": mt5.ORDER_TYPE_SELL_LIMIT,
    "buy_stop": mt5.ORDER_TYPE_BUY_STOP,
    "sell_stop": mt5.ORDER_TYPE_SELL_STOP,
}
```

**Endpoint 1: Place pending order**
```python
@app.post("/order/pending")
async def place_pending_order(req: PendingOrderRequest):
    """Place a pending (limit/stop) order."""
    if req.volume > 5.0:
        raise HTTPException(400, f"Volume {req.volume} exceeds maximum 5.0")

    otype = _ORDER_TYPE_MAP.get(req.order_type.lower())
    if otype is None:
        raise HTTPException(400, f"Invalid order_type: {req.order_type}. Valid: {list(_ORDER_TYPE_MAP.keys())}")

    sym_info = mt5.symbol_info(req.symbol)
    if sym_info is None:
        raise HTTPException(404, f"Symbol {req.symbol} not found")
    if not sym_info.visible:
        mt5.symbol_select(req.symbol, True)

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": req.symbol,
        "volume": req.volume,
        "type": otype,
        "price": req.price,
        "deviation": 20,
        "magic": 234000,
        "comment": req.comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,  # RETURN for pending orders
    }
    if req.sl is not None:
        request["sl"] = req.sl
    if req.tp is not None:
        request["tp"] = req.tp

    result = mt5.order_send(request)
    if result is None:
        raise HTTPException(500, f"order_send failed: {mt5.last_error()}")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(400, f"Order rejected: retcode={result.retcode}, comment={result.comment}")

    return {
        "retcode": result.retcode,
        "order": result.order,
        "volume": result.volume,
        "price": req.price,
        "comment": result.comment,
    }
```

**Endpoint 2: Modify order/position**
```python
@app.put("/order/{ticket}")
async def modify_order(ticket: int, req: ModifyRequest):
    """Modify SL/TP/price on pending order or open position."""
    # Check if it's a pending order
    orders = mt5.orders_get(ticket=ticket)
    if orders:
        # Pending order — can modify sl, tp, price
        order = orders[0]
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "symbol": order.symbol,
            "volume": order.volume_current,
            "type": order.type,
            "price": req.price if req.price is not None else order.price_open,
            "sl": req.sl if req.sl is not None else order.sl,
            "tp": req.tp if req.tp is not None else order.tp,
            "type_time": mt5.ORDER_TIME_GTC,
        }
    else:
        # Check if it's an open position
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            raise HTTPException(404, f"No order or position with ticket {ticket}")
        pos = positions[0]
        if req.price is not None:
            raise HTTPException(400, "Cannot modify entry price of an open position")
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": req.sl if req.sl is not None else pos.sl,
            "tp": req.tp if req.tp is not None else pos.tp,
        }

    result = mt5.order_send(request)
    if result is None:
        raise HTTPException(500, f"modify failed: {mt5.last_error()}")

    return {
        "retcode": result.retcode,
        "comment": result.comment,
    }
```

**Endpoint 3: Cancel pending order**
```python
@app.delete("/order/{ticket}")
async def cancel_order(ticket: int):
    """Cancel a pending order."""
    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        raise HTTPException(404, f"Pending order {ticket} not found")

    request = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
    }

    result = mt5.order_send(request)
    if result is None:
        raise HTTPException(500, f"cancel failed: {mt5.last_error()}")

    return {
        "retcode": result.retcode,
        "comment": result.comment,
    }
```

**Endpoint 4: List pending orders**
```python
def _order_to_dict(order) -> dict:
    """Convert MT5 order named tuple to dict."""
    return {
        "ticket": order.ticket,
        "symbol": order.symbol,
        "type": order.type,
        "volume_current": order.volume_current,
        "price_open": order.price_open,
        "sl": order.sl,
        "tp": order.tp,
        "time_setup": int(order.time_setup),
        "magic": order.magic,
        "comment": order.comment,
    }

@app.get("/orders/pending")
async def get_pending_orders(symbol: Optional[str] = None):
    """List pending orders."""
    if symbol:
        orders = mt5.orders_get(symbol=symbol)
    else:
        orders = mt5.orders_get()
    if orders is None:
        return []
    return [_order_to_dict(o) for o in orders]
```

**Tests:** Manual test via curl after deploying bridge on Windows. No pytest for bridge (requires Windows + MT5).

---

## Task 2: MT5 Client Extensions

**Files:**
- Modify: `src/trading/mt5_client.py`
- Create: `tests/unit/test_mt5_client_v2.py`

**Add 4 new methods** to `MT5Client` class (after existing `get_history` method):

```python
async def place_pending(
    self,
    symbol: str,
    order_type: str,
    volume: float,
    price: float,
    sl: float | None = None,
    tp: float | None = None,
    comment: str = "JARVIS",
) -> dict[str, Any]:
    """Place a pending (limit/stop) order."""
    symbol = self._resolve_symbol(symbol)
    client = await self._get_client()
    payload: dict[str, Any] = {
        "symbol": symbol,
        "order_type": order_type,
        "volume": volume,
        "price": price,
        "comment": comment,
    }
    if sl is not None:
        payload["sl"] = sl
    if tp is not None:
        payload["tp"] = tp
    resp = await client.post("/order/pending", json=payload)
    resp.raise_for_status()
    return resp.json()

async def modify_order(
    self,
    ticket: int,
    sl: float | None = None,
    tp: float | None = None,
    price: float | None = None,
) -> dict[str, Any]:
    """Modify SL/TP/price on pending order or open position."""
    client = await self._get_client()
    payload: dict[str, Any] = {}
    if sl is not None:
        payload["sl"] = sl
    if tp is not None:
        payload["tp"] = tp
    if price is not None:
        payload["price"] = price
    resp = await client.put(f"/order/{ticket}", json=payload)
    resp.raise_for_status()
    return resp.json()

async def cancel_order(self, ticket: int) -> dict[str, Any]:
    """Cancel a pending order."""
    client = await self._get_client()
    resp = await client.delete(f"/order/{ticket}")
    resp.raise_for_status()
    return resp.json()

async def get_pending_orders(
    self, symbol: str | None = None
) -> list[dict[str, Any]]:
    """List pending orders."""
    client = await self._get_client()
    params = {"symbol": symbol} if symbol else {}
    resp = await client.get("/orders/pending", params=params)
    resp.raise_for_status()
    return resp.json()
```

**Tests (~8):**

```python
# tests/unit/test_mt5_client_v2.py

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.trading.mt5_client import MT5Client

@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("TRADING_SYMBOL", raising=False)
    return MT5Client(base_url="http://test:8710")

class TestPlacePending:
    async def test_place_buy_limit(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"retcode": 10009, "order": 12345}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client, '_get_client') as mock_get:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_resp
            mock_get.return_value = mock_http
            result = await client.place_pending("XAUUSD", "buy_limit", 0.01, 2640.0, sl=2625.0, tp=2670.0)
            assert result["retcode"] == 10009
            mock_http.post.assert_called_once()
            call_args = mock_http.post.call_args
            assert call_args[1]["json"]["order_type"] == "buy_limit"

    async def test_place_sell_limit(self, client): ...
    async def test_symbol_resolved(self, client, monkeypatch): ...

class TestModifyOrder:
    async def test_modify_sl_tp(self, client): ...
    async def test_modify_price(self, client): ...

class TestCancelOrder:
    async def test_cancel_success(self, client): ...

class TestGetPendingOrders:
    async def test_list_all(self, client): ...
    async def test_list_by_symbol(self, client): ...
```

---

## Task 3: Dynamic Lot Sizing

**Files:**
- Modify: `src/trading/risk.py`
- Modify: `tests/unit/test_trading.py` (add new test class)

**Add function** after existing `calculate_position_size` (~line 119):

```python
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

    Examples:
        $1000 equity, 1% risk, entry=2650, SL=2635 → lot=0.01
        $10000 equity, 1% risk, entry=2650, SL=2640 → lot=0.10
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
```

**Tests (~6):**

```python
class TestCalculateLotFromRisk:
    def test_small_account(self):
        # $1000, 1% risk, 15 pip SL → lot = $10 / (1500 * 1.0) = 0.007 → 0.01
        lot = calculate_lot_from_risk(1000.0, 1.0, 2650.0, 2635.0)
        assert lot == 0.01

    def test_large_account(self):
        # $10000, 1% risk, 10 pip SL → lot = $100 / (1000 * 1.0) = 0.10
        lot = calculate_lot_from_risk(10000.0, 1.0, 2650.0, 2640.0)
        assert lot == 0.10

    def test_capped_at_max(self):
        # Very large account → capped at 0.10
        lot = calculate_lot_from_risk(100000.0, 1.0, 2650.0, 2649.0)
        assert lot == 0.10

    def test_zero_equity(self):
        lot = calculate_lot_from_risk(0, 1.0, 2650.0, 2635.0)
        assert lot == 0.01

    def test_same_entry_sl(self):
        lot = calculate_lot_from_risk(1000.0, 1.0, 2650.0, 2650.0)
        assert lot == 0.01

    def test_custom_max_lot(self):
        lot = calculate_lot_from_risk(10000.0, 1.0, 2650.0, 2640.0, max_lot=0.05)
        assert lot == 0.05
```

---

## Task 4: Trading Persistence Module

**Files:**
- Create: `src/trading/persistence.py`
- Create: `tests/unit/test_trading_persistence.py`

```python
"""TradingPersistence — SQLite storage for trade plans, positions, and pending orders.

Uses same DB as journal (data/jarvis.db) via src/memory/store.get_connection().
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("trading.persistence")

_tables_initialized = False


def _init_trading_tables(conn: sqlite3.Connection) -> None:
    """Create persistence tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session TEXT NOT NULL,
            created_at TEXT NOT NULL,
            bias TEXT DEFAULT 'neutral',
            bias_reasoning TEXT DEFAULT '',
            market_regime TEXT DEFAULT '',
            key_levels TEXT DEFAULT '{}',
            alert_zones TEXT DEFAULT '[]',
            scenarios TEXT DEFAULT '[]',
            invalidation TEXT DEFAULT '',
            risk_budget_pct REAL DEFAULT 2.0,
            max_trades INTEGER DEFAULT 3,
            trades_taken INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS managed_positions (
            ticket INTEGER PRIMARY KEY,
            zone_id TEXT DEFAULT '',
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            volume REAL NOT NULL,
            entry_price REAL NOT NULL,
            sl REAL DEFAULT 0,
            tp1 REAL DEFAULT 0,
            tp2 REAL DEFAULT 0,
            original_sl REAL DEFAULT 0,
            be_moved INTEGER DEFAULT 0,
            tp1_hit INTEGER DEFAULT 0,
            remaining_volume REAL DEFAULT 0,
            trail_sl REAL DEFAULT 0,
            confluence_score INTEGER DEFAULT 0,
            opened_at TEXT,
            closed_at TEXT,
            close_reason TEXT,
            pnl REAL
        );

        CREATE TABLE IF NOT EXISTS pending_orders (
            ticket INTEGER PRIMARY KEY,
            zone_id TEXT DEFAULT '',
            symbol TEXT NOT NULL,
            order_type TEXT NOT NULL,
            volume REAL NOT NULL,
            price REAL NOT NULL,
            sl REAL DEFAULT 0,
            tp REAL DEFAULT 0,
            comment TEXT DEFAULT '',
            placed_at TEXT,
            status TEXT DEFAULT 'pending'
        );
    """)


def _ensure_tables() -> sqlite3.Connection:
    """Get DB connection and ensure tables exist."""
    global _tables_initialized
    conn = get_connection()
    if not _tables_initialized:
        _init_trading_tables(conn)
        _tables_initialized = True
    return conn


class TradingPersistence:
    """SQLite persistence for trading state."""

    # ── Trade Plans ──

    def save_plan(self, plan) -> int:
        """Save TradePlan, return row id. Deactivates previous active plans."""
        conn = _ensure_tables()
        # Deactivate old plans
        conn.execute("UPDATE trade_plans SET active = 0 WHERE active = 1")
        cursor = conn.execute(
            """INSERT INTO trade_plans
            (session, created_at, bias, bias_reasoning, market_regime,
             key_levels, alert_zones, scenarios, invalidation,
             risk_budget_pct, max_trades, trades_taken, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                plan.session,
                plan.created_at.isoformat(),
                plan.bias,
                plan.bias_reasoning,
                plan.market_regime,
                json.dumps(plan.key_levels, default=str),
                json.dumps(plan.alert_zones, default=str),
                json.dumps([
                    {"condition": s.condition, "action": s.action, "new_bias": s.new_bias}
                    for s in plan.scenarios
                ]),
                plan.invalidation,
                plan.risk_budget_pct,
                plan.max_trades,
                plan.trades_taken,
            ),
        )
        conn.commit()
        plan_id = cursor.lastrowid
        log.info("plan_saved", plan_id=plan_id, session=plan.session)
        return plan_id

    def load_active_plan(self) -> dict | None:
        """Load latest active plan as dict. Returns None if no active plan."""
        conn = _ensure_tables()
        row = conn.execute(
            "SELECT * FROM trade_plans WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "session": row["session"],
            "created_at": row["created_at"],
            "bias": row["bias"],
            "bias_reasoning": row["bias_reasoning"],
            "market_regime": row["market_regime"],
            "key_levels": json.loads(row["key_levels"] or "{}"),
            "alert_zones": json.loads(row["alert_zones"] or "[]"),
            "scenarios": json.loads(row["scenarios"] or "[]"),
            "invalidation": row["invalidation"],
            "risk_budget_pct": row["risk_budget_pct"],
            "max_trades": row["max_trades"],
            "trades_taken": row["trades_taken"],
        }

    def deactivate_plan(self, plan_id: int) -> None:
        conn = _ensure_tables()
        conn.execute("UPDATE trade_plans SET active = 0 WHERE id = ?", (plan_id,))
        conn.commit()

    def update_plan_trades(self, plan_id: int, trades_taken: int) -> None:
        conn = _ensure_tables()
        conn.execute(
            "UPDATE trade_plans SET trades_taken = ? WHERE id = ?",
            (trades_taken, plan_id),
        )
        conn.commit()

    # ── Managed Positions ──

    def save_position(self, pos) -> None:
        """Save or update a ManagedPosition."""
        conn = _ensure_tables()
        conn.execute(
            """INSERT OR REPLACE INTO managed_positions
            (ticket, zone_id, symbol, direction, volume, entry_price,
             sl, tp1, tp2, original_sl, be_moved, tp1_hit,
             remaining_volume, trail_sl, confluence_score, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pos.ticket, pos.zone_id, pos.symbol, pos.direction,
                pos.volume, pos.entry_price, pos.sl, pos.tp1, pos.tp2,
                pos.original_sl, int(pos.be_moved), int(pos.tp1_hit),
                pos.remaining_volume, pos.trail_sl, pos.confluence_score,
                pos.opened_at.isoformat() if hasattr(pos.opened_at, 'isoformat') else str(pos.opened_at),
            ),
        )
        conn.commit()

    def update_position(self, ticket: int, **kwargs) -> None:
        """Update specific fields on a position."""
        conn = _ensure_tables()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [ticket]
        conn.execute(f"UPDATE managed_positions SET {sets} WHERE ticket = ?", values)
        conn.commit()

    def close_position_record(self, ticket: int, reason: str, pnl: float) -> None:
        conn = _ensure_tables()
        conn.execute(
            "UPDATE managed_positions SET closed_at = ?, close_reason = ?, pnl = ? WHERE ticket = ?",
            (datetime.now(timezone.utc).isoformat(), reason, pnl, ticket),
        )
        conn.commit()

    def load_open_positions(self) -> list[dict]:
        """Load positions that haven't been closed."""
        conn = _ensure_tables()
        rows = conn.execute(
            "SELECT * FROM managed_positions WHERE closed_at IS NULL"
        ).fetchall()
        return [dict(row) for row in rows]

    # ── Pending Orders ──

    def save_pending(self, ticket: int, zone_id: str, symbol: str,
                     order_type: str, volume: float, price: float,
                     sl: float = 0, tp: float = 0, comment: str = "") -> None:
        conn = _ensure_tables()
        conn.execute(
            """INSERT OR REPLACE INTO pending_orders
            (ticket, zone_id, symbol, order_type, volume, price, sl, tp, comment, placed_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (ticket, zone_id, symbol, order_type, volume, price, sl, tp, comment,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    def update_pending_status(self, ticket: int, status: str) -> None:
        conn = _ensure_tables()
        conn.execute(
            "UPDATE pending_orders SET status = ? WHERE ticket = ?", (status, ticket)
        )
        conn.commit()

    def load_pending_orders(self) -> list[dict]:
        conn = _ensure_tables()
        rows = conn.execute(
            "SELECT * FROM pending_orders WHERE status = 'pending'"
        ).fetchall()
        return [dict(row) for row in rows]

    def cancel_all_pending(self) -> int:
        """Mark all pending orders as cancelled. Returns count."""
        conn = _ensure_tables()
        cursor = conn.execute(
            "UPDATE pending_orders SET status = 'cancelled' WHERE status = 'pending'"
        )
        conn.commit()
        return cursor.rowcount

    # ── Recovery ──

    def get_recovery_state(self) -> dict:
        """Return full state for recovery: plan + positions + pending."""
        return {
            "plan": self.load_active_plan(),
            "positions": self.load_open_positions(),
            "pending_orders": self.load_pending_orders(),
        }
```

**Tests (~16):**

| Class | Tests |
|-------|-------|
| TestSavePlan | save_and_load, deactivates_previous, no_active_returns_none |
| TestUpdatePlan | update_trades_taken, deactivate |
| TestPositions | save_and_load, update_fields, close_record, load_only_open |
| TestPending | save_and_load, update_status, cancel_all, load_only_pending |
| TestRecovery | full_recovery_state, empty_recovery |
| TestTableInit | tables_created_once |

Use `tmp_path` fixture with a temp SQLite DB (monkeypatch `get_connection`).

---

## Task 5: PendingOrderManager

**Files:**
- Create: `src/trading/pending_orders.py`
- Create: `tests/unit/test_pending_orders.py`

```python
"""PendingOrderManager — Manage pending limit/stop order lifecycle.

Places limit orders at confluence zones after LLM pre-approval,
tracks fills, handles cancellation on invalidation.
"""

from __future__ import annotations

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
        self._symbol = __import__("os").environ.get("TRADING_SYMBOL", "XAUUSD")

    def on_notify(self, callback: NotifyCallback) -> None:
        self._notify_cb = callback

    async def place_zone_orders(
        self,
        zones: list[dict],
        equity: float,
        risk_pct: float = 1.0,
    ) -> list[int]:
        """For each zone: calculate lot → determine order type → place pending.

        Order type logic:
        - BUY zone with price below current → buy_limit
        - BUY zone with price above current → buy_stop
        - SELL zone with price above current → sell_limit
        - SELL zone with price below current → sell_stop

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
            lot = calculate_lot_from_risk(
                equity, risk_pct, zone_mid, sl_price,
                max_lot=getattr(self._risk_guard, 'config', None)
                and self._risk_guard.config.max_lot_size or 0.10,
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
            # Remove from zone_tickets
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
        If a local order is NOT in MT5 pending → it was filled (or expired).
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
                # Order no longer pending → filled or cancelled
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
```

**Tests (~14):**

| Class | Tests |
|-------|-------|
| TestPlaceZoneOrders | place_buy_limit, place_sell_limit, buy_stop_above_price, dynamic_lot_sizing, skip_bad_zone, riskguard_lot_cap |
| TestCancelZone | cancel_by_zone_id, cancel_nonexistent |
| TestCheckFills | detect_filled_order, no_fills, multiple_fills |
| TestCancelAll | cancel_all_orders |
| TestSync | sync_from_mt5, ignore_non_jarvis_orders |

Mock MT5 client with AsyncMock. Mock persistence with MagicMock.

---

## Task 6: Position Recovery

**Files:**
- Modify: `src/trading/trading_brain.py`

**Add recovery method and modify `start()` and `__init__`:**

```python
# In __init__, add:
from src.trading.pending_orders import PendingOrderManager
from src.trading.persistence import TradingPersistence

# New params in __init__:
self.persistence = TradingPersistence()
self.pending_manager = PendingOrderManager(mt5_client, risk_guard, self.persistence)
self.pending_manager.on_notify(self._notify)
self._plan_id: int | None = None  # DB row id

# In start(), add recovery before starting loops:
async def start(self) -> None:
    if self._running:
        return
    self._running = True

    # Recover state first
    await self._recover_state()

    await self.position_manager.start()
    self._scheduler_task = asyncio.create_task(self._session_scheduler())
    log.info("trading_brain_started")

# New method:
async def _recover_state(self) -> None:
    """On startup: load plan + positions from DB + sync with MT5."""
    state = self.persistence.get_recovery_state()

    # 1. Recover plan
    plan_dict = state.get("plan")
    if plan_dict:
        from src.trading.trade_planner import TradePlan, Scenario
        scenarios = [
            Scenario(
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
                # Still open in MT5 — resume management
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
                # Closed while we were offline
                self.persistence.close_position_record(ticket, "closed_offline", 0)
                log.info("position_closed_offline", ticket=ticket)

    # 3. Recover/sync pending orders
    await self.pending_manager.sync_from_mt5()
    synced = self.pending_manager.order_count
    if synced:
        log.info("pending_orders_synced", count=synced)
```

**Also modify `plan_now()` to save plan and place pending orders:**

```python
async def plan_now(self, session: str = "") -> str:
    if not session:
        session = self._detect_session()

    plan = await self.planner.create_plan(session)
    self.current_plan = plan

    # Save plan to DB
    self._plan_id = self.persistence.save_plan(plan)

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
        if self._risk_guard and hasattr(self._risk_guard, 'config'):
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

    return formatted
```

**Modify `_on_zone_invalidate` to cancel pending:**
```python
async def _on_zone_invalidate(self, zone: dict, reason: str) -> None:
    zone_id = zone.get("zone_id", "unknown")
    # Cancel pending order for this zone
    cancelled = await self.pending_manager.cancel_zone(zone_id)
    cancel_note = " (pending order cancelled)" if cancelled else ""
    msg = f"Zone {zone_id} invalidated: {reason}{cancel_note}"
    await self._notify(msg)
    log.info("zone_invalidated", zone_id=zone_id, reason=reason, cancelled=cancelled)
```

**Modify `kill()` to cancel all pending:**
```python
async def kill(self) -> str:
    count = await self.position_manager.emergency_close_all("kill_switch")
    pending_cancelled = await self.pending_manager.cancel_all()
    await self.monitor.stop()
    summary = (
        f"KILL SWITCH: {count} position(s) closed, "
        f"{pending_cancelled} pending order(s) cancelled, monitor stopped."
    )
    await self._notify(summary)
    return summary
```

**Modify `get_status()` to include pending orders:**
```python
def get_status(self) -> dict:
    # ... existing code ...
    return {
        "running": self._running,
        "plan": plan_summary,
        "active_zones": len(self.monitor.active_zones),
        "active_positions": self.position_manager.position_count,
        "pending_orders": self.pending_manager.order_count,
        "risk": risk_status,
        "trades_taken": trades_taken,
    }
```

**Add fill checker in session scheduler:**
```python
# In _session_scheduler loop, add fill checking every 60s:
# Check for filled pending orders
filled = await self.pending_manager.check_fills()
for order_info in filled:
    await self._on_pending_filled(order_info)

# New method:
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
    self.persistence.save_position(managed)

    if self.current_plan:
        self.current_plan.trades_taken += 1
        if self._plan_id:
            self.persistence.update_plan_trades(self._plan_id, self.current_plan.trades_taken)

    msg = (
        f"FILLED: {direction.upper()} {self._symbol} @ {price}\n"
        f"Ticket: {ticket} | Vol: {volume}\n"
        f"SL: {sl} | TP1: {tp1} | TP2: {tp2}\n"
        f"Zone: {zone_id}"
    )
    await self._notify(msg)
    log.info("pending_filled_managed", ticket=ticket, zone_id=zone_id)
```

**Tests:** Update `tests/unit/test_trading_brain.py` to cover recovery + pending orders.

---

## Task 7: App + Telegram + CLI Integration

**Files:**
- Modify: `src/app.py` — update `init_trading_brain()` to pass persistence
- Modify: `src/tools/trading_advanced.py` — add `trade_pending` tool
- Modify: `src/gateway/channels/telegram.py` — update `/trade` status display
- Modify: `src/gateway/channels/cli.py` — update `/trade` status display

**App changes:**
```python
def init_trading_brain(self) -> None:
    from src.trading.trading_brain import TradingBrain
    from src.trading.risk_guard import RiskGuard
    from src.trading.mt5_client import MT5Client
    from src.trading.journal import TradeJournal
    client = MT5Client()
    risk_guard = RiskGuard()
    journal = TradeJournal()
    self.trading_brain = TradingBrain(client, risk_guard, journal)
    # Persistence and PendingOrderManager are created inside TradingBrain now
```

**New tool: `trade_pending`**
```python
async def trade_pending(action: str = "list") -> ToolResult:
    """View or cancel pending orders. Actions: list, cancel_all."""
```

**Telegram `/trade` status update:**
Add pending orders count to status display.

---

## Execution Order

```
Task 1 (bridge endpoints)      ── Windows Python, manual test
Task 2 (client extensions)     ── after Task 1
Task 3 (dynamic lot sizing)    ── independent, can parallel with T1/T2
Task 4 (persistence module)    ── independent, can parallel with T1/T2/T3
Task 5 (pending orders)        ── after T2 + T3 + T4
Task 6 (recovery + brain v2)   ── after T5
Task 7 (integration)           ── after T6
```

```
Task 1 (bridge)    ┐
Task 3 (lot size)  ├── parallel (independent)
Task 4 (persist)   ┘
Task 2 (client)    ── after T1
Task 5 (pending)   ── after T2 + T3 + T4
Task 6 (brain v2)  ── after T5
Task 7 (integrate) ── after T6
```

---

## Verification

1. `pytest tests/unit/test_mt5_client_v2.py tests/unit/test_trading.py -v` — client + lot sizing tests
2. `pytest tests/unit/test_trading_persistence.py -v` — persistence tests
3. `pytest tests/unit/test_pending_orders.py -v` — pending order tests
4. `pytest tests/unit/test_trading_brain.py -v` — brain v2 tests (recovery + pending)
5. `pytest tests/unit/ -x` → full suite green, no regressions
6. Manual: deploy bridge on Windows → test `/order/pending`, `/order/{ticket}` PUT/DELETE, `/orders/pending`
7. Manual: `/trade plan` on Telegram → see pending orders placed at zones
