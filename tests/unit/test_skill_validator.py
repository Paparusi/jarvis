"""Tests for Skill Validator, Loader, and Generator — Anthropic Agent Skills Spec."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.skills.loader import Skill, SkillLoader, SkillMetadata
from src.skills.validator import (
    SkillValidator,
    ValidationResult,
    _DESC_MAX_LEN,
    _NAME_MAX_LEN,
    _NAME_PATTERN,
)
from src.skills.generator import SkillGenerator


# === Name pattern ===

class TestNamePattern:
    def test_valid_names(self):
        for name in ["web-research", "mcp-builder", "a", "my-skill-123", "code-assistant"]:
            assert _NAME_PATTERN.match(name), f"{name} should be valid"

    def test_invalid_names(self):
        for name in ["Web-Research", "mcp_builder", "-leading", "trailing-", "a--b", "có-dấu"]:
            assert not _NAME_PATTERN.match(name), f"{name} should be invalid"

    def test_max_length(self):
        assert _NAME_MAX_LEN == 64

    def test_desc_max_length(self):
        assert _DESC_MAX_LEN == 1024


# === SkillMetadata new fields ===

class TestSkillMetadataFields:
    def test_default_new_fields(self):
        meta = SkillMetadata(name="test", description="test skill")
        assert meta.license == ""
        assert meta.compatibility == {}
        assert meta.allowed_tools == []

    def test_set_new_fields(self):
        meta = SkillMetadata(
            name="test",
            description="test skill",
            license="MIT",
            compatibility={"claude-code": ">=1.0.0"},
            allowed_tools=["Bash", "Read"],
        )
        assert meta.license == "MIT"
        assert meta.compatibility == {"claude-code": ">=1.0.0"}
        assert meta.allowed_tools == ["Bash", "Read"]


# === SkillValidator — name validation ===

class TestValidatorNameRules:
    def setup_method(self):
        self.validator = SkillValidator()

    def test_valid_name_passes(self):
        skill = Skill(
            metadata=SkillMetadata(name="web-research", description="Research the web"),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert result.valid
        name_warnings = [w for w in result.warnings if "Name" in w or "name" in w]
        assert len(name_warnings) == 0

    def test_uppercase_name_warns(self):
        skill = Skill(
            metadata=SkillMetadata(name="Web-Research", description="Research the web"),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert any("lowercase" in w for w in result.warnings)

    def test_underscore_name_warns(self):
        skill = Skill(
            metadata=SkillMetadata(name="web_research", description="Test"),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert any("lowercase" in w or "hyphens" in w for w in result.warnings)

    def test_vietnamese_name_warns(self):
        skill = Skill(
            metadata=SkillMetadata(name="tìm-kiếm", description="Test"),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert any("lowercase" in w for w in result.warnings)

    def test_too_long_name_errors(self):
        long_name = "a" * 65
        skill = Skill(
            metadata=SkillMetadata(name=long_name, description="Test"),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert not result.valid
        assert any("exceeds" in e and "64" in e for e in result.errors)

    def test_leading_hyphen_warns(self):
        skill = Skill(
            metadata=SkillMetadata(name="-bad-name", description="Test"),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert any("lowercase" in w for w in result.warnings)

    def test_consecutive_hyphens_warns(self):
        skill = Skill(
            metadata=SkillMetadata(name="bad--name", description="Test"),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert any("lowercase" in w for w in result.warnings)

    def test_empty_name_errors(self):
        skill = Skill(
            metadata=SkillMetadata(name="", description="Test"),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert not result.valid
        assert any("Missing skill name" in e for e in result.errors)


# === SkillValidator — description validation ===

class TestValidatorDescriptionRules:
    def setup_method(self):
        self.validator = SkillValidator()

    def test_normal_description_ok(self):
        skill = Skill(
            metadata=SkillMetadata(name="test", description="A normal description"),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        desc_warnings = [w for w in result.warnings if "Description" in w]
        assert len(desc_warnings) == 0

    def test_too_long_description_warns(self):
        long_desc = "x" * 1025
        skill = Skill(
            metadata=SkillMetadata(name="test", description=long_desc),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert any("Description" in w and "1024" in w for w in result.warnings)

    def test_empty_description_errors(self):
        skill = Skill(
            metadata=SkillMetadata(name="test", description=""),
            body="## Workflow\n\nSome workflow content here that is long enough.",
        )
        result = self.validator.validate_skill(skill)
        assert not result.valid
        assert any("Missing skill description" in e for e in result.errors)


# === SkillValidator — frontmatter validation ===

class TestValidatorFrontmatter:
    def setup_method(self):
        self.validator = SkillValidator()

    def test_valid_frontmatter(self):
        content = textwrap.dedent("""\
        ---
        name: web-research
        description: Research the web
        version: 1.0.0
        metadata:
          jarvis:
            category: productivity
        ---

        # Web Research

        ## Workflow
        1. Search the web
        2. Return results
        """)
        result = ValidationResult(skill_name="web-research")
        self.validator._validate_frontmatter(content, result)
        assert result.valid
        assert len(result.errors) == 0

    def test_missing_frontmatter(self):
        content = "# Just a heading\n\nSome content."
        result = ValidationResult(skill_name="test")
        self.validator._validate_frontmatter(content, result)
        assert not result.valid

    def test_uppercase_name_in_frontmatter_warns(self):
        content = textwrap.dedent("""\
        ---
        name: Web-Research
        description: Test
        ---

        ## Workflow
        Content.
        """)
        result = ValidationResult(skill_name="Web-Research")
        self.validator._validate_frontmatter(content, result)
        assert any("lowercase" in w for w in result.warnings)

    def test_missing_name_field(self):
        content = textwrap.dedent("""\
        ---
        description: Test
        ---

        ## Workflow
        Content.
        """)
        result = ValidationResult(skill_name="test")
        self.validator._validate_frontmatter(content, result)
        assert not result.valid
        assert any("name" in e for e in result.errors)


# === SkillLoader — new fields parsing ===

class TestLoaderNewFields:
    def test_parse_license(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(textwrap.dedent("""\
        ---
        name: test-skill
        description: A test skill
        version: 1.0.0
        license: MIT
        ---

        # Test Skill

        ## Workflow
        1. Do something
        """))

        loader = SkillLoader()
        skill = loader._parse_skill(skill_file)
        assert skill is not None
        assert skill.metadata.license == "MIT"

    def test_parse_compatibility(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(textwrap.dedent("""\
        ---
        name: test-skill
        description: A test skill
        compatibility:
          claude-code: ">=1.0.0"
        ---

        # Test Skill

        ## Workflow
        1. Do something
        """))

        loader = SkillLoader()
        skill = loader._parse_skill(skill_file)
        assert skill is not None
        assert skill.metadata.compatibility == {"claude-code": ">=1.0.0"}

    def test_parse_allowed_tools(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(textwrap.dedent("""\
        ---
        name: test-skill
        description: A test skill
        allowed-tools:
          - Bash
          - Read
          - Write
        ---

        # Test Skill

        ## Workflow
        1. Do something
        """))

        loader = SkillLoader()
        skill = loader._parse_skill(skill_file)
        assert skill is not None
        assert skill.metadata.allowed_tools == ["Bash", "Read", "Write"]

    def test_missing_new_fields_defaults(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(textwrap.dedent("""\
        ---
        name: test-skill
        description: A test skill
        ---

        # Test Skill

        ## Workflow
        1. Do something
        """))

        loader = SkillLoader()
        skill = loader._parse_skill(skill_file)
        assert skill is not None
        assert skill.metadata.license == ""
        assert skill.metadata.compatibility == {}
        assert skill.metadata.allowed_tools == []


# === SkillGenerator — name normalization ===

class TestGeneratorNormalization:
    def test_ascii_passthrough(self):
        assert SkillGenerator._normalize_name("web-research") == "web-research"

    def test_vietnamese_stripped(self):
        assert SkillGenerator._normalize_name("tìm-kiếm-tin") == "tim-kiem-tin"

    def test_d_bar(self):
        assert SkillGenerator._normalize_name("đang-model") == "dang-model"

    def test_uppercase_lowered(self):
        assert SkillGenerator._normalize_name("Web-Research") == "web-research"

    def test_underscores_to_hyphens(self):
        assert SkillGenerator._normalize_name("web_research") == "web-research"

    def test_spaces_to_hyphens(self):
        assert SkillGenerator._normalize_name("web research tool") == "web-research-tool"

    def test_consecutive_hyphens_collapsed(self):
        assert SkillGenerator._normalize_name("a--b---c") == "a-b-c"

    def test_leading_trailing_stripped(self):
        assert SkillGenerator._normalize_name("-bad-name-") == "bad-name"

    def test_max_64_chars(self):
        long_name = "a" * 100
        result = SkillGenerator._normalize_name(long_name)
        assert len(result) <= 64

    def test_mixed_unicode(self):
        result = SkillGenerator._normalize_name("café-résumé")
        assert result == "cafe-resume"

    def test_empty_string(self):
        assert SkillGenerator._normalize_name("") == ""

    def test_only_special_chars(self):
        assert SkillGenerator._normalize_name("@#$%") == ""


# === Validate existing SKILL.md files ===

class TestExistingSkillsCompliance:
    """Validate all existing SKILL.md files pass the new rules."""

    def setup_method(self):
        from src.utils.config import get_project_root
        self.skills_dir = get_project_root() / "workspace" / "skills"
        self.validator = SkillValidator()
        self.loader = SkillLoader()

    def test_all_skills_load(self):
        skills = self.loader.load_all()
        assert len(skills) > 0

    def test_all_skills_valid_structure(self):
        """All SKILL.md files should pass structural validation."""
        errors = []
        for skill_md in self.skills_dir.rglob("SKILL.md"):
            result = self.validator.validate_file(skill_md)
            if not result.valid:
                errors.append(f"{skill_md}: {result.errors}")
        assert errors == [], f"Invalid skills: {errors}"

    def test_no_name_exceeds_64(self):
        """No skill name should exceed 64 characters."""
        skills = self.loader.load_all()
        for name, skill in skills.items():
            assert len(name) <= 64, f"Skill '{name}' exceeds 64 chars"

    def test_name_format_compliance(self):
        """All skill names should match lowercase-hyphens pattern.

        Note: this is a warning, not blocking — existing skills may
        still have non-standard names.
        """
        skills = self.loader.load_all()
        non_compliant = []
        for name in skills:
            if not _NAME_PATTERN.match(name):
                non_compliant.append(name)
        # After our fixes, all should comply
        assert non_compliant == [], f"Non-compliant names: {non_compliant}"
