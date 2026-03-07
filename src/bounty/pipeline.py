"""Pipeline Orchestrator for the Bug Bounty Pipeline.

Ties together Monitor, Queue, ReconEngine, VulnScanner, Verifier,
and Reporter into a full scan cycle with background scheduling.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any, Callable

from src.bounty.models import BountyFinding, BountyTarget, FindingStatus, TargetState
from src.bounty.monitor import ProgramMonitor
from src.bounty.queue import TargetQueue
from src.bounty.recon import ReconEngine
from src.bounty.reporter import BountyReportGenerator
from src.bounty.scanner import VulnScanner
from src.bounty.store import init_bounty_tables
from src.bounty.verifier import Verifier
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("bounty.pipeline")

NotifyCallback = Callable[[BountyFinding, str], Any]


class BountyPipeline:
    """Core orchestrator that drives the full bug bounty pipeline.

    Lifecycle: monitor programs -> enqueue targets -> recon -> scan ->
    verify -> estimate bounty -> dedup -> save -> notify.
    """

    def __init__(self, conn: sqlite3.Connection, tool_registry: ToolRegistry) -> None:
        self.conn = conn
        init_bounty_tables(conn)

        self.monitor = ProgramMonitor(conn)
        self.queue = TargetQueue(conn)
        self.recon = ReconEngine(tool_registry)
        self.scanner = VulnScanner(tool_registry)
        self.verifier = Verifier(tool_registry)
        self.reporter = BountyReportGenerator()
        self.tool_registry = tool_registry

        self._running = False
        self._task: asyncio.Task | None = None
        self._notify_callback: NotifyCallback | None = None

    @property
    def is_running(self) -> bool:
        """Whether the background loop is currently active."""
        return self._running

    def set_notify_callback(self, callback: NotifyCallback) -> None:
        """Set a callback for new findings: callback(finding, report_text)."""
        self._notify_callback = callback

    async def scan_target(self, target: BountyTarget) -> list[BountyFinding]:
        """Full pipeline for one target: recon -> scan -> verify -> estimate bounty -> dedup.

        Also scans discovered subdomains (up to 5) for broader coverage.
        Returns a list of reportable, deduplicated findings.
        """
        domain = target.domain.lstrip("*.")
        url = f"https://{domain}"

        # 1. Recon on main domain
        recon_result = await self.recon.run_full(domain, url)

        # 2. Scan main domain
        scan_result = await self.scanner.scan(url, recon_result)
        all_findings = list(scan_result.findings)

        # 3. Scan discovered subdomains (max 5, skip main domain)
        subdomains_to_scan = [
            s for s in recon_result.subdomains
            if s != domain and not s.startswith("*")
        ][:5]

        for sub in subdomains_to_scan:
            sub_url = f"https://{sub}"
            try:
                sub_scan = await self.scanner.scan(sub_url, recon_result)
                all_findings.extend(sub_scan.findings)
                log.info("subdomain_scanned", subdomain=sub, findings=len(sub_scan.findings))
            except Exception as exc:
                log.warning("subdomain_scan_failed", subdomain=sub, error=str(exc))

        # 4. Look up program bounty range
        row = self.conn.execute(
            "SELECT bounty_low, bounty_high FROM bounty_programs WHERE id = ?",
            (target.program_id,),
        ).fetchone()
        bounty_low = row["bounty_low"] if row else 100
        bounty_high = row["bounty_high"] if row else 5000

        # 5. For each finding: set target_id, verify, estimate bounty, filter
        reportable: list[BountyFinding] = []
        for finding in all_findings:
            finding.target_id = target.id or 0

            # Verify
            finding = await self.verifier.verify(finding, url)

            # Estimate bounty
            finding = self.verifier.estimate_bounty(finding, bounty_low, bounty_high)

            # Only keep reportable findings
            if finding.should_report:
                reportable.append(finding)

        # 6. Dedup
        deduped = self.verifier.dedup_findings(reportable)

        log.info(
            "scan_target_complete",
            domain=domain,
            subdomains_scanned=len(subdomains_to_scan),
            raw=len(all_findings),
            reportable=len(reportable),
            deduped=len(deduped),
        )
        return deduped

    def save_finding(self, finding: BountyFinding) -> int:
        """INSERT a finding into bounty_findings. Return the new row id."""
        cur = self.conn.execute(
            """INSERT INTO bounty_findings
               (target_id, vuln_type, severity, cvss, confidence,
                title, description, steps_to_reproduce, poc, impact,
                suggested_fix, estimated_bounty_low, estimated_bounty_high, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                finding.target_id,
                finding.vuln_type,
                finding.severity,
                finding.cvss,
                finding.confidence,
                finding.title,
                finding.description,
                finding.steps_to_reproduce,
                finding.poc,
                finding.impact,
                finding.suggested_fix,
                finding.estimated_bounty_low,
                finding.estimated_bounty_high,
                finding.status.value if isinstance(finding.status, FindingStatus) else finding.status,
            ),
        )
        self.conn.commit()
        finding_id = cur.lastrowid
        log.info("finding_saved", id=finding_id, vuln_type=finding.vuln_type)
        return finding_id

    def get_pending_findings(self) -> list[BountyFinding]:
        """Return all pending findings ordered by CVSS descending."""
        rows = self.conn.execute(
            """SELECT id, target_id, vuln_type, severity, cvss, confidence,
                      title, description, steps_to_reproduce, poc, impact,
                      suggested_fix, estimated_bounty_low, estimated_bounty_high, status
               FROM bounty_findings
               WHERE status = ?
               ORDER BY cvss DESC""",
            (FindingStatus.PENDING.value,),
        ).fetchall()
        return [self._row_to_finding(row) for row in rows]

    def update_finding_status(self, finding_id: int, status: str) -> None:
        """Update the status of a finding by id."""
        self.conn.execute(
            "UPDATE bounty_findings SET status = ? WHERE id = ?",
            (status, finding_id),
        )
        self.conn.commit()
        log.info("finding_status_updated", id=finding_id, status=status)

    def stats(self) -> dict[str, Any]:
        """Return pipeline statistics."""
        # Programs count
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM bounty_programs WHERE status = 'active'"
        ).fetchone()
        programs_count = row["cnt"] if row else 0

        # Target stats
        target_stats = self.queue.stats()

        # Findings
        row_pending = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM bounty_findings WHERE status = ?",
            (FindingStatus.PENDING.value,),
        ).fetchone()
        pending = row_pending["cnt"] if row_pending else 0

        row_total = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM bounty_findings"
        ).fetchone()
        total = row_total["cnt"] if row_total else 0

        # Earnings
        row_earnings = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM bounty_earnings"
        ).fetchone()
        earnings = row_earnings["total"] if row_earnings else 0

        return {
            "running": self._running,
            "programs": programs_count,
            "targets": target_stats,
            "findings": {"pending": pending, "total": total},
            "earnings_usd": earnings,
        }

    async def run_cycle(self) -> dict[str, int]:
        """Execute one full pipeline cycle.

        Steps:
        1. Refresh programs from HackerOne
        2. Enqueue targets from all active programs
        3. Promote due rescans
        4. Scan next batch (limit 3)
        5. Save findings and notify
        """
        result = {"programs_refreshed": 0, "targets_enqueued": 0, "scanned": 0, "findings": 0}

        # 1. Refresh programs
        try:
            result["programs_refreshed"] = await self.monitor.refresh()
        except Exception as exc:
            log.warning("program_refresh_failed", error=str(exc))

        # 2. Enqueue targets from active programs
        programs = self.monitor.get_active_programs()
        for prog in programs:
            if prog.id is not None:
                result["targets_enqueued"] += self.queue.enqueue_from_program(prog.id)

        # 3. Promote due rescans
        self.queue.promote_due_rescans()

        # 4. Scan next batch
        targets = self.queue.get_next(limit=3)
        for target in targets:
            if target.id is None:
                continue

            self.queue.mark_scanning(target.id)
            try:
                findings = await self.scan_target(target)
                self.queue.mark_scanned(target.id, findings_count=len(findings))

                # 5. Save and notify
                for finding in findings:
                    finding_id = self.save_finding(finding)
                    finding.id = finding_id
                    result["findings"] += 1

                    # Notify callback
                    if self._notify_callback:
                        domain = target.domain.lstrip("*.")
                        report = self.reporter.generate_telegram_summary(finding)
                        try:
                            cb_result = self._notify_callback(finding, report)
                            if asyncio.iscoroutine(cb_result):
                                await cb_result
                        except Exception as exc:
                            log.warning("notify_callback_error", error=str(exc))

                # Schedule rescan
                self.queue.schedule_rescan(target.id, hours=24)

            except Exception as exc:
                log.error("scan_target_failed", domain=target.domain, error=str(exc))
                # Mark as scanned even on failure to avoid infinite retry
                self.queue.mark_scanned(target.id, findings_count=0)

            result["scanned"] += 1

        log.info("cycle_complete", **result)
        return result

    async def start(self, interval_hours: int = 6) -> None:
        """Start the background scanning loop."""
        if self._running:
            log.warning("pipeline_already_running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(interval_hours))
        log.info("pipeline_started", interval_hours=interval_hours)

    async def stop(self) -> None:
        """Stop the background scanning loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        log.info("pipeline_stopped")

    async def _loop(self, interval_hours: int) -> None:
        """Background loop: run_cycle() then sleep."""
        while self._running:
            try:
                await self.run_cycle()
            except Exception as exc:
                log.error("cycle_error", error=str(exc))

            try:
                await asyncio.sleep(interval_hours * 3600)
            except asyncio.CancelledError:
                break

    def _row_to_finding(self, row: sqlite3.Row) -> BountyFinding:
        """Convert a sqlite3.Row to a BountyFinding dataclass."""
        return BountyFinding(
            id=row["id"],
            target_id=row["target_id"],
            vuln_type=row["vuln_type"],
            severity=row["severity"],
            cvss=row["cvss"],
            confidence=row["confidence"],
            title=row["title"],
            description=row["description"],
            steps_to_reproduce=row["steps_to_reproduce"],
            poc=row["poc"],
            impact=row["impact"],
            suggested_fix=row["suggested_fix"],
            estimated_bounty_low=row["estimated_bounty_low"],
            estimated_bounty_high=row["estimated_bounty_high"],
            status=FindingStatus(row["status"]),
        )
