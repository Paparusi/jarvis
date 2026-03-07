"""Tests for Auto-Trainer — Autonomous retraining orchestration."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.brain.auto_trainer import AutoTrainer


@pytest.fixture
def auto_trainer(tmp_path, monkeypatch):
    monkeypatch.setattr("src.brain.auto_trainer.get_project_root", lambda: tmp_path)
    (tmp_path / "data" / "training").mkdir(parents=True)
    return AutoTrainer(min_new_sft=10)


class TestState:
    def test_load_empty_state(self, auto_trainer):
        assert auto_trainer._state["last_train_at"] is None
        assert auto_trainer._state["train_count"] == 0

    def test_save_and_load_state(self, auto_trainer, tmp_path):
        auto_trainer._state["train_count"] = 3
        auto_trainer._state["last_sft_count"] = 100
        auto_trainer._save_state()

        # Create new instance to verify persistence
        with patch("src.brain.auto_trainer.get_project_root", return_value=tmp_path):
            trainer2 = AutoTrainer()
        assert trainer2._state["train_count"] == 3
        assert trainer2._state["last_sft_count"] == 100

    def test_load_corrupt_state(self, auto_trainer):
        auto_trainer._state_path.parent.mkdir(parents=True, exist_ok=True)
        auto_trainer._state_path.write_text("not json{{{")
        state = auto_trainer._load_state()
        assert state["train_count"] == 0


class TestShouldRetrain:
    def test_not_enough_total_data(self, auto_trainer):
        with patch.object(auto_trainer, "should_retrain") as mock:
            # Actually test real method
            pass

        # Use real method with mocked pipeline
        mock_pipeline = MagicMock()
        mock_pipeline.get_dataset_stats.return_value = {
            "sft_records": 50, "dpo_records": 0,
        }
        with patch("src.brain.auto_trainer.TrainingPipeline", return_value=mock_pipeline):
            should, reason = auto_trainer.should_retrain()
        assert should is False
        assert "Not enough total" in reason

    def test_not_enough_new_data(self, auto_trainer):
        auto_trainer._state["last_sft_count"] = 95
        mock_pipeline = MagicMock()
        mock_pipeline.get_dataset_stats.return_value = {
            "sft_records": 100, "dpo_records": 0,
        }
        with patch("src.brain.auto_trainer.TrainingPipeline", return_value=mock_pipeline):
            should, reason = auto_trainer.should_retrain()
        assert should is False
        assert "new SFT records since last train" in reason

    def test_ready_to_retrain(self, auto_trainer):
        auto_trainer._state["last_sft_count"] = 80
        mock_pipeline = MagicMock()
        mock_pipeline.get_dataset_stats.return_value = {
            "sft_records": 150, "dpo_records": 10,
        }
        with patch("src.brain.auto_trainer.TrainingPipeline", return_value=mock_pipeline):
            should, reason = auto_trainer.should_retrain()
        assert should is True
        assert "70 new" in reason  # "70 new records ready" or "70 new SFT records ready"


class TestRun:
    @pytest.mark.asyncio
    async def test_run_skipped(self, auto_trainer):
        """Should skip when not enough data."""
        mock_processor = MagicMock()
        mock_processor.process_all.return_value = {"sft": 5, "dpo": 0}

        mock_pipeline = MagicMock()
        mock_pipeline.get_dataset_stats.return_value = {
            "sft_records": 50, "dpo_records": 0,
        }

        with patch("src.brain.auto_trainer.DataProcessor", return_value=mock_processor), \
             patch("src.brain.auto_trainer.TrainingPipeline", return_value=mock_pipeline):
            result = await auto_trainer.run()

        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_run_force(self, auto_trainer):
        """Force run should proceed even with insufficient data."""
        mock_processor = MagicMock()
        mock_processor.process_all.return_value = {"sft": 5, "dpo": 0}

        mock_report = MagicMock()
        mock_report.status = "completed"
        mock_report.metrics = {"train_loss": 0.5, "mode": "inline"}
        mock_report.to_dict.return_value = {
            "status": "completed",
            "metrics": {"train_loss": 0.5, "mode": "inline"},
        }
        mock_report.error = ""

        mock_export = MagicMock()
        mock_export.status = "completed"
        mock_export.to_dict.return_value = {"status": "completed"}

        mock_pipeline = MagicMock()
        mock_pipeline.get_dataset_stats.return_value = {
            "sft_records": 50, "dpo_records": 0,
        }
        mock_pipeline.train_sft.return_value = mock_report
        mock_pipeline.export_to_ollama.return_value = mock_export

        # Mock GGUF path in metrics
        mock_report.metrics = {
            "train_loss": 0.5,
            "mode": "inline",
            "gguf_path": "/tmp/model.gguf",
        }

        with patch("src.brain.auto_trainer.DataProcessor", return_value=mock_processor), \
             patch("src.brain.auto_trainer.TrainingPipeline", return_value=mock_pipeline):
            result = await auto_trainer.run(force=True)

        assert result["status"] == "completed"
        assert auto_trainer._state["train_count"] == 1

    @pytest.mark.asyncio
    async def test_run_sft_failed(self, auto_trainer):
        """Should stop if SFT training fails."""
        mock_processor = MagicMock()
        mock_processor.process_all.return_value = {"sft": 5, "dpo": 0}

        mock_report = MagicMock()
        mock_report.status = "failed"
        mock_report.error = "Out of memory"
        mock_report.to_dict.return_value = {"status": "failed", "error": "Out of memory"}

        mock_pipeline = MagicMock()
        mock_pipeline.get_dataset_stats.return_value = {
            "sft_records": 200, "dpo_records": 0,
        }
        mock_pipeline.train_sft.return_value = mock_report

        auto_trainer._state["last_sft_count"] = 0

        with patch("src.brain.auto_trainer.DataProcessor", return_value=mock_processor), \
             patch("src.brain.auto_trainer.TrainingPipeline", return_value=mock_pipeline):
            result = await auto_trainer.run()

        assert result["status"] == "sft_failed"


class TestGetStats:
    def test_get_stats(self, auto_trainer):
        mock_pipeline = MagicMock()
        mock_pipeline.get_dataset_stats.return_value = {
            "sft_records": 50, "dpo_records": 0,
        }
        with patch("src.brain.auto_trainer.TrainingPipeline", return_value=mock_pipeline):
            stats = auto_trainer.get_stats()
        assert "should_retrain" in stats
        assert "reason" in stats
        assert "train_count" in stats
        assert stats["train_count"] == 0
