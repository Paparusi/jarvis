"""SQLite-based persistent store for JARVIS memory.

Thay thế PostgreSQL cho Phase 2 — không cần cài gì thêm.
Migrate lên PostgreSQL + pgvector khi cần scale.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("memory.store")

_DB_PATH: Path | None = None
_conn: sqlite3.Connection | None = None


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = get_project_root() / "data" / "jarvis.db"
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get or create SQLite connection."""
    global _conn
    if _conn is None:
        db_path = _get_db_path()
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _init_tables(_conn)
        log.info("database_connected", path=str(db_path))
    return _conn


def _init_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        -- Semantic Memory: facts, knowledge, user preferences
        CREATE TABLE IF NOT EXISTS semantic_memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            source TEXT DEFAULT 'conversation',
            embedding BLOB,
            importance REAL DEFAULT 0.5,
            access_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Episodic Memory: conversation summaries, events
        CREATE TABLE IF NOT EXISTS episodic_memories (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            summary TEXT NOT NULL,
            details TEXT,
            embedding BLOB,
            outcome TEXT,
            importance REAL DEFAULT 0.5,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- Conversation History: persistent across restarts
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_key TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_key);

        -- Full-text search on semantic memories
        CREATE VIRTUAL TABLE IF NOT EXISTS semantic_fts USING fts5(
            content,
            category,
            content_rowid='rowid'
        );
    """)
    conn.commit()


def close() -> None:
    """Close the database connection."""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
