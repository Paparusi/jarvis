"""Data Processor — Brain Independence Phase B.

Xử lý raw interaction logs → training-ready data:
1. Load raw JSONL interactions
2. Deduplicate (by content similarity)
3. Quality filter (remove errors, too-short responses)
4. Format thành ChatML cho SFT
5. Tạo DPO pairs từ local-fail/cloud-success patterns
6. Split train/val/test
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("brain.processor")


class DataProcessor:
    """Process raw interactions into training-ready datasets."""

    def __init__(self) -> None:
        root = get_project_root() / "training" / "data"
        self._raw_dir = root / "raw"
        self._processed_dir = root / "processed"
        self._preferences_dir = root / "preferences"
        self._processed_dir.mkdir(parents=True, exist_ok=True)
        self._preferences_dir.mkdir(parents=True, exist_ok=True)

    def process_all(self) -> dict:
        """Run the full processing pipeline. Returns stats."""
        raw_records = self._load_raw()
        if not raw_records:
            log.info("no_raw_data")
            return {"raw": 0, "sft": 0, "dpo": 0}

        # 1. Quality filter
        good = self._quality_filter(raw_records)

        # 2. Deduplicate
        unique = self._deduplicate(good)

        # 3. Generate SFT data (ChatML format)
        sft_data = self._format_sft(unique)

        # 4. Generate DPO preference pairs
        dpo_data = self._generate_dpo_pairs(raw_records)

        # 5. Save
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        sft_path = self._processed_dir / f"sft_{timestamp}.jsonl"
        self._save_jsonl(sft_data, sft_path)

        dpo_path = None
        if dpo_data:
            dpo_path = self._preferences_dir / f"dpo_{timestamp}.jsonl"
            self._save_jsonl(dpo_data, dpo_path)

        stats = {
            "raw": len(raw_records),
            "after_filter": len(good),
            "after_dedup": len(unique),
            "sft": len(sft_data),
            "dpo": len(dpo_data),
            "sft_file": str(sft_path),
            "dpo_file": str(dpo_path) if dpo_path else None,
        }
        log.info("processing_complete", **stats)
        return stats

    def _load_raw(self) -> list[dict]:
        """Load all raw JSONL files."""
        records = []
        for f in sorted(self._raw_dir.glob("interactions-*.jsonl")):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return records

    def _quality_filter(self, records: list[dict]) -> list[dict]:
        """Filter out low-quality interactions."""
        good = []
        for r in records:
            response = r.get("model_response", "")
            user_msg = r.get("user_message", "")

            # Skip error responses
            if "xin lỗi" in response.lower() and "lỗi" in response.lower():
                continue
            # Skip empty or too-short responses
            if len(response.strip()) < 10:
                continue
            # Skip empty user messages
            if len(user_msg.strip()) < 2:
                continue
            # Skip if response starts with error prefix
            if response.startswith("Xin lỗi, tôi gặp lỗi"):
                continue

            good.append(r)
        return good

    def _deduplicate(self, records: list[dict]) -> list[dict]:
        """Remove duplicate interactions (same user message)."""
        seen = set()
        unique = []
        for r in records:
            key = r.get("user_message", "").strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def _format_sft(self, records: list[dict]) -> list[dict]:
        """Format records into ChatML for SFT training."""
        sft_data = []
        for r in records:
            entry = {
                "messages": [
                    {"role": "system", "content": "Bạn là JARVIS — trợ lý AI cá nhân thông minh."},
                    {"role": "user", "content": r["user_message"]},
                    {"role": "assistant", "content": r["model_response"]},
                ],
                "metadata": {
                    "source_model": r.get("model_used", "unknown"),
                    "timestamp": r.get("timestamp", ""),
                    "tokens_in": r.get("tokens_in", 0),
                    "tokens_out": r.get("tokens_out", 0),
                },
            }
            sft_data.append(entry)
        return sft_data

    def _generate_dpo_pairs(self, records: list[dict]) -> list[dict]:
        """Generate DPO preference pairs.

        Pattern: same/similar query handled by both local AND cloud
        → cloud response = chosen, local response = rejected
        (assumption: cloud model gives better answers)
        """
        # Group by user message
        by_message: dict[str, list[dict]] = {}
        for r in records:
            key = r.get("user_message", "").strip().lower()
            if key:
                by_message.setdefault(key, []).append(r)

        dpo_pairs = []
        for msg_key, interactions in by_message.items():
            local_responses = [r for r in interactions if "ollama" in r.get("model_used", "")]
            cloud_responses = [r for r in interactions if "claude" in r.get("model_used", "")]

            if local_responses and cloud_responses:
                # Cloud = chosen, Local = rejected
                chosen = cloud_responses[0]
                rejected = local_responses[0]
                dpo_pairs.append({
                    "prompt": chosen["user_message"],
                    "chosen": chosen["model_response"],
                    "rejected": rejected["model_response"],
                    "chosen_model": chosen["model_used"],
                    "rejected_model": rejected["model_used"],
                })

        return dpo_pairs

    @staticmethod
    def _save_jsonl(data: list[dict], path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def get_stats(self) -> dict:
        """Get processing statistics."""
        sft_files = list(self._processed_dir.glob("sft_*.jsonl"))
        dpo_files = list(self._preferences_dir.glob("dpo_*.jsonl"))
        return {
            "sft_datasets": len(sft_files),
            "dpo_datasets": len(dpo_files),
            "latest_sft": str(sft_files[-1]) if sft_files else None,
            "latest_dpo": str(dpo_files[-1]) if dpo_files else None,
        }
