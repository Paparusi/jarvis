"""Skill Evolver — Automated skill optimization, merging, and pruning.

During Dreamtime, the evolver:
1. Analyzes skill performance metrics
2. Optimizes underperforming skills (GEPA) using LLM analysis
3. Merges semantically similar skills
4. Prunes unused skills to .archive/
5. Maintains version history
"""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import litellm

from src.memory.embeddings import cosine_similarity, get_embedding
from src.skills.loader import Skill, SkillLoader
from src.skills.registry import SkillRegistry
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("skills.evolver")

# Thresholds
_MERGE_SIMILARITY = 0.90     # Cosine similarity to consider merging
_PRUNE_UNUSED_DAYS = 30       # Days without use before archiving
_UNDERPERFORM_RATE = 0.70     # Success rate below this = underperforming
_MIN_USAGE_FOR_EVAL = 5       # Minimum uses before evaluating performance


class EvolutionReport:
    """Report of what the evolver did during a cycle."""

    def __init__(self) -> None:
        self.optimized: list[str] = []       # Skills that were optimized
        self.merged: list[tuple[str, str, str]] = []  # (skill_a, skill_b, new_name)
        self.pruned: list[str] = []          # Skills archived
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "optimized": self.optimized,
            "merged": [{"a": a, "b": b, "merged_as": c} for a, b, c in self.merged],
            "pruned": self.pruned,
            "errors": self.errors,
            "total_actions": len(self.optimized) + len(self.merged) + len(self.pruned),
        }


class SkillEvolver:
    """Automated skill evolution engine."""

    def __init__(self, registry: SkillRegistry, loader: SkillLoader) -> None:
        self._registry = registry
        self._loader = loader
        self._archive_dir = get_project_root() / "workspace" / "skills" / ".archive"
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._reports_dir = get_project_root() / "data" / "evolution"
        self._reports_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> EvolutionReport:
        """Run full evolution cycle. Called during Dreamtime."""
        report = EvolutionReport()
        log.info("evolution_cycle_start")

        try:
            # 1. Optimize underperforming skills
            await self._optimize_underperformers(report)

            # 2. Merge similar skills
            await self._merge_similar(report)

            # 3. Prune unused skills
            self._prune_unused(report)

        except Exception as e:
            report.errors.append(f"Evolution cycle error: {e}")
            log.error("evolution_cycle_error", error=str(e))

        # Save report
        self._save_report(report)

        log.info("evolution_cycle_complete",
                 optimized=len(report.optimized),
                 merged=len(report.merged),
                 pruned=len(report.pruned),
                 errors=len(report.errors))

        return report

    # --- 1. GEPA Optimization ---

    async def _optimize_underperformers(self, report: EvolutionReport) -> None:
        """Find underperforming skills and improve them."""
        all_skills = self._registry.get_all()

        for skill in all_skills:
            meta = skill.metadata
            if meta.usage_count < _MIN_USAGE_FOR_EVAL:
                continue
            if meta.success_rate >= _UNDERPERFORM_RATE:
                continue

            log.info("optimizing_skill", name=meta.name,
                     success_rate=meta.success_rate, usage=meta.usage_count)

            try:
                improved = await self._gepa_optimize(skill)
                if improved:
                    report.optimized.append(meta.name)
                    log.info("skill_optimized", name=meta.name)
            except Exception as e:
                report.errors.append(f"Optimize {meta.name}: {e}")
                log.warning("skill_optimize_error", name=meta.name, error=str(e))

    async def _gepa_optimize(self, skill: Skill) -> bool:
        """GEPA — Generate, Evaluate, Promote, Archive.

        For underperforming skills:
        1. Archive current version
        2. Analyze failure patterns from interaction logs
        3. Use LLM to generate improved SKILL.md
        4. Bump version and write updated skill
        """
        if not skill.metadata.path:
            return False

        skill_file = skill.metadata.path
        if not skill_file.exists():
            return False

        # Archive current version
        self._archive_version(skill)

        # Read current SKILL.md
        content = skill_file.read_text(encoding="utf-8")

        # Gather failure context from interaction logs
        failure_examples = self._gather_failure_context(skill.metadata.name)

        # Use LLM to generate improved skill
        try:
            improved_body = await self._llm_improve_skill(
                skill_name=skill.metadata.name,
                current_content=content,
                success_rate=skill.metadata.success_rate,
                usage_count=skill.metadata.usage_count,
                failure_examples=failure_examples,
            )

            if not improved_body:
                log.warning("gepa_llm_no_improvement", skill=skill.metadata.name)
                # Fall through to fallback notes below
                raise ValueError("LLM returned no improvement")

            # Bump version
            version_match = re.search(r'version:\s*"?(\d+)\.(\d+)\.(\d+)"?', improved_body)
            if version_match:
                major = int(version_match.group(1))
                minor = int(version_match.group(2))
                patch = int(version_match.group(3))
                new_version = f"{major}.{minor + 1}.0"
                improved_body = improved_body.replace(
                    version_match.group(0),
                    f'version: "{new_version}"'
                )

            skill_file.write_text(improved_body, encoding="utf-8")
            log.info("gepa_skill_improved", skill=skill.metadata.name)
            return True

        except Exception as e:
            log.error("gepa_llm_error", skill=skill.metadata.name, error=str(e))
            # Fallback: append optimization notes without LLM
            optimization_notes = (
                "\n\n## Optimization Notes\n"
                f"- Previous success rate: {skill.metadata.success_rate:.0%}\n"
                f"- Total uses: {skill.metadata.usage_count}\n"
                f"- Optimized: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
                f"- LLM optimization failed: {str(e)[:100]}\n"
            )
            if "## Optimization Notes" not in content:
                content += optimization_notes
            skill_file.write_text(content, encoding="utf-8")
            return True

    def _gather_failure_context(self, skill_name: str, max_examples: int = 5) -> list[dict]:
        """Gather recent failure examples for a skill from interaction logs."""
        root = get_project_root()
        raw_dir = root / "training" / "data" / "raw"
        failures = []

        if not raw_dir.exists():
            return failures

        # Read recent interaction files (last 7 days worth)
        for f in sorted(raw_dir.glob("interactions-*.jsonl"), reverse=True)[:7]:
            try:
                for line in f.read_text(encoding="utf-8").strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Check if this interaction used the target skill and had issues
                    skills_used = record.get("skills_used", [])
                    feedback = record.get("user_feedback", "")
                    was_escalated = record.get("routing_info", {}).get("was_escalated", False)

                    if skill_name not in skills_used:
                        continue

                    # Failure indicators: negative feedback, escalation, or error
                    if feedback in ("👎", "negative") or was_escalated:
                        failures.append({
                            "user_message": record.get("user_message", "")[:200],
                            "response": record.get("model_response", "")[:200],
                            "feedback": feedback,
                            "was_escalated": was_escalated,
                        })

                    if len(failures) >= max_examples:
                        return failures
            except Exception:
                continue

        return failures

    async def _llm_improve_skill(
        self,
        skill_name: str,
        current_content: str,
        success_rate: float,
        usage_count: int,
        failure_examples: list[dict],
    ) -> str | None:
        """Use LLM to generate an improved version of a SKILL.md."""
        failures_text = ""
        if failure_examples:
            failures_text = "\n\n## Recent Failure Examples:\n"
            for i, ex in enumerate(failure_examples, 1):
                failures_text += (
                    f"\n### Failure {i}:\n"
                    f"User: {ex['user_message']}\n"
                    f"Response: {ex['response']}\n"
                    f"Feedback: {ex.get('feedback', 'N/A')}\n"
                    f"Escalated: {ex.get('was_escalated', False)}\n"
                )

        prompt = f"""You are a skill optimization expert for JARVIS AI assistant.

A skill is underperforming and needs improvement.

## Current SKILL.md:
```
{current_content}
```

## Performance:
- Success rate: {success_rate:.0%} (target: >70%)
- Total uses: {usage_count}
{failures_text}

## Task:
Improve this SKILL.md to increase its success rate. Focus on:
1. More specific trigger conditions (when to activate this skill)
2. Better workflow steps (clearer, more actionable)
3. Error handling (what to do when things go wrong)
4. Output format improvements
5. Edge case handling based on failure examples

Return ONLY the improved SKILL.md content (with frontmatter and body).
Keep the same YAML frontmatter structure. Do NOT change the skill name.
Write in the same language as the original (Vietnamese or English)."""

        try:
            response = await litellm.acompletion(
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.3,
            )

            improved = response.choices[0].message.content.strip()

            # Clean up code fences if LLM wrapped it
            if improved.startswith("```"):
                improved = re.sub(r"^```\w*\n?", "", improved)
                improved = re.sub(r"\n?```$", "", improved)

            # Validate it still has frontmatter
            if "---" not in improved:
                log.warning("gepa_invalid_output", skill=skill_name)
                return None

            return improved.strip()

        except Exception as e:
            log.error("gepa_llm_call_failed", error=str(e))
            return None

    # --- 2. Skill Merging ---

    async def _merge_similar(self, report: EvolutionReport) -> None:
        """Find and merge semantically similar skills."""
        all_skills = self._registry.get_all()
        if len(all_skills) < 2:
            return

        # Compute embeddings for all skill descriptions
        embeddings = {}
        for skill in all_skills:
            text = f"{skill.metadata.name}: {skill.metadata.description}"
            embeddings[skill.metadata.name] = await get_embedding(text)

        # Find pairs above merge threshold
        merged_names: set[str] = set()
        skill_names = list(embeddings.keys())

        for i in range(len(skill_names)):
            if skill_names[i] in merged_names:
                continue
            for j in range(i + 1, len(skill_names)):
                if skill_names[j] in merged_names:
                    continue

                sim = cosine_similarity(
                    embeddings[skill_names[i]], embeddings[skill_names[j]]
                )
                if sim >= _MERGE_SIMILARITY:
                    name_a, name_b = skill_names[i], skill_names[j]
                    skill_a = self._loader.get_skill(name_a)
                    skill_b = self._loader.get_skill(name_b)

                    if not skill_a or not skill_b:
                        continue

                    # Keep the one with higher success rate or more usage
                    # (the "winner" absorbs the other)
                    score_a = (
                        skill_a.metadata.usage_count * skill_a.metadata.success_rate
                    )
                    score_b = (
                        skill_b.metadata.usage_count * skill_b.metadata.success_rate
                    )

                    winner = skill_a if score_a >= score_b else skill_b
                    loser = skill_b if score_a >= score_b else skill_a

                    try:
                        self._merge_skills(winner, loser)
                        merged_names.add(loser.metadata.name)
                        report.merged.append(
                            (name_a, name_b, winner.metadata.name)
                        )
                        log.info("skills_merged",
                                 winner=winner.metadata.name,
                                 loser=loser.metadata.name,
                                 similarity=f"{sim:.2f}")
                    except Exception as e:
                        report.errors.append(f"Merge {name_a}+{name_b}: {e}")

    def _merge_skills(self, winner: Skill, loser: Skill) -> None:
        """Merge loser into winner skill."""
        # Archive loser
        self._archive_version(loser)

        # Add loser's description triggers to winner's SKILL.md
        if winner.metadata.path and winner.metadata.path.exists():
            content = winner.metadata.path.read_text(encoding="utf-8")
            merge_note = (
                f"\n\n## Merged From: {loser.metadata.name}\n"
                f"- Original description: {loser.metadata.description}\n"
                f"- Merged on: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            )
            if loser.metadata.name not in content:
                content += merge_note
                winner.metadata.path.write_text(content, encoding="utf-8")

        # Move loser to archive
        if loser.metadata.path and loser.metadata.path.exists():
            loser_dir = loser.metadata.path.parent
            archive_dest = self._archive_dir / loser.metadata.name
            if archive_dest.exists():
                shutil.rmtree(archive_dest)
            shutil.move(str(loser_dir), str(archive_dest))

    # --- 3. Pruning ---

    def _prune_unused(self, report: EvolutionReport) -> None:
        """Archive skills unused for _PRUNE_UNUSED_DAYS days."""
        unused_names = self._registry.get_unused_skills(days=_PRUNE_UNUSED_DAYS)

        for name in unused_names:
            skill = self._loader.get_skill(name)
            if not skill:
                continue

            # Don't prune core skills or recently created ones
            if skill.metadata.category == "core":
                continue
            if not skill.metadata.path:
                continue

            # Check if it's truly old (not just newly created)
            # If no metrics at all, it might be brand new — skip
            metrics = self._registry._metrics.get(name)
            if not metrics:
                continue  # No metrics = never used, but might be new

            try:
                self._archive_skill(skill)
                report.pruned.append(name)
                log.info("skill_pruned", name=name)
            except Exception as e:
                report.errors.append(f"Prune {name}: {e}")

    def _archive_skill(self, skill: Skill) -> None:
        """Move a skill to the archive directory."""
        if not skill.metadata.path or not skill.metadata.path.exists():
            return

        skill_dir = skill.metadata.path.parent
        archive_dest = self._archive_dir / skill.metadata.name

        if archive_dest.exists():
            # Append timestamp to avoid collision
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
            archive_dest = self._archive_dir / f"{skill.metadata.name}_{ts}"

        shutil.move(str(skill_dir), str(archive_dest))
        log.info("skill_archived", name=skill.metadata.name, dest=str(archive_dest))

    # --- Version Management ---

    def _archive_version(self, skill: Skill) -> None:
        """Archive the current version of a skill before modification."""
        if not skill.metadata.path or not skill.metadata.path.exists():
            return

        versions_dir = self._archive_dir / "versions" / skill.metadata.name
        versions_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        version_file = versions_dir / f"SKILL_{skill.metadata.version}_{ts}.md"

        shutil.copy2(str(skill.metadata.path), str(version_file))
        log.debug("version_archived",
                  name=skill.metadata.name, version=skill.metadata.version)

    def restore_version(self, skill_name: str, version_file: str) -> bool:
        """Restore a skill from a specific archived version."""
        version_path = (
            self._archive_dir / "versions" / skill_name / version_file
        )
        if not version_path.exists():
            log.warning("version_not_found", name=skill_name, file=version_file)
            return False

        # Find current skill
        skill = self._loader.get_skill(skill_name)
        if not skill or not skill.metadata.path:
            log.warning("skill_not_found_for_restore", name=skill_name)
            return False

        # Archive current before restoring
        self._archive_version(skill)

        # Restore
        shutil.copy2(str(version_path), str(skill.metadata.path))
        log.info("version_restored", name=skill_name, from_file=version_file)
        return True

    def list_versions(self, skill_name: str) -> list[str]:
        """List all archived versions of a skill."""
        versions_dir = self._archive_dir / "versions" / skill_name
        if not versions_dir.exists():
            return []
        return sorted(
            [f.name for f in versions_dir.glob("SKILL_*.md")], reverse=True
        )

    # --- Reports ---

    def _save_report(self, report: EvolutionReport) -> None:
        """Save evolution report."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
        report_path = self._reports_dir / f"evolution_{ts}.json"

        try:
            report_path.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("evolution_report_saved", path=str(report_path))
        except Exception as e:
            log.error("evolution_report_save_error", error=str(e))

    def get_recent_reports(self, limit: int = 5) -> list[dict]:
        """Get recent evolution reports."""
        reports = []
        for f in sorted(
            self._reports_dir.glob("evolution_*.json"), reverse=True
        )[:limit]:
            try:
                reports.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return reports
