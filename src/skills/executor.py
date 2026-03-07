"""Skill Executor — Execute skill workflows as mini-agents.

Upgrades skills from passive instructions to active executors:
1. Parse skill workflow steps from SKILL.md body
2. Inject precise tool-calling directives into the prompt
3. Track execution progress per skill
4. Report skill success/failure for metrics

Skills remain declarative (SKILL.md) — the executor translates
workflow steps into structured LLM directives that guide tool calling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.skills.loader import Skill
from src.utils.logging import get_logger

log = get_logger("skills.executor")


@dataclass
class SkillWorkflow:
    """Parsed workflow from a SKILL.md body."""
    steps: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    tools_referenced: list[str] = field(default_factory=list)
    output_format: str = ""


# Known tool names in JARVIS
_KNOWN_TOOLS = {
    "web_search", "fetch_url", "run_command", "run_python",
    "read_file", "write_file", "list_directory",
}

# Tool aliases found in SKILL.md text
_TOOL_ALIASES = {
    "search_web": "web_search",
    "tìm kiếm": "web_search",
    "search": "web_search",
    "chạy lệnh": "run_command",
    "shell": "run_command",
    "terminal": "run_command",
    "chạy code": "run_python",
    "python": "run_python",
    "đọc file": "read_file",
    "ghi file": "write_file",
}


def parse_workflow(skill: Skill) -> SkillWorkflow:
    """Parse structured workflow from SKILL.md body.

    Extracts:
    - Numbered steps from ## Workflow section
    - Rules from ## Quy tắc / ## Rules section
    - Tools referenced in the text
    - Output format from ## Output format section
    """
    body = skill.body or ""
    if not body.strip():
        return SkillWorkflow()

    workflow = SkillWorkflow()
    current_section = ""

    for line in body.split("\n"):
        stripped = line.strip()

        # Detect section headers
        if stripped.startswith("##"):
            header = stripped.lstrip("#").strip().lower()
            if any(w in header for w in ("workflow", "steps", "quy trình", "các bước")):
                current_section = "workflow"
            elif any(w in header for w in ("rule", "quy tắc", "constraint")):
                current_section = "rules"
            elif any(w in header for w in ("output", "format", "kết quả")):
                current_section = "output"
            else:
                current_section = "other"
            continue

        # Parse numbered steps
        if current_section == "workflow":
            match = re.match(r"^\d+\.\s+(.+)", stripped)
            if match:
                workflow.steps.append(match.group(1).strip())
            elif stripped.startswith("- "):
                workflow.steps.append(stripped[2:].strip())

        # Parse rules
        elif current_section == "rules":
            if stripped.startswith("- ") or stripped.startswith("* "):
                workflow.rules.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                workflow.rules.append(stripped)

        # Parse output format
        elif current_section == "output":
            if stripped:
                workflow.output_format += stripped + "\n"

    # Detect tool references in the full body
    body_lower = body.lower()
    for tool_name in _KNOWN_TOOLS:
        if tool_name in body_lower:
            workflow.tools_referenced.append(tool_name)

    for alias, tool_name in _TOOL_ALIASES.items():
        if alias in body_lower and tool_name not in workflow.tools_referenced:
            workflow.tools_referenced.append(tool_name)

    return workflow


def build_execution_directive(skill: Skill) -> str:
    """Build a structured LLM directive from a skill for tool-guided execution.

    Transforms SKILL.md content into an actionable prompt directive
    that guides the LLM to follow the skill's workflow precisely.
    """
    workflow = parse_workflow(skill)
    meta = skill.metadata
    emoji = f"{meta.emoji} " if meta.emoji else ""

    parts = [f"### {emoji}{meta.name} — EXECUTE THIS SKILL"]
    parts.append(f"_{meta.description}_\n")

    # Workflow steps as numbered directives
    if workflow.steps:
        parts.append("**Thực hiện theo thứ tự:**")
        for i, step in enumerate(workflow.steps, 1):
            parts.append(f"{i}. {step}")
        parts.append("")

    # Tool hints
    if workflow.tools_referenced:
        tool_list = ", ".join(f"`{t}`" for t in workflow.tools_referenced)
        parts.append(f"**Tools cần dùng:** {tool_list}")
        parts.append("")

    # Rules as constraints
    if workflow.rules:
        parts.append("**Quy tắc BẮT BUỘC:**")
        for rule in workflow.rules:
            parts.append(f"- {rule}")
        parts.append("")

    # Output format
    if workflow.output_format:
        parts.append(f"**Output format:**\n{workflow.output_format.strip()}")

    return "\n".join(parts)


class SkillExecutor:
    """Orchestrate skill execution by building precise tool-calling directives.

    Instead of just injecting raw SKILL.md body, the executor:
    1. Parses the skill's workflow
    2. Builds structured directives
    3. Tracks which skills were executed and their outcomes
    """

    def __init__(self) -> None:
        self._execution_log: list[dict] = []

    def build_skill_context(self, skills: list[Skill]) -> str:
        """Build execution-ready skill context for prompt injection.

        Returns structured directives instead of raw SKILL.md text.
        """
        if not skills:
            return ""

        parts = ["# ACTIVE SKILLS — Follow these workflows:\n"]

        for skill in skills:
            directive = build_execution_directive(skill)
            parts.append(directive)
            parts.append("---")

        return "\n".join(parts)

    def record_execution(
        self,
        skill_name: str,
        success: bool,
        tools_called: list[str] | None = None,
    ) -> None:
        """Record skill execution for analytics."""
        self._execution_log.append({
            "skill": skill_name,
            "success": success,
            "tools_called": tools_called or [],
        })
        log.info(
            "skill_executed",
            skill=skill_name,
            success=success,
            tools=tools_called,
        )

    def get_execution_stats(self) -> dict:
        """Get skill execution statistics."""
        if not self._execution_log:
            return {"total": 0, "success": 0, "failure": 0}

        total = len(self._execution_log)
        success = sum(1 for e in self._execution_log if e["success"])
        return {
            "total": total,
            "success": success,
            "failure": total - success,
            "success_rate": success / total if total > 0 else 0,
        }
