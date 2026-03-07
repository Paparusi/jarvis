"""Feedback Loop — Learn routing decisions from user feedback.

Connects 👍/👎 feedback to confidence calibration:
- Tracks which query types get negative feedback when handled locally
- Stores query embeddings + feedback to build routing patterns
- Auto-escalates similar queries to cloud when local model historically fails

Self-improving routing loop:
User gives 👎 to local response → similar future queries route to cloud → better UX
"""

from __future__ import annotations

import numpy as np

from src.memory.embeddings import cosine_similarity, get_embedding
from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("metacognition.feedback_loop")

# Similarity threshold to consider a query "similar" to past feedback
_SIMILARITY_THRESHOLD = 0.78

# Minimum negative feedbacks before pattern is actionable
_MIN_NEGATIVES = 2


class FeedbackLoop:
    """Learn routing patterns from user feedback."""

    def __init__(self) -> None:
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback_routing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_text TEXT NOT NULL,
                query_embedding BLOB,
                model_used TEXT NOT NULL,
                rating TEXT NOT NULL,
                complexity TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_feedback_routing_rating
            ON feedback_routing(rating)
        """)
        conn.commit()

    async def record_feedback(
        self,
        query: str,
        model_used: str,
        rating: str,
        complexity: str = "",
    ) -> None:
        """Record feedback for a query to learn routing patterns."""
        embedding = await get_embedding(query)
        conn = get_connection()
        conn.execute(
            """INSERT INTO feedback_routing
               (query_text, query_embedding, model_used, rating, complexity)
               VALUES (?, ?, ?, ?, ?)""",
            (query, embedding.tobytes(), model_used, rating, complexity),
        )
        conn.commit()
        log.info("feedback_recorded", rating=rating, model=model_used,
                 query=query[:60])

    async def check_escalation(self, query: str) -> tuple[bool, float]:
        """Check if similar queries historically got negative feedback on local.

        Returns:
            (should_escalate, penalty_score)
            penalty_score is 0.0-1.0 — how strongly history suggests escalation.
        """
        conn = get_connection()

        # Get negative feedback entries for local models
        neg_rows = conn.execute(
            """SELECT query_embedding FROM feedback_routing
               WHERE rating = 'negative' AND model_used LIKE '%ollama%'
               ORDER BY created_at DESC LIMIT 100"""
        ).fetchall()

        if len(neg_rows) < _MIN_NEGATIVES:
            return False, 0.0

        query_embedding = await get_embedding(query)

        # Check similarity against past negative feedback
        similar_negatives = 0
        max_similarity = 0.0

        for row in neg_rows:
            if not row["query_embedding"]:
                continue
            stored_vec = np.frombuffer(row["query_embedding"], dtype=np.float32)
            sim = cosine_similarity(query_embedding, stored_vec)
            if sim >= _SIMILARITY_THRESHOLD:
                similar_negatives += 1
                max_similarity = max(max_similarity, sim)

        if similar_negatives == 0:
            return False, 0.0

        # Also check positive feedback to balance
        pos_rows = conn.execute(
            """SELECT query_embedding FROM feedback_routing
               WHERE rating = 'positive' AND model_used LIKE '%ollama%'
               ORDER BY created_at DESC LIMIT 100"""
        ).fetchall()

        similar_positives = 0
        for row in pos_rows:
            if not row["query_embedding"]:
                continue
            stored_vec = np.frombuffer(row["query_embedding"], dtype=np.float32)
            sim = cosine_similarity(query_embedding, stored_vec)
            if sim >= _SIMILARITY_THRESHOLD:
                similar_positives += 1

        # Calculate penalty: negative_ratio * max_similarity
        total_similar = similar_negatives + similar_positives
        negative_ratio = similar_negatives / total_similar

        penalty = negative_ratio * max_similarity
        should_escalate = (
            similar_negatives >= _MIN_NEGATIVES and negative_ratio > 0.6
        )

        if should_escalate:
            log.info(
                "feedback_escalation_triggered",
                similar_negatives=similar_negatives,
                similar_positives=similar_positives,
                penalty=f"{penalty:.2f}",
                max_sim=f"{max_similarity:.2f}",
            )

        return should_escalate, penalty

    def get_stats(self) -> dict:
        """Get feedback loop statistics."""
        conn = get_connection()
        total = conn.execute(
            "SELECT COUNT(*) FROM feedback_routing"
        ).fetchone()[0]
        positives = conn.execute(
            "SELECT COUNT(*) FROM feedback_routing WHERE rating = 'positive'"
        ).fetchone()[0]
        negatives = conn.execute(
            "SELECT COUNT(*) FROM feedback_routing WHERE rating = 'negative'"
        ).fetchone()[0]
        local_negatives = conn.execute(
            "SELECT COUNT(*) FROM feedback_routing "
            "WHERE rating = 'negative' AND model_used LIKE '%ollama%'"
        ).fetchone()[0]

        return {
            "total_feedback": total,
            "positive": positives,
            "negative": negatives,
            "local_negatives": local_negatives,
        }
