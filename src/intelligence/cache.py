"""Semantic Cache — Cache LLM responses based on embedding similarity.

Nếu user hỏi câu tương tự (cosine similarity >= threshold) → trả kết quả cached.
Tiết kiệm API cost + giảm latency đáng kể.
"""

from __future__ import annotations

import time

import numpy as np

from src.memory.embeddings import cosine_similarity, get_embedding
from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("intelligence.cache")


class SemanticCache:
    """Embedding-based semantic cache for LLM responses.

    Optimized for production:
    - Exact text match short-circuit (skip embedding computation)
    - Only scan non-expired entries (TTL filter in SQL)
    - Max cache size with LRU eviction
    - Periodic cleanup on put()
    """

    MAX_ENTRIES = 500  # Limit cache size
    CLEANUP_INTERVAL = 100  # Clean every N puts

    def __init__(self, similarity_threshold: float = 0.92, ttl_seconds: int = 86400) -> None:
        self._threshold = similarity_threshold
        self._ttl = ttl_seconds
        self._put_count = 0
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS llm_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_text TEXT NOT NULL,
                query_embedding BLOB NOT NULL,
                response_text TEXT NOT NULL,
                model_used TEXT NOT NULL,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                hit_count INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                last_hit_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_llm_cache_created ON llm_cache(created_at);
        """)
        conn.commit()

    async def get(self, query: str) -> dict | None:
        """Look up cache for a semantically similar query.

        Returns dict with response_text, model_used if found, else None.
        """
        conn = get_connection()
        now = time.time()
        cutoff = now - self._ttl

        # 1. Exact text match — skip embedding entirely
        exact = conn.execute(
            "SELECT id, query_text, response_text, model_used, tokens_in, tokens_out "
            "FROM llm_cache WHERE query_text = ? AND created_at > ? LIMIT 1",
            (query, cutoff),
        ).fetchone()
        if exact:
            conn.execute(
                "UPDATE llm_cache SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
                (now, exact["id"]),
            )
            conn.commit()
            log.info("cache_hit", similarity="1.000", original_query=query[:60])
            return {
                "response_text": exact["response_text"],
                "model_used": exact["model_used"],
                "tokens_in": exact["tokens_in"],
                "tokens_out": exact["tokens_out"],
                "cache_hit": True,
                "similarity": 1.0,
            }

        # 2. Semantic similarity search — only non-expired entries
        query_embedding = await get_embedding(query)

        rows = conn.execute(
            "SELECT id, query_text, query_embedding, response_text, model_used, "
            "tokens_in, tokens_out FROM llm_cache WHERE created_at > ? "
            "ORDER BY created_at DESC LIMIT ?",
            (cutoff, self.MAX_ENTRIES),
        ).fetchall()

        best_match = None
        best_sim = 0.0

        for row in rows:
            stored_vec = np.frombuffer(row["query_embedding"], dtype=np.float32)
            sim = cosine_similarity(query_embedding, stored_vec)

            if sim >= self._threshold and sim > best_sim:
                best_sim = sim
                best_match = row

        if best_match:
            conn.execute(
                "UPDATE llm_cache SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
                (now, best_match["id"]),
            )
            conn.commit()
            log.info(
                "cache_hit",
                similarity=f"{best_sim:.3f}",
                original_query=best_match["query_text"][:60],
            )
            return {
                "response_text": best_match["response_text"],
                "model_used": best_match["model_used"],
                "tokens_in": best_match["tokens_in"],
                "tokens_out": best_match["tokens_out"],
                "cache_hit": True,
                "similarity": best_sim,
            }

        return None

    async def put(
        self,
        query: str,
        response_text: str,
        model_used: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        """Store a query-response pair in cache."""
        query_embedding = await get_embedding(query)
        conn = get_connection()
        conn.execute(
            "INSERT INTO llm_cache (query_text, query_embedding, response_text, "
            "model_used, tokens_in, tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (query, query_embedding.tobytes(), response_text, model_used,
             tokens_in, tokens_out, time.time()),
        )
        conn.commit()

        # Periodic cleanup
        self._put_count += 1
        if self._put_count % self.CLEANUP_INTERVAL == 0:
            self._cleanup()

    def _cleanup(self) -> None:
        """Remove expired entries and enforce max size."""
        conn = get_connection()
        cutoff = time.time() - self._ttl
        conn.execute("DELETE FROM llm_cache WHERE created_at < ?", (cutoff,))

        # LRU eviction if over max size
        count = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
        if count > self.MAX_ENTRIES:
            conn.execute(
                "DELETE FROM llm_cache WHERE id IN ("
                "SELECT id FROM llm_cache ORDER BY COALESCE(last_hit_at, created_at) ASC "
                "LIMIT ?)",
                (count - self.MAX_ENTRIES,),
            )
        conn.commit()

    def clear_expired(self) -> int:
        """Remove expired cache entries. Returns number removed."""
        conn = get_connection()
        cutoff = time.time() - self._ttl
        cursor = conn.execute("DELETE FROM llm_cache WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount

    def clear_all(self) -> int:
        """Remove ALL cache entries. Returns number removed."""
        conn = get_connection()
        cursor = conn.execute("DELETE FROM llm_cache")
        conn.commit()
        return cursor.rowcount

    def get_stats(self) -> dict:
        conn = get_connection()
        now = time.time()
        cutoff = now - self._ttl
        row = conn.execute(
            "SELECT COUNT(*) as total, COALESCE(SUM(hit_count), 0) as total_hits "
            "FROM llm_cache WHERE created_at > ?", (cutoff,)
        ).fetchone()
        return {"cached_entries": row["total"], "total_hits": row["total_hits"]}
