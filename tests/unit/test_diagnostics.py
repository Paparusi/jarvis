"""Tests for Self-Diagnostics."""

import pytest

from src.metacognition.diagnostics import HealthCheck, SelfDiagnostics


class TestHealthCheck:
    def test_ok_emoji(self):
        check = HealthCheck(name="Test", status="ok", message="All good")
        assert check.emoji == "\u2705"  # green checkmark

    def test_warning_emoji(self):
        check = HealthCheck(name="Test", status="warning", message="Watch out")
        assert check.emoji == "\u26a0\ufe0f"  # warning sign

    def test_error_emoji(self):
        check = HealthCheck(name="Test", status="error", message="Failed")
        assert check.emoji == "\u274c"  # red X

    def test_unknown_emoji(self):
        check = HealthCheck(name="Test", status="unknown", message="?")
        assert check.emoji == "\u2753"  # question mark

    def test_details_default(self):
        check = HealthCheck(name="Test", status="ok", message="OK")
        assert check.details == {}


class TestSelfDiagnostics:
    def setup_method(self):
        self.diag = SelfDiagnostics()

    @pytest.mark.asyncio
    async def test_check_api_keys(self):
        result = await self.diag.check_api_keys()
        assert result.name == "API Keys"
        assert result.status in ("ok", "error")

    @pytest.mark.asyncio
    async def test_check_disk_space(self):
        result = await self.diag.check_disk_space()
        assert result.name == "Disk Space"
        assert result.status in ("ok", "warning", "error")
        assert "GB" in result.message
        assert result.details.get("free_gb", 0) > 0

    @pytest.mark.asyncio
    async def test_check_database(self):
        result = await self.diag.check_database()
        assert result.name == "Database"
        assert result.status in ("ok", "warning")

    @pytest.mark.asyncio
    async def test_check_training_data(self):
        result = await self.diag.check_training_data()
        assert result.name == "Training Data"
        assert result.status in ("ok", "warning")

    @pytest.mark.asyncio
    async def test_run_all(self):
        results = await self.diag.run_all()
        assert len(results) == 5
        for r in results:
            assert isinstance(r, HealthCheck)
            assert r.status in ("ok", "warning", "error")

    def test_format_report(self):
        checks = [
            HealthCheck(name="Test A", status="ok", message="All good"),
            HealthCheck(name="Test B", status="warning", message="Watch out"),
            HealthCheck(name="Test C", status="error", message="Failed"),
        ]
        report = self.diag.format_report(checks)
        assert "JARVIS Health Report" in report
        assert "Test A" in report
        assert "Test B" in report
        assert "Test C" in report
        assert "1/3 checks passed" in report

    def test_format_report_all_ok(self):
        checks = [
            HealthCheck(name="A", status="ok", message="Good"),
            HealthCheck(name="B", status="ok", message="Good"),
        ]
        report = self.diag.format_report(checks)
        assert "2/2 checks passed" in report
