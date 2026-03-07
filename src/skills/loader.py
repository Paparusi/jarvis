"""Skill Loader — Progressive disclosure loading of SKILL.md files.

3 cấp tải:
- L1 Metadata: name + description (luôn trong context, ~100 từ/skill)
- L2 Full body: workflow, rules, output format (khi skill triggered)
- L3 References: examples, chi tiết (khi cần deep context)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("skills.loader")


@dataclass
class SkillMetadata:
    """L1: Lightweight metadata always in context."""
    name: str
    description: str
    version: str = "1.0.0"
    category: str = "general"
    emoji: str = ""
    requires_env: list[str] = field(default_factory=list)
    requires_bins: list[str] = field(default_factory=list)
    success_rate: float = 1.0
    usage_count: int = 0
    priority: float = 0.5
    auto_generated: bool = False
    path: Path | None = None
    # Anthropic Agent Skills Spec fields
    license: str = ""
    compatibility: dict = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class Skill:
    """Full skill with body content."""
    metadata: SkillMetadata
    body: str = ""          # L2: full SKILL.md markdown body
    references: list[str] = field(default_factory=list)  # L3: reference file paths


class SkillLoader:
    """Load skills from workspace/skills/ directory."""

    def __init__(self) -> None:
        self._skills_dir = get_project_root() / "workspace" / "skills"
        self._skills: dict[str, Skill] = {}

    def load_all(self) -> dict[str, Skill]:
        """Scan and load all skills (L1 metadata + L2 body)."""
        self._skills.clear()
        if not self._skills_dir.exists():
            self._skills_dir.mkdir(parents=True, exist_ok=True)
            return self._skills

        for skill_md in self._skills_dir.rglob("SKILL.md"):
            try:
                skill = self._parse_skill(skill_md)
                if skill:
                    self._skills[skill.metadata.name] = skill
            except Exception as e:
                log.warning("skill_load_error", path=str(skill_md), error=str(e))

        log.info("skills_loaded", count=len(self._skills))
        return self._skills

    def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def get_all_metadata(self) -> list[SkillMetadata]:
        """L1: Get all skill metadata (lightweight, for context injection)."""
        return [s.metadata for s in self._skills.values()]

    def get_metadata_summary(self) -> str:
        """Generate a compact metadata summary for system prompt injection."""
        if not self._skills:
            return ""

        lines = ["## Kỹ năng hiện có:"]
        for skill in sorted(self._skills.values(), key=lambda s: -s.metadata.priority):
            meta = skill.metadata
            emoji = f"{meta.emoji} " if meta.emoji else ""
            lines.append(f"- {emoji}**{meta.name}**: {meta.description}")
        return "\n".join(lines)

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Fix UTF-16 surrogate pairs that sneak in via YAML emoji escapes."""
        try:
            text.encode("utf-8", errors="strict")
            return text
        except UnicodeEncodeError:
            return text.encode("utf-16", "surrogatepass").decode("utf-16")

    def _parse_skill(self, path: Path) -> Skill | None:
        """Parse a SKILL.md file into a Skill object."""
        content = path.read_text(encoding="utf-8")

        # Split YAML frontmatter and body
        frontmatter, body = self._split_frontmatter(content)
        if not frontmatter:
            return None

        meta = frontmatter
        jarvis_meta = meta.get("metadata", {}).get("jarvis", {})

        skill_meta = SkillMetadata(
            name=meta.get("name", path.parent.name),
            description=self._sanitize_text(meta.get("description", "").strip()),
            version=meta.get("version", "1.0.0"),
            category=jarvis_meta.get("category", "general"),
            emoji=self._sanitize_text(jarvis_meta.get("emoji", "")),
            requires_env=jarvis_meta.get("requires", {}).get("env", []),
            requires_bins=jarvis_meta.get("requires", {}).get("bins", []),
            success_rate=jarvis_meta.get("success_rate", 1.0),
            usage_count=jarvis_meta.get("usage_count", 0),
            priority=jarvis_meta.get("priority", 0.5),
            auto_generated=jarvis_meta.get("auto_generated", False),
            path=path,
            license=meta.get("license", ""),
            compatibility=meta.get("compatibility", {}),
            allowed_tools=meta.get("allowed-tools", []),
        )

        # Find reference files
        refs_dir = path.parent / "references"
        references = []
        if refs_dir.exists():
            references = [str(f) for f in refs_dir.glob("*.md")]

        return Skill(metadata=skill_meta, body=body.strip(), references=references)

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[dict | None, str]:
        """Split YAML frontmatter from markdown body."""
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
        if not match:
            return None, content

        try:
            frontmatter = yaml.safe_load(match.group(1))
            body = match.group(2)
            return frontmatter, body
        except yaml.YAMLError:
            return None, content
