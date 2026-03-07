"""Semantic Memory — Long-term knowledge & facts.

Lưu trữ facts, user preferences, kiến thức — tồn tại vĩnh viễn.
Ví dụ: "User thích Python hơn Java", "User tên là X", "XAUUSD đang ở 2645".

Retrieval: Hybrid BM25 + Dense + RRF fusion with:
- Recency boost (newer memories rank higher)
- Importance weighting
- Access frequency boost
- Diversity filtering (no duplicate content)
"""

from __future__ import annotations

import time
from uuid import uuid4

import numpy as np

from src.memory.embeddings import cosine_similarity, get_embedding
from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("memory.semantic")

# Weights for scoring signals
_VECTOR_WEIGHT = 0.50      # Semantic similarity
_BM25_WEIGHT = 0.20        # Keyword match
_IMPORTANCE_WEIGHT = 0.15  # User-assigned importance
_RECENCY_WEIGHT = 0.10     # How recently stored
_ACCESS_WEIGHT = 0.05      # How often accessed

# Recency decay: memory older than this (seconds) gets no recency boost
_RECENCY_WINDOW = 7 * 24 * 3600  # 7 days


class SemanticMemory:
    """Long-term factual memory with hybrid vector + BM25 search."""

    async def remember(
        self,
        content: str,
        category: str = "general",
        source: str = "conversation",
        importance: float = 0.5,
    ) -> str:
        """Store a fact/knowledge in semantic memory.

        Deduplicates: if very similar content exists (>0.92 similarity),
        updates importance instead of creating duplicate.
        """
        embedding = await get_embedding(content)

        # Dedup check: search for very similar existing memories
        existing = await self._find_duplicate(content, embedding, threshold=0.92)
        if existing:
            conn = get_connection()
            # Update importance if new is higher
            new_importance = max(existing["importance"], importance)
            conn.execute(
                "UPDATE semantic_memories SET importance = ?, access_count = access_count + 1 WHERE id = ?",
                (new_importance, existing["id"]),
            )
            conn.commit()
            log.info("memory_deduped", existing_id=existing["id"][:8],
                     content=content[:60])
            return existing["id"]

        memory_id = str(uuid4())
        conn = get_connection()
        conn.execute(
            """INSERT INTO semantic_memories (id, content, category, source, embedding, importance)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (memory_id, content, category, source, embedding.tobytes(), importance),
        )
        # Update FTS index — use explicit rowid from the insert
        try:
            rowid = conn.execute(
                "SELECT rowid FROM semantic_memories WHERE id = ?", (memory_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT OR REPLACE INTO semantic_fts (rowid, content, category) VALUES (?, ?, ?)",
                (rowid, content, category),
            )
        except Exception as e:
            log.warning("fts_insert_failed", error=str(e))
        conn.commit()

        log.info("memory_stored", id=memory_id[:8], category=category,
                 content=content[:80])
        return memory_id

    async def _find_duplicate(
        self, content: str, embedding: np.ndarray, threshold: float = 0.92
    ) -> dict | None:
        """Find an existing memory with very similar content."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, content, importance, embedding FROM semantic_memories"
        ).fetchall()

        for row in rows:
            if row["embedding"]:
                stored_vec = np.frombuffer(row["embedding"], dtype=np.float32)
                sim = cosine_similarity(embedding, stored_vec)
                if sim >= threshold:
                    return {
                        "id": row["id"],
                        "content": row["content"],
                        "importance": row["importance"],
                    }
        return None

    async def search(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.25,
    ) -> list[dict]:
        """Search memories using hybrid: vector + BM25 + multi-signal ranking.

        Scoring combines:
        - Vector similarity (semantic match)
        - BM25 score (keyword match)
        - Importance weight
        - Recency boost (newer memories score higher)
        - Access frequency (frequently accessed = more relevant)
        - Diversity filtering (removes near-duplicate results)
        """
        conn = get_connection()

        # 1. Vector search (Dense)
        query_embedding = await get_embedding(query)
        all_rows = conn.execute(
            "SELECT id, content, category, source, embedding, importance, "
            "access_count, created_at FROM semantic_memories"
        ).fetchall()

        vector_results = []
        now_ts = time.time()

        for row in all_rows:
            if not row["embedding"]:
                continue
            stored_vec = np.frombuffer(row["embedding"], dtype=np.float32)
            sim = cosine_similarity(query_embedding, stored_vec)
            if sim >= min_similarity:
                # Calculate recency score (1.0 for new, decays to 0.0)
                created = row["created_at"] or ""
                recency_score = self._recency_score(created, now_ts)

                # Access frequency score (log-scaled, capped at 1.0)
                access_count = row["access_count"] or 0
                access_score = min(1.0, np.log1p(access_count) / 3.0)

                # Combined weighted score
                combined = (
                    _VECTOR_WEIGHT * sim
                    + _IMPORTANCE_WEIGHT * (row["importance"] or 0.5)
                    + _RECENCY_WEIGHT * recency_score
                    + _ACCESS_WEIGHT * access_score
                )

                vector_results.append({
                    "id": row["id"],
                    "content": row["content"],
                    "category": row["category"],
                    "source": row["source"],
                    "importance": row["importance"],
                    "vector_sim": sim,
                    "combined_score": combined,
                    "embedding": stored_vec,
                })

        # 2. BM25 text search (Sparse) — with fallback on failure
        bm25_scores: dict[str, float] = {}
        try:
            fts_rows = conn.execute(
                "SELECT rowid, content, category FROM semantic_fts "
                "WHERE semantic_fts MATCH ? LIMIT ?",
                (query, top_k * 3),
            ).fetchall()
            for i, row in enumerate(fts_rows):
                bm25_scores[row["content"]] = 1.0 / (i + 1)
        except Exception as e:
            log.debug("fts_search_fallback", error=str(e), query=query[:50])

        # 3. Merge BM25 scores into vector results
        for result in vector_results:
            bm25 = bm25_scores.get(result["content"], 0.0)
            result["combined_score"] += _BM25_WEIGHT * bm25

        # 4. Sort by combined score
        vector_results.sort(key=lambda x: x["combined_score"], reverse=True)

        # 5. Diversity filtering (remove near-duplicate results)
        diverse_results = self._diversity_filter(vector_results, threshold=0.85)

        # 6. Update access counts for returned results
        result_ids = [r["id"] for r in diverse_results[:top_k] if "id" in r]
        if result_ids:
            placeholders = ",".join("?" * len(result_ids))
            conn.execute(
                f"UPDATE semantic_memories SET access_count = access_count + 1 "
                f"WHERE id IN ({placeholders})",
                result_ids,
            )
            conn.commit()

        # Clean up: remove embedding from output
        for r in diverse_results:
            r.pop("embedding", None)

        return diverse_results[:top_k]

    @staticmethod
    def _recency_score(created_at: str, now_ts: float) -> float:
        """Calculate recency score: 1.0 for just created, 0.0 for old."""
        if not created_at:
            return 0.5

        try:
            from datetime import datetime
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_seconds = now_ts - dt.timestamp()
            if age_seconds <= 0:
                return 1.0
            # Linear decay over _RECENCY_WINDOW
            return max(0.0, 1.0 - (age_seconds / _RECENCY_WINDOW))
        except (ValueError, TypeError):
            return 0.5

    @staticmethod
    def _diversity_filter(
        results: list[dict], threshold: float = 0.85
    ) -> list[dict]:
        """Remove near-duplicate results to ensure diversity."""
        if not results:
            return []

        filtered = [results[0]]
        for candidate in results[1:]:
            is_duplicate = False
            cand_emb = candidate.get("embedding")
            if cand_emb is not None:
                for kept in filtered:
                    kept_emb = kept.get("embedding")
                    if kept_emb is not None:
                        sim = cosine_similarity(cand_emb, kept_emb)
                        if sim >= threshold:
                            is_duplicate = True
                            break
            if not is_duplicate:
                filtered.append(candidate)

        return filtered

    def get_all(self, category: str | None = None, limit: int = 100) -> list[dict]:
        """Get all memories, optionally filtered by category."""
        conn = get_connection()
        if category:
            rows = conn.execute(
                "SELECT id, content, category, source, importance, access_count, created_at "
                "FROM semantic_memories WHERE category = ? ORDER BY importance DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, content, category, source, importance, access_count, created_at "
                "FROM semantic_memories ORDER BY importance DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        conn = get_connection()
        row = conn.execute("SELECT COUNT(*) as cnt FROM semantic_memories").fetchone()
        return row["cnt"]

    def update_importance(self, memory_id: str, importance: float) -> bool:
        """Update the importance score of a memory."""
        conn = get_connection()
        result = conn.execute(
            "UPDATE semantic_memories SET importance = ? WHERE id = ?",
            (importance, memory_id),
        )
        conn.commit()
        return result.rowcount > 0

    def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID."""
        conn = get_connection()
        result = conn.execute(
            "DELETE FROM semantic_memories WHERE id = ?", (memory_id,)
        )
        conn.commit()
        return result.rowcount > 0
