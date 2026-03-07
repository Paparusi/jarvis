"""Episodic Memory — Conversation history persistent across restarts.

Lưu lịch sử hội thoại vào SQLite, tự động load khi JARVIS restart.
Khác với Working Memory (in-memory, mất khi restart).
"""

from __future__ import annotations

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("memory.episodic")


class EpisodicMemory:
    """Persistent conversation history."""

    def save_message(self, session_key: str, role: str, content: str) -> None:
        """Save a message to persistent history."""
        conn = get_connection()
        conn.execute(
            "INSERT INTO conversations (session_key, role, content) VALUES (?, ?, ?)",
            (session_key, role, content),
        )
        conn.commit()

    def load_history(self, session_key: str, limit: int = 50) -> list[dict]:
        """Load conversation history for a session."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT role, content FROM conversations WHERE session_key = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_key, limit),
        ).fetchall()
        # Reverse because we selected DESC
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def get_recent_context(self, session_key: str, n_messages: int = 10) -> str:
        """Get recent conversation as a text summary for context injection."""
        history = self.load_history(session_key, limit=n_messages)
        if not history:
            return ""
        lines = []
        for msg in history:
            prefix = "User" if msg["role"] == "user" else "JARVIS"
            lines.append(f"{prefix}: {msg['content']}")
        return "\n".join(lines)

    def count_messages(self, session_key: str) -> int:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM conversations WHERE session_key = ?",
            (session_key,),
        ).fetchone()
        return row["cnt"]

    def get_all_sessions(self) -> list[dict]:
        """List all sessions with message counts."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT session_key, COUNT(*) as msg_count, "
            "MIN(created_at) as first_msg, MAX(created_at) as last_msg "
            "FROM conversations GROUP BY session_key "
            "ORDER BY last_msg DESC"
        ).fetchall()
        return [dict(r) for r in rows]
