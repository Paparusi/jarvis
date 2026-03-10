# Company Phase 2: Autonomous Worker System — Implementation Plan

> **For Claude:** Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add 10 autonomous workers across 5 departments with per-worker memory, centralized task queue, cost controls, and performance metrics.

**Architecture:** Workers are persistent agents with own AgentLoop, polling a SQLite-backed TaskQueue. Department Heads decompose complex requests into sub-tasks, dispatch to workers, aggregate results. Per-worker memory enables learning over time. CostGuard enforces budget limits.

**Tech Stack:** Python 3.11, SQLite (existing `get_connection()`), asyncio, existing AgentLoop/ToolRegistry/ToolDefinition patterns.

---

## Task 1: Worker Data Layer (SQLite tables + CRUD)

**Files:**
- Create: `src/company/worker_store.py`
- Create: `tests/unit/test_worker_store.py`

### `src/company/worker_store.py` (~200 lines)

Tables and CRUD for worker tasks, memories, and metrics.

```python
"""Worker data layer — SQLite persistence for tasks, memories, metrics."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("worker_store")

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
    """Get DB connection and ensure tables exist."""
    global _tables_initialized
    conn = get_connection()
    if not _tables_initialized:
        _init_worker_tables(conn)
        _tables_initialized = True
    return conn


class TaskStore:
    """CRUD for worker_tasks table."""

    def create_task(
        self,
        department: str,
        instruction: str,
        session_id: str = "",
        worker_id: str | None = None,
        parent_task_id: int | None = None,
        priority: int = 5,
        context: dict | None = None,
    ) -> int:
        """Create a new task. Returns task id."""
        conn = _ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """INSERT INTO worker_tasks
               (department, worker_id, parent_task_id, priority, status,
                instruction, context, session_id, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
            (department, worker_id, parent_task_id, priority,
             instruction, json.dumps(context or {}), session_id, now),
        )
        conn.commit()
        return cursor.lastrowid

    def claim_task(self, worker_id: str, department: str) -> dict | None:
        """Claim oldest pending task for this department. Returns task dict or None."""
        conn = _ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        # Find oldest pending task for this department (or specifically assigned)
        row = conn.execute(
            """SELECT * FROM worker_tasks
               WHERE status = 'pending'
               AND department = ?
               AND (worker_id IS NULL OR worker_id = ?)
               ORDER BY priority ASC, id ASC
               LIMIT 1""",
            (department, worker_id),
        ).fetchone()
        if not row:
            return None
        task_id = row["id"]
        conn.execute(
            "UPDATE worker_tasks SET status = 'assigned', worker_id = ?, assigned_at = ? WHERE id = ?",
            (worker_id, now, task_id),
        )
        conn.commit()
        return dict(row)

    def start_task(self, task_id: int) -> None:
        """Mark task as in_progress."""
        conn = _ensure_tables()
        conn.execute(
            "UPDATE worker_tasks SET status = 'in_progress' WHERE id = ?",
            (task_id,),
        )
        conn.commit()

    def complete_task(self, task_id: int, result: str) -> None:
        """Mark task as completed with result."""
        conn = _ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE worker_tasks SET status = 'completed', result = ?, completed_at = ? WHERE id = ?",
            (result, now, task_id),
        )
        conn.commit()

    def fail_task(self, task_id: int, error: str) -> None:
        """Mark task as failed with error."""
        conn = _ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE worker_tasks SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
            (error, now, task_id),
        )
        conn.commit()

    def escalate_task(self, task_id: int, error: str) -> None:
        """Mark task as escalated (worker couldn't handle)."""
        conn = _ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE worker_tasks SET status = 'escalated', error = ?, completed_at = ? WHERE id = ?",
            (error, now, task_id),
        )
        conn.commit()

    def get_task(self, task_id: int) -> dict | None:
        """Get task by id."""
        conn = _ensure_tables()
        row = conn.execute("SELECT * FROM worker_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def get_pending_count(self, department: str) -> int:
        """Count pending tasks for a department."""
        conn = _ensure_tables()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM worker_tasks WHERE department = ? AND status = 'pending'",
            (department,),
        ).fetchone()
        return row["cnt"]

    def get_worker_tasks(self, worker_id: str, limit: int = 20) -> list[dict]:
        """Get recent tasks for a worker."""
        conn = _ensure_tables()
        rows = conn.execute(
            "SELECT * FROM worker_tasks WHERE worker_id = ? ORDER BY id DESC LIMIT ?",
            (worker_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def cleanup_old(self, days: int = 30) -> int:
        """Delete completed/failed tasks older than N days."""
        conn = _ensure_tables()
        cutoff = datetime.now(timezone.utc).isoformat()[:10]  # simplified
        cursor = conn.execute(
            "DELETE FROM worker_tasks WHERE status IN ('completed', 'failed') AND created_at < date(?, ?)",
            (cutoff, f'-{days} days'),
        )
        conn.commit()
        return cursor.rowcount


class WorkerMemoryStore:
    """CRUD for worker_memories table."""

    def log(self, worker_id: str, memory_type: str, content: str, metadata: dict | None = None) -> int:
        """Log a memory entry. Returns row id."""
        conn = _ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """INSERT INTO worker_memories (worker_id, memory_type, content, metadata, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (worker_id, memory_type, content, json.dumps(metadata or {}), now),
        )
        conn.commit()
        return cursor.lastrowid

    def get_recent(self, worker_id: str, memory_type: str | None = None, limit: int = 10) -> list[dict]:
        """Get recent memories for a worker, optionally filtered by type."""
        conn = _ensure_tables()
        if memory_type:
            rows = conn.execute(
                "SELECT * FROM worker_memories WHERE worker_id = ? AND memory_type = ? ORDER BY id DESC LIMIT ?",
                (worker_id, memory_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM worker_memories WHERE worker_id = ? ORDER BY id DESC LIMIT ?",
                (worker_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_patterns(self, worker_id: str, limit: int = 10) -> list[dict]:
        """Get learned patterns (memory_type='pattern') for context injection."""
        return self.get_recent(worker_id, memory_type="pattern", limit=limit)

    def cleanup_old(self, worker_id: str, days: int = 30, keep_patterns: bool = True) -> int:
        """Delete old memories. Optionally keep 'pattern' type forever."""
        conn = _ensure_tables()
        extra = " AND memory_type != 'pattern'" if keep_patterns else ""
        cursor = conn.execute(
            f"DELETE FROM worker_memories WHERE worker_id = ? AND created_at < date('now', '-{days} days'){extra}",
            (worker_id,),
        )
        conn.commit()
        return cursor.rowcount


class MetricsStore:
    """CRUD for worker_metrics table."""

    def update(self, worker_id: str, tokens: int = 0, cost: float = 0.0,
               response_ms: float = 0.0, success: bool = True) -> None:
        """Update metrics for a worker after task completion."""
        conn = _ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        # Upsert
        existing = conn.execute(
            "SELECT * FROM worker_metrics WHERE worker_id = ?", (worker_id,)
        ).fetchone()
        if existing:
            completed = existing["tasks_completed"] + (1 if success else 0)
            failed = existing["tasks_failed"] + (0 if success else 1)
            total_tokens = existing["total_tokens"] + tokens
            total_cost = existing["total_cost"] + cost
            total_tasks = completed + failed
            avg_ms = ((existing["avg_response_ms"] * (total_tasks - 1)) + response_ms) / total_tasks if total_tasks > 0 else 0
            conn.execute(
                """UPDATE worker_metrics
                   SET tasks_completed = ?, tasks_failed = ?, total_tokens = ?,
                       total_cost = ?, avg_response_ms = ?, updated_at = ?
                   WHERE worker_id = ?""",
                (completed, failed, total_tokens, total_cost, avg_ms, now, worker_id),
            )
        else:
            conn.execute(
                """INSERT INTO worker_metrics
                   (worker_id, tasks_completed, tasks_failed, total_tokens,
                    total_cost, avg_response_ms, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (worker_id, 1 if success else 0, 0 if success else 1,
                 tokens, cost, response_ms, now),
            )
        conn.commit()

    def get(self, worker_id: str) -> dict | None:
        """Get metrics for a worker."""
        conn = _ensure_tables()
        row = conn.execute(
            "SELECT * FROM worker_metrics WHERE worker_id = ?", (worker_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_all(self) -> list[dict]:
        """Get metrics for all workers."""
        conn = _ensure_tables()
        rows = conn.execute("SELECT * FROM worker_metrics ORDER BY worker_id").fetchall()
        return [dict(r) for r in rows]

    def get_daily_cost(self, worker_id: str) -> float:
        """Get today's cost for a worker (sum from worker_tasks)."""
        conn = _ensure_tables()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute(
            """SELECT COALESCE(SUM(
                 CAST(json_extract(context, '$.cost') AS REAL)
               ), 0.0) as daily_cost
               FROM worker_tasks
               WHERE worker_id = ? AND created_at >= ? AND status = 'completed'""",
            (worker_id, today),
        ).fetchone()
        return row["daily_cost"] if row else 0.0
```

### `tests/unit/test_worker_store.py` (~25 tests)

```python
"""Tests for worker data layer."""
import json
import sqlite3
from unittest.mock import patch

import pytest

# Patch get_connection to use in-memory DB
def _make_test_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


class TestTaskStore:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import src.company.worker_store as ws
        ws._tables_initialized = False
        self.conn = _make_test_conn(tmp_path)
        self.patcher = patch("src.company.worker_store.get_connection", return_value=self.conn)
        self.patcher.start()
        self.store = ws.TaskStore()
        yield
        self.patcher.stop()

    def test_create_task(self):
        tid = self.store.create_task("finance", "Analyze XAUUSD")
        assert tid == 1

    def test_create_with_all_fields(self):
        tid = self.store.create_task(
            "security", "Scan target", session_id="sess1",
            worker_id="security.pen_tester", priority=1, context={"url": "example.com"}
        )
        task = self.store.get_task(tid)
        assert task["department"] == "security"
        assert task["worker_id"] == "security.pen_tester"
        assert task["priority"] == 1
        assert json.loads(task["context"])["url"] == "example.com"

    def test_claim_task(self):
        self.store.create_task("finance", "Task 1")
        task = self.store.claim_task("finance.analyst", "finance")
        assert task is not None
        assert task["instruction"] == "Task 1"
        # Verify status changed
        updated = self.store.get_task(task["id"])
        assert updated["status"] == "assigned"
        assert updated["worker_id"] == "finance.analyst"

    def test_claim_empty(self):
        result = self.store.claim_task("finance.analyst", "finance")
        assert result is None

    def test_claim_respects_department(self):
        self.store.create_task("security", "Scan vuln")
        result = self.store.claim_task("finance.analyst", "finance")
        assert result is None

    def test_claim_priority_order(self):
        self.store.create_task("finance", "Low priority", priority=10)
        self.store.create_task("finance", "High priority", priority=1)
        task = self.store.claim_task("finance.analyst", "finance")
        assert task["instruction"] == "High priority"

    def test_complete_task(self):
        tid = self.store.create_task("finance", "Analyze")
        self.store.complete_task(tid, "Analysis: bullish")
        task = self.store.get_task(tid)
        assert task["status"] == "completed"
        assert task["result"] == "Analysis: bullish"
        assert task["completed_at"] is not None

    def test_fail_task(self):
        tid = self.store.create_task("finance", "Analyze")
        self.store.fail_task(tid, "MT5 connection failed")
        task = self.store.get_task(tid)
        assert task["status"] == "failed"
        assert task["error"] == "MT5 connection failed"

    def test_escalate_task(self):
        tid = self.store.create_task("finance", "Complex query")
        self.store.escalate_task(tid, "Beyond my capability")
        task = self.store.get_task(tid)
        assert task["status"] == "escalated"

    def test_start_task(self):
        tid = self.store.create_task("finance", "Work")
        self.store.start_task(tid)
        task = self.store.get_task(tid)
        assert task["status"] == "in_progress"

    def test_get_pending_count(self):
        self.store.create_task("finance", "Task 1")
        self.store.create_task("finance", "Task 2")
        self.store.create_task("security", "Task 3")
        assert self.store.get_pending_count("finance") == 2
        assert self.store.get_pending_count("security") == 1

    def test_get_worker_tasks(self):
        self.store.create_task("finance", "T1", worker_id="finance.analyst")
        self.store.create_task("finance", "T2", worker_id="finance.analyst")
        tasks = self.store.get_worker_tasks("finance.analyst")
        assert len(tasks) == 2


class TestWorkerMemoryStore:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import src.company.worker_store as ws
        ws._tables_initialized = False
        self.conn = _make_test_conn(tmp_path)
        self.patcher = patch("src.company.worker_store.get_connection", return_value=self.conn)
        self.patcher.start()
        self.store = ws.WorkerMemoryStore()
        yield
        self.patcher.stop()

    def test_log_memory(self):
        mid = self.store.log("finance.analyst", "task_result", "Analyzed XAUUSD: bullish")
        assert mid == 1

    def test_log_with_metadata(self):
        mid = self.store.log("finance.analyst", "pattern", "XAUUSD rejects 2350 zone",
                             metadata={"confidence": 0.8})
        memories = self.store.get_recent("finance.analyst")
        assert len(memories) == 1
        assert json.loads(memories[0]["metadata"])["confidence"] == 0.8

    def test_get_recent_filtered(self):
        self.store.log("finance.analyst", "task_result", "Result 1")
        self.store.log("finance.analyst", "pattern", "Pattern 1")
        self.store.log("finance.analyst", "task_result", "Result 2")
        results = self.store.get_recent("finance.analyst", memory_type="task_result")
        assert len(results) == 2

    def test_get_patterns(self):
        self.store.log("finance.analyst", "task_result", "Result")
        self.store.log("finance.analyst", "pattern", "Important pattern")
        patterns = self.store.get_patterns("finance.analyst")
        assert len(patterns) == 1
        assert patterns[0]["content"] == "Important pattern"

    def test_isolation_between_workers(self):
        self.store.log("finance.analyst", "task_result", "Finance stuff")
        self.store.log("security.scanner", "task_result", "Security stuff")
        assert len(self.store.get_recent("finance.analyst")) == 1
        assert len(self.store.get_recent("security.scanner")) == 1


class TestMetricsStore:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import src.company.worker_store as ws
        ws._tables_initialized = False
        self.conn = _make_test_conn(tmp_path)
        self.patcher = patch("src.company.worker_store.get_connection", return_value=self.conn)
        self.patcher.start()
        self.store = ws.MetricsStore()
        yield
        self.patcher.stop()

    def test_update_creates_new(self):
        self.store.update("finance.analyst", tokens=1000, cost=0.05, response_ms=500, success=True)
        m = self.store.get("finance.analyst")
        assert m["tasks_completed"] == 1
        assert m["tasks_failed"] == 0
        assert m["total_tokens"] == 1000

    def test_update_increments(self):
        self.store.update("finance.analyst", tokens=1000, cost=0.05, success=True)
        self.store.update("finance.analyst", tokens=500, cost=0.02, success=True)
        m = self.store.get("finance.analyst")
        assert m["tasks_completed"] == 2
        assert m["total_tokens"] == 1500

    def test_update_failure(self):
        self.store.update("finance.analyst", success=False)
        m = self.store.get("finance.analyst")
        assert m["tasks_failed"] == 1
        assert m["tasks_completed"] == 0

    def test_get_all(self):
        self.store.update("finance.analyst", success=True)
        self.store.update("security.scanner", success=True)
        all_m = self.store.get_all()
        assert len(all_m) == 2

    def test_get_nonexistent(self):
        assert self.store.get("nonexistent") is None
```

---

## Task 2: Worker Base Class

**Files:**
- Create: `src/company/worker.py`
- Create: `tests/unit/test_worker.py`

### `src/company/worker.py` (~200 lines)

```python
"""Worker — autonomous agent with own event loop, memory, and task queue."""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum

from src.company.worker_store import MetricsStore, TaskStore, WorkerMemoryStore
from src.gateway.models import AgentResponse, Channel, SessionState
from src.intelligence.agent_loop import AgentLoop
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("worker")


class WorkerStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class Worker:
    """Autonomous agent — polls TaskQueue, executes, reports."""

    def __init__(
        self,
        worker_id: str,
        name: str,
        department: str,
        role: str,
        tools: set[str],
        tool_registry: ToolRegistry,
        assembler,
        tracer,
        cloud_model: str = "claude-sonnet-4-20250514",
        poll_interval: float = 2.0,
        max_iterations: int = 8,
        task_timeout: float = 120.0,
    ) -> None:
        self.worker_id = worker_id
        self.name = name
        self.department = department
        self.role = role
        self.tools = tools
        self.status = WorkerStatus.IDLE
        self._poll_interval = poll_interval
        self._task_timeout = task_timeout
        self._running = False
        self._task: asyncio.Task | None = None

        # Own AgentLoop instance
        self._agent_loop = AgentLoop(
            tool_registry=tool_registry,
            assembler=assembler,
            tracer=tracer,
            cloud_model=cloud_model,
            max_iterations=max_iterations,
        )

        # Data stores
        self._task_store = TaskStore()
        self._memory_store = WorkerMemoryStore()
        self._metrics_store = MetricsStore()

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the worker event loop."""
        if self._running:
            return
        self._running = True
        self.status = WorkerStatus.IDLE
        self._task = asyncio.create_task(self._run_loop())
        log.info("worker_started", worker=self.worker_id)

    async def stop(self) -> None:
        """Stop the worker event loop."""
        self._running = False
        self.status = WorkerStatus.OFFLINE
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("worker_stopped", worker=self.worker_id)

    async def _run_loop(self) -> None:
        """Main event loop — poll for tasks, execute, report."""
        while self._running:
            try:
                task = self._task_store.claim_task(self.worker_id, self.department)
                if task:
                    await self._execute_task(task)
                else:
                    await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("worker_loop_error", worker=self.worker_id, error=str(exc))
                self.status = WorkerStatus.ERROR
                await asyncio.sleep(self._poll_interval * 5)
                self.status = WorkerStatus.IDLE

    async def _execute_task(self, task: dict) -> None:
        """Execute a single task through AgentLoop."""
        task_id = task["id"]
        self.status = WorkerStatus.BUSY
        self._task_store.start_task(task_id)
        start_time = time.time()

        try:
            # Build context from worker memory
            context = self._build_context(task)

            # Create session for this task
            session = SessionState(
                channel=Channel.CLI,
                user_id=f"worker_{self.worker_id}",
                session_id=task.get("session_id", ""),
            )

            # Execute with timeout
            result = await asyncio.wait_for(
                self._agent_loop.run(
                    session=session,
                    user_message=task["instruction"],
                    memory_context=context,
                    skill_context=self.role,
                    use_tools=True,
                    tool_filter=self.tools,
                ),
                timeout=self._task_timeout,
            )

            elapsed_ms = (time.time() - start_time) * 1000

            # Complete task
            self._task_store.complete_task(task_id, result.content)

            # Log to worker memory
            self._memory_store.log(
                self.worker_id, "task_result",
                f"Task: {task['instruction'][:200]}\nResult: {result.content[:500]}",
                metadata={"task_id": task_id, "tokens": result.tokens_in + result.tokens_out},
            )

            # Update metrics
            self._metrics_store.update(
                self.worker_id,
                tokens=result.tokens_in + result.tokens_out,
                cost=result.cost_usd,
                response_ms=elapsed_ms,
                success=True,
            )

            log.info("worker_task_completed", worker=self.worker_id,
                     task_id=task_id, ms=int(elapsed_ms))

        except asyncio.TimeoutError:
            self._task_store.fail_task(task_id, "Task timed out")
            self._metrics_store.update(self.worker_id, success=False)
            log.warning("worker_task_timeout", worker=self.worker_id, task_id=task_id)

        except Exception as exc:
            self._task_store.fail_task(task_id, str(exc))
            self._metrics_store.update(self.worker_id, success=False)
            log.error("worker_task_failed", worker=self.worker_id,
                      task_id=task_id, error=str(exc))

        finally:
            self.status = WorkerStatus.IDLE

    def _build_context(self, task: dict) -> str:
        """Build context string from worker memory for LLM injection."""
        parts = [f"## Worker: {self.name} ({self.worker_id})"]

        # Recent task results
        recent = self._memory_store.get_recent(self.worker_id, "task_result", limit=5)
        if recent:
            parts.append("### Recent Work:")
            for m in recent:
                parts.append(f"- {m['content'][:150]}")

        # Learned patterns
        patterns = self._memory_store.get_patterns(self.worker_id, limit=5)
        if patterns:
            parts.append("### Learned Patterns:")
            for p in patterns:
                parts.append(f"- {p['content']}")

        # Task context from parent
        try:
            ctx = json.loads(task.get("context", "{}"))
            if ctx:
                parts.append(f"### Task Context: {json.dumps(ctx, indent=2)[:500]}")
        except (json.JSONDecodeError, TypeError):
            pass

        return "\n\n".join(parts)

    def learn_pattern(self, pattern: str, metadata: dict | None = None) -> int:
        """Store a learned insight for future context injection."""
        return self._memory_store.log(
            self.worker_id, "pattern", pattern, metadata=metadata,
        )

    def get_metrics(self) -> dict | None:
        """Get this worker's performance metrics."""
        return self._metrics_store.get(self.worker_id)

    def get_status_dict(self) -> dict:
        """Get worker status as dict for reporting."""
        metrics = self.get_metrics() or {}
        return {
            "worker_id": self.worker_id,
            "name": self.name,
            "department": self.department,
            "status": self.status.value,
            "tools_count": len(self.tools),
            "tasks_completed": metrics.get("tasks_completed", 0),
            "tasks_failed": metrics.get("tasks_failed", 0),
            "total_cost": metrics.get("total_cost", 0.0),
        }

    async def execute_direct(self, instruction: str, session_id: str = "") -> AgentResponse:
        """Execute a task directly (bypass queue). Used by DepartmentHead for sync calls."""
        session = SessionState(
            channel=Channel.CLI,
            user_id=f"worker_{self.worker_id}",
            session_id=session_id,
        )
        context = self._build_context({"instruction": instruction, "context": "{}"})
        result = await self._agent_loop.run(
            session=session,
            user_message=instruction,
            memory_context=context,
            skill_context=self.role,
            use_tools=True,
            tool_filter=self.tools,
        )
        # Log and track
        self._memory_store.log(
            self.worker_id, "task_result",
            f"Task: {instruction[:200]}\nResult: {result.content[:500]}",
        )
        self._metrics_store.update(
            self.worker_id,
            tokens=result.tokens_in + result.tokens_out,
            cost=result.cost_usd,
            success=True,
        )
        return result
```

### `tests/unit/test_worker.py` (~20 tests)

```python
"""Tests for Worker autonomous agent."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.company.worker import Worker, WorkerStatus
from src.gateway.models import AgentResponse


def _mock_response(content="test response"):
    return AgentResponse(
        request_id="req1", session_id="sess1",
        content=content, tokens_in=100, tokens_out=50, cost_usd=0.01,
    )


class TestWorkerInit:
    @pytest.fixture
    def deps(self):
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        assembler = MagicMock()
        tracer = MagicMock()
        return registry, assembler, tracer

    def test_create_worker(self, deps):
        registry, assembler, tracer = deps
        w = Worker(
            worker_id="finance.analyst",
            name="Market Analyst",
            department="finance",
            role="Analyze XAUUSD market",
            tools={"mt5_candles", "web_search"},
            tool_registry=registry,
            assembler=assembler,
            tracer=tracer,
        )
        assert w.worker_id == "finance.analyst"
        assert w.name == "Market Analyst"
        assert w.status == WorkerStatus.IDLE
        assert not w.is_running

    def test_tools_stored(self, deps):
        registry, assembler, tracer = deps
        tools = {"mt5_candles", "mt5_analyze", "web_search"}
        w = Worker("f.a", "Analyst", "finance", "role", tools, registry, assembler, tracer)
        assert w.tools == tools

    def test_default_params(self, deps):
        registry, assembler, tracer = deps
        w = Worker("f.a", "Analyst", "finance", "role", set(), registry, assembler, tracer)
        assert w._poll_interval == 2.0
        assert w._task_timeout == 120.0


class TestWorkerLifecycle:
    @pytest.fixture
    def worker(self):
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        w = Worker("f.a", "Analyst", "finance", "role", set(),
                   registry, MagicMock(), MagicMock())
        return w

    def test_start(self, worker):
        worker.start()
        assert worker.is_running
        assert worker.status == WorkerStatus.IDLE
        # Cleanup
        worker._running = False
        if worker._task:
            worker._task.cancel()

    @pytest.mark.asyncio
    async def test_stop(self, worker):
        worker.start()
        await worker.stop()
        assert not worker.is_running
        assert worker.status == WorkerStatus.OFFLINE


class TestWorkerExecute:
    @pytest.fixture
    def worker(self):
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        w = Worker("f.a", "Analyst", "finance", "role", {"web_search"},
                   registry, MagicMock(), MagicMock())
        w._agent_loop.run = AsyncMock(return_value=_mock_response("Analysis done"))
        w._task_store = MagicMock()
        w._memory_store = MagicMock()
        w._metrics_store = MagicMock()
        return w

    @pytest.mark.asyncio
    async def test_execute_direct(self, worker):
        result = await worker.execute_direct("Analyze XAUUSD")
        assert result.content == "Analysis done"
        worker._agent_loop.run.assert_called_once()
        call_kwargs = worker._agent_loop.run.call_args.kwargs
        assert call_kwargs["tool_filter"] == {"web_search"}

    @pytest.mark.asyncio
    async def test_execute_direct_logs_memory(self, worker):
        await worker.execute_direct("Analyze XAUUSD")
        worker._memory_store.log.assert_called_once()
        args = worker._memory_store.log.call_args
        assert args[0][0] == "f.a"  # worker_id
        assert args[0][1] == "task_result"  # memory_type

    @pytest.mark.asyncio
    async def test_execute_direct_updates_metrics(self, worker):
        await worker.execute_direct("Analyze XAUUSD")
        worker._metrics_store.update.assert_called_once()
        call_kwargs = worker._metrics_store.update.call_args.kwargs
        assert call_kwargs["success"] is True

    @pytest.mark.asyncio
    async def test_execute_task_from_queue(self, worker):
        task = {
            "id": 1, "instruction": "Analyze gold", "session_id": "s1",
            "context": "{}",
        }
        await worker._execute_task(task)
        worker._task_store.start_task.assert_called_with(1)
        worker._task_store.complete_task.assert_called_once()
        assert worker.status == WorkerStatus.IDLE

    @pytest.mark.asyncio
    async def test_execute_task_failure(self, worker):
        worker._agent_loop.run = AsyncMock(side_effect=RuntimeError("LLM error"))
        task = {"id": 2, "instruction": "Bad task", "session_id": "", "context": "{}"}
        await worker._execute_task(task)
        worker._task_store.fail_task.assert_called_once()
        assert worker.status == WorkerStatus.IDLE


class TestWorkerContext:
    @pytest.fixture
    def worker(self):
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        w = Worker("f.a", "Analyst", "finance", "role", set(),
                   registry, MagicMock(), MagicMock())
        w._memory_store = MagicMock()
        w._memory_store.get_recent.return_value = [
            {"content": "Previous analysis: bullish on XAUUSD"},
        ]
        w._memory_store.get_patterns.return_value = [
            {"content": "XAUUSD reacts to NFP strongly"},
        ]
        return w

    def test_build_context_includes_recent(self, worker):
        ctx = worker._build_context({"instruction": "test", "context": "{}"})
        assert "Previous analysis" in ctx

    def test_build_context_includes_patterns(self, worker):
        ctx = worker._build_context({"instruction": "test", "context": "{}"})
        assert "NFP" in ctx

    def test_build_context_includes_worker_name(self, worker):
        ctx = worker._build_context({"instruction": "test", "context": "{}"})
        assert "Analyst" in ctx

    def test_learn_pattern(self, worker):
        worker.learn_pattern("Gold rises before FOMC")
        worker._memory_store.log.assert_called_once()

    def test_get_status_dict(self, worker):
        worker._metrics_store.get.return_value = {
            "tasks_completed": 5, "tasks_failed": 1, "total_cost": 0.25
        }
        status = worker.get_status_dict()
        assert status["worker_id"] == "f.a"
        assert status["tasks_completed"] == 5
        assert status["status"] == "idle"
```

---

## Task 3: Worker Registry & Cost Guard

**Files:**
- Create: `src/company/worker_registry.py`
- Create: `tests/unit/test_worker_registry.py`

### `src/company/worker_registry.py` (~180 lines)

```python
"""WorkerRegistry — manages all workers + CostGuard for budget enforcement."""

from __future__ import annotations

from datetime import datetime, timezone

from src.company.worker import Worker, WorkerStatus
from src.company.worker_store import MetricsStore
from src.utils.logging import get_logger

log = get_logger("worker_registry")


class CostGuard:
    """Enforce API cost limits per worker and globally."""

    MAX_DAILY_COST = 10.0          # $10/day total across all workers
    MAX_TASK_COST = 2.0            # $2 per single task
    MAX_WORKER_DAILY_COST = 3.0    # $3/worker/day

    def __init__(self) -> None:
        self._metrics = MetricsStore()
        self._daily_totals: dict[str, float] = {}  # worker_id -> today's cost
        self._last_reset: str = ""

    def can_execute(self, worker_id: str) -> bool:
        """Check if worker can afford another task."""
        self._maybe_reset()
        worker_cost = self._daily_totals.get(worker_id, 0.0)
        total_cost = sum(self._daily_totals.values())
        if worker_cost >= self.MAX_WORKER_DAILY_COST:
            log.warning("cost_guard_worker_limit", worker=worker_id, cost=worker_cost)
            return False
        if total_cost >= self.MAX_DAILY_COST:
            log.warning("cost_guard_daily_limit", total=total_cost)
            return False
        return True

    def log_cost(self, worker_id: str, cost: float) -> None:
        """Track cost after task completion."""
        self._maybe_reset()
        self._daily_totals[worker_id] = self._daily_totals.get(worker_id, 0.0) + cost

    def get_daily_usage(self) -> dict:
        """Get today's cost breakdown."""
        self._maybe_reset()
        return {
            "total": sum(self._daily_totals.values()),
            "limit": self.MAX_DAILY_COST,
            "per_worker": dict(self._daily_totals),
        }

    def _maybe_reset(self) -> None:
        """Reset daily totals at midnight UTC."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_reset:
            self._daily_totals.clear()
            self._last_reset = today


# --- Worker definitions (roster) ---

WORKER_ROSTER: list[dict] = [
    # Finance
    {
        "worker_id": "finance.market_analyst",
        "name": "Market Analyst",
        "department": "finance",
        "role": (
            "Bạn là Market Analyst chuyên phân tích XAUUSD.\n"
            "Nhiệm vụ: phân tích kỹ thuật (chart patterns, volume profile, SMC), "
            "phân tích cơ bản (tin tức, events), đánh giá market regime.\n"
            "Luôn đưa ra bias (bullish/bearish/neutral) kèm reasoning."
        ),
        "tools": {
            "mt5_candles", "mt5_analyze", "mt5_smc", "mt5_price",
            "technical_indicators", "market_session", "trading_calendar",
            "web_search", "fetch_url",
        },
    },
    {
        "worker_id": "finance.trader",
        "name": "Trader",
        "department": "finance",
        "role": (
            "Bạn là Trader chuyên thực thi giao dịch XAUUSD trên MT5.\n"
            "Nhiệm vụ: đặt lệnh, quản lý positions, pending orders, "
            "theo dõi P&L, close positions khi cần.\n"
            "Luôn tuân thủ risk management rules."
        ),
        "tools": {
            "mt5_order", "mt5_close", "mt5_positions", "mt5_account",
            "mt5_price", "mt5_history",
            "trade_plan", "trade_status", "trade_config",
            "trade_control", "trade_pending",
        },
    },
    {
        "worker_id": "finance.crypto_specialist",
        "name": "Crypto Specialist",
        "department": "finance",
        "role": (
            "Bạn là Crypto Specialist chuyên về airdrop hunting và DeFi research.\n"
            "Nhiệm vụ: tìm airdrop campaigns mới, đánh giá tiềm năng, "
            "theo dõi crypto market trends, phân tích tokenomics.\n"
            "Focus: early-stage airdrops, DeFi protocols, market opportunities."
        ),
        "tools": {"web_search", "browse_web", "deep_search", "fetch_url"},
    },
    # Security
    {
        "worker_id": "security.pen_tester",
        "name": "Pen Tester",
        "department": "security",
        "role": (
            "Bạn là Penetration Tester chuyên kiểm tra bảo mật.\n"
            "Nhiệm vụ: scan vulnerabilities, test web app security, "
            "tìm XSS/SQLi/SSRF, report findings.\n"
            "Luôn document tất cả findings chi tiết."
        ),
        "tools": {
            "subdomain_enum", "http_headers", "tech_detect",
            "xss_scan", "sqli_scan", "ssrf_scan", "nuclei_scan",
            "port_scan", "web_search", "fetch_url",
        },
    },
    {
        "worker_id": "security.researcher",
        "name": "Security Researcher",
        "department": "security",
        "role": (
            "Bạn là Security Researcher chuyên nghiên cứu vulnerabilities.\n"
            "Nhiệm vụ: CVE tracking, threat intelligence, security advisories, "
            "đánh giá impact, recommend patches.\n"
            "Focus: actionable intelligence, not just raw data."
        ),
        "tools": {
            "cve_lookup", "reverse_dns", "tech_detect", "http_headers",
            "web_search", "fetch_url", "browse_web",
        },
    },
    # Engineering
    {
        "worker_id": "engineering.developer",
        "name": "Developer",
        "department": "engineering",
        "role": (
            "Bạn là Developer chuyên phân tích và viết code.\n"
            "Nhiệm vụ: code review, debugging, code generation, "
            "refactoring, dependency analysis.\n"
            "Focus: clean code, SOLID principles, testing."
        ),
        "tools": {
            "ast_analyze", "complexity_check", "code_search",
            "diff_summary", "dependency_graph",
            "run_python", "code_exec", "read_file", "write_file", "list_dir",
        },
    },
    {
        "worker_id": "engineering.devops",
        "name": "DevOps",
        "department": "engineering",
        "role": (
            "Bạn là DevOps Engineer chuyên vận hành và tự động hóa.\n"
            "Nhiệm vụ: deployment, monitoring, CI/CD, automation scripts, "
            "system administration.\n"
            "Focus: reliability, automation, efficiency."
        ),
        "tools": {
            "run_python", "code_exec", "read_file", "write_file", "list_dir",
        },
    },
    # Research
    {
        "worker_id": "research.product_researcher",
        "name": "Product Researcher",
        "department": "research",
        "role": (
            "Bạn là Product Researcher chuyên tìm kiếm cơ hội kinh doanh mới.\n"
            "Nhiệm vụ: market research, competitor analysis, trend spotting, "
            "đánh giá product-market fit, tìm niches có tiềm năng.\n"
            "Focus: actionable insights, data-driven recommendations."
        ),
        "tools": {"web_search", "browse_web", "deep_search", "fetch_url"},
    },
    {
        "worker_id": "research.data_analyst",
        "name": "Data Analyst",
        "department": "research",
        "role": (
            "Bạn là Data Analyst chuyên xử lý và phân tích dữ liệu.\n"
            "Nhiệm vụ: data processing, statistical analysis, visualization, "
            "extract insights từ raw data.\n"
            "Focus: accuracy, clear presentation, actionable metrics."
        ),
        "tools": {
            "csv_analyze", "json_query", "sqlite_query",
            "text_stats", "json_transform", "run_python",
        },
    },
    # Operations
    {
        "worker_id": "operations.office_manager",
        "name": "Office Manager",
        "department": "operations",
        "role": (
            "Bạn là Office Manager quản lý công việc hàng ngày.\n"
            "Nhiệm vụ: scheduling, reminders, daily digest, "
            "communications, media processing (TTS, image analysis).\n"
            "Focus: organization, timeliness, clear communication."
        ),
        "tools": {
            "set_reminder", "list_reminders", "daily_digest",
            "text_to_speech", "analyze_image", "ocr_image",
        },
    },
]


class WorkerRegistry:
    """Registry of all workers with lifecycle management."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        assembler,
        tracer,
        cloud_model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._workers: dict[str, Worker] = {}
        self._cost_guard = CostGuard()

        # Create all workers from roster
        for spec in WORKER_ROSTER:
            worker = Worker(
                worker_id=spec["worker_id"],
                name=spec["name"],
                department=spec["department"],
                role=spec["role"],
                tools=spec["tools"],
                tool_registry=tool_registry,
                assembler=assembler,
                tracer=tracer,
                cloud_model=cloud_model,
            )
            self._workers[spec["worker_id"]] = worker

        log.info("worker_registry_created", workers=len(self._workers))

    def get(self, worker_id: str) -> Worker | None:
        """Get worker by id."""
        return self._workers.get(worker_id)

    def get_department_workers(self, department: str) -> list[Worker]:
        """Get all workers in a department."""
        return [w for w in self._workers.values() if w.department == department]

    def get_all(self) -> list[Worker]:
        """Get all workers."""
        return list(self._workers.values())

    def start_all(self) -> None:
        """Start all worker event loops."""
        for worker in self._workers.values():
            worker.start()
        log.info("all_workers_started", count=len(self._workers))

    async def stop_all(self) -> None:
        """Stop all worker event loops."""
        for worker in self._workers.values():
            await worker.stop()
        log.info("all_workers_stopped")

    def start_department(self, department: str) -> int:
        """Start all workers in a department. Returns count started."""
        workers = self.get_department_workers(department)
        for w in workers:
            w.start()
        return len(workers)

    async def stop_department(self, department: str) -> int:
        """Stop all workers in a department. Returns count stopped."""
        workers = self.get_department_workers(department)
        for w in workers:
            await w.stop()
        return len(workers)

    @property
    def cost_guard(self) -> CostGuard:
        return self._cost_guard

    def get_status(self) -> dict:
        """Get full company status with all workers."""
        departments: dict[str, list[dict]] = {}
        for w in self._workers.values():
            dept = w.department
            if dept not in departments:
                departments[dept] = []
            departments[dept].append(w.get_status_dict())
        return {
            "total_workers": len(self._workers),
            "departments": departments,
            "cost": self._cost_guard.get_daily_usage(),
        }


# Need this import at module level for type hints in WorkerRegistry.__init__
from src.tools.base import ToolRegistry  # noqa: E402
```

### `tests/unit/test_worker_registry.py` (~15 tests)

```python
"""Tests for WorkerRegistry and CostGuard."""
from unittest.mock import MagicMock

import pytest

from src.company.worker_registry import CostGuard, WorkerRegistry, WORKER_ROSTER


class TestCostGuard:
    def test_initial_can_execute(self):
        cg = CostGuard()
        assert cg.can_execute("finance.analyst")

    def test_worker_limit(self):
        cg = CostGuard()
        cg.log_cost("finance.analyst", 3.0)
        assert not cg.can_execute("finance.analyst")

    def test_other_worker_unaffected(self):
        cg = CostGuard()
        cg.log_cost("finance.analyst", 3.0)
        assert cg.can_execute("security.scanner")

    def test_global_limit(self):
        cg = CostGuard()
        for i in range(5):
            cg.log_cost(f"worker_{i}", 2.0)  # Total $10
        assert not cg.can_execute("worker_new")

    def test_daily_usage(self):
        cg = CostGuard()
        cg.log_cost("finance.analyst", 1.5)
        usage = cg.get_daily_usage()
        assert usage["total"] == 1.5
        assert usage["per_worker"]["finance.analyst"] == 1.5


class TestWorkerRegistry:
    @pytest.fixture
    def registry(self):
        tool_reg = MagicMock()
        tool_reg.get_all.return_value = []
        tool_reg.get_schemas.return_value = []
        tool_reg.get_filtered_schemas.return_value = []
        return WorkerRegistry(tool_reg, MagicMock(), MagicMock())

    def test_creates_all_workers(self, registry):
        assert len(registry.get_all()) == len(WORKER_ROSTER)

    def test_get_by_id(self, registry):
        w = registry.get("finance.market_analyst")
        assert w is not None
        assert w.name == "Market Analyst"

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    def test_get_department_workers(self, registry):
        finance = registry.get_department_workers("finance")
        assert len(finance) == 3
        names = {w.name for w in finance}
        assert "Market Analyst" in names
        assert "Trader" in names
        assert "Crypto Specialist" in names

    def test_security_workers(self, registry):
        security = registry.get_department_workers("security")
        assert len(security) == 2

    def test_get_status(self, registry):
        status = registry.get_status()
        assert status["total_workers"] == 10
        assert "finance" in status["departments"]
        assert "cost" in status


class TestWorkerRoster:
    def test_all_workers_have_required_fields(self):
        for spec in WORKER_ROSTER:
            assert "worker_id" in spec
            assert "name" in spec
            assert "department" in spec
            assert "role" in spec
            assert "tools" in spec
            assert isinstance(spec["tools"], set)

    def test_worker_ids_unique(self):
        ids = [s["worker_id"] for s in WORKER_ROSTER]
        assert len(ids) == len(set(ids))

    def test_worker_id_format(self):
        for spec in WORKER_ROSTER:
            assert "." in spec["worker_id"]
            dept, name = spec["worker_id"].split(".", 1)
            assert dept == spec["department"]
```

---

## Task 4: Upgrade DepartmentHead to use Workers

**Files:**
- Modify: `src/company/department_head.py`
- Modify: `tests/unit/test_department_head.py`

### `src/company/department_head.py` changes

Add worker-aware routing to DepartmentHead. When workers are available, Head selects the best worker for the task. For simple tasks, delegates directly. For complex tasks, decomposes into sub-tasks.

Replace the existing `handle()` method and add worker integration:

```python
# Add imports at top:
from src.company.worker import Worker

# Add to __init__:
self._workers: list[Worker] = []

# Add methods:
def set_workers(self, workers: list[Worker]) -> None:
    """Attach workers to this department head."""
    self._workers = workers
    log.info("dept_workers_set", dept=self.dept.value, workers=len(workers))

def _select_worker(self, message: str) -> Worker | None:
    """Select best worker for this message based on tool overlap."""
    if not self._workers:
        return None
    # Simple heuristic: pick idle worker with most relevant tools
    msg_lower = message.lower()
    best_worker = None
    best_score = -1
    for w in self._workers:
        if w.status.value != "idle":
            continue
        # Score based on keyword-tool relevance
        score = sum(1 for tool in w.tools if tool.replace("_", " ") in msg_lower or tool in msg_lower)
        if score > best_score:
            best_score = score
            best_worker = w
    # If no keyword match, pick first idle worker
    if best_worker is None:
        for w in self._workers:
            if w.status.value == "idle":
                return w
    return best_worker

async def handle(
    self,
    session: SessionState,
    message: str,
    memory_context: str = "",
    skill_context: str = "",
) -> AgentResponse:
    """Handle request — delegate to worker if available, else self."""
    # Try to find a suitable worker
    worker = self._select_worker(message)
    if worker:
        log.info("dept_delegating_to_worker", dept=self.dept.value,
                 worker=worker.worker_id)
        result = await worker.execute_direct(
            instruction=message, session_id=session.session_id,
        )
        # Tag with department + worker
        dept_name = get_department_display_name(self.dept)
        result.reasoning_trace = f"[{dept_name} → {worker.name}]"
        return result

    # Fallback: handle directly (existing behavior)
    dept_context = self._dept_prompt
    if skill_context:
        dept_context = f"{dept_context}\n\n{skill_context}"
    result = await self._agent_loop.run(
        session=session,
        user_message=message,
        memory_context=memory_context,
        skill_context=dept_context,
        use_tools=True,
        tool_filter=self._tool_names,
    )
    return result
```

### Test additions for `tests/unit/test_department_head.py`

Add 5 new tests:

```python
class TestDepartmentHeadWithWorkers:
    @pytest.fixture
    def head_with_workers(self, mock_deps):
        registry, assembler, tracer = mock_deps
        head = DepartmentHead(Department.FINANCE, registry, assembler, tracer)
        # Create mock workers
        worker1 = MagicMock()
        worker1.worker_id = "finance.analyst"
        worker1.name = "Market Analyst"
        worker1.status = MagicMock(value="idle")
        worker1.tools = {"mt5_candles", "mt5_analyze"}
        worker1.execute_direct = AsyncMock(return_value=AgentResponse(
            request_id="r1", session_id="s1", content="Analysis done"
        ))
        worker2 = MagicMock()
        worker2.worker_id = "finance.trader"
        worker2.name = "Trader"
        worker2.status = MagicMock(value="idle")
        worker2.tools = {"mt5_order", "mt5_close"}
        worker2.execute_direct = AsyncMock(return_value=AgentResponse(
            request_id="r2", session_id="s1", content="Order placed"
        ))
        head.set_workers([worker1, worker2])
        return head, worker1, worker2

    @pytest.mark.asyncio
    async def test_delegates_to_worker(self, head_with_workers):
        head, worker1, worker2 = head_with_workers
        session = SessionState(channel=Channel.CLI, session_id="s1")
        result = await head.handle(session, "analyze mt5_candles XAUUSD")
        # Should delegate to worker1 (mt5_candles keyword)
        assert "Analysis done" in result.content or "Order placed" in result.content

    @pytest.mark.asyncio
    async def test_worker_response_tagged(self, head_with_workers):
        head, worker1, _ = head_with_workers
        session = SessionState(channel=Channel.CLI, session_id="s1")
        result = await head.handle(session, "test message")
        assert result.reasoning_trace is not None

    def test_set_workers(self, head_with_workers):
        head, _, _ = head_with_workers
        assert len(head._workers) == 2

    @pytest.mark.asyncio
    async def test_fallback_when_no_workers(self, mock_deps):
        registry, assembler, tracer = mock_deps
        head = DepartmentHead(Department.FINANCE, registry, assembler, tracer)
        head._agent_loop.run = AsyncMock(return_value=AgentResponse(
            request_id="r1", session_id="s1", content="Direct response"
        ))
        session = SessionState(channel=Channel.CLI, session_id="s1")
        result = await head.handle(session, "test")
        assert result.content == "Direct response"

    @pytest.mark.asyncio
    async def test_fallback_when_all_workers_busy(self, head_with_workers):
        head, worker1, worker2 = head_with_workers
        worker1.status = MagicMock(value="busy")
        worker2.status = MagicMock(value="busy")
        head._agent_loop.run = AsyncMock(return_value=AgentResponse(
            request_id="r1", session_id="s1", content="Head handles"
        ))
        session = SessionState(channel=Channel.CLI, session_id="s1")
        result = await head.handle(session, "test")
        assert result.content == "Head handles"
```

---

## Task 5: Wire Workers into CEO + JarvisApp

**Files:**
- Modify: `src/company/ceo.py`
- Modify: `src/app.py`
- Modify: `tests/unit/test_ceo.py`

### `src/company/ceo.py` changes

Add WorkerRegistry integration:

```python
# Add import:
from src.company.worker_registry import WorkerRegistry

# Modify __init__ to accept and store WorkerRegistry:
def __init__(
    self,
    agent_loop: AgentLoop,
    tool_registry: ToolRegistry,
    assembler,
    tracer,
    cloud_model: str = "claude-sonnet-4-20250514",
    worker_registry: WorkerRegistry | None = None,
) -> None:
    # ... existing code ...
    self._worker_registry = worker_registry

    # Wire workers to department heads
    if worker_registry:
        for dept, head in self._departments.items():
            workers = worker_registry.get_department_workers(dept.value)
            head.set_workers(workers)

# Add method:
def start_workers(self) -> None:
    """Start all worker event loops."""
    if self._worker_registry:
        self._worker_registry.start_all()

async def stop_workers(self) -> None:
    """Stop all worker event loops."""
    if self._worker_registry:
        await self._worker_registry.stop_all()

# Modify get_status() to include worker info:
def get_status(self) -> dict[str, Any]:
    status = {
        "departments": {
            dept.value: {
                "name": head.display_name,
                "tools": len(head._tool_names),
                "workers": [w.get_status_dict() for w in head._workers],
            }
            for dept, head in self._departments.items()
        },
        "total_departments": len(self._departments),
        "total_workers": sum(len(h._workers) for h in self._departments.values()),
    }
    if self._worker_registry:
        status["cost"] = self._worker_registry.cost_guard.get_daily_usage()
    return status
```

### `src/app.py` changes

Modify `init_company()` to create WorkerRegistry and pass to CEO:

```python
def init_company(self) -> None:
    """Initialize Company Structure — CEO + Department Heads + Workers."""
    from src.company.ceo import CEO
    from src.company.worker_registry import WorkerRegistry

    worker_registry = WorkerRegistry(
        tool_registry=self.tool_registry,
        assembler=self.router._assembler,
        tracer=self.router._tracer,
        cloud_model=self.router._cloud_model,
    )

    ceo = CEO(
        agent_loop=self.router._agent_loop,
        tool_registry=self.tool_registry,
        assembler=self.router._assembler,
        tracer=self.router._tracer,
        cloud_model=self.router._cloud_model,
        worker_registry=worker_registry,
    )
    self.router.ceo = ceo
    self._ceo = ceo
    self._worker_registry = worker_registry

    # Start all workers
    worker_registry.start_all()

    status = ceo.get_status()
    log.info("company_initialized",
             departments=status["total_departments"],
             workers=status["total_workers"])
```

Update `shutdown()` to stop workers:

```python
async def shutdown(self) -> None:
    # ... existing code ...
    if self._ceo:
        await self._ceo.stop_workers()
```

### Test additions for `tests/unit/test_ceo.py`

Add 3 new tests:

```python
class TestCEOWithWorkers:
    @pytest.fixture
    def ceo_with_workers(self, mock_deps):
        loop, registry, assembler, tracer = mock_deps
        worker_reg = MagicMock()
        worker_reg.get_department_workers.return_value = [MagicMock(), MagicMock()]
        worker_reg.cost_guard.get_daily_usage.return_value = {"total": 0.5}
        ceo = CEO(loop, registry, assembler, tracer, worker_registry=worker_reg)
        return ceo

    def test_workers_wired_to_heads(self, ceo_with_workers):
        ceo = ceo_with_workers
        for head in ceo._departments.values():
            assert len(head._workers) == 2

    def test_status_includes_workers(self, ceo_with_workers):
        status = ceo_with_workers.get_status()
        assert status["total_workers"] > 0
        assert "cost" in status

    def test_status_includes_cost(self, ceo_with_workers):
        status = ceo_with_workers.get_status()
        assert status["cost"]["total"] == 0.5
```

---

## Task 6: Upgrade /company command (Telegram + CLI)

**Files:**
- Modify: `src/gateway/channels/telegram.py`
- Modify: `src/gateway/channels/cli.py`

### Telegram `/company` handler upgrade

Replace `_handle_company()` to show workers:

```python
async def _handle_company(self, update, context):
    """Show company structure with workers."""
    if not self._ceo:
        await update.message.reply_text("Company structure not initialized.")
        return
    status = self._ceo.get_status()
    dept_emojis = {
        "finance": "💰", "security": "🔒", "engineering": "⚙️",
        "research": "🔬", "operations": "📋",
    }
    lines = [
        f"🏢 **JARVIS Tech Startup**",
        f"👔 CEO: JARVIS",
        f"📊 Departments: {status['total_departments']} | Workers: {status['total_workers']}",
        "",
    ]
    for dept_name, dept_info in status["departments"].items():
        emoji = dept_emojis.get(dept_name, "📁")
        lines.append(f"{emoji} **{dept_info['name']}** ({dept_info['tools']} tools)")
        workers = dept_info.get("workers", [])
        for w in workers:
            status_icon = "🟢" if w["status"] == "idle" else "🔴" if w["status"] == "busy" else "⚫"
            lines.append(f"  {status_icon} {w['name']} — {w['tasks_completed']} tasks, ${w['total_cost']:.2f}")
        lines.append("")

    if "cost" in status:
        cost = status["cost"]
        lines.append(f"💵 Today: ${cost['total']:.2f} / ${cost['limit']:.2f}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
```

### CLI `/company` handler upgrade

Same pattern adapted for CLI output (no markdown bold, use plain text formatting).

---

## Task 7: Full test run + verification

1. `pytest tests/unit/test_worker_store.py -v` — data layer tests
2. `pytest tests/unit/test_worker.py -v` — worker agent tests
3. `pytest tests/unit/test_worker_registry.py -v` — registry + cost guard tests
4. `pytest tests/unit/test_department_head.py -v` — updated head tests
5. `pytest tests/unit/test_ceo.py -v` — updated CEO tests
6. `pytest tests/unit/ -x -q` — full suite, 0 regressions
7. Restart JARVIS, verify startup shows workers initialized
8. Test `/company` on Telegram — should show workers with status

---

## Execution Order

```
Task 1 (Worker Store)         — foundation, no deps
Task 2 (Worker Base Class)    — depends on Task 1
Task 3 (Worker Registry)      — depends on Task 2
Task 4 (DepartmentHead upgrade) — depends on Task 2
Task 5 (CEO + App wiring)     — depends on Tasks 3, 4
Task 6 (Telegram + CLI)       — depends on Task 5
Task 7 (Tests + verification) — after all
```

**Parallelizable:** Tasks 3 + 4 (after Task 2)
