"""Tests for Skill Auto-Generator."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.skills.generator import SkillGenerator


@pytest.fixture
def generator(tmp_path):
    """Create a SkillGenerator with temp directories."""
    gen = SkillGenerator(min_occurrences=2, similarity_threshold=0.70)
    gen._raw_dir = tmp_path / "raw"
    gen._skills_dir = tmp_path / "skills" / "auto"
    gen._raw_dir.mkdir(parents=True)
    gen._skills_dir.mkdir(parents=True)
    return gen


def _write_interactions(raw_dir: Path, records: list[dict]) -> None:
    """Write test interactions to a JSONL file."""
    path = raw_dir / "interactions-test.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class TestSkillGenerator:
    def test_load_empty(self, generator):
        records = generator._load_interactions()
        assert records == []

    def test_load_interactions(self, generator):
        _write_interactions(generator._raw_dir, [
            {"user_message": "hello", "model_response": "hi there!"},
            {"user_message": "how are you", "model_response": "I'm good!"},
        ])
        records = generator._load_interactions()
        assert len(records) == 2

    def test_extract_topic(self, generator):
        messages = [
            "Help me write Python code",
            "I need Python programming help",
            "Debug my Python script",
        ]
        topic = generator._extract_topic(messages)
        assert topic  # Should extract something
        assert "python" in topic.lower()

    def test_extract_topic_empty(self, generator):
        topic = generator._extract_topic([])
        assert topic == ""

    def test_analyze_cluster(self, generator):
        cluster = [
            {
                "user_message": "Write Python code",
                "model_response": "Here's your code...",
                "reasoning_trace": json.dumps([{"tool": "run_python", "success": True}]),
                "skills_used": ["code-assistant"],
                "confidence_score": 0.9,
            },
            {
                "user_message": "Help with Python",
                "model_response": "Sure, let me help...",
                "reasoning_trace": "",
                "skills_used": [],
                "confidence_score": 0.85,
            },
        ]
        pattern = generator._analyze_cluster(cluster)
        assert pattern is not None
        assert pattern["count"] == 2
        assert len(pattern["examples"]) == 2

    def test_generate_skill(self, generator):
        pattern = {
            "topic": "test-python-coding",
            "count": 5,
            "examples": ["Write Python code", "Help with Python"],
            "tools_used": {"run_python": 3},
            "skills_used": {},
            "avg_confidence": 0.88,
            "models_used": [("claude-sonnet", 5)],
        }

        path = generator.generate_skill(pattern)
        assert path is not None
        assert Path(path).exists()

        content = Path(path).read_text()
        assert "test-python-coding" in content
        assert "auto_generated: true" in content
        assert "run_python" in content

    def test_generate_skill_already_exists(self, generator):
        pattern = {"topic": "existing-skill", "count": 3, "examples": [],
                   "tools_used": {}, "skills_used": {}, "avg_confidence": 0.8,
                   "models_used": []}

        # Create first
        path1 = generator.generate_skill(pattern)
        assert path1 is not None

        # Try again — should return None
        path2 = generator.generate_skill(pattern)
        assert path2 is None

    @pytest.mark.asyncio
    async def test_detect_patterns_insufficient_data(self, generator):
        """Not enough data → no patterns."""
        _write_interactions(generator._raw_dir, [
            {"user_message": "hello", "model_response": "hi there world!"},
        ])
        patterns = await generator.detect_patterns()
        assert patterns == []

    @pytest.mark.asyncio
    async def test_run_pipeline(self, generator):
        """Test full pipeline with mocked embeddings."""
        # Create enough similar interactions
        records = []
        for i in range(4):
            records.append({
                "user_message": f"Help me with Python code example {i}",
                "model_response": f"Here's your Python code example {i} with detailed explanation and implementation.",
                "reasoning_trace": "",
                "skills_used": [],
                "confidence_score": 0.9,
                "model_used": "claude",
            })
        _write_interactions(generator._raw_dir, records)

        # Mock embeddings to be very similar
        base = np.ones(384, dtype=np.float32) / np.sqrt(384)
        call_count = 0

        async def mock_embedding(text):
            nonlocal call_count
            call_count += 1
            noise = np.random.RandomState(42).randn(384).astype(np.float32) * 0.01
            return base + noise

        with patch("src.skills.generator.get_embedding", side_effect=mock_embedding):
            stats = await generator.run()

        assert stats["patterns_detected"] >= 1
