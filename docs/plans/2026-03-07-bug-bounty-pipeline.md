# Bug Bounty Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an autonomous bug bounty hunting pipeline that monitors programs, scans targets, verifies findings, generates reports, and notifies Bi via Telegram for review + submission.

**Architecture:** 8-module pipeline (`src/bounty/`) backed by SQLite tables in `data/jarvis.db`. Reuses existing security tools (recon, web_attack, osint, threat_intel) via ToolRegistry. Core loop: Monitor → Queue → Recon → Scan → Verify → Report → Notify → Track. Runs as background asyncio task, controlled via `/bounty` Telegram command.

**Tech Stack:** Python 3.11+, asyncio, httpx (HTTP), SQLite, existing ToolRegistry + security tools, Playwright (verification), Telegram bot API, Prometheus metrics.

**Design doc:** `docs/plans/2026-03-07-bug-bounty-pipeline-design.md`

---

## Context for the Implementer

**Project structure:** JARVIS v2 at `~/projects/jarvis`. Source in `src/`, tests in `tests/unit/`.

**Key patterns to follow:**
- All async functions return domain-specific dataclasses
- SQLite tables created via `_init_tables()` in the module's store (see `src/memory/store.py:44-89`)
- Tools follow `async def tool_name(params) -> ToolResult` pattern (see `src/tools/recon.py`)
- ToolDefinition objects exported as `<name>_tool` variables (see `src/tools/recon.py:600+`)
- All tools registered centrally in `src/tools/registry_all.py` ALL_TOOLS list
- Telegram commands: `CommandHandler("name", self._handle_name)` in `start()` method
- Tests: pytest + pytest-asyncio, class-based in `tests/unit/`
- Input validation: `_validate_domain()`, `_validate_input()` with dangerous char blocking

**Existing files you'll reference often:**
- `src/tools/base.py` — ToolDefinition, ToolResult, ToolRegistry, ToolParameter
- `src/tools/registry_all.py` — ALL_TOOLS central list
- `src/memory/store.py` — get_connection(), _init_tables() pattern
- `src/intelligence/pentest.py` — Finding, Severity, PentestPipeline pattern
- `src/intelligence/report_generator.py` — ReportGenerator pattern
- `src/gateway/channels/telegram.py` — command handler pattern (line 287-318)

**Run tests:** `cd ~/projects/jarvis && python -m pytest tests/unit/ -x -q`

---

### Task 1: Database Schema — Bounty Tables

Create the SQLite storage layer for the bounty pipeline.

**Files:**
- Create: `src/bounty/__init__.py`
- Create: `src/bounty/store.py`
- Test: `tests/unit/test_bounty_store.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_store.py`:

```python
"""Tests for bounty pipeline SQLite store."""

from __future__ import annotations

import sqlite3

import pytest

from src.bounty.store import init_bounty_tables, get_bounty_connection


class TestBountyTables:
    """Verify all bounty tables are created correctly."""

    def setup_method(self):
        """Use in-memory DB for each test."""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_bounty_tables(self.conn)

    def teardown_method(self):
        self.conn.close()

    def test_bounty_programs_table_exists(self):
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bounty_programs'"
        )
        assert cur.fetchone() is not None

    def test_bounty_targets_table_exists(self):
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bounty_targets'"
        )
        assert cur.fetchone() is not None

    def test_bounty_findings_table_exists(self):
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bounty_findings'"
        )
        assert cur.fetchone() is not None

    def test_bounty_earnings_table_exists(self):
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bounty_earnings'"
        )
        assert cur.fetchone() is not None

    def test_insert_program(self):
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name, url, scope_domains) "
            "VALUES (?, ?, ?, ?, ?)",
            ("hackerone", "test_prog", "Test Program", "https://hackerone.com/test", '["*.test.com"]'),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM bounty_programs WHERE program_id='test_prog'").fetchone()
        assert row["name"] == "Test Program"
        assert row["platform"] == "hackerone"
        assert row["status"] == "active"

    def test_insert_target(self):
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
            ("hackerone", "p1", "P1"),
        )
        prog_id = self.conn.execute("SELECT id FROM bounty_programs WHERE program_id='p1'").fetchone()["id"]
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain, scope_type) VALUES (?, ?, ?)",
            (prog_id, "test.com", "domain"),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM bounty_targets WHERE domain='test.com'").fetchone()
        assert row["state"] == "queued"
        assert row["scan_count"] == 0

    def test_insert_finding(self):
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES (?, ?, ?)",
            ("hackerone", "p1", "P1"),
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain) VALUES (1, 'test.com')",
        )
        self.conn.execute(
            "INSERT INTO bounty_findings (target_id, vuln_type, severity, cvss, confidence, title) "
            "VALUES (1, 'xss', 'HIGH', 7.5, 0.92, 'Reflected XSS in search')",
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM bounty_findings WHERE id=1").fetchone()
        assert row["vuln_type"] == "xss"
        assert row["severity"] == "HIGH"
        assert row["status"] == "pending"

    def test_unique_constraint_program(self):
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES ('hackerone', 'dup', 'D')"
        )
        self.conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO bounty_programs (platform, program_id, name) VALUES ('hackerone', 'dup', 'D2')"
            )

    def test_unique_constraint_target(self):
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES ('hackerone', 'p1', 'P')"
        )
        self.conn.execute("INSERT INTO bounty_targets (program_id, domain) VALUES (1, 'test.com')")
        self.conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO bounty_targets (program_id, domain) VALUES (1, 'test.com')")


class TestBountyConnection:
    """Test connection helper uses shared JARVIS db."""

    def test_get_bounty_connection_returns_connection(self):
        conn = get_bounty_connection()
        assert conn is not None
        # Verify bounty tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bounty_%'"
        ).fetchall()
        assert len(tables) >= 4
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.bounty'`

**Step 3: Create the bounty package and store module**

Create `src/bounty/__init__.py`:
```python
"""JARVIS Bug Bounty Pipeline — autonomous vulnerability hunting."""
```

Create `src/bounty/store.py`:
```python
"""SQLite storage for the Bug Bounty pipeline.

Tables: bounty_programs, bounty_targets, bounty_findings, bounty_earnings.
Uses the shared JARVIS database (data/jarvis.db).
"""

from __future__ import annotations

import sqlite3

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("bounty.store")


def init_bounty_tables(conn: sqlite3.Connection) -> None:
    """Create bounty tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bounty_programs (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            program_id TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT,
            scope_domains TEXT,
            bounty_low INTEGER DEFAULT 0,
            bounty_high INTEGER DEFAULT 0,
            priority_score REAL DEFAULT 0.0,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_checked TIMESTAMP,
            UNIQUE(platform, program_id)
        );

        CREATE TABLE IF NOT EXISTS bounty_targets (
            id INTEGER PRIMARY KEY,
            program_id INTEGER REFERENCES bounty_programs(id),
            domain TEXT NOT NULL,
            scope_type TEXT DEFAULT 'domain',
            state TEXT DEFAULT 'queued',
            last_scanned TIMESTAMP,
            next_scan TIMESTAMP,
            scan_count INTEGER DEFAULT 0,
            findings_count INTEGER DEFAULT 0,
            UNIQUE(program_id, domain)
        );

        CREATE TABLE IF NOT EXISTS bounty_findings (
            id INTEGER PRIMARY KEY,
            target_id INTEGER REFERENCES bounty_targets(id),
            vuln_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            cvss REAL,
            confidence REAL,
            title TEXT NOT NULL,
            description TEXT,
            steps_to_reproduce TEXT,
            poc TEXT,
            impact TEXT,
            suggested_fix TEXT,
            estimated_bounty_low INTEGER,
            estimated_bounty_high INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bounty_earnings (
            id INTEGER PRIMARY KEY,
            finding_id INTEGER REFERENCES bounty_findings(id),
            platform TEXT,
            amount REAL,
            currency TEXT DEFAULT 'USD',
            paid_at TIMESTAMP
        );
    """)
    conn.commit()
    log.info("bounty_tables_initialized")


def get_bounty_connection() -> sqlite3.Connection:
    """Get the shared JARVIS DB connection with bounty tables initialized."""
    conn = get_connection()
    # Ensure bounty tables exist (idempotent)
    init_bounty_tables(conn)
    return conn
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bounty_store.py -v`
Expected: ALL PASS (11 tests)

**Step 5: Commit**

```bash
git add src/bounty/__init__.py src/bounty/store.py tests/unit/test_bounty_store.py
git commit -m "feat(bounty): add SQLite store with 4 bounty tables"
```

---

### Task 2: Data Models — BountyProgram, BountyTarget, BountyFinding

Dataclasses for type-safe pipeline data flow.

**Files:**
- Create: `src/bounty/models.py`
- Test: `tests/unit/test_bounty_models.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_models.py`:

```python
"""Tests for bounty data models."""

from __future__ import annotations

import json
import pytest

from src.bounty.models import (
    BountyProgram,
    BountyTarget,
    BountyFinding,
    TargetState,
    FindingStatus,
    Confidence,
)


class TestBountyProgram:
    def test_creation(self):
        p = BountyProgram(
            platform="hackerone",
            program_id="test_prog",
            name="Test Program",
            url="https://hackerone.com/test",
            scope_domains=["*.test.com", "api.test.com"],
            bounty_low=100,
            bounty_high=5000,
        )
        assert p.platform == "hackerone"
        assert p.name == "Test Program"
        assert len(p.scope_domains) == 2

    def test_priority_score_default(self):
        p = BountyProgram(platform="hackerone", program_id="x", name="X")
        assert p.priority_score == 0.0
        assert p.status == "active"

    def test_scope_domains_json(self):
        p = BountyProgram(
            platform="hackerone", program_id="x", name="X",
            scope_domains=["*.test.com"],
        )
        encoded = json.dumps(p.scope_domains)
        assert encoded == '["*.test.com"]'


class TestBountyTarget:
    def test_creation(self):
        t = BountyTarget(program_id=1, domain="test.com")
        assert t.state == TargetState.QUEUED
        assert t.scan_count == 0

    def test_state_transitions(self):
        assert TargetState.QUEUED.value == "queued"
        assert TargetState.SCANNING.value == "scanning"
        assert TargetState.SCANNED.value == "scanned"
        assert TargetState.RESCAN_SCHEDULED.value == "rescan_scheduled"


class TestBountyFinding:
    def test_creation(self):
        f = BountyFinding(
            target_id=1,
            vuln_type="xss",
            severity="HIGH",
            cvss=7.5,
            confidence=0.92,
            title="Reflected XSS in search",
            description="The search parameter is reflected without encoding.",
            poc="<script>alert(1)</script>",
        )
        assert f.vuln_type == "xss"
        assert f.status == FindingStatus.PENDING

    def test_confidence_level(self):
        f = BountyFinding(target_id=1, vuln_type="sqli", severity="CRITICAL",
                          cvss=9.8, confidence=0.95, title="SQLi")
        assert f.confidence_level == Confidence.CONFIRMED

    def test_confidence_level_likely(self):
        f = BountyFinding(target_id=1, vuln_type="xss", severity="HIGH",
                          cvss=7.0, confidence=0.75, title="XSS")
        assert f.confidence_level == Confidence.LIKELY

    def test_confidence_level_possible(self):
        f = BountyFinding(target_id=1, vuln_type="cors", severity="MEDIUM",
                          cvss=5.0, confidence=0.55, title="CORS")
        assert f.confidence_level == Confidence.POSSIBLE

    def test_confidence_level_false_positive(self):
        f = BountyFinding(target_id=1, vuln_type="lfi", severity="LOW",
                          cvss=3.0, confidence=0.30, title="LFI")
        assert f.confidence_level == Confidence.FALSE_POSITIVE

    def test_should_report(self):
        f = BountyFinding(target_id=1, vuln_type="sqli", severity="CRITICAL",
                          cvss=9.8, confidence=0.95, title="SQLi")
        assert f.should_report is True

    def test_should_not_report_low_confidence(self):
        f = BountyFinding(target_id=1, vuln_type="cors", severity="MEDIUM",
                          cvss=5.0, confidence=0.40, title="CORS maybe")
        assert f.should_report is False

    def test_estimated_bounty_str(self):
        f = BountyFinding(target_id=1, vuln_type="xss", severity="HIGH",
                          cvss=7.5, confidence=0.9, title="XSS",
                          estimated_bounty_low=500, estimated_bounty_high=2000)
        assert f.estimated_bounty_str == "$500-$2,000"

    def test_estimated_bounty_str_none(self):
        f = BountyFinding(target_id=1, vuln_type="xss", severity="HIGH",
                          cvss=7.5, confidence=0.9, title="XSS")
        assert f.estimated_bounty_str == "N/A"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.bounty.models'`

**Step 3: Write minimal implementation**

Create `src/bounty/models.py`:

```python
"""Data models for the Bug Bounty pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TargetState(str, Enum):
    QUEUED = "queued"
    SCANNING = "scanning"
    SCANNED = "scanned"
    RESCAN_SCHEDULED = "rescan_scheduled"


class FindingStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Confidence(str, Enum):
    CONFIRMED = "confirmed"        # 90%+
    LIKELY = "likely"              # 70-90%
    POSSIBLE = "possible"          # 50-70%
    FALSE_POSITIVE = "false_positive"  # <50%


@dataclass
class BountyProgram:
    """A bug bounty program being monitored."""

    platform: str                  # hackerone, bugcrowd
    program_id: str
    name: str
    url: str = ""
    scope_domains: list[str] = field(default_factory=list)
    bounty_low: int = 0
    bounty_high: int = 0
    priority_score: float = 0.0
    status: str = "active"
    id: int | None = None


@dataclass
class BountyTarget:
    """An individual scan target (domain from a program's scope)."""

    program_id: int
    domain: str
    scope_type: str = "domain"
    state: TargetState = TargetState.QUEUED
    scan_count: int = 0
    findings_count: int = 0
    id: int | None = None


@dataclass
class BountyFinding:
    """A verified vulnerability finding."""

    target_id: int
    vuln_type: str
    severity: str
    cvss: float
    confidence: float              # 0.0-1.0
    title: str
    description: str = ""
    steps_to_reproduce: str = ""
    poc: str = ""
    impact: str = ""
    suggested_fix: str = ""
    estimated_bounty_low: int | None = None
    estimated_bounty_high: int | None = None
    status: FindingStatus = FindingStatus.PENDING
    id: int | None = None

    @property
    def confidence_level(self) -> Confidence:
        if self.confidence >= 0.90:
            return Confidence.CONFIRMED
        if self.confidence >= 0.70:
            return Confidence.LIKELY
        if self.confidence >= 0.50:
            return Confidence.POSSIBLE
        return Confidence.FALSE_POSITIVE

    @property
    def should_report(self) -> bool:
        """Only report CONFIRMED or LIKELY findings."""
        return self.confidence_level in (Confidence.CONFIRMED, Confidence.LIKELY)

    @property
    def estimated_bounty_str(self) -> str:
        if self.estimated_bounty_low and self.estimated_bounty_high:
            return f"${self.estimated_bounty_low:,}-${self.estimated_bounty_high:,}"
        return "N/A"
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bounty_models.py -v`
Expected: ALL PASS (14 tests)

**Step 5: Commit**

```bash
git add src/bounty/models.py tests/unit/test_bounty_models.py
git commit -m "feat(bounty): add data models (BountyProgram, Target, Finding)"
```

---

### Task 3: Program Monitor — HackerOne Crawler

Crawl HackerOne's public directory for programs with monetary bounties.

**Files:**
- Create: `src/bounty/monitor.py`
- Test: `tests/unit/test_bounty_monitor.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_monitor.py`:

```python
"""Tests for bounty program monitor."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.bounty.monitor import ProgramMonitor
from src.bounty.store import init_bounty_tables


class TestProgramMonitor:
    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_bounty_tables(self.conn)
        self.monitor = ProgramMonitor(self.conn)

    def teardown_method(self):
        self.conn.close()

    def test_init(self):
        assert self.monitor is not None

    def test_priority_score_new_program(self):
        """New programs (< 7 days old) get highest priority."""
        from datetime import datetime, timedelta, timezone
        launch = datetime.now(timezone.utc) - timedelta(days=3)
        score = self.monitor.calculate_priority(
            bounty_high=5000, launch_date=launch.isoformat(), report_count=0,
        )
        assert score > 0.8  # New + high bounty = very high priority

    def test_priority_score_old_program(self):
        from datetime import datetime, timedelta, timezone
        launch = datetime.now(timezone.utc) - timedelta(days=60)
        score = self.monitor.calculate_priority(
            bounty_high=500, launch_date=launch.isoformat(), report_count=100,
        )
        assert score < 0.5  # Old + low bounty + many reports = low priority

    def test_save_program(self):
        from src.bounty.models import BountyProgram
        prog = BountyProgram(
            platform="hackerone", program_id="test_1", name="Test Corp",
            url="https://hackerone.com/test_corp",
            scope_domains=["*.test.com"],
            bounty_low=100, bounty_high=5000,
            priority_score=0.85,
        )
        self.monitor.save_program(prog)
        row = self.conn.execute(
            "SELECT * FROM bounty_programs WHERE program_id='test_1'"
        ).fetchone()
        assert row["name"] == "Test Corp"
        assert json.loads(row["scope_domains"]) == ["*.test.com"]

    def test_save_program_upsert(self):
        """Saving same program twice updates instead of duplicating."""
        from src.bounty.models import BountyProgram
        prog = BountyProgram(
            platform="hackerone", program_id="dup", name="V1",
            bounty_high=1000, priority_score=0.5,
        )
        self.monitor.save_program(prog)
        prog.name = "V2"
        prog.bounty_high = 2000
        self.monitor.save_program(prog)
        rows = self.conn.execute(
            "SELECT * FROM bounty_programs WHERE program_id='dup'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "V2"
        assert rows[0]["bounty_high"] == 2000

    def test_get_active_programs(self):
        from src.bounty.models import BountyProgram
        self.monitor.save_program(BountyProgram(
            platform="hackerone", program_id="a", name="A", priority_score=0.9,
        ))
        self.monitor.save_program(BountyProgram(
            platform="hackerone", program_id="b", name="B", priority_score=0.5,
        ))
        programs = self.monitor.get_active_programs()
        assert len(programs) == 2
        assert programs[0].priority_score >= programs[1].priority_score

    @pytest.mark.asyncio
    async def test_fetch_hackerone_programs_mock(self):
        """Test HackerOne fetch with mocked HTTP response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "prog_1",
                    "attributes": {
                        "name": "Mock Corp",
                        "handle": "mock_corp",
                        "offers_bounties": True,
                        "state": "public_mode",
                        "started_accepting_at": "2026-03-01T00:00:00Z",
                    },
                    "relationships": {
                        "structured_scopes": {
                            "data": [
                                {"attributes": {"asset_identifier": "*.mock.com", "asset_type": "URL", "eligible_for_bounty": True}}
                            ]
                        }
                    },
                }
            ],
            "links": {},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("src.bounty.monitor.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            programs = await self.monitor.fetch_hackerone_programs()
            assert len(programs) >= 1
            assert programs[0].platform == "hackerone"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_monitor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.bounty.monitor'`

**Step 3: Write minimal implementation**

Create `src/bounty/monitor.py`:

```python
"""Program Monitor — Crawl bug bounty platforms for profitable targets.

Currently supports HackerOne public program directory.
Phase D will add Bugcrowd and Intigriti.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

import httpx

from src.bounty.models import BountyProgram
from src.utils.logging import get_logger

log = get_logger("bounty.monitor")

_HACKERONE_API = "https://api.hackerone.com/v1/hackers/programs"
_TIMEOUT = httpx.Timeout(30.0)
_USER_AGENT = "JARVIS/2.0 BountyBot"


class ProgramMonitor:
    """Monitors bug bounty platforms for new programs."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def calculate_priority(
        self,
        bounty_high: int = 0,
        launch_date: str = "",
        report_count: int = 0,
    ) -> float:
        """Calculate priority score (0.0-1.0) for a program.

        Factors:
        - Newness (< 7 days = +0.4 bonus)
        - Bounty amount (high = better)
        - Report count (low = less competition)
        """
        score = 0.0

        # Newness bonus: newer programs have less competition
        if launch_date:
            try:
                launch = datetime.fromisoformat(launch_date.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - launch).days
                if age_days < 7:
                    score += 0.4
                elif age_days < 30:
                    score += 0.2
                elif age_days < 90:
                    score += 0.1
            except (ValueError, TypeError):
                pass

        # Bounty amount factor (0-0.4)
        if bounty_high >= 10000:
            score += 0.4
        elif bounty_high >= 5000:
            score += 0.3
        elif bounty_high >= 1000:
            score += 0.2
        elif bounty_high >= 100:
            score += 0.1

        # Competition factor: fewer reports = better (0-0.2)
        if report_count <= 10:
            score += 0.2
        elif report_count <= 50:
            score += 0.1

        return min(score, 1.0)

    def save_program(self, program: BountyProgram) -> None:
        """Save or update a bounty program in the database."""
        scope_json = json.dumps(program.scope_domains) if program.scope_domains else "[]"
        self._conn.execute(
            """INSERT INTO bounty_programs
               (platform, program_id, name, url, scope_domains, bounty_low, bounty_high, priority_score, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(platform, program_id) DO UPDATE SET
                 name=excluded.name, url=excluded.url, scope_domains=excluded.scope_domains,
                 bounty_low=excluded.bounty_low, bounty_high=excluded.bounty_high,
                 priority_score=excluded.priority_score, last_checked=CURRENT_TIMESTAMP
            """,
            (program.platform, program.program_id, program.name, program.url,
             scope_json, program.bounty_low, program.bounty_high,
             program.priority_score, program.status),
        )
        self._conn.commit()

    def get_active_programs(self) -> list[BountyProgram]:
        """Get all active programs sorted by priority (highest first)."""
        rows = self._conn.execute(
            "SELECT * FROM bounty_programs WHERE status='active' ORDER BY priority_score DESC"
        ).fetchall()
        programs = []
        for row in rows:
            programs.append(BountyProgram(
                id=row["id"],
                platform=row["platform"],
                program_id=row["program_id"],
                name=row["name"],
                url=row["url"] or "",
                scope_domains=json.loads(row["scope_domains"]) if row["scope_domains"] else [],
                bounty_low=row["bounty_low"],
                bounty_high=row["bounty_high"],
                priority_score=row["priority_score"],
                status=row["status"],
            ))
        return programs

    async def fetch_hackerone_programs(self) -> list[BountyProgram]:
        """Fetch public programs from HackerOne API."""
        programs: list[BountyProgram] = []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                resp = await client.get(
                    _HACKERONE_API,
                    headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                    params={"page[size]": 100},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.error("hackerone_fetch_failed", error=str(e))
                return programs

            for item in data.get("data", []):
                attrs = item.get("attributes", {})

                # Skip non-bounty and non-public programs
                if not attrs.get("offers_bounties"):
                    continue
                if attrs.get("state") != "public_mode":
                    continue

                # Extract scope domains
                scopes: list[str] = []
                rels = item.get("relationships", {})
                scope_data = rels.get("structured_scopes", {}).get("data", [])
                for scope in scope_data:
                    s_attrs = scope.get("attributes", {})
                    if s_attrs.get("asset_type") == "URL" and s_attrs.get("eligible_for_bounty"):
                        scopes.append(s_attrs.get("asset_identifier", ""))

                if not scopes:
                    continue  # No web scope — skip

                handle = attrs.get("handle", item.get("id", ""))
                launch_date = attrs.get("started_accepting_at", "")

                priority = self.calculate_priority(
                    bounty_high=0,  # HackerOne doesn't expose bounty table in public API
                    launch_date=launch_date,
                    report_count=0,
                )

                programs.append(BountyProgram(
                    platform="hackerone",
                    program_id=handle,
                    name=attrs.get("name", handle),
                    url=f"https://hackerone.com/{handle}",
                    scope_domains=scopes,
                    priority_score=priority,
                ))

        log.info("hackerone_programs_fetched", count=len(programs))
        return programs

    async def refresh(self) -> int:
        """Fetch programs from all platforms and save to DB. Returns count."""
        programs = await self.fetch_hackerone_programs()
        for prog in programs:
            self.save_program(prog)
        log.info("programs_refreshed", total=len(programs))
        return len(programs)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bounty_monitor.py -v`
Expected: ALL PASS (8 tests)

**Step 5: Commit**

```bash
git add src/bounty/monitor.py tests/unit/test_bounty_monitor.py
git commit -m "feat(bounty): add program monitor with HackerOne crawler"
```

---

### Task 4: Target Queue — Priority Queue with Rate Limiting

Feed scan targets from programs, enforce rate limits and concurrency.

**Files:**
- Create: `src/bounty/queue.py`
- Test: `tests/unit/test_bounty_queue.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_queue.py`:

```python
"""Tests for bounty target queue."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.bounty.queue import TargetQueue
from src.bounty.models import BountyProgram, TargetState
from src.bounty.store import init_bounty_tables


class TestTargetQueue:
    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_bounty_tables(self.conn)
        self.queue = TargetQueue(self.conn)
        # Insert a test program
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name, scope_domains) "
            "VALUES ('hackerone', 'test', 'Test', ?)",
            (json.dumps(["*.test.com", "api.test.com"]),),
        )
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()

    def test_enqueue_targets_from_program(self):
        """Extract domains from program scope and enqueue them."""
        count = self.queue.enqueue_from_program(program_id=1)
        assert count == 2
        rows = self.conn.execute("SELECT * FROM bounty_targets WHERE program_id=1").fetchall()
        assert len(rows) == 2
        domains = {r["domain"] for r in rows}
        assert "*.test.com" in domains
        assert "api.test.com" in domains

    def test_enqueue_idempotent(self):
        """Enqueueing same program twice doesn't duplicate targets."""
        self.queue.enqueue_from_program(program_id=1)
        self.queue.enqueue_from_program(program_id=1)
        rows = self.conn.execute("SELECT * FROM bounty_targets WHERE program_id=1").fetchall()
        assert len(rows) == 2

    def test_get_next_targets(self):
        self.queue.enqueue_from_program(program_id=1)
        targets = self.queue.get_next(limit=5)
        assert len(targets) == 2
        assert all(t.state == TargetState.QUEUED for t in targets)

    def test_get_next_respects_limit(self):
        self.queue.enqueue_from_program(program_id=1)
        targets = self.queue.get_next(limit=1)
        assert len(targets) == 1

    def test_mark_scanning(self):
        self.queue.enqueue_from_program(program_id=1)
        targets = self.queue.get_next(limit=1)
        self.queue.mark_scanning(targets[0].id)
        row = self.conn.execute(
            "SELECT state FROM bounty_targets WHERE id=?", (targets[0].id,)
        ).fetchone()
        assert row["state"] == "scanning"

    def test_mark_scanned(self):
        self.queue.enqueue_from_program(program_id=1)
        targets = self.queue.get_next(limit=1)
        self.queue.mark_scanning(targets[0].id)
        self.queue.mark_scanned(targets[0].id, findings_count=3)
        row = self.conn.execute(
            "SELECT * FROM bounty_targets WHERE id=?", (targets[0].id,)
        ).fetchone()
        assert row["state"] == "scanned"
        assert row["scan_count"] == 1
        assert row["findings_count"] == 3

    def test_get_next_skips_scanning(self):
        """Targets currently being scanned are not returned."""
        self.queue.enqueue_from_program(program_id=1)
        targets = self.queue.get_next(limit=1)
        self.queue.mark_scanning(targets[0].id)
        remaining = self.queue.get_next(limit=5)
        assert len(remaining) == 1  # Only the other target

    def test_schedule_rescan(self):
        self.queue.enqueue_from_program(program_id=1)
        targets = self.queue.get_next(limit=1)
        self.queue.mark_scanning(targets[0].id)
        self.queue.mark_scanned(targets[0].id)
        self.queue.schedule_rescan(targets[0].id, hours=24)
        row = self.conn.execute(
            "SELECT state, next_scan FROM bounty_targets WHERE id=?", (targets[0].id,)
        ).fetchone()
        assert row["state"] == "rescan_scheduled"
        assert row["next_scan"] is not None

    def test_stats(self):
        self.queue.enqueue_from_program(program_id=1)
        stats = self.queue.stats()
        assert stats["queued"] == 2
        assert stats["scanning"] == 0
        assert stats["total"] == 2
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_queue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.bounty.queue'`

**Step 3: Write minimal implementation**

Create `src/bounty/queue.py`:

```python
"""Target Queue — Priority queue with rate limiting for scan targets.

Manages the lifecycle of scan targets:
  queued → scanning → scanned → rescan_scheduled → queued (cycle)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from src.bounty.models import BountyTarget, TargetState
from src.utils.logging import get_logger

log = get_logger("bounty.queue")


class TargetQueue:
    """Priority queue for bug bounty scan targets."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def enqueue_from_program(self, program_id: int) -> int:
        """Extract scope domains from a program and add them as targets.

        Returns the number of new targets added.
        """
        row = self._conn.execute(
            "SELECT scope_domains FROM bounty_programs WHERE id=?", (program_id,)
        ).fetchone()
        if not row or not row["scope_domains"]:
            return 0

        domains = json.loads(row["scope_domains"])
        added = 0
        for domain in domains:
            domain = domain.strip()
            if not domain:
                continue
            try:
                self._conn.execute(
                    "INSERT INTO bounty_targets (program_id, domain, scope_type) VALUES (?, ?, ?)",
                    (program_id, domain, "wildcard" if domain.startswith("*") else "domain"),
                )
                added += 1
            except sqlite3.IntegrityError:
                pass  # Already exists — skip
        self._conn.commit()
        log.info("targets_enqueued", program_id=program_id, added=added)
        return added

    def get_next(self, limit: int = 3) -> list[BountyTarget]:
        """Get the next targets to scan (queued state only)."""
        rows = self._conn.execute(
            "SELECT t.*, p.priority_score FROM bounty_targets t "
            "JOIN bounty_programs p ON t.program_id = p.id "
            "WHERE t.state = 'queued' "
            "ORDER BY p.priority_score DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            BountyTarget(
                id=r["id"],
                program_id=r["program_id"],
                domain=r["domain"],
                scope_type=r["scope_type"],
                state=TargetState(r["state"]),
                scan_count=r["scan_count"],
                findings_count=r["findings_count"],
            )
            for r in rows
        ]

    def mark_scanning(self, target_id: int) -> None:
        """Mark a target as currently being scanned."""
        self._conn.execute(
            "UPDATE bounty_targets SET state='scanning' WHERE id=?",
            (target_id,),
        )
        self._conn.commit()

    def mark_scanned(self, target_id: int, findings_count: int = 0) -> None:
        """Mark a target as scan complete."""
        self._conn.execute(
            "UPDATE bounty_targets SET state='scanned', scan_count=scan_count+1, "
            "findings_count=?, last_scanned=CURRENT_TIMESTAMP WHERE id=?",
            (findings_count, target_id),
        )
        self._conn.commit()

    def schedule_rescan(self, target_id: int, hours: int = 24) -> None:
        """Schedule a target for rescanning after a delay."""
        next_scan = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        self._conn.execute(
            "UPDATE bounty_targets SET state='rescan_scheduled', next_scan=? WHERE id=?",
            (next_scan, target_id),
        )
        self._conn.commit()

    def promote_due_rescans(self) -> int:
        """Move rescan_scheduled targets back to queued if their next_scan is past."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "UPDATE bounty_targets SET state='queued' "
            "WHERE state='rescan_scheduled' AND next_scan <= ?",
            (now,),
        )
        self._conn.commit()
        return cur.rowcount

    def stats(self) -> dict[str, int]:
        """Get queue statistics."""
        rows = self._conn.execute(
            "SELECT state, COUNT(*) as cnt FROM bounty_targets GROUP BY state"
        ).fetchall()
        result = {s.value: 0 for s in TargetState}
        total = 0
        for row in rows:
            result[row["state"]] = row["cnt"]
            total += row["cnt"]
        result["total"] = total
        return result
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bounty_queue.py -v`
Expected: ALL PASS (10 tests)

**Step 5: Commit**

```bash
git add src/bounty/queue.py tests/unit/test_bounty_queue.py
git commit -m "feat(bounty): add target queue with rate limiting and state management"
```

---

### Task 5: Recon Engine — Orchestrate Existing Recon Tools

Run passive + active recon against a target using existing JARVIS tools.

**Files:**
- Create: `src/bounty/recon.py`
- Test: `tests/unit/test_bounty_recon.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_recon.py`:

```python
"""Tests for bounty recon engine."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.bounty.recon import ReconEngine, ReconResult
from src.tools.base import ToolRegistry, ToolResult


class TestReconEngine:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.engine = ReconEngine(self.registry)

    def test_init(self):
        assert self.engine is not None

    @pytest.mark.asyncio
    async def test_run_passive_recon(self):
        """Test passive recon orchestration with mocked tools."""
        # Mock the tool registry to return fake results
        async def mock_execute(name, **kwargs):
            return ToolResult(
                success=True,
                output=f"Mock output for {name}",
                data={"mock": True},
            )

        self.registry.execute = AsyncMock(side_effect=mock_execute)

        result = await self.engine.run_passive("test.com")
        assert isinstance(result, ReconResult)
        assert result.domain == "test.com"
        assert len(result.tool_results) > 0

    @pytest.mark.asyncio
    async def test_run_active_recon(self):
        async def mock_execute(name, **kwargs):
            return ToolResult(success=True, output=f"Mock {name}", data={})

        self.registry.execute = AsyncMock(side_effect=mock_execute)
        result = await self.engine.run_active("test.com", "https://test.com")
        assert isinstance(result, ReconResult)
        assert result.domain == "test.com"

    @pytest.mark.asyncio
    async def test_run_full_recon(self):
        async def mock_execute(name, **kwargs):
            if name == "subdomain_enum":
                return ToolResult(success=True, output="sub1.test.com\nsub2.test.com",
                                  data={"subdomains": ["sub1.test.com", "sub2.test.com"]})
            return ToolResult(success=True, output=f"Mock {name}", data={})

        self.registry.execute = AsyncMock(side_effect=mock_execute)
        result = await self.engine.run_full("test.com", "https://test.com")
        assert isinstance(result, ReconResult)
        assert len(result.subdomains) == 2

    @pytest.mark.asyncio
    async def test_tool_failure_handled(self):
        """A single tool failure doesn't crash the entire recon."""
        call_count = 0

        async def mock_execute(name, **kwargs):
            nonlocal call_count
            call_count += 1
            if name == "subdomain_enum":
                return ToolResult(success=False, output="", error="Timeout")
            return ToolResult(success=True, output=f"Mock {name}", data={})

        self.registry.execute = AsyncMock(side_effect=mock_execute)
        result = await self.engine.run_full("test.com", "https://test.com")
        assert isinstance(result, ReconResult)
        assert call_count > 1  # Other tools still ran


class TestReconResult:
    def test_creation(self):
        r = ReconResult(domain="test.com")
        assert r.domain == "test.com"
        assert r.subdomains == []
        assert r.tool_results == {}

    def test_summary(self):
        r = ReconResult(domain="test.com", subdomains=["a.test.com", "b.test.com"])
        summary = r.summary()
        assert "test.com" in summary
        assert "2" in summary  # 2 subdomains
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_recon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.bounty.recon'`

**Step 3: Write minimal implementation**

Create `src/bounty/recon.py`:

```python
"""Recon Engine — Orchestrate existing JARVIS recon tools against bounty targets.

Phase 1 (passive): subdomain_enum, google_dork, wayback_lookup, github_leaks, tech_detect, whois
Phase 2 (active): http_headers, ssl_check, cve_lookup
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.tools.base import ToolRegistry, ToolResult
from src.utils.logging import get_logger

log = get_logger("bounty.recon")


@dataclass
class ReconResult:
    """Aggregated reconnaissance result for a target."""

    domain: str = ""
    subdomains: list[str] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)
    interesting_endpoints: list[str] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)
    cves: list[dict[str, Any]] = field(default_factory=list)
    tool_results: dict[str, ToolResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary of recon findings."""
        lines = [f"Recon Summary: {self.domain}"]
        lines.append(f"  Subdomains: {len(self.subdomains)}")
        lines.append(f"  Tech stack: {', '.join(self.tech_stack) if self.tech_stack else 'unknown'}")
        lines.append(f"  Endpoints: {len(self.interesting_endpoints)}")
        lines.append(f"  Leaks: {len(self.leaks)}")
        lines.append(f"  CVEs: {len(self.cves)}")
        lines.append(f"  Tools run: {len(self.tool_results)}")
        lines.append(f"  Errors: {len(self.errors)}")
        return "\n".join(lines)


class ReconEngine:
    """Orchestrates recon tools for bug bounty scanning."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._registry = tool_registry

    async def _run_tool(self, name: str, **kwargs: Any) -> ToolResult:
        """Run a single tool, catching failures gracefully."""
        try:
            return await self._registry.execute(name, **kwargs)
        except Exception as e:
            log.warning("recon_tool_failed", tool=name, error=str(e))
            return ToolResult(success=False, output="", error=str(e))

    async def run_passive(self, domain: str) -> ReconResult:
        """Phase 1: Passive recon — no direct contact with target."""
        result = ReconResult(domain=domain)

        tasks = {
            "subdomain_enum": self._run_tool("subdomain_enum", domain=domain),
            "google_dork": self._run_tool("google_dork", query=f"site:{domain}"),
            "wayback_lookup": self._run_tool("wayback_lookup", domain=domain),
            "github_leaks": self._run_tool("github_leaks", query=domain),
            "tech_detect": self._run_tool("tech_detect", url=f"https://{domain}"),
            "whois": self._run_tool("whois", domain=domain),
        }

        names = list(tasks.keys())
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for name, res in zip(names, results):
            if isinstance(res, Exception):
                result.errors.append(f"{name}: {res}")
                continue
            result.tool_results[name] = res
            if not res.success:
                result.errors.append(f"{name}: {res.error}")
                continue

            # Extract structured data
            data = res.data or {}
            if name == "subdomain_enum":
                result.subdomains = data.get("subdomains", [])
            elif name == "tech_detect":
                result.tech_stack = data.get("technologies", [])
            elif name == "github_leaks":
                result.leaks = data.get("results", [])

        log.info("passive_recon_done", domain=domain, tools=len(result.tool_results))
        return result

    async def run_active(self, domain: str, url: str) -> ReconResult:
        """Phase 2: Active recon — light direct contact."""
        result = ReconResult(domain=domain)

        tasks = {
            "http_headers": self._run_tool("http_headers", url=url),
            "ssl_check": self._run_tool("ssl_check", domain=domain),
            "cve_lookup": self._run_tool("cve_lookup", query=domain),
        }

        names = list(tasks.keys())
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for name, res in zip(names, results):
            if isinstance(res, Exception):
                result.errors.append(f"{name}: {res}")
                continue
            result.tool_results[name] = res
            if not res.success:
                result.errors.append(f"{name}: {res.error}")
                continue

            data = res.data or {}
            if name == "cve_lookup":
                result.cves = data.get("cves", [])

        log.info("active_recon_done", domain=domain, tools=len(result.tool_results))
        return result

    async def run_full(self, domain: str, url: str) -> ReconResult:
        """Run both passive and active recon, merge results."""
        passive = await self.run_passive(domain)
        active = await self.run_active(domain, url)

        # Merge active into passive
        passive.tool_results.update(active.tool_results)
        passive.errors.extend(active.errors)
        passive.cves = active.cves
        return passive
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bounty_recon.py -v`
Expected: ALL PASS (7 tests)

**Step 5: Commit**

```bash
git add src/bounty/recon.py tests/unit/test_bounty_recon.py
git commit -m "feat(bounty): add recon engine orchestrating existing security tools"
```

---

### Task 6: Vuln Scanner — Orchestrate Existing Attack Tools

Run vulnerability scans against recon-discovered endpoints.

**Files:**
- Create: `src/bounty/scanner.py`
- Test: `tests/unit/test_bounty_scanner.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_scanner.py`:

```python
"""Tests for bounty vulnerability scanner."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.bounty.scanner import VulnScanner, ScanResult
from src.bounty.recon import ReconResult
from src.tools.base import ToolRegistry, ToolResult


class TestVulnScanner:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.scanner = VulnScanner(self.registry)

    def test_init(self):
        assert self.scanner is not None

    @pytest.mark.asyncio
    async def test_scan_target(self):
        async def mock_execute(name, **kwargs):
            if name == "sqli_test":
                return ToolResult(
                    success=True,
                    output="Possible SQL injection found",
                    data={"vulnerable": True, "parameter": "id", "payload": "1' OR '1'='1"},
                )
            if name == "waf_detect":
                return ToolResult(success=True, output="No WAF detected", data={"waf": None})
            return ToolResult(success=True, output=f"Clean for {name}", data={"vulnerable": False})

        self.registry.execute = AsyncMock(side_effect=mock_execute)

        recon = ReconResult(domain="test.com", subdomains=["test.com"])
        result = await self.scanner.scan("https://test.com", recon)
        assert isinstance(result, ScanResult)
        assert len(result.findings) > 0  # Should find the SQLi

    @pytest.mark.asyncio
    async def test_scan_no_vulns(self):
        async def mock_execute(name, **kwargs):
            return ToolResult(success=True, output="Clean", data={"vulnerable": False})

        self.registry.execute = AsyncMock(side_effect=mock_execute)

        recon = ReconResult(domain="safe.com")
        result = await self.scanner.scan("https://safe.com", recon)
        assert isinstance(result, ScanResult)
        assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_scan_tool_failure_handled(self):
        async def mock_execute(name, **kwargs):
            if name == "xss_scan":
                raise TimeoutError("Tool timed out")
            return ToolResult(success=True, output="Clean", data={"vulnerable": False})

        self.registry.execute = AsyncMock(side_effect=mock_execute)

        recon = ReconResult(domain="test.com")
        result = await self.scanner.scan("https://test.com", recon)
        assert isinstance(result, ScanResult)
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_waf_detection_affects_scan(self):
        """When WAF is detected, scanner notes it."""
        async def mock_execute(name, **kwargs):
            if name == "waf_detect":
                return ToolResult(success=True, output="Cloudflare WAF",
                                  data={"waf": "Cloudflare"})
            return ToolResult(success=True, output="Clean", data={"vulnerable": False})

        self.registry.execute = AsyncMock(side_effect=mock_execute)

        recon = ReconResult(domain="protected.com")
        result = await self.scanner.scan("https://protected.com", recon)
        assert result.waf_detected == "Cloudflare"


class TestScanResult:
    def test_creation(self):
        r = ScanResult(target="https://test.com")
        assert r.target == "https://test.com"
        assert r.findings == []
        assert r.waf_detected is None

    def test_summary(self):
        r = ScanResult(target="https://test.com")
        s = r.summary()
        assert "test.com" in s
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.bounty.scanner'`

**Step 3: Write minimal implementation**

Create `src/bounty/scanner.py`:

```python
"""Vuln Scanner — Run existing attack tools against bounty targets.

Uses: sqli_test, xss_scan, cors_check, lfi_test, header_audit, dir_bruteforce, waf_detect.
Findings are raw — the Verifier module will validate them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.bounty.models import BountyFinding
from src.bounty.recon import ReconResult
from src.tools.base import ToolRegistry, ToolResult
from src.utils.logging import get_logger

log = get_logger("bounty.scanner")

# Ordered by severity/value: high-value vulns first
_SCAN_TOOLS = [
    ("sqli_test", "sqli", "CRITICAL", 9.8),
    ("xss_scan", "xss", "HIGH", 7.5),
    ("lfi_test", "lfi", "HIGH", 8.0),
    ("cors_check", "cors", "MEDIUM", 5.3),
    ("header_audit", "headers", "LOW", 3.0),
    ("dir_bruteforce", "dir_enum", "INFO", 0.0),
]


@dataclass
class ScanResult:
    """Result of vulnerability scanning on a target."""

    target: str = ""
    findings: list[BountyFinding] = field(default_factory=list)
    tool_results: dict[str, ToolResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    waf_detected: str | None = None

    def summary(self) -> str:
        lines = [f"Scan Summary: {self.target}"]
        lines.append(f"  Findings: {len(self.findings)}")
        lines.append(f"  WAF: {self.waf_detected or 'none'}")
        lines.append(f"  Tools run: {len(self.tool_results)}")
        lines.append(f"  Errors: {len(self.errors)}")
        return "\n".join(lines)


class VulnScanner:
    """Orchestrates vulnerability scanning tools."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._registry = tool_registry

    async def _run_tool(self, name: str, **kwargs: Any) -> ToolResult:
        try:
            return await self._registry.execute(name, **kwargs)
        except Exception as e:
            log.warning("scan_tool_failed", tool=name, error=str(e))
            return ToolResult(success=False, output="", error=str(e))

    async def scan(self, url: str, recon: ReconResult) -> ScanResult:
        """Run all vuln scan tools against a target URL."""
        result = ScanResult(target=url)

        # Step 1: WAF detection first — informs scan strategy
        waf_result = await self._run_tool("waf_detect", url=url)
        result.tool_results["waf_detect"] = waf_result
        if waf_result.success:
            waf_data = waf_result.data or {}
            result.waf_detected = waf_data.get("waf")

        # Step 2: Run all scan tools in parallel
        tasks = {}
        for tool_name, vuln_type, severity, cvss in _SCAN_TOOLS:
            tasks[tool_name] = self._run_tool(tool_name, url=url)

        names = list(tasks.keys())
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for tool_name, res in zip(names, results):
            if isinstance(res, Exception):
                result.errors.append(f"{tool_name}: {res}")
                continue

            result.tool_results[tool_name] = res

            if not res.success:
                result.errors.append(f"{tool_name}: {res.error}")
                continue

            # Check if vulnerability was found
            data = res.data or {}
            is_vulnerable = data.get("vulnerable", False)

            if is_vulnerable:
                # Find the vuln info from _SCAN_TOOLS
                vuln_info = next(
                    (s for s in _SCAN_TOOLS if s[0] == tool_name), None
                )
                if vuln_info:
                    _, vuln_type, severity, cvss = vuln_info
                    finding = BountyFinding(
                        target_id=0,  # Will be set by pipeline
                        vuln_type=vuln_type,
                        severity=severity,
                        cvss=cvss,
                        confidence=0.6,  # Initial — verifier will adjust
                        title=f"{vuln_type.upper()} in {url}",
                        description=res.output,
                        poc=data.get("payload", ""),
                    )
                    result.findings.append(finding)

        log.info("scan_done", target=url, findings=len(result.findings),
                 tools=len(result.tool_results))
        return result
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bounty_scanner.py -v`
Expected: ALL PASS (7 tests)

**Step 5: Commit**

```bash
git add src/bounty/scanner.py tests/unit/test_bounty_scanner.py
git commit -m "feat(bounty): add vulnerability scanner orchestrating attack tools"
```

---

### Task 7: Verifier — Reproduce + CVSS + Confidence Scoring

Verify raw findings by re-running exploits and calculating confidence.

**Files:**
- Create: `src/bounty/verifier.py`
- Test: `tests/unit/test_bounty_verifier.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_verifier.py`:

```python
"""Tests for bounty finding verifier."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.bounty.verifier import Verifier
from src.bounty.models import BountyFinding, Confidence
from src.tools.base import ToolRegistry, ToolResult


class TestVerifier:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.verifier = Verifier(self.registry)

    def test_init(self):
        assert self.verifier is not None

    @pytest.mark.asyncio
    async def test_verify_confirmed(self):
        """Finding confirmed when tool reports vulnerable on re-test."""
        async def mock_execute(name, **kwargs):
            return ToolResult(
                success=True, output="SQL injection confirmed",
                data={"vulnerable": True, "payload": "1' OR '1'='1"},
            )

        self.registry.execute = AsyncMock(side_effect=mock_execute)

        finding = BountyFinding(
            target_id=1, vuln_type="sqli", severity="CRITICAL",
            cvss=9.8, confidence=0.6, title="SQLi in /api/users",
            poc="1' OR '1'='1",
        )
        verified = await self.verifier.verify(finding, "https://test.com/api/users")
        assert verified.confidence >= 0.9
        assert verified.confidence_level == Confidence.CONFIRMED

    @pytest.mark.asyncio
    async def test_verify_not_reproduced(self):
        """Confidence drops when re-test doesn't confirm the vuln."""
        async def mock_execute(name, **kwargs):
            return ToolResult(
                success=True, output="No vulnerability found",
                data={"vulnerable": False},
            )

        self.registry.execute = AsyncMock(side_effect=mock_execute)

        finding = BountyFinding(
            target_id=1, vuln_type="xss", severity="HIGH",
            cvss=7.5, confidence=0.6, title="XSS in search",
        )
        verified = await self.verifier.verify(finding, "https://test.com/search")
        assert verified.confidence < 0.5

    @pytest.mark.asyncio
    async def test_verify_tool_error_keeps_original(self):
        """If re-test tool errors, keep original confidence."""
        async def mock_execute(name, **kwargs):
            return ToolResult(success=False, output="", error="Timeout")

        self.registry.execute = AsyncMock(side_effect=mock_execute)

        finding = BountyFinding(
            target_id=1, vuln_type="cors", severity="MEDIUM",
            cvss=5.3, confidence=0.6, title="CORS misconfig",
        )
        verified = await self.verifier.verify(finding, "https://test.com")
        assert verified.confidence == 0.6  # Unchanged

    def test_estimate_bounty(self):
        finding = BountyFinding(
            target_id=1, vuln_type="sqli", severity="CRITICAL",
            cvss=9.8, confidence=0.95, title="SQLi",
        )
        self.verifier.estimate_bounty(finding, bounty_low=100, bounty_high=10000)
        assert finding.estimated_bounty_low is not None
        assert finding.estimated_bounty_high is not None
        assert finding.estimated_bounty_high >= finding.estimated_bounty_low

    def test_estimate_bounty_low_severity(self):
        finding = BountyFinding(
            target_id=1, vuln_type="headers", severity="LOW",
            cvss=3.0, confidence=0.8, title="Missing headers",
        )
        self.verifier.estimate_bounty(finding, bounty_low=100, bounty_high=10000)
        assert finding.estimated_bounty_low < 500

    @pytest.mark.asyncio
    async def test_dedup_findings(self):
        """Duplicate findings across endpoints are merged."""
        findings = [
            BountyFinding(target_id=1, vuln_type="xss", severity="HIGH",
                          cvss=7.5, confidence=0.9, title="XSS in /search"),
            BountyFinding(target_id=1, vuln_type="xss", severity="HIGH",
                          cvss=7.5, confidence=0.85, title="XSS in /query"),
            BountyFinding(target_id=1, vuln_type="sqli", severity="CRITICAL",
                          cvss=9.8, confidence=0.95, title="SQLi in /api"),
        ]
        deduped = self.verifier.dedup_findings(findings)
        assert len(deduped) == 2  # Two XSS merged into one
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_verifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.bounty.verifier'`

**Step 3: Write minimal implementation**

Create `src/bounty/verifier.py`:

```python
"""Verifier — Re-test findings, calculate CVSS/confidence, estimate bounty.

Confidence levels:
  CONFIRMED (90%+) → auto-report
  LIKELY (70-90%) → report, flag for Bi review
  POSSIBLE (50-70%) → log only
  FALSE_POSITIVE (<50%) → discard
"""

from __future__ import annotations

from src.bounty.models import BountyFinding
from src.tools.base import ToolRegistry, ToolResult
from src.utils.logging import get_logger

log = get_logger("bounty.verifier")

# Maps vuln_type to the tool used for re-testing
_VULN_TO_TOOL = {
    "sqli": "sqli_test",
    "xss": "xss_scan",
    "lfi": "lfi_test",
    "cors": "cors_check",
    "headers": "header_audit",
}

# Severity → bounty multiplier (proportion of program's bounty range)
_SEVERITY_MULTIPLIER = {
    "CRITICAL": (0.7, 1.0),
    "HIGH": (0.4, 0.7),
    "MEDIUM": (0.15, 0.4),
    "LOW": (0.05, 0.15),
    "INFO": (0.0, 0.05),
}


class Verifier:
    """Verify findings by re-testing and scoring confidence."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._registry = tool_registry

    async def verify(self, finding: BountyFinding, url: str) -> BountyFinding:
        """Re-run the exploit tool to verify the finding. Adjusts confidence."""
        tool_name = _VULN_TO_TOOL.get(finding.vuln_type)
        if not tool_name:
            return finding  # No re-test tool available

        try:
            result = await self._registry.execute(tool_name, url=url)
        except Exception as e:
            log.warning("verify_tool_failed", tool=tool_name, error=str(e))
            return finding  # Keep original confidence on error

        if not result.success:
            return finding  # Keep original confidence

        data = result.data or {}
        if data.get("vulnerable"):
            # Confirmed — boost confidence
            finding.confidence = max(finding.confidence, 0.90)
            finding.poc = finding.poc or data.get("payload", "")
            log.info("finding_confirmed", vuln=finding.vuln_type, url=url)
        else:
            # Not reproduced — lower confidence
            finding.confidence = min(finding.confidence, 0.40)
            log.info("finding_not_reproduced", vuln=finding.vuln_type, url=url)

        return finding

    def estimate_bounty(
        self,
        finding: BountyFinding,
        bounty_low: int = 100,
        bounty_high: int = 5000,
    ) -> None:
        """Estimate bounty payout based on severity and program's bounty range."""
        multiplier = _SEVERITY_MULTIPLIER.get(finding.severity, (0.0, 0.05))
        bounty_range = bounty_high - bounty_low
        finding.estimated_bounty_low = int(bounty_low + bounty_range * multiplier[0])
        finding.estimated_bounty_high = int(bounty_low + bounty_range * multiplier[1])

    def dedup_findings(self, findings: list[BountyFinding]) -> list[BountyFinding]:
        """Deduplicate findings by vuln_type — keep highest confidence per type."""
        best: dict[str, BountyFinding] = {}
        for f in findings:
            if f.vuln_type not in best or f.confidence > best[f.vuln_type].confidence:
                best[f.vuln_type] = f
        return list(best.values())
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bounty_verifier.py -v`
Expected: ALL PASS (7 tests)

**Step 5: Commit**

```bash
git add src/bounty/verifier.py tests/unit/test_bounty_verifier.py
git commit -m "feat(bounty): add verifier with re-test, confidence scoring, bounty estimation"
```

---

### Task 8: Report Generator — HackerOne-Format Reports

Generate platform-specific bug bounty reports from verified findings.

**Files:**
- Create: `src/bounty/reporter.py`
- Test: `tests/unit/test_bounty_reporter.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_reporter.py`:

```python
"""Tests for bounty report generator."""

from __future__ import annotations

import pytest

from src.bounty.reporter import BountyReportGenerator
from src.bounty.models import BountyFinding


class TestBountyReportGenerator:
    def test_generate_hackerone(self):
        finding = BountyFinding(
            target_id=1, vuln_type="sqli", severity="CRITICAL",
            cvss=9.8, confidence=0.95, title="SQL Injection in /api/users",
            description="The 'id' parameter is vulnerable to SQL injection.",
            steps_to_reproduce="1. Go to /api/users?id=1\n2. Change id to 1' OR '1'='1\n3. Observe all users returned",
            poc="GET /api/users?id=1' OR '1'='1 HTTP/1.1",
            impact="Full database access — attacker can read all user data.",
            suggested_fix="Use parameterized queries instead of string concatenation.",
            estimated_bounty_low=3000, estimated_bounty_high=10000,
        )
        report = BountyReportGenerator.generate_hackerone(finding, "test.com")
        assert "## Summary" in report
        assert "## Severity" in report
        assert "## Steps to Reproduce" in report
        assert "## Impact" in report
        assert "## Proof of Concept" in report
        assert "## Suggested Fix" in report
        assert "SQL Injection" in report
        assert "CRITICAL" in report
        assert "9.8" in report

    def test_generate_markdown(self):
        finding = BountyFinding(
            target_id=1, vuln_type="xss", severity="HIGH",
            cvss=7.5, confidence=0.85, title="Reflected XSS in search",
            description="Search parameter is reflected without encoding.",
            poc="<script>alert(1)</script>",
        )
        report = BountyReportGenerator.generate_markdown(finding, "test.com")
        assert "XSS" in report or "xss" in report
        assert "test.com" in report

    def test_generate_telegram_summary(self):
        finding = BountyFinding(
            target_id=1, vuln_type="sqli", severity="CRITICAL",
            cvss=9.8, confidence=0.95, title="SQLi in /api",
            estimated_bounty_low=3000, estimated_bounty_high=10000,
        )
        summary = BountyReportGenerator.generate_telegram_summary(
            finding, program_name="Test Corp", platform="hackerone",
        )
        assert "Test Corp" in summary
        assert "CRITICAL" in summary
        assert "$3,000-$10,000" in summary

    def test_empty_fields_handled(self):
        """Report doesn't crash with missing optional fields."""
        finding = BountyFinding(
            target_id=1, vuln_type="cors", severity="MEDIUM",
            cvss=5.3, confidence=0.75, title="CORS misconfig",
        )
        report = BountyReportGenerator.generate_hackerone(finding, "test.com")
        assert "## Summary" in report
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_reporter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.bounty.reporter'`

**Step 3: Write minimal implementation**

Create `src/bounty/reporter.py`:

```python
"""Bug Bounty Report Generator — Platform-specific report formatting.

Supports: HackerOne, generic Markdown, Telegram summary.
"""

from __future__ import annotations

from src.bounty.models import BountyFinding
from src.utils.logging import get_logger

log = get_logger("bounty.reporter")


class BountyReportGenerator:
    """Generate professional bug bounty reports."""

    @staticmethod
    def generate_hackerone(finding: BountyFinding, domain: str) -> str:
        """Generate a HackerOne-format vulnerability report."""
        lines = []

        lines.append(f"## Summary")
        lines.append(f"{finding.title} on {domain}")
        lines.append("")

        lines.append(f"## Severity")
        lines.append(f"{finding.severity} — CVSS {finding.cvss}")
        lines.append("")

        lines.append(f"## Steps to Reproduce")
        if finding.steps_to_reproduce:
            lines.append(finding.steps_to_reproduce)
        else:
            lines.append(f"1. Navigate to https://{domain}")
            lines.append(f"2. Trigger the {finding.vuln_type} vulnerability")
            lines.append(f"3. Observe the result")
        lines.append("")

        lines.append(f"## Impact")
        if finding.impact:
            lines.append(finding.impact)
        else:
            lines.append(f"An attacker could exploit this {finding.vuln_type} vulnerability to compromise the application.")
        lines.append("")

        lines.append(f"## Proof of Concept")
        if finding.poc:
            lines.append(f"```")
            lines.append(finding.poc)
            lines.append(f"```")
        else:
            lines.append("See steps to reproduce above.")
        lines.append("")

        lines.append(f"## Suggested Fix")
        if finding.suggested_fix:
            lines.append(finding.suggested_fix)
        else:
            lines.append(f"Remediate the {finding.vuln_type} vulnerability following OWASP guidelines.")
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def generate_markdown(finding: BountyFinding, domain: str) -> str:
        """Generate a generic Markdown vulnerability report."""
        lines = [
            f"# {finding.title}",
            "",
            f"**Domain:** {domain}",
            f"**Type:** {finding.vuln_type}",
            f"**Severity:** {finding.severity} (CVSS {finding.cvss})",
            f"**Confidence:** {finding.confidence:.0%}",
            "",
        ]
        if finding.description:
            lines.extend(["## Description", finding.description, ""])
        if finding.poc:
            lines.extend(["## PoC", f"```\n{finding.poc}\n```", ""])
        return "\n".join(lines)

    @staticmethod
    def generate_telegram_summary(
        finding: BountyFinding,
        program_name: str = "",
        platform: str = "",
    ) -> str:
        """Short summary for Telegram notification."""
        lines = [
            "Bug Bounty Finding!",
            "",
            f"Program: {program_name} ({platform})",
            f"Vuln: {finding.title}",
            f"Severity: {finding.severity} (CVSS {finding.cvss})",
            f"Confidence: {finding.confidence:.0%} {finding.confidence_level.value.upper()}",
            f"Est. Bounty: {finding.estimated_bounty_str}",
        ]
        return "\n".join(lines)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bounty_reporter.py -v`
Expected: ALL PASS (4 tests)

**Step 5: Commit**

```bash
git add src/bounty/reporter.py tests/unit/test_bounty_reporter.py
git commit -m "feat(bounty): add report generator with HackerOne format + Telegram summary"
```

---

### Task 9: Pipeline Orchestrator — The Core Loop

The main pipeline that ties everything together: Monitor → Queue → Recon → Scan → Verify → Report.

**Files:**
- Create: `src/bounty/pipeline.py`
- Test: `tests/unit/test_bounty_pipeline.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_pipeline.py`:

```python
"""Tests for bounty pipeline orchestrator."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bounty.pipeline import BountyPipeline
from src.bounty.models import BountyFinding, BountyProgram, BountyTarget, TargetState
from src.bounty.recon import ReconResult
from src.bounty.scanner import ScanResult
from src.bounty.store import init_bounty_tables
from src.tools.base import ToolRegistry, ToolResult


class TestBountyPipeline:
    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_bounty_tables(self.conn)
        self.registry = ToolRegistry()
        self.pipeline = BountyPipeline(self.conn, self.registry)

    def teardown_method(self):
        self.conn.close()

    def test_init(self):
        assert self.pipeline is not None
        assert self.pipeline.is_running is False

    @pytest.mark.asyncio
    async def test_scan_single_target(self):
        """Full pipeline for a single target: recon → scan → verify → save."""
        # Insert test program + target
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name, scope_domains, bounty_high) "
            "VALUES ('hackerone', 'test', 'Test', '[\"test.com\"]', 5000)"
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain, state) VALUES (1, 'test.com', 'queued')"
        )
        self.conn.commit()

        async def mock_execute(name, **kwargs):
            if name == "sqli_test":
                return ToolResult(success=True, output="SQLi found",
                                  data={"vulnerable": True, "payload": "1' OR '1'='1"})
            if name == "waf_detect":
                return ToolResult(success=True, output="No WAF", data={"waf": None})
            return ToolResult(success=True, output=f"Clean {name}", data={"vulnerable": False})

        self.registry.execute = AsyncMock(side_effect=mock_execute)

        target = BountyTarget(id=1, program_id=1, domain="test.com")
        findings = await self.pipeline.scan_target(target)
        assert isinstance(findings, list)

    @pytest.mark.asyncio
    async def test_save_findings(self):
        """Findings are saved to the database."""
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES ('hackerone', 'p1', 'P1')"
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain) VALUES (1, 'test.com')"
        )
        self.conn.commit()

        finding = BountyFinding(
            target_id=1, vuln_type="sqli", severity="CRITICAL",
            cvss=9.8, confidence=0.95, title="SQLi in /api",
            description="SQL injection found",
        )
        self.pipeline.save_finding(finding)
        row = self.conn.execute("SELECT * FROM bounty_findings WHERE id=1").fetchone()
        assert row["vuln_type"] == "sqli"
        assert row["severity"] == "CRITICAL"

    def test_get_pending_findings(self):
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES ('hackerone', 'p1', 'P1')"
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain) VALUES (1, 'test.com')"
        )
        self.conn.execute(
            "INSERT INTO bounty_findings (target_id, vuln_type, severity, cvss, confidence, title) "
            "VALUES (1, 'xss', 'HIGH', 7.5, 0.9, 'XSS')"
        )
        self.conn.commit()
        findings = self.pipeline.get_pending_findings()
        assert len(findings) == 1
        assert findings[0].vuln_type == "xss"

    def test_update_finding_status(self):
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES ('hackerone', 'p1', 'P1')"
        )
        self.conn.execute(
            "INSERT INTO bounty_targets (program_id, domain) VALUES (1, 'test.com')"
        )
        self.conn.execute(
            "INSERT INTO bounty_findings (target_id, vuln_type, severity, cvss, confidence, title) "
            "VALUES (1, 'xss', 'HIGH', 7.5, 0.9, 'XSS')"
        )
        self.conn.commit()
        self.pipeline.update_finding_status(1, "approved")
        row = self.conn.execute("SELECT status FROM bounty_findings WHERE id=1").fetchone()
        assert row["status"] == "approved"

    def test_stats(self):
        stats = self.pipeline.stats()
        assert "programs" in stats
        assert "targets" in stats
        assert "findings" in stats
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.bounty.pipeline'`

**Step 3: Write minimal implementation**

Create `src/bounty/pipeline.py`:

```python
"""Bug Bounty Pipeline Orchestrator — The core autonomous loop.

Ties together: Monitor → Queue → Recon → Scan → Verify → Report → Notify.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Callable

from src.bounty.models import BountyFinding, BountyTarget, FindingStatus, TargetState
from src.bounty.monitor import ProgramMonitor
from src.bounty.queue import TargetQueue
from src.bounty.recon import ReconEngine
from src.bounty.reporter import BountyReportGenerator
from src.bounty.scanner import VulnScanner
from src.bounty.verifier import Verifier
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("bounty.pipeline")

# Callback type for notifications
NotifyCallback = Callable[[BountyFinding, str], Any]  # (finding, report_text)


class BountyPipeline:
    """Orchestrates the full bug bounty hunting pipeline."""

    def __init__(self, conn: sqlite3.Connection, tool_registry: ToolRegistry) -> None:
        self._conn = conn
        self._registry = tool_registry
        self._monitor = ProgramMonitor(conn)
        self._queue = TargetQueue(conn)
        self._recon = ReconEngine(tool_registry)
        self._scanner = VulnScanner(tool_registry)
        self._verifier = Verifier(tool_registry)
        self._notify_callback: NotifyCallback | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def set_notify_callback(self, callback: NotifyCallback) -> None:
        self._notify_callback = callback

    async def scan_target(self, target: BountyTarget) -> list[BountyFinding]:
        """Full pipeline for a single target: recon → scan → verify."""
        domain = target.domain.lstrip("*.")
        url = f"https://{domain}"

        # 1. Recon
        recon_result = await self._recon.run_full(domain, url)

        # 2. Scan
        scan_result = await self._scanner.scan(url, recon_result)

        # 3. Verify each finding
        verified_findings: list[BountyFinding] = []
        for finding in scan_result.findings:
            finding.target_id = target.id or 0
            verified = await self._verifier.verify(finding, url)

            # Only keep findings worth reporting
            if verified.should_report:
                # Estimate bounty from program's bounty range
                row = self._conn.execute(
                    "SELECT bounty_low, bounty_high FROM bounty_programs WHERE id=?",
                    (target.program_id,),
                ).fetchone()
                if row:
                    self._verifier.estimate_bounty(
                        verified, bounty_low=row["bounty_low"], bounty_high=row["bounty_high"],
                    )

                # Dedup against existing findings for this target
                verified_findings.append(verified)

        # Dedup within this scan
        verified_findings = self._verifier.dedup_findings(verified_findings)

        log.info("target_scanned", domain=domain,
                 raw=len(scan_result.findings), verified=len(verified_findings))
        return verified_findings

    def save_finding(self, finding: BountyFinding) -> int:
        """Save a verified finding to the database. Returns the finding ID."""
        cur = self._conn.execute(
            "INSERT INTO bounty_findings "
            "(target_id, vuln_type, severity, cvss, confidence, title, description, "
            " steps_to_reproduce, poc, impact, suggested_fix, "
            " estimated_bounty_low, estimated_bounty_high, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (finding.target_id, finding.vuln_type, finding.severity,
             finding.cvss, finding.confidence, finding.title,
             finding.description, finding.steps_to_reproduce,
             finding.poc, finding.impact, finding.suggested_fix,
             finding.estimated_bounty_low, finding.estimated_bounty_high,
             finding.status.value),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def get_pending_findings(self) -> list[BountyFinding]:
        """Get all findings with status 'pending'."""
        rows = self._conn.execute(
            "SELECT * FROM bounty_findings WHERE status='pending' ORDER BY cvss DESC"
        ).fetchall()
        return [self._row_to_finding(r) for r in rows]

    def update_finding_status(self, finding_id: int, status: str) -> None:
        """Update a finding's status (approve, reject, submit)."""
        self._conn.execute(
            "UPDATE bounty_findings SET status=? WHERE id=?",
            (status, finding_id),
        )
        self._conn.commit()

    def stats(self) -> dict[str, Any]:
        """Get pipeline statistics."""
        programs = self._conn.execute(
            "SELECT COUNT(*) as c FROM bounty_programs WHERE status='active'"
        ).fetchone()["c"]
        targets = self._queue.stats()
        findings_pending = self._conn.execute(
            "SELECT COUNT(*) as c FROM bounty_findings WHERE status='pending'"
        ).fetchone()["c"]
        findings_total = self._conn.execute(
            "SELECT COUNT(*) as c FROM bounty_findings"
        ).fetchone()["c"]
        earnings = self._conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM bounty_earnings"
        ).fetchone()["total"]

        return {
            "running": self._running,
            "programs": programs,
            "targets": targets,
            "findings": {"pending": findings_pending, "total": findings_total},
            "earnings_usd": earnings,
        }

    async def run_cycle(self) -> dict[str, int]:
        """Run one full pipeline cycle: refresh programs → process queue."""
        results = {"programs_found": 0, "targets_scanned": 0, "findings_found": 0}

        # 1. Refresh programs
        try:
            results["programs_found"] = await self._monitor.refresh()
        except Exception as e:
            log.error("monitor_refresh_failed", error=str(e))

        # 2. Enqueue targets from all active programs
        for prog in self._monitor.get_active_programs():
            self._queue.enqueue_from_program(prog.id)

        # 3. Promote due rescans
        self._queue.promote_due_rescans()

        # 4. Process next batch of targets
        targets = self._queue.get_next(limit=3)
        for target in targets:
            self._queue.mark_scanning(target.id)
            try:
                findings = await self.scan_target(target)
                for finding in findings:
                    fid = self.save_finding(finding)
                    results["findings_found"] += 1

                    # Notify via callback
                    if self._notify_callback and finding.should_report:
                        report_text = BountyReportGenerator.generate_hackerone(
                            finding, target.domain,
                        )
                        try:
                            await self._notify_callback(finding, report_text)
                        except Exception as e:
                            log.warning("notify_failed", error=str(e))

                self._queue.mark_scanned(target.id, findings_count=len(findings))
                self._queue.schedule_rescan(target.id, hours=24)
                results["targets_scanned"] += 1
            except Exception as e:
                log.error("scan_target_failed", target=target.domain, error=str(e))
                self._queue.mark_scanned(target.id, findings_count=0)

        log.info("pipeline_cycle_done", **results)
        return results

    async def start(self, interval_hours: int = 6) -> None:
        """Start the pipeline as a background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(interval_hours))
        log.info("pipeline_started", interval_hours=interval_hours)

    async def stop(self) -> None:
        """Stop the pipeline."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("pipeline_stopped")

    async def _loop(self, interval_hours: int) -> None:
        """Main pipeline loop — runs cycles at the given interval."""
        while self._running:
            try:
                await self.run_cycle()
            except Exception as e:
                log.error("pipeline_cycle_error", error=str(e))
            await asyncio.sleep(interval_hours * 3600)

    def _row_to_finding(self, row: Any) -> BountyFinding:
        return BountyFinding(
            id=row["id"],
            target_id=row["target_id"],
            vuln_type=row["vuln_type"],
            severity=row["severity"],
            cvss=row["cvss"],
            confidence=row["confidence"],
            title=row["title"],
            description=row["description"] or "",
            steps_to_reproduce=row["steps_to_reproduce"] or "",
            poc=row["poc"] or "",
            impact=row["impact"] or "",
            suggested_fix=row["suggested_fix"] or "",
            estimated_bounty_low=row["estimated_bounty_low"],
            estimated_bounty_high=row["estimated_bounty_high"],
            status=FindingStatus(row["status"]),
        )
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bounty_pipeline.py -v`
Expected: ALL PASS (6 tests)

**Step 5: Commit**

```bash
git add src/bounty/pipeline.py tests/unit/test_bounty_pipeline.py
git commit -m "feat(bounty): add pipeline orchestrator (monitor → queue → recon → scan → verify)"
```

---

### Task 10: Telegram Integration — /bounty Command

Wire the pipeline into Telegram with the `/bounty` command.

**Files:**
- Modify: `src/gateway/channels/telegram.py` (add handler + command)
- Modify: `src/app.py` (add bounty pipeline init)
- Test: `tests/unit/test_bounty_telegram.py`

**Step 1: Write the failing test**

Create `tests/unit/test_bounty_telegram.py`:

```python
"""Tests for bounty Telegram command integration."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bounty.pipeline import BountyPipeline
from src.bounty.store import init_bounty_tables
from src.tools.base import ToolRegistry


class TestBountyTelegramHelpers:
    """Test helper functions used by the /bounty command."""

    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_bounty_tables(self.conn)
        self.registry = ToolRegistry()
        self.pipeline = BountyPipeline(self.conn, self.registry)

    def teardown_method(self):
        self.conn.close()

    def test_pipeline_stats(self):
        stats = self.pipeline.stats()
        assert stats["programs"] == 0
        assert stats["findings"]["pending"] == 0
        assert stats["earnings_usd"] == 0

    def test_get_pending_findings_empty(self):
        findings = self.pipeline.get_pending_findings()
        assert findings == []

    def test_update_finding_approved(self):
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES ('h1', 'p', 'P')"
        )
        self.conn.execute("INSERT INTO bounty_targets (program_id, domain) VALUES (1, 't.com')")
        self.conn.execute(
            "INSERT INTO bounty_findings (target_id, vuln_type, severity, cvss, confidence, title) "
            "VALUES (1, 'xss', 'HIGH', 7.5, 0.9, 'XSS')"
        )
        self.conn.commit()
        self.pipeline.update_finding_status(1, "approved")
        row = self.conn.execute("SELECT status FROM bounty_findings WHERE id=1").fetchone()
        assert row["status"] == "approved"

    def test_update_finding_rejected(self):
        self.conn.execute(
            "INSERT INTO bounty_programs (platform, program_id, name) VALUES ('h1', 'p', 'P')"
        )
        self.conn.execute("INSERT INTO bounty_targets (program_id, domain) VALUES (1, 't.com')")
        self.conn.execute(
            "INSERT INTO bounty_findings (target_id, vuln_type, severity, cvss, confidence, title) "
            "VALUES (1, 'sqli', 'CRITICAL', 9.8, 0.95, 'SQLi')"
        )
        self.conn.commit()
        self.pipeline.update_finding_status(1, "rejected")
        row = self.conn.execute("SELECT status FROM bounty_findings WHERE id=1").fetchone()
        assert row["status"] == "rejected"

    def test_pipeline_start_stop(self):
        assert self.pipeline.is_running is False

    @pytest.mark.asyncio
    async def test_pipeline_start_sets_running(self):
        await self.pipeline.start(interval_hours=999)
        assert self.pipeline.is_running is True
        await self.pipeline.stop()
        assert self.pipeline.is_running is False
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bounty_telegram.py -v`
Expected: ALL PASS (these tests use already-built components — verify they pass before modifying Telegram)

**Step 3: Add bounty pipeline to JarvisApp**

Modify `src/app.py` — add `init_bounty()` method. Find the `init_proactive` method and add after it:

```python
# In imports section, add:
from src.bounty.store import get_bounty_connection, init_bounty_tables
from src.bounty.pipeline import BountyPipeline
```

In `__init__`, add to the lazy subsystems section:
```python
self.bounty_pipeline: BountyPipeline | None = None
```

Add new method after `init_proactive`:
```python
def init_bounty(self) -> None:
    """Initialize the bug bounty pipeline."""
    from src.bounty.store import init_bounty_tables
    from src.bounty.pipeline import BountyPipeline
    from src.memory.store import get_connection

    conn = get_connection()
    init_bounty_tables(conn)
    self.bounty_pipeline = BountyPipeline(conn, self.tool_registry)
    log.info("bounty_pipeline_initialized")
```

**Step 4: Add `/bounty` command to Telegram adapter**

Modify `src/gateway/channels/telegram.py`:

In the `start()` method (around line 305), add before the `pentest` handler:
```python
self._app.add_handler(CommandHandler("bounty", self._handle_bounty))
```

Add the handler method at the end of the class (after `_handle_pentest`):
```python
async def _handle_bounty(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bounty — Bug bounty pipeline management."""
    if not self._is_authorized(update):
        return

    text = (update.message.text or "").replace("/bounty", "").strip()
    parts = text.split() if text else []
    subcmd = parts[0] if parts else "status"

    # Get pipeline from JarvisApp
    pipeline = getattr(self._jarvis_app, "bounty_pipeline", None) if hasattr(self, "_jarvis_app") else None
    if pipeline is None:
        await update.message.reply_text("Bug bounty pipeline chưa khởi tạo.")
        return

    if subcmd == "status":
        stats = pipeline.stats()
        targets = stats["targets"]
        msg = (
            f"**Bug Bounty Pipeline**\n\n"
            f"Running: {'Yes' if stats['running'] else 'No'}\n"
            f"Programs: {stats['programs']}\n"
            f"Targets: {targets['total']} (queued: {targets['queued']}, scanning: {targets['scanning']})\n"
            f"Findings: {stats['findings']['pending']} pending / {stats['findings']['total']} total\n"
            f"Earnings: ${stats['earnings_usd']:.2f}\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif subcmd == "start":
        if pipeline.is_running:
            await update.message.reply_text("Pipeline is already running.")
            return
        await pipeline.start(interval_hours=6)
        await update.message.reply_text("Bug bounty pipeline started! Checking every 6 hours.")

    elif subcmd == "stop":
        await pipeline.stop()
        await update.message.reply_text("Bug bounty pipeline stopped.")

    elif subcmd == "findings":
        findings = pipeline.get_pending_findings()
        if not findings:
            await update.message.reply_text("No pending findings.")
            return
        lines = ["**Pending Findings:**\n"]
        for f in findings[:10]:
            lines.append(
                f"#{f.id} [{f.severity}] {f.title} — "
                f"{f.confidence:.0%} — {f.estimated_bounty_str}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif subcmd == "review" and len(parts) > 1:
        try:
            fid = int(parts[1])
        except ValueError:
            await update.message.reply_text("Usage: `/bounty review <id>`", parse_mode="Markdown")
            return
        findings = pipeline.get_pending_findings()
        finding = next((f for f in findings if f.id == fid), None)
        if not finding:
            # Try all findings, not just pending
            from src.bounty.reporter import BountyReportGenerator
            row = pipeline._conn.execute("SELECT * FROM bounty_findings WHERE id=?", (fid,)).fetchone()
            if not row:
                await update.message.reply_text(f"Finding #{fid} not found.")
                return
            finding = pipeline._row_to_finding(row)
        from src.bounty.reporter import BountyReportGenerator
        target_row = pipeline._conn.execute(
            "SELECT domain FROM bounty_targets WHERE id=?", (finding.target_id,)
        ).fetchone()
        domain = target_row["domain"] if target_row else "unknown"
        report = BountyReportGenerator.generate_hackerone(finding, domain)
        if len(report) > 4000:
            report = report[:4000] + "\n...(truncated)"
        await update.message.reply_text(f"```\n{report}\n```", parse_mode="Markdown")

    elif subcmd == "approve" and len(parts) > 1:
        try:
            fid = int(parts[1])
        except ValueError:
            await update.message.reply_text("Usage: `/bounty approve <id>`", parse_mode="Markdown")
            return
        pipeline.update_finding_status(fid, "approved")
        await update.message.reply_text(f"Finding #{fid} approved. Submit manually on platform.")

    elif subcmd == "reject" and len(parts) > 1:
        try:
            fid = int(parts[1])
        except ValueError:
            await update.message.reply_text("Usage: `/bounty reject <id>`", parse_mode="Markdown")
            return
        pipeline.update_finding_status(fid, "rejected")
        await update.message.reply_text(f"Finding #{fid} rejected (false positive logged).")

    elif subcmd == "earnings":
        total = pipeline._conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as t FROM bounty_earnings"
        ).fetchone()["t"]
        await update.message.reply_text(f"Total earnings: **${total:.2f}**", parse_mode="Markdown")

    elif subcmd == "programs":
        programs = pipeline._monitor.get_active_programs()
        if not programs:
            await update.message.reply_text("No programs being monitored.")
            return
        lines = ["**Monitored Programs:**\n"]
        for p in programs[:15]:
            lines.append(f"• {p.name} ({p.platform}) — ${p.bounty_low}-${p.bounty_high} — score: {p.priority_score:.2f}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    else:
        await update.message.reply_text(
            "**Bug Bounty Commands:**\n\n"
            "`/bounty status` — Pipeline stats\n"
            "`/bounty start` — Start pipeline\n"
            "`/bounty stop` — Stop pipeline\n"
            "`/bounty programs` — List programs\n"
            "`/bounty findings` — Pending findings\n"
            "`/bounty review <id>` — Full report\n"
            "`/bounty approve <id>` — Approve finding\n"
            "`/bounty reject <id>` — Reject as FP\n"
            "`/bounty earnings` — Total earnings\n",
            parse_mode="Markdown",
        )
```

**Step 5: Wire bounty init into main.py**

Modify `src/main.py` — in `run_telegram()`, add after `app.init_proactive()`:
```python
app.init_bounty()
```

**Step 6: Run all tests to verify nothing breaks**

Run: `python -m pytest tests/unit/ -x -q`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add src/app.py src/gateway/channels/telegram.py src/main.py tests/unit/test_bounty_telegram.py
git commit -m "feat(bounty): wire pipeline into Telegram with /bounty command"
```

---

### Task 11: CLI Integration — /bounty Command

Add bounty commands to the CLI adapter.

**Files:**
- Modify: `src/gateway/channels/cli.py` (add bounty command handler)

**Step 1: Read the CLI adapter to find the command dispatch pattern**

Look at `src/gateway/channels/cli.py` — find the `_handle_slash_command` method and the dispatch dict.

**Step 2: Add /bounty to CLI dispatch**

Add to the command dispatch dictionary:
```python
"/bounty": self._handle_bounty_cmd,
```

Add the handler method:
```python
async def _handle_bounty_cmd(self, args: str) -> str:
    """Handle /bounty commands."""
    pipeline = getattr(self._app, "bounty_pipeline", None) if self._app else None
    if pipeline is None:
        return "Bug bounty pipeline not initialized. Start with --telegram mode."

    parts = args.strip().split() if args.strip() else []
    subcmd = parts[0] if parts else "status"

    if subcmd == "status":
        stats = pipeline.stats()
        targets = stats["targets"]
        return (
            f"Bug Bounty Pipeline\n"
            f"  Running: {'Yes' if stats['running'] else 'No'}\n"
            f"  Programs: {stats['programs']}\n"
            f"  Targets: {targets['total']} (queued: {targets['queued']})\n"
            f"  Findings: {stats['findings']['pending']} pending / {stats['findings']['total']} total\n"
            f"  Earnings: ${stats['earnings_usd']:.2f}"
        )
    elif subcmd == "findings":
        findings = pipeline.get_pending_findings()
        if not findings:
            return "No pending findings."
        lines = ["Pending Findings:"]
        for f in findings[:10]:
            lines.append(f"  #{f.id} [{f.severity}] {f.title} ({f.confidence:.0%})")
        return "\n".join(lines)
    elif subcmd == "start":
        await pipeline.start(interval_hours=6)
        return "Bug bounty pipeline started."
    elif subcmd == "stop":
        await pipeline.stop()
        return "Bug bounty pipeline stopped."
    else:
        return (
            "Bounty commands:\n"
            "  /bounty status   — Pipeline stats\n"
            "  /bounty start    — Start pipeline\n"
            "  /bounty stop     — Stop pipeline\n"
            "  /bounty findings — Pending findings"
        )
```

**Step 3: Run all tests**

Run: `python -m pytest tests/unit/ -x -q`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add src/gateway/channels/cli.py
git commit -m "feat(bounty): add /bounty command to CLI adapter"
```

---

### Task 12: Full Integration Test + Push

Verify the entire pipeline works end-to-end, run full test suite, push.

**Step 1: Run the full test suite**

Run: `python -m pytest tests/unit/ -x -q`
Expected: ALL PASS (previous count + ~57 new tests)

**Step 2: Verify bounty package imports cleanly**

Run: `python -c "from src.bounty.pipeline import BountyPipeline; print('OK')"`
Expected: `OK`

**Step 3: Commit any fixups**

If any tests fail, fix them and commit.

**Step 4: Push to remote**

```bash
git push origin master
```

---

## Summary

| Task | Module | Tests | Description |
|------|--------|-------|-------------|
| 1 | `src/bounty/store.py` | 11 | SQLite tables (programs, targets, findings, earnings) |
| 2 | `src/bounty/models.py` | 14 | Dataclasses (BountyProgram, Target, Finding, enums) |
| 3 | `src/bounty/monitor.py` | 8 | HackerOne program crawler + priority scoring |
| 4 | `src/bounty/queue.py` | 10 | Priority queue with state machine + rate limiting |
| 5 | `src/bounty/recon.py` | 7 | Orchestrate passive+active recon via existing tools |
| 6 | `src/bounty/scanner.py` | 7 | Orchestrate vuln scan tools (SQLi, XSS, etc.) |
| 7 | `src/bounty/verifier.py` | 7 | Re-test, confidence scoring, bounty estimation, dedup |
| 8 | `src/bounty/reporter.py` | 4 | HackerOne-format reports + Telegram summaries |
| 9 | `src/bounty/pipeline.py` | 6 | Core orchestrator loop tying everything together |
| 10 | Telegram integration | 6 | `/bounty` command (status/start/stop/findings/review/approve/reject) |
| 11 | CLI integration | — | `/bounty` command in CLI |
| 12 | Integration | — | Full test suite + push |

**Total: ~80 new tests, 10 new files, 2 modified files**
