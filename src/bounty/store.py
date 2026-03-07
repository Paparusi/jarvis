"""SQLite storage for the Bug Bounty Pipeline.

Tables: bounty_programs, bounty_targets, bounty_findings, bounty_earnings.
Follows the same pattern as src.memory.store (get_connection + init_tables).
"""

from __future__ import annotations

import sqlite3

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("bounty.store")

_bounty_initialized = False


def init_bounty_tables(conn: sqlite3.Connection) -> None:
    """Create bounty tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bounty_programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            program_id TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT DEFAULT '',
            scope_domains TEXT DEFAULT '[]',
            bounty_low INTEGER DEFAULT 0,
            bounty_high INTEGER DEFAULT 0,
            priority_score REAL DEFAULT 0.0,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now')),
            last_checked TEXT,
            UNIQUE(platform, program_id)
        );

        CREATE TABLE IF NOT EXISTS bounty_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            program_id INTEGER NOT NULL,
            domain TEXT NOT NULL,
            scope_type TEXT DEFAULT 'domain',
            state TEXT DEFAULT 'queued',
            last_scanned TEXT,
            next_scan TEXT,
            scan_count INTEGER DEFAULT 0,
            findings_count INTEGER DEFAULT 0,
            FOREIGN KEY (program_id) REFERENCES bounty_programs(id),
            UNIQUE(program_id, domain)
        );

        CREATE TABLE IF NOT EXISTS bounty_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            vuln_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            cvss REAL DEFAULT 0.0,
            confidence REAL DEFAULT 0.0,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            steps_to_reproduce TEXT DEFAULT '',
            poc TEXT DEFAULT '',
            impact TEXT DEFAULT '',
            suggested_fix TEXT DEFAULT '',
            estimated_bounty_low INTEGER,
            estimated_bounty_high INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (target_id) REFERENCES bounty_targets(id)
        );

        CREATE TABLE IF NOT EXISTS bounty_earnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            finding_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            paid_at TEXT,
            FOREIGN KEY (finding_id) REFERENCES bounty_findings(id)
        );
    """)
    conn.commit()
    log.info("bounty_tables_initialized")


def get_bounty_connection() -> sqlite3.Connection:
    """Get a SQLite connection with bounty tables initialized."""
    global _bounty_initialized
    conn = get_connection()
    if not _bounty_initialized:
        init_bounty_tables(conn)
        _bounty_initialized = True
    return conn
