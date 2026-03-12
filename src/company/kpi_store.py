"""KPI Store — SQLite persistence for company KPIs, reports, and schedule logs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("company.kpi_store")

_tables_initialized = False


def _init_kpi_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS company_kpis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            department TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kpi_date ON company_kpis(date, department);

        CREATE TABLE IF NOT EXISTS company_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            department TEXT NOT NULL,
            report_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            worker_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_report_date ON company_reports(date, department);

        CREATE TABLE IF NOT EXISTS company_schedule_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            routine_id TEXT NOT NULL,
            task_id INTEGER,
            status TEXT DEFAULT 'dispatched',
            executed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sched_date ON company_schedule_log(executed_at);
    """)


def _ensure_kpi_tables() -> sqlite3.Connection:
    global _tables_initialized
    conn = get_connection()
    if not _tables_initialized:
        _init_kpi_tables(conn)
        _tables_initialized = True
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class KPIStore:
    """Persistence for company KPIs, reports, and schedule execution logs."""

    def log_kpi(
        self,
        department: str,
        metric_name: str,
        value: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conn = _ensure_kpi_tables()
        conn.execute(
            """INSERT INTO company_kpis
               (date, department, metric_name, metric_value, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_today(), department, metric_name, value, json.dumps(metadata or {}), _now_iso()),
        )
        conn.commit()

    def get_kpis(self, date: str | None = None) -> list[dict]:
        conn = _ensure_kpi_tables()
        d = date or _today()
        rows = conn.execute(
            "SELECT * FROM company_kpis WHERE date = ? ORDER BY created_at DESC",
            (d,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_kpi_history(self, metric_name: str, days: int = 30) -> list[dict]:
        conn = _ensure_kpi_tables()
        rows = conn.execute(
            """SELECT date, SUM(metric_value) as value
               FROM company_kpis
               WHERE metric_name = ?
               GROUP BY date
               ORDER BY date DESC
               LIMIT ?""",
            (metric_name, days),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_report(
        self,
        department: str,
        report_type: str,
        title: str,
        content: str,
        worker_id: str = "",
    ) -> int:
        conn = _ensure_kpi_tables()
        cursor = conn.execute(
            """INSERT INTO company_reports
               (date, department, report_type, title, content, worker_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_today(), department, report_type, title, content, worker_id, _now_iso()),
        )
        conn.commit()
        log.info("report_saved", report_id=cursor.lastrowid, department=department, report_type=report_type)
        return cursor.lastrowid

    def get_reports(self, limit: int = 20, department: str | None = None) -> list[dict]:
        conn = _ensure_kpi_tables()
        if department:
            rows = conn.execute(
                "SELECT * FROM company_reports WHERE department = ? ORDER BY created_at DESC LIMIT ?",
                (department, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM company_reports ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def log_schedule_execution(self, routine_id: str, task_id: int, status: str = "dispatched") -> None:
        conn = _ensure_kpi_tables()
        conn.execute(
            "INSERT INTO company_schedule_log (routine_id, task_id, status, executed_at) VALUES (?, ?, ?, ?)",
            (routine_id, task_id, status, _now_iso()),
        )
        conn.commit()

    def update_schedule_status(self, routine_id: str, status: str) -> None:
        conn = _ensure_kpi_tables()
        conn.execute(
            """UPDATE company_schedule_log SET status = ?
               WHERE routine_id = ? AND executed_at >= ?
               ORDER BY id DESC LIMIT 1""",
            (status, routine_id, _today()),
        )
        conn.commit()

    def get_schedule_log(self, date: str | None = None) -> list[dict]:
        conn = _ensure_kpi_tables()
        d = date or _today()
        rows = conn.execute(
            "SELECT * FROM company_schedule_log WHERE executed_at >= ? ORDER BY executed_at",
            (d,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_today_reports_count(self, department: str | None = None) -> int:
        conn = _ensure_kpi_tables()
        if department:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM company_reports WHERE date = ? AND department = ?",
                (_today(), department),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM company_reports WHERE date = ?",
                (_today(),),
            ).fetchone()
        return row["cnt"]
