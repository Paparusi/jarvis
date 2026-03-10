"""Tests for Company Worker Store — TaskStore, WorkerMemoryStore, MetricsStore."""

import sqlite3
from unittest.mock import patch

import pytest


def _make_test_conn(tmp_path):
    """Create an in-memory-like test DB at tmp_path."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


class TestTaskStore:
    """Tests for TaskStore CRUD operations."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import src.company.worker_store as ws

        ws._tables_initialized = False
        self.conn = _make_test_conn(tmp_path)
        self.patcher = patch(
            "src.company.worker_store.get_connection",
            return_value=self.conn,
        )
        self.patcher.start()
        self.store = ws.TaskStore()
        yield
        self.patcher.stop()
        self.conn.close()

    def test_create_task_returns_id(self):
        task_id = self.store.create_task("engineering", "Build feature X")
        assert task_id == 1

    def test_create_task_with_all_fields(self):
        task_id = self.store.create_task(
            department="research",
            instruction="Analyze dataset",
            session_id="sess-001",
            worker_id="worker-r1",
            parent_task_id=42,
            priority=2,
            context={"source": "ceo", "tags": ["urgent"]},
        )
        task = self.store.get_task(task_id)
        assert task["department"] == "research"
        assert task["instruction"] == "Analyze dataset"
        assert task["session_id"] == "sess-001"
        assert task["worker_id"] == "worker-r1"
        assert task["parent_task_id"] == 42
        assert task["priority"] == 2
        assert task["context"] == {"source": "ceo", "tags": ["urgent"]}
        assert task["status"] == "pending"

    def test_claim_task_returns_oldest_pending(self):
        self.store.create_task("engineering", "First task")
        self.store.create_task("engineering", "Second task")

        claimed = self.store.claim_task("worker-e1", "engineering")
        assert claimed is not None
        assert claimed["instruction"] == "First task"
        assert claimed["status"] == "assigned"
        assert claimed["worker_id"] == "worker-e1"
        assert claimed["assigned_at"] is not None

    def test_claim_task_empty_returns_none(self):
        result = self.store.claim_task("worker-e1", "engineering")
        assert result is None

    def test_claim_task_respects_department(self):
        self.store.create_task("engineering", "Eng task")
        self.store.create_task("research", "Research task")

        claimed = self.store.claim_task("worker-r1", "research")
        assert claimed["instruction"] == "Research task"
        assert claimed["department"] == "research"

        # Engineering task should still be pending
        assert self.store.get_pending_count("engineering") == 1

    def test_claim_task_priority_order(self):
        self.store.create_task("engineering", "Low priority", priority=9)
        self.store.create_task("engineering", "High priority", priority=1)

        claimed = self.store.claim_task("worker-e1", "engineering")
        assert claimed["instruction"] == "High priority"
        assert claimed["priority"] == 1

    def test_complete_task(self):
        task_id = self.store.create_task("engineering", "Do work")
        self.store.complete_task(task_id, "Work done successfully")

        task = self.store.get_task(task_id)
        assert task["status"] == "completed"
        assert task["result"] == "Work done successfully"
        assert task["completed_at"] is not None

    def test_fail_task(self):
        task_id = self.store.create_task("engineering", "Risky work")
        self.store.fail_task(task_id, "Out of memory")

        task = self.store.get_task(task_id)
        assert task["status"] == "failed"
        assert task["error"] == "Out of memory"
        assert task["completed_at"] is not None

    def test_escalate_task(self):
        task_id = self.store.create_task("engineering", "Complex work")
        self.store.escalate_task(task_id, "Needs human review")

        task = self.store.get_task(task_id)
        assert task["status"] == "escalated"
        assert task["error"] == "Needs human review"
        assert task["completed_at"] is not None

    def test_start_task(self):
        task_id = self.store.create_task("engineering", "Some work")
        self.store.start_task(task_id)

        task = self.store.get_task(task_id)
        assert task["status"] == "in_progress"

    def test_get_pending_count(self):
        self.store.create_task("engineering", "Task 1")
        self.store.create_task("engineering", "Task 2")
        self.store.create_task("engineering", "Task 3")
        self.store.create_task("research", "Research 1")

        assert self.store.get_pending_count("engineering") == 3
        assert self.store.get_pending_count("research") == 1
        assert self.store.get_pending_count("marketing") == 0

    def test_get_worker_tasks(self):
        t1 = self.store.create_task("engineering", "Task A", worker_id="w1")
        t2 = self.store.create_task("engineering", "Task B", worker_id="w1")
        self.store.create_task("engineering", "Task C", worker_id="w2")

        tasks = self.store.get_worker_tasks("w1")
        assert len(tasks) == 2
        instructions = [t["instruction"] for t in tasks]
        assert "Task A" in instructions
        assert "Task B" in instructions
        # Should not include w2's task
        assert "Task C" not in instructions

    def test_get_task_nonexistent(self):
        assert self.store.get_task(999) is None

    def test_cleanup_old(self):
        # Create and complete a task
        task_id = self.store.create_task("engineering", "Old task")
        self.store.complete_task(task_id, "done")

        # Manually backdate the completed_at to 60 days ago
        from datetime import datetime, timedelta, timezone
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        self.conn.execute(
            "UPDATE worker_tasks SET completed_at = ? WHERE id = ?",
            (old_date, task_id),
        )
        self.conn.commit()

        deleted = self.store.cleanup_old(days=30)
        assert deleted == 1
        assert self.store.get_task(task_id) is None


class TestWorkerMemoryStore:
    """Tests for WorkerMemoryStore CRUD operations."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import src.company.worker_store as ws

        ws._tables_initialized = False
        self.conn = _make_test_conn(tmp_path)
        self.patcher = patch(
            "src.company.worker_store.get_connection",
            return_value=self.conn,
        )
        self.patcher.start()
        self.store = ws.WorkerMemoryStore()
        yield
        self.patcher.stop()
        self.conn.close()

    def test_log_memory(self):
        mem_id = self.store.log("worker-1", "observation", "User prefers short answers")
        assert mem_id == 1

        recent = self.store.get_recent("worker-1")
        assert len(recent) == 1
        assert recent[0]["content"] == "User prefers short answers"
        assert recent[0]["memory_type"] == "observation"

    def test_log_with_metadata(self):
        meta = {"confidence": 0.9, "source": "feedback"}
        mem_id = self.store.log("worker-1", "insight", "Pattern detected", metadata=meta)

        recent = self.store.get_recent("worker-1")
        assert recent[0]["metadata"] == meta

    def test_get_recent_filtered_by_type(self):
        self.store.log("worker-1", "observation", "Obs 1")
        self.store.log("worker-1", "pattern", "Pattern 1")
        self.store.log("worker-1", "observation", "Obs 2")

        obs = self.store.get_recent("worker-1", memory_type="observation")
        assert len(obs) == 2
        assert all(m["memory_type"] == "observation" for m in obs)

    def test_get_patterns_shortcut(self):
        self.store.log("worker-1", "observation", "Some observation")
        self.store.log("worker-1", "pattern", "Recurring behavior X")
        self.store.log("worker-1", "pattern", "Recurring behavior Y")

        patterns = self.store.get_patterns("worker-1")
        assert len(patterns) == 2
        assert all(p["memory_type"] == "pattern" for p in patterns)

    def test_isolation_between_workers(self):
        self.store.log("worker-a", "observation", "A sees this")
        self.store.log("worker-b", "observation", "B sees this")

        a_mems = self.store.get_recent("worker-a")
        b_mems = self.store.get_recent("worker-b")

        assert len(a_mems) == 1
        assert len(b_mems) == 1
        assert a_mems[0]["content"] == "A sees this"
        assert b_mems[0]["content"] == "B sees this"


class TestMetricsStore:
    """Tests for MetricsStore CRUD operations."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import src.company.worker_store as ws

        ws._tables_initialized = False
        self.conn = _make_test_conn(tmp_path)
        self.patcher = patch(
            "src.company.worker_store.get_connection",
            return_value=self.conn,
        )
        self.patcher.start()
        self.store = ws.MetricsStore()
        yield
        self.patcher.stop()
        self.conn.close()

    def test_update_creates_new(self):
        self.store.update("worker-1", tokens=100, cost=0.01, response_ms=150.0)

        metrics = self.store.get("worker-1")
        assert metrics is not None
        assert metrics["tasks_completed"] == 1
        assert metrics["tasks_failed"] == 0
        assert metrics["total_tokens"] == 100
        assert metrics["total_cost"] == 0.01
        assert metrics["avg_response_ms"] == 150.0

    def test_update_increments(self):
        self.store.update("worker-1", tokens=100, cost=0.01, response_ms=100.0)
        self.store.update("worker-1", tokens=200, cost=0.02, response_ms=200.0)

        metrics = self.store.get("worker-1")
        assert metrics["tasks_completed"] == 2
        assert metrics["total_tokens"] == 300
        assert metrics["total_cost"] == pytest.approx(0.03)
        # Running average: (100*1 + 200) / 2 = 150
        assert metrics["avg_response_ms"] == pytest.approx(150.0)

    def test_update_failure(self):
        self.store.update("worker-1", tokens=50, cost=0.005, response_ms=80.0, success=False)

        metrics = self.store.get("worker-1")
        assert metrics["tasks_completed"] == 0
        assert metrics["tasks_failed"] == 1

    def test_get_all(self):
        self.store.update("worker-a", tokens=100, cost=0.05)
        self.store.update("worker-b", tokens=200, cost=0.10)

        all_metrics = self.store.get_all()
        assert len(all_metrics) == 2
        # Ordered by cost DESC, so worker-b first
        assert all_metrics[0]["worker_id"] == "worker-b"
        assert all_metrics[1]["worker_id"] == "worker-a"

    def test_get_nonexistent(self):
        assert self.store.get("ghost-worker") is None

    def test_get_daily_cost(self):
        self.store.update("worker-1", tokens=100, cost=0.05, response_ms=100.0)
        # updated_at is set to now, so it should count
        daily = self.store.get_daily_cost("worker-1")
        assert daily == pytest.approx(0.05)

    def test_get_daily_cost_no_data(self):
        daily = self.store.get_daily_cost("nonexistent")
        assert daily == 0.0
