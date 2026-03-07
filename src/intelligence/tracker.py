"""Cost Tracker + Circuit Breaker — Theo dõi chi phí và bảo vệ hệ thống.

Cost Tracker: log mọi API call, tính chi phí, cung cấp thống kê.
Circuit Breaker: tự động fallback khi model gặp lỗi liên tục.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.memory.store import get_connection
from src.utils.logging import get_logger

log = get_logger("intelligence.tracker")


@dataclass
class ModelHealth:
    """Track health of a model endpoint."""
    failures: int = 0
    successes: int = 0
    last_failure: float = 0.0
    circuit_open: bool = False
    circuit_open_until: float = 0.0

    # Circuit breaker config
    failure_threshold: int = 3
    recovery_timeout: float = 60.0  # seconds

    def record_success(self) -> None:
        self.successes += 1
        self.failures = 0
        self.circuit_open = False

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.failure_threshold:
            self.circuit_open = True
            self.circuit_open_until = time.time() + self.recovery_timeout
            log.warning("circuit_opened", failures=self.failures)

    def is_available(self) -> bool:
        if not self.circuit_open:
            return True
        if time.time() > self.circuit_open_until:
            # Half-open: allow one request to test
            self.circuit_open = False
            self.failures = 0
            log.info("circuit_half_open")
            return True
        return False


class CostTracker:
    """Track API usage costs across all models."""

    # Approximate pricing per 1M tokens (USD)
    PRICING = {
        "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
        "claude-opus-4-6": {"input": 15.0, "output": 75.0},
        # Local models = free
        "ollama/qwen3:4b": {"input": 0.0, "output": 0.0},
        "ollama/qwen3.5:4b": {"input": 0.0, "output": 0.0},
        "ollama/qwen3:8b": {"input": 0.0, "output": 0.0},
        "ollama/qwen3:14b": {"input": 0.0, "output": 0.0},
        "ollama/qwen3:30b-a3b": {"input": 0.0, "output": 0.0},
        "ollama/jarvis-brain": {"input": 0.0, "output": 0.0},
        "ollama/jarvis-brain-14b": {"input": 0.0, "output": 0.0},
    }

    def __init__(self) -> None:
        self._model_health: dict[str, ModelHealth] = {}
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cost_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                latency_ms INTEGER DEFAULT 0,
                source TEXT DEFAULT 'api',
                cached INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()

    def estimate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        pricing = self.PRICING.get(model, {"input": 3.0, "output": 15.0})
        return (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000

    def log_usage(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        source: str = "api",
        cached: bool = False,
    ) -> float:
        """Log a model usage event. Returns estimated cost."""
        cost = self.estimate_cost(model, tokens_in, tokens_out)
        conn = get_connection()
        conn.execute(
            "INSERT INTO cost_log (model, tokens_in, tokens_out, cost_usd, "
            "latency_ms, source, cached) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (model, tokens_in, tokens_out, cost, latency_ms, source, int(cached)),
        )
        conn.commit()

        # Prometheus metrics
        try:
            from src.monitoring.metrics import (
                llm_requests_total,
                llm_tokens_total,
                llm_cost_usd_total,
                llm_latency_seconds,
                llm_cache_hits_total,
                llm_cache_misses_total,
            )
            llm_requests_total.labels(model=model, source=source).inc()
            llm_tokens_total.labels(model=model, direction="input").inc(tokens_in)
            llm_tokens_total.labels(model=model, direction="output").inc(tokens_out)
            llm_cost_usd_total.labels(model=model).inc(cost)
            llm_latency_seconds.labels(model=model).observe(latency_ms / 1000.0)
            if cached:
                llm_cache_hits_total.inc()
            else:
                llm_cache_misses_total.inc()
        except Exception:
            pass  # metrics are best-effort

        return cost

    def record_success(self, model: str) -> None:
        self._get_health(model).record_success()

    def record_failure(self, model: str) -> None:
        self._get_health(model).record_failure()

    def is_model_available(self, model: str) -> bool:
        return self._get_health(model).is_available()

    def _get_health(self, model: str) -> ModelHealth:
        if model not in self._model_health:
            self._model_health[model] = ModelHealth()
        return self._model_health[model]

    def get_stats(self, days: int = 30) -> dict:
        """Get usage statistics for the last N days."""
        conn = get_connection()
        row = conn.execute(
            "SELECT "
            "COUNT(*) as total_calls, "
            "COALESCE(SUM(tokens_in), 0) as total_tokens_in, "
            "COALESCE(SUM(tokens_out), 0) as total_tokens_out, "
            "COALESCE(SUM(cost_usd), 0) as total_cost, "
            "COALESCE(AVG(latency_ms), 0) as avg_latency, "
            "COALESCE(SUM(cached), 0) as cache_hits "
            f"FROM cost_log WHERE created_at > datetime('now', '-{days} days')"
        ).fetchone()

        per_model = conn.execute(
            "SELECT model, COUNT(*) as calls, "
            "COALESCE(SUM(cost_usd), 0) as cost, "
            "COALESCE(AVG(latency_ms), 0) as avg_latency "
            f"FROM cost_log WHERE created_at > datetime('now', '-{days} days') "
            "GROUP BY model ORDER BY cost DESC"
        ).fetchall()

        # Calculate local vs cloud ratio
        local_calls = sum(
            r["calls"] for r in per_model
            if r["model"].startswith("ollama/")
        )
        cloud_calls = sum(
            r["calls"] for r in per_model
            if not r["model"].startswith("ollama/")
        )
        total = local_calls + cloud_calls
        local_ratio = local_calls / total if total > 0 else 0.0

        return {
            "total_calls": row["total_calls"],
            "total_tokens_in": row["total_tokens_in"],
            "total_tokens_out": row["total_tokens_out"],
            "total_cost_usd": round(row["total_cost"], 4),
            "avg_latency_ms": round(row["avg_latency"]),
            "cache_hits": row["cache_hits"],
            "local_calls": local_calls,
            "cloud_calls": cloud_calls,
            "local_ratio": round(local_ratio, 3),
            "per_model": [dict(r) for r in per_model],
        }
