"""CRM Tools — Customer Relationship Management for JARVIS.

Track leads, log interactions, and view the sales pipeline.
Uses SQLite for persistence via the shared memory store.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from src.memory.store import get_connection
from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.crm")

_tables_initialized = False

_VALID_STATUSES = {"new", "contacted", "qualified", "proposal", "won", "lost"}
_VALID_ACTIVITY_TYPES = {"call", "email", "meeting", "demo", "follow_up", "social"}


def _init_tables() -> None:
    """Create CRM tables if they don't exist (idempotent)."""
    global _tables_initialized
    if _tables_initialized:
        return

    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS crm_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            company TEXT,
            source TEXT,
            status TEXT DEFAULT 'new',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_lead_status ON crm_leads(status);

        CREATE TABLE IF NOT EXISTS crm_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            activity_type TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES crm_leads(id)
        );
        CREATE INDEX IF NOT EXISTS idx_activity_lead ON crm_activities(lead_id);
    """)
    conn.commit()
    _tables_initialized = True
    log.info("crm_tables_initialized")


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ── Tool Handlers ─────────────────────────────────────────────────────


async def crm_add_lead(
    name: str,
    source: str,
    email: str = "",
    company: str = "",
    notes: str = "",
    status: str = "new",
) -> ToolResult:
    """Add a new lead/contact to the CRM."""
    start = time.monotonic()
    _init_tables()

    if not name or not name.strip():
        return ToolResult(success=False, output="", error="Lead name is required.")

    if not source or not source.strip():
        return ToolResult(success=False, output="", error="Lead source is required.")

    status = status.lower().strip()
    if status not in _VALID_STATUSES:
        return ToolResult(
            success=False, output="",
            error=f"Invalid status: '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}",
        )

    now = _now_iso()
    conn = get_connection()

    try:
        cursor = conn.execute(
            """INSERT INTO crm_leads (name, email, company, source, status, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name.strip(), email.strip(), company.strip(), source.strip(), status, notes.strip(), now, now),
        )
        conn.commit()
        lead_id = cursor.lastrowid
        elapsed = int((time.monotonic() - start) * 1000)

        log.info("crm_lead_added", lead_id=lead_id, name=name, status=status)

        output = (
            f"Lead added successfully.\n"
            f"  ID: {lead_id}\n"
            f"  Name: {name.strip()}\n"
            f"  Status: {status}\n"
            f"  Source: {source.strip()}"
        )
        if company.strip():
            output += f"\n  Company: {company.strip()}"
        if email.strip():
            output += f"\n  Email: {email.strip()}"

        return ToolResult(
            success=True,
            output=output,
            data={"lead_id": lead_id, "name": name.strip(), "status": status},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("crm_add_lead_error", error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Failed to add lead: {e}",
            execution_time_ms=elapsed,
        )


async def crm_search(query: str, status: str = "") -> ToolResult:
    """Search leads/contacts by name, email, company, or notes."""
    start = time.monotonic()
    _init_tables()

    if not query or not query.strip():
        return ToolResult(success=False, output="", error="Search query is required.")

    conn = get_connection()
    pattern = f"%{query.strip()}%"

    try:
        if status and status.strip():
            status = status.lower().strip()
            if status not in _VALID_STATUSES:
                return ToolResult(
                    success=False, output="",
                    error=f"Invalid status filter: '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}",
                )
            rows = conn.execute(
                """SELECT id, name, email, company, source, status, notes, created_at, updated_at
                   FROM crm_leads
                   WHERE (name LIKE ? OR email LIKE ? OR company LIKE ? OR notes LIKE ?)
                     AND status = ?
                   ORDER BY updated_at DESC
                   LIMIT 50""",
                (pattern, pattern, pattern, pattern, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, name, email, company, source, status, notes, created_at, updated_at
                   FROM crm_leads
                   WHERE name LIKE ? OR email LIKE ? OR company LIKE ? OR notes LIKE ?
                   ORDER BY updated_at DESC
                   LIMIT 50""",
                (pattern, pattern, pattern, pattern),
            ).fetchall()

        elapsed = int((time.monotonic() - start) * 1000)

        if not rows:
            filter_msg = f" with status '{status}'" if status else ""
            return ToolResult(
                success=True,
                output=f"No leads found matching '{query.strip()}'{filter_msg}.",
                data={"count": 0, "results": []},
                execution_time_ms=elapsed,
            )

        lines = [f"Found {len(rows)} lead(s) matching '{query.strip()}':\n"]
        results = []
        for row in rows:
            lead = dict(row)
            results.append(lead)
            line = f"  [{lead['id']}] {lead['name']} — {lead['status']}"
            if lead.get("company"):
                line += f" @ {lead['company']}"
            if lead.get("email"):
                line += f" ({lead['email']})"
            lines.append(line)
            if lead.get("notes"):
                note_preview = lead["notes"][:80]
                if len(lead["notes"]) > 80:
                    note_preview += "..."
                lines.append(f"       Notes: {note_preview}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"count": len(rows), "results": results},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("crm_search_error", error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Search failed: {e}",
            execution_time_ms=elapsed,
        )


async def crm_update(
    lead_id: int,
    status: str = "",
    notes: str = "",
    email: str = "",
    company: str = "",
) -> ToolResult:
    """Update a lead's status or information."""
    start = time.monotonic()
    _init_tables()

    conn = get_connection()

    # Check the lead exists
    existing = conn.execute(
        "SELECT id, name, status FROM crm_leads WHERE id = ?", (lead_id,)
    ).fetchone()

    if not existing:
        return ToolResult(
            success=False, output="",
            error=f"Lead with ID {lead_id} not found.",
        )

    # Build dynamic UPDATE
    updates: list[str] = []
    params: list[Any] = []
    changes: list[str] = []

    if status and status.strip():
        status = status.lower().strip()
        if status not in _VALID_STATUSES:
            return ToolResult(
                success=False, output="",
                error=f"Invalid status: '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}",
            )
        updates.append("status = ?")
        params.append(status)
        changes.append(f"status -> {status}")

    if notes and notes.strip():
        updates.append("notes = ?")
        params.append(notes.strip())
        changes.append("notes updated")

    if email and email.strip():
        updates.append("email = ?")
        params.append(email.strip())
        changes.append(f"email -> {email.strip()}")

    if company and company.strip():
        updates.append("company = ?")
        params.append(company.strip())
        changes.append(f"company -> {company.strip()}")

    if not updates:
        return ToolResult(
            success=False, output="",
            error="No fields to update. Provide at least one of: status, notes, email, company.",
        )

    updates.append("updated_at = ?")
    params.append(_now_iso())
    params.append(lead_id)

    try:
        conn.execute(
            f"UPDATE crm_leads SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        elapsed = int((time.monotonic() - start) * 1000)

        lead_name = existing["name"]
        log.info("crm_lead_updated", lead_id=lead_id, changes=changes)

        return ToolResult(
            success=True,
            output=f"Lead [{lead_id}] {lead_name} updated:\n  " + "\n  ".join(changes),
            data={"lead_id": lead_id, "changes": changes},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("crm_update_error", lead_id=lead_id, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Failed to update lead: {e}",
            execution_time_ms=elapsed,
        )


async def crm_pipeline() -> ToolResult:
    """View sales pipeline summary — leads grouped by status with recent activity."""
    start = time.monotonic()
    _init_tables()

    conn = get_connection()

    try:
        # Count leads per stage
        stage_rows = conn.execute(
            """SELECT status, COUNT(*) as count
               FROM crm_leads
               GROUP BY status
               ORDER BY CASE status
                   WHEN 'new' THEN 1
                   WHEN 'contacted' THEN 2
                   WHEN 'qualified' THEN 3
                   WHEN 'proposal' THEN 4
                   WHEN 'won' THEN 5
                   WHEN 'lost' THEN 6
               END""",
        ).fetchall()

        # Total leads
        total_row = conn.execute("SELECT COUNT(*) as total FROM crm_leads").fetchone()
        total = total_row["total"] if total_row else 0

        # Recent activity (last 10)
        recent_activities = conn.execute(
            """SELECT a.activity_type, a.notes, a.created_at, l.name as lead_name
               FROM crm_activities a
               JOIN crm_leads l ON a.lead_id = l.id
               ORDER BY a.created_at DESC
               LIMIT 10""",
        ).fetchall()

        # Recently updated leads
        recent_leads = conn.execute(
            """SELECT id, name, status, company, updated_at
               FROM crm_leads
               ORDER BY updated_at DESC
               LIMIT 5""",
        ).fetchall()

        elapsed = int((time.monotonic() - start) * 1000)

        if total == 0:
            return ToolResult(
                success=True,
                output="CRM Pipeline is empty. Add leads with crm_add_lead to get started.",
                data={"total": 0, "stages": {}},
                execution_time_ms=elapsed,
            )

        # Build output
        lines = [f"Sales Pipeline ({total} total leads)\n"]
        lines.append("Stage Breakdown:")

        stage_data = {}
        pipeline_order = ["new", "contacted", "qualified", "proposal", "won", "lost"]
        stage_emoji = {
            "new": "[ NEW ]",
            "contacted": "[ CTD ]",
            "qualified": "[ QFD ]",
            "proposal": "[ PRP ]",
            "won": "[ WON ]",
            "lost": "[ LST ]",
        }

        # Build a map from query results
        for row in stage_rows:
            stage_data[row["status"]] = row["count"]

        for stage in pipeline_order:
            count = stage_data.get(stage, 0)
            if count > 0:
                bar = "#" * min(count, 30)
                lines.append(f"  {stage_emoji.get(stage, stage):>8s}  {bar} {count}")

        # Recently updated leads
        if recent_leads:
            lines.append("\nRecently Updated:")
            for lead in recent_leads:
                line = f"  [{lead['id']}] {lead['name']} — {lead['status']}"
                if lead.get("company"):
                    line += f" @ {lead['company']}"
                lines.append(line)

        # Recent activities
        if recent_activities:
            lines.append("\nRecent Activity:")
            for act in recent_activities:
                note_preview = (act["notes"] or "")[:60]
                if len(act["notes"] or "") > 60:
                    note_preview += "..."
                lines.append(
                    f"  {act['activity_type']:>9s} | {act['lead_name']} — {note_preview}"
                )

        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"total": total, "stages": stage_data},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("crm_pipeline_error", error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Failed to load pipeline: {e}",
            execution_time_ms=elapsed,
        )


async def crm_log_activity(
    lead_id: int,
    activity_type: str,
    notes: str,
) -> ToolResult:
    """Log a customer interaction / activity for a lead."""
    start = time.monotonic()
    _init_tables()

    conn = get_connection()

    # Validate lead exists
    existing = conn.execute(
        "SELECT id, name FROM crm_leads WHERE id = ?", (lead_id,)
    ).fetchone()

    if not existing:
        return ToolResult(
            success=False, output="",
            error=f"Lead with ID {lead_id} not found.",
        )

    activity_type = activity_type.lower().strip()
    if activity_type not in _VALID_ACTIVITY_TYPES:
        return ToolResult(
            success=False, output="",
            error=f"Invalid activity type: '{activity_type}'. Must be one of: {', '.join(sorted(_VALID_ACTIVITY_TYPES))}",
        )

    if not notes or not notes.strip():
        return ToolResult(success=False, output="", error="Activity notes are required.")

    now = _now_iso()

    try:
        cursor = conn.execute(
            """INSERT INTO crm_activities (lead_id, activity_type, notes, created_at)
               VALUES (?, ?, ?, ?)""",
            (lead_id, activity_type, notes.strip(), now),
        )
        # Also update the lead's updated_at timestamp
        conn.execute(
            "UPDATE crm_leads SET updated_at = ? WHERE id = ?",
            (now, lead_id),
        )
        conn.commit()

        activity_id = cursor.lastrowid
        elapsed = int((time.monotonic() - start) * 1000)

        lead_name = existing["name"]
        log.info("crm_activity_logged", activity_id=activity_id, lead_id=lead_id, type=activity_type)

        return ToolResult(
            success=True,
            output=(
                f"Activity logged for [{lead_id}] {lead_name}.\n"
                f"  Type: {activity_type}\n"
                f"  Notes: {notes.strip()}\n"
                f"  Activity ID: {activity_id}"
            ),
            data={"activity_id": activity_id, "lead_id": lead_id, "activity_type": activity_type},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("crm_log_activity_error", lead_id=lead_id, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Failed to log activity: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

crm_add_lead_tool = ToolDefinition(
    name="crm_add_lead",
    description="Add a new lead/contact to the CRM. Track potential customers through the sales pipeline.",
    parameters=[
        ToolParameter(name="name", type="string", description="Full name of the lead/contact"),
        ToolParameter(name="source", type="string", description="Where the lead came from (e.g. website, referral, cold_call, linkedin, conference)"),
        ToolParameter(
            name="email", type="string",
            description="Email address of the lead",
            required=False, default="",
        ),
        ToolParameter(
            name="company", type="string",
            description="Company or organization the lead belongs to",
            required=False, default="",
        ),
        ToolParameter(
            name="notes", type="string",
            description="Additional notes about the lead",
            required=False, default="",
        ),
        ToolParameter(
            name="status", type="string",
            description="Current status in the pipeline",
            required=False, default="new",
            enum=["new", "contacted", "qualified", "proposal", "won", "lost"],
        ),
    ],
    handler=crm_add_lead,
    timeout_seconds=10,
)

crm_search_tool = ToolDefinition(
    name="crm_search",
    description="Search CRM leads/contacts by name, email, company, or notes. Optionally filter by pipeline status.",
    parameters=[
        ToolParameter(name="query", type="string", description="Search term to match against name, email, company, or notes"),
        ToolParameter(
            name="status", type="string",
            description="Filter results by pipeline status",
            required=False, default="",
            enum=["new", "contacted", "qualified", "proposal", "won", "lost"],
        ),
    ],
    handler=crm_search,
    timeout_seconds=10,
)

crm_update_tool = ToolDefinition(
    name="crm_update",
    description="Update an existing lead's status or information. Provide the lead ID and at least one field to change.",
    parameters=[
        ToolParameter(name="lead_id", type="integer", description="ID of the lead to update"),
        ToolParameter(
            name="status", type="string",
            description="New pipeline status",
            required=False, default="",
            enum=["new", "contacted", "qualified", "proposal", "won", "lost"],
        ),
        ToolParameter(
            name="notes", type="string",
            description="Updated notes for the lead",
            required=False, default="",
        ),
        ToolParameter(
            name="email", type="string",
            description="Updated email address",
            required=False, default="",
        ),
        ToolParameter(
            name="company", type="string",
            description="Updated company name",
            required=False, default="",
        ),
    ],
    handler=crm_update,
    timeout_seconds=10,
)

crm_pipeline_tool = ToolDefinition(
    name="crm_pipeline",
    description="View the sales pipeline summary — leads grouped by stage, counts per status, and recent activity.",
    parameters=[],
    handler=crm_pipeline,
    timeout_seconds=10,
)

crm_log_activity_tool = ToolDefinition(
    name="crm_log_activity",
    description="Log a customer interaction (call, email, meeting, demo, follow-up, or social) for a lead.",
    parameters=[
        ToolParameter(name="lead_id", type="integer", description="ID of the lead this activity is for"),
        ToolParameter(
            name="activity_type", type="string",
            description="Type of interaction",
            enum=["call", "email", "meeting", "demo", "follow_up", "social"],
        ),
        ToolParameter(name="notes", type="string", description="Description of the interaction or activity"),
    ],
    handler=crm_log_activity,
    timeout_seconds=10,
)
