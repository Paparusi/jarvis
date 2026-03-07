"""Tests for Training Pipeline."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from src.brain.trainer import (
    TrainingPipeline,
    TrainingReport,
    _MODEL_PROFILES,
    get_profile_config,
    _DEFAULT_CONFIG,
)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr("src.brain.trainer.get_project_root", lambda: tmp_path)
    # Create required directories
    (tmp_path / "training" / "data" / "processed").mkdir(parents=True)
    (tmp_path / "training" / "data" / "preferences").mkdir(parents=True)
    return TrainingPipeline()


class TestTrainingReport:
    def test_to_dict(self):
        report = TrainingReport(stage="sft", status="completed")
        d = report.to_dict()
        assert d["stage"] == "sft"
        assert d["status"] == "completed"

    def test_default_values(self):
        report = TrainingReport(stage="dpo")
        assert report.status == "pending"
        assert report.dataset_size == 0
        assert report.error == ""


class TestDatasetPreparation:
    def test_prepare_sft_empty(self, pipeline):
        records, count = pipeline.prepare_sft_dataset()
        assert count == 0
        assert records == []

    def test_prepare_sft_with_data(self, pipeline, tmp_path):
        sft_file = tmp_path / "training" / "data" / "processed" / "sft_2026-03.jsonl"
        records = [
            {"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]},
            {"messages": [{"role": "user", "content": "test"}, {"role": "assistant", "content": "ok"}]},
        ]
        sft_file.write_text("\n".join(json.dumps(r) for r in records))

        loaded, count = pipeline.prepare_sft_dataset()
        assert count == 2
        assert loaded[0]["messages"][0]["content"] == "hello"

    def test_prepare_sft_filters_invalid(self, pipeline, tmp_path):
        sft_file = tmp_path / "training" / "data" / "processed" / "sft_test.jsonl"
        lines = [
            json.dumps({"messages": [{"role": "user", "content": "good"}, {"role": "assistant", "content": "ok"}]}),
            json.dumps({"messages": []}),  # Too few messages
            "invalid json",
            json.dumps({"no_messages": True}),  # Missing messages key
        ]
        sft_file.write_text("\n".join(lines))

        loaded, count = pipeline.prepare_sft_dataset()
        assert count == 1

    def test_prepare_dpo_empty(self, pipeline):
        records, count = pipeline.prepare_dpo_dataset()
        assert count == 0

    def test_prepare_dpo_with_data(self, pipeline, tmp_path):
        dpo_file = tmp_path / "training" / "data" / "preferences" / "dpo_2026-03.jsonl"
        records = [
            {"prompt": "test", "chosen": "good answer", "rejected": "bad answer"},
        ]
        dpo_file.write_text("\n".join(json.dumps(r) for r in records))

        loaded, count = pipeline.prepare_dpo_dataset()
        assert count == 1

    def test_get_dataset_stats(self, pipeline):
        stats = pipeline.get_dataset_stats()
        assert "sft_records" in stats
        assert "dpo_records" in stats
        assert stats["ready_for_sft"] is False
        assert stats["ready_for_dpo"] is False


class TestSFTTraining:
    def test_train_sft_insufficient_data(self, pipeline):
        report = pipeline.train_sft()
        assert report.status == "failed"
        assert "Need at least 100" in report.error

    def test_train_sft_generates_script(self, pipeline, tmp_path):
        # Create 100+ SFT records
        sft_file = tmp_path / "training" / "data" / "processed" / "sft_bulk.jsonl"
        records = []
        for i in range(110):
            records.append(json.dumps({
                "messages": [
                    {"role": "user", "content": f"question {i}"},
                    {"role": "assistant", "content": f"answer {i}"},
                ]
            }))
        sft_file.write_text("\n".join(records))

        # Mock unsloth as unavailable to test script generation path
        import unittest.mock
        with unittest.mock.patch.dict("sys.modules", {"unsloth": None}):
            report = pipeline.train_sft()
        assert report.status == "completed"
        assert report.metrics["mode"] == "script_generated"
        # Script file should exist
        script_path = Path(report.metrics["script_path"])
        assert script_path.exists()


class TestDPOTraining:
    def test_train_dpo_insufficient_data(self, pipeline):
        report = pipeline.train_dpo()
        assert report.status == "failed"
        assert "Need at least 50" in report.error

    def test_train_dpo_generates_script(self, pipeline, tmp_path):
        dpo_file = tmp_path / "training" / "data" / "preferences" / "dpo_bulk.jsonl"
        records = []
        for i in range(60):
            records.append(json.dumps({
                "prompt": f"question {i}",
                "chosen": f"good answer {i}",
                "rejected": f"bad answer {i}",
            }))
        dpo_file.write_text("\n".join(records))

        report = pipeline.train_dpo()
        assert report.status == "completed"
        assert report.metrics["mode"] == "script_generated"


class TestEvaluation:
    def test_evaluate_no_data(self, pipeline):
        report = pipeline.evaluate()
        assert report.status == "failed"

    def test_evaluate_with_data(self, pipeline, tmp_path):
        sft_file = tmp_path / "training" / "data" / "processed" / "sft_eval.jsonl"
        records = []
        for i in range(20):
            records.append(json.dumps({
                "messages": [
                    {"role": "user", "content": f"q{i}"},
                    {"role": "assistant", "content": f"a{i}"},
                ]
            }))
        sft_file.write_text("\n".join(records))

        report = pipeline.evaluate()
        assert report.status == "completed"
        assert report.metrics["test_size"] >= 2


class TestExport:
    def test_export_no_gguf(self, pipeline):
        report = pipeline.export_to_ollama()
        assert report.status == "failed"
        assert "No GGUF file found" in report.error


class TestStatus:
    def test_get_status(self, pipeline):
        status = pipeline.get_status()
        assert "dataset" in status
        assert "artifacts" in status
        assert status["artifacts"]["sft_adapter"] is False


# === Model Profile Tests ===


class TestModelProfiles:
    """Test model profile configuration for 4B and 14B."""

    def test_profiles_exist(self):
        assert "4b" in _MODEL_PROFILES
        assert "14b" in _MODEL_PROFILES

    def test_4b_profile_values(self):
        p = _MODEL_PROFILES["4b"]
        assert p["base_model"] == "Qwen/Qwen3.5-4B"
        assert p["ollama_model"] == "qwen3.5:4b"
        assert p["load_in_4bit"] is False
        assert p["load_in_16bit"] is True

    def test_14b_profile_values(self):
        p = _MODEL_PROFILES["14b"]
        assert p["base_model"] == "Qwen/Qwen3-14B"
        assert p["ollama_model"] == "qwen3:14b"
        assert p["load_in_4bit"] is True
        assert p["load_in_16bit"] is False
        assert p["learning_rate"] == 1e-4  # Lower LR for larger model

    def test_get_profile_config_4b(self):
        config = get_profile_config("4b")
        # Should merge with defaults
        assert config["base_model"] == "Qwen/Qwen3.5-4B"
        assert "num_epochs" in config  # From default
        assert config["load_in_4bit"] is False

    def test_get_profile_config_14b(self):
        config = get_profile_config("14b")
        assert config["base_model"] == "Qwen/Qwen3-14B"
        assert config["load_in_4bit"] is True
        assert config["learning_rate"] == 1e-4

    def test_get_profile_config_invalid(self):
        with pytest.raises(ValueError, match="Unknown profile"):
            get_profile_config("70b")

    def test_profile_overrides_defaults(self):
        """Profile values should override _DEFAULT_CONFIG values."""
        config = get_profile_config("14b")
        # 14b profile sets learning_rate=1e-4 overriding default 2e-4
        assert config["learning_rate"] == 1e-4
        # But keeps defaults not in profile
        assert config["num_epochs"] == _DEFAULT_CONFIG["num_epochs"]

    def test_pipeline_with_profile(self, tmp_path, monkeypatch):
        """TrainingPipeline should accept profile config."""
        monkeypatch.setattr("src.brain.trainer.get_project_root", lambda: tmp_path)
        (tmp_path / "training" / "data" / "processed").mkdir(parents=True)
        (tmp_path / "training" / "data" / "preferences").mkdir(parents=True)

        config = get_profile_config("14b")
        pipeline = TrainingPipeline(config=config)
        assert pipeline._config["base_model"] == "Qwen/Qwen3-14B"
        assert pipeline._config["load_in_4bit"] is True

    def test_sft_script_uses_profile_loading(self, tmp_path, monkeypatch):
        """Generated SFT script should use load_in_4bit from 14b profile."""
        monkeypatch.setattr("src.brain.trainer.get_project_root", lambda: tmp_path)
        (tmp_path / "training" / "data" / "processed").mkdir(parents=True)
        (tmp_path / "training" / "data" / "preferences").mkdir(parents=True)

        # Create enough SFT records
        sft_file = tmp_path / "training" / "data" / "processed" / "sft_bulk.jsonl"
        records = [
            json.dumps({
                "messages": [
                    {"role": "user", "content": f"q{i}"},
                    {"role": "assistant", "content": f"a{i}"},
                ]
            })
            for i in range(110)
        ]
        sft_file.write_text("\n".join(records))

        config = get_profile_config("14b")
        pipeline = TrainingPipeline(config=config)

        import unittest.mock
        with unittest.mock.patch.dict("sys.modules", {"unsloth": None}):
            report = pipeline.train_sft()

        assert report.status == "completed"
        assert report.metrics["mode"] == "script_generated"

        # Read generated script and verify it uses load_in_4bit=True
        script_path = Path(report.metrics["script_path"])
        script_content = script_path.read_text()
        assert "load_in_4bit=True" in script_content
        assert "Qwen/Qwen3-14B" in script_content


class TestAutoTrainerProfile:
    """Test AutoTrainer with model profile support."""

    def test_auto_trainer_accepts_profile(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.brain.trainer.get_project_root", lambda: tmp_path)
        monkeypatch.setattr("src.brain.auto_trainer.get_project_root", lambda: tmp_path)
        (tmp_path / "training" / "data" / "processed").mkdir(parents=True)
        (tmp_path / "training" / "data" / "preferences").mkdir(parents=True)
        (tmp_path / "data" / "training").mkdir(parents=True)

        from src.brain.auto_trainer import AutoTrainer
        trainer = AutoTrainer(model_profile="14b")
        assert trainer._model_profile == "14b"

    def test_auto_trainer_none_profile(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.brain.trainer.get_project_root", lambda: tmp_path)
        monkeypatch.setattr("src.brain.auto_trainer.get_project_root", lambda: tmp_path)
        (tmp_path / "training" / "data" / "processed").mkdir(parents=True)
        (tmp_path / "training" / "data" / "preferences").mkdir(parents=True)
        (tmp_path / "data" / "training").mkdir(parents=True)

        from src.brain.auto_trainer import AutoTrainer
        trainer = AutoTrainer(model_profile=None)
        assert trainer._model_profile is None
