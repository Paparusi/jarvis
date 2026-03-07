"""Skill Registry — Centralized skill management with semantic search and metrics.

Manages skill lifecycle:
- Registration with embedding-based indexing
- Semantic search for skill discovery
- Usage tracking and performance metrics
- Persistent metrics storage (survives restart)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.memory.embeddings import cosine_similarity, get_embedding
from src.skills.loader import Skill, SkillLoader, SkillMetadata
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("skills.registry")

# In-memory embedding cache for skill descriptions
_embedding_cache: dict[str, object] = {}


class SkillRegistry:
    """Enhanced skill registry with semantic search, metrics, and persistence."""

    def __init__(self, loader: SkillLoader) -> None:
        self._loader = loader
        self._metrics_file = get_project_root() / "data" / "skill_metrics.json"
        self._metrics: dict[str, dict] = self._load_metrics()

    # --- Core Operations ---

    def get_skill(self, name: str) -> Skill | None:
        return self._loader.get_skill(name)

    def get_all(self) -> list[Skill]:
        return list(self._loader._skills.values())

    def get_all_metadata(self) -> list[SkillMetadata]:
        return self._loader.get_all_metadata()

    def reload(self) -> int:
        """Reload all skills from disk and apply persisted metrics."""
        skills = self._loader.load_all()
        self._apply_metrics()
        return len(skills)

    # --- Semantic Search ---

    async def search(
        self,
        query: str,
        threshold: float = 0.35,
        max_results: int = 3,
    ) -> list[tuple[float, Skill]]:
        """Search skills by semantic similarity to query."""
        skills = self._loader._skills
        if not skills:
            return []

        query_embedding = await get_embedding(query)
        scored: list[tuple[float, Skill]] = []

        for name, skill in skills.items():
            if name not in _embedding_cache:
                skill_text = f"{skill.metadata.name}: {skill.metadata.description}"
                _embedding_cache[name] = await get_embedding(skill_text)

            sim = cosine_similarity(query_embedding, _embedding_cache[name])
            # Boost by priority and success rate
            adjusted = sim * (0.6 + 0.2 * skill.metadata.priority + 0.2 * skill.metadata.success_rate)

            if adjusted >= threshold:
                scored.append((adjusted, skill))

        scored.sort(key=lambda x: -x[0])
        return scored[:max_results]

    # --- Usage Tracking ---

    def record_usage(self, skill_name: str, success: bool = True) -> None:
        """Record skill usage and update metrics."""
        if skill_name not in self._metrics:
            self._metrics[skill_name] = {
                "usage_count": 0,
                "success_count": 0,
                "fail_count": 0,
                "last_used": None,
            }

        m = self._metrics[skill_name]
        m["usage_count"] += 1
        if success:
            m["success_count"] += 1
        else:
            m["fail_count"] += 1
        m["last_used"] = datetime.now(timezone.utc).isoformat()

        # Update in-memory skill metadata
        skill = self._loader.get_skill(skill_name)
        if skill:
            skill.metadata.usage_count = m["usage_count"]
            total = m["success_count"] + m["fail_count"]
            if total > 0:
                skill.metadata.success_rate = m["success_count"] / total

        self._save_metrics()

    def get_top_skills(self, limit: int = 5) -> list[tuple[str, dict]]:
        """Get most-used skills sorted by usage count."""
        sorted_skills = sorted(
            self._metrics.items(),
            key=lambda x: x[1]["usage_count"],
            reverse=True,
        )
        return sorted_skills[:limit]

    def get_unused_skills(self, days: int = 30) -> list[str]:
        """Get skills not used in the last N days."""
        cutoff = datetime.now(timezone.utc)
        unused = []
        for skill in self._loader._skills.values():
            name = skill.metadata.name
            metrics = self._metrics.get(name)
            if not metrics or not metrics["last_used"]:
                unused.append(name)
                continue
            last = datetime.fromisoformat(metrics["last_used"])
            if (cutoff - last).days > days:
                unused.append(name)
        return unused

    # --- Stats ---

    def get_stats(self) -> dict:
        """Get registry statistics."""
        total_skills = len(self._loader._skills)
        total_usage = sum(m["usage_count"] for m in self._metrics.values())
        avg_success = 0.0
        if self._metrics:
            rates = []
            for m in self._metrics.values():
                total = m["success_count"] + m["fail_count"]
                if total > 0:
                    rates.append(m["success_count"] / total)
            avg_success = sum(rates) / len(rates) if rates else 0.0

        return {
            "total_skills": total_skills,
            "total_usage": total_usage,
            "avg_success_rate": round(avg_success, 2),
            "tracked_skills": len(self._metrics),
        }

    # --- Persistence ---

    def _load_metrics(self) -> dict[str, dict]:
        """Load persisted skill metrics from disk."""
        if not self._metrics_file.exists():
            return {}
        try:
            with open(self._metrics_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_metrics(self) -> None:
        """Save skill metrics to disk."""
        self._metrics_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._metrics_file, "w", encoding="utf-8") as f:
            json.dump(self._metrics, f, ensure_ascii=False, indent=2)

    def _apply_metrics(self) -> None:
        """Apply persisted metrics to loaded skills."""
        for name, metrics in self._metrics.items():
            skill = self._loader.get_skill(name)
            if skill:
                skill.metadata.usage_count = metrics["usage_count"]
                total = metrics["success_count"] + metrics["fail_count"]
                if total > 0:
                    skill.metadata.success_rate = metrics["success_count"] / total
