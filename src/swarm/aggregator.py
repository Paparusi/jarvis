"""Result Aggregator — Intelligent merging of multi-agent outputs.

Replaces naive concatenation with:
- Quality scoring + weighted merging
- Semantic deduplication (detect near-identical outputs)
- Conflict detection between agent results
- Summary statistics for each merged result
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from src.swarm.decomposer import TaskPlan, Subtask
from src.utils.logging import get_logger

log = get_logger("swarm.aggregator")


@dataclass
class AggregatedResult:
    """Result of aggregation with metadata."""

    content: str
    quality_scores: dict[str, float]  # subtask_id → quality score
    conflicts: list[dict]  # detected conflicts
    duplicates_removed: int = 0
    total_subtasks: int = 0
    successful_subtasks: int = 0


class ResultAggregator:
    """Aggregate results from multiple agents intelligently."""

    def __init__(self, dedup_threshold: float = 0.75) -> None:
        self._dedup_threshold = dedup_threshold

    def aggregate(
        self,
        plan: TaskPlan,
        results: dict[str, str],
    ) -> AggregatedResult:
        """Aggregate subtask results into a unified response.

        Steps:
        1. Score quality of each result
        2. Detect and remove near-duplicates
        3. Detect conflicts between results
        4. Merge with quality indicators
        """
        quality_scores = {}
        valid_results: list[tuple[Subtask, str, float]] = []

        # Step 1: Score each result
        for subtask in plan.subtasks:
            content = results.get(subtask.id, "")
            if not content.strip():
                continue
            score = self._score_quality(content)
            quality_scores[subtask.id] = score
            valid_results.append((subtask, content, score))

        total = len(plan.subtasks)
        successful = len(valid_results)

        if not valid_results:
            return AggregatedResult(
                content="Không có kết quả từ các agents.",
                quality_scores=quality_scores,
                conflicts=[],
                total_subtasks=total,
                successful_subtasks=0,
            )

        # Single result — return directly
        if len(valid_results) == 1:
            st, content, score = valid_results[0]
            return AggregatedResult(
                content=content,
                quality_scores=quality_scores,
                conflicts=[],
                total_subtasks=total,
                successful_subtasks=1,
            )

        # Step 2: Deduplicate
        deduped, removed = self._deduplicate(valid_results)

        # Step 3: Detect conflicts
        conflicts = self._detect_conflicts(deduped)

        # Step 4: Merge
        merged = self._merge(deduped, plan, conflicts)

        return AggregatedResult(
            content=merged,
            quality_scores=quality_scores,
            conflicts=conflicts,
            duplicates_removed=removed,
            total_subtasks=total,
            successful_subtasks=successful,
        )

    def _score_quality(self, content: str) -> float:
        """Score result quality from 0.0 to 1.0."""
        if not content or not content.strip():
            return 0.0

        score = 0.4  # baseline

        # Length (more content generally better for research/analysis)
        words = len(content.split())
        if words > 30:
            score += 0.1
        if words > 100:
            score += 0.1

        # Structure indicators
        structure_signals = ["- ", "* ", "1.", "##", "```", "**", "|"]
        structure_count = sum(1 for s in structure_signals if s in content)
        score += min(0.15, structure_count * 0.05)

        # Error indicators (reduce score)
        error_words = ["error", "failed", "không thể", "lỗi", "exception", "timeout"]
        if any(w in content.lower() for w in error_words):
            score -= 0.2

        # Failed subtask markers
        if content.startswith("[Subtask") and "failed" in content:
            score = 0.1

        return max(0.0, min(1.0, score))

    def _deduplicate(
        self,
        results: list[tuple[Subtask, str, float]],
    ) -> tuple[list[tuple[Subtask, str, float]], int]:
        """Remove near-duplicate results. Keep higher-quality version."""
        if len(results) <= 1:
            return results, 0

        kept: list[tuple[Subtask, str, float]] = []
        removed = 0

        for st, content, score in results:
            is_dup = False
            for i, (kept_st, kept_content, kept_score) in enumerate(kept):
                similarity = self._text_similarity(content, kept_content)
                if similarity >= self._dedup_threshold:
                    is_dup = True
                    # Keep the higher-quality one
                    if score > kept_score:
                        kept[i] = (st, content, score)
                        log.info("dedup_replaced",
                                 removed=kept_st.id, kept=st.id,
                                 similarity=f"{similarity:.2f}")
                    else:
                        log.info("dedup_removed",
                                 removed=st.id, kept=kept_st.id,
                                 similarity=f"{similarity:.2f}")
                    removed += 1
                    break

            if not is_dup:
                kept.append((st, content, score))

        return kept, removed

    def _detect_conflicts(
        self,
        results: list[tuple[Subtask, str, float]],
    ) -> list[dict]:
        """Detect potential conflicts between results.

        Uses heuristic: if two results discuss same topic but contain
        opposing signals (yes/no, positive/negative, etc.).
        """
        conflicts = []

        # Opposing signal pairs
        opposites = [
            ("yes", "no"), ("true", "false"), ("correct", "incorrect"),
            ("safe", "unsafe"), ("valid", "invalid"), ("success", "failure"),
            ("đúng", "sai"), ("có", "không"), ("tốt", "xấu"),
        ]

        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                st_a, content_a, _ = results[i]
                st_b, content_b, _ = results[j]

                content_a_lower = content_a.lower()
                content_b_lower = content_b.lower()

                for pos, neg in opposites:
                    a_has_pos = pos in content_a_lower
                    a_has_neg = neg in content_a_lower
                    b_has_pos = pos in content_b_lower
                    b_has_neg = neg in content_b_lower

                    # Agent A says positive, Agent B says negative (or vice versa)
                    if (a_has_pos and b_has_neg and not a_has_neg) or \
                       (a_has_neg and b_has_pos and not b_has_neg):
                        conflicts.append({
                            "agents": [st_a.id, st_b.id],
                            "signal": f"{pos}/{neg}",
                            "description": (
                                f"Agent {st_a.id} and {st_b.id} may disagree "
                                f"(opposing signals: {pos}/{neg})"
                            ),
                        })
                        break  # One conflict per pair is enough

        return conflicts

    def _merge(
        self,
        results: list[tuple[Subtask, str, float]],
        plan: TaskPlan,
        conflicts: list[dict],
    ) -> str:
        """Merge results with quality indicators and conflict notes."""
        parts = []

        # Sort by priority (highest first), then quality
        sorted_results = sorted(
            results,
            key=lambda x: (x[0].priority, x[2]),
            reverse=True,
        )

        for subtask, content, quality in sorted_results:
            indicator = "✅" if quality >= 0.5 else "⚠️"
            header = f"{indicator} **{subtask.description[:60]}**"
            parts.append(f"{header}\n{content}")

        merged = "\n\n---\n\n".join(parts)

        # Add conflict warnings if any
        if conflicts:
            conflict_notes = "\n".join(
                f"  ⚠️ {c['description']}" for c in conflicts
            )
            merged += f"\n\n---\n**Lưu ý:** Phát hiện mâu thuẫn giữa các kết quả:\n{conflict_notes}"

        return merged

    @staticmethod
    def _text_similarity(text_a: str, text_b: str) -> float:
        """Compute text similarity ratio (0.0 to 1.0).

        Uses SequenceMatcher on first 500 chars for performance.
        """
        # Truncate for performance
        a = text_a[:500].lower().strip()
        b = text_b[:500].lower().strip()
        return SequenceMatcher(None, a, b).ratio()
