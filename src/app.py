"""JarvisApp — Central application container (DI).

Single source of truth for all JARVIS components. Adapters receive this
container instead of creating their own component graphs.

Usage:
    app = JarvisApp()
    adapter = TelegramAdapter(app, token="...")
    adapter = CLIAdapter(app)
"""

from __future__ import annotations

import os

from src.brain.collector import DataCollector
from src.brain.processor import DataProcessor
from src.digital_twin.user_model import UserModel
from src.gateway.event_bus import get_event_bus
from src.gateway.session import SessionManager
from src.intelligence.router import LLMRouter
from src.memory.manager import MemoryManager
from src.skills.evolver import SkillEvolver
from src.skills.executor import SkillExecutor
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry
from src.skills.router import SkillRouter
from src.tools.base import ToolRegistry
from src.tools.registry_all import ALL_TOOLS
from src.utils.logging import get_logger

log = get_logger("app")


class JarvisApp:
    """Central DI container — builds the full component graph once."""

    def __init__(self) -> None:
        log.info("jarvis_app_init_start")

        # --- Core infrastructure ---
        self.event_bus = get_event_bus()
        self.sessions = SessionManager()

        # --- Memory layer ---
        self.memory = MemoryManager()
        self.user_model = UserModel()

        # --- Skills layer ---
        self.skill_loader = SkillLoader()
        self.skill_loader.load_all()
        self.skill_registry = SkillRegistry(self.skill_loader)
        self.skill_registry._apply_metrics()
        self.skill_router = SkillRouter(self.skill_loader)
        self.skill_executor = SkillExecutor()

        # --- Tool layer (from centralized registry) ---
        self.tool_registry = ToolRegistry()
        for tool in ALL_TOOLS:
            self.tool_registry.register(tool)
        log.info("tools_registered", count=len(self.tool_registry.get_all()))

        # --- Intelligence layer ---
        skill_summary = self.skill_loader.get_metadata_summary()
        self.router = LLMRouter(
            skill_summary=skill_summary,
            tool_registry=self.tool_registry,
        )

        # --- Brain Independence ---
        self.collector = DataCollector()
        self.processor = DataProcessor()

        # --- Lazy subsystems (initialized per adapter needs) ---
        self.memory_consolidator = None
        self.dreamer = None
        self.evolver = None
        self.dreamtime = None
        self.health_monitor = None
        self.proactive = None
        self.decomposer = None
        self.swarm = None
        self._mcp_bridge = None
        self.bounty_pipeline = None
        self.hunter_pipeline = None
        self.trading_brain = None

        log.info("jarvis_app_init_done")

    def init_dreamtime(self) -> None:
        """Initialize Dreamtime components (memory consolidation + skill evolution)."""
        from src.dreamtime.consolidator import MemoryConsolidator
        from src.dreamtime.dreamer import Dreamer
        from src.dreamtime.scheduler import DreamtimeScheduler

        self.memory_consolidator = MemoryConsolidator(self.memory.semantic)
        self.dreamer = Dreamer(
            collector=self.collector,
            skill_registry=self.skill_registry,
        )
        self.evolver = SkillEvolver(self.skill_registry, self.skill_loader)
        self.dreamtime = DreamtimeScheduler(idle_minutes=30, cron_hour=2, enabled=True)

    def init_health(self) -> None:
        """Initialize Health Monitor and wire to router."""
        from src.metacognition.health_monitor import HealthMonitor

        self.health_monitor = HealthMonitor()
        self.router._health_monitor = self.health_monitor

    def init_swarm(self) -> None:
        """Initialize Swarm Coordinator for multi-agent tasks."""
        from src.swarm.coordinator import SwarmCoordinator
        from src.swarm.decomposer import TaskDecomposer
        from src.swarm.factory import AgentFactory

        self.decomposer = TaskDecomposer(complexity_threshold=40)
        self.swarm = SwarmCoordinator(
            decomposer=self.decomposer,
            factory=AgentFactory(tool_registry=self.tool_registry),
        )

    def init_proactive(self, user_id: str | None = None) -> None:
        """Initialize ProactiveEngine for background insights."""
        from src.intelligence.proactive import ProactiveEngine

        uid = user_id or f"telegram_{os.environ.get('JARVIS_OWNER_ID', '1991690969')}"
        self.proactive = ProactiveEngine(
            memory=self.memory.semantic,
            user_model=self.user_model,
            collector=self.collector,
            user_id=uid,
        )

    def init_bounty(self) -> None:
        """Initialize Bug Bounty Pipeline."""
        from src.bounty.pipeline import BountyPipeline
        from src.bounty.store import get_bounty_connection

        conn = get_bounty_connection()
        self.bounty_pipeline = BountyPipeline(conn, self.tool_registry)

    def init_hunter(self) -> None:
        """Initialize AI Bug Hunter Pipeline."""
        from src.bounty.hunter import HunterPipeline

        async def _llm_call(prompt: str) -> str:
            """Route LLM call through the JARVIS router."""
            result = await self.router.route(prompt, user_id="system_hunter")
            return result.text if hasattr(result, "text") else str(result)

        self.hunter_pipeline = HunterPipeline(
            tool_registry=self.tool_registry,
            llm_fn=_llm_call,
        )

    def init_trading_brain(self) -> None:
        """Initialize Trading Brain — autonomous trading agent."""
        from src.trading.trading_brain import TradingBrain
        from src.trading.risk_guard import RiskGuard
        from src.trading.mt5_client import MT5Client
        from src.trading.trading_memory import TradingMemory

        client = MT5Client()
        risk_guard = RiskGuard()
        trading_memory = TradingMemory()

        # Restore risk state from DB (survives restarts)
        try:
            from src.trading.persistence import TradingPersistence
            persistence = TradingPersistence()
            risk_guard.load_state(persistence)
            log.info("risk_state_restored")
        except Exception as exc:
            log.warning("risk_state_restore_failed", error=str(exc))

        self.trading_brain = TradingBrain(
            client, risk_guard, trading_memory=trading_memory,
        )

        # Bridge TradingMemory into MemoryManager for LLM context injection
        self.memory.trading_memory = trading_memory

        # Wire the brain instance into tool handlers
        from src.tools.trading_advanced import set_trading_brain
        set_trading_brain(self.trading_brain)

    async def connect_mcp(self) -> int:
        """Connect MCP servers and register their tools. Returns tool count."""
        try:
            from src.skills.mcp_bridge import MCPBridge

            self._mcp_bridge = MCPBridge(tool_registry=self.tool_registry)
            count = await self._mcp_bridge.connect_all()
            log.info("mcp_connected", tool_count=count)
            return count
        except Exception as e:
            log.warning("mcp_connect_failed", error=str(e))
            return 0

    async def shutdown(self) -> None:
        """Graceful shutdown of all subsystems."""
        log.info("jarvis_app_shutdown")
        if self.trading_brain:
            await self.trading_brain.stop()
        if self.bounty_pipeline and self.bounty_pipeline.is_running:
            await self.bounty_pipeline.stop()
        if self.dreamtime:
            await self.dreamtime.stop()
        if self._mcp_bridge:
            try:
                await self._mcp_bridge.disconnect_all()
            except Exception as e:
                log.warning("mcp_disconnect_failed", error=str(e))
