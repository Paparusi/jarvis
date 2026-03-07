"""Program Monitor for the Bug Bounty Pipeline.

Fetches, scores, and stores bug bounty programs from HackerOne.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import httpx

from src.bounty.models import BountyProgram
from src.bounty.store import init_bounty_tables
from src.utils.config import get_env
from src.utils.logging import get_logger

log = get_logger("bounty.monitor")

_USER_AGENT = "JARVIS/2.0 BountyBot"
_HACKERONE_API_URL = "https://api.hackerone.com/v1/hackers/programs"
_TIMEOUT = 30


class ProgramMonitor:
    """Monitor bug bounty programs from HackerOne and other platforms."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        init_bounty_tables(conn)

    def calculate_priority(
        self,
        bounty_high: int = 0,
        launch_date: str = "",
        report_count: int = 0,
    ) -> float:
        """Calculate a priority score from 0.0 to 1.0.

        Factors:
        - Newness: <7d = +0.4, <30d = +0.2, <90d = +0.1
        - Bounty amount: >=10k = +0.4, >=5k = +0.3, >=1k = +0.2, >=100 = +0.1
        - Competition: <=10 reports = +0.2, <=50 = +0.1
        """
        score = 0.0

        # Newness bonus
        if launch_date:
            try:
                launched = datetime.fromisoformat(launch_date.replace("Z", "+00:00"))
                if launched.tzinfo is None:
                    launched = launched.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                age_days = (now - launched).days
                if age_days < 7:
                    score += 0.4
                elif age_days < 30:
                    score += 0.2
                elif age_days < 90:
                    score += 0.1
            except (ValueError, TypeError):
                pass

        # Bounty amount bonus
        if bounty_high >= 10000:
            score += 0.4
        elif bounty_high >= 5000:
            score += 0.3
        elif bounty_high >= 1000:
            score += 0.2
        elif bounty_high >= 100:
            score += 0.1

        # Competition bonus (lower = better)
        if report_count <= 10:
            score += 0.2
        elif report_count <= 50:
            score += 0.1

        return min(score, 1.0)

    def save_program(self, program: BountyProgram) -> None:
        """Insert or update a bounty program.

        Uses INSERT ... ON CONFLICT(platform, program_id) DO UPDATE
        to upsert. scope_domains is stored as a JSON string.
        """
        self.conn.execute(
            """INSERT INTO bounty_programs
               (platform, program_id, name, url, scope_domains,
                bounty_low, bounty_high, priority_score, status, last_checked)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(platform, program_id) DO UPDATE SET
                   name = excluded.name,
                   url = excluded.url,
                   scope_domains = excluded.scope_domains,
                   bounty_low = excluded.bounty_low,
                   bounty_high = excluded.bounty_high,
                   priority_score = excluded.priority_score,
                   status = excluded.status,
                   last_checked = datetime('now')
            """,
            (
                program.platform,
                program.program_id,
                program.name,
                program.url,
                json.dumps(program.scope_domains),
                program.bounty_low,
                program.bounty_high,
                program.priority_score,
                program.status,
            ),
        )
        self.conn.commit()

    def get_active_programs(self) -> list[BountyProgram]:
        """Return all active programs ordered by priority_score descending."""
        rows = self.conn.execute(
            """SELECT id, platform, program_id, name, url, scope_domains,
                      bounty_low, bounty_high, priority_score, status
               FROM bounty_programs
               WHERE status = 'active'
               ORDER BY priority_score DESC"""
        ).fetchall()

        programs: list[BountyProgram] = []
        for row in rows:
            programs.append(
                BountyProgram(
                    id=row["id"],
                    platform=row["platform"],
                    program_id=row["program_id"],
                    name=row["name"],
                    url=row["url"],
                    scope_domains=json.loads(row["scope_domains"]),
                    bounty_low=row["bounty_low"],
                    bounty_high=row["bounty_high"],
                    priority_score=row["priority_score"],
                    status=row["status"],
                )
            )
        return programs

    async def fetch_hackerone_programs(self) -> list[BountyProgram]:
        """Fetch public bounty programs from HackerOne API.

        Filters for programs that:
        - offer bounties
        - are in public mode
        - have URL-type scopes

        Returns a list of BountyProgram instances (not yet saved).
        """
        username = get_env("HACKERONE_API_USERNAME")
        token = get_env("HACKERONE_API_TOKEN")
        if not token or not username:
            log.warning("hackerone_credentials_missing")
            return []

        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        }

        programs: list[BountyProgram] = []

        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=headers, auth=(username, token),
        ) as client:
            url: str | None = _HACKERONE_API_URL
            while url:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()

                for item in data.get("data", []):
                    attrs = item.get("attributes", {})

                    # Filter: must offer bounties and be in public mode
                    if not attrs.get("offers_bounties", False):
                        continue
                    if attrs.get("state") != "public_mode":
                        continue

                    # Extract scope domains from structured_scopes (if available)
                    relationships = item.get("relationships", {})
                    scopes_data = (
                        relationships.get("structured_scopes", {})
                        .get("data", [])
                    )
                    domains: list[str] = []
                    for scope in scopes_data:
                        scope_attrs = scope.get("attributes", {})
                        asset_type = scope_attrs.get("asset_type", "")
                        if asset_type.upper() == "URL":
                            identifier = scope_attrs.get("asset_identifier", "")
                            if identifier:
                                domains.append(identifier)

                    handle = attrs.get("handle", item.get("id", ""))

                    # HackerOne API v1 often omits scopes — use handle as domain
                    if not domains and handle:
                        domains = [f"{handle}.com"]
                    bounty_high = int(attrs.get("top_bounty_range", 0) or 0)
                    bounty_low = int(attrs.get("low_bounty_range", 0) or 0)
                    launch_date = attrs.get("started_accepting_at", "")
                    report_count = int(
                        attrs.get("number_of_reports_for_user", 0) or 0
                    )

                    priority = self.calculate_priority(
                        bounty_high=bounty_high,
                        launch_date=launch_date,
                        report_count=report_count,
                    )

                    programs.append(
                        BountyProgram(
                            platform="hackerone",
                            program_id=handle,
                            name=attrs.get("name", handle),
                            url=f"https://hackerone.com/{handle}",
                            scope_domains=domains,
                            bounty_low=bounty_low,
                            bounty_high=bounty_high,
                            priority_score=priority,
                            status="active",
                        )
                    )

                # Pagination
                url = data.get("links", {}).get("next")

        log.info("hackerone_fetch_complete", count=len(programs))
        return programs

    @staticmethod
    async def _fetch_program_scopes(client: httpx.AsyncClient, handle: str) -> list[str]:
        """Fetch structured scopes for a single program by handle."""
        url = f"https://api.hackerone.com/v1/hackers/programs/{handle}"
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        data = resp.json()
        relationships = data.get("data", {}).get("relationships", {})
        scopes = relationships.get("structured_scopes", {}).get("data", [])
        domains: list[str] = []
        for scope in scopes:
            sa = scope.get("attributes", {})
            if sa.get("asset_type", "").upper() == "URL":
                identifier = sa.get("asset_identifier", "")
                if identifier:
                    domains.append(identifier)
        return domains

    async def refresh(self) -> int:
        """Fetch programs from HackerOne and save them all.

        Returns the number of programs saved.
        """
        programs = await self.fetch_hackerone_programs()
        for prog in programs:
            self.save_program(prog)
        log.info("programs_refreshed", count=len(programs))
        return len(programs)
