# Phase 1: JarvisApp Container + Tool Auto-Discovery — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate 25-component init duplication across 3 adapters by creating a single `JarvisApp` container, and replace 56 manual tool imports with `@register_tool` auto-discovery.

**Architecture:** Add `@register_tool` decorator to `src/tools/base.py` that collects tool definitions at import time. Create `src/app.py` as a DI container that builds the full component graph once. Refactor all 3 adapters (Telegram, CLI, Web) + `main.py` to receive `JarvisApp` instead of creating their own components.

**Tech Stack:** Python 3.11+, dataclasses, existing ToolDefinition/ToolRegistry

---

### Task 1: Add `@register_tool` decorator and `discover_tools()` to base.py

**Files:**
- Modify: `src/tools/base.py`
- Test: `tests/unit/test_tool_system.py`

**Step 1: Write the failing test**

Add to `tests/unit/test_tool_system.py`:

```python
class TestToolAutoDiscovery:
    """Test @register_tool decorator and discover_tools()."""

    def test_register_tool_decorator_adds_to_registry(self):
        from src.tools.base import _TOOL_REGISTRY, register_tool, ToolDefinition, ToolParameter, ToolResult

        # Create a test tool with the decorator
        @register_tool("test_decorator_tool", group="test")
        def my_test_tool(x: str) -> ToolResult:
            return ToolResult(success=True, output=x)

        assert "test_decorator_tool" in _TOOL_REGISTRY
        entry = _TOOL_REGISTRY["test_decorator_tool"]
        assert entry["group"] == "test"
        assert entry["definition"].name == "test_decorator_tool"

        # Cleanup
        del _TOOL_REGISTRY["test_decorator_tool"]

    def test_register_tool_with_tool_definition(self):
        from src.tools.base import _TOOL_REGISTRY, register_tool, ToolDefinition, ToolParameter, ToolResult

        handler = AsyncMock(return_value=ToolResult(success=True, output="ok"))
        tool_def = ToolDefinition(
            name="test_reg_def",
            description="A test",
            parameters=[ToolParameter(name="q", type="string", description="query")],
            handler=handler,
        )

        register_tool("test_reg_def", group="test", definition=tool_def)
        assert "test_reg_def" in _TOOL_REGISTRY
        assert _TOOL_REGISTRY["test_reg_def"]["definition"] is tool_def

        # Cleanup
        del _TOOL_REGISTRY["test_reg_def"]

    def test_discover_tools_returns_all_registered(self):
        from src.tools.base import discover_tools
        tools = discover_tools()
        # Should find all 56 tools from src/tools/
        assert len(tools) >= 50, f"Expected >= 50 tools, got {len(tools)}"
        # Spot check some known tools
        tool_names = [t.name for t in tools]
        assert "web_search" in tool_names
        assert "run_command" in tool_names
        assert "read_file" in tool_names

    def test_discover_tools_returns_tool_definitions(self):
        from src.tools.base import discover_tools, ToolDefinition
        tools = discover_tools()
        for tool in tools:
            assert isinstance(tool, ToolDefinition)
            assert tool.name
            assert tool.description
            assert tool.handler is not None
```

**Step 2: Run test to verify it fails**

Run: `cd ~/projects/jarvis && python -m pytest tests/unit/test_tool_system.py::TestToolAutoDiscovery -v`
Expected: FAIL — `ImportError: cannot import name '_TOOL_REGISTRY' from 'src.tools.base'`

**Step 3: Add `_TOOL_REGISTRY`, `register_tool()`, and `discover_tools()` to base.py**

Add after the imports section (line 17) in `src/tools/base.py`:

```python
# --- Auto-discovery registry ---
# Tools register themselves here at import time via @register_tool or register_tool()
_TOOL_REGISTRY: dict[str, dict] = {}


def register_tool(
    name: str,
    group: str = "core",
    definition: ToolDefinition | None = None,
) -> callable:
    """Register a tool for auto-discovery.

    Can be used as a decorator on a handler function, or called directly
    with a ToolDefinition.

    Usage as decorator:
        @register_tool("my_tool", group="network")
        async def my_handler(query: str) -> ToolResult:
            ...

    Usage with definition:
        register_tool("my_tool", group="network", definition=my_tool_def)
    """
    if definition is not None:
        # Direct registration with a ToolDefinition
        _TOOL_REGISTRY[name] = {"definition": definition, "group": group}
        return definition

    # Decorator usage — returns the function unchanged, stores it for later
    def decorator(func):
        # Create a minimal placeholder — actual ToolDefinition must be
        # registered separately since we need parameters metadata
        _TOOL_REGISTRY[name] = {"handler": func, "group": group}
        return func
    return decorator


def discover_tools() -> list[ToolDefinition]:
    """Import all tool modules to trigger registration, return discovered tools.

    This is called once during JarvisApp initialization.
    """
    import importlib

    _tool_modules = [
        "src.tools.web_search",
        "src.tools.shell",
        "src.tools.file_ops",
        "src.tools.git_ops",
        "src.tools.docker_ops",
        "src.tools.http_client",
        "src.tools.network",
        "src.tools.code_exec",
        "src.tools.browser",
        "src.tools.crypto_utils",
        "src.tools.recon",
        "src.tools.code_analysis",
        "src.tools.data_tools",
        "src.tools.vision",
        "src.tools.document",
        "src.tools.tts",
    ]
    for mod_name in _tool_modules:
        try:
            importlib.import_module(mod_name)
        except ImportError as e:
            log.warning("tool_module_import_failed", module=mod_name, error=str(e))

    return [
        entry["definition"]
        for entry in _TOOL_REGISTRY.values()
        if "definition" in entry
    ]
```

**Step 4: Run test to verify it passes**

Run: `cd ~/projects/jarvis && python -m pytest tests/unit/test_tool_system.py::TestToolAutoDiscovery -v`
Expected: `test_register_tool_decorator_adds_to_registry` and `test_register_tool_with_tool_definition` PASS. The `test_discover_tools_*` tests still FAIL because no tool files call `register_tool()` yet.

**Step 5: Commit**

```bash
cd ~/projects/jarvis
git add src/tools/base.py tests/unit/test_tool_system.py
git commit -m "feat: add @register_tool decorator and discover_tools() to base.py"
```

---

### Task 2: Add `register_tool()` calls to all 16 tool modules

**Files:**
- Modify: all 16 files in `src/tools/` (except `base.py` and `__init__.py`)

**Step 1: For each tool module, add `register_tool()` call after each ToolDefinition**

Pattern — at the bottom of each file, after each `xxx_tool = ToolDefinition(...)`, add:

```python
register_tool("xxx_tool_name", group="group_name", definition=xxx_tool)
```

Add the import at the top of each tool file (right after `from src.tools.base import ToolDefinition, ToolParameter, ToolResult`):

```python
from src.tools.base import ToolDefinition, ToolParameter, ToolResult, register_tool
```

**Group assignments:**

| Module | Tools | Group |
|--------|-------|-------|
| `web_search.py` | web_search, fetch_url | `core` |
| `shell.py` | run_command | `core` |
| `file_ops.py` | read_file, write_file, list_dir | `core` |
| `code_exec.py` | run_python | `core` |
| `http_client.py` | http_request | `core` |
| `browser.py` | browse_web, deep_search, screenshot | `core` |
| `git_ops.py` | git_status, git_diff, git_log, git_commit, git_branch | `devops` |
| `docker_ops.py` | docker_ps, docker_logs, docker_exec, docker_images, docker_compose | `devops` |
| `network.py` | port_scan, dns_lookup, ping, traceroute | `network` |
| `crypto_utils.py` | hash, url_encode, jwt_decode, hex_convert, regex_test, timestamp, ip_info, whois, ssl_check, generate_password, cidr_calc, base64_encode | `crypto` |
| `recon.py` | subdomain_enum, http_headers, cve_lookup, reverse_dns, tech_detect | `security` |
| `code_analysis.py` | ast_analyze, complexity_check, dependency_graph, code_search, diff_summary | `analysis` |
| `data_tools.py` | csv_analyze, json_query, sqlite_query, text_stats, json_transform | `data` |
| `vision.py` | analyze_image, ocr | `media` |
| `document.py` | ingest_document, query_documents | `media` |
| `tts.py` | text_to_speech | `media` |

**Example for `web_search.py` (line ~196):**

```python
from src.tools.base import ToolDefinition, ToolParameter, ToolResult, register_tool

# ... existing code ...

web_search_tool = ToolDefinition(
    name="web_search",
    # ... existing definition unchanged ...
)
register_tool("web_search", group="core", definition=web_search_tool)

fetch_url_tool = ToolDefinition(
    name="fetch_url",
    # ... existing definition unchanged ...
)
register_tool("fetch_url", group="core", definition=fetch_url_tool)
```

**Step 2: Run discover_tools test**

Run: `cd ~/projects/jarvis && python -m pytest tests/unit/test_tool_system.py::TestToolAutoDiscovery -v`
Expected: ALL PASS (including `test_discover_tools_returns_all_registered`)

**Step 3: Run full test suite to verify no regressions**

Run: `cd ~/projects/jarvis && python -m pytest tests/ --tb=short -q`
Expected: 935 passed (or 934+1 pre-existing failure)

**Step 4: Commit**

```bash
cd ~/projects/jarvis
git add src/tools/
git commit -m "feat: register all 56 tools via register_tool() for auto-discovery"
```

---

### Task 3: Create `src/app.py` — JarvisApp container

**Files:**
- Create: `src/app.py`
- Test: `tests/unit/test_app.py`

**Step 1: Write the failing test**

Create `tests/unit/test_app.py`:

```python
"""Tests for JarvisApp — central DI container."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestJarvisApp:
    """Test JarvisApp container initialization."""

    def test_app_creates_all_core_components(self):
        from src.app import JarvisApp
        app = JarvisApp()

        # Core infrastructure
        assert app.event_bus is not None
        assert app.sessions is not None

        # Memory
        assert app.memory is not None
        assert app.user_model is not None

        # Skills
        assert app.skill_loader is not None
        assert app.skill_registry is not None

        # Tools
        assert app.tool_registry is not None
        assert app.tool_registry.get_all()  # Has tools registered

        # Intelligence
        assert app.router is not None

        # Brain Independence
        assert app.collector is not None

    def test_app_tool_registry_has_all_tools(self):
        from src.app import JarvisApp
        app = JarvisApp()
        tools = app.tool_registry.get_all()
        assert len(tools) >= 50, f"Expected >= 50 tools, got {len(tools)}"

        # Spot check known tools
        names = [t.name for t in tools]
        assert "web_search" in names
        assert "run_command" in names
        assert "read_file" in names

    def test_app_skill_registry_has_skills(self):
        from src.app import JarvisApp
        app = JarvisApp()
        # Should have loaded skills from workspace/skills/
        assert app.skill_loader is not None

    def test_app_is_reusable_across_adapters(self):
        """Two adapters using the same app share the same components."""
        from src.app import JarvisApp
        app = JarvisApp()

        # Both references point to same objects
        router1 = app.router
        router2 = app.router
        assert router1 is router2

        registry1 = app.tool_registry
        registry2 = app.tool_registry
        assert registry1 is registry2

    @pytest.mark.asyncio
    async def test_app_connect_mcp(self):
        """MCP connect should not crash (may fail if no servers configured)."""
        from src.app import JarvisApp
        app = JarvisApp()
        # Should not raise even if MCP servers aren't available
        count = await app.connect_mcp()
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_app_shutdown(self):
        """Shutdown should not crash."""
        from src.app import JarvisApp
        app = JarvisApp()
        await app.shutdown()  # Should not raise
```

**Step 2: Run test to verify it fails**

Run: `cd ~/projects/jarvis && python -m pytest tests/unit/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.app'`

**Step 3: Implement `src/app.py`**

```python
"""JarvisApp — Central application container.

Single source of truth for all JARVIS components. Adapters receive this
container instead of creating their own component graphs.

Usage:
    app = JarvisApp()
    adapter = TelegramAdapter(app, token="...")
"""

from __future__ import annotations

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
from src.tools.base import ToolRegistry, discover_tools
from src.utils.logging import get_logger

log = get_logger("app")


class JarvisApp:
    """Central DI container — builds the full component graph once."""

    def __init__(self) -> None:
        log.info("jarvis_app_init_start")

        # Core infrastructure
        self.event_bus = get_event_bus()
        self.sessions = SessionManager()

        # Memory layer
        self.memory = MemoryManager()
        self.user_model = UserModel()

        # Skills layer
        self.skill_loader = SkillLoader()
        self.skill_loader.load_all()
        self.skill_registry = SkillRegistry(self.skill_loader)
        self.skill_registry._apply_metrics()
        self.skill_router = SkillRouter(self.skill_loader)
        self.skill_executor = SkillExecutor()

        # Tool layer — auto-discover all registered tools
        self.tool_registry = ToolRegistry()
        for tool in discover_tools():
            self.tool_registry.register(tool)
        log.info("tools_discovered", count=len(self.tool_registry.get_all()))

        # Intelligence layer
        skill_summary = self.skill_loader.get_metadata_summary()
        self.router = LLMRouter(
            skill_summary=skill_summary,
            tool_registry=self.tool_registry,
        )

        # Brain Independence
        self.collector = DataCollector()
        self.processor = DataProcessor()

        # Dreamtime (lazy — set callbacks from adapter)
        self.memory_consolidator = None
        self.dreamer = None
        self.evolver = None
        self.dreamtime = None

        # Health (lazy — only Telegram needs it)
        self.health_monitor = None

        # MCP
        self._mcp_bridge = None

        log.info("jarvis_app_init_done")

    def init_dreamtime(self) -> None:
        """Initialize Dreamtime components (call after core init)."""
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
        """Initialize Health Monitor."""
        from src.metacognition.health_monitor import HealthMonitor
        self.health_monitor = HealthMonitor()
        self.router._health_monitor = self.health_monitor

    def init_swarm(self) -> None:
        """Initialize Swarm Coordinator."""
        from src.swarm.coordinator import SwarmCoordinator
        from src.swarm.decomposer import TaskDecomposer
        from src.swarm.factory import AgentFactory

        self.decomposer = TaskDecomposer(complexity_threshold=40)
        self.swarm = SwarmCoordinator(
            decomposer=self.decomposer,
            factory=AgentFactory(tool_registry=self.tool_registry),
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
        """Graceful shutdown."""
        log.info("jarvis_app_shutdown")
        if self.dreamtime:
            self.dreamtime.stop()
        if self._mcp_bridge:
            try:
                await self._mcp_bridge.disconnect_all()
            except Exception as e:
                log.warning("mcp_disconnect_failed", error=str(e))
```

**Step 4: Run test to verify it passes**

Run: `cd ~/projects/jarvis && python -m pytest tests/unit/test_app.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `cd ~/projects/jarvis && python -m pytest tests/ --tb=short -q`
Expected: No new failures

**Step 6: Commit**

```bash
cd ~/projects/jarvis
git add src/app.py tests/unit/test_app.py
git commit -m "feat: add JarvisApp DI container with auto tool discovery"
```

---

### Task 4: Refactor CLIAdapter to use JarvisApp

**Files:**
- Modify: `src/gateway/channels/cli.py`
- Test: `tests/unit/test_app.py` (add CLI integration test)

**Step 1: Write the failing test**

Add to `tests/unit/test_app.py`:

```python
class TestCLIAdapterWithApp:
    """Test CLIAdapter receives JarvisApp instead of building its own components."""

    def test_cli_adapter_uses_app_router(self):
        from src.app import JarvisApp
        from src.gateway.channels.cli import CLIAdapter

        app = JarvisApp()
        adapter = CLIAdapter(app)

        # Adapter uses the app's router, not its own
        assert adapter._router is app.router

    def test_cli_adapter_uses_app_tool_registry(self):
        from src.app import JarvisApp
        from src.gateway.channels.cli import CLIAdapter

        app = JarvisApp()
        adapter = CLIAdapter(app)

        # Tools come from app, not locally registered
        assert adapter._tool_registry is app.tool_registry

    def test_cli_adapter_uses_app_memory(self):
        from src.app import JarvisApp
        from src.gateway.channels.cli import CLIAdapter

        app = JarvisApp()
        adapter = CLIAdapter(app)

        assert adapter._memory is app.memory
```

**Step 2: Run test to verify it fails**

Run: `cd ~/projects/jarvis && python -m pytest tests/unit/test_app.py::TestCLIAdapterWithApp -v`
Expected: FAIL — `TypeError: CLIAdapter.__init__() takes 1 positional argument but 2 were given`

**Step 3: Refactor CLIAdapter**

Rewrite `src/gateway/channels/cli.py` `__init__` to accept `JarvisApp`:

```python
class CLIAdapter:
    """Interactive CLI adapter for JARVIS."""

    def __init__(self, app: JarvisApp | None = None) -> None:
        if app is not None:
            # New path: use shared JarvisApp
            self._app = app
            self._sessions = app.sessions
            self._collector = app.collector
            self._processor = app.processor
            self._memory = app.memory
            self._user_model = app.user_model
            self._skill_loader = app.skill_loader
            self._skill_router = app.skill_router
            self._skill_registry = app.skill_registry
            self._tool_registry = app.tool_registry
            self._router = app.router
            self._bus = app.event_bus
            self._memory_consolidator = app.memory_consolidator
            self._dreamer = app.dreamer
            self._evolver = app.evolver
        else:
            # Legacy path: backward compatible (will be removed in Phase 5)
            self._app = None
            self._sessions = SessionManager()
            self._collector = DataCollector()
            self._processor = DataProcessor()
            self._memory = MemoryManager()
            self._user_model = UserModel()
            self._skill_loader = SkillLoader()
            self._skill_router = SkillRouter(self._skill_loader)
            self._bus = get_event_bus()
            self._skill_loader.load_all()
            self._skill_registry = SkillRegistry(self._skill_loader)
            self._skill_registry._apply_metrics()
            self._tool_registry = ToolRegistry()
            for tool in [web_search_tool, fetch_url_tool, shell_tool,
                          read_file_tool, write_file_tool, list_dir_tool,
                          code_exec_tool,
                          browse_web_tool, deep_search_tool, screenshot_tool,
                          analyze_image_tool, ocr_tool,
                          ingest_document_tool, query_documents_tool,
                          tts_tool]:
                self._tool_registry.register(tool)
            skill_summary = self._skill_loader.get_metadata_summary()
            self._router = LLMRouter(
                skill_summary=skill_summary,
                tool_registry=self._tool_registry,
            )
            self._memory_consolidator = MemoryConsolidator(self._memory.semantic)
            self._dreamer = Dreamer(collector=self._collector, skill_registry=self._skill_registry)
            self._evolver = SkillEvolver(self._skill_registry, self._skill_loader)

        # CLI-specific state (always local)
        self._user_id = "cli_user"
        self._session = self._sessions.get_or_create(
            Channel.CLI, self._user_id, "CLI User"
        )
```

Also add the import at the top:

```python
from src.app import JarvisApp
```

**Step 4: Run tests**

Run: `cd ~/projects/jarvis && python -m pytest tests/unit/test_app.py -v`
Expected: ALL PASS

Run: `cd ~/projects/jarvis && python -m pytest tests/ --tb=short -q`
Expected: No new failures

**Step 5: Commit**

```bash
cd ~/projects/jarvis
git add src/gateway/channels/cli.py tests/unit/test_app.py
git commit -m "refactor: CLIAdapter accepts JarvisApp container (backward-compat)"
```

---

### Task 5: Refactor WebAdapter to use JarvisApp

**Files:**
- Modify: `src/gateway/channels/web.py`
- Test: `tests/unit/test_app.py` (add Web integration test)

**Step 1: Write the failing test**

Add to `tests/unit/test_app.py`:

```python
class TestWebAdapterWithApp:
    """Test WebAdapter receives JarvisApp."""

    def test_web_adapter_uses_app_router(self):
        from src.app import JarvisApp
        from src.gateway.channels.web import WebAdapter

        app = JarvisApp()
        adapter = WebAdapter(app)
        assert adapter._router is app.router

    def test_web_adapter_uses_app_tools(self):
        from src.app import JarvisApp
        from src.gateway.channels.web import WebAdapter

        app = JarvisApp()
        adapter = WebAdapter(app)
        assert adapter._tool_registry is app.tool_registry
```

**Step 2: Refactor WebAdapter.__init__ (same pattern as CLI)**

Accept `app: JarvisApp | None = None`. New path uses `app.*`, legacy path keeps existing code.

Also update `create_app()` factory:

```python
def create_app(app: JarvisApp | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    fastapi_app = FastAPI(title="JARVIS", version="2.0")
    adapter = WebAdapter(app)
    # ... rest unchanged ...
```

**Step 3: Run tests**

Run: `cd ~/projects/jarvis && python -m pytest tests/unit/test_app.py tests/unit/test_web_channel.py -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
cd ~/projects/jarvis
git add src/gateway/channels/web.py tests/unit/test_app.py
git commit -m "refactor: WebAdapter accepts JarvisApp container (backward-compat)"
```

---

### Task 6: Refactor TelegramAdapter to use JarvisApp

**Files:**
- Modify: `src/gateway/channels/telegram.py`
- Test: `tests/unit/test_app.py` (add Telegram integration test)

**Step 1: Write the failing test**

Add to `tests/unit/test_app.py`:

```python
class TestTelegramAdapterWithApp:
    """Test TelegramAdapter receives JarvisApp."""

    def test_telegram_adapter_uses_app_router(self):
        from src.app import JarvisApp
        from src.gateway.channels.telegram import TelegramAdapter

        app = JarvisApp()
        app.init_dreamtime()
        app.init_health()
        app.init_swarm()
        adapter = TelegramAdapter(app, token="test_token")
        assert adapter._router is app.router

    def test_telegram_adapter_uses_app_tools(self):
        from src.app import JarvisApp
        from src.gateway.channels.telegram import TelegramAdapter

        app = JarvisApp()
        app.init_dreamtime()
        app.init_health()
        app.init_swarm()
        adapter = TelegramAdapter(app, token="test_token")
        assert adapter._tool_registry is app.tool_registry
        # Telegram should have ALL tools (not just CLI subset)
        assert len(adapter._tool_registry.get_all()) >= 50
```

**Step 2: Refactor TelegramAdapter.__init__**

Same dual-path pattern. The `app` path eliminates ~80 lines of imports and init. Add `from src.app import JarvisApp` import. Signature becomes:

```python
def __init__(self, app_or_token, token: str | None = None) -> None:
    # Support both: TelegramAdapter(app, token="xxx") and TelegramAdapter("xxx")
    if isinstance(app_or_token, JarvisApp):
        app = app_or_token
        self._token = token
        # ... use app.* for everything
    else:
        # Legacy: app_or_token is the token string
        self._token = app_or_token
        app = None
        # ... existing init code
```

**Step 3: Run tests**

Run: `cd ~/projects/jarvis && python -m pytest tests/unit/test_app.py -v`
Expected: ALL PASS

Run: `cd ~/projects/jarvis && python -m pytest tests/ --tb=short -q`
Expected: No new failures

**Step 4: Commit**

```bash
cd ~/projects/jarvis
git add src/gateway/channels/telegram.py tests/unit/test_app.py
git commit -m "refactor: TelegramAdapter accepts JarvisApp container (backward-compat)"
```

---

### Task 7: Update main.py to use JarvisApp

**Files:**
- Modify: `src/main.py`

**Step 1: Refactor run_telegram() and run_cli()**

```python
async def run_telegram() -> None:
    log = get_logger("main")
    token = get_env("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("missing_telegram_token")
        sys.exit(1)

    from src.app import JarvisApp
    app = JarvisApp()
    app.init_dreamtime()
    app.init_health()
    app.init_swarm()

    # Connect MCP servers
    await app.connect_mcp()

    # Start Prometheus metrics
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

    from src.gateway.channels.telegram import TelegramAdapter
    adapter = TelegramAdapter(app, token=token)
    await adapter.start()

    # ... rest unchanged (signal handling, shutdown) ...
    # Add: await app.shutdown() before adapter.stop()


async def run_cli() -> None:
    log = get_logger("main")
    from src.app import JarvisApp
    app = JarvisApp()
    app.init_dreamtime()

    from src.gateway.channels.cli import CLIAdapter
    adapter = CLIAdapter(app)
    await adapter.start()
    await app.shutdown()
```

**Step 2: Test manually**

Run: `cd ~/projects/jarvis && python -m src.main --cli` (type "hello", verify response, Ctrl+C)

**Step 3: Commit**

```bash
cd ~/projects/jarvis
git add src/main.py
git commit -m "refactor: main.py uses JarvisApp to create shared component graph"
```

---

### Task 8: Fix the pre-existing test failure + run final validation

**Files:**
- Modify: the skill with Vietnamese name `tìm-kiếm-tin`

**Step 1: Find and fix the non-compliant skill name**

```bash
cd ~/projects/jarvis && grep -r "tìm-kiếm-tin" workspace/skills/
```

Rename the skill file and update its `name:` field to `tim-kiem-tin` (ASCII).

**Step 2: Run full test suite**

Run: `cd ~/projects/jarvis && python -m pytest tests/ --tb=short -q`
Expected: ALL 935+ tests pass, 0 failures

**Step 3: Commit**

```bash
cd ~/projects/jarvis
git add workspace/skills/
git commit -m "fix: normalize Vietnamese skill name tìm-kiếm-tin → tim-kiem-tin"
```

---

## Summary

| Task | What | Files Changed | Tests Added |
|------|------|---------------|-------------|
| 1 | `@register_tool` + `discover_tools()` | base.py | 4 |
| 2 | Add `register_tool()` to 16 tool modules | 16 tool files | 0 (existing tests cover) |
| 3 | `JarvisApp` container | app.py | 6 |
| 4 | CLIAdapter refactor | cli.py | 3 |
| 5 | WebAdapter refactor | web.py | 2 |
| 6 | TelegramAdapter refactor | telegram.py | 2 |
| 7 | main.py update | main.py | 0 (manual test) |
| 8 | Fix skill name + final validation | skill file | 0 (existing test covers) |

**Total: 8 tasks, ~22 files changed, ~17 new tests, 8 commits**

After Phase 1:
- All adapters share one JarvisApp instance
- 56 tools auto-discovered (0 manual imports in adapters)
- Legacy backward-compat preserved (removed in Phase 5)
- All tests pass
