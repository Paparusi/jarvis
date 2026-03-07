"""Tests for Bug Bounty Pipeline integration (app, telegram, cli)."""

from __future__ import annotations

import sqlite3

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.bounty.models import BountyFinding, FindingStatus, Confidence
from src.bounty.pipeline import BountyPipeline
from src.bounty.store import init_bounty_tables


class TestAppBountyInit:
    """Test JarvisApp.init_bounty() integration."""

    def test_bounty_pipeline_none_before_init(self):
        """bounty_pipeline should be None before init_bounty()."""
        with patch("src.app.SkillLoader") as mock_loader:
            mock_loader.return_value.load_all.return_value = None
            mock_loader.return_value.get_all_metadata.return_value = []
            mock_loader.return_value.get_metadata_summary.return_value = ""
            with patch("src.app.LLMRouter"):
                with patch("src.app.MemoryManager"):
                    with patch("src.app.ToolRegistry") as mock_tr:
                        mock_tr.return_value.get_all.return_value = []
                        from src.app import JarvisApp

                        app = JarvisApp()
                        assert app.bounty_pipeline is None

    def test_init_bounty_creates_pipeline(self):
        """init_bounty() should create a BountyPipeline instance."""
        with patch("src.app.SkillLoader") as mock_loader:
            mock_loader.return_value.load_all.return_value = None
            mock_loader.return_value.get_all_metadata.return_value = []
            mock_loader.return_value.get_metadata_summary.return_value = ""
            with patch("src.app.LLMRouter"):
                with patch("src.app.MemoryManager"):
                    with patch("src.app.ToolRegistry") as mock_tr:
                        mock_tr.return_value.get_all.return_value = []
                        from src.app import JarvisApp

                        app = JarvisApp()
                        with patch("src.bounty.store.get_connection") as mock_conn:
                            conn = sqlite3.connect(":memory:")
                            conn.row_factory = sqlite3.Row
                            mock_conn.return_value = conn
                            app.init_bounty()
                            assert app.bounty_pipeline is not None
                            assert isinstance(app.bounty_pipeline, BountyPipeline)


class TestAppBountyShutdown:
    """Test shutdown stops bounty pipeline."""

    @pytest.mark.asyncio
    async def test_shutdown_stops_running_pipeline(self):
        """shutdown() should stop a running bounty pipeline."""
        with patch("src.app.SkillLoader") as mock_loader:
            mock_loader.return_value.load_all.return_value = None
            mock_loader.return_value.get_all_metadata.return_value = []
            mock_loader.return_value.get_metadata_summary.return_value = ""
            with patch("src.app.LLMRouter"):
                with patch("src.app.MemoryManager"):
                    with patch("src.app.ToolRegistry") as mock_tr:
                        mock_tr.return_value.get_all.return_value = []
                        from src.app import JarvisApp

                        app = JarvisApp()
                        # Create a mock pipeline that is running
                        mock_pipeline = MagicMock()
                        mock_pipeline.is_running = True
                        mock_pipeline.stop = AsyncMock()
                        app.bounty_pipeline = mock_pipeline

                        await app.shutdown()
                        mock_pipeline.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_skips_stopped_pipeline(self):
        """shutdown() should not call stop on a non-running pipeline."""
        with patch("src.app.SkillLoader") as mock_loader:
            mock_loader.return_value.load_all.return_value = None
            mock_loader.return_value.get_all_metadata.return_value = []
            mock_loader.return_value.get_metadata_summary.return_value = ""
            with patch("src.app.LLMRouter"):
                with patch("src.app.MemoryManager"):
                    with patch("src.app.ToolRegistry") as mock_tr:
                        mock_tr.return_value.get_all.return_value = []
                        from src.app import JarvisApp

                        app = JarvisApp()
                        mock_pipeline = MagicMock()
                        mock_pipeline.is_running = False
                        mock_pipeline.stop = AsyncMock()
                        app.bounty_pipeline = mock_pipeline

                        await app.shutdown()
                        mock_pipeline.stop.assert_not_awaited()


class TestBountyCommandParsing:
    """Test bounty subcommand parsing for CLI and Telegram."""

    @pytest.fixture
    def pipeline(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_bounty_tables(conn)
        tr = MagicMock()
        return BountyPipeline(conn, tr)

    def test_stats_empty(self, pipeline):
        stats = pipeline.stats()
        assert stats["running"] is False
        assert stats["programs"] == 0
        assert stats["findings"]["pending"] == 0
        assert stats["findings"]["total"] == 0
        assert stats["earnings_usd"] == 0

    def test_save_and_get_pending(self, pipeline):
        finding = BountyFinding(
            target_id=1,
            vuln_type="xss",
            severity="HIGH",
            cvss=7.5,
            confidence=0.92,
            title="Reflected XSS on /search",
            description="XSS via q parameter",
            status=FindingStatus.PENDING,
        )
        fid = pipeline.save_finding(finding)
        assert fid > 0
        pending = pipeline.get_pending_findings()
        assert len(pending) == 1
        assert pending[0].title == "Reflected XSS on /search"

    def test_update_finding_status(self, pipeline):
        finding = BountyFinding(
            target_id=1,
            vuln_type="sqli",
            severity="CRITICAL",
            cvss=9.8,
            confidence=0.95,
            title="SQL Injection in /api/users",
            status=FindingStatus.PENDING,
        )
        fid = pipeline.save_finding(finding)
        pipeline.update_finding_status(fid, "approved")
        # Should no longer appear in pending
        pending = pipeline.get_pending_findings()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_start_stop(self, pipeline):
        assert not pipeline.is_running
        await pipeline.start(interval_hours=1)
        assert pipeline.is_running
        await pipeline.stop()
        assert not pipeline.is_running
