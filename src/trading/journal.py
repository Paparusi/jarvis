"""Trade Journal — Track, log, and analyze trading history.

SQLite-backed persistent journal. Follows bounty/store.py pattern.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("trading.journal")

_journal_initialized = False


def _init_journal_tables(conn: sqlite3.Connection) -> None:
    """Create journal tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket INTEGER NOT NULL,
            symbol TEXT NOT NULL DEFAULT 'XAUUSD',
            side TEXT NOT NULL,
            volume REAL NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            sl REAL,
            tp REAL,
            signal_score REAL,
            strategy_name TEXT DEFAULT '',
            session TEXT DEFAULT '',
            timeframe TEXT DEFAULT '',
            profit REAL DEFAULT 0.0,
            profit_pips REAL DEFAULT 0.0,
            r_multiple REAL,
            opened_at TEXT,
            closed_at TEXT,
            duration_seconds INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(ticket)
        );

        CREATE INDEX IF NOT EXISTS idx_journal_symbol ON trade_journal(symbol);
        CREATE INDEX IF NOT EXISTS idx_journal_closed_at ON trade_journal(closed_at);
        CREATE INDEX IF NOT EXISTS idx_journal_strategy ON trade_journal(strategy_name);

        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equity REAL NOT NULL,
            balance REAL NOT NULL,
            open_positions INTEGER DEFAULT 0,
            recorded_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    # Migration: add source column if missing
    try:
        conn.execute("ALTER TABLE trade_journal ADD COLUMN source TEXT DEFAULT 'unknown'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    log.info("journal_tables_initialized")


def get_journal_connection() -> sqlite3.Connection:
    """Get SQLite connection with journal tables initialized."""
    global _journal_initialized
    conn = get_connection()
    if not _journal_initialized:
        _init_journal_tables(conn)
        _journal_initialized = True
    return conn


def log_trade(trade: dict[str, Any]) -> int:
    """Insert or update a trade record. Returns row id."""
    conn = get_journal_connection()
    ticket = trade.get("ticket", 0)
    if not ticket:
        raise ValueError("ticket is required")

    conn.execute(
        """INSERT OR REPLACE INTO trade_journal
           (ticket, symbol, side, volume, entry_price, exit_price,
            sl, tp, signal_score, strategy_name, session, timeframe,
            profit, profit_pips, r_multiple, opened_at, closed_at,
            duration_seconds, notes, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ticket,
            trade.get("symbol", "XAUUSD"),
            trade.get("side", "buy"),
            trade.get("volume", 0.0),
            trade.get("entry_price", 0.0),
            trade.get("exit_price"),
            trade.get("sl"),
            trade.get("tp"),
            trade.get("signal_score"),
            trade.get("strategy_name", ""),
            trade.get("session", ""),
            trade.get("timeframe", ""),
            trade.get("profit", 0.0),
            trade.get("profit_pips", 0.0),
            trade.get("r_multiple"),
            trade.get("opened_at"),
            trade.get("closed_at"),
            trade.get("duration_seconds", 0),
            trade.get("notes", ""),
            trade.get("tags", ""),
        ),
    )
    conn.commit()
    cursor = conn.execute(
        "SELECT id FROM trade_journal WHERE ticket = ?", (ticket,)
    )
    row = cursor.fetchone()
    return row[0] if row else 0


def get_trade(ticket: int) -> dict[str, Any] | None:
    """Get a single trade by ticket."""
    conn = get_journal_connection()
    row = conn.execute(
        "SELECT * FROM trade_journal WHERE ticket = ?", (ticket,)
    ).fetchone()
    return dict(row) if row else None


def get_trades(
    days: int = 30,
    symbol: str = "",
    strategy: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get trades with optional filters."""
    conn = get_journal_connection()
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()

    query = "SELECT * FROM trade_journal WHERE created_at >= ?"
    params: list[Any] = [cutoff]

    if symbol:
        query += " AND symbol = ?"
        params.append(symbol.upper())
    if strategy:
        query += " AND strategy_name = ?"
        params.append(strategy)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def calculate_stats(days: int = 30) -> dict[str, Any]:
    """Calculate trading statistics for the given period."""
    conn = get_journal_connection()
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()

    rows = conn.execute(
        "SELECT * FROM trade_journal WHERE closed_at IS NOT NULL AND closed_at >= ? ORDER BY closed_at",
        (cutoff,),
    ).fetchall()

    if not rows:
        return {"total_trades": 0, "message": "Không có giao dịch trong khoảng thời gian này"}

    trades = [dict(r) for r in rows]
    wins = [t for t in trades if t["profit"] > 0]
    losses = [t for t in trades if t["profit"] < 0]

    total_profit = sum(t["profit"] for t in wins) if wins else 0.0
    total_loss = abs(sum(t["profit"] for t in losses)) if losses else 0.0

    # Best session
    session_profits: dict[str, float] = {}
    for t in trades:
        s = t.get("session") or "Unknown"
        session_profits[s] = session_profits.get(s, 0.0) + t["profit"]
    best_session = max(session_profits, key=session_profits.get) if session_profits else "N/A"

    # Best day of week
    dow_profits: dict[str, float] = {}
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for t in trades:
        if t.get("closed_at"):
            try:
                dt = datetime.fromisoformat(t["closed_at"])
                day_name = day_names[dt.weekday()]
                dow_profits[day_name] = dow_profits.get(day_name, 0.0) + t["profit"]
            except Exception:
                pass
    best_day = max(dow_profits, key=dow_profits.get) if dow_profits else "N/A"

    # Streaks
    current_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    for t in trades:
        if t["profit"] > 0:
            if current_streak < 0:
                current_streak = 0
            current_streak += 1
            max_win_streak = max(max_win_streak, current_streak)
        elif t["profit"] < 0:
            if current_streak > 0:
                current_streak = 0
            current_streak -= 1
            max_loss_streak = max(max_loss_streak, abs(current_streak))
        else:
            current_streak = 0

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(sum(t["profit"] for t in trades), 2),
        "avg_profit": round(total_profit / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-total_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(total_profit / total_loss, 2) if total_loss > 0 else float("inf"),
        "avg_r": round(
            sum(t.get("r_multiple") or 0 for t in trades) / len(trades), 2
        ),
        "best_session": best_session,
        "best_day_of_week": best_day,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "days": days,
    }


def sync_from_mt5(deals: list[dict[str, Any]]) -> dict[str, int]:
    """Sync MT5 deal history into the journal."""
    conn = get_journal_connection()
    synced = 0
    skipped = 0
    errors = 0

    for deal in deals:
        ticket = deal.get("ticket", 0)
        if not ticket:
            errors += 1
            continue

        existing = conn.execute(
            "SELECT id FROM trade_journal WHERE ticket = ?", (ticket,)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        try:
            side = "buy" if deal.get("type", 0) == 0 else "sell"
            opened_ts = deal.get("time", 0)
            opened_at = (
                datetime.fromtimestamp(opened_ts, tz=timezone.utc).isoformat()
                if opened_ts else None
            )

            comment = deal.get("comment", "")
            source = "jarvis" if "JARVIS" in comment.upper() else "user"

            conn.execute(
                """INSERT INTO trade_journal
                   (ticket, symbol, side, volume, entry_price, exit_price,
                    profit, opened_at, closed_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticket,
                    deal.get("symbol", "XAUUSD"),
                    side,
                    deal.get("volume", 0),
                    deal.get("price", 0),
                    deal.get("price", 0),
                    deal.get("profit", 0),
                    opened_at,
                    opened_at,
                    source,
                ),
            )
            synced += 1
        except Exception as e:
            log.warning("journal_sync_error", ticket=ticket, error=str(e))
            errors += 1

    conn.commit()
    return {"synced": synced, "skipped": skipped, "errors": errors}


def record_equity_snapshot(
    equity: float, balance: float, positions: int = 0
) -> None:
    """Record equity for equity curve tracking."""
    conn = get_journal_connection()
    conn.execute(
        "INSERT INTO equity_snapshots (equity, balance, open_positions) VALUES (?, ?, ?)",
        (equity, balance, positions),
    )
    conn.commit()


def get_equity_curve(days: int = 30) -> list[dict[str, Any]]:
    """Get equity snapshots for charting."""
    conn = get_journal_connection()
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM equity_snapshots WHERE recorded_at >= ? ORDER BY recorded_at",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]
