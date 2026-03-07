"""Tests for Brain Independence — Phase 8 enhancements.

Covers: SyntheticDataGenerator, ModelEvaluator, enhanced AutoTrainer.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.brain.synthetic import (
    SyntheticDataGenerator,
    CATEGORIES,
    MULTITURN_CATEGORIES,
    DPO_CATEGORIES,
    SYSTEM_PROMPT,
)
from src.brain.evaluator import (
    ModelEvaluator,
    BenchmarkReport,
    EvalResult,
    EVAL_CATEGORIES,
)
from src.brain.auto_trainer import AutoTrainer


# --- Fixtures ---

@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    """Set up a temporary project root with training dirs."""
    monkeypatch.setattr("src.brain.synthetic.get_project_root", lambda: tmp_path)
    monkeypatch.setattr("src.brain.evaluator.get_project_root", lambda: tmp_path)
    (tmp_path / "training" / "data" / "processed").mkdir(parents=True)
    (tmp_path / "training" / "data" / "preferences").mkdir(parents=True)
    (tmp_path / "data" / "training" / "evaluations").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def generator(tmp_project):
    return SyntheticDataGenerator(model="test-model", temperature=0.7)


@pytest.fixture
def evaluator(tmp_project):
    return ModelEvaluator(
        local_model="test-local",
        cloud_model="test-cloud",
        judge_model="test-judge",
    )


# === SyntheticDataGenerator Tests ===

class TestSyntheticCategories:

    def test_categories_count(self):
        """Should have 20+ diverse categories."""
        assert len(CATEGORIES) >= 20

    def test_categories_have_required_fields(self):
        for cat in CATEGORIES:
            assert "category" in cat
            assert "instruction" in cat
            assert "count" in cat
            assert cat["count"] > 0

    def test_multiturn_categories(self):
        assert len(MULTITURN_CATEGORIES) >= 3
        for cat in MULTITURN_CATEGORIES:
            assert "multiturn" in cat["category"]

    def test_dpo_categories(self):
        assert len(DPO_CATEGORIES) >= 3
        for cat in DPO_CATEGORIES:
            assert "dpo" in cat["category"]

    def test_unique_category_names(self):
        names = [c["category"] for c in CATEGORIES]
        assert len(names) == len(set(names))


class TestSyntheticFormatting:

    def test_format_sft(self, generator):
        pairs = [
            {"user": "Xin chào", "assistant": "Chào bạn! Tôi có thể giúp gì?"},
            {"user": "Cảm ơn", "assistant": "Không có chi! Chúc bạn một ngày tốt lành."},
        ]
        records = generator._format_sft(pairs, "greeting")
        assert len(records) == 2
        assert records[0]["messages"][0]["role"] == "system"
        assert records[0]["messages"][0]["content"] == SYSTEM_PROMPT
        assert records[0]["messages"][1]["content"] == "Xin chào"
        assert records[0]["metadata"]["category"] == "greeting"
        assert records[0]["metadata"]["synthetic"] is True

    def test_format_sft_skips_empty(self, generator):
        pairs = [
            {"user": "", "assistant": "response"},
            {"user": "question", "assistant": ""},
            {"user": "ok", "assistant": "short"},  # < 10 chars
        ]
        records = generator._format_sft(pairs, "test")
        assert len(records) == 0

    def test_format_multiturn(self, generator):
        raw = [{
            "turns": [
                {"role": "user", "content": "Viết hàm Python"},
                {"role": "assistant", "content": "def hello(): pass"},
                {"role": "user", "content": "Thêm docstring"},
                {"role": "assistant", "content": "def hello():\n    '''Hello'''"},
            ]
        }]
        records = generator._format_multiturn(raw, "multiturn_coding")
        assert len(records) == 1
        assert len(records[0]["messages"]) == 5  # system + 4 turns
        assert records[0]["metadata"]["multiturn"] is True

    def test_format_multiturn_skips_short(self, generator):
        raw = [{"turns": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]}]
        records = generator._format_multiturn(raw, "test")
        assert len(records) == 0  # < 4 turns

    def test_format_dpo(self, generator):
        raw = [{
            "prompt": "What is Python?",
            "chosen": "Python is a versatile programming language...",
            "rejected": "Python is a thing.",
        }]
        pairs = generator._format_dpo(raw, "dpo_coding")
        assert len(pairs) == 1
        assert pairs[0]["category"] == "dpo_coding"
        assert "synthetic" in pairs[0]["chosen_model"]

    def test_extract_json_array(self, generator):
        text = 'Some text [{"user": "hi", "assistant": "hello"}] more text'
        result = generator._extract_json_array(text)
        assert len(result) == 1
        assert result[0]["user"] == "hi"

    def test_extract_json_array_invalid(self, generator):
        assert generator._extract_json_array("no json here") == []
        assert generator._extract_json_array("[invalid json}") == []


class TestSyntheticGeneration:

    @pytest.mark.asyncio
    async def test_generate_category(self, generator):
        """Should call LLM and parse response."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"user": "Hello", "assistant": "Hi there! How can I help?"},
        ])

        cat = {"category": "test", "instruction": "Generate test data", "count": 1}

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await generator._generate_category(cat, "{instruction}")

        assert len(result) == 1
        assert result[0]["user"] == "Hello"

    @pytest.mark.asyncio
    async def test_generate_category_error(self, generator):
        """Should return empty list on error."""
        cat = {"category": "test", "instruction": "Test", "count": 1}

        with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("API error")):
            result = await generator._generate_category(cat, "{instruction}")

        assert result == []

    @pytest.mark.asyncio
    async def test_generate_sft(self, generator, tmp_project):
        """Should generate and save SFT data."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"user": "Test question", "assistant": "Test answer with enough content for validation"},
        ])

        small_cats = [
            {"category": "test_cat", "instruction": "Generate data", "count": 1},
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await generator.generate_sft(categories=small_cats)

        assert result["total_records"] >= 1
        assert "test_cat" in result["categories"]
        output = Path(result["output_file"])
        assert output.exists()

    @pytest.mark.asyncio
    async def test_generate_dpo(self, generator, tmp_project):
        """Should generate DPO pairs."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"prompt": "Q1", "chosen": "Good answer", "rejected": "Bad answer"},
        ])

        small_cats = [
            {"category": "dpo_test", "instruction": "Generate DPO", "count": 1},
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await generator.generate_dpo(categories=small_cats)

        assert result["total_pairs"] >= 1

    def test_combine_sft(self, generator, tmp_project):
        """Should combine all SFT sources and dedup."""
        sft_dir = tmp_project / "training" / "data" / "processed"

        # Write two files with one duplicate
        records1 = [
            {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]},
        ]
        records2 = [
            {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi duplicate"}]},
            {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "Bye"}, {"role": "assistant", "content": "Goodbye"}]},
        ]

        (sft_dir / "sft_20260305_000001.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records1)
        )
        (sft_dir / "sft_20260305_000002.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records2)
        )

        result = generator._combine_sft()
        assert result["total_records"] == 2  # Deduped: "Hello" appears once

    def test_get_stats(self, generator, tmp_project):
        stats = generator.get_stats()
        assert "sft_records" in stats
        assert "dpo_pairs" in stats
        assert "combined_sft" in stats


# === ModelEvaluator Tests ===

class TestEvalCategories:

    def test_eval_categories_count(self):
        assert len(EVAL_CATEGORIES) >= 8

    def test_eval_categories_have_prompts(self):
        for cat, prompts in EVAL_CATEGORIES.items():
            assert len(prompts) >= 2, f"Category {cat} needs at least 2 prompts"


class TestEvalResult:

    def test_eval_result_defaults(self):
        r = EvalResult(prompt="test", category="test")
        assert r.local_score == 0.0
        assert r.cloud_score == 0.0
        assert r.local_response == ""

    def test_benchmark_report_to_dict(self):
        report = BenchmarkReport(
            timestamp="2026-03-05",
            model_name="test",
            total_prompts=10,
            accuracy=0.8,
            parity=0.7,
            overall_local_score=7.5,
            overall_cloud_score=8.0,
        )
        d = report.to_dict()
        assert d["accuracy"] == 0.8
        assert d["overall_local_score"] == 7.5


class TestHeuristicScoring:

    def test_empty_response(self):
        assert ModelEvaluator._heuristic_score("") == 0.0

    def test_error_response(self):
        assert ModelEvaluator._heuristic_score("[Error: timeout]") == 0.0

    def test_short_response(self):
        score = ModelEvaluator._heuristic_score("ok")
        assert score < 5.0

    def test_good_response(self):
        text = "## Analysis\n- Point 1: important finding\n- Point 2: another key insight\n```python\nprint('hello')\n```"
        score = ModelEvaluator._heuristic_score(text)
        assert score >= 6.0

    def test_apologetic_response(self):
        score = ModelEvaluator._heuristic_score(
            "Xin lỗi, tôi không thể giúp bạn về vấn đề này."
        )
        assert score < 5.0


class TestEvalComputation:

    def test_compute_stats_empty(self, evaluator):
        report = BenchmarkReport()
        result = evaluator._compute_stats(report)
        assert result.accuracy == 0

    def test_compute_stats_with_results(self, evaluator):
        report = BenchmarkReport(total_prompts=3)
        report.results = [
            EvalResult(prompt="q1", category="cat_a", local_score=8.0, cloud_score=9.0),
            EvalResult(prompt="q2", category="cat_a", local_score=6.0, cloud_score=8.0),
            EvalResult(prompt="q3", category="cat_b", local_score=9.0, cloud_score=7.0),
        ]
        result = evaluator._compute_stats(report)
        assert result.accuracy == pytest.approx(2 / 3)  # 2 out of 3 >= 7.0
        assert "cat_a" in result.category_scores
        assert "cat_b" in result.category_scores
        assert result.category_scores["cat_a"]["count"] == 2
        assert result.category_scores["cat_b"]["local_avg"] == 9.0


class TestEvalBenchmark:

    @pytest.mark.asyncio
    async def test_run_benchmark_skip_cloud(self, evaluator, tmp_project):
        """Should run benchmark with local model only."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Thủ đô Việt Nam là Hà Nội, một thành phố lớn."
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=20)

        # Judge response
        judge_response = MagicMock()
        judge_response.choices = [MagicMock()]
        judge_response.choices[0].message.content = json.dumps({
            "local_score": 7.5, "reasoning": "Good answer"
        })

        small_cats = {"factual_vn": ["Thủ đô Việt Nam là gì?"]}

        call_count = 0
        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_response  # local model
            return judge_response  # judge

        with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=mock_completion):
            report = await evaluator.run_benchmark(
                categories=small_cats, skip_cloud=True,
            )

        assert report.total_prompts == 1
        assert len(report.results) == 1
        assert report.results[0].local_score == 7.5

    def test_save_and_load_report(self, evaluator, tmp_project):
        report = BenchmarkReport(
            timestamp="2026-03-05T00:00:00",
            model_name="test",
            total_prompts=5,
            accuracy=0.8,
        )
        evaluator._save_report(report)

        history = evaluator.get_history(limit=5)
        assert len(history) >= 1
        assert history[0]["accuracy"] == 0.8

    def test_compare_versions_no_data(self, evaluator, tmp_project):
        result = evaluator.compare_versions()
        assert result["trend"] == "no_data"

    def test_compare_versions_with_data(self, evaluator, tmp_project):
        eval_dir = tmp_project / "data" / "training" / "evaluations"

        # Create fake evaluation reports (newest first when sorted reverse)
        # eval_20260305 (newest, acc=0.8) > eval_20260304 > eval_20260303 (oldest, acc=0.6)
        for i, acc in enumerate([0.6, 0.7, 0.8]):
            path = eval_dir / f"eval_2026030{3+i}_000000.json"
            path.write_text(json.dumps({
                "accuracy": acc,
                "overall_local_score": acc * 10,
            }))

        result = evaluator.compare_versions(limit=5)
        assert result["evaluations"] == 3
        # Sorted reverse: [0.8, 0.7, 0.6] → latest=0.8, oldest=0.6 → improving
        assert result["trend"] == "improving"


# === Enhanced AutoTrainer Tests ===

class TestAutoTrainerEnhanced:

    def test_count_categories(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.brain.auto_trainer.get_project_root", lambda: tmp_path)
        monkeypatch.setattr("src.brain.trainer.get_project_root", lambda: tmp_path)

        # Create SFT data with categories
        sft_dir = tmp_path / "training" / "data" / "processed"
        sft_dir.mkdir(parents=True)
        (tmp_path / "training" / "data" / "preferences").mkdir(parents=True)
        (tmp_path / "training" / "output").mkdir(parents=True)
        (tmp_path / "data" / "training").mkdir(parents=True)

        records = [
            {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"category": "greeting"}},
            {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "code"}, {"role": "assistant", "content": "def f(): pass"}], "metadata": {"category": "coding"}},
            {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "math"}, {"role": "assistant", "content": "42"}], "metadata": {"category": "math"}},
        ]
        combined = sft_dir / "combined_sft.jsonl"
        combined.write_text("\n".join(json.dumps(r) for r in records))

        trainer = AutoTrainer()
        from src.brain.trainer import TrainingPipeline
        pipeline = TrainingPipeline()
        count = trainer._count_categories(pipeline)
        assert count == 3

    def test_get_last_accuracy(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.brain.auto_trainer.get_project_root", lambda: tmp_path)

        # Create state with history
        state_dir = tmp_path / "data" / "training"
        state_dir.mkdir(parents=True)
        state = {
            "last_train_at": "2026-03-05",
            "last_sft_count": 100,
            "last_dpo_count": 50,
            "train_count": 2,
            "history": [
                {"at": "2026-03-04", "sft_count": 100, "loss": 0.5, "accuracy": 0.72},
                {"at": "2026-03-05", "sft_count": 150, "loss": 0.4, "accuracy": 0.78},
            ],
        }
        (state_dir / "auto_trainer_state.json").write_text(json.dumps(state))

        trainer = AutoTrainer()
        assert trainer._get_last_accuracy() == 0.78

    def test_get_last_accuracy_no_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.brain.auto_trainer.get_project_root", lambda: tmp_path)
        trainer = AutoTrainer()
        assert trainer._get_last_accuracy() is None

    @pytest.mark.asyncio
    async def test_evaluate_model_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.brain.auto_trainer.get_project_root", lambda: tmp_path)
        trainer = AutoTrainer()

        # Should handle evaluation errors gracefully
        with patch("src.brain.evaluator.ModelEvaluator") as MockEval:
            MockEval.side_effect = RuntimeError("No model available")
            result = await trainer._evaluate_model({})

        assert result["status"] == "eval_error"


# === Metrics Integration Tests ===

class TestBrainMetrics:

    def test_eval_metrics_exist(self):
        from src.monitoring.metrics import (
            brain_eval_accuracy,
            brain_eval_score,
            brain_cloud_fallback_rate,
        )
        # Should be importable and callable
        brain_eval_accuracy.set(0.75)
        brain_eval_score.set(7.5)
        brain_cloud_fallback_rate.set(0.15)


# === Router Integration Tests ===

class TestRouterConfig:

    def test_config_has_local_enabled(self):
        from src.utils.config import load_config
        config = load_config()
        router = config.get("intelligence", {}).get("router", {})
        assert router.get("enabled") is True
        assert "local_model" in router

    def test_config_has_cache(self):
        from src.utils.config import load_config
        config = load_config()
        cache = config["intelligence"]["router"].get("cache", {})
        assert cache.get("similarity_threshold", 0) > 0.8
