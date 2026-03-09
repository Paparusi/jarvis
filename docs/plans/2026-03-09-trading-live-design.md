# Trading Live Mode — Semi-Auto with Approval Gate

> Date: 2026-03-09
> Status: Approved

## Problem

Trading Brain can analyze, plan, and place orders — but currently executes directly without human confirmation. For real money trading, Bi needs to approve every entry before execution.

## Goals

- Semi-auto: Brain plans + confirms entry, Bi approves before order placement
- Approve/reject from both Telegram and Dashboard
- Rich approval alerts showing full JARVIS analysis (bias, SMC, confluence, risk)
- Live data on Dashboard (price ticks, chart candles, position updates)
- Safe for real account with small lots (0.01-0.05)

## Architecture

**Approval Gate Pattern:** Insert an approval step between EntryConfirmer and order placement. When Brain decides ENTER, create a PendingApproval record, send alerts to Telegram + Dashboard, wait for Bi's response.

```
Current:  EntryConfirmer → ENTER → place_order()
New:      EntryConfirmer → ENTER → RiskGuard pre-check
            → ApprovalManager.create()
            → Notify Telegram (inline buttons)
            → Notify Dashboard (WebSocket)
            → Bi approves → RiskGuard re-check → place_order()
            → Bi rejects → log + skip
```

## Components

### 1. ApprovalManager (`src/trading/approval_manager.py`) — NEW

Orchestrates the approval lifecycle.

```python
class ApprovalManager:
    create_approval(entry_decision, zone, plan) -> PendingApproval
    approve(id, via="telegram"|"dashboard") -> OrderResult | Error
    reject(id, via, reason="") -> None
    get_pending() -> list[PendingApproval]
    cancel_stale() -> None  # cancel if zone invalidated
    cancel_all() -> None    # on brain stop / new plan
```

**PendingApproval record:**
```python
{
    "id": "apr_<uuid>",
    "plan_id": int,
    "zone_id": str,
    "symbol": str,
    "direction": "buy" | "sell",
    "order_type": "buy_limit" | "sell_stop" | ...,
    "price": float,
    "sl": float,
    "tp1": float,
    "tp2": float,
    "lot": float,
    "risk_pct": float,
    "risk_usd": float,
    "confluence_score": float,
    "analysis": str,        # Full LLM reasoning text
    "smc_summary": str,     # SMC patterns found
    "status": "pending" | "approved" | "rejected" | "expired" | "cancelled",
    "created_at": datetime,
    "responded_at": datetime | None,
    "responded_via": "telegram" | "dashboard" | None,
}
```

### 2. Trading Brain Changes (`trading_brain.py`) — MODIFY

In the zone alert handler, replace direct order placement:
- Before: `EntryConfirmer → ENTER → self._place_order()`
- After: `EntryConfirmer → ENTER → self._approval_manager.create_approval()`

On brain stop / new plan: `self._approval_manager.cancel_all()`

### 3. Persistence (`persistence.py`) — MODIFY

Add `trade_approvals` table:
```sql
trade_approvals (
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
```

### 4. Telegram Integration (`telegram.py`) — MODIFY

**Approval alert message:**
```
🔔 TRADE SIGNAL — XAUUSD

📊 Phân tích:
• Bias: BULLISH (H4 uptrend, H1 pullback to OB)
• Zone: 2,340.50 - 2,341.20 (confluence: 85/100)
• SMC: Bullish OB + FVG overlap, BOS confirmed on M15
• Session: London, volume above average

📈 Entry Plan:
• Type: BUY LIMIT @ 2,340.80
• SL: 2,338.50 (-230 pips)
• TP1: 2,344.00 (R:R 1.4)
• TP2: 2,347.50 (R:R 2.9)
• Lot: 0.03 (risk 0.8% = $24)

⚖️ Risk Guard: ✅ PASS

[✅ Approve] [❌ Reject] [📊 Chi tiết]
```

Callback handlers: `approve:{id}`, `reject:{id}`, `detail:{id}`
- Approve → `approval_manager.approve(id, "telegram")` → edit message "✅ Approved — Order #12345"
- Reject → `approval_manager.reject(id, "telegram")` → edit message "❌ Rejected"
- Detail → send full LLM analysis text

### 5. Dashboard WebSocket (`/ws/trading`) — NEW

Real-time event push:
```json
{"type": "approval", "data": {...}}           // new trade signal
{"type": "approval_update", "id": "...", "status": "approved"}
{"type": "tick", "bid": 2340.50, "ask": 2340.80}
{"type": "position_update", "positions": [...]}
```

### 6. Dashboard API Endpoints — NEW

```
GET  /api/trading/approvals       — list pending approvals
POST /api/trading/approve/{id}    — approve a trade
POST /api/trading/reject/{id}     — reject (body: {reason})
GET  /api/trading/candles         — OHLCV data from MT5
```

### 7. Dashboard Trading Page — MODIFY

- Approval card with Approve/Reject buttons
- Live price ticker
- Chart with real candle data from MT5
- WebSocket connection for real-time updates

## Safety

### Re-validation on Approve
- RiskGuard re-checks at approve time (conditions may have changed)
- Price distance check: reject if price moved >50% of zone width
- All 13 circuit breaker rules must pass again

### Stale Cleanup
- Zone invalidation → auto-cancel pending approval
- Brain stop / new plan → cancel all pending approvals
- No timeout default (price validation handles staleness)

### Recovery
- Pending approvals persist in DB, survive restart
- Re-send notifications on recovery
- Sync with MT5 for any filled orders

### Lot Constraints
- Max lot: 0.05 (override RiskGuard config for initial phase)
- Lot calculated from risk% — no manual override (change risk config to adjust)

### Logging
- All approve/reject logged via structlog
- Trade journal auto-entry on order placement

## Data Flow

```
TradingBrain
  ├── EntryConfirmer → ENTER decision
  │     ↓
  ├── ApprovalManager.create_approval()
  │     ├── Save to DB (trade_approvals)
  │     ├── Telegram: send alert + buttons
  │     └── WebSocket: push {type: "approval"}
  │
  ├── Bi approves (Telegram OR Dashboard)
  │     ↓
  ├── ApprovalManager.approve()
  │     ├── RiskGuard re-check
  │     ├── Price validation
  │     ├── MT5 place_order()
  │     ├── Create ManagedPosition
  │     ├── Update DB status
  │     ├── Telegram: edit "✅ Approved"
  │     └── WebSocket: push {type: "approval_update"}
  │
  └── Bi rejects
        ↓
        ApprovalManager.reject()
        ├── Update DB status
        ├── Telegram: edit "❌ Rejected"
        └── WebSocket: push {type: "approval_update"}
```

## Non-Goals (v1)

- Auto-trade mode (no approval needed) — future upgrade
- Multi-account support
- Adjustable lot at approve time
- Economic calendar auto-fetch
