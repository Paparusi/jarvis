"""Tests for Skill Evolver (GEPA) and Skill Validator."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from src.skills.evolver import SkillEvolver, EvolutionReport
from src.skills.validator import SkillValidator, ValidationResult


# --- Fixtures ---

SAMPLE_SKILL_MD = """---
name: test-skill
description: A test skill for validation
version: "1.0.0"
metadata:
  jarvis:
    category: core
    success_rate: 0.65
    usage_count: 10
---

# Test Skill

## Workflow
1. Step one
2. Step two

## Quy tắc
- Rule one
"""


@pytest.fixture
def skill_dir(tmp_path):
    """Create a temporary skill directory with a SKILL.md."""
    d = tmp_path / "workspace" / "skills" / "test-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SAMPLE_SKILL_MD, encoding="utf-8")

    # Create archive dir
    (tmp_path / "workspace" / "skills" / ".archive").mkdir(parents=True)
    (tmp_path / "data" / "evolution").mkdir(parents=True)

    return d


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.get_unused_skills.return_value = []
    registry._metrics = {}
    return registry


@pytest.fixture
def mock_loader(skill_dir):
    loader = MagicMock()
    skill = MagicMock()
    skill.metadata.name = "test-skill"
    skill.metadata.description = "A test skill"
    skill.metadata.version = "1.0.0"
    skill.metadata.path = skill_dir / "SKILL.md"
    skill.metadata.success_rate = 0.65
    skill.metadata.usage_count = 10
    skill.metadata.category = "productivity"
    skill.body = SAMPLE_SKILL_MD
    loader.get_skill.return_value = skill
    return loader


@pytest.fixture
def evolver(mock_registry, mock_loader, tmp_path, monkeypatch):
    monkeypatch.setattr("src.skills.evolver.get_project_root", lambda: tmp_path)
    return SkillEvolver(registry=mock_registry, loader=mock_loader)


# === EvolutionReport Tests ===

class TestEvolutionReport:
    def test_empty_report(self):
        report = EvolutionReport()
        d = report.to_dict()
        assert d["total_actions"] == 0
        assert d["optimized"] == []

    def test_report_with_actions(self):
        report = EvolutionReport()
        report.optimized.append("skill-a")
        report.pruned.append("skill-b")
        d = report.to_dict()
        assert d["total_actions"] == 2


# === GEPA Optimization Tests ===

class TestGEPAOptimization:

    @pytest.mark.asyncio
    async def test_gepa_archives_version(self, evolver, skill_dir, tmp_path):
        """GEPA should archive current version before modifying."""
        skill = MagicMock()
        skill.metadata.name = "test-skill"
        skill.metadata.path = skill_dir / "SKILL.md"
        skill.metadata.version = "1.0.0"
        skill.metadata.success_rate = 0.50
        skill.metadata.usage_count = 15

        # Mock LLM response
        improved_md = SAMPLE_SKILL_MD.replace("Step one", "Improved step one with error handling")
        from src.intelligence.llm_models import LLMResponse, Choice, Message
        mock_response = LLMResponse(
            choices=[Choice(message=Message(content=improved_md))],
        )

        with patch("src.skills.evolver.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client
            result = await evolver._gepa_optimize(skill)

        assert result is True

        # Check archive was created
        versions_dir = tmp_path / "workspace" / "skills" / ".archive" / "versions" / "test-skill"
        assert versions_dir.exists()
        archived_files = list(versions_dir.glob("SKILL_*.md"))
        assert len(archived_files) >= 1

    @pytest.mark.asyncio
    async def test_gepa_llm_failure_falls_back(self, evolver, skill_dir):
        """GEPA should fall back to notes when LLM fails."""
        skill = MagicMock()
        skill.metadata.name = "test-skill"
        skill.metadata.path = skill_dir / "SKILL.md"
        skill.metadata.version = "1.0.0"
        skill.metadata.success_rate = 0.50
        skill.metadata.usage_count = 15

        with patch("src.skills.evolver.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(side_effect=RuntimeError("API down"))
            mock_get_client.return_value = mock_client
            result = await evolver._gepa_optimize(skill)

        assert result is True
        content = (skill_dir / "SKILL.md").read_text()
        assert "Optimization Notes" in content
        assert "LLM optimization failed" in content

    @pytest.mark.asyncio
    async def test_gepa_skips_no_path(self, evolver):
        """GEPA should skip skills without a path."""
        skill = MagicMock()
        skill.metadata.path = None
        result = await evolver._gepa_optimize(skill)
        assert result is False


class TestGatherFailureContext:

    def test_no_data_dir(self, evolver, tmp_path, monkeypatch):
        monkeypatch.setattr("src.skills.evolver.get_project_root", lambda: tmp_path)
        failures = evolver._gather_failure_context("nonexistent-skill")
        assert failures == []

    def test_gathers_from_logs(self, evolver, tmp_path, monkeypatch):
        monkeypatch.setattr("src.skills.evolver.get_project_root", lambda: tmp_path)
        raw_dir = tmp_path / "training" / "data" / "raw"
        raw_dir.mkdir(parents=True)

        records = [
            {
                "user_message": "test query",
                "model_response": "bad response",
                "skills_used": ["test-skill"],
                "user_feedback": "👎",
                "routing_info": {"was_escalated": False},
            },
            {
                "user_message": "good query",
                "model_response": "good response",
                "skills_used": ["test-skill"],
                "user_feedback": "👍",
                "routing_info": {"was_escalated": False},
            },
            {
                "user_message": "escalated query",
                "model_response": "escalated response",
                "skills_used": ["test-skill"],
                "user_feedback": "",
                "routing_info": {"was_escalated": True},
            },
        ]
        log_file = raw_dir / "interactions-2026-03-05.jsonl"
        log_file.write_text("\n".join(json.dumps(r) for r in records))

        failures = evolver._gather_failure_context("test-skill")
        assert len(failures) == 2  # Only negative feedback + escalated
        assert failures[0]["feedback"] == "👎"


# === Skill Merging Tests ===

class TestSkillMerging:

    @pytest.mark.asyncio
    async def test_merge_similar_skills(self, evolver, mock_registry, mock_loader, tmp_path):
        """Should merge skills with >0.90 similarity."""
        skill_a = MagicMock()
        skill_a.metadata.name = "api-testing"
        skill_a.metadata.description = "Test API endpoints"
        skill_a.metadata.usage_count = 20
        skill_a.metadata.success_rate = 0.85
        skill_a.metadata.path = tmp_path / "workspace" / "skills" / "api-testing" / "SKILL.md"
        skill_a.metadata.path.parent.mkdir(parents=True)
        skill_a.metadata.path.write_text("---\nname: api-testing\n---\nTest")

        skill_b = MagicMock()
        skill_b.metadata.name = "endpoint-checker"
        skill_b.metadata.description = "Check API endpoints"
        skill_b.metadata.usage_count = 10
        skill_b.metadata.success_rate = 0.80
        skill_b.metadata.path = tmp_path / "workspace" / "skills" / "endpoint-checker" / "SKILL.md"
        skill_b.metadata.path.parent.mkdir(parents=True)
        skill_b.metadata.path.write_text("---\nname: endpoint-checker\n---\nCheck")

        mock_registry.get_all.return_value = [skill_a, skill_b]
        mock_loader.get_skill.side_effect = lambda n: skill_a if n == "api-testing" else skill_b

        # Mock embeddings to be very similar
        with patch("src.skills.evolver.get_embedding", new_callable=AsyncMock,
                    return_value=[1.0] * 384), \
             patch("src.skills.evolver.cosine_similarity", return_value=0.95):
            report = EvolutionReport()
            await evolver._merge_similar(report)

        assert len(report.merged) == 1


# === Pruning Tests ===

class TestSkillPruning:

    def test_prune_unused(self, evolver, mock_registry, mock_loader, tmp_path):
        mock_registry.get_unused_skills.return_value = ["old-skill"]
        mock_registry._metrics = {"old-skill": {"usage_count": 5}}

        skill = MagicMock()
        skill.metadata.name = "old-skill"
        skill.metadata.category = "productivity"
        skill.metadata.path = tmp_path / "workspace" / "skills" / "old-skill" / "SKILL.md"
        skill.metadata.path.parent.mkdir(parents=True)
        skill.metadata.path.write_text("---\nname: old-skill\n---\nOld")
        mock_loader.get_skill.return_value = skill

        report = EvolutionReport()
        evolver._prune_unused(report)

        assert "old-skill" in report.pruned

    def test_skip_core_skills(self, evolver, mock_registry, mock_loader):
        mock_registry.get_unused_skills.return_value = ["general-chat"]
        mock_registry._metrics = {"general-chat": {"usage_count": 0}}

        skill = MagicMock()
        skill.metadata.name = "general-chat"
        skill.metadata.category = "core"
        mock_loader.get_skill.return_value = skill

        report = EvolutionReport()
        evolver._prune_unused(report)

        assert report.pruned == []  # Core skills never pruned


# === Version Management Tests ===

class TestVersionManagement:

    def test_archive_and_list(self, evolver, skill_dir, tmp_path):
        skill = MagicMock()
        skill.metadata.name = "test-skill"
        skill.metadata.path = skill_dir / "SKILL.md"
        skill.metadata.version = "1.0.0"

        evolver._archive_version(skill)

        versions = evolver.list_versions("test-skill")
        assert len(versions) >= 1
        assert versions[0].startswith("SKILL_1.0.0_")

    def test_restore_version(self, evolver, skill_dir, mock_loader, tmp_path):
        # Create a version from the original SKILL.md
        skill = MagicMock()
        skill.metadata.name = "test-skill"
        skill.metadata.path = skill_dir / "SKILL.md"
        skill.metadata.version = "1.0.0"

        # Ensure original has frontmatter
        assert "---" in (skill_dir / "SKILL.md").read_text()

        evolver._archive_version(skill)

        # Modify original
        (skill_dir / "SKILL.md").write_text("modified content")
        assert "---" not in (skill_dir / "SKILL.md").read_text()

        # Set up loader to return skill with correct path
        restore_skill = MagicMock()
        restore_skill.metadata.name = "test-skill"
        restore_skill.metadata.path = skill_dir / "SKILL.md"
        restore_skill.metadata.version = "modified"
        mock_loader.get_skill.return_value = restore_skill

        versions = evolver.list_versions("test-skill")
        assert len(versions) >= 1

        result = evolver.restore_version("test-skill", versions[0])
        assert result is True

        # Content should be restored to original (with frontmatter)
        restored = (skill_dir / "SKILL.md").read_text()
        assert "---" in restored


# === Skill Validator Tests ===

class TestSkillValidatorFile:

    def test_valid_skill(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(SAMPLE_SKILL_MD)

        validator = SkillValidator()
        result = validator.validate_file(skill_file)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_missing_file(self, tmp_path):
        validator = SkillValidator()
        result = validator.validate_file(tmp_path / "nonexistent" / "SKILL.md")
        assert result.valid is False
        assert any("not found" in e for e in result.errors)

    def test_missing_frontmatter(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# No Frontmatter\nJust body content")

        validator = SkillValidator()
        result = validator.validate_file(skill_file)
        assert result.valid is False
        assert any("frontmatter" in e.lower() for e in result.errors)

    def test_missing_required_fields(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("---\nversion: 1.0.0\n---\n# Body")

        validator = SkillValidator()
        result = validator.validate_file(skill_file)
        assert result.valid is False
        assert any("name" in e for e in result.errors)

    def test_empty_body(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("---\nname: test\ndescription: test\n---\n")

        validator = SkillValidator()
        result = validator.validate_file(skill_file)
        assert result.valid is False
        assert any("Empty" in e for e in result.errors)


class TestSkillValidatorObject:

    def test_valid_skill_object(self):
        skill = MagicMock()
        skill.metadata.name = "test"
        skill.metadata.description = "A test skill"
        skill.metadata.version = "1.0.0"
        skill.body = "## Workflow\n1. Do something\n2. Do another thing"

        validator = SkillValidator()
        result = validator.validate_skill(skill)
        assert result.valid is True

    def test_missing_name(self):
        skill = MagicMock()
        skill.metadata.name = ""
        skill.metadata.description = "test"
        skill.metadata.version = "1.0.0"
        skill.body = "content"

        validator = SkillValidator()
        result = validator.validate_skill(skill)
        assert result.valid is False


class TestTraceReplay:

    def test_no_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.skills.validator.get_project_root", lambda: tmp_path)
        validator = SkillValidator()
        result = validator.replay_traces("nonexistent")
        assert result["traces_found"] == 0

    def test_with_traces(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.skills.validator.get_project_root", lambda: tmp_path)
        raw_dir = tmp_path / "training" / "data" / "raw"
        raw_dir.mkdir(parents=True)

        records = [
            {
                "user_message": "good query",
                "skills_used": ["my-skill"],
                "user_feedback": "👍",
                "confidence": 0.9,
                "routing_info": {"was_escalated": False},
            },
            {
                "user_message": "bad query",
                "skills_used": ["my-skill"],
                "user_feedback": "👎",
                "confidence": 0.3,
                "routing_info": {"was_escalated": True},
            },
            {
                "user_message": "other skill",
                "skills_used": ["other"],
                "user_feedback": "👍",
                "confidence": 0.95,
                "routing_info": {"was_escalated": False},
            },
        ]
        (raw_dir / "interactions-2026-03-05.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records)
        )

        validator = SkillValidator()
        result = validator.replay_traces("my-skill")

        assert result["traces_found"] == 2
        assert result["successes"] == 1
        assert result["failures"] == 1
        assert result["escalations"] == 1
        assert result["success_rate"] == 0.5
