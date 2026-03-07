"""Tests for JarvisApp — central DI container."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestJarvisApp:
    """Test JarvisApp container initialization."""

    def test_app_creates_core_components(self):
        from src.app import JarvisApp

        app = JarvisApp()

        assert app.event_bus is not None
        assert app.sessions is not None
        assert app.memory is not None
        assert app.user_model is not None
        assert app.skill_loader is not None
        assert app.skill_registry is not None
        assert app.tool_registry is not None
        assert app.router is not None
        assert app.collector is not None
        assert app.processor is not None

    def test_app_has_all_tools(self):
        from src.app import JarvisApp

        app = JarvisApp()
        tools = app.tool_registry.get_all()
        assert len(tools) >= 60, f"Expected >= 60 tools, got {len(tools)}"

        names = [t.name for t in tools]
        assert "web_search" in names
        assert "run_command" in names
        assert "read_file" in names

    def test_app_has_skills(self):
        from src.app import JarvisApp

        app = JarvisApp()
        metadata = app.skill_loader.get_all_metadata()
        assert len(metadata) >= 20, f"Expected >= 20 skills, got {len(metadata)}"

    def test_app_components_are_singletons(self):
        """Same app instance returns same component references."""
        from src.app import JarvisApp

        app = JarvisApp()
        assert app.router is app.router
        assert app.tool_registry is app.tool_registry
        assert app.memory is app.memory
        assert app.sessions is app.sessions

    def test_lazy_subsystems_are_none_initially(self):
        from src.app import JarvisApp

        app = JarvisApp()
        assert app.health_monitor is None
        assert app.dreamtime is None
        assert app.swarm is None
        assert app.proactive is None

    def test_init_dreamtime(self):
        from src.app import JarvisApp

        app = JarvisApp()
        app.init_dreamtime()

        assert app.memory_consolidator is not None
        assert app.dreamer is not None
        assert app.evolver is not None
        assert app.dreamtime is not None

    def test_init_health(self):
        from src.app import JarvisApp

        app = JarvisApp()
        app.init_health()

        assert app.health_monitor is not None

    def test_init_swarm(self):
        from src.app import JarvisApp

        app = JarvisApp()
        app.init_swarm()

        assert app.decomposer is not None
        assert app.swarm is not None

    def test_init_proactive(self):
        from src.app import JarvisApp

        app = JarvisApp()
        app.init_proactive(user_id="test_user")

        assert app.proactive is not None

    @pytest.mark.asyncio
    async def test_connect_mcp_returns_int(self):
        from src.app import JarvisApp

        app = JarvisApp()
        count = await app.connect_mcp()
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_shutdown_no_crash(self):
        from src.app import JarvisApp

        app = JarvisApp()
        await app.shutdown()  # Should not raise

    @pytest.mark.asyncio
    async def test_shutdown_with_dreamtime(self):
        from src.app import JarvisApp

        app = JarvisApp()
        app.init_dreamtime()
        await app.shutdown()  # Should stop dreamtime cleanly
