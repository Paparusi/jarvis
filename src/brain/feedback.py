"""User Feedback Store — Collect 👍/👎 ratings for Brain Independence.

Feedback directly feeds DPO training:
- 👍 = positive signal → chosen response
- 👎 = negative signal → rejected response
- Enables preference-based training (DPO/GRPO)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("brain.feedback")


class FeedbackStore:
    """Store and query user feedback on JARVIS responses."""

    def __init__(self) -> None:
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_feedback (
                id TEXT PRIMARY KEY,
                interaction_id TEXT,
                user_id TEXT NOT NULL,
                user_message TEXT NOT NULL,
                model_response TEXT NOT NULL,
                model_used TEXT DEFAULT '',
                rating TEXT NOT NULL,
                comment TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_feedback_user ON user_feedback(user_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_feedback_rating ON user_feedback(rating)
        """)
        conn.commit()

    def store(
        self,
        user_id: str,
        user_message: str,
        model_response: str,
        rating: str,
        model_used: str = "",
        interaction_id: str = "",
        comment: str = "",
    ) -> str:
        """Store a feedback entry. Returns feedback ID."""
        feedback_id = str(uuid4())[:8]
        conn = get_connection()
        conn.execute(
            """INSERT INTO user_feedback
               (id, interaction_id, user_id, user_message, model_response,
                model_used, rating, comment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (feedback_id, interaction_id, user_id, user_message,
             model_response, model_used, rating, comment),
        )
        conn.commit()
        log.info("feedback_stored", id=feedback_id, rating=rating, user_id=user_id)
        return feedback_id

    def get_stats(self, user_id: str | None = None) -> dict:
        """Get feedback statistics."""
        conn = get_connection()
        where = "WHERE user_id = ?" if user_id else ""
        params = (user_id,) if user_id else ()

        total = conn.execute(
            f"SELECT count(*) FROM user_feedback {where}", params
        ).fetchone()[0]

        positive = conn.execute(
            f"SELECT count(*) FROM user_feedback {where}"
            + (" AND" if where else "WHERE") + " rating = 'positive'",
            params,
        ).fetchone()[0]

        negative = conn.execute(
            f"SELECT count(*) FROM user_feedback {where}"
            + (" AND" if where else "WHERE") + " rating = 'negative'",
            params,
        ).fetchone()[0]

        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "satisfaction_rate": round(positive / total, 2) if total > 0 else 0.0,
        }

    def get_dpo_pairs(self, limit: int = 100) -> list[dict]:
        """Get DPO training pairs from feedback.

        Negative-rated responses become 'rejected' in DPO.
        We pair them with the best-rated response to the same/similar query.
        """
        conn = get_connection()

        # Get negative feedback (rejected responses)
        negatives = conn.execute(
            """SELECT user_message, model_response, model_used
               FROM user_feedback WHERE rating = 'negative'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        # Get positive feedback (chosen responses)
        positives = conn.execute(
            """SELECT user_message, model_response, model_used
               FROM user_feedback WHERE rating = 'positive'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        # Build pairs: for each negative, find closest positive by user_message
        positive_by_msg: dict[str, dict] = {}
        for row in positives:
            key = row["user_message"].strip().lower()
            positive_by_msg[key] = {
                "response": row["model_response"],
                "model": row["model_used"],
            }

        pairs = []
        for row in negatives:
            key = row["user_message"].strip().lower()
            if key in positive_by_msg:
                chosen = positive_by_msg[key]
                pairs.append({
                    "prompt": row["user_message"],
                    "chosen": chosen["response"],
                    "rejected": row["model_response"],
                    "chosen_model": chosen["model"],
                    "rejected_model": row["model_used"],
                    "source": "user_feedback",
                })

        return pairs

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get recent feedback entries."""
        conn = get_connection()
        rows = conn.execute(
            """SELECT id, user_message, rating, model_used, created_at
               FROM user_feedback ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
