"""Training Pipeline — Orchestrate SFT and DPO fine-tuning for Brain Independence.

Pipeline:
1. Prepare Dataset — Load and validate processed training data
2. Train SFT — QLoRA fine-tuning with Unsloth (if installed)
3. Train DPO — Preference alignment from user feedback
4. Evaluate — Compare fine-tuned vs base model
5. Export — Convert to GGUF and deploy to Ollama

This is designed to run offline (not during serving) via:
  python -m src.brain.trainer --stage sft
  python -m src.brain.trainer --stage dpo
  python -m src.brain.trainer --stage evaluate
  python -m src.brain.trainer --stage export
Or triggered via Dreamtime / /train command.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("brain.trainer")

# Model profiles — hardware-aware presets
_MODEL_PROFILES: dict[str, dict] = {
    "4b": {
        "base_model": "Qwen/Qwen3.5-4B",
        "ollama_model": "qwen3.5:4b",
        "load_in_4bit": False,
        "load_in_16bit": True,
        "max_seq_length": 2048,
        "lora_r": 16,
        "lora_alpha": 16,
        "batch_size": 1,
        "gradient_accumulation_steps": 8,
        "learning_rate": 2e-4,
    },
    "14b": {
        "base_model": "Qwen/Qwen3-14B",
        "ollama_model": "qwen3:14b",
        "load_in_4bit": True,          # 4-bit QLoRA — fits 16GB VRAM
        "load_in_16bit": False,
        "max_seq_length": 2048,
        "lora_r": 16,
        "lora_alpha": 16,
        "batch_size": 1,
        "gradient_accumulation_steps": 8,
        "learning_rate": 1e-4,         # Lower LR for larger model
    },
}

# Default training config
_DEFAULT_CONFIG = {
    "base_model": "Qwen/Qwen3.5-4B",               # HF model for training
    "ollama_model": "qwen3.5:4b",                   # Deployed Ollama model
    "load_in_4bit": False,
    "load_in_16bit": True,
    "max_seq_length": 2048,
    "lora_r": 16,
    "lora_alpha": 16,
    "lora_dropout": 0,
    "learning_rate": 2e-4,
    "num_epochs": 3,
    "batch_size": 1,
    "gradient_accumulation_steps": 8,
    "warmup_ratio": 0.1,
    "weight_decay": 0.01,
    "output_dir": "training/output",
    "logging_steps": 5,
    "save_steps": 50,
    "eval_steps": 50,
    "bf16": True,                                    # bf16 for Blackwell GPU
    "dpo_beta": 0.1,
    "gguf_quantization": "q4_k_m",                   # GGUF export quantization
}


def get_profile_config(profile: str) -> dict:
    """Get training config for a model profile (merges profile into defaults)."""
    if profile not in _MODEL_PROFILES:
        raise ValueError(f"Unknown profile '{profile}'. Choose from: {list(_MODEL_PROFILES)}")
    return {**_DEFAULT_CONFIG, **_MODEL_PROFILES[profile]}


@dataclass
class TrainingReport:
    """Summary of a training run."""
    stage: str                      # "sft", "dpo", "evaluate", "export"
    status: str = "pending"         # "pending", "running", "completed", "failed"
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    dataset_size: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "dataset_size": self.dataset_size,
            "metrics": self.metrics,
            "error": self.error,
        }


class TrainingPipeline:
    """Orchestrate model training for Brain Independence."""

    def __init__(self, config: dict | None = None) -> None:
        self._config = {**_DEFAULT_CONFIG, **(config or {})}
        self._root = get_project_root()
        self._sft_dir = self._root / "training" / "data" / "processed"
        self._dpo_dir = self._root / "training" / "data" / "preferences"
        self._output_dir = self._root / self._config["output_dir"]
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._reports_dir = self._root / "data" / "training"
        self._reports_dir.mkdir(parents=True, exist_ok=True)

    # --- Dataset Preparation ---

    def prepare_sft_dataset(self) -> tuple[list[dict], int]:
        """Load and validate SFT training data.

        Prefers combined_sft.jsonl (real + synthetic merged).
        Falls back to individual sft_*.jsonl + synthetic_sft.jsonl files.

        Returns:
            Tuple of (dataset_records, total_count)
        """
        records = []
        if not self._sft_dir.exists():
            log.warning("sft_dir_not_found", path=str(self._sft_dir))
            return records, 0

        # Prefer combined file (already deduplicated real + synthetic)
        combined = self._sft_dir / "combined_sft.jsonl"
        if combined.exists():
            for line in combined.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if "messages" in record and len(record["messages"]) >= 2:
                        records.append(record)
                except json.JSONDecodeError:
                    continue
            log.info("sft_dataset_prepared", records=len(records), source="combined")
            return records, len(records)

        # Fallback: load individual files
        seen_contents = set()
        for pattern in ["sft_*.jsonl", "synthetic_sft.jsonl"]:
            for f in sorted(self._sft_dir.glob(pattern)):
                for line in f.read_text(encoding="utf-8").strip().split("\n"):
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if "messages" in record and len(record["messages"]) >= 2:
                            # Dedup by user message content
                            user_msg = record["messages"][1].get("content", "")
                            if user_msg not in seen_contents:
                                seen_contents.add(user_msg)
                                records.append(record)
                    except json.JSONDecodeError:
                        continue

        log.info("sft_dataset_prepared", records=len(records), source="individual")
        return records, len(records)

    def prepare_dpo_dataset(self) -> tuple[list[dict], int]:
        """Load and validate DPO preference data.

        Returns:
            Tuple of (dataset_records, total_count)
        """
        records = []
        if not self._dpo_dir.exists():
            log.warning("dpo_dir_not_found", path=str(self._dpo_dir))
            return records, 0

        for f in sorted(self._dpo_dir.glob("dpo_*.jsonl")):
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    # Validate required fields for DPO
                    if all(k in record for k in ("prompt", "chosen", "rejected")):
                        records.append(record)
                except json.JSONDecodeError:
                    continue

        log.info("dpo_dataset_prepared", records=len(records))
        return records, len(records)

    def get_dataset_stats(self) -> dict:
        """Get statistics about available training data."""
        sft_records, sft_count = self.prepare_sft_dataset()
        dpo_records, dpo_count = self.prepare_dpo_dataset()

        return {
            "sft_records": sft_count,
            "dpo_records": dpo_count,
            "sft_files": len(list(self._sft_dir.glob("sft_*.jsonl"))) if self._sft_dir.exists() else 0,
            "dpo_files": len(list(self._dpo_dir.glob("dpo_*.jsonl"))) if self._dpo_dir.exists() else 0,
            "ready_for_sft": sft_count >= 100,
            "ready_for_dpo": dpo_count >= 50,
        }

    # --- SFT Training ---

    def train_sft(self) -> TrainingReport:
        """Run SFT fine-tuning using Unsloth + QLoRA.

        Checks if Unsloth is available, falls back to reporting
        dataset readiness if not installed.
        """
        report = TrainingReport(stage="sft")
        report.started_at = datetime.now(timezone.utc).isoformat()
        report.status = "running"
        start = time.monotonic()

        # Load dataset
        records, count = self.prepare_sft_dataset()
        report.dataset_size = count

        if count < 100:
            report.status = "failed"
            report.error = f"Need at least 100 SFT records, have {count}. Keep collecting data."
            report.duration_seconds = time.monotonic() - start
            self._save_report(report)
            return report

        # Write consolidated training file
        train_file = self._output_dir / "sft_train.jsonl"
        with open(train_file, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        try:
            self._run_sft_training(train_file, report)
        except Exception as e:
            report.status = "failed"
            report.error = str(e)
            log.error("sft_training_failed", error=str(e))

        report.duration_seconds = time.monotonic() - start
        report.completed_at = datetime.now(timezone.utc).isoformat()
        self._save_report(report)
        return report

    def _run_sft_training(self, train_file: Path, report: TrainingReport) -> None:
        """Execute SFT training with Unsloth + QLoRA on Qwen3.5-4B."""
        try:
            import unsloth  # noqa: F401
            has_unsloth = True
        except ImportError:
            has_unsloth = False

        if not has_unsloth:
            script = self._generate_sft_script(train_file)
            script_path = self._output_dir / "run_sft.py"
            script_path.write_text(script, encoding="utf-8")
            report.status = "completed"
            report.metrics = {
                "mode": "script_generated",
                "script_path": str(script_path),
                "dataset_size": report.dataset_size,
                "instructions": f"Install unsloth then run: python {script_path}",
            }
            log.info("sft_script_generated", path=str(script_path))
            return

        import torch
        from unsloth import FastLanguageModel
        from trl import SFTTrainer, SFTConfig
        from datasets import load_dataset

        log.info("sft_loading_model", model=self._config["base_model"],
                 load_in_4bit=self._config.get("load_in_4bit", False))

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=self._config["base_model"],
            max_seq_length=self._config["max_seq_length"],
            load_in_4bit=self._config.get("load_in_4bit", False),
            load_in_16bit=self._config.get("load_in_16bit", True),
            full_finetuning=False,
        )

        model = FastLanguageModel.get_peft_model(
            model,
            r=self._config["lora_r"],
            lora_alpha=self._config["lora_alpha"],
            lora_dropout=self._config["lora_dropout"],
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                          "gate_proj", "up_proj", "down_proj"],
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=3407,
            max_seq_length=self._config["max_seq_length"],
        )

        # Load dataset — messages format with chat template
        dataset = load_dataset("json", data_files=str(train_file), split="train")

        # Apply chat template to format messages into training text
        def format_messages(example):
            messages = example["messages"]
            text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=False
            )
            return {"text": text}

        dataset = dataset.map(format_messages, remove_columns=dataset.column_names)

        log.info("sft_dataset_formatted", examples=len(dataset),
                 sample_len=len(dataset[0]["text"]))

        # Training config
        training_args = SFTConfig(
            output_dir=str(self._output_dir / "sft_checkpoints"),
            max_seq_length=self._config["max_seq_length"],
            num_train_epochs=self._config["num_epochs"],
            per_device_train_batch_size=self._config["batch_size"],
            gradient_accumulation_steps=self._config["gradient_accumulation_steps"],
            learning_rate=self._config["learning_rate"],
            warmup_ratio=self._config["warmup_ratio"],
            weight_decay=self._config["weight_decay"],
            logging_steps=self._config["logging_steps"],
            save_steps=self._config["save_steps"],
            bf16=self._config["bf16"],
            optim="adamw_8bit",
            seed=3407,
            report_to="none",
        )

        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            args=training_args,
            train_dataset=dataset,
        )

        log.info("sft_training_start", epochs=self._config["num_epochs"],
                 dataset_size=len(dataset))
        train_result = trainer.train()

        # Save LoRA adapter
        adapter_dir = self._output_dir / "sft_adapter"
        model.save_pretrained(str(adapter_dir))
        tokenizer.save_pretrained(str(adapter_dir))

        # Export to GGUF for Ollama
        gguf_dir = self._output_dir / "gguf"
        gguf_dir.mkdir(exist_ok=True)
        quant = self._config["gguf_quantization"]
        try:
            model.save_pretrained_gguf(str(gguf_dir), tokenizer,
                                        quantization_method=quant)
            log.info("gguf_exported", dir=str(gguf_dir), quant=quant)
            gguf_path = str(gguf_dir)
        except Exception as e:
            log.warning("gguf_export_failed", error=str(e))
            gguf_path = None

        report.status = "completed"
        report.metrics = {
            "mode": "trained",
            "train_loss": train_result.training_loss,
            "train_steps": train_result.global_step,
            "adapter_path": str(adapter_dir),
            "gguf_path": gguf_path,
            "gguf_quantization": quant,
        }
        log.info("sft_training_complete",
                loss=train_result.training_loss,
                steps=train_result.global_step)

    def _generate_sft_script(self, train_file: Path) -> str:
        """Generate a standalone SFT training script."""
        config = self._config
        return f'''#!/usr/bin/env python3
"""JARVIS SFT Training Script — Auto-generated.

Run: pip install unsloth trl datasets
Then: python {self._output_dir}/run_sft.py
"""
from unsloth import FastLanguageModel
from trl import SFTTrainer, SFTConfig
from datasets import load_dataset

max_seq_length = {config["max_seq_length"]}

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{config["base_model"]}",
    max_seq_length=max_seq_length,
    load_in_4bit={config.get("load_in_4bit", False)},
    load_in_16bit={config.get("load_in_16bit", True)},
    full_finetuning=False,
)

model = FastLanguageModel.get_peft_model(
    model,
    r={config["lora_r"]},
    lora_alpha={config["lora_alpha"]},
    lora_dropout={config["lora_dropout"]},
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
    max_seq_length=max_seq_length,
)

dataset = load_dataset("json", data_files="{train_file}", split="train")

def format_messages(example):
    text = tokenizer.apply_chat_template(
        example["messages"], tokenize=False, add_generation_prompt=False
    )
    return {{"text": text}}

dataset = dataset.map(format_messages, remove_columns=dataset.column_names)
print(f"Dataset: {{len(dataset)}} examples")

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    args=SFTConfig(
        output_dir="{self._output_dir}/sft_checkpoints",
        max_seq_length=max_seq_length,
        num_train_epochs={config["num_epochs"]},
        per_device_train_batch_size={config["batch_size"]},
        gradient_accumulation_steps={config["gradient_accumulation_steps"]},
        learning_rate={config["learning_rate"]},
        warmup_ratio={config["warmup_ratio"]},
        weight_decay={config["weight_decay"]},
        logging_steps={config["logging_steps"]},
        bf16={config["bf16"]},
        optim="adamw_8bit",
        seed=3407,
        report_to="none",
    ),
    train_dataset=dataset,
)

result = trainer.train()
print(f"Training complete! Loss: {{result.training_loss:.4f}}")

model.save_pretrained("{self._output_dir}/sft_adapter")
tokenizer.save_pretrained("{self._output_dir}/sft_adapter")

model.save_pretrained_gguf("{self._output_dir}/gguf", tokenizer,
                           quantization_method="{config["gguf_quantization"]}")
print(f"GGUF exported to {self._output_dir}/gguf")
'''

    # --- DPO Training ---

    def train_dpo(self) -> TrainingReport:
        """Run DPO preference alignment training."""
        report = TrainingReport(stage="dpo")
        report.started_at = datetime.now(timezone.utc).isoformat()
        report.status = "running"
        start = time.monotonic()

        # Load dataset
        records, count = self.prepare_dpo_dataset()
        report.dataset_size = count

        if count < 50:
            report.status = "failed"
            report.error = f"Need at least 50 DPO records, have {count}. Keep collecting feedback."
            report.duration_seconds = time.monotonic() - start
            self._save_report(report)
            return report

        # Write consolidated training file
        train_file = self._output_dir / "dpo_train.jsonl"
        with open(train_file, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # Generate DPO script
        script = self._generate_dpo_script(train_file)
        script_path = self._output_dir / "run_dpo.py"
        script_path.write_text(script, encoding="utf-8")

        report.status = "completed"
        report.metrics = {
            "mode": "script_generated",
            "script_path": str(script_path),
            "dataset_size": count,
        }
        report.duration_seconds = time.monotonic() - start
        report.completed_at = datetime.now(timezone.utc).isoformat()
        self._save_report(report)

        log.info("dpo_script_generated", path=str(script_path), records=count)
        return report

    def _generate_dpo_script(self, train_file: Path) -> str:
        """Generate standalone DPO training script."""
        config = self._config
        return f'''#!/usr/bin/env python3
"""JARVIS DPO Training Script — Auto-generated.

Run after SFT training. Uses the SFT adapter as base.
"""
from unsloth import FastLanguageModel
from trl import DPOTrainer, DPOConfig
from datasets import load_dataset
import torch

# Load SFT-adapted model
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{self._output_dir}/sft_adapter",
    max_seq_length={config["max_seq_length"]},
    dtype=torch.float16,
    load_in_4bit=True,
)

model = FastLanguageModel.get_peft_model(
    model,
    r={config["lora_r"] // 2},
    lora_alpha={config["lora_alpha"] // 2},
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
)

# Load DPO dataset
dataset = load_dataset("json", data_files="{train_file}", split="train")

# DPO Training
dpo_config = DPOConfig(
    output_dir="{self._output_dir}/dpo_checkpoints",
    num_train_epochs=1,
    per_device_train_batch_size=2,
    gradient_accumulation_steps=8,
    learning_rate=5e-5,
    beta={config["dpo_beta"]},
    logging_steps=5,
    bf16={config["bf16"]},
    report_to="none",
)

trainer = DPOTrainer(
    model=model,
    ref_model=None,  # Unsloth handles reference model
    tokenizer=tokenizer,
    args=dpo_config,
    train_dataset=dataset,
)

result = trainer.train()
print(f"DPO complete! Loss: {{result.training_loss:.4f}}")

model.save_pretrained("{self._output_dir}/dpo_adapter")
tokenizer.save_pretrained("{self._output_dir}/dpo_adapter")
'''

    # --- Evaluation ---

    def evaluate(self) -> TrainingReport:
        """Evaluate fine-tuned model vs base model."""
        report = TrainingReport(stage="evaluate")
        report.started_at = datetime.now(timezone.utc).isoformat()
        start = time.monotonic()

        # Check if we have test data
        sft_records, _ = self.prepare_sft_dataset()
        if len(sft_records) < 10:
            report.status = "failed"
            report.error = "Not enough data for evaluation"
            self._save_report(report)
            return report

        # Use last 10% as test set
        test_size = max(10, len(sft_records) // 10)
        test_records = sft_records[-test_size:]

        report.status = "completed"
        report.dataset_size = len(test_records)
        report.metrics = {
            "test_size": len(test_records),
            "total_training_data": len(sft_records),
            "adapter_exists": (self._output_dir / "sft_adapter").exists(),
            "dpo_adapter_exists": (self._output_dir / "dpo_adapter").exists(),
        }

        report.duration_seconds = time.monotonic() - start
        report.completed_at = datetime.now(timezone.utc).isoformat()
        self._save_report(report)
        return report

    # --- Export to Ollama ---

    def export_to_ollama(self, model_name: str = "jarvis-brain") -> TrainingReport:
        """Export fine-tuned GGUF model to Ollama."""
        report = TrainingReport(stage="export")
        report.started_at = datetime.now(timezone.utc).isoformat()
        start = time.monotonic()

        # Find GGUF file
        gguf_dir = self._output_dir / "gguf"
        gguf_file = None
        if gguf_dir.exists():
            gguf_files = list(gguf_dir.glob("*.gguf"))
            if gguf_files:
                gguf_file = gguf_files[0]  # Take first GGUF file

        if not gguf_file:
            report.status = "failed"
            report.error = "No GGUF file found. Run SFT training first."
            self._save_report(report)
            return report

        # Generate Modelfile for Ollama using GGUF
        modelfile_content = f"""FROM {gguf_file}
PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER num_ctx {self._config["max_seq_length"]}

SYSTEM \"\"\"Bạn là JARVIS — trợ lý AI cá nhân thông minh. Bạn trả lời bằng tiếng Việt, thân thiện và hữu ích.\"\"\"
"""

        modelfile_path = self._output_dir / "Modelfile"
        modelfile_path.write_text(modelfile_content, encoding="utf-8")

        ollama_bin = Path.home() / "ollama" / "bin" / "ollama"
        if not ollama_bin.exists():
            ollama_bin = Path("ollama")

        try:
            result = subprocess.run(
                [str(ollama_bin), "create", model_name, "-f", str(modelfile_path)],
                capture_output=True, text=True, timeout=600,
            )

            if result.returncode == 0:
                report.status = "completed"
                report.metrics = {
                    "model_name": model_name,
                    "gguf_file": str(gguf_file),
                    "modelfile": str(modelfile_path),
                    "output": result.stdout[:500],
                }
                log.info("model_exported_to_ollama", name=model_name)
            else:
                report.status = "failed"
                report.error = f"Ollama create failed: {result.stderr[:500]}"
        except FileNotFoundError:
            report.status = "failed"
            report.error = "Ollama binary not found"
            report.metrics = {
                "modelfile_path": str(modelfile_path),
                "manual_command": f"ollama create {model_name} -f {modelfile_path}",
            }
        except subprocess.TimeoutExpired:
            report.status = "failed"
            report.error = "Ollama model creation timed out (10 min)"

        report.duration_seconds = time.monotonic() - start
        report.completed_at = datetime.now(timezone.utc).isoformat()
        self._save_report(report)
        return report

    # --- Pipeline Orchestration ---

    def run_full_pipeline(self) -> list[TrainingReport]:
        """Run the complete training pipeline: SFT -> DPO -> Evaluate -> Export."""
        reports = []

        log.info("full_pipeline_start")

        # Stage 1: SFT
        sft_report = self.train_sft()
        reports.append(sft_report)
        if sft_report.status == "failed" and "Need at least" in sft_report.error:
            log.info("pipeline_stopped", reason="insufficient_sft_data")
            return reports

        # Stage 2: DPO
        dpo_report = self.train_dpo()
        reports.append(dpo_report)

        # Stage 3: Evaluate
        eval_report = self.evaluate()
        reports.append(eval_report)

        # Stage 4: Export
        export_report = self.export_to_ollama()
        reports.append(export_report)

        log.info("full_pipeline_complete",
                stages=len(reports),
                successful=sum(1 for r in reports if r.status == "completed"))

        return reports

    def get_status(self) -> dict:
        """Get current training pipeline status."""
        dataset_stats = self.get_dataset_stats()

        # Check for existing artifacts
        has_sft_adapter = (self._output_dir / "sft_adapter").exists()
        has_dpo_adapter = (self._output_dir / "dpo_adapter").exists()
        has_sft_script = (self._output_dir / "run_sft.py").exists()
        has_dpo_script = (self._output_dir / "run_dpo.py").exists()

        return {
            "dataset": dataset_stats,
            "artifacts": {
                "sft_adapter": has_sft_adapter,
                "dpo_adapter": has_dpo_adapter,
                "sft_script": has_sft_script,
                "dpo_script": has_dpo_script,
            },
            "recent_reports": self._get_recent_reports(3),
        }

    # --- Report Management ---

    def _save_report(self, report: TrainingReport) -> None:
        """Save training report to disk."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        report_path = self._reports_dir / f"training_{report.stage}_{ts}.json"

        try:
            report_path.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("training_report_saved", path=str(report_path))
        except Exception as e:
            log.error("training_report_save_error", error=str(e))

    def _get_recent_reports(self, limit: int = 5) -> list[dict]:
        """Get recent training reports."""
        reports = []
        for f in sorted(self._reports_dir.glob("training_*.json"), reverse=True)[:limit]:
            try:
                reports.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return reports


# --- CLI Entry Point ---

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="JARVIS Training Pipeline")
    parser.add_argument("--stage", choices=["sft", "dpo", "evaluate", "export", "status", "full"],
                       default="status", help="Training stage to run")
    parser.add_argument("--profile", choices=list(_MODEL_PROFILES), default=None,
                       help="Model profile (4b or 14b). Overrides base_model and loading config.")
    parser.add_argument("--model-name", default=None, help="Ollama model name for export")
    args = parser.parse_args()

    config = get_profile_config(args.profile) if args.profile else None
    pipeline = TrainingPipeline(config=config)

    # Default model name based on profile
    model_name = args.model_name
    if not model_name:
        if args.profile == "14b":
            model_name = "jarvis-brain-14b"
        else:
            model_name = "jarvis-brain"

    if args.stage == "status":
        status = pipeline.get_status()
        print(json.dumps(status, indent=2))
    elif args.stage == "sft":
        report = pipeline.train_sft()
        print(json.dumps(report.to_dict(), indent=2))
    elif args.stage == "dpo":
        report = pipeline.train_dpo()
        print(json.dumps(report.to_dict(), indent=2))
    elif args.stage == "evaluate":
        report = pipeline.evaluate()
        print(json.dumps(report.to_dict(), indent=2))
    elif args.stage == "export":
        report = pipeline.export_to_ollama(model_name)
        print(json.dumps(report.to_dict(), indent=2))
    elif args.stage == "full":
        reports = pipeline.run_full_pipeline()
        for r in reports:
            print(json.dumps(r.to_dict(), indent=2))
