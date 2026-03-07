"""Metrics HTTP Server — Serves /metrics for Prometheus scraping.

Lightweight aiohttp server on port 9090 (configurable).
Also serves /health as a quick JSON health check endpoint.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp import web
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from src.monitoring.metrics import (
    init_info,
    sync_from_health,
    sync_from_skills,
    sync_from_tracker,
)
from src.utils.logging import get_logger

log = get_logger("monitoring.server")

_DEFAULT_PORT = 9090


class MetricsServer:
    """Async HTTP server exposing Prometheus /metrics endpoint."""

    def __init__(
        self,
        port: int = _DEFAULT_PORT,
        tracker=None,
        health_monitor=None,
        skill_registry=None,
    ) -> None:
        self._port = port
        self._tracker = tracker
        self._health_monitor = health_monitor
        self._skill_registry = skill_registry
        self._app = web.Application()
        self._app.router.add_get("/metrics", self._handle_metrics)
        self._app.router.add_get("/health", self._handle_health)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        init_info()

    async def start(self) -> None:
        """Start the metrics server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        try:
            await self._site.start()
            log.info("metrics_server_started", port=self._port)
        except OSError as e:
            log.warning("metrics_server_port_in_use", port=self._port, error=str(e))

    async def stop(self) -> None:
        """Stop the metrics server."""
        if self._runner:
            await self._runner.cleanup()
            log.info("metrics_server_stopped")

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """Prometheus scrape endpoint."""
        # Sync latest stats before responding
        if self._tracker:
            sync_from_tracker(self._tracker)
        if self._health_monitor:
            sync_from_health(self._health_monitor.health)
        if self._skill_registry:
            sync_from_skills(self._skill_registry)

        metrics_output = generate_latest()
        return web.Response(
            body=metrics_output,
            content_type="text/plain",
        )

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Quick health check endpoint."""
        health = {"status": "ok"}

        if self._health_monitor:
            h = self._health_monitor.health
            health.update({
                "ollama": h.ollama_available,
                "api_keys": h.api_keys_ok,
                "disk": h.disk_ok,
                "db": h.db_ok,
                "degraded_mode": h.degraded_mode or "none",
            })
            if h.degraded_mode:
                health["status"] = "degraded"

        return web.json_response(health)
