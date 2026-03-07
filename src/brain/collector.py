"""Data Collector — Brain Independence Phase A.

Log MỌI interaction từ ngày 1 thành training data.
Mỗi lần user chat = 1 InteractionRecord = 1 dòng "vàng" cho future training.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.gateway.models import AgentResponse, Channel, MessageEnvelope
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("brain.collector")


class DataCollector:
    """Collects interaction data for Brain Independence Pipeline.

    Phase 1: Ghi ra JSONL file (đơn giản, hoạt động ngay).
    Phase 2+: Sẽ thêm PostgreSQL storage.
    """

    def __init__(self) -> None:
        self._data_dir = get_project_root() / "training" / "data" / "raw"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._today_file: Path | None = None
        self._today_str: str = ""

    def _get_file(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._today_str:
            self._today_str = today
            self._today_file = self._data_dir / f"interactions-{today}.jsonl"
        return self._today_file

    async def log_interaction(
        self,
        envelope: MessageEnvelope,
        response: AgentResponse,
        user_feedback: str | None = None,
        skills_used: list[str] | None = None,
        routing_info: dict | None = None,
    ) -> None:
        """Log a complete interaction (request + response) for future training.

        routing_info can include: complexity, was_escalated, was_cached, source (local/cloud/cache).
        """
        route = routing_info or {}
        is_cloud = not response.model_used.startswith("ollama/")
        record = {
            "id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": envelope.session_id,
            # Input
            "channel": envelope.channel.value,
            "user_id": envelope.user_id,
            "user_message": envelope.content,
            # Output
            "model_used": response.model_used,
            "model_response": response.content,
            "reasoning_trace": response.reasoning_trace,
            "confidence_score": response.confidence_score,
            # Skills
            "skills_used": skills_used or [],
            # Routing metadata (for training data quality)
            "source": route.get("source", "cloud" if is_cloud else "local"),
            "complexity": route.get("complexity", "unknown"),
            "was_escalated": route.get("was_escalated", False),
            "was_cached": route.get("was_cached", False),
            # Metrics
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "latency_ms": response.latency_ms,
            "cost_usd": response.cost_usd,
            # Feedback (updated later via update_feedback)
            "user_feedback": user_feedback,
            # Training flags
            "quality_score": None,
            "selected_for_training": False,
            # Cloud responses are PRIORITY training data
            "priority_training": is_cloud and response.confidence_score > 0.5,
        }

        filepath = self._get_file()
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        log.info(
            "interaction_logged",
            session_id=envelope.session_id,
            model=response.model_used,
            tokens=response.tokens_in + response.tokens_out,
            file=filepath.name,
        )

    def get_stats(self) -> dict:
        """Get collection statistics."""
        total_files = list(self._data_dir.glob("interactions-*.jsonl"))
        total_records = 0
        for f in total_files:
            with open(f) as fh:
                total_records += sum(1 for _ in fh)
        return {
            "total_files": len(total_files),
            "total_records": total_records,
            "data_dir": str(self._data_dir),
        }
