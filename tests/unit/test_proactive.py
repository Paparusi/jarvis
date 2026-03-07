"""Tests for Proactive Intelligence Engine."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.intelligence.proactive import ProactiveEngine, ProactiveInsight


class TestProactiveInsight:
    def test_creation(self):
        insight = ProactiveInsight(
            type="briefing",
            title="Test",
            content="Content",
            priority=0.5,
        )
        assert insight.type == "briefing"
        assert insight.priority == 0.5
        assert isinstance(insight.created_at, datetime)

    def test_defaults(self):
        insight = ProactiveInsight(type="alert", title="T", content="C", priority=1.0)
        assert insight.metadata == {}


class TestProactiveEngine:
    def setup_method(self):
        self.memory = MagicMock()
        self.memory.search = AsyncMock(return_value=[])
        self.memory.get_all = MagicMock(return_value=[])
        self.user_model = MagicMock()
        self.user_model.get_or_create = MagicMock(return_value={
            "total_messages": 10,
            "avg_msg_length": 50,
            "language": "vi",
            "topics": '{"python": 5, "docker": 3}',
        })
        self.collector = MagicMock()
        self.collector.get_stats = MagicMock(return_value={
            "total_records": 100,
            "recent_records": 5,
        })
        self.engine = ProactiveEngine(
            self.memory, self.user_model, self.collector,
        )

    def test_init(self):
        assert not self.engine._running
        assert len(self.engine._callbacks) == 0

    def test_on_notify(self):
        cb = AsyncMock()
        self.engine.on_notify(cb)
        assert len(self.engine._callbacks) == 1

    def test_mark_interaction(self):
        import time
        old = self.engine._last_interaction
        time.sleep(0.01)
        self.engine.mark_interaction()
        assert self.engine._last_interaction > old

    def test_briefing_not_sent_initially(self):
        assert not self.engine._briefing_sent_today()

    def test_get_stats(self):
        stats = self.engine.get_stats()
        assert "running" in stats
        assert "idle_minutes" in stats
        assert stats["running"] is False

    @pytest.mark.asyncio
    async def test_generate_briefing(self):
        cb = AsyncMock()
        self.engine.on_notify(cb)
        await self.engine._generate_briefing()
        assert cb.called
        insight = cb.call_args[0][0]
        assert insight.type == "briefing"
        assert "buổi sáng" in insight.title.lower() or "briefing" in insight.title.lower()

    @pytest.mark.asyncio
    async def test_generate_idle_insight(self):
        cb = AsyncMock()
        self.engine.on_notify(cb)
        await self.engine._generate_idle_insight()
        assert cb.called
        insight = cb.call_args[0][0]
        assert insight.type == "insight"

    @pytest.mark.asyncio
    async def test_idle_insight_dedup(self):
        cb = AsyncMock()
        self.engine.on_notify(cb)
        await self.engine._generate_idle_insight()
        await self.engine._generate_idle_insight()
        # Should only notify once (dedup)
        assert cb.call_count == 1

    @pytest.mark.asyncio
    async def test_generate_followup(self):
        self.memory.search = AsyncMock(return_value=[
            {"content": "test", "score": 0.9}
        ])
        insight = await self.engine.generate_followup("python")
        assert insight is not None
        assert insight.type == "followup"

    @pytest.mark.asyncio
    async def test_generate_followup_empty(self):
        insight = await self.engine.generate_followup("")
        assert insight is None

    @pytest.mark.asyncio
    async def test_check_alerts(self):
        alerts = await self.engine.check_alerts()
        assert isinstance(alerts, list)

    @pytest.mark.asyncio
    async def test_start_stop(self):
        await self.engine.start()
        assert self.engine._running
        await self.engine.stop()
        assert not self.engine._running


class TestContextCompression:
    """Test progressive compression in ConversationSummarizer."""

    def setup_method(self):
        from src.memory.summarizer import ConversationSummarizer
        self.summarizer = ConversationSummarizer()

    def test_no_compression_short(self):
        msgs = [{"role": "user", "content": "hi"}] * 5
        assert self.summarizer.compression_level(msgs) == 0

    def test_level_1_medium(self):
        msgs = [{"role": "user", "content": "hi"}] * 25
        assert self.summarizer.compression_level(msgs) == 1

    def test_level_2_long(self):
        msgs = [{"role": "user", "content": "hi"}] * 75
        assert self.summarizer.compression_level(msgs) == 2

    def test_level_3_very_long(self):
        msgs = [{"role": "user", "content": "hi"}] * 150
        assert self.summarizer.compression_level(msgs) == 3

    def test_needs_summarization_false(self):
        msgs = [{"role": "user", "content": "hi"}] * 10
        assert not self.summarizer.needs_summarization(msgs)

    def test_needs_summarization_true(self):
        msgs = [{"role": "user", "content": "hi"}] * 25
        assert self.summarizer.needs_summarization(msgs)
