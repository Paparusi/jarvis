# Trading Brain — Alert-Driven Autonomous Trading Intelligence

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Transform JARVIS from a signal analyzer into an autonomous trading agent that plans sessions, sets price alerts at high-confluence zones, confirms entries with LLM reasoning, manages positions dynamically, and enforces strict risk controls.

**Architecture:** Alert-driven LLM agent — deep analysis at session open, lightweight price monitoring, LLM re-confirmation only when price reaches zones, full dynamic position management with RiskGuard veto layer.

**Tech Stack:** Pure Python async, existing MT5 bridge, Claude API for reasoning, SQLite for state, Telegram for notifications.

---

## 1. Architecture Overview

```
Session Open (London/NY)
    |
    v
+---------------------+
|  1. DEEP ANALYSIS   |  <-- LLM + SMC + Multi-TF + Volume Profile (1x/session)
|  HTF -> Key zones   |
|  -> Scenarios        |
|  -> Entry plans      |
+--------+------------+
         v
+---------------------+
|  2. PRICE MONITOR   |  <-- Lightweight, NO LLM
|  Set alerts at      |      Check price vs zones every 10s
|  analyzed zones     |
+--------+------------+
         | (price enters zone -> alert!)
         v
+---------------------+
|  3. CONFIRMATION    |  <-- Call LLM
|  Re-analyze LTF     |     Check: structure intact?
|  Check SMC + RSI    |     OB not mitigated?
|  + session + news   |     Kill zone? News?
+-------+-----+------+
        |     |
     OK v     v NOT OK
+----------+ +--------------+
| 4. ENTER | | Re-analyze   |
| + MANAGE | | -> Find new  |
| trailing | |    zones     |
| partial  | | -> Set new   |
| BE, exit | |    alerts    |
+----------+ +--------------+
```

**Key Principle:** LLM decides, RiskGuard protects. RiskGuard can NEVER be overridden by LLM.

**LLM usage is minimal:**
- 1x at session open (deep analysis + plan)
- 1x per alert trigger (quick confirmation)
- NOT called during price monitoring or position management (deterministic rules)

---

## 2. SessionPlanner — Deep Analysis + TradePlan

Runs once at session open. Produces a TradePlan with ranked alert zones.

### 3-Layer Analysis

**Layer 1: Market Context**
- Weekly/Daily bias (W1 + D1 structure, last BOS/ChoCH)
- Intermarket: DXY correlation with Gold (via web_search)
- Previous session recap: Asian range (H/L/close), sweeps
- Market regime: Trending / Ranging / Volatile / Quiet (ATR + BB width + swing frequency)

**Layer 2: Key Levels**
- SMC zones: Order Blocks, FVG, Supply/Demand (existing)
- Session levels: Asian High/Low, PDH/PDL/PDO/PDC
- Liquidity pools: Equal highs/lows clusters
- Round numbers: $2650, $2660, etc.
- Fibonacci: 0.618 / 0.705 / 0.786 of recent swing
- Volume Profile: POC, VAH, VAL, HVN, LVN (NEW module)
- Confluence scoring: rank zones by overlap count

**Layer 3: Timing & Risk Filter**
- Economic calendar events in session
- Kill zone check
- Journal patterns (recent win rate by session/day)
- Spread check

### TradePlan Data Structure

```python
@dataclass
class TradePlan:
    session: str               # "London" / "NY"
    bias: str                  # "bullish" / "bearish" / "neutral"
    bias_reasoning: str
    alert_zones: list[AlertZone]
    scenarios: list[Scenario]
    risk_budget: float         # Max risk for this session (% equity)
    max_trades: int
    invalidation: str          # When entire plan is invalid
    created_at: datetime

@dataclass
class AlertZone:
    zone_id: str
    direction: str             # "buy" / "sell"
    price_high: float
    price_low: float
    confluence: list[str]      # ["H1 Bullish OB", "Fib 0.618", "PDL"]
    confluence_score: int      # 0-100
    sl_price: float
    tp1_price: float           # Partial close target
    tp2_price: float           # Full close target
    rr_ratio: float
    lot_size: float            # Auto-calculated
    reasoning: str
    invalidation: str          # Zone-specific invalidation
    expiry: str                # "End of London session"

@dataclass
class Scenario:
    condition: str             # "If price breaks above 2665..."
    action: str                # "Cancel sell zones, re-scan for buy"
    new_bias: str
```

### Example Telegram Output

```
TRADE PLAN -- London Session 07/03/2026

Market Context:
  W1: Bearish (ChoCH @ 2680)
  D1: Bearish BOS, making LH-LL
  DXY: 104.2 (+0.3%) -> Gold bearish pressure
  Asian: Ranged 2648-2658, no sweep
  Regime: TRENDING (ATR expanding)
  Volume: POC 2652, VAH 2664, VAL 2641

Alert Zones (ranked by confluence):
1. SELL @ [2663-2667] -- Score: 85
   H1 OB + Fib 0.705 + PDH + VAH + EQH liquidity
   SL: 2672 | TP1: 2650 | TP2: 2638
   R:R = 1:3.2 | Lot: 0.03
   Invalid if close > 2670

2. BUY @ [2635-2638] -- Score: 52
   H1 Demand + Fib 0.618 + VAL
   SL: 2630 | TP1: 2648 | TP2: 2660
   R:R = 1:2.0 | Lot: 0.02
   Invalid if close < 2628

Scenarios:
  Break 2670 -> Cancel sell zones, re-scan for buy
  Sweep 2635 + reclaim -> Strong buy signal
  NFP 20:30 -> Flatten all before 20:00

Budget: 2% max | 2 trades max | DD today: 0.5%
```

---

## 3. PriceMonitor — Lightweight Alert Watcher

Background loop. NO LLM calls. Only compares price to alert zones.

```python
class PriceMonitor:
    poll_interval: int = 10      # seconds
    active_alerts: list[AlertZone]
    active_scenarios: list[Scenario]
```

**Logic:**
1. Every 10s: `get_tick()` -> compare to alert zones
2. Price enters zone -> trigger EntryConfirmer (LLM call)
3. Price closes past invalidation -> remove zone, notify Telegram
4. Scenario condition met -> trigger re-plan or action
5. Zone expiry (session end) -> cleanup

---

## 4. EntryConfirmer — LLM Re-analysis on Alert

When PriceMonitor triggers, EntryConfirmer runs quick LTF analysis.

**Input to LLM:**
- Alert zone info (type, direction, confluence, reasoning)
- Current M5/M15 candles (10-20 recent)
- M15 SMC (structure still intact?)
- M15 RSI, MACD
- Current spread
- Open positions
- Risk state

**LLM decisions:**

| Decision | Action |
|----------|--------|
| ENTER | Execute trade with AlertZone params, hand to PositionManager |
| SKIP | Zone no longer valid, remove alert, scan for new zones |
| WAIT | Not enough confirmation yet, keep alert, re-check after 1-2 candles |

---

## 5. PositionManager — Dynamic Position Management

After entry, manages the position with deterministic rules (no LLM):

### Actions:

1. **Break-Even Move**
   - Price moves >= 50% toward TP1 -> move SL to entry + 1 pip
   - "Free trade" protection

2. **Partial Close (TP1)**
   - Price hits TP1 -> close 50% volume
   - Move remaining SL to entry (break-even)

3. **Trailing Stop**
   - After TP1: trail SL following M15 swing structure
   - BUY: SL below each new Higher Low
   - SELL: SL above each new Lower High
   - Alternative: ATR trailing (SL = price +/- 1.5 * ATR)
   - Use whichever is tighter

4. **Emergency Exit**
   - H1 ChoCH reversal -> close immediately
   - High-impact news in 15 min -> close or tighten SL
   - Daily drawdown exceeded -> close all

5. **Full Close (TP2)**
   - Price hits TP2 -> close remaining
   - Log to journal with full trade details

### Monitoring intervals:
- Every 10s: price vs SL/TP/BE levels
- Every 1 min: M15 structure (trailing logic)
- Every 5 min: H1 structure (emergency logic)

### Post-trade:
1. Log to journal (ticket, P/L, R-multiple, duration, strategy, confluence)
2. Notify Telegram with result
3. Check remaining risk budget -> if available, return to PriceMonitor
4. If budget exhausted or max trades reached -> stop for session

---

## 6. RiskGuard — Circuit Breaker with Veto Power

**NEVER bypassed by LLM.** All actions go through RiskGuard before execution.

```python
@dataclass
class RiskConfig:
    # Per-trade limits
    max_risk_per_trade_pct: float = 1.0
    max_lot_size: float = 0.1
    min_rr_ratio: float = 1.5

    # Portfolio limits
    max_open_positions: int = 3
    max_same_direction: int = 2
    max_correlated_exposure_pct: float = 3.0

    # Daily limits
    max_daily_loss_pct: float = 3.0
    max_daily_trades: int = 5
    max_consecutive_losses: int = 3

    # Session filter
    allowed_sessions: list[str] = ["London", "NY"]
    kill_zone_only: bool = False

    # News filter
    stop_before_high_impact_mins: int = 15
    flatten_before_news: bool = True

    # Emergency
    max_weekly_loss_pct: float = 8.0
    kill_switch: bool = False
```

Every action is checked against ALL rules. Any violation -> VETO + Telegram notification.

---

## 7. Integration

### Telegram Commands

| Command | Action |
|---------|--------|
| `/trade start` | Start Trading Brain, begin session monitoring |
| `/trade stop` | Stop, clear alerts, keep open positions |
| `/trade plan` | Show current TradePlan |
| `/trade status` | Status: alerts, positions, P&L, risk state |
| `/trade config` | View/edit RiskConfig |
| `/trade kill` | Kill switch - close everything immediately |

### Auto-scheduling (via ProactiveEngine)

```
06:50 VN (23:50 UTC)  -> Asian session recap
14:00 VN (07:00 UTC)  -> London plan: deep analysis -> alerts -> monitor
19:30 VN (12:30 UTC)  -> NY plan: re-analyze -> update zones -> continue
23:00 VN (16:00 UTC)  -> End-of-day: close alerts, daily summary
```

### Daily Summary

```
DAILY SUMMARY -- 07/03/2026

Trades: 2 | Win: 1 | Loss: 1
P/L: +$285 (+1.4%)
Best: SELL @ 2665 -> 2638 (+$810, 3.2R)
Worst: BUY @ 2638 -> 2645 SL (-$525, -1.0R)

Alerts triggered: 3 | Entered: 2 | Skipped: 1
Risk used: 2.4% / 3.0% budget

Win rate (30d): 62% | Profit factor: 1.85
Equity: $20,285 (+1.4% today, +8.2% month)
```

### New Files

```
src/trading/
  volume_profile.py     # POC, VAH, VAL, HVN, LVN from tick_volume
  session_levels.py     # PDH/PDL/Asian H/L, round numbers, Fibonacci
  confluence.py         # Zone ranking + multi-factor scoring
  trade_planner.py      # SessionPlanner + TradePlan + LLM orchestration
  price_monitor.py      # Lightweight alert watcher loop
  entry_confirmer.py    # LLM re-analysis on alert trigger
  position_manager.py   # Trailing/partial/BE/emergency management
  risk_guard.py         # Circuit breaker + veto logic + config
```

### Existing Files Modified
- `src/tools/trading_advanced.py` — add new tool handlers
- `src/tools/registry_all.py` — register new tools
- `src/app.py` — init_trading_brain() in JarvisApp
- `src/gateway/channels/telegram.py` — /trade commands
- `src/gateway/channels/cli.py` — /trade commands
- `workspace/skills/analysis/trading-analyst/SKILL.md` — v5.0.0 with brain features
