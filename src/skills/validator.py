"""Skill Validator — Schema validation and trace replay for skills.

Validates:
1. Structure: YAML frontmatter, required fields, format
2. Requirements: env vars, binaries, tools available
3. Trace replay: test skill against past interactions to measure quality

Used by evolver after GEPA optimization to verify improvements.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.skills.loader import Skill, SkillLoader
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("skills.validator")

# Required fields in YAML frontmatter
_REQUIRED_FIELDS = {"name", "description"}
_RECOMMENDED_FIELDS = {"version", "metadata"}

# Anthropic Agent Skills Spec constraints
_NAME_MAX_LEN = 64
_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_DESC_MAX_LEN = 1024


@dataclass
class ValidationResult:
    """Result of a skill validation."""

    skill_name: str
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    trace_results: dict | None = None  # If trace replay was done

    def to_dict(self) -> dict:
        d = {
            "skill_name": self.skill_name,
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }
        if self.trace_results:
            d["trace_results"] = self.trace_results
        return d


class SkillValidator:
    """Validate skill structure and test against interaction traces."""

    def __init__(self, loader: SkillLoader | None = None) -> None:
        self._loader = loader
        self._root = get_project_root()

    def validate_file(self, skill_path: Path) -> ValidationResult:
        """Validate a SKILL.md file's structure."""
        name = skill_path.parent.name
        result = ValidationResult(skill_name=name)

        if not skill_path.exists():
            result.valid = False
            result.errors.append(f"File not found: {skill_path}")
            return result

        content = skill_path.read_text(encoding="utf-8")

        # Check frontmatter
        self._validate_frontmatter(content, result)

        # Check body
        self._validate_body(content, result)

        return result

    def validate_skill(self, skill: Skill) -> ValidationResult:
        """Validate a loaded Skill object."""
        result = ValidationResult(skill_name=skill.metadata.name)

        # Check metadata
        if not skill.metadata.name:
            result.valid = False
            result.errors.append("Missing skill name")
        else:
            self._validate_name(skill.metadata.name, result)

        if not skill.metadata.description:
            result.valid = False
            result.errors.append("Missing skill description")
        elif len(skill.metadata.description) > _DESC_MAX_LEN:
            result.warnings.append(
                f"Description exceeds {_DESC_MAX_LEN} chars "
                f"({len(skill.metadata.description)} chars)"
            )

        if not skill.metadata.version:
            result.warnings.append("Missing version (recommended)")

        # Check body
        if not skill.body or len(skill.body.strip()) < 20:
            result.warnings.append("Skill body is very short — may lack detail")

        # Check for workflow section
        if skill.body and "workflow" not in skill.body.lower():
            result.warnings.append("No '## Workflow' section found")

        return result

    def _validate_name(self, name: str, result: ValidationResult) -> None:
        """Validate skill name per Anthropic Agent Skills Spec."""
        if len(name) > _NAME_MAX_LEN:
            result.valid = False
            result.errors.append(
                f"Name exceeds {_NAME_MAX_LEN} chars ({len(name)} chars)"
            )
        if not _NAME_PATTERN.match(name):
            result.warnings.append(
                f"Name '{name}' should be lowercase alphanumeric with hyphens only "
                f"(e.g., 'my-skill-name')"
            )

    def _validate_frontmatter(self, content: str, result: ValidationResult) -> None:
        """Validate YAML frontmatter."""
        if not content.startswith("---"):
            result.valid = False
            result.errors.append("Missing YAML frontmatter (file should start with ---)")
            return

        # Find closing ---
        parts = content.split("---", 2)
        if len(parts) < 3:
            result.valid = False
            result.errors.append("Malformed frontmatter (missing closing ---)")
            return

        frontmatter = parts[1].strip()

        # Check required fields
        for field_name in _REQUIRED_FIELDS:
            pattern = rf"^{field_name}:"
            if not re.search(pattern, frontmatter, re.MULTILINE):
                result.valid = False
                result.errors.append(f"Missing required field: {field_name}")

        # Validate name format if present
        name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
        if name_match:
            name = name_match.group(1).strip()
            self._validate_name(name, result)

        # Check recommended fields
        for field_name in _RECOMMENDED_FIELDS:
            pattern = rf"^{field_name}:"
            if not re.search(pattern, frontmatter, re.MULTILINE):
                result.warnings.append(f"Missing recommended field: {field_name}")

    def _validate_body(self, content: str, result: ValidationResult) -> None:
        """Validate skill body content."""
        # Split off frontmatter
        parts = content.split("---", 2)
        body = parts[2] if len(parts) >= 3 else ""

        if not body.strip():
            result.valid = False
            result.errors.append("Empty skill body")
            return

        if len(body.strip()) < 30:
            result.warnings.append("Skill body is very short")

        # Check for key sections
        body_lower = body.lower()
        if "##" not in body:
            result.warnings.append("No markdown sections (##) found in body")

        if "workflow" not in body_lower and "quy trình" not in body_lower:
            result.warnings.append("No workflow section — skill may lack actionable steps")

    def replay_traces(
        self,
        skill_name: str,
        max_traces: int = 10,
    ) -> dict:
        """Replay past interactions that used this skill.

        Returns metrics on how the skill performed historically.
        """
        raw_dir = self._root / "training" / "data" / "raw"
        if not raw_dir.exists():
            return {"traces_found": 0, "error": "No interaction data"}

        traces = []
        successes = 0
        failures = 0
        escalations = 0

        for f in sorted(raw_dir.glob("interactions-*.jsonl"), reverse=True)[:14]:
            try:
                for line in f.read_text(encoding="utf-8").strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    skills_used = record.get("skills_used", [])
                    if skill_name not in skills_used:
                        continue

                    feedback = record.get("user_feedback", "")
                    was_escalated = record.get("routing_info", {}).get("was_escalated", False)

                    trace = {
                        "user_message": record.get("user_message", "")[:100],
                        "confidence": record.get("confidence", 0),
                        "feedback": feedback,
                        "was_escalated": was_escalated,
                    }
                    traces.append(trace)

                    if feedback in ("👍", "positive"):
                        successes += 1
                    elif feedback in ("👎", "negative"):
                        failures += 1
                    if was_escalated:
                        escalations += 1

                    if len(traces) >= max_traces:
                        break

            except Exception:
                continue

            if len(traces) >= max_traces:
                break

        total = len(traces)
        return {
            "traces_found": total,
            "successes": successes,
            "failures": failures,
            "escalations": escalations,
            "success_rate": successes / total if total > 0 else 0,
            "escalation_rate": escalations / total if total > 0 else 0,
            "sample_traces": traces[:5],
        }

    def compare_versions(
        self,
        skill_name: str,
        old_path: Path,
        new_path: Path,
    ) -> dict:
        """Compare two versions of a skill file.

        Returns structural differences and quality indicators.
        """
        old_content = old_path.read_text(encoding="utf-8") if old_path.exists() else ""
        new_content = new_path.read_text(encoding="utf-8") if new_path.exists() else ""

        old_lines = len(old_content.splitlines())
        new_lines = len(new_content.splitlines())

        # Count sections
        old_sections = len(re.findall(r"^##\s", old_content, re.MULTILINE))
        new_sections = len(re.findall(r"^##\s", new_content, re.MULTILINE))

        # Validate both
        old_result = self.validate_file(old_path) if old_path.exists() else None
        new_result = self.validate_file(new_path) if new_path.exists() else None

        return {
            "old_lines": old_lines,
            "new_lines": new_lines,
            "lines_delta": new_lines - old_lines,
            "old_sections": old_sections,
            "new_sections": new_sections,
            "old_valid": old_result.valid if old_result else False,
            "new_valid": new_result.valid if new_result else False,
            "new_errors": new_result.errors if new_result else [],
            "new_warnings": new_result.warnings if new_result else [],
        }
