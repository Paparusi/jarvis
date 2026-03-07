"""Tests for .md-based identity system (JARVIS.md, USER.md, AGENTS.md)."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from src.digital_twin.user_model import UserModel


@pytest.fixture
def user_model(tmp_path, monkeypatch):
    """UserModel with temp database."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.digital_twin.user_model.get_connection", _make_connection(db_path))
    monkeypatch.setattr("src.digital_twin.user_model.get_project_root", lambda: tmp_path)
    (tmp_path / "workspace").mkdir(exist_ok=True)
    return UserModel()


def _make_connection(db_path):
    """Create a SQLite connection factory for testing."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    def get_conn():
        return conn

    return get_conn


class TestJarvisMdExists:
    def test_jarvis_md_exists(self):
        jarvis_md = Path("/home/admin_1/projects/jarvis/workspace/JARVIS.md")
        assert jarvis_md.exists(), "JARVIS.md should exist"

    def test_jarvis_md_has_identity(self):
        content = Path("/home/admin_1/projects/jarvis/workspace/JARVIS.md").read_text()
        assert "JARVIS" in content
        assert "Bi" in content
        assert "Core Identity" in content

    def test_jarvis_md_has_personality(self):
        content = Path("/home/admin_1/projects/jarvis/workspace/JARVIS.md").read_text()
        assert "Personality" in content
        assert "trung thực" in content.lower() or "honest" in content.lower()

    def test_jarvis_md_has_capabilities(self):
        content = Path("/home/admin_1/projects/jarvis/workspace/JARVIS.md").read_text()
        assert "Capabilities" in content
        assert "Layer" in content

    def test_jarvis_md_has_rules(self):
        content = Path("/home/admin_1/projects/jarvis/workspace/JARVIS.md").read_text()
        assert "Rules" in content


class TestUserMdExists:
    def test_user_md_exists(self):
        user_md = Path("/home/admin_1/projects/jarvis/workspace/USER.md")
        assert user_md.exists(), "USER.md should exist"

    def test_user_md_has_identity(self):
        content = Path("/home/admin_1/projects/jarvis/workspace/USER.md").read_text()
        assert "Bi" in content
        assert "Digital Twin" in content


class TestAgentsMd:
    def test_agents_md_exists(self):
        agents_md = Path("/home/admin_1/projects/jarvis/workspace/AGENTS.md")
        assert agents_md.exists()

    def test_agents_md_updated_to_phase_8(self):
        content = Path("/home/admin_1/projects/jarvis/workspace/AGENTS.md").read_text()
        assert "Phase 8" in content
        assert "Brain Independence" in content

    def test_agents_md_references_jarvis_md(self):
        content = Path("/home/admin_1/projects/jarvis/workspace/AGENTS.md").read_text()
        assert "JARVIS.md" in content

    def test_agents_md_references_user_md(self):
        content = Path("/home/admin_1/projects/jarvis/workspace/AGENTS.md").read_text()
        assert "USER.md" in content


class TestExportUserMd:
    def test_export_creates_file(self, user_model, tmp_path):
        user_model.get_or_create("test_user", "TestName")
        # Need enough messages for context
        for i in range(5):
            user_model.update_from_message("test_user", f"Test message {i} about python coding")

        path = user_model.export_user_md("test_user")
        assert path.exists()
        content = path.read_text()
        assert "TestName" in content
        assert "Digital Twin" in content

    def test_export_includes_topics(self, user_model, tmp_path):
        user_model.get_or_create("test_user", "Bi")
        for _ in range(10):
            user_model.update_from_message("test_user", "I need help with python code debugging")

        path = user_model.export_user_md("test_user")
        content = path.read_text()
        assert "programming" in content.lower() or "Frequent Topics" in content

    def test_export_includes_preferences(self, user_model, tmp_path):
        user_model.get_or_create("test_user", "Bi")
        user_model.add_preference("test_user", "language", "Vietnamese")
        user_model.add_preference("test_user", "style", "concise")

        path = user_model.export_user_md("test_user")
        content = path.read_text()
        assert "Vietnamese" in content
        assert "concise" in content

    def test_export_includes_expertise(self, user_model, tmp_path):
        user_model.get_or_create("test_user", "Bi")
        user_model.add_expertise("test_user", "Python", "expert")
        user_model.add_expertise("test_user", "DevOps", "advanced")

        path = user_model.export_user_md("test_user")
        content = path.read_text()
        assert "Python" in content
        assert "expert" in content

    def test_export_overwrites_existing(self, user_model, tmp_path):
        user_model.get_or_create("test_user", "OldName")
        user_model.export_user_md("test_user")

        # Update and re-export
        conn = user_model._ensure_table() or None
        from src.memory.store import get_connection as _gc
        user_model.get_or_create("test_user")
        path = user_model.export_user_md("test_user")
        content = path.read_text()
        assert "Digital Twin" in content  # Still valid markdown

    def test_export_path_is_workspace(self, user_model, tmp_path):
        user_model.get_or_create("test_user", "Bi")
        path = user_model.export_user_md("test_user")
        assert path.parent.name == "workspace"
        assert path.name == "USER.md"
