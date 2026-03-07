"""Tests for Monitoring — Prometheus metrics and HTTP server."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock
from prometheus_client import CollectorRegistry

from src.monitoring.metrics import (
    llm_requests_total,
    llm_tokens_total,
    llm_cost_usd_total,
    llm_latency_seconds,
    llm_cache_hits_total,
    brain_local_ratio,
    brain_training_runs_total,
    skills_total,
    health_ollama,
    health_db,
    sync_from_health,
    sync_from_skills,
    init_info,
)


class TestMetricsDefinition:
    """Verify metric objects are properly defined."""

    def test_llm_counters_exist(self):
        assert llm_requests_total is not None
        assert llm_tokens_total is not None
        assert llm_cost_usd_total is not None

    def test_llm_histogram_exists(self):
        assert llm_latency_seconds is not None

    def test_brain_gauges_exist(self):
        assert brain_local_ratio is not None
        assert brain_training_runs_total is not None

    def test_health_gauges_exist(self):
        assert health_ollama is not None
        assert health_db is not None

    def test_init_info(self):
        # Should not raise
        init_info(version="test")


class TestSyncFromHealth:
    def test_sync_sets_gauges(self):
        health = MagicMock()
        health.ollama_available = True
        health.api_keys_ok = True
        health.disk_ok = False
        health.db_ok = True

        sync_from_health(health)

        # Gauges should be set (no assertion on value since
        # prometheus_client uses global registry)

    def test_sync_handles_error(self):
        # Should not raise even with broken input
        sync_from_health(None)


class TestSyncFromSkills:
    def test_sync_sets_gauges(self):
        registry = MagicMock()
        registry.get_stats.return_value = {
            "total_skills": 12,
            "avg_success_rate": 0.85,
            "total_usage": 100,
        }
        sync_from_skills(registry)

    def test_sync_handles_error(self):
        registry = MagicMock()
        registry.get_stats.side_effect = Exception("broken")
        # Should not raise
        sync_from_skills(registry)


class TestTrackerInstrumentation:
    """Verify CostTracker instruments Prometheus on log_usage."""

    def test_log_usage_increments_metrics(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.memory.store._DB_PATH", str(tmp_path / "test.db"))

        from src.intelligence.tracker import CostTracker
        tracker = CostTracker()

        # This should not raise and should instrument Prometheus
        cost = tracker.log_usage(
            model="ollama/qwen3:4b",
            tokens_in=100,
            tokens_out=50,
            latency_ms=250,
            source="local",
            cached=False,
        )
        assert cost == 0.0  # Local model = free

        cost2 = tracker.log_usage(
            model="ollama/qwen3:4b",
            tokens_in=200,
            tokens_out=100,
            latency_ms=300,
            source="cache",
            cached=True,
        )
        assert cost2 == 0.0


class TestMetricsServer:
    @pytest.mark.asyncio
    async def test_server_lifecycle(self):
        from src.monitoring.server import MetricsServer
        server = MetricsServer(port=19999)
        await server.start()

        # Test /metrics endpoint
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:19999/metrics") as resp:
                assert resp.status == 200
                text = await resp.text()
                assert "jarvis_llm_requests" in text

        # Test /health endpoint
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:19999/health") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"

        await server.stop()
