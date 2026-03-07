"""Dreamer — Analyze patterns and generate improvement insights during Dreamtime.

The Dreamer reflects on JARVIS's performance:
1. Analyze skill success rates → suggest improvements
2. Identify frequently failed queries → create training priorities
3. Detect interaction patterns → suggest new skills
4. Generate self-improvement report
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from src.brain.collector import DataCollector
from src.skills.registry import SkillRegistry
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("dreamtime.dreamer")


class Dreamer:
    """Analyze JARVIS performance and generate improvement insights."""

    def __init__(
        self,
        collector: DataCollector,
        skill_registry: SkillRegistry,
    ) -> None:
        self._collector = collector
        self._skill_registry = skill_registry

    async def dream(self) -> dict:
        """Run a full dream cycle — analyze and generate insights.

        Returns a report with findings and recommendations.
        """
        report = {
            "skill_analysis": self._analyze_skills(),
            "failure_patterns": self._analyze_failures(),
            "improvement_suggestions": [],
            "new_skill_candidates": [],
        }

        # Generate improvement suggestions
        report["improvement_suggestions"] = self._generate_suggestions(report)

        # Detect patterns that could become new skills
        report["new_skill_candidates"] = await self._detect_skill_candidates()

        log.info("dream_complete",
                suggestions=len(report["improvement_suggestions"]),
                candidates=len(report["new_skill_candidates"]))

        # Save report
        self._save_report(report)

        return report

    def _analyze_skills(self) -> list[dict]:
        """Analyze skill performance and identify underperformers."""
        results = []
        all_skills = self._skill_registry.get_all()

        for skill in all_skills:
            meta = skill.metadata
            if meta.usage_count < 1:
                continue

            status = "healthy"
            if meta.success_rate < 0.7 and meta.usage_count >= 5:
                status = "underperforming"
            elif meta.usage_count == 0:
                status = "unused"

            results.append({
                "name": meta.name,
                "usage_count": meta.usage_count,
                "success_rate": meta.success_rate,
                "status": status,
            })

        return sorted(results, key=lambda x: x["success_rate"])

    def _analyze_failures(self) -> list[dict]:
        """Analyze recent failures to identify patterns."""
        # Check both possible data locations
        raw_dir = get_project_root() / "training" / "data" / "raw"

        if not raw_dir.exists():
            return []

        error_patterns: Counter = Counter()
        escalation_patterns: Counter = Counter()

        # Read recent interaction logs
        log_files = sorted(raw_dir.glob("interactions-*.jsonl"), reverse=True)[:7]

        for log_file in log_files:
            try:
                for line in log_file.read_text().strip().split("\n"):
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    query = record.get("user_message", "")[:100]
                    feedback = record.get("user_feedback")
                    was_escalated = record.get("was_escalated", False)

                    # Track negative feedback
                    if feedback == "negative":
                        words = query.lower().split()[:5]
                        error_patterns[" ".join(words)] += 1

                    # Track cloud escalations (local model couldn't handle)
                    if was_escalated:
                        words = query.lower().split()[:5]
                        escalation_patterns[" ".join(words)] += 1

            except Exception:
                continue

        # Combine patterns
        repeated = [
            {"pattern": pattern, "count": count, "type": "failure"}
            for pattern, count in error_patterns.most_common(5)
            if count >= 2
        ]
        repeated.extend([
            {"pattern": pattern, "count": count, "type": "escalation"}
            for pattern, count in escalation_patterns.most_common(5)
            if count >= 3
        ])

        return repeated

    def _generate_suggestions(self, report: dict) -> list[str]:
        """Generate actionable improvement suggestions."""
        suggestions = []

        # Skill improvements
        for skill in report.get("skill_analysis", []):
            if skill["status"] == "underperforming":
                suggestions.append(
                    f"Skill '{skill['name']}' has {skill['success_rate']:.0%} success rate "
                    f"over {skill['usage_count']} uses — review and improve workflow"
                )

        # Failure pattern suggestions
        for pattern in report.get("failure_patterns", []):
            if pattern["count"] >= 3:
                suggestions.append(
                    f"Repeated failures on queries like '{pattern['pattern']}' "
                    f"({pattern['count']} times) — consider creating a dedicated skill"
                )

        # Unused skills (returns list of skill names)
        unused = self._skill_registry.get_unused_skills(days=14)
        if len(unused) > 3:
            suggestions.append(
                f"Skills unused in 14 days: {', '.join(unused[:5])} — "
                f"consider archiving or improving discovery"
            )

        return suggestions

    async def _detect_skill_candidates(self) -> list[dict]:
        """Detect repeated interaction patterns that could become skills."""
        try:
            from src.skills.generator import SkillGenerator
            generator = SkillGenerator(min_occurrences=3)
            patterns = await generator.detect_patterns()
            return [
                {"pattern": p.get("description", p.get("topic", "unknown")),
                 "occurrences": p.get("count", 0),
                 "topic": p.get("topic", "")}
                for p in patterns[:5]
            ]
        except Exception as e:
            log.debug("skill_detection_error", error=str(e))
            return []

    def _save_report(self, report: dict) -> None:
        """Save dream report for later analysis."""
        reports_dir = get_project_root() / "data" / "dreamtime"
        reports_dir.mkdir(parents=True, exist_ok=True)

        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
        report_path = reports_dir / f"dream_{timestamp}.json"

        try:
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            log.info("dream_report_saved", path=str(report_path))
        except Exception as e:
            log.error("dream_report_save_error", error=str(e))
