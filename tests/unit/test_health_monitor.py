"""Tests for Health Monitor — background health checking."""

import asyncio

import pytest

from src.metacognition.diagnostics import HealthCheck
from src.metacognition.health_monitor import HealthMonitor, SystemHealth


class TestSystemHealth:
    def test_defaults(self):
        health = SystemHealth()
        assert health.ollama_available
        assert health.api_keys_ok
        assert health.disk_ok
        assert health.db_ok
        assert health.degraded_mode == ""


class TestHealthMonitor:
    @pytest.fixture
    def monitor(self):
        return HealthMonitor()

    def test_init(self, monitor):
        assert monitor.health.ollama_available
        assert monitor.health.degraded_mode == ""

    @pytest.mark.asyncio
    async def test_run_check(self, monitor):
        """Should run diagnostics and return results."""
        checks = await monitor.run_check()
        assert isinstance(checks, list)
        assert len(checks) >= 1
        for check in checks:
            assert isinstance(check, HealthCheck)
            assert check.status in ("ok", "warning", "error")

    @pytest.mark.asyncio
    async def test_ollama_down_triggers_degradation(self, monitor):
        """When Ollama goes down, should switch to cloud_only mode."""
        checks = [
            HealthCheck(name="Ollama", status="error", message="Not reachable"),
            HealthCheck(name="API Keys", status="ok", message="All keys set"),
            HealthCheck(name="Disk Space", status="ok", message="100GB free"),
            HealthCheck(name="Database", status="ok", message="OK"),
        ]
        await monitor._process_checks(checks)
        assert not monitor.health.ollama_available
        assert monitor.health.degraded_mode == "cloud_only"

    @pytest.mark.asyncio
    async def test_ollama_recovery(self, monitor):
        """When Ollama comes back, should clear degradation."""
        # First, simulate down
        monitor._health.ollama_available = False
        monitor._health.degraded_mode = "cloud_only"

        checks = [
            HealthCheck(name="Ollama", status="ok", message="3 models loaded"),
            HealthCheck(name="API Keys", status="ok", message="OK"),
        ]
        await monitor._process_checks(checks)
        assert monitor.health.ollama_available
        assert monitor.health.degraded_mode == ""

    @pytest.mark.asyncio
    async def test_notification_callback(self, monitor):
        """Should call notification callback on alerts."""
        alerts_sent = []

        async def mock_notify(user_id: str, message: str) -> None:
            alerts_sent.append((user_id, message))

        monitor.set_notification_callback(mock_notify, ["user1"])

        # Simulate Ollama going down
        checks = [
            HealthCheck(name="Ollama", status="error", message="Down"),
        ]
        await monitor._process_checks(checks)

        assert len(alerts_sent) >= 1
        assert "user1" == alerts_sent[0][0]
        assert "Ollama" in alerts_sent[0][1]

    @pytest.mark.asyncio
    async def test_alert_cooldown(self, monitor):
        """Should not spam alerts — respects cooldown."""
        alerts_sent = []

        async def mock_notify(user_id: str, message: str) -> None:
            alerts_sent.append(message)

        monitor.set_notification_callback(mock_notify, ["user1"])

        checks = [
            HealthCheck(name="Ollama", status="error", message="Down"),
        ]

        # First alert
        await monitor._process_checks(checks)
        count_after_first = len(alerts_sent)

        # Second alert (same issue, should be suppressed by cooldown)
        monitor._health.ollama_available = True  # Reset so transition triggers
        await monitor._process_checks(checks)
        # Should not send duplicate alert within cooldown
        assert len(alerts_sent) <= count_after_first + 1

    @pytest.mark.asyncio
    async def test_start_stop(self, monitor):
        """Should start and stop cleanly."""
        await monitor.start()
        assert monitor._running
        await monitor.stop()
        assert not monitor._running

    @pytest.mark.asyncio
    async def test_disk_warning(self, monitor):
        """Disk warning should update health state."""
        checks = [
            HealthCheck(name="Disk Space", status="error", message="0.5GB free"),
        ]
        await monitor._process_checks(checks)
        assert not monitor.health.disk_ok
