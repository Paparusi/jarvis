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
        if self.dreamtime:
            await self.dreamtime.stop()
        if self._mcp_bridge:
            try:
                await self._mcp_bridge.disconnect_all()
            except Exception as e:
                log.warning("mcp_disconnect_failed", error=str(e))
