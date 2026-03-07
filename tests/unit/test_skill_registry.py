"""Tests for Skill Registry."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from src.skills.loader import Skill, SkillLoader, SkillMetadata
from src.skills.registry import SkillRegistry


@pytest.fixture
def loader():
    """Create a SkillLoader with mock skills."""
    loader = SkillLoader.__new__(SkillLoader)
    loader._skills_dir = Path("/tmp/test_skills")
    loader._skills = {}

    # Add test skills
    for name, desc, priority in [
        ("code-assistant", "Help with programming and code review", 0.9),
        ("web-research", "Search the web for information", 0.85),
        ("general-chat", "General conversation and chat", 0.5),
    ]:
        meta = SkillMetadata(
            name=name,
            description=desc,
            priority=priority,
        )
        loader._skills[name] = Skill(metadata=meta, body=f"# {name}")

    return loader


@pytest.fixture
def registry(loader, tmp_path):
    """Create a SkillRegistry with isolated metrics (no state from previous runs)."""
    reg = SkillRegistry(loader)
    reg._metrics_file = tmp_path / "metrics.json"
    reg._metrics = {}  # Clear any loaded metrics from real data dir
    return reg


class TestSkillRegistry:
    def test_get_all(self, registry):
        skills = registry.get_all()
        assert len(skills) == 3

    def test_get_skill(self, registry):
        skill = registry.get_skill("code-assistant")
        assert skill is not None
        assert skill.metadata.name == "code-assistant"

    def test_get_skill_not_found(self, registry):
        assert registry.get_skill("nonexistent") is None

    def test_get_all_metadata(self, registry):
        metadata = registry.get_all_metadata()
        assert len(metadata) == 3
        names = {m.name for m in metadata}
        assert "code-assistant" in names

    def test_record_usage(self, registry):
        registry.record_usage("code-assistant", success=True)
        assert registry._metrics["code-assistant"]["usage_count"] == 1
        assert registry._metrics["code-assistant"]["success_count"] == 1

    def test_record_usage_failure(self, registry):
        registry.record_usage("code-assistant", success=False)
        assert registry._metrics["code-assistant"]["fail_count"] == 1

    def test_record_usage_updates_metadata(self, registry):
        registry.record_usage("code-assistant", success=True)
        registry.record_usage("code-assistant", success=True)
        registry.record_usage("code-assistant", success=False)

        skill = registry.get_skill("code-assistant")
        assert skill.metadata.usage_count == 3
        assert abs(skill.metadata.success_rate - 2/3) < 0.01

    def test_get_top_skills(self, registry):
        registry.record_usage("code-assistant")
        registry.record_usage("code-assistant")
        registry.record_usage("web-research")

        top = registry.get_top_skills(2)
        assert len(top) == 2
        assert top[0][0] == "code-assistant"
        assert top[0][1]["usage_count"] == 2

    def test_get_unused_skills(self, registry):
        # All skills unused initially
        unused = registry.get_unused_skills(days=30)
        assert len(unused) == 3

    def test_get_stats(self, registry):
        registry.record_usage("code-assistant")
        stats = registry.get_stats()
        assert stats["total_skills"] == 3
        assert stats["total_usage"] == 1
        assert stats["tracked_skills"] == 1

    def test_metrics_persistence(self, registry):
        registry.record_usage("code-assistant")
        registry.record_usage("web-research")

        # Save happens in record_usage, now load
        loaded = registry._load_metrics()
        assert "code-assistant" in loaded
        assert loaded["code-assistant"]["usage_count"] == 1

    def test_apply_metrics(self, registry):
        # Manually set metrics
        registry._metrics = {
            "code-assistant": {
                "usage_count": 10,
                "success_count": 8,
                "fail_count": 2,
                "last_used": "2026-03-04T00:00:00+00:00",
            }
        }
        registry._apply_metrics()

        skill = registry.get_skill("code-assistant")
        assert skill.metadata.usage_count == 10
        assert abs(skill.metadata.success_rate - 0.8) < 0.01

    @pytest.mark.asyncio
    async def test_search(self, registry):
        """Test semantic search (with mocked embeddings)."""
        import numpy as np

        # Use similar vectors so cosine similarity is high
        base = np.ones(384, dtype=np.float32) / np.sqrt(384)

        call_count = 0
        async def mock_embedding(text):
            nonlocal call_count
            call_count += 1
            # Add small noise so vectors aren't identical
            noise = np.random.RandomState(call_count).randn(384).astype(np.float32) * 0.1
            return base + noise

        with patch("src.skills.registry.get_embedding", side_effect=mock_embedding):
            # Clear cache to force re-embedding
            from src.skills import registry as reg_mod
            reg_mod._embedding_cache.clear()
            results = await registry.search("help me code", threshold=0.0)
            assert len(results) > 0
            # Results are (score, skill) tuples
            assert all(isinstance(r[0], float) for r in results)
            assert all(isinstance(r[1], Skill) for r in results)
