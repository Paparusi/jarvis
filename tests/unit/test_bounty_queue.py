"""Tests for Bug Bounty Pipeline — Target Queue."""

import json
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from src.bounty.models import BountyTarget, TargetState
from src.bounty.queue import TargetQueue
from src.bounty.store import init_bounty_tables


@pytest.fixture
def conn():
    """Create an in-memory SQLite database with bounty tables."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    init_bounty_tables(db)
    yield db
    db.close()


@pytest.fixture
def queue(conn):
    """Create a TargetQueue with the test database."""
    return TargetQueue(conn)


def _insert_program(conn, name="Test Program", priority=0.5, domains=None):
    """Helper: insert a bounty program and return its id."""
    if domains is None:
        domains = ["example.com", "api.example.com"]
    conn.execute(
        """INSERT INTO bounty_programs
           (platform, program_id, name, scope_domains, priority_score)
           VALUES (?, ?, ?, ?, ?)""",
        ("hackerone", name.lower().replace(" ", "_"), name,
         json.dumps(domains), priority),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM bounty_programs WHERE program_id = ?",
        (name.lower().replace(" ", "_"),),
    ).fetchone()["id"]


class TestEnqueue:
    """Test enqueue_from_program."""

    def test_enqueue_from_program(self, queue, conn):
        pid = _insert_program(conn, domains=["a.com", "b.com", "c.com"])
        count = queue.enqueue_from_program(pid)
        assert count == 3
        rows = conn.execute(
            "SELECT * FROM bounty_targets WHERE program_id = ?", (pid,)
        ).fetchall()
        assert len(rows) == 3
        domains = {r["domain"] for r in rows}
        assert domains == {"a.com", "b.com", "c.com"}
        assert all(r["state"] == "queued" for r in rows)

    def test_enqueue_idempotent(self, queue, conn):
        """Calling enqueue twice should not create duplicates."""
        pid = _insert_program(conn, domains=["x.com", "y.com"])
        count1 = queue.enqueue_from_program(pid)
        count2 = queue.enqueue_from_program(pid)
        assert count1 == 2
        assert count2 == 0
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM bounty_targets WHERE program_id = ?",
            (pid,),
        ).fetchone()["cnt"]
        assert total == 2

    def test_enqueue_nonexistent_program(self, queue):
        """Enqueue for a missing program should return 0."""
        count = queue.enqueue_from_program(999)
        assert count == 0


class TestGetNext:
    """Test get_next retrieval and ordering."""

    def test_get_next_returns_queued(self, queue, conn):
        pid = _insert_program(conn, domains=["one.com"])
        queue.enqueue_from_program(pid)
        targets = queue.get_next(limit=5)
        assert len(targets) == 1
        assert targets[0].domain == "one.com"
        assert targets[0].state == TargetState.QUEUED

    def test_get_next_respects_limit(self, queue, conn):
        pid = _insert_program(conn, domains=["a.com", "b.com", "c.com", "d.com"])
        queue.enqueue_from_program(pid)
        targets = queue.get_next(limit=2)
        assert len(targets) == 2

    def test_get_next_orders_by_priority(self, queue, conn):
        """Targets from higher-priority programs should come first."""
        pid_low = _insert_program(conn, name="Low Priority", priority=0.1,
                                  domains=["low.com"])
        pid_high = _insert_program(conn, name="High Priority", priority=0.9,
                                   domains=["high.com"])
        queue.enqueue_from_program(pid_low)
        queue.enqueue_from_program(pid_high)
        targets = queue.get_next(limit=5)
        assert len(targets) == 2
        assert targets[0].domain == "high.com"
        assert targets[1].domain == "low.com"

    def test_get_next_skips_scanning(self, queue, conn):
        """Targets in 'scanning' state should not appear in get_next."""
        pid = _insert_program(conn, domains=["scan.com", "wait.com"])
        queue.enqueue_from_program(pid)
        # Mark first as scanning
        target = queue.get_next(limit=1)[0]
        queue.mark_scanning(target.id)
        # Now get_next should only return the other
        remaining = queue.get_next(limit=5)
        assert len(remaining) == 1
        assert remaining[0].domain != target.domain


class TestMarkScanning:
    """Test mark_scanning state transition."""

    def test_mark_scanning(self, queue, conn):
        pid = _insert_program(conn, domains=["target.com"])
        queue.enqueue_from_program(pid)
        target = queue.get_next(limit=1)[0]
        queue.mark_scanning(target.id)
        row = conn.execute(
            "SELECT state FROM bounty_targets WHERE id = ?", (target.id,)
        ).fetchone()
        assert row["state"] == "scanning"


class TestMarkScanned:
    """Test mark_scanned state transition."""

    def test_mark_scanned(self, queue, conn):
        pid = _insert_program(conn, domains=["done.com"])
        queue.enqueue_from_program(pid)
        target = queue.get_next(limit=1)[0]
        queue.mark_scanning(target.id)
        queue.mark_scanned(target.id, findings_count=3)
        row = conn.execute(
            "SELECT state, scan_count, findings_count, last_scanned FROM bounty_targets WHERE id = ?",
            (target.id,),
        ).fetchone()
        assert row["state"] == "scanned"
        assert row["scan_count"] == 1
        assert row["findings_count"] == 3
        assert row["last_scanned"] is not None


class TestScheduleRescan:
    """Test rescan scheduling and promotion."""

    def test_schedule_rescan(self, queue, conn):
        pid = _insert_program(conn, domains=["rescan.com"])
        queue.enqueue_from_program(pid)
        target = queue.get_next(limit=1)[0]
        queue.mark_scanning(target.id)
        queue.mark_scanned(target.id)
        queue.schedule_rescan(target.id, hours=24)
        row = conn.execute(
            "SELECT state, next_scan FROM bounty_targets WHERE id = ?",
            (target.id,),
        ).fetchone()
        assert row["state"] == "rescan_scheduled"
        assert row["next_scan"] is not None

    def test_promote_due_rescans(self, queue, conn):
        """Targets past their next_scan time should be promoted to queued."""
        pid = _insert_program(conn, domains=["promote.com"])
        queue.enqueue_from_program(pid)
        target = queue.get_next(limit=1)[0]
        queue.mark_scanning(target.id)
        queue.mark_scanned(target.id)
        # Set next_scan in the past
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn.execute(
            "UPDATE bounty_targets SET state = ?, next_scan = ? WHERE id = ?",
            (TargetState.RESCAN_SCHEDULED.value, past, target.id),
        )
        conn.commit()
        promoted = queue.promote_due_rescans()
        assert promoted == 1
        row = conn.execute(
            "SELECT state FROM bounty_targets WHERE id = ?", (target.id,)
        ).fetchone()
        assert row["state"] == "queued"


class TestStats:
    """Test stats aggregation."""

    def test_stats(self, queue, conn):
        pid = _insert_program(conn, domains=["s1.com", "s2.com", "s3.com"])
        queue.enqueue_from_program(pid)
        targets = queue.get_next(limit=3)
        # Mark first as scanning, second as scanned
        queue.mark_scanning(targets[0].id)
        queue.mark_scanning(targets[1].id)
        queue.mark_scanned(targets[1].id)

        result = queue.stats()
        assert result["queued"] == 1
        assert result["scanning"] == 1
        assert result["scanned"] == 1
        assert result["total"] == 3
