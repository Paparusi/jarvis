"""Task Scheduler — Background jobs & reminders for JARVIS.

Capabilities:
- One-time reminders: "nhắc tao sau 2 tiếng"
- Recurring tasks: hourly health check, daily memory consolidation
- Persistent across restarts (SQLite-backed)
- Telegram push notifications for reminders
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("scheduling.scheduler")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Scheduler:
    """Async task scheduler with SQLite persistence."""

    def __init__(self) -> None:
        self._ensure_table()
        self._running = False
        self._task: asyncio.Task | None = None
        self._notification_callback = None  # Set by Telegram adapter

    def _ensure_table(self) -> None:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                channel TEXT DEFAULT 'telegram',
                job_type TEXT NOT NULL,
                description TEXT NOT NULL,
                run_at TEXT NOT NULL,
                repeat_interval_seconds INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_status
            ON scheduled_jobs(status, run_at)
        """)
        conn.commit()

    def set_notification_callback(self, callback) -> None:
        """Set callback for sending notifications: async fn(user_id, message)."""
        self._notification_callback = callback

    # --- Job Management ---

    def add_reminder(
        self,
        user_id: str,
        description: str,
        run_at: datetime,
        channel: str = "telegram",
    ) -> str:
        """Schedule a one-time reminder."""
        job_id = str(uuid4())[:8]
        conn = get_connection()
        conn.execute(
            """INSERT INTO scheduled_jobs
               (id, user_id, channel, job_type, description, run_at)
               VALUES (?, ?, ?, 'reminder', ?, ?)""",
            (job_id, user_id, channel, description, run_at.isoformat()),
        )
        conn.commit()
        log.info("reminder_scheduled", id=job_id, user_id=user_id,
                 run_at=run_at.isoformat(), description=description[:50])
        return job_id

    def add_recurring(
        self,
        user_id: str,
        description: str,
        interval_seconds: int,
        job_type: str = "recurring",
    ) -> str:
        """Schedule a recurring job."""
        job_id = str(uuid4())[:8]
        run_at = _utcnow() + timedelta(seconds=interval_seconds)
        conn = get_connection()
        conn.execute(
            """INSERT INTO scheduled_jobs
               (id, user_id, channel, job_type, description, run_at, repeat_interval_seconds)
               VALUES (?, ?, 'system', ?, ?, ?, ?)""",
            (job_id, user_id, job_type, description, run_at.isoformat(), interval_seconds),
        )
        conn.commit()
        log.info("recurring_job_added", id=job_id, interval_s=interval_seconds)
        return job_id

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a scheduled job."""
        conn = get_connection()
        result = conn.execute(
            "UPDATE scheduled_jobs SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (job_id,),
        )
        conn.commit()
        return result.rowcount > 0

    def get_pending(self, user_id: str | None = None) -> list[dict]:
        """Get pending jobs for a user."""
        conn = get_connection()
        if user_id:
            rows = conn.execute(
                """SELECT id, description, run_at, job_type, repeat_interval_seconds
                   FROM scheduled_jobs WHERE user_id = ? AND status = 'pending'
                   ORDER BY run_at""",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, user_id, description, run_at, job_type
                   FROM scheduled_jobs WHERE status = 'pending'
                   ORDER BY run_at""",
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stats(self) -> dict:
        conn = get_connection()
        pending = conn.execute(
            "SELECT count(*) FROM scheduled_jobs WHERE status = 'pending'"
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT count(*) FROM scheduled_jobs WHERE status = 'completed'"
        ).fetchone()[0]
        return {"pending": pending, "completed": completed, "running": self._running}

    # --- Scheduler Loop ---

    async def start(self) -> None:
        """Start the background scheduler loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        log.info("scheduler_started")

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("scheduler_stopped")

    async def _run_loop(self) -> None:
        """Main loop: check for due jobs every 30 seconds."""
        while self._running:
            try:
                await self._process_due_jobs()
            except Exception as e:
                log.error("scheduler_loop_error", error=str(e))
            await asyncio.sleep(30)

    async def _process_due_jobs(self) -> None:
        """Find and execute all jobs that are due."""
        conn = get_connection()
        now = _utcnow().isoformat()

        due_jobs = conn.execute(
            """SELECT id, user_id, channel, job_type, description,
                      repeat_interval_seconds
               FROM scheduled_jobs
               WHERE status = 'pending' AND run_at <= ?
               ORDER BY run_at""",
            (now,),
        ).fetchall()

        for job in due_jobs:
            job = dict(job)
            await self._execute_job(job)

    async def _execute_job(self, job: dict) -> None:
        """Execute a single job."""
        job_id = job["id"]
        conn = get_connection()

        try:
            if job["job_type"] == "reminder":
                await self._send_reminder(job)
            elif job["job_type"] == "recurring":
                await self._run_recurring(job)

            # Mark completed
            if job.get("repeat_interval_seconds", 0) > 0:
                # Reschedule recurring job
                next_run = _utcnow() + timedelta(seconds=job["repeat_interval_seconds"])
                conn.execute(
                    "UPDATE scheduled_jobs SET run_at = ? WHERE id = ?",
                    (next_run.isoformat(), job_id),
                )
            else:
                conn.execute(
                    "UPDATE scheduled_jobs SET status = 'completed', completed_at = datetime('now') WHERE id = ?",
                    (job_id,),
                )
            conn.commit()

            log.info("job_executed", id=job_id, type=job["job_type"])

        except Exception as e:
            log.error("job_execution_error", id=job_id, error=str(e))

    async def _send_reminder(self, job: dict) -> None:
        """Send a reminder notification to the user."""
        if self._notification_callback:
            message = f"⏰ **Nhắc nhở**: {job['description']}"
            await self._notification_callback(job["user_id"], message)
        else:
            log.warning("no_notification_callback", job_id=job["id"])

    async def _run_recurring(self, job: dict) -> None:
        """Run a recurring system job."""
        # For now, just log. Future: health checks, memory consolidation, etc.
        log.info("recurring_job_ran", description=job["description"])


# --- Natural Language Time Parsing ---

def parse_reminder_time(text: str) -> tuple[str, datetime] | None:
    """Parse natural language time from reminder text.

    Returns (description, run_at) or None if can't parse.

    Supports:
    - "sau 5 phút" / "in 5 minutes"
    - "sau 2 tiếng" / "in 2 hours"
    - "sau 1 ngày" / "in 1 day"
    - "lúc 15:00" / "at 3pm"
    - "lúc 14:30" / "at 2:30pm"
    """
    text = text.strip()

    # Pattern: "sau X phút/tiếng/ngày"
    match = re.search(
        r"sau\s+(\d+)\s*(phút|giờ|tiếng|ngày|tuần|minute|hour|day|week)s?",
        text, re.I,
    )
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        delta = _unit_to_timedelta(unit, amount)
        if delta:
            desc = re.sub(r"sau\s+\d+\s*\S+\s*", "", text, flags=re.I).strip()
            if not desc:
                desc = "Nhắc nhở"
            return desc, _utcnow() + delta

    # Pattern: "in X minutes/hours/days"
    match = re.search(
        r"in\s+(\d+)\s*(minute|hour|day|week)s?",
        text, re.I,
    )
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        delta = _unit_to_timedelta(unit, amount)
        if delta:
            desc = re.sub(r"in\s+\d+\s*\S+s?\s*", "", text, flags=re.I).strip()
            if not desc:
                desc = "Reminder"
            return desc, _utcnow() + delta

    # Pattern: "lúc HH:MM" or "at HH:MM"
    match = re.search(r"(?:lúc|at)\s+(\d{1,2})[:\.](\d{2})", text, re.I)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            now = _utcnow()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)  # Tomorrow
            desc = re.sub(r"(?:lúc|at)\s+\d{1,2}[:\.]\d{2}\s*", "", text, flags=re.I).strip()
            if not desc:
                desc = "Nhắc nhở"
            return desc, target

    # Pattern: "at Xpm/am"
    match = re.search(r"at\s+(\d{1,2})\s*(am|pm)", text, re.I)
    if match:
        hour = int(match.group(1))
        ampm = match.group(2).lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        now = _utcnow()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        desc = re.sub(r"at\s+\d{1,2}\s*(?:am|pm)\s*", "", text, flags=re.I).strip()
        if not desc:
            desc = "Reminder"
        return desc, target

    return None


def detect_reminder_intent(text: str) -> tuple[str, datetime] | None:
    """Detect reminder intent in natural conversation (no /remind prefix needed).

    Patterns detected:
    - "nhắc tao sau 30 phút uống nước"
    - "nhớ nhắc tao lúc 3 giờ đi họp"
    - "remind me in 2 hours to check email"
    - "đừng quên nhắc tao sau 1 tiếng"
    """
    text_lower = text.lower()

    # Check for Vietnamese reminder triggers
    vi_triggers = [
        r"nhắc\s+(?:tao|tôi|mình|em|anh|chị)",
        r"nhớ\s+nhắc",
        r"đừng\s+(?:quên\s+)?nhắc",
        r"hẹn\s+(?:tao|tôi)",
    ]

    # Check for English reminder triggers
    en_triggers = [
        r"remind\s+me",
        r"don'?t\s+forget\s+to",
        r"set\s+(?:a\s+)?reminder",
    ]

    has_trigger = False
    for pattern in vi_triggers + en_triggers:
        if re.search(pattern, text_lower):
            has_trigger = True
            break

    if not has_trigger:
        return None

    # Try to parse time from the full text
    return parse_reminder_time(text)


def _unit_to_timedelta(unit: str, amount: int) -> timedelta | None:
    mapping = {
        "phút": timedelta(minutes=amount),
        "minute": timedelta(minutes=amount),
        "giờ": timedelta(hours=amount),
        "tiếng": timedelta(hours=amount),
        "hour": timedelta(hours=amount),
        "ngày": timedelta(days=amount),
        "day": timedelta(days=amount),
        "tuần": timedelta(weeks=amount),
        "week": timedelta(weeks=amount),
    }
    return mapping.get(unit)
