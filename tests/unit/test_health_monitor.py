"""Tests for Health Monitor — background health checking."""

import asyncio

import pytest

from src.metacognition.diagnostics import HealthCheck
from src.metacognition.health_monitor import HealthMonitor, SystemHealth


class TestSystemHealth:
    def test_defaults(self):
        health = SystemHealth()
        assert health.api_keys_ok
        assert health.disk_ok
        assert health.db_ok


class TestHealthMonitor:
    @pytest.fixture
    def monitor(self):
        return HealthMonitor()

    def test_init(self, monitor):
        assert monitor.health.api_keys_ok
        assert monitor.health.disk_ok
        assert monitor.health.db_ok

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
    async def test_ollama_check_skipped(self, monitor):
        """Ollama check should be skipped (no local models)."""
        checks = [
            HealthCheck(name="Ollama", status="error", message="Not reachable"),
            HealthCheck(name="API Keys", status="ok", message="All keys set"),
            HealthCheck(name="Disk Space", status="ok", message="100GB free"),
            HealthCheck(name="Database", status="ok", message="OK"),
        ]
        await monitor._process_checks(checks)
        # No Ollama fields — health should remain default
        assert monitor.health.api_keys_ok
        assert monitor.health.disk_ok
        assert monitor.health.db_ok

    @pytest.mark.asyncio
    async def test_api_keys_alert(self, monitor):
        """Should alert when API keys go from ok to missing."""
        alerts_sent = []

        async def mock_notify(user_id: str, message: str) -> None:
            alerts_sent.append((user_id, message))

        monitor.set_notification_callback(mock_notify, ["user1"])

        checks = [
            HealthCheck(name="API Keys", status="error", message="ANTHROPIC_API_KEY missing"),
        ]
        await monitor._process_checks(checks)

        assert len(alerts_sent) == 1
        assert "user1" == alerts_sent[0][0]
        assert "API Keys" in alerts_sent[0][1]
        assert not monitor.health.api_keys_ok

    @pytest.mark.asyncio
    async def test_alert_cooldown(self, monitor):
        """Should not spam alerts — respects cooldown."""
        alerts_sent = []

        async def mock_notify(user_id: str, message: str) -> None:
            alerts_sent.append(message)

        monitor.set_notification_callback(mock_notify, ["user1"])

        checks = [
            HealthCheck(name="API Keys", status="error", message="Missing key"),
        ]

        # First alert
        await monitor._process_checks(checks)
        count_after_first = len(alerts_sent)
        assert count_after_first == 1

        # Second alert (same issue, should be suppressed by cooldown)
        monitor._health.api_keys_ok = True  # Reset so transition triggers
        await monitor._process_checks(checks)
        # Cooldown should suppress — same alert_key within 30 min
        assert len(alerts_sent) == count_after_first

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

    @pytest.mark.asyncio
    async def test_disk_warning_alert(self, monitor):
        """Disk warning should send alert when going from ok to warning."""
        alerts_sent = []

        async def mock_notify(user_id: str, message: str) -> None:
            alerts_sent.append(message)

        monitor.set_notification_callback(mock_notify, ["user1"])

        checks = [
            HealthCheck(name="Disk Space", status="warning", message="5GB free"),
        ]
        await monitor._process_checks(checks)
        assert len(alerts_sent) == 1
        assert "Disk" in alerts_sent[0]

    @pytest.mark.asyncio
    async def test_disk_error_alert(self, monitor):
        """Disk error should send critical alert."""
        alerts_sent = []

        async def mock_notify(user_id: str, message: str) -> None:
            alerts_sent.append(message)

        monitor.set_notification_callback(mock_notify, ["user1"])

        checks = [
            HealthCheck(name="Disk Space", status="error", message="0.5GB free"),
        ]
        await monitor._process_checks(checks)
        assert len(alerts_sent) == 1
        assert "Disk" in alerts_sent[0]
        assert not monitor.health.disk_ok

    @pytest.mark.asyncio
    async def test_no_callback_no_crash(self, monitor):
        """Should not crash if no notification callback set."""
        checks = [
            HealthCheck(name="API Keys", status="error", message="Missing"),
        ]
        # Should not raise — just logs
        await monitor._process_checks(checks)
        assert not monitor.health.api_keys_ok
