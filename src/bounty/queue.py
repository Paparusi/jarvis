"""Target Queue for the Bug Bounty Pipeline.

Manages the lifecycle of scan targets: enqueue, dequeue, scan tracking,
and rescan scheduling.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta

from src.bounty.models import BountyTarget, TargetState
from src.bounty.store import init_bounty_tables
from src.utils.logging import get_logger

log = get_logger("bounty.queue")


class TargetQueue:
    """Priority queue for bounty scan targets."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        init_bounty_tables(conn)

    def enqueue_from_program(self, program_id: int) -> int:
        """Read scope_domains from a bounty program and enqueue each as a target.

        Skips domains that already exist for this program (idempotent).
        Returns the number of targets added.
        """
        row = self.conn.execute(
            "SELECT scope_domains FROM bounty_programs WHERE id = ?",
            (program_id,),
        ).fetchone()
        if not row:
            return 0

        domains = json.loads(row["scope_domains"])
        count = 0
        for domain in domains:
            try:
                self.conn.execute(
                    """INSERT INTO bounty_targets (program_id, domain, state)
                       VALUES (?, ?, ?)""",
                    (program_id, domain, TargetState.QUEUED.value),
                )
                count += 1
            except sqlite3.IntegrityError:
                # Already exists — skip
                pass
        self.conn.commit()
        log.info("targets_enqueued", program_id=program_id, count=count)
        return count

    def get_next(self, limit: int = 3) -> list[BountyTarget]:
        """Get the next batch of queued targets, ordered by program priority.

        Joins with bounty_programs to sort by priority_score DESC.
        Returns up to `limit` BountyTarget instances.
        """
        rows = self.conn.execute(
            """SELECT t.id, t.program_id, t.domain, t.scope_type,
                      t.state, t.scan_count, t.findings_count
               FROM bounty_targets t
               JOIN bounty_programs p ON t.program_id = p.id
               WHERE t.state = ?
               ORDER BY p.priority_score DESC
               LIMIT ?""",
            (TargetState.QUEUED.value, limit),
        ).fetchall()

        targets: list[BountyTarget] = []
        for row in rows:
            targets.append(
                BountyTarget(
                    id=row["id"],
                    program_id=row["program_id"],
                    domain=row["domain"],
                    scope_type=row["scope_type"],
                    state=TargetState(row["state"]),
                    scan_count=row["scan_count"],
                    findings_count=row["findings_count"],
                )
            )
        return targets

    def mark_scanning(self, target_id: int) -> None:
        """Mark a target as currently being scanned."""
        self.conn.execute(
            "UPDATE bounty_targets SET state = ? WHERE id = ?",
            (TargetState.SCANNING.value, target_id),
        )
        self.conn.commit()

    def mark_scanned(self, target_id: int, findings_count: int = 0) -> None:
        """Mark a target as scanned, incrementing scan_count and recording findings."""
        self.conn.execute(
            """UPDATE bounty_targets
               SET state = ?,
                   scan_count = scan_count + 1,
                   findings_count = ?,
                   last_scanned = datetime('now')
               WHERE id = ?""",
            (TargetState.SCANNED.value, findings_count, target_id),
        )
        self.conn.commit()

    def schedule_rescan(self, target_id: int, hours: int = 24) -> None:
        """Schedule a target for rescan after the specified number of hours."""
        next_scan = datetime.now(timezone.utc) + timedelta(hours=hours)
        self.conn.execute(
            """UPDATE bounty_targets
               SET state = ?,
                   next_scan = ?
               WHERE id = ?""",
            (
                TargetState.RESCAN_SCHEDULED.value,
                next_scan.isoformat(),
                target_id,
            ),
        )
        self.conn.commit()

    def promote_due_rescans(self) -> int:
        """Promote rescan-scheduled targets whose next_scan time has passed.

        Sets state back to 'queued' so they appear in get_next().
        Returns the number of targets promoted.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            """UPDATE bounty_targets
               SET state = ?
               WHERE state = ? AND next_scan <= ?""",
            (TargetState.QUEUED.value, TargetState.RESCAN_SCHEDULED.value, now),
        )
        self.conn.commit()
        count = cur.rowcount
        if count:
            log.info("rescans_promoted", count=count)
        return count

    def stats(self) -> dict[str, int]:
        """Return a dict with target counts grouped by state, plus a total."""
        rows = self.conn.execute(
            "SELECT state, COUNT(*) as cnt FROM bounty_targets GROUP BY state"
        ).fetchall()
        result: dict[str, int] = {}
        total = 0
        for row in rows:
            result[row["state"]] = row["cnt"]
            total += row["cnt"]
        result["total"] = total
        return result
