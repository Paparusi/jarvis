"""Dreamtime Scheduler — Trigger offline processing during idle periods.

Triggers:
1. Idle trigger: 30+ minutes without user activity
2. Cron trigger: Daily at 2:00 AM UTC (configurable)

Dreamtime tasks (in order):
1. Memory consolidation (dedup, strengthen, decay)
2. Dream analysis (skill performance, failure patterns, suggestions)
3. Skill optimization (future: GEPA auto-improve)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.utils.logging import get_logger

log = get_logger("dreamtime.scheduler")


class DreamtimeScheduler:
    """Schedule and run Dreamtime tasks during idle periods."""

    def __init__(
        self,
        idle_minutes: int = 30,
        cron_hour: int = 2,  # UTC hour for daily run
        enabled: bool = True,
    ) -> None:
        self._idle_minutes = idle_minutes
        self._cron_hour = cron_hour
        self._enabled = enabled
        self._last_activity = datetime.now(timezone.utc)
        self._last_dream = datetime.min.replace(tzinfo=timezone.utc)
        self._running = False
        self._task: asyncio.Task | None = None
        self._dream_callback = None

    def record_activity(self) -> None:
        """Record user activity — resets idle timer."""
        self._last_activity = datetime.now(timezone.utc)

    def set_dream_callback(self, callback) -> None:
        """Set the callback that runs during dreamtime.

        Callback signature: async def callback() -> dict
        """
        self._dream_callback = callback

    async def start(self) -> None:
        """Start the dreamtime scheduler loop."""
        if not self._enabled:
            log.info("dreamtime_disabled")
            return

        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        log.info("dreamtime_scheduler_started",
                idle_minutes=self._idle_minutes,
                cron_hour=self._cron_hour)

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("dreamtime_scheduler_stopped")

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop — check triggers every 5 minutes."""
        while self._running:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes

                now = datetime.now(timezone.utc)

                # Check idle trigger
                idle_seconds = (now - self._last_activity).total_seconds()
                idle_minutes = idle_seconds / 60

                # Check cron trigger (daily at configured hour)
                is_cron_time = (
                    now.hour == self._cron_hour
                    and (now - self._last_dream).total_seconds() > 3600 * 20  # Min 20h gap
                )

                should_dream = (
                    (idle_minutes >= self._idle_minutes or is_cron_time)
                    and self._dream_callback is not None
                )

                if should_dream:
                    trigger = "cron" if is_cron_time else "idle"
                    log.info("dreamtime_triggered",
                            trigger=trigger,
                            idle_minutes=f"{idle_minutes:.0f}")

                    try:
                        result = await self._dream_callback()
                        self._last_dream = now
                        log.info("dreamtime_completed", result=str(result)[:200])
                    except Exception as e:
                        log.error("dreamtime_error", error=str(e))

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("scheduler_loop_error", error=str(e))

    async def run_now(self) -> dict:
        """Manually trigger a dreamtime cycle (e.g., from /train command)."""
        if not self._dream_callback:
            return {"error": "No dream callback configured"}

        log.info("dreamtime_manual_trigger")
        try:
            result = await self._dream_callback()
            self._last_dream = datetime.now(timezone.utc)
            return result
        except Exception as e:
            log.error("dreamtime_manual_error", error=str(e))
            return {"error": str(e)}

    def get_stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "idle_minutes": self._idle_minutes,
            "last_activity": self._last_activity.isoformat(),
            "last_dream": self._last_dream.isoformat() if self._last_dream.year > 1 else "never",
            "running": self._running,
        }
