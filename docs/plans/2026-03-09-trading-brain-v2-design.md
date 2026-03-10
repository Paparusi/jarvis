# Trading Brain v2 — Smart Execution Design

## Goal
Transform JARVIS trading from reactive (wait for price → LLM confirm → market order) to proactive (LLM analyze → place limit orders at zones → auto-fill). Add position recovery, persistent state, dynamic lot sizing, and order modification.

## Architecture

**Current flow (v1):**
```
Plan → Monitor price (10s) → Price hits zone → LLM confirm (3-5s) → Market order
Total latency: 8-20s
```

**New flow (v2):**
```
Plan → LLM confirm zones → Place limit orders at zones → Auto-fill (0s latency)
Monitor: cancel stale orders on invalidation, modify SL/TP, manage filled positions
```

**Key change:** LLM confirmation moves from "on alert" to "before placing orders" — one-time cost at plan creation, zero cost at execution.

## Tech Stack
- Python async, SQLite for persistence
- MT5 bridge: FastAPI (Windows), 4 new endpoints
- httpx async client (WSL2)
- Existing: litellm, structlog, pytest

---

## Component Design

### 1. MT5 Bridge v2 — New Endpoints

Add to `scripts/mt5_bridge.py`:

```python
# Place pending order (limit/stop)
@app.post("/order/pending")
async def place_pending_order(body: PendingOrderRequest):
    """
    body: {symbol, order_type, volume, price, sl, tp, comment}
    order_type: "buy_limit" | "sell_limit" | "buy_stop" | "sell_stop"

    MT5 mapping:
      buy_limit  → mt5.ORDER_TYPE_BUY_LIMIT
      sell_limit → mt5.ORDER_TYPE_SELL_LIMIT
      buy_stop   → mt5.ORDER_TYPE_BUY_STOP
      sell_stop  → mt5.ORDER_TYPE_SELL_STOP

    Returns: {ticket, retcode, comment}
    Volume cap: 5.0 lots (same as market orders)
    """

# Modify existing order or position
@app.put("/order/{ticket}")
async def modify_order(ticket: int, body: ModifyRequest):
    """
    body: {sl?, tp?, price?}
    - For pending orders: can modify sl, tp, price
    - For open positions: can modify sl, tp only

    Uses mt5.order_send() with action=TRADE_ACTION_MODIFY
    Returns: {retcode, comment}
    """

# Cancel pending order
@app.delete("/order/{ticket}")
async def cancel_order(ticket: int):
    """
    Uses mt5.order_send() with action=TRADE_ACTION_REMOVE
    Returns: {retcode, comment}
    """

# List pending orders
@app.get("/orders/pending")
async def get_pending_orders(symbol: str = None):
    """
    Uses mt5.orders_get(symbol=symbol)
    Returns: [{ticket, symbol, type, volume, price_open, sl, tp, comment, time_setup}, ...]
    """
```

### 2. MT5 Client Extensions

Add to `src/trading/mt5_client.py`:

```python
async def place_pending(self, symbol, order_type, volume, price, sl=None, tp=None, comment="JARVIS"):
    """POST /order/pending"""

async def modify_order(self, ticket, sl=None, tp=None, price=None):
    """PUT /order/{ticket}"""

async def cancel_order(self, ticket):
    """DELETE /order/{ticket}"""

async def get_pending_orders(self, symbol=None):
    """GET /orders/pending"""
```

### 3. Dynamic Lot Sizing

Add to `src/trading/risk.py`:

```python
def calculate_lot_from_risk(
    equity: float,
    risk_pct: float,          # 1.0 = 1%
    entry_price: float,
    sl_price: float,
    pip_value_per_lot: float = 100.0,  # XAUUSD: $100/pip/1.0lot ($1/pip/0.01lot)
    min_lot: float = 0.01,
    max_lot: float = 0.1,
) -> float:
    """
    risk_amount = equity * risk_pct / 100
    sl_pips = abs(entry_price - sl_price)
    lot = risk_amount / (sl_pips * pip_value_per_lot)
    return clamp(round_to_0.01, min_lot, max_lot)

    Example: $1000 equity, 1% risk, entry=2650, SL=2635 (15 pips)
    risk = $10, lot = $10 / (15 * 100) = 0.0067 → 0.01

    Example: $10000 equity, 1% risk, entry=2650, SL=2640 (10 pips)
    risk = $100, lot = $100 / (10 * 100) = 0.10
    """
```

### 4. PendingOrderManager

New file: `src/trading/pending_orders.py`

```python
@dataclass
class PendingOrder:
    ticket: int
    zone_id: str
    symbol: str
    order_type: str       # "buy_limit", "sell_limit", etc.
    volume: float
    price: float
    sl: float
    tp1: float
    tp2: float
    placed_at: datetime
    status: str = "pending"  # "pending" | "filled" | "cancelled" | "expired"

class PendingOrderManager:
    def __init__(self, mt5_client, risk_guard, db_path="data/jarvis.db"):
        self._mt5 = mt5_client
        self._risk_guard = risk_guard
        self._db_path = db_path
        self._orders: dict[int, PendingOrder] = {}

    async def place_zone_orders(self, zones, equity, risk_pct=1.0):
        """For each approved zone:
        1. Calculate lot from risk
        2. Determine order type (buy_limit for buy zones below price, sell_limit for sell zones above)
        3. Place pending order via MT5
        4. Track in self._orders + persist to SQLite
        Returns list of placed tickets.
        """

    async def cancel_zone(self, zone_id):
        """Cancel pending order for invalidated zone."""

    async def modify_sl_tp(self, ticket, sl=None, tp=None):
        """Modify SL/TP on pending or filled order."""

    async def sync_from_mt5(self):
        """Fetch pending orders from MT5, reconcile with local state.
        - New orders in MT5 not in local → adopt
        - Local orders not in MT5 → mark filled or cancelled
        """

    async def check_fills(self):
        """Called periodically. Check if any pending order has been filled.
        If filled → notify TradingBrain to start managing the position.
        """

    async def cancel_all(self):
        """Cancel all pending orders (for /trade kill)."""

    async def on_plan_change(self, old_zones, new_zones):
        """When plan updates: cancel orders for removed zones, place for new ones."""
```

### 5. State Persistence (SQLite)

New file: `src/trading/persistence.py`

```python
class TradingPersistence:
    """SQLite persistence for trade plans and managed positions."""

    def __init__(self, db_path="data/jarvis.db"):
        self._db_path = db_path
        self._ensure_tables()

    # Trade Plans
    def save_plan(self, plan: TradePlan) -> int:
    def load_active_plan(self) -> TradePlan | None:
    def deactivate_plan(self, plan_id: int):
    def list_plans(self, limit=10) -> list[dict]:

    # Managed Positions
    def save_position(self, pos: ManagedPosition):
    def update_position(self, ticket: int, **kwargs):
    def load_open_positions(self) -> list[ManagedPosition]:
    def close_position(self, ticket: int, reason: str, pnl: float):

    # Pending Orders
    def save_pending(self, order: PendingOrder):
    def update_pending(self, ticket: int, status: str):
    def load_pending_orders(self) -> list[PendingOrder]:

    # Recovery
    def get_recovery_state(self) -> dict:
        """Return: {plan, positions, pending_orders}"""
```

SQL schema:
```sql
CREATE TABLE IF NOT EXISTS trade_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    created_at TEXT NOT NULL,
    bias TEXT DEFAULT 'neutral',
    bias_reasoning TEXT,
    market_regime TEXT,
    key_levels TEXT,        -- JSON
    alert_zones TEXT,       -- JSON
    scenarios TEXT,         -- JSON
    invalidation TEXT,
    risk_budget_pct REAL DEFAULT 2.0,
    max_trades INTEGER DEFAULT 3,
    trades_taken INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS managed_positions (
    ticket INTEGER PRIMARY KEY,
    zone_id TEXT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    volume REAL NOT NULL,
    entry_price REAL NOT NULL,
    sl REAL,
    tp1 REAL,
    tp2 REAL,
    original_sl REAL,
    be_moved INTEGER DEFAULT 0,
    tp1_hit INTEGER DEFAULT 0,
    remaining_volume REAL,
    trail_sl REAL DEFAULT 0,
    opened_at TEXT,
    closed_at TEXT,
    close_reason TEXT,
    pnl REAL
);

CREATE TABLE IF NOT EXISTS pending_orders (
    ticket INTEGER PRIMARY KEY,
    zone_id TEXT,
    symbol TEXT NOT NULL,
    order_type TEXT NOT NULL,
    volume REAL NOT NULL,
    price REAL NOT NULL,
    sl REAL,
    tp REAL,
    placed_at TEXT,
    status TEXT DEFAULT 'pending'
);
```

### 6. Position Recovery

In `TradingBrain.start()`:

```python
async def start(self):
    # 1. Load state from SQLite
    state = self._persistence.get_recovery_state()

    # 2. Recover plan
    if state["plan"]:
        self._current_plan = state["plan"]
        log.info("plan_recovered", session=self._current_plan.session)

    # 3. Recover managed positions
    saved_positions = state["positions"]
    mt5_positions = await self._mt5.get_positions()

    for saved in saved_positions:
        # Check if still open in MT5
        mt5_match = find_by_ticket(mt5_positions, saved.ticket)
        if mt5_match:
            # Still open — resume management
            self._position_manager.add_position(saved)
            log.info("position_recovered", ticket=saved.ticket)
        else:
            # Closed while we were offline
            self._persistence.close_position(saved.ticket, "closed_offline", 0)

    # 4. Recover pending orders
    for pending in state["pending_orders"]:
        self._pending_manager.adopt(pending)

    # 5. Sync with MT5 pending orders
    await self._pending_manager.sync_from_mt5()

    # 6. Start loops
    await self._position_manager.start()
    # ...
```

### 7. TradingBrain v2 Flow Changes

**New flow:**
```
TradePlanner.create_plan()
  → Quantitative analysis (same as v1)
  → LLM reasoning (same as v1, but now also confirms zones for pending orders)
  → For each approved zone:
      → EntryConfirmer.pre_confirm(zone)  # LLM pre-approve
      → If approved: PendingOrderManager.place_zone_order(zone)
      → Else: skip zone
  → PriceMonitor tracks invalidation → cancel stale pending orders
  → When pending order fills → PositionManager takes over (same as v1)
```

**EntryConfirmer changes:**
- New method: `pre_confirm(zone, plan_context)` — confirm at plan time, not at fill time
- Returns: APPROVE (place pending) or REJECT (skip zone)
- Simpler than real-time confirm since no M15 momentum check needed yet

**PriceMonitor changes:**
- Primary role shifts from "detect zone entry" to "detect zone invalidation"
- On invalidation → cancel pending order for that zone
- Also monitors fills: when pending order becomes position → notify brain

---

## Execution Order

```
Task 1: Bridge v2 endpoints (Windows Python)
Task 2: MT5 Client extensions (4 new methods)
Task 3: Dynamic lot sizing (risk.py)
Task 4: Persistence module (SQLite tables + CRUD)
Task 5: PendingOrderManager (core module)
Task 6: Position recovery logic
Task 7: TradingBrain v2 wiring (integrate all new modules)
Task 8: Tools + Telegram + CLI integration
```

## Risk Mitigation
- RiskGuard still checks BEFORE placing pending orders (not bypassed)
- Pending orders have MT5-level SL/TP (safety even if JARVIS offline)
- Volume cap in bridge (5.0 lots) prevents catastrophic orders
- Kill switch cancels ALL pending orders + closes ALL positions
