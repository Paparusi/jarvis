"""TradingMemory — Persistent event log + context builder for trading awareness.

Captures ALL trading lifecycle events and builds rich context for LLM conversations.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("trading.trading_memory")

_tm_initialized = False


def _init_tm_tables(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trading_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            symbol TEXT DEFAULT 'XAUUSD',
            session TEXT DEFAULT '',
            summary TEXT NOT NULL,
            details TEXT DEFAULT '{}',
            price_at_event REAL DEFAULT 0,
            plan_id INTEGER,
            ticket INTEGER,
            source TEXT DEFAULT 'brain',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_te_type ON trading_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_te_created ON trading_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_te_ticket ON trading_events(ticket);
    """)
    conn.commit()


def _ensure_tm_tables():
    global _tm_initialized
    conn = get_connection()
    if not _tm_initialized:
        _init_tm_tables(conn)
        _tm_initialized = True
    return conn


class TradingMemory:
    """Persistent trading event log and context builder."""

    # Event type constants
    PLAN = "plan"
    ZONE_ALERT = "zone_alert"
    ENTRY_DECISION = "entry_decision"
    APPROVAL = "approval"
    POSITION_OPEN = "position_open"
    POSITION_CLOSE = "position_close"
    DAILY_SUMMARY = "daily_summary"

    def log_event(self, event_type, summary, details=None, symbol="XAUUSD",
                  session="", price_at_event=0, plan_id=None, ticket=None,
                  source="brain") -> int:
        """Log a trading event. Returns row id."""
        conn = _ensure_tm_tables()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """INSERT INTO trading_events
               (event_type, symbol, session, summary, details,
                price_at_event, plan_id, ticket, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_type, symbol, session, summary,
             json.dumps(details or {}, default=str),
             price_at_event, plan_id, ticket, source, now),
        )
        conn.commit()
        return cursor.lastrowid

    def get_recent_events(self, hours=12, event_types=None, symbol="", limit=30) -> list[dict]:
        """Get recent trading events."""
        conn = _ensure_tm_tables()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        query = "SELECT * FROM trading_events WHERE created_at >= ?"
        params: list[Any] = [cutoff]
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            query += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_events_for_plan(self, plan_id: int) -> list[dict]:
        conn = _ensure_tm_tables()
        rows = conn.execute(
            "SELECT * FROM trading_events WHERE plan_id = ? ORDER BY created_at",
            (plan_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_events_for_ticket(self, ticket: int) -> list[dict]:
        conn = _ensure_tm_tables()
        rows = conn.execute(
            "SELECT * FROM trading_events WHERE ticket = ? ORDER BY created_at",
            (ticket,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_today_pnl(self) -> dict:
        """Calculate today's P&L from position_close events."""
        conn = _ensure_tm_tables()
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        rows = conn.execute(
            "SELECT details FROM trading_events WHERE event_type = 'position_close' AND created_at >= ?",
            (today_start,),
        ).fetchall()
        total_pnl = 0.0
        trades = 0
        wins = 0
        for row in rows:
            try:
                d = json.loads(row["details"])
                pnl = d.get("pnl", 0)
                total_pnl += pnl
                trades += 1
                if pnl > 0:
                    wins += 1
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "total_pnl": round(total_pnl, 2),
            "trades": trades,
            "wins": wins,
            "win_rate": round(wins / trades * 100, 1) if trades else 0,
        }

    def build_context(self, query="", symbol="XAUUSD") -> str:
        """Build comprehensive trading context for LLM injection."""
        parts = []

        # 1. Active plan summary
        plan_summary = self._build_plan_summary()
        if plan_summary:
            parts.append(plan_summary)

        # 2. Recent events (last 12h)
        events = self.get_recent_events(hours=12, limit=20)
        if events:
            event_lines = []
            for e in reversed(events):  # chronological order
                ts = e["created_at"][11:16]  # HH:MM
                event_lines.append(f"  [{ts}] {e['event_type'].upper()}: {e['summary']}")
            parts.append("## Trading Events (last 12h):\n" + "\n".join(event_lines))

        # 3. Today's P&L
        pnl = self.get_today_pnl()
        if pnl["trades"] > 0:
            parts.append(
                f"## Today P&L: ${pnl['total_pnl']:+.2f} "
                f"({pnl['trades']} trades, {pnl['win_rate']}% win rate)"
            )

        # 4. Managed positions
        pos_summary = self._build_position_summary()
        if pos_summary:
            parts.append(pos_summary)

        # 5. Recent setup outcomes
        outcomes = self._build_setup_outcomes()
        if outcomes:
            parts.append(outcomes)

        if not parts:
            return ""
        return "## TRADING MEMORY (persistent across sessions)\n\n" + "\n\n".join(parts)

    def _build_plan_summary(self) -> str:
        conn = _ensure_tm_tables()
        try:
            row = conn.execute(
                "SELECT * FROM trade_plans WHERE active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except Exception:
            return ""
        if not row:
            return ""
        plan = dict(row)
        zones = json.loads(plan.get("alert_zones", "[]"))
        return (
            f"## Active Plan (ID: {plan['id']}):\n"
            f"  Session: {plan['session']} | Bias: {plan['bias']}\n"
            f"  Reasoning: {plan.get('bias_reasoning', 'N/A')[:200]}\n"
            f"  Zones: {len(zones)} | Trades: {plan['trades_taken']}/{plan['max_trades']}"
        )

    def _build_position_summary(self) -> str:
        conn = _ensure_tm_tables()
        try:
            managed = conn.execute(
                "SELECT * FROM managed_positions WHERE closed_at IS NULL"
            ).fetchall()
        except Exception:
            return ""
        if not managed:
            return ""
        lines = []
        for row in managed:
            r = dict(row)
            lines.append(
                f"  #{r['ticket']} {r['direction'].upper()} {r.get('symbol', 'XAUUSD')} "
                f"vol:{r.get('volume', 0.01)} @ {r['entry_price']} "
                f"SL:{r['sl']} TP1:{r['tp1']} [JARVIS]"
            )
        return "## Managed Positions (JARVIS):\n" + "\n".join(lines)

    def _build_setup_outcomes(self) -> str:
        events = self.get_recent_events(
            hours=24, event_types=["zone_alert", "entry_decision"], limit=10
        )
        if not events:
            return ""
        lines = []
        for e in events[:5]:
            try:
                d = json.loads(e.get("details", "{}"))
            except (json.JSONDecodeError, TypeError):
                d = {}
            if e["event_type"] == "entry_decision":
                action = d.get("action", "?")
                reason = d.get("reasoning", "")[:100]
                lines.append(f"  [{e['created_at'][11:16]}] {action} @ {e['price_at_event']} — {reason}")
            elif e["event_type"] == "zone_alert":
                lines.append(f"  [{e['created_at'][11:16]}] Zone triggered @ {e['price_at_event']}")
        if not lines:
            return ""
        return "## Recent Setups:\n" + "\n".join(lines)

    def cleanup_old_events(self, days=30) -> int:
        conn = _ensure_tm_tables()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = conn.execute("DELETE FROM trading_events WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount
