"""Tests for Bug Bounty Pipeline — SQLite store."""

import json
import sqlite3

import pytest

from src.bounty.store import init_bounty_tables, get_bounty_connection


@pytest.fixture
def conn():
    """Create an in-memory SQLite database with bounty tables."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    init_bounty_tables(db)
    yield db
    db.close()


class TestBountyTables:
    """Verify all 4 tables exist with correct schemas."""

    def test_bounty_programs_table_exists(self, conn):
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bounty_programs'"
        )
        assert cur.fetchone() is not None

    def test_bounty_targets_table_exists(self, conn):
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bounty_targets'"
        )
        assert cur.fetchone() is not None

    def test_bounty_findings_table_exists(self, conn):
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bounty_findings'"
        )
        assert cur.fetchone() is not None

    def test_bounty_earnings_table_exists(self, conn):
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bounty_earnings'"
        )
        assert cur.fetchone() is not None


class TestInsertProgram:
    """Test inserting and querying bounty programs."""

    def test_insert_program(self, conn):
        conn.execute(
            """INSERT INTO bounty_programs (platform, program_id, name, url, scope_domains, bounty_low, bounty_high)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("hackerone", "prog_001", "Acme Corp", "https://hackerone.com/acme",
             json.dumps(["acme.com", "*.acme.com"]), 100, 10000),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM bounty_programs WHERE program_id = ?", ("prog_001",)).fetchone()
        assert row["platform"] == "hackerone"
        assert row["name"] == "Acme Corp"
        assert row["bounty_low"] == 100
        assert row["bounty_high"] == 10000
        assert row["status"] == "active"
        assert json.loads(row["scope_domains"]) == ["acme.com", "*.acme.com"]

    def test_program_unique_constraint(self, conn):
        conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
            ("hackerone", "prog_dup", "First"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
                ("hackerone", "prog_dup", "Duplicate"),
            )

    def test_program_defaults(self, conn):
        conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
            ("bugcrowd", "bc_001", "Defaults Test"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM bounty_programs WHERE program_id = ?", ("bc_001",)).fetchone()
        assert row["status"] == "active"
        assert row["bounty_low"] == 0
        assert row["bounty_high"] == 0
        assert row["priority_score"] == 0.0
        assert row["created_at"] is not None


class TestInsertTarget:
    """Test inserting targets linked to programs."""

    def _insert_program(self, conn) -> int:
        conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
            ("hackerone", "prog_t", "Target Test"),
        )
        conn.commit()
        return conn.execute("SELECT id FROM bounty_programs WHERE program_id = ?", ("prog_t",)).fetchone()["id"]

    def test_insert_target(self, conn):
        pid = self._insert_program(conn)
        conn.execute(
            "INSERT INTO bounty_targets (program_id, domain) VALUES (?, ?)",
            (pid, "api.acme.com"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM bounty_targets WHERE domain = ?", ("api.acme.com",)).fetchone()
        assert row["program_id"] == pid
        assert row["state"] == "queued"
        assert row["scope_type"] == "domain"
        assert row["scan_count"] == 0

    def test_target_unique_constraint(self, conn):
        pid = self._insert_program(conn)
        conn.execute(
            "INSERT INTO bounty_targets (program_id, domain) VALUES (?, ?)",
            (pid, "dup.acme.com"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO bounty_targets (program_id, domain) VALUES (?, ?)",
                (pid, "dup.acme.com"),
            )


class TestInsertFinding:
    """Test inserting findings linked to targets."""

    def _setup_target(self, conn) -> int:
        conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
            ("hackerone", "prog_f", "Finding Test"),
        )
        conn.commit()
        pid = conn.execute("SELECT id FROM bounty_programs WHERE program_id = ?", ("prog_f",)).fetchone()["id"]
        conn.execute(
            "INSERT INTO bounty_targets (program_id, domain) VALUES (?, ?)",
            (pid, "vuln.acme.com"),
        )
        conn.commit()
        return conn.execute("SELECT id FROM bounty_targets WHERE domain = ?", ("vuln.acme.com",)).fetchone()["id"]

    def test_insert_finding(self, conn):
        tid = self._setup_target(conn)
        conn.execute(
            """INSERT INTO bounty_findings
               (target_id, vuln_type, severity, cvss, confidence, title, description, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (tid, "XSS", "HIGH", 7.5, 0.92, "Reflected XSS in search",
             "The search parameter is vulnerable to XSS", "pending"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM bounty_findings WHERE target_id = ?", (tid,)).fetchone()
        assert row["vuln_type"] == "XSS"
        assert row["severity"] == "HIGH"
        assert row["cvss"] == 7.5
        assert row["confidence"] == 0.92
        assert row["status"] == "pending"
        assert row["created_at"] is not None


class TestGetBountyConnection:
    """Test get_bounty_connection returns a working connection."""

    def test_get_bounty_connection_works(self):
        conn = get_bounty_connection()
        assert conn is not None
        # Verify bounty tables are accessible
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bounty_%'"
        )
        tables = {row["name"] for row in cur.fetchall()}
        assert "bounty_programs" in tables
        assert "bounty_targets" in tables
        assert "bounty_findings" in tables
        assert "bounty_earnings" in tables
