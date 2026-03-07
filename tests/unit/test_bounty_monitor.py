"""Tests for Bug Bounty Pipeline — Program Monitor."""

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.bounty.models import BountyProgram
from src.bounty.monitor import ProgramMonitor
from src.bounty.store import init_bounty_tables


@pytest.fixture
def conn():
    """Create an in-memory SQLite database with bounty tables."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    init_bounty_tables(db)
    yield db
    db.close()


@pytest.fixture
def monitor(conn):
    """Create a ProgramMonitor with the test database."""
    return ProgramMonitor(conn)


class TestProgramMonitorInit:
    """Test ProgramMonitor initialisation."""

    def test_init_creates_tables(self, conn):
        monitor = ProgramMonitor(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bounty_programs'"
        )
        assert cur.fetchone() is not None

    def test_init_stores_connection(self, conn):
        monitor = ProgramMonitor(conn)
        assert monitor.conn is conn


class TestCalculatePriority:
    """Test priority scoring logic."""

    def test_new_high_bounty_low_competition(self, monitor):
        """Brand new program (<7d), high bounty (>=10k), few reports (<=10) = max."""
        launch = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        score = monitor.calculate_priority(
            bounty_high=15000, launch_date=launch, report_count=5
        )
        # 0.4 (new) + 0.4 (bounty) + 0.2 (competition) = 1.0
        assert score == 1.0

    def test_old_low_bounty_high_competition(self, monitor):
        """Old program (>90d), low bounty (<100), many reports (>50) = 0.0."""
        launch = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        score = monitor.calculate_priority(
            bounty_high=50, launch_date=launch, report_count=100
        )
        assert score == 0.0

    def test_moderate_priority(self, monitor):
        """Program <30d, bounty >=5k, reports <=50."""
        launch = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        score = monitor.calculate_priority(
            bounty_high=6000, launch_date=launch, report_count=30
        )
        # 0.2 (30d) + 0.3 (5k) + 0.1 (50 reports) = 0.6
        assert score == pytest.approx(0.6)

    def test_no_launch_date(self, monitor):
        """Missing launch date should not crash, just skip newness bonus."""
        score = monitor.calculate_priority(bounty_high=2000, report_count=5)
        # 0.0 (no date) + 0.2 (1k bounty) + 0.2 (10 reports) = 0.4
        assert score == pytest.approx(0.4)

    def test_cap_at_one(self, monitor):
        """Score should never exceed 1.0."""
        launch = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        score = monitor.calculate_priority(
            bounty_high=50000, launch_date=launch, report_count=1
        )
        assert score <= 1.0


class TestSaveProgram:
    """Test save_program insert and upsert."""

    def test_save_program_insert(self, monitor, conn):
        prog = BountyProgram(
            platform="hackerone",
            program_id="acme",
            name="Acme Corp",
            url="https://hackerone.com/acme",
            scope_domains=["acme.com", "*.acme.com"],
            bounty_low=100,
            bounty_high=10000,
            priority_score=0.8,
        )
        monitor.save_program(prog)

        row = conn.execute(
            "SELECT * FROM bounty_programs WHERE program_id = ?", ("acme",)
        ).fetchone()
        assert row is not None
        assert row["platform"] == "hackerone"
        assert row["name"] == "Acme Corp"
        assert row["bounty_high"] == 10000
        assert json.loads(row["scope_domains"]) == ["acme.com", "*.acme.com"]
        assert row["priority_score"] == pytest.approx(0.8)

    def test_save_program_upsert(self, monitor, conn):
        """Saving twice with same platform+program_id should update, not duplicate."""
        prog_v1 = BountyProgram(
            platform="hackerone",
            program_id="acme",
            name="Acme Corp v1",
            bounty_high=5000,
            priority_score=0.5,
        )
        monitor.save_program(prog_v1)

        prog_v2 = BountyProgram(
            platform="hackerone",
            program_id="acme",
            name="Acme Corp v2",
            bounty_high=15000,
            priority_score=0.9,
        )
        monitor.save_program(prog_v2)

        rows = conn.execute(
            "SELECT * FROM bounty_programs WHERE program_id = ?", ("acme",)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "Acme Corp v2"
        assert rows[0]["bounty_high"] == 15000
        assert rows[0]["priority_score"] == pytest.approx(0.9)


class TestGetActivePrograms:
    """Test get_active_programs retrieval and ordering."""

    def test_get_active_programs_sorted(self, monitor):
        """Active programs should be returned sorted by priority_score DESC."""
        for name, score, status in [
            ("Low", 0.2, "active"),
            ("High", 0.9, "active"),
            ("Mid", 0.5, "active"),
            ("Inactive", 0.8, "inactive"),
        ]:
            prog = BountyProgram(
                platform="hackerone",
                program_id=name.lower(),
                name=name,
                priority_score=score,
                status=status,
            )
            monitor.save_program(prog)

        active = monitor.get_active_programs()
        assert len(active) == 3  # Inactive excluded
        assert active[0].name == "High"
        assert active[1].name == "Mid"
        assert active[2].name == "Low"

    def test_get_active_programs_parses_scope_domains(self, monitor):
        prog = BountyProgram(
            platform="hackerone",
            program_id="scope_test",
            name="Scope Test",
            scope_domains=["a.com", "b.com"],
        )
        monitor.save_program(prog)
        result = monitor.get_active_programs()
        assert len(result) == 1
        assert result[0].scope_domains == ["a.com", "b.com"]


class TestFetchHackerOne:
    """Test HackerOne API fetching with mocked httpx."""

    @pytest.mark.asyncio
    async def test_fetch_hackerone_programs(self, monitor):
        """Mocked HackerOne API response should be parsed into BountyProgram list."""
        mock_response_data = {
            "data": [
                {
                    "id": "prog1",
                    "attributes": {
                        "handle": "test-program",
                        "name": "Test Program",
                        "offers_bounties": True,
                        "state": "public_mode",
                        "top_bounty_range": 5000,
                        "low_bounty_range": 100,
                        "started_accepting_at": datetime.now(timezone.utc).isoformat(),
                        "number_of_reports_for_user": 5,
                    },
                    "relationships": {
                        "structured_scopes": {
                            "data": [
                                {
                                    "attributes": {
                                        "asset_type": "URL",
                                        "asset_identifier": "test.com",
                                    }
                                },
                                {
                                    "attributes": {
                                        "asset_type": "URL",
                                        "asset_identifier": "api.test.com",
                                    }
                                },
                            ]
                        }
                    },
                },
                {
                    "id": "prog2",
                    "attributes": {
                        "handle": "no-bounty",
                        "name": "No Bounty",
                        "offers_bounties": False,
                        "state": "public_mode",
                    },
                    "relationships": {},
                },
            ],
            "links": {},
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_resp.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"HACKERONE_API_TOKEN": "test-token"}):
            with patch("src.bounty.monitor.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get.return_value = mock_resp
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                programs = await monitor.fetch_hackerone_programs()

        assert len(programs) == 1
        assert programs[0].program_id == "test-program"
        assert programs[0].name == "Test Program"
        assert "test.com" in programs[0].scope_domains
        assert "api.test.com" in programs[0].scope_domains
        assert programs[0].bounty_high == 5000

    @pytest.mark.asyncio
    async def test_fetch_hackerone_no_token(self, monitor):
        """Without API token, fetch should return empty list."""
        with patch.dict("os.environ", {}, clear=False):
            with patch("src.utils.config.os.getenv", return_value=""):
                programs = await monitor.fetch_hackerone_programs()
        assert programs == []
