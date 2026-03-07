"""Tests for src.bounty.pipeline — BountyPipeline orchestrator."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.bounty.models import BountyFinding, BountyTarget, FindingStatus, TargetState
from src.bounty.pipeline import BountyPipeline
from src.bounty.store import init_bounty_tables
from src.tools.base import ToolRegistry, ToolResult


def _make_result(success=True, output="ok", data=None, error=""):
    return ToolResult(success=success, output=output, data=data or {}, error=error)


class TestBountyPipeline:
    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_bounty_tables(self.conn)
        self.registry = ToolRegistry()
        self.pipeline = BountyPipeline(self.conn, self.registry)

    def teardown_method(self):
        self.conn.close()

    # ------------------------------------------------------------------ #
    # 1. test_init
    # ------------------------------------------------------------------ #
    def test_init(self):
        """Pipeline initializes with all sub-components and is_running=False."""
        assert self.pipeline is not None
        assert self.pipeline.is_running is False
        assert self.pipeline.monitor is not None
        assert self.pipeline.queue is not None
        assert self.pipeline.recon is not None
        assert self.pipeline.scanner is not None
        assert self.pipeline.verifier is not None
        assert self.pipeline.reporter is not None
        assert self.pipeline._task is None

    # ------------------------------------------------------------------ #
    # 2. test_save_finding
    # ------------------------------------------------------------------ #
    def test_save_finding(self):
        """save_finding inserts a row and returns the new id."""
        # Need a program + target first for FK
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
            ("hackerone", "test-prog", "Test Program"),
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain, state) VALUES (?, ?, ?)",
            (1, "test.com", "queued"),
        )
        self.conn.commit()

        finding = BountyFinding(
            target_id=1,
            vuln_type="xss",
            severity="HIGH",
            cvss=7.5,
            confidence=0.9,
            title="XSS in search",
            description="Reflected XSS",
            poc="<script>alert(1)</script>",
        )
        fid = self.pipeline.save_finding(finding)
        assert fid is not None
        assert fid > 0

        # Verify in DB
        row = self.conn.execute(
            "SELECT * FROM bounty_findings WHERE id = ?", (fid,)
        ).fetchone()
        assert row is not None
        assert row["vuln_type"] == "xss"
        assert row["severity"] == "HIGH"
        assert row["cvss"] == 7.5
        assert row["title"] == "XSS in search"
        assert row["status"] == "pending"

    # ------------------------------------------------------------------ #
    # 3. test_get_pending_findings
    # ------------------------------------------------------------------ #
    def test_get_pending_findings(self):
        """get_pending_findings retrieves pending findings sorted by cvss DESC."""
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
            ("hackerone", "p1", "P1"),
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain) VALUES (?, ?)",
            (1, "a.com"),
        )
        self.conn.commit()

        # Insert two findings with different CVSS
        f_low = BountyFinding(
            target_id=1, vuln_type="cors", severity="MEDIUM",
            cvss=5.3, confidence=0.8, title="CORS misconfiguration",
        )
        f_high = BountyFinding(
            target_id=1, vuln_type="sqli", severity="CRITICAL",
            cvss=9.8, confidence=0.95, title="SQL Injection",
        )
        self.pipeline.save_finding(f_low)
        self.pipeline.save_finding(f_high)

        pending = self.pipeline.get_pending_findings()
        assert len(pending) == 2
        # Ordered by cvss DESC
        assert pending[0].cvss == 9.8
        assert pending[1].cvss == 5.3
        assert all(f.status == FindingStatus.PENDING for f in pending)

    # ------------------------------------------------------------------ #
    # 4. test_get_pending_findings_empty
    # ------------------------------------------------------------------ #
    def test_get_pending_findings_empty(self):
        """get_pending_findings returns [] when no pending findings exist."""
        result = self.pipeline.get_pending_findings()
        assert result == []

    # ------------------------------------------------------------------ #
    # 5. test_update_finding_status
    # ------------------------------------------------------------------ #
    def test_update_finding_status(self):
        """update_finding_status changes the status in the DB."""
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
            ("hackerone", "p1", "P1"),
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain) VALUES (?, ?)",
            (1, "a.com"),
        )
        self.conn.commit()

        finding = BountyFinding(
            target_id=1, vuln_type="xss", severity="HIGH",
            cvss=7.5, confidence=0.9, title="XSS",
        )
        fid = self.pipeline.save_finding(finding)

        # Approve
        self.pipeline.update_finding_status(fid, FindingStatus.APPROVED.value)
        row = self.conn.execute(
            "SELECT status FROM bounty_findings WHERE id = ?", (fid,)
        ).fetchone()
        assert row["status"] == "approved"

        # Reject
        self.pipeline.update_finding_status(fid, FindingStatus.REJECTED.value)
        row = self.conn.execute(
            "SELECT status FROM bounty_findings WHERE id = ?", (fid,)
        ).fetchone()
        assert row["status"] == "rejected"

    # ------------------------------------------------------------------ #
    # 6. test_stats
    # ------------------------------------------------------------------ #
    def test_stats(self):
        """stats() returns a dict with expected keys and correct values."""
        # Insert a program and finding for non-trivial stats
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name, status) VALUES (?, ?, ?, ?)",
            ("hackerone", "p1", "P1", "active"),
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain, state) VALUES (?, ?, ?)",
            (1, "a.com", "queued"),
        )
        self.conn.commit()

        finding = BountyFinding(
            target_id=1, vuln_type="xss", severity="HIGH",
            cvss=7.5, confidence=0.9, title="XSS",
        )
        self.pipeline.save_finding(finding)

        s = self.pipeline.stats()
        assert "running" in s
        assert s["running"] is False
        assert "programs" in s
        assert s["programs"] == 1
        assert "targets" in s
        assert "findings" in s
        assert s["findings"]["pending"] == 1
        assert s["findings"]["total"] == 1
        assert "earnings_usd" in s
        assert s["earnings_usd"] == 0

    # ------------------------------------------------------------------ #
    # 7. test_start_stop
    # ------------------------------------------------------------------ #
    @pytest.mark.asyncio
    async def test_start_stop(self):
        """start() sets running=True, stop() clears it."""
        # Patch _loop to avoid actual scanning
        with patch.object(self.pipeline, "_loop", new_callable=AsyncMock) as mock_loop:
            await self.pipeline.start(interval_hours=1)
            assert self.pipeline.is_running is True
            assert self.pipeline._task is not None

            # Allow task to be scheduled
            await asyncio.sleep(0.01)

            await self.pipeline.stop()
            assert self.pipeline.is_running is False
            assert self.pipeline._task is None

    # ------------------------------------------------------------------ #
    # 8. test_scan_target
    # ------------------------------------------------------------------ #
    @pytest.mark.asyncio
    async def test_scan_target(self):
        """scan_target runs recon+scan+verify+dedup and returns findings."""
        # Set up program and target in DB
        self.conn.execute(
            """INSERT INTO bounty_programs
               (platform, program_id, name, bounty_low, bounty_high, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("hackerone", "test-prog", "Test", 100, 10000, "active"),
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain, state) VALUES (?, ?, ?)",
            (1, "vuln.example.com", "queued"),
        )
        self.conn.commit()

        target = BountyTarget(
            id=1,
            program_id=1,
            domain="vuln.example.com",
            state=TargetState.QUEUED,
        )

        # Mock the registry.execute to simulate tools
        async def mock_execute(name, **kwargs):
            if name == "waf_detect":
                return _make_result(data={"waf": None})
            if name == "sqli_test":
                return _make_result(
                    output="SQL injection found",
                    data={"vulnerable": True, "payload": "' OR 1=1--"},
                )
            # For verify re-run of sqli_test — confirm it
            if name in ("xss_scan", "lfi_test", "cors_check", "header_audit", "dir_bruteforce"):
                return _make_result(data={"vulnerable": False})
            # Recon tools
            return _make_result(data={})

        mock_reg = MagicMock(spec=ToolRegistry)
        mock_reg.execute = AsyncMock(side_effect=mock_execute)

        self.pipeline.recon = MagicMock()
        self.pipeline.recon.run_full = AsyncMock(return_value=MagicMock(domain="vuln.example.com"))

        self.pipeline.scanner = MagicMock()
        sqli_finding = BountyFinding(
            target_id=0,
            vuln_type="sqli",
            severity="CRITICAL",
            cvss=9.8,
            confidence=0.6,
            title="SQLI vulnerability found",
            poc="' OR 1=1--",
        )
        scan_result_mock = MagicMock()
        scan_result_mock.findings = [sqli_finding]
        self.pipeline.scanner.scan = AsyncMock(return_value=scan_result_mock)

        # Mock verifier to confirm the finding
        async def mock_verify(finding, url):
            finding.confidence = 0.95
            return finding

        self.pipeline.verifier = MagicMock()
        self.pipeline.verifier.verify = AsyncMock(side_effect=mock_verify)
        self.pipeline.verifier.estimate_bounty = MagicMock(side_effect=lambda f, lo, hi: f)
        self.pipeline.verifier.dedup_findings = MagicMock(side_effect=lambda fs: fs)

        findings = await self.pipeline.scan_target(target)

        assert len(findings) == 1
        assert findings[0].vuln_type == "sqli"
        assert findings[0].target_id == 1
        assert findings[0].confidence == 0.95
        self.pipeline.recon.run_full.assert_awaited_once()
        self.pipeline.scanner.scan.assert_awaited_once()
        self.pipeline.verifier.verify.assert_awaited_once()
        self.pipeline.verifier.dedup_findings.assert_called_once()
