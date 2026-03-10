"""Health Monitor — Background health monitoring with proactive alerts.

Runs periodic health checks and notifies users when systems degrade.
Monitors API keys, disk space, database, and other system health.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Awaitable

from src.metacognition.diagnostics import HealthCheck, SelfDiagnostics
from src.utils.logging import get_logger

log = get_logger("metacognition.health_monitor")

# Check interval in seconds
_CHECK_INTERVAL = 300  # 5 minutes

# Only alert once per issue (don't spam)
_ALERT_COOLDOWN = 1800  # 30 minutes


@dataclass
class SystemHealth:
    """Current system health state."""
    api_keys_ok: bool = True
    disk_ok: bool = True
    db_ok: bool = True


class HealthMonitor:
    """Background health monitor with proactive user alerts."""

    def __init__(self) -> None:
        self._diag = SelfDiagnostics()
        self._health = SystemHealth()
        self._task: asyncio.Task | None = None
        self._notify_callback: Callable[[str, str], Awaitable[None]] | None = None
        self._alert_user_ids: list[str] = []
        self._last_alerts: dict[str, float] = {}  # issue → timestamp
        self._running = False

    @property
    def health(self) -> SystemHealth:
        return self._health

    def set_notification_callback(
        self,
        callback: Callable[[str, str], Awaitable[None]],
        user_ids: list[str],
    ) -> None:
        """Set callback for proactive notifications."""
        self._notify_callback = callback
        self._alert_user_ids = user_ids

    async def start(self) -> None:
        """Start background health monitoring."""
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        log.info("health_monitor_started", interval=_CHECK_INTERVAL)

    async def stop(self) -> None:
        """Stop the health monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("health_monitor_stopped")

    async def run_check(self) -> list[HealthCheck]:
        """Run a single health check cycle and update state."""
        checks = await self._diag.run_all()
        await self._process_checks(checks)
        return checks

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        # Initial check after 30 seconds (let system stabilize)
        await asyncio.sleep(30)

        while self._running:
            try:
                await self.run_check()
            except Exception as e:
                log.error("health_check_error", error=str(e))
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _process_checks(self, checks: list[HealthCheck]) -> None:
        """Process health check results and send alerts."""
        import time

        now = time.time()
        alerts: list[str] = []

        for check in checks:
            if check.name == "Ollama":
                continue  # No local models — skip Ollama check

            elif check.name == "API Keys":
                was_ok = self._health.api_keys_ok
                self._health.api_keys_ok = check.status == "ok"
                if was_ok and not self._health.api_keys_ok:
                    alerts.append(
                        f"❌ **API Keys bị thiếu**: {check.message}\n"
                        "Một số tính năng có thể không hoạt động."
                    )

            elif check.name == "Disk Space":
                was_ok = self._health.disk_ok
                self._health.disk_ok = check.status != "error"
                if check.status == "error":
                    alerts.append(
                        f"🔴 **Disk gần đầy**: {check.message}\n"
                        "Hãy dọn dẹp để tránh mất dữ liệu."
                    )
                elif check.status == "warning" and was_ok:
                    alerts.append(
                        f"⚠️ **Disk sắp đầy**: {check.message}"
                    )

            elif check.name == "Database":
                self._health.db_ok = check.status == "ok"

        # Send alerts (with cooldown to avoid spam)
        for alert in alerts:
            alert_key = alert[:50]  # Use first 50 chars as dedup key
            last_sent = self._last_alerts.get(alert_key, 0)
            if now - last_sent >= _ALERT_COOLDOWN:
                self._last_alerts[alert_key] = now
                await self._send_alert(alert)

        if checks:
            statuses = {c.name: c.status for c in checks}
            log.debug("health_check_complete", statuses=statuses)

    async def _send_alert(self, message: str) -> None:
        """Send alert to all registered users."""
        if not self._notify_callback or not self._alert_user_ids:
            log.info("health_alert_no_callback", message=message[:100])
            return

        for user_id in self._alert_user_ids:
            try:
                await self._notify_callback(user_id, message)
                log.info("health_alert_sent", user_id=user_id)
            except Exception as e:
                log.error("health_alert_failed", user_id=user_id, error=str(e))
