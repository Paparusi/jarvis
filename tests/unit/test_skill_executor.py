"""Tests for Skill Execution Framework — workflow parsing and execution directives."""

import pytest
from unittest.mock import MagicMock

from src.skills.executor import (
    SkillExecutor, SkillWorkflow, parse_workflow, build_execution_directive,
)


def _mock_skill(body: str) -> MagicMock:
    """Create a mock Skill object with the given body."""
    skill = MagicMock()
    skill.body = body
    skill.metadata.name = "test-skill"
    return skill


class TestParseWorkflow:

    def test_parse_workflow_with_all_sections(self):
        body = """# Web Research

## Workflow
1. Analyze the user's query
2. Search using web_search tool
3. Synthesize results

## Rules
- Always cite sources
- Cross-reference multiple sources

## Output format
- Markdown with bullet points
- Sources section at the end
"""
        wf = parse_workflow(_mock_skill(body))
        assert isinstance(wf, SkillWorkflow)
        assert len(wf.steps) == 3
        assert "Analyze" in wf.steps[0]
        assert len(wf.rules) == 2
        assert "cite" in wf.rules[0].lower()
        assert "web_search" in wf.tools_referenced
        assert wf.output_format

    def test_parse_workflow_empty(self):
        wf = parse_workflow(_mock_skill(""))
        assert wf.steps == []
        assert wf.rules == []

    def test_parse_workflow_no_sections(self):
        body = "Just some text about this skill."
        wf = parse_workflow(_mock_skill(body))
        assert wf.steps == []

    def test_detects_tool_references(self):
        body = """## Workflow
1. Use fetch_url to get page content
2. Run run_python to analyze data
3. Search with web search
"""
        wf = parse_workflow(_mock_skill(body))
        assert "fetch_url" in wf.tools_referenced
        assert "run_python" in wf.tools_referenced


class TestBuildExecutionDirective:

    def test_builds_directive_with_workflow(self):
        skill = MagicMock()
        skill.body = """## Workflow
1. Search for info
2. Analyze results

## Rules
- Always cite sources

## Output format
Markdown with sources
"""
        skill.metadata.name = "web-research"
        skill.metadata.description = "Web research skill"
        skill.metadata.emoji = ""

        directive = build_execution_directive(skill)

        assert "web-research" in directive
        assert "Search for info" in directive
        assert "Always cite sources" in directive

    def test_empty_workflow(self):
        skill = MagicMock()
        skill.body = ""
        skill.metadata.name = "empty-skill"
        skill.metadata.description = "Empty"
        skill.metadata.emoji = ""

        directive = build_execution_directive(skill)
        assert "empty-skill" in directive


class TestSkillExecutor:

    def _make_skill(self, name="test-skill", body="## Workflow\n1. Do something\n## Rules\n- Be good"):
        skill = MagicMock()
        skill.metadata.name = name
        skill.metadata.description = "A test skill"
        skill.metadata.emoji = ""
        skill.body = body
        return skill

    def test_build_skill_context(self):
        executor = SkillExecutor()
        skill = self._make_skill()

        context = executor.build_skill_context([skill])
        assert "test-skill" in context
        assert "Do something" in context

    def test_record_execution(self):
        executor = SkillExecutor()
        executor.record_execution("test-skill", success=True)
        executor.record_execution("test-skill", success=True)
        executor.record_execution("test-skill", success=False)

        stats = executor.get_execution_stats()
        assert stats["total"] == 3
        assert stats["success"] == 2
        assert stats["failure"] == 1
        assert stats["success_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_execution_stats_empty(self):
        executor = SkillExecutor()
        stats = executor.get_execution_stats()
        assert stats["total"] == 0
