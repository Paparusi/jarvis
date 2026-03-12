"""Message Store — SQLite persistence for agent-to-agent messaging and meetings."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("company.message_store")

_tables_initialized = False


def _init_message_tables(conn: sqlite3.Connection) -> None:
    """Create message and meeting tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            sender_type TEXT NOT NULL,
            recipient_id TEXT NOT NULL,
            recipient_type TEXT NOT NULL,
            message_type TEXT NOT NULL,
            subject TEXT DEFAULT '',
            content TEXT NOT NULL,
            context TEXT DEFAULT '{}',
            priority INTEGER DEFAULT 5,
            status TEXT DEFAULT 'pending',
            parent_message_id INTEGER,
            created_at TEXT NOT NULL,
            read_at TEXT,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_am_recipient ON agent_messages(recipient_id, status);
        CREATE INDEX IF NOT EXISTS idx_am_thread ON agent_messages(thread_id);

        CREATE TABLE IF NOT EXISTS agent_meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL UNIQUE,
            topic TEXT NOT NULL,
            initiated_by TEXT NOT NULL,
            participants TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            summary TEXT,
            max_rounds INTEGER DEFAULT 3,
            current_round INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            concluded_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_meeting_status ON agent_meetings(status);
    """)


def _ensure_tables() -> sqlite3.Connection:
    """Get DB connection and ensure message tables exist."""
    global _tables_initialized
    conn = get_connection()
    if not _tables_initialized:
        _init_message_tables(conn)
        _tables_initialized = True
    return conn


def _now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


class MessageStore:
    """CRUD for the agent_messages table."""

    def send_message(
        self,
        thread_id: str,
        sender_id: str,
        sender_type: str,
        recipient_id: str,
        recipient_type: str,
        message_type: str,
        content: str,
        subject: str = "",
        context: dict[str, Any] | None = None,
        priority: int = 5,
        parent_message_id: int | None = None,
    ) -> int:
        """Insert a new message. Returns the message id."""
        conn = _ensure_tables()
        cursor = conn.execute(
            """INSERT INTO agent_messages
               (thread_id, sender_id, sender_type, recipient_id, recipient_type,
                message_type, subject, content, context, priority,
                parent_message_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                thread_id,
                sender_id,
                sender_type,
                recipient_id,
                recipient_type,
                message_type,
                subject,
                content,
                json.dumps(context or {}),
                priority,
                parent_message_id,
                _now_iso(),
            ),
        )
        conn.commit()
        message_id = cursor.lastrowid
        log.info(
            "message_sent",
            message_id=message_id,
            thread_id=thread_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
        )
        return message_id

    def get_inbox(
        self,
        agent_id: str,
        unread_only: bool = True,
        limit: int = 20,
    ) -> list[dict]:
        """Get messages for an agent, newest first."""
        conn = _ensure_tables()
        if unread_only:
            rows = conn.execute(
                """SELECT * FROM agent_messages
                   WHERE recipient_id = ? AND status = 'pending'
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (agent_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM agent_messages
                   WHERE recipient_id = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (agent_id, limit),
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["context"] = json.loads(d.get("context") or "{}")
            results.append(d)
        return results

    def get_thread(self, thread_id: str, limit: int = 50) -> list[dict]:
        """Get all messages in a thread, oldest first."""
        conn = _ensure_tables()
        rows = conn.execute(
            """SELECT * FROM agent_messages
               WHERE thread_id = ?
               ORDER BY created_at ASC
               LIMIT ?""",
            (thread_id, limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["context"] = json.loads(d.get("context") or "{}")
            results.append(d)
        return results

    def mark_read(self, message_id: int) -> None:
        """Mark a message as read."""
        conn = _ensure_tables()
        conn.execute(
            """UPDATE agent_messages
               SET status = 'read', read_at = ?
               WHERE id = ?""",
            (_now_iso(), message_id),
        )
        conn.commit()

    def mark_resolved(self, message_id: int) -> None:
        """Mark a message as resolved."""
        conn = _ensure_tables()
        conn.execute(
            """UPDATE agent_messages
               SET status = 'resolved', resolved_at = ?
               WHERE id = ?""",
            (_now_iso(), message_id),
        )
        conn.commit()

    def get_pending_count(self, agent_id: str) -> int:
        """Count pending messages for an agent."""
        conn = _ensure_tables()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM agent_messages WHERE recipient_id = ? AND status = 'pending'",
            (agent_id,),
        ).fetchone()
        return row["cnt"]

    def get_recent(self, limit: int = 50) -> list[dict]:
        """Get recent messages across all agents, newest first."""
        conn = _ensure_tables()
        rows = conn.execute(
            """SELECT * FROM agent_messages
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["context"] = json.loads(d.get("context") or "{}")
            results.append(d)
        return results


class MeetingStore:
    """CRUD for the agent_meetings table."""

    def create_meeting(
        self,
        thread_id: str,
        topic: str,
        initiated_by: str,
        participants: list[str],
        max_rounds: int = 3,
    ) -> int:
        """Insert a new meeting. Returns the meeting id."""
        conn = _ensure_tables()
        cursor = conn.execute(
            """INSERT INTO agent_meetings
               (thread_id, topic, initiated_by, participants, max_rounds, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                thread_id,
                topic,
                initiated_by,
                json.dumps(participants),
                max_rounds,
                _now_iso(),
            ),
        )
        conn.commit()
        meeting_id = cursor.lastrowid
        log.info(
            "meeting_created",
            meeting_id=meeting_id,
            thread_id=thread_id,
            topic=topic,
            initiated_by=initiated_by,
        )
        return meeting_id

    def get_meeting(self, thread_id: str) -> dict | None:
        """Get a meeting by thread_id. Returns None if not found."""
        conn = _ensure_tables()
        row = conn.execute(
            "SELECT * FROM agent_meetings WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["participants"] = json.loads(d.get("participants") or "[]")
        return d

    def add_round(self, thread_id: str) -> int:
        """Increment current_round for a meeting. Returns the new round number."""
        conn = _ensure_tables()
        conn.execute(
            """UPDATE agent_meetings
               SET current_round = current_round + 1
               WHERE thread_id = ?""",
            (thread_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT current_round FROM agent_meetings WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return row["current_round"]

    def conclude_meeting(self, thread_id: str, summary: str) -> None:
        """Mark a meeting as concluded with a summary."""
        conn = _ensure_tables()
        conn.execute(
            """UPDATE agent_meetings
               SET status = 'concluded', summary = ?, concluded_at = ?
               WHERE thread_id = ?""",
            (summary, _now_iso(), thread_id),
        )
        conn.commit()
        log.info("meeting_concluded", thread_id=thread_id)

    def get_active_meetings(self) -> list[dict]:
        """Get all active meetings."""
        conn = _ensure_tables()
        rows = conn.execute(
            "SELECT * FROM agent_meetings WHERE status = 'active' ORDER BY created_at DESC",
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["participants"] = json.loads(d.get("participants") or "[]")
            results.append(d)
        return results
