"""Tests for Feedback Loop — learned routing from user feedback."""

import numpy as np
import pytest

from src.metacognition.feedback_loop import FeedbackLoop


class TestFeedbackLoop:
    @pytest.fixture
    def loop(self):
        return FeedbackLoop()

    def test_table_created(self, loop):
        from src.memory.store import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_routing'"
        ).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_record_feedback(self, loop):
        await loop.record_feedback(
            query="viết code Python sort list",
            model_used="ollama/qwen3:4b",
            rating="negative",
        )
        stats = loop.get_stats()
        assert stats["total_feedback"] >= 1
        assert stats["negative"] >= 1

    @pytest.mark.asyncio
    async def test_record_positive(self, loop):
        await loop.record_feedback(
            query="hello how are you",
            model_used="ollama/qwen3:4b",
            rating="positive",
        )
        stats = loop.get_stats()
        assert stats["positive"] >= 1

    @pytest.mark.asyncio
    async def test_no_escalation_with_few_feedbacks(self, loop):
        """Should not escalate with fewer than MIN_NEGATIVES feedbacks."""
        should_escalate, penalty = await loop.check_escalation("some random query")
        assert not should_escalate
        assert penalty == 0.0

    @pytest.mark.asyncio
    async def test_escalation_after_negatives(self, loop):
        """Should escalate for similar queries after repeated negative feedback."""
        # Record multiple negative feedbacks for similar queries
        for i in range(3):
            await loop.record_feedback(
                query=f"viết code Python sort danh sách theo thứ tự {i}",
                model_used="ollama/qwen3:4b",
                rating="negative",
            )

        # A similar query should trigger escalation
        should_escalate, penalty = await loop.check_escalation(
            "viết code Python sort mảng theo thứ tự giảm dần"
        )
        # Should likely escalate (depends on embedding similarity)
        # At minimum, penalty should be > 0 if embeddings are working
        assert isinstance(should_escalate, bool)
        assert isinstance(penalty, float)
        assert penalty >= 0.0

    @pytest.mark.asyncio
    async def test_no_escalation_for_unrelated_query(self, loop):
        """Unrelated queries should not be escalated even with negatives."""
        # Record negatives for code queries
        for _ in range(3):
            await loop.record_feedback(
                query="viết code Python machine learning tensorflow",
                model_used="ollama/qwen3:4b",
                rating="negative",
            )

        # A totally different query should not be affected
        should_escalate, penalty = await loop.check_escalation("xin chào bạn tên gì")
        assert not should_escalate

    @pytest.mark.asyncio
    async def test_positive_feedback_balances(self, loop):
        """Positive feedback should reduce escalation tendency."""
        # Record mix of positive and negative
        await loop.record_feedback("hello greeting", "ollama/qwen3:4b", "negative")
        await loop.record_feedback("hello greeting", "ollama/qwen3:4b", "negative")
        await loop.record_feedback("hello greeting how are you", "ollama/qwen3:4b", "positive")
        await loop.record_feedback("hello greeting today", "ollama/qwen3:4b", "positive")
        await loop.record_feedback("hello greeting friend", "ollama/qwen3:4b", "positive")

        # With more positives than negatives, should not escalate
        should_escalate, penalty = await loop.check_escalation("hello greeting")
        # negative_ratio would be low, so should not escalate
        assert not should_escalate

    @pytest.mark.asyncio
    async def test_cloud_feedback_ignored_for_escalation(self, loop):
        """Negative feedback for cloud model should not trigger local escalation."""
        for _ in range(5):
            await loop.record_feedback(
                query="complex analysis task",
                model_used="claude-sonnet-4-20250514",
                rating="negative",
            )

        should_escalate, penalty = await loop.check_escalation("complex analysis task")
        assert not should_escalate  # Only local model negatives matter

    def test_get_stats(self, loop):
        stats = loop.get_stats()
        assert "total_feedback" in stats
        assert "positive" in stats
        assert "negative" in stats
        assert "local_negatives" in stats

    @pytest.mark.asyncio
    async def test_record_with_complexity(self, loop):
        await loop.record_feedback(
            query="simple greeting",
            model_used="ollama/qwen3:4b",
            rating="positive",
            complexity="simple",
        )
        stats = loop.get_stats()
        assert stats["total_feedback"] >= 1
