"""Tests for Dreamtime Engine — scheduler, consolidator, dreamer."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from src.dreamtime.scheduler import DreamtimeScheduler
from src.dreamtime.consolidator import MemoryConsolidator
from src.dreamtime.dreamer import Dreamer


# === DreamtimeScheduler Tests ===

class TestDreamtimeScheduler:

    def test_initial_state(self):
        scheduler = DreamtimeScheduler()
        stats = scheduler.get_stats()
        assert stats["enabled"] is True
        assert stats["idle_minutes"] == 30
        assert stats["running"] is False
        assert stats["last_dream"] == "never"

    def test_disabled_scheduler(self):
        scheduler = DreamtimeScheduler(enabled=False)
        assert scheduler.get_stats()["enabled"] is False

    def test_record_activity_updates_timestamp(self):
        scheduler = DreamtimeScheduler()
        before = scheduler._last_activity
        scheduler.record_activity()
        assert scheduler._last_activity >= before

    def test_set_dream_callback(self):
        scheduler = DreamtimeScheduler()
        callback = AsyncMock()
        scheduler.set_dream_callback(callback)
        assert scheduler._dream_callback is callback

    @pytest.mark.asyncio
    async def test_run_now_no_callback(self):
        scheduler = DreamtimeScheduler()
        result = await scheduler.run_now()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_run_now_with_callback(self):
        scheduler = DreamtimeScheduler()
        callback = AsyncMock(return_value={"status": "ok", "consolidated": 5})
        scheduler.set_dream_callback(callback)

        result = await scheduler.run_now()
        assert result == {"status": "ok", "consolidated": 5}
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_now_callback_error(self):
        scheduler = DreamtimeScheduler()
        callback = AsyncMock(side_effect=RuntimeError("Dream failed"))
        scheduler.set_dream_callback(callback)

        result = await scheduler.run_now()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_start_disabled(self):
        scheduler = DreamtimeScheduler(enabled=False)
        await scheduler.start()
        assert scheduler._running is False
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_start_stop(self):
        scheduler = DreamtimeScheduler()
        await scheduler.start()
        assert scheduler._running is True
        assert scheduler._task is not None

        await scheduler.stop()
        assert scheduler._running is False


# === MemoryConsolidator Tests ===

class TestMemoryConsolidator:

    def _make_memory(self, id, content, importance=0.5, access_count=0, days_old=0):
        created = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        return {
            "id": id,
            "content": content,
            "importance": importance,
            "access_count": access_count,
            "created_at": created,
        }

    @pytest.mark.asyncio
    async def test_consolidate_empty(self):
        mock_memory = MagicMock()
        mock_memory.get_all.return_value = []
        consolidator = MemoryConsolidator(semantic_memory=mock_memory)

        stats = await consolidator.consolidate()
        assert stats["total_before"] == 0
        assert stats["total_after"] == 0

    @pytest.mark.asyncio
    async def test_consolidate_single_memory(self):
        mock_memory = MagicMock()
        mock_memory.get_all.return_value = [self._make_memory("m1", "hello")]
        consolidator = MemoryConsolidator(semantic_memory=mock_memory)

        stats = await consolidator.consolidate()
        assert stats["total_before"] == 1
        assert stats["total_after"] == 1

    def test_strengthen_frequent(self):
        mock_memory = MagicMock()
        memories = [
            self._make_memory("m1", "important fact", importance=0.5, access_count=10),
            self._make_memory("m2", "rarely used", importance=0.5, access_count=1),
        ]
        consolidator = MemoryConsolidator(semantic_memory=mock_memory)

        count = consolidator._strengthen_frequent(memories)
        assert count == 1  # Only m1 has enough accesses
        mock_memory.update_importance.assert_called_once_with("m1", 0.55)

    def test_strengthen_already_high(self):
        mock_memory = MagicMock()
        memories = [
            self._make_memory("m1", "top memory", importance=0.95, access_count=10),
        ]
        consolidator = MemoryConsolidator(semantic_memory=mock_memory)

        count = consolidator._strengthen_frequent(memories)
        assert count == 0  # Already at max

    def test_decay_old_memories(self):
        mock_memory = MagicMock()
        memories = [
            self._make_memory("m1", "old unused", importance=0.5, access_count=0, days_old=60),
            self._make_memory("m2", "recent", importance=0.5, access_count=0, days_old=5),
            self._make_memory("m3", "old but important", importance=0.95, access_count=0, days_old=60),
        ]
        consolidator = MemoryConsolidator(semantic_memory=mock_memory, decay_days=30)

        count = consolidator._decay_old(memories)
        assert count == 1  # Only m1 should decay (m2 is recent, m3 is high importance)
        mock_memory.update_importance.assert_called_once_with("m1", 0.4)

    def test_decay_respects_min_importance(self):
        mock_memory = MagicMock()
        memories = [
            self._make_memory("m1", "already low", importance=0.15, access_count=0, days_old=60),
        ]
        consolidator = MemoryConsolidator(
            semantic_memory=mock_memory, decay_days=30, min_importance=0.1,
        )

        count = consolidator._decay_old(memories)
        assert count == 1
        mock_memory.update_importance.assert_called_once_with("m1", 0.1)


# === Dreamer Tests ===

class TestDreamer:

    def _make_skill(self, name, usage_count, success_rate):
        skill = MagicMock()
        skill.metadata.name = name
        skill.metadata.usage_count = usage_count
        skill.metadata.success_rate = success_rate
        return skill

    def test_analyze_skills(self):
        collector = MagicMock()
        registry = MagicMock()
        registry.get_all.return_value = [
            self._make_skill("good-skill", usage_count=20, success_rate=0.95),
            self._make_skill("bad-skill", usage_count=10, success_rate=0.45),
            self._make_skill("new-skill", usage_count=0, success_rate=0.0),
        ]

        dreamer = Dreamer(collector=collector, skill_registry=registry)
        results = dreamer._analyze_skills()

        # bad-skill should be marked as underperforming
        assert any(r["name"] == "bad-skill" and r["status"] == "underperforming" for r in results)
        # good-skill should be healthy
        assert any(r["name"] == "good-skill" and r["status"] == "healthy" for r in results)
        # new-skill should be excluded (0 usage)
        assert not any(r["name"] == "new-skill" for r in results)

    def test_generate_suggestions(self):
        collector = MagicMock()
        registry = MagicMock()
        registry.get_unused_skills.return_value = []

        dreamer = Dreamer(collector=collector, skill_registry=registry)

        report = {
            "skill_analysis": [
                {"name": "bad-skill", "usage_count": 10, "success_rate": 0.45, "status": "underperforming"},
            ],
            "failure_patterns": [
                {"pattern": "search for something", "count": 5},
            ],
        }

        suggestions = dreamer._generate_suggestions(report)
        assert len(suggestions) >= 2
        assert any("bad-skill" in s for s in suggestions)
        assert any("search for something" in s for s in suggestions)

    @pytest.mark.asyncio
    async def test_dream_full_cycle(self, tmp_path):
        collector = MagicMock()
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_unused_skills.return_value = []

        dreamer = Dreamer(collector=collector, skill_registry=registry)

        async def mock_detect():
            return []

        with patch("src.dreamtime.dreamer.get_project_root", return_value=tmp_path):
            with patch.object(dreamer, "_detect_skill_candidates", side_effect=mock_detect):
                report = await dreamer.dream()

        assert "skill_analysis" in report
        assert "failure_patterns" in report
        assert "improvement_suggestions" in report
        assert "new_skill_candidates" in report

        # Report should be saved
        reports_dir = tmp_path / "data" / "dreamtime"
        assert reports_dir.exists()
