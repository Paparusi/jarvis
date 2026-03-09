"""TradingPersistence — SQLite storage for trade plans, positions, and pending orders."""

from __future__ import annotations

import json
import sqlite3
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
        );

        CREATE TABLE IF NOT EXISTS risk_state (
            key TEXT PRIMARY KEY,
            value REAL NOT NULL,
            updated_at TEXT NOT NULL
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
                json.dumps(getattr(plan, 'key_levels', {}), default=str),
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

    # ── Trade Approvals ──

    def save_approval(self, approval: dict) -> None:
        conn = _ensure_tables()
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
        conn = _ensure_tables()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [approval_id]
        conn.execute(f"UPDATE trade_approvals SET {sets} WHERE id = ?", vals)
        conn.commit()

    def get_pending_approvals(self) -> list[dict]:
        conn = _ensure_tables()
        rows = conn.execute(
            "SELECT * FROM trade_approvals WHERE status = 'pending' ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_approval(self, approval_id: str) -> dict | None:
        conn = _ensure_tables()
        row = conn.execute(
            "SELECT * FROM trade_approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Risk State ──

    def save_risk_state(self, state: dict[str, float]) -> None:
        """Persist risk state key-value pairs."""
        conn = _ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        for key, value in state.items():
            conn.execute(
                "INSERT OR REPLACE INTO risk_state (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )
        conn.commit()

    def load_risk_state(self) -> dict[str, float]:
        """Load persisted risk state. Returns empty dict if no data."""
        conn = _ensure_tables()
        rows = conn.execute("SELECT key, value FROM risk_state").fetchall()
        return {row["key"]: row["value"] for row in rows}

    # ── Recovery ──

    def get_recovery_state(self) -> dict:
        """Return full state for recovery: plan + positions + pending."""
        return {
            "plan": self.load_active_plan(),
            "positions": self.load_open_positions(),
            "pending_orders": self.load_pending_orders(),
        }
