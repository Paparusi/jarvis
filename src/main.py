"""JARVIS v2 — Entry Point.

Chat with JARVIS via Telegram or CLI.

Usage:
    python -m src.main              # Telegram (default)
    python -m src.main --cli        # CLI mode
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from src.utils.config import get_env, load_config
from src.utils.logging import get_logger, setup_logging


async def run_telegram() -> None:
    """Run JARVIS with Telegram adapter."""
    log = get_logger("main")

    token = get_env("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("missing_telegram_token", hint="Set TELEGRAM_BOT_TOKEN in .env file")
        sys.exit(1)

    api_key = get_env("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("missing_api_key", hint="Set ANTHROPIC_API_KEY in .env file")
        sys.exit(1)

    # --- Build shared application container ---
    from src.app import JarvisApp

    app = JarvisApp()
    app.init_dreamtime()
    app.init_health()
    app.init_swarm()
    app.init_proactive()
    app.init_bounty()
    app.init_trading_brain()
    app.init_company()
    app.init_ops_engine()

    # Connect MCP servers (async)
    await app.connect_mcp()

    # Start Prometheus metrics server
    metrics_server = None
    try:
        from src.monitoring.server import MetricsServer

        metrics_server = MetricsServer(port=9090)
        metrics_server._tracker = app.router._tracker
        metrics_server._health_monitor = app.health_monitor
        metrics_server._skill_registry = app.skill_registry
        await metrics_server.start()
    except Exception as e:
        log.warning("metrics_server_failed", error=str(e))

    # Start web dashboard API server
    web_server = None
    try:
        import uvicorn
        from src.gateway.channels.web import create_app

        web_app = create_app(app)
        web_config = uvicorn.Config(web_app, host="0.0.0.0", port=8000, log_level="warning")
        web_server = uvicorn.Server(web_config)
        asyncio.create_task(web_server.serve())
        log.info("web_server_started", port=8000)
    except Exception as e:
        log.warning("web_server_failed", error=str(e))

    # Start OpsEngine scheduler
    if app.ops_engine:
        await app.ops_engine.start()

    # Create adapter with shared container
    from src.gateway.channels.telegram import TelegramAdapter

    adapter = TelegramAdapter(app, token=token)
    await adapter.start()

    log.info("jarvis_ready", channel="telegram")
    print("\n🤖 JARVIS is running! Chat with me on Telegram.")
    print("   Dashboard: http://localhost:3000")
    print("   API: http://localhost:8000")
    print("   Metrics: http://localhost:9090/metrics")
    print("   Press Ctrl+C to stop.\n")

    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("shutdown_signal_received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    log.info("jarvis_shutting_down")
    if app.ops_engine:
        await app.ops_engine.stop()
    if web_server:
        web_server.should_exit = True
    if metrics_server:
        await metrics_server.stop()
    await adapter.stop()
    await app.shutdown()
    log.info("jarvis_stopped")


async def run_cli() -> None:
    """Run JARVIS with CLI adapter."""
    log = get_logger("main")

    api_key = get_env("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("missing_api_key", hint="Set ANTHROPIC_API_KEY in .env file")
        sys.exit(1)

    # --- Build shared application container ---
    from src.app import JarvisApp

    app = JarvisApp()
    app.init_dreamtime()
    app.init_bounty()
    app.init_trading_brain()
    app.init_company()

    from src.gateway.channels.cli import CLIAdapter

    adapter = CLIAdapter(app)

    log.info("jarvis_ready", channel="cli")
    await adapter.start()

    await app.shutdown()
    log.info("jarvis_stopped", channel="cli")


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS v2 — Personal AI Agent")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode (interactive terminal)")
    args = parser.parse_args()

    setup_logging()
    log = get_logger("main")

    config = load_config()
    log.info("jarvis_starting", version="2.0.0-alpha.1")

    try:
        if args.cli:
            asyncio.run(run_cli())
        else:
            asyncio.run(run_telegram())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
