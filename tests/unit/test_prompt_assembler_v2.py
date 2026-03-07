"""Tests for Prompt Assembler v2 — .md-based identity injection."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.intelligence.prompt_assembler import (
    PromptAssembler,
    _build_system_prompt_from_md,
    _load_md_file,
    _TOOL_INSTRUCTIONS,
    _FALLBACK_SYSTEM_PROMPT,
    BASE_SYSTEM_PROMPT,
    estimate_tokens,
)


class TestLoadMdFile:
    def test_loads_existing_file(self, tmp_path, monkeypatch):
        (tmp_path / "workspace").mkdir()
        md = tmp_path / "workspace" / "TEST.md"
        md.write_text("# Test Content", encoding="utf-8")
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        result = _load_md_file("TEST.md")
        assert result == "# Test Content"

    def test_returns_none_for_missing_file(self, tmp_path, monkeypatch):
        (tmp_path / "workspace").mkdir()
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        result = _load_md_file("NONEXISTENT.md")
        assert result is None

    def test_handles_error_gracefully(self, monkeypatch):
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root",
                           lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        result = _load_md_file("TEST.md")
        assert result is None


class TestBuildSystemPromptFromMd:
    def test_builds_from_jarvis_md(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "JARVIS.md").write_text("# JARVIS\nI am JARVIS.", encoding="utf-8")
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        prompt = _build_system_prompt_from_md()
        assert "JARVIS" in prompt
        assert "TOOL CALLING" in prompt  # Tool instructions always appended

    def test_includes_user_md(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "JARVIS.md").write_text("# JARVIS Identity", encoding="utf-8")
        (ws / "USER.md").write_text("# USER\nName: Bi", encoding="utf-8")
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        prompt = _build_system_prompt_from_md()
        assert "JARVIS Identity" in prompt
        assert "Name: Bi" in prompt

    def test_falls_back_without_jarvis_md(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        prompt = _build_system_prompt_from_md()
        assert "JARVIS" in prompt  # fallback still mentions JARVIS
        assert "TOOL CALLING" in prompt

    def test_works_without_user_md(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "JARVIS.md").write_text("# JARVIS Soul", encoding="utf-8")
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        prompt = _build_system_prompt_from_md()
        assert "JARVIS Soul" in prompt
        assert "USER" not in prompt  # No USER.md, no user section


class TestPromptAssemblerV2:
    def test_uses_md_files_by_default(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "JARVIS.md").write_text("# JARVIS v2 Identity", encoding="utf-8")
        (ws / "USER.md").write_text("# USER Profile\nOwner: Bi", encoding="utf-8")
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        assembler = PromptAssembler()
        assert "JARVIS v2 Identity" in assembler._system_prompt
        assert "Owner: Bi" in assembler._system_prompt

    def test_override_skips_md_files(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "JARVIS.md").write_text("# Should not appear", encoding="utf-8")
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        assembler = PromptAssembler(system_prompt_override="Custom prompt")
        assert assembler._system_prompt == "Custom prompt"
        assert "Should not appear" not in assembler._system_prompt

    @pytest.mark.asyncio
    async def test_assemble_includes_md_content(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "JARVIS.md").write_text("# JARVIS\nToi la JARVIS.", encoding="utf-8")
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        assembler = PromptAssembler()
        messages = await assembler.assemble(
            user_message="Hello",
            history=[],
        )

        system_msg = messages[0]
        assert system_msg["role"] == "system"
        assert "JARVIS" in system_msg["content"]
        assert "Toi la JARVIS" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_assemble_includes_memory(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "JARVIS.md").write_text("# JARVIS", encoding="utf-8")
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        assembler = PromptAssembler()
        messages = await assembler.assemble(
            user_message="Test",
            history=[],
            memory_context="User likes Python programming",
        )

        system_content = messages[0]["content"]
        assert "User likes Python programming" in system_content

    @pytest.mark.asyncio
    async def test_assemble_includes_skills(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "JARVIS.md").write_text("# JARVIS", encoding="utf-8")
        monkeypatch.setattr("src.intelligence.prompt_assembler.get_project_root", lambda: tmp_path)

        assembler = PromptAssembler()
        messages = await assembler.assemble(
            user_message="Search web",
            history=[],
            skill_context="## Web Research Skill\nSearch and summarize",
        )

        system_content = messages[0]["content"]
        assert "Web Research Skill" in system_content


class TestBackwardCompatibility:
    def test_base_system_prompt_still_exported(self):
        """BASE_SYSTEM_PROMPT should still be available for imports."""
        assert "JARVIS" in BASE_SYSTEM_PROMPT
        assert "TOOL CALLING" in BASE_SYSTEM_PROMPT

    def test_estimate_tokens_works(self):
        assert estimate_tokens("hello world") > 0
        assert estimate_tokens("") == 0

    def test_tool_instructions_separate(self):
        assert "TOOL CALLING" in _TOOL_INSTRUCTIONS
        assert "web_search" in _TOOL_INSTRUCTIONS


class TestRealJarvisMd:
    """Test with real JARVIS.md if available."""

    def test_real_jarvis_md_loaded(self):
        from src.utils.config import get_project_root
        jarvis_md = get_project_root() / "workspace" / "JARVIS.md"
        if jarvis_md.exists():
            content = _load_md_file("JARVIS.md")
            assert content is not None
            assert "JARVIS" in content
            assert "Personality" in content

    def test_real_prompt_assembler(self):
        from src.utils.config import get_project_root
        jarvis_md = get_project_root() / "workspace" / "JARVIS.md"
        if jarvis_md.exists():
            assembler = PromptAssembler()
            assert "JARVIS" in assembler._system_prompt
            assert len(assembler._system_prompt) > 500  # Should be substantial
