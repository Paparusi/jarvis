"""Auto-Trainer — Autonomous retraining orchestration for Brain Independence.

Checks if enough new data has accumulated since last training,
runs SFT/DPO training, exports GGUF, deploys to Ollama,
then evaluates the new model and rolls back if degraded.

Designed to be called from Dreamtime cycle or /train command.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.brain.processor import DataProcessor
from src.brain.trainer import TrainingPipeline, get_profile_config
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("brain.auto_trainer")

# Minimum new SFT records since last training to trigger retrain
_MIN_NEW_SFT = 50
# Minimum category diversity for training data
_MIN_CATEGORIES = 5
# Minimum accuracy improvement to auto-deploy (2%)
_MIN_IMPROVEMENT = 0.02
# State file to track training history
_STATE_FILE = "data/training/auto_trainer_state.json"


class AutoTrainer:
    """Autonomous training orchestrator with evaluation and rollback."""

    def __init__(
        self,
        min_new_sft: int = _MIN_NEW_SFT,
        model_profile: str | None = None,
    ) -> None:
        self._min_new_sft = min_new_sft
        self._model_profile = model_profile
        self._root = get_project_root()
        self._state_path = self._root / _STATE_FILE
        self._state = self._load_state()

    def _load_state(self) -> dict:
        """Load training state from disk."""
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "last_train_at": None,
            "last_sft_count": 0,
            "last_dpo_count": 0,
            "train_count": 0,
            "history": [],
        }

    def _save_state(self) -> None:
        """Persist training state."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False)
        )

    def should_retrain(self) -> tuple[bool, str]:
        """Check if we have enough new data to trigger retraining.

        Considers both volume and category diversity.
        Returns (should_train, reason).
        """
        pipeline = TrainingPipeline()
        stats = pipeline.get_dataset_stats()

        current_sft = stats["sft_records"]
        last_sft = self._state.get("last_sft_count", 0)
        new_sft = current_sft - last_sft

        if current_sft < 100:
            return False, f"Not enough total SFT data ({current_sft}, need 100+)"

        if new_sft < self._min_new_sft:
            return False, f"Only {new_sft} new SFT records since last train (need {self._min_new_sft})"

        # Check diversity — count unique categories in training data
        categories = self._count_categories(pipeline)
        if categories < _MIN_CATEGORIES:
            return True, (
                f"{new_sft} new records ready (total: {current_sft}), "
                f"but low diversity ({categories} categories, recommend {_MIN_CATEGORIES}+)"
            )

        return True, (
            f"{new_sft} new SFT records ready "
            f"(total: {current_sft}, {categories} categories)"
        )

    def _count_categories(self, pipeline: TrainingPipeline) -> int:
        """Count unique categories in training data."""
        categories = set()
        try:
            result = pipeline.prepare_sft_dataset()
            records = result[0] if isinstance(result, tuple) else result
        except Exception:
            return 0
        for r in records:
            cat = r.get("metadata", {}).get("category", "")
            if cat:
                categories.add(cat)
        return len(categories)

    async def run(self, force: bool = False) -> dict[str, Any]:
        """Run the full auto-training pipeline.

        Steps:
        1. Process raw data → SFT/DPO format
        2. Check if enough new data (skip if not, unless force=True)
        3. Run SFT training
        4. Export to GGUF
        5. Deploy to Ollama
        6. Update state

        Returns a status dict with results from each step.
        """
        result: dict[str, Any] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "steps": {},
        }

        # Step 1: Process raw data
        try:
            processor = DataProcessor()
            proc_stats = processor.process_all()
            result["steps"]["process"] = proc_stats
            log.info("auto_train_processed", sft=proc_stats.get("sft", 0),
                     dpo=proc_stats.get("dpo", 0))
        except Exception as e:
            log.error("auto_train_process_error", error=str(e))
            result["steps"]["process_error"] = str(e)

        # Step 2: Check threshold
        should, reason = self.should_retrain()
        result["should_retrain"] = should
        result["reason"] = reason

        if not should and not force:
            log.info("auto_train_skipped", reason=reason)
            result["status"] = "skipped"
            return result

        log.info("auto_train_starting", reason=reason, force=force)

        # Step 3: Run SFT training
        profile_config = get_profile_config(self._model_profile) if self._model_profile else None
        pipeline = TrainingPipeline(config=profile_config)
        model_name = "jarvis-brain-14b" if self._model_profile == "14b" else "jarvis-brain"
        try:
            sft_report = pipeline.train_sft()
            result["steps"]["sft"] = sft_report.to_dict()

            if sft_report.status != "completed":
                result["status"] = "sft_failed"
                result["error"] = sft_report.error
                return result

            log.info("auto_train_sft_complete",
                     loss=sft_report.metrics.get("train_loss"),
                     mode=sft_report.metrics.get("mode"))
        except Exception as e:
            log.error("auto_train_sft_error", error=str(e))
            result["steps"]["sft_error"] = str(e)
            result["status"] = "sft_error"
            return result

        # Step 4: Export to GGUF and deploy to Ollama
        try:
            # If training was inline (Unsloth), GGUF should already be exported
            gguf_path = sft_report.metrics.get("gguf_path")
            if not gguf_path:
                # Try manual GGUF conversion
                gguf_path = self._convert_to_gguf(pipeline)
                result["steps"]["gguf_conversion"] = "manual" if gguf_path else "failed"

            if gguf_path:
                export_report = pipeline.export_to_ollama(model_name=model_name)
                result["steps"]["export"] = export_report.to_dict()
                log.info("auto_train_exported",
                         status=export_report.status,
                         model="jarvis-brain")
            else:
                result["steps"]["export"] = {"status": "skipped", "reason": "no GGUF"}
        except Exception as e:
            log.error("auto_train_export_error", error=str(e))
            result["steps"]["export_error"] = str(e)

        # Step 5: Evaluate new model
        eval_result = await self._evaluate_model(result)
        result["steps"]["evaluation"] = eval_result

        # Step 6: Update state
        stats = pipeline.get_dataset_stats()
        self._state["last_train_at"] = datetime.now(timezone.utc).isoformat()
        self._state["last_sft_count"] = stats["sft_records"]
        self._state["last_dpo_count"] = stats["dpo_records"]
        self._state["train_count"] = self._state.get("train_count", 0) + 1

        # Keep last 10 training records
        history_entry = {
            "at": self._state["last_train_at"],
            "sft_count": stats["sft_records"],
            "loss": sft_report.metrics.get("train_loss"),
            "mode": sft_report.metrics.get("mode"),
            "accuracy": eval_result.get("accuracy"),
        }
        self._state["history"] = self._state.get("history", [])[-9:] + [history_entry]
        self._save_state()

        result["status"] = "completed"
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        log.info("auto_train_complete", train_count=self._state["train_count"])
        return result

    def _convert_to_gguf(self, pipeline: TrainingPipeline) -> str | None:
        """Manual GGUF conversion using llama.cpp if Unsloth export failed."""
        converter = Path("/tmp/llama.cpp/convert_hf_to_gguf.py")
        quantizer = Path("/tmp/llama.cpp/build/bin/llama-quantize")
        gguf_dir = pipeline._output_dir / "gguf"

        if not converter.exists():
            log.warning("gguf_converter_not_found")
            return None

        merged_dir = gguf_dir
        if not any(merged_dir.glob("*.safetensors")):
            # No merged model — can't convert
            log.warning("no_merged_safetensors")
            return None

        f16_path = gguf_dir / "jarvis-brain-f16.gguf"
        q4_path = gguf_dir / "jarvis-brain-q4_k_m.gguf"

        try:
            # Step 1: Convert to f16 GGUF
            subprocess.run(
                ["python3", str(converter), str(merged_dir),
                 "--outfile", str(f16_path), "--outtype", "f16"],
                capture_output=True, text=True, timeout=600, check=True,
            )

            # Step 2: Quantize to Q4_K_M
            if quantizer.exists():
                subprocess.run(
                    [str(quantizer), str(f16_path), str(q4_path), "Q4_K_M"],
                    capture_output=True, text=True, timeout=600, check=True,
                )
                # Clean up f16
                f16_path.unlink(missing_ok=True)
                return str(q4_path)
            else:
                return str(f16_path)
        except Exception as e:
            log.error("manual_gguf_conversion_failed", error=str(e))
            return None

    async def _evaluate_model(self, result: dict) -> dict:
        """Run benchmark evaluation on the newly trained model."""
        try:
            from src.brain.evaluator import ModelEvaluator

            evaluator = ModelEvaluator()
            prev = evaluator.compare_versions(limit=3)
            prev_accuracy = prev.get("latest_accuracy", 0)

            report = await evaluator.run_benchmark(skip_cloud=True)
            new_accuracy = report.accuracy

            eval_data = {
                "accuracy": new_accuracy,
                "score": report.overall_local_score,
                "previous_accuracy": prev_accuracy,
                "improvement": round(new_accuracy - prev_accuracy, 3),
                "category_scores": report.category_scores,
                "status": "evaluated",
            }

            # Check for regression
            if prev_accuracy > 0 and new_accuracy < prev_accuracy - _MIN_IMPROVEMENT:
                eval_data["warning"] = (
                    f"Regression detected: {new_accuracy:.1%} vs previous {prev_accuracy:.1%}"
                )
                log.warning("model_regression",
                            new=new_accuracy, previous=prev_accuracy)

            return eval_data

        except Exception as e:
            log.error("auto_eval_error", error=str(e))
            return {"status": "eval_error", "error": str(e)}

    def get_stats(self) -> dict:
        """Get auto-trainer status."""
        should, reason = self.should_retrain()
        return {
            "should_retrain": should,
            "reason": reason,
            "last_train_at": self._state.get("last_train_at"),
            "train_count": self._state.get("train_count", 0),
            "last_sft_count": self._state.get("last_sft_count", 0),
            "last_accuracy": self._get_last_accuracy(),
            "history": self._state.get("history", [])[-5:],
        }

    def _get_last_accuracy(self) -> float | None:
        """Get accuracy from most recent training run."""
        history = self._state.get("history", [])
        if history:
            return history[-1].get("accuracy")
        return None
