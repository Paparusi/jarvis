"""Worker data layer — SQLite persistence for tasks, memories, and metrics."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("company.worker_store")

_tables_initialized = False


def _init_worker_tables(conn: sqlite3.Connection) -> None:
    """Create worker tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS worker_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department TEXT NOT NULL,
            worker_id TEXT,
            parent_task_id INTEGER,
            priority INTEGER DEFAULT 5,
            status TEXT DEFAULT 'pending',
            instruction TEXT NOT NULL,
            context TEXT DEFAULT '{}',
            result TEXT,
            error TEXT,
            session_id TEXT,
            created_at TEXT NOT NULL,
            assigned_at TEXT,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_wt_status ON worker_tasks(status);
        CREATE INDEX IF NOT EXISTS idx_wt_worker ON worker_tasks(worker_id, status);
        CREATE INDEX IF NOT EXISTS idx_wt_dept ON worker_tasks(department, status);

        CREATE TABLE IF NOT EXISTS worker_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_wm_worker ON worker_memories(worker_id, created_at);

        CREATE TABLE IF NOT EXISTS worker_metrics (
            worker_id TEXT PRIMARY KEY,
            tasks_completed INTEGER DEFAULT 0,
            tasks_failed INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            total_cost REAL DEFAULT 0.0,
            avg_response_ms REAL DEFAULT 0.0,
            updated_at TEXT NOT NULL
        );
    """)


def _ensure_tables() -> sqlite3.Connection:
    """Get DB connection and ensure worker tables exist."""
    global _tables_initialized
    conn = get_connection()
    if not _tables_initialized:
        _init_worker_tables(conn)
        _tables_initialized = True
    return conn


def _now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    """CRUD for the worker_tasks table."""

    def create_task(
        self,
        department: str,
        instruction: str,
        session_id: str | None = None,
        worker_id: str | None = None,
        parent_task_id: int | None = None,
        priority: int = 5,
        context: dict[str, Any] | None = None,
    ) -> int:
        """Insert a new task. Returns the row id."""
        conn = _ensure_tables()
        cursor = conn.execute(
            """INSERT INTO worker_tasks
               (department, instruction, session_id, worker_id,
                parent_task_id, priority, context, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                department,
                instruction,
                session_id,
                worker_id,
                parent_task_id,
                priority,
                json.dumps(context or {}),
                _now_iso(),
            ),
        )
        conn.commit()
        task_id = cursor.lastrowid
        log.info("task_created", task_id=task_id, department=department)
        return task_id

    def claim_task(self, worker_id: str, department: str) -> dict | None:
        """Claim the oldest pending task for a department.

        Sets status to 'assigned' and records worker_id.
        Returns the task dict or None if no pending tasks exist.
        """
        conn = _ensure_tables()
        row = conn.execute(
            """SELECT id FROM worker_tasks
               WHERE department = ? AND status = 'pending'
               ORDER BY priority ASC, created_at ASC
               LIMIT 1""",
            (department,),
        ).fetchone()
        if row is None:
            return None

        task_id = row["id"]
        now = _now_iso()
        conn.execute(
            """UPDATE worker_tasks
               SET status = 'assigned', worker_id = ?, assigned_at = ?
               WHERE id = ?""",
            (worker_id, now, task_id),
        )
        conn.commit()
        return self.get_task(task_id)

    def start_task(self, task_id: int) -> None:
        """Mark a task as in_progress."""
        conn = _ensure_tables()
        conn.execute(
            "UPDATE worker_tasks SET status = 'in_progress' WHERE id = ?",
            (task_id,),
        )
        conn.commit()

    def complete_task(self, task_id: int, result: str) -> None:
        """Mark a task as completed with a result."""
        conn = _ensure_tables()
        conn.execute(
            """UPDATE worker_tasks
               SET status = 'completed', result = ?, completed_at = ?
               WHERE id = ?""",
            (result, _now_iso(), task_id),
        )
        conn.commit()
        log.info("task_completed", task_id=task_id)

    def fail_task(self, task_id: int, error: str) -> None:
        """Mark a task as failed with an error message."""
        conn = _ensure_tables()
        conn.execute(
            """UPDATE worker_tasks
               SET status = 'failed', error = ?, completed_at = ?
               WHERE id = ?""",
            (error, _now_iso(), task_id),
        )
        conn.commit()
        log.warning("task_failed", task_id=task_id, error=error)

    def escalate_task(self, task_id: int, error: str) -> None:
        """Mark a task as escalated with an error message."""
        conn = _ensure_tables()
        conn.execute(
            """UPDATE worker_tasks
               SET status = 'escalated', error = ?, completed_at = ?
               WHERE id = ?""",
            (error, _now_iso(), task_id),
        )
        conn.commit()
        log.warning("task_escalated", task_id=task_id, error=error)

    def get_task(self, task_id: int) -> dict | None:
        """Fetch a single task by id. Returns None if not found."""
        conn = _ensure_tables()
        row = conn.execute(
            "SELECT * FROM worker_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["context"] = json.loads(result.get("context") or "{}")
        return result

    def get_pending_count(self, department: str) -> int:
        """Count pending tasks for a department."""
        conn = _ensure_tables()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM worker_tasks WHERE department = ? AND status = 'pending'",
            (department,),
        ).fetchone()
        return row["cnt"]

    def get_worker_tasks(self, worker_id: str, limit: int = 20) -> list[dict]:
        """Get recent tasks assigned to a worker, newest first."""
        conn = _ensure_tables()
        rows = conn.execute(
            """SELECT * FROM worker_tasks
               WHERE worker_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (worker_id, limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["context"] = json.loads(d.get("context") or "{}")
            results.append(d)
        return results

    def cleanup_old(self, days: int = 30) -> int:
        """Delete completed/failed tasks older than *days*. Returns count deleted."""
        conn = _ensure_tables()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = conn.execute(
            """DELETE FROM worker_tasks
               WHERE status IN ('completed', 'failed')
                 AND completed_at < ?""",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount


class WorkerMemoryStore:
    """CRUD for the worker_memories table."""

    def log(
        self,
        worker_id: str,
        memory_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Insert a memory entry. Returns the row id."""
        conn = _ensure_tables()
        cursor = conn.execute(
            """INSERT INTO worker_memories
               (worker_id, memory_type, content, metadata, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                worker_id,
                memory_type,
                content,
                json.dumps(metadata or {}),
                _now_iso(),
            ),
        )
        conn.commit()
        return cursor.lastrowid

    def get_recent(
        self,
        worker_id: str,
        memory_type: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Get recent memories for a worker, optionally filtered by type."""
        conn = _ensure_tables()
        if memory_type is not None:
            rows = conn.execute(
                """SELECT * FROM worker_memories
                   WHERE worker_id = ? AND memory_type = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (worker_id, memory_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM worker_memories
                   WHERE worker_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (worker_id, limit),
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["metadata"] = json.loads(d.get("metadata") or "{}")
            results.append(d)
        return results

    def get_patterns(self, worker_id: str, limit: int = 20) -> list[dict]:
        """Shortcut: get recent pattern-type memories."""
        return self.get_recent(worker_id, memory_type="pattern", limit=limit)

    def cleanup_old(
        self,
        worker_id: str,
        days: int = 30,
        keep_patterns: bool = True,
    ) -> int:
        """Delete old memories for a worker. Optionally preserves patterns."""
        conn = _ensure_tables()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        if keep_patterns:
            cursor = conn.execute(
                """DELETE FROM worker_memories
                   WHERE worker_id = ?
                     AND memory_type != 'pattern'
                     AND created_at < ?""",
                (worker_id, cutoff),
            )
        else:
            cursor = conn.execute(
                """DELETE FROM worker_memories
                   WHERE worker_id = ? AND created_at < ?""",
                (worker_id, cutoff),
            )
        conn.commit()
        return cursor.rowcount


class MetricsStore:
    """CRUD for the worker_metrics table."""

    def update(
        self,
        worker_id: str,
        tokens: int = 0,
        cost: float = 0.0,
        response_ms: float = 0.0,
        success: bool = True,
    ) -> None:
        """Upsert worker metrics. Increments counters and recalculates averages."""
        conn = _ensure_tables()
        existing = conn.execute(
            "SELECT * FROM worker_metrics WHERE worker_id = ?", (worker_id,)
        ).fetchone()

        now = _now_iso()

        if existing is None:
            conn.execute(
                """INSERT INTO worker_metrics
                   (worker_id, tasks_completed, tasks_failed,
                    total_tokens, total_cost, avg_response_ms, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    worker_id,
                    1 if success else 0,
                    0 if success else 1,
                    tokens,
                    cost,
                    response_ms,
                    now,
                ),
            )
        else:
            completed = existing["tasks_completed"] + (1 if success else 0)
            failed = existing["tasks_failed"] + (0 if success else 1)
            new_tokens = existing["total_tokens"] + tokens
            new_cost = existing["total_cost"] + cost
            total_tasks = completed + failed
            # Running average for response time
            old_avg = existing["avg_response_ms"]
            old_total = existing["tasks_completed"] + existing["tasks_failed"]
            if old_total > 0:
                new_avg = (old_avg * old_total + response_ms) / total_tasks
            else:
                new_avg = response_ms

            conn.execute(
                """UPDATE worker_metrics
                   SET tasks_completed = ?, tasks_failed = ?,
                       total_tokens = ?, total_cost = ?,
                       avg_response_ms = ?, updated_at = ?
                   WHERE worker_id = ?""",
                (completed, failed, new_tokens, new_cost, new_avg, now, worker_id),
            )
        conn.commit()

    def get(self, worker_id: str) -> dict | None:
        """Get metrics for a single worker. Returns None if not found."""
        conn = _ensure_tables()
        row = conn.execute(
            "SELECT * FROM worker_metrics WHERE worker_id = ?", (worker_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_all(self) -> list[dict]:
        """Get metrics for all workers."""
        conn = _ensure_tables()
        rows = conn.execute(
            "SELECT * FROM worker_metrics ORDER BY total_cost DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_daily_cost(self, worker_id: str) -> float:
        """Get total cost from tasks completed today for a worker."""
        conn = _ensure_tables()
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        row = conn.execute(
            """SELECT COALESCE(SUM(total_cost), 0.0) as daily
               FROM worker_metrics
               WHERE worker_id = ? AND updated_at >= ?""",
            (worker_id, today_start),
        ).fetchone()
        return row["daily"]
