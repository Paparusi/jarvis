"""Skill Router — Match user requests to the most relevant skill.

Flow:
1. Embed user request
2. Compute similarity against all skill descriptions
3. Top-K skills with score > threshold → inject into prompt
4. Log skill usage for metrics
"""

from __future__ import annotations

from src.memory.embeddings import cosine_similarity, get_embedding
from src.skills.loader import Skill, SkillLoader, SkillMetadata
from src.utils.logging import get_logger

log = get_logger("skills.router")

# Pre-computed embeddings cache for skill descriptions
_skill_embeddings: dict[str, object] = {}


class SkillRouter:
    """Route user requests to relevant skills."""

    def __init__(
        self,
        loader: SkillLoader,
        similarity_threshold: float = 0.35,
        max_skills: int = 2,
    ) -> None:
        self._loader = loader
        self._threshold = similarity_threshold
        self._max_skills = max_skills

    async def find_skills(self, user_message: str) -> list[Skill]:
        """Find the most relevant skills for a user message."""
        skills = self._loader._skills
        if not skills:
            return []

        query_embedding = await get_embedding(user_message)

        scored: list[tuple[float, Skill]] = []
        for name, skill in skills.items():
            # Get or compute skill embedding
            if name not in _skill_embeddings:
                # Combine name + description for better matching
                skill_text = f"{skill.metadata.name}: {skill.metadata.description}"
                _skill_embeddings[name] = await get_embedding(skill_text)

            sim = cosine_similarity(query_embedding, _skill_embeddings[name])

            # Boost by priority
            adjusted_score = sim * (0.7 + 0.3 * skill.metadata.priority)

            if adjusted_score >= self._threshold:
                scored.append((adjusted_score, skill))

        # Sort by score descending
        scored.sort(key=lambda x: -x[0])
        matched = [skill for _, skill in scored[:self._max_skills]]

        if matched:
            log.info(
                "skills_matched",
                query=user_message[:50],
                skills=[s.metadata.name for s in matched],
                scores=[f"{score:.3f}" for score, _ in scored[:self._max_skills]],
            )

        return matched

    def build_skill_context(self, skills: list[Skill]) -> str:
        """Build skill instructions to inject into the LLM prompt."""
        if not skills:
            return ""

        parts = ["## Kỹ năng đang sử dụng:\n"]
        for skill in skills:
            meta = skill.metadata
            emoji = f"{meta.emoji} " if meta.emoji else ""
            parts.append(f"### {emoji}{meta.name}")
            if skill.body:
                parts.append(skill.body)
            parts.append("")

        return "\n".join(parts)

    def update_usage(self, skill_name: str, success: bool = True) -> None:
        """Update skill usage metrics."""
        skill = self._loader.get_skill(skill_name)
        if skill:
            skill.metadata.usage_count += 1
            # Update success rate with exponential moving average
            alpha = 0.1
            skill.metadata.success_rate = (
                alpha * (1.0 if success else 0.0)
                + (1 - alpha) * skill.metadata.success_rate
            )
