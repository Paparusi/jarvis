"""Memory Consolidator — Clean and strengthen memories during Dreamtime.

Runs during idle periods to:
1. Deduplicate similar semantic memories
2. Strengthen frequently accessed memories (increase importance)
3. Decay rarely accessed memories
4. Cluster related memories into groups
5. Generate summary memories from clusters
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta

from src.memory.embeddings import cosine_similarity, get_embedding
from src.memory.semantic import SemanticMemory
from src.utils.logging import get_logger

log = get_logger("dreamtime.consolidator")


class MemoryConsolidator:
    """Consolidate and optimize semantic memories."""

    def __init__(
        self,
        semantic_memory: SemanticMemory,
        dedup_threshold: float = 0.90,
        decay_days: int = 30,
        min_importance: float = 0.1,
    ) -> None:
        self._memory = semantic_memory
        self._dedup_threshold = dedup_threshold
        self._decay_days = decay_days
        self._min_importance = min_importance

    async def consolidate(self) -> dict:
        """Run full memory consolidation cycle.

        Returns stats about what was done.
        """
        stats = {
            "deduplicated": 0,
            "strengthened": 0,
            "decayed": 0,
            "total_before": 0,
            "total_after": 0,
        }

        all_memories = self._memory.get_all(limit=1000)
        stats["total_before"] = len(all_memories)

        if len(all_memories) < 2:
            stats["total_after"] = len(all_memories)
            return stats

        # 1. Deduplicate similar memories
        dedup_count = await self._deduplicate(all_memories)
        stats["deduplicated"] = dedup_count

        # 2. Strengthen frequently accessed memories
        strengthen_count = self._strengthen_frequent(all_memories)
        stats["strengthened"] = strengthen_count

        # 3. Decay old, rarely accessed memories
        decay_count = self._decay_old(all_memories)
        stats["decayed"] = decay_count

        # 4. Cluster related memories
        remaining = self._memory.get_all(limit=1000)
        clusters = await self._cluster_memories(remaining)
        stats["clusters_found"] = len(clusters)

        # 5. Generate summary memories for large clusters
        summaries_created = await self._generate_cluster_summaries(clusters)
        stats["summaries_created"] = summaries_created

        # Refresh count
        final = self._memory.get_all(limit=1000)
        stats["total_after"] = len(final)

        log.info("consolidation_complete", **stats)
        return stats

    async def _deduplicate(self, memories: list[dict]) -> int:
        """Remove near-duplicate memories, keeping the one with higher importance."""
        if len(memories) < 2:
            return 0

        # Compute embeddings for all memories
        embeddings = []
        for mem in memories:
            emb = await get_embedding(mem["content"])
            embeddings.append(emb)

        to_delete = set()
        count = 0

        for i in range(len(memories)):
            if i in to_delete:
                continue
            for j in range(i + 1, len(memories)):
                if j in to_delete:
                    continue

                sim = cosine_similarity(embeddings[i], embeddings[j])
                if sim >= self._dedup_threshold:
                    # Keep the one with higher importance/access_count
                    imp_i = memories[i].get("importance", 0.5)
                    imp_j = memories[j].get("importance", 0.5)
                    acc_i = memories[i].get("access_count", 0)
                    acc_j = memories[j].get("access_count", 0)

                    score_i = imp_i + acc_i * 0.01
                    score_j = imp_j + acc_j * 0.01

                    if score_i >= score_j:
                        to_delete.add(j)
                    else:
                        to_delete.add(i)
                    count += 1

        # Delete duplicates
        for idx in to_delete:
            mem_id = memories[idx].get("id")
            if mem_id:
                self._memory.delete(mem_id)

        return count

    def _strengthen_frequent(self, memories: list[dict]) -> int:
        """Increase importance of frequently accessed memories."""
        count = 0
        for mem in memories:
            access = mem.get("access_count", 0)
            importance = mem.get("importance", 0.5)

            # If accessed 5+ times and importance < 0.9, boost it
            if access >= 5 and importance < 0.9:
                new_importance = min(0.95, importance + 0.05)
                mem_id = mem.get("id")
                if mem_id:
                    self._memory.update_importance(mem_id, new_importance)
                    count += 1

        return count

    async def _cluster_memories(
        self, memories: list[dict], cluster_threshold: float = 0.75,
    ) -> list[list[dict]]:
        """Group related memories into clusters using greedy agglomerative approach.

        Uses cosine similarity on embeddings. Returns clusters with 2+ memories.
        """
        if len(memories) < 3:
            return []

        # Compute embeddings
        embeddings = []
        for mem in memories:
            emb = await get_embedding(mem["content"])
            embeddings.append(emb)

        # Greedy clustering: assign each memory to its most similar cluster
        assigned = set()
        clusters: list[list[int]] = []

        for i in range(len(memories)):
            if i in assigned:
                continue

            cluster = [i]
            assigned.add(i)

            for j in range(i + 1, len(memories)):
                if j in assigned:
                    continue
                sim = cosine_similarity(embeddings[i], embeddings[j])
                if sim >= cluster_threshold:
                    cluster.append(j)
                    assigned.add(j)

            if len(cluster) >= 2:
                clusters.append(cluster)

        # Convert index clusters to memory clusters
        result = []
        for idx_cluster in clusters:
            mem_cluster = [memories[idx] for idx in idx_cluster]
            result.append(mem_cluster)

        log.info("memory_clusters_found", count=len(result),
                 sizes=[len(c) for c in result])
        return result

    async def _generate_cluster_summaries(
        self, clusters: list[list[dict]],
    ) -> int:
        """Generate summary memories for clusters with 3+ items.

        The summary combines key information from cluster members.
        """
        created = 0
        for cluster in clusters:
            if len(cluster) < 3:
                continue

            # Extract contents
            contents = [m["content"] for m in cluster]

            # Simple extractive summary: take first sentence of each, dedup
            summary_parts = []
            seen = set()
            for content in contents:
                # Take first sentence or first 100 chars
                first_sentence = content.split(".")[0].strip()
                if first_sentence and first_sentence.lower() not in seen:
                    seen.add(first_sentence.lower())
                    summary_parts.append(first_sentence)

            if len(summary_parts) < 2:
                continue

            summary = "[Tổng hợp] " + "; ".join(summary_parts[:5])

            # Check if this summary already exists
            existing = await self._memory.search(summary[:50], top_k=1)
            if existing and existing[0].get("score", 0) > 0.90:
                continue

            # Store summary with high importance
            try:
                await self._memory.remember(
                    content=summary,
                    category="summary",
                    importance=0.8,
                )
                created += 1
                log.info("cluster_summary_created", items=len(cluster),
                         summary_preview=summary[:80])
            except Exception as e:
                log.warning("cluster_summary_failed", error=str(e))

        return created

    def _decay_old(self, memories: list[dict]) -> int:
        """Decay importance of old, rarely accessed memories."""
        count = 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._decay_days)

        for mem in memories:
            created_str = mem.get("created_at", "")
            access = mem.get("access_count", 0)
            importance = mem.get("importance", 0.5)

            # Skip high-importance memories
            if importance >= 0.9:
                continue

            try:
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                # Ensure timezone-aware for comparison
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue

            # Old + rarely accessed → decay
            if created < cutoff and access < 3 and importance > self._min_importance:
                new_importance = max(self._min_importance, importance - 0.1)
                mem_id = mem.get("id")
                if mem_id:
                    self._memory.update_importance(mem_id, new_importance)
                    count += 1

        return count
