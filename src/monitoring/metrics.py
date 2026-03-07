"""Prometheus Metrics — Expose JARVIS internals for monitoring.

Defines all Prometheus metrics and a collector that syncs from
internal trackers (CostTracker, SkillRegistry, HealthMonitor).
"""

from __future__ import annotations

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
)

from src.utils.logging import get_logger

log = get_logger("monitoring.metrics")

# ── LLM Router Metrics ──────────────────────────────────────────────

llm_requests_total = Counter(
    "jarvis_llm_requests_total",
    "Total LLM requests",
    ["model", "source"],  # source: cache, local, cloud
)

llm_tokens_total = Counter(
    "jarvis_llm_tokens_total",
    "Total tokens processed",
    ["model", "direction"],  # direction: input, output
)

llm_cost_usd_total = Counter(
    "jarvis_llm_cost_usd_total",
    "Total API cost in USD",
    ["model"],
)

llm_latency_seconds = Histogram(
    "jarvis_llm_latency_seconds",
    "LLM request latency",
    ["model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

llm_cache_hits_total = Counter(
    "jarvis_llm_cache_hits_total",
    "Semantic cache hits",
)

llm_cache_misses_total = Counter(
    "jarvis_llm_cache_misses_total",
    "Semantic cache misses",
)

# ── Brain Independence ───────────────────────────────────────────────

brain_local_ratio = Gauge(
    "jarvis_brain_local_ratio",
    "Ratio of requests handled locally (0.0-1.0)",
)

brain_training_runs_total = Gauge(
    "jarvis_brain_training_runs_total",
    "Total auto-training runs completed",
)

brain_sft_records = Gauge(
    "jarvis_brain_sft_records",
    "Total SFT training records available",
)

brain_dpo_records = Gauge(
    "jarvis_brain_dpo_records",
    "Total DPO training records available",
)

brain_last_train_loss = Gauge(
    "jarvis_brain_last_train_loss",
    "Loss from last training run",
)

brain_eval_accuracy = Gauge(
    "jarvis_brain_eval_accuracy",
    "Model accuracy from latest evaluation benchmark",
)

brain_eval_score = Gauge(
    "jarvis_brain_eval_score",
    "Model score from latest evaluation (0-10)",
)

brain_cloud_fallback_rate = Gauge(
    "jarvis_brain_cloud_fallback_rate",
    "Rate of local→cloud escalations (lower is better)",
)

# ── Skills ────────────────────────────────────────────────────────────

skills_total = Gauge(
    "jarvis_skills_total",
    "Number of loaded skills",
)

skills_usage_total = Counter(
    "jarvis_skills_usage_total",
    "Skill invocations",
    ["skill_name"],
)

skills_avg_success_rate = Gauge(
    "jarvis_skills_avg_success_rate",
    "Average skill success rate (0.0-1.0)",
)

# ── Memory ────────────────────────────────────────────────────────────

memory_entries = Gauge(
    "jarvis_memory_entries",
    "Number of semantic memory entries",
)

memory_retrieval_latency = Histogram(
    "jarvis_memory_retrieval_latency_seconds",
    "Memory retrieval latency",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

# ── System Health ─────────────────────────────────────────────────────

health_ollama = Gauge(
    "jarvis_health_ollama_up",
    "Ollama availability (1=up, 0=down)",
)

health_api_keys = Gauge(
    "jarvis_health_api_keys_ok",
    "API keys valid (1=ok, 0=invalid)",
)

health_disk = Gauge(
    "jarvis_health_disk_ok",
    "Disk space ok (1=ok, 0=low)",
)

health_db = Gauge(
    "jarvis_health_db_ok",
    "Database connectivity (1=ok, 0=down)",
)

# ── Safety ────────────────────────────────────────────────────────────

safety_asr = Gauge(
    "jarvis_safety_asr",
    "Attack Success Rate from last red team test (lower is better)",
)

safety_tests_total = Gauge(
    "jarvis_safety_tests_total",
    "Total safety tests in last evaluation",
)

# ── Agent ─────────────────────────────────────────────────────────────

agent_iterations_total = Counter(
    "jarvis_agent_iterations_total",
    "Total agent loop iterations",
)

agent_tool_calls_total = Counter(
    "jarvis_agent_tool_calls_total",
    "Tool calls by agent",
    ["tool_name"],
)

# ── Swarm ─────────────────────────────────────────────────────────────

swarm_runs_total = Counter(
    "jarvis_swarm_runs_total",
    "Total swarm executions",
)

swarm_agents_total = Counter(
    "jarvis_swarm_agents_total",
    "Total swarm agents created",
)

swarm_retries_total = Counter(
    "jarvis_swarm_retries_total",
    "Total agent retries in swarm",
)

swarm_conflicts_total = Counter(
    "jarvis_swarm_conflicts_total",
    "Conflicts detected between agent results",
)

swarm_latency_seconds = Histogram(
    "jarvis_swarm_latency_seconds",
    "Swarm execution latency",
    buckets=(1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# ── System Info ───────────────────────────────────────────────────────

jarvis_info = Info(
    "jarvis",
    "JARVIS system information",
)


def init_info(version: str = "2.0.0") -> None:
    """Set static system info."""
    jarvis_info.info({
        "version": version,
        "project": "jarvis",
    })


# ── Sync from internal trackers ───────────────────────────────────────

def sync_from_tracker(tracker) -> None:
    """Pull latest stats from CostTracker into Prometheus gauges."""
    try:
        stats = tracker.get_stats(days=30)
        for m in stats.get("per_model", []):
            model = m["model"]
            # We set gauges via _metrics for cost summary
            llm_cost_usd_total._metrics.clear()
        # Brain independence ratio
        per_model = stats.get("per_model", [])
        local_calls = sum(m["calls"] for m in per_model if "ollama" in m["model"])
        total_calls = stats.get("total_calls", 0)
        if total_calls > 0:
            brain_local_ratio.set(local_calls / total_calls)
    except Exception as e:
        log.warning("metrics_sync_tracker_error", error=str(e))


def sync_from_health(health_state) -> None:
    """Pull health state into Prometheus gauges."""
    try:
        health_ollama.set(1 if health_state.ollama_available else 0)
        health_api_keys.set(1 if health_state.api_keys_ok else 0)
        health_disk.set(1 if health_state.disk_ok else 0)
        health_db.set(1 if health_state.db_ok else 0)
    except Exception as e:
        log.warning("metrics_sync_health_error", error=str(e))


def sync_from_skills(registry) -> None:
    """Pull skill stats into Prometheus gauges."""
    try:
        stats = registry.get_stats()
        skills_total.set(stats.get("total_skills", 0))
        skills_avg_success_rate.set(stats.get("avg_success_rate", 0))
    except Exception as e:
        log.warning("metrics_sync_skills_error", error=str(e))
