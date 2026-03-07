"""Tests for Skill Evolver."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from src.skills.evolver import SkillEvolver, EvolutionReport


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.get_all.return_value = []
    registry.get_unused_skills.return_value = []
    registry._metrics = {}
    return registry


@pytest.fixture
def mock_loader():
    loader = MagicMock()
    loader.get_skill.return_value = None
    return loader


@pytest.fixture
def evolver(mock_registry, mock_loader, tmp_path, monkeypatch):
    monkeypatch.setattr("src.skills.evolver.get_project_root", lambda: tmp_path)
    ev = SkillEvolver(registry=mock_registry, loader=mock_loader)
    return ev


class TestEvolutionReport:
    def test_to_dict(self):
        report = EvolutionReport()
        report.optimized = ["skill-a"]
        report.pruned = ["skill-b"]
        d = report.to_dict()
        assert d["optimized"] == ["skill-a"]
        assert d["pruned"] == ["skill-b"]
        assert d["total_actions"] == 2

    def test_empty_report(self):
        report = EvolutionReport()
        d = report.to_dict()
        assert d["total_actions"] == 0
        assert d["errors"] == []


class TestSkillEvolver:
    @pytest.mark.asyncio
    async def test_run_empty(self, evolver):
        """Run with no skills should succeed with empty report."""
        report = await evolver.run()
        assert isinstance(report, EvolutionReport)
        assert report.to_dict()["total_actions"] == 0

    @pytest.mark.asyncio
    async def test_prune_unused(self, evolver, mock_registry, mock_loader, tmp_path):
        """Should archive skills unused for 30+ days."""
        # Create a skill directory
        skill_dir = tmp_path / "workspace" / "skills" / "old-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("---\nname: old-skill\n---\n# Old Skill")

        # Create mock skill
        mock_skill = MagicMock()
        mock_skill.metadata.name = "old-skill"
        mock_skill.metadata.category = "productivity"
        mock_skill.metadata.path = skill_file
        mock_skill.metadata.version = "1.0.0"

        mock_registry.get_unused_skills.return_value = ["old-skill"]
        mock_loader.get_skill.return_value = mock_skill
        mock_registry._metrics = {"old-skill": {"usage_count": 5, "last_used": "2026-01-01T00:00:00"}}

        report = await evolver.run()
        assert "old-skill" in report.pruned

    @pytest.mark.asyncio
    async def test_skip_core_prune(self, evolver, mock_registry, mock_loader, tmp_path):
        """Should NOT prune core skills."""
        skill_dir = tmp_path / "workspace" / "skills" / "core-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("---\nname: core-skill\n---\n# Core")

        mock_skill = MagicMock()
        mock_skill.metadata.name = "core-skill"
        mock_skill.metadata.category = "core"
        mock_skill.metadata.path = skill_file

        mock_registry.get_unused_skills.return_value = ["core-skill"]
        mock_loader.get_skill.return_value = mock_skill
        mock_registry._metrics = {"core-skill": {"usage_count": 0, "last_used": None}}

        report = await evolver.run()
        assert "core-skill" not in report.pruned

    def test_list_versions_empty(self, evolver):
        """Should return empty list for nonexistent skill."""
        versions = evolver.list_versions("nonexistent")
        assert versions == []

    def test_get_recent_reports_empty(self, evolver):
        """Should return empty list when no reports exist."""
        reports = evolver.get_recent_reports()
        assert reports == []

    @pytest.mark.asyncio
    async def test_report_saved(self, evolver, tmp_path):
        """Evolution report should be saved to disk."""
        await evolver.run()
        reports_dir = tmp_path / "data" / "evolution"
        report_files = list(reports_dir.glob("evolution_*.json"))
        assert len(report_files) >= 1
        data = json.loads(report_files[0].read_text())
        assert "optimized" in data
