"""Tests for Skill Loader — YAML parsing, metadata extraction."""

from pathlib import Path
from textwrap import dedent

from src.skills.loader import SkillLoader, SkillMetadata


class TestSkillLoader:
    def test_split_frontmatter_valid(self):
        content = dedent("""\
        ---
        name: test-skill
        description: A test skill
        version: 1.0.0
        ---

        # Test Skill Body
        This is the body.
        """)
        fm, body = SkillLoader._split_frontmatter(content)
        assert fm is not None
        assert fm["name"] == "test-skill"
        assert fm["description"] == "A test skill"
        assert "# Test Skill Body" in body

    def test_split_frontmatter_no_yaml(self):
        content = "# Just Markdown\nNo frontmatter here."
        fm, body = SkillLoader._split_frontmatter(content)
        assert fm is None
        assert "Just Markdown" in body

    def test_split_frontmatter_invalid_yaml(self):
        content = "---\n: bad: yaml: here\n---\nBody."
        fm, body = SkillLoader._split_frontmatter(content)
        # Should handle gracefully
        assert isinstance(body, str)

    def test_parse_skill_with_jarvis_metadata(self):
        loader = SkillLoader()
        # Create a temp SKILL.md
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(dedent("""\
            ---
            name: test-skill
            description: Testing skill
            version: 2.0.0
            metadata:
              jarvis:
                emoji: "🧪"
                category: testing
                priority: 0.9
                success_rate: 0.95
            ---

            # Test Skill
            Workflow goes here.
            """), encoding="utf-8")

            skill = loader._parse_skill(skill_file)
            assert skill is not None
            assert skill.metadata.name == "test-skill"
            assert skill.metadata.emoji == "🧪"
            assert skill.metadata.category == "testing"
            assert skill.metadata.priority == 0.9
            assert skill.metadata.success_rate == 0.95
            assert "Workflow goes here" in skill.body


class TestSkillMetadata:
    def test_defaults(self):
        meta = SkillMetadata(name="test", description="A test")
        assert meta.version == "1.0.0"
        assert meta.category == "general"
        assert meta.priority == 0.5
        assert meta.success_rate == 1.0
        assert meta.usage_count == 0
        assert meta.auto_generated is False

    def test_metadata_summary(self):
        loader = SkillLoader()
        # Manually add a skill
        from src.skills.loader import Skill
        loader._skills["test"] = Skill(
            metadata=SkillMetadata(
                name="test-skill",
                description="A test skill",
                emoji="🧪",
                priority=0.8,
            ),
            body="Body here",
        )
        summary = loader.get_metadata_summary()
        assert "test-skill" in summary
        assert "A test skill" in summary
        assert "🧪" in summary
