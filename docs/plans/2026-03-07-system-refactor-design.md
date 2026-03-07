# JARVIS System Refactor — DI Container + Clean Architecture

**Date:** 2026-03-07
**Status:** Approved
**Approach:** Incremental refactor (Approach A — DI Container)

## Problem Statement

JARVIS has grown to 97 files / 24K lines organically. The top architectural debts:

1. **Init duplication** — 3 adapters (Telegram/CLI/Web) each instantiate 25+ identical components
2. **Tool registration explosion** — 40+ imports, manual for-loop registration per adapter
3. **Router god class** — 903 LOC, 10 responsibilities in one class
4. **Weak type safety** — EventType enum defined but raw strings used everywhere
5. **MCP incomplete** — bridge exists but never actually connected in production

## Design: 5-Phase Incremental Refactor

### Phase 1: JarvisApp Container + @register_tool

**Goal:** Single source of truth for all components. Tools auto-discover.

#### 1a. `@register_tool` decorator + auto-discovery

```python
# src/tools/base.py — NEW

# Global registry for auto-discovery
_TOOL_REGISTRY: dict[str, type] = {}

def register_tool(name: str, group: str = "core"):
    """Decorator to auto-register tool classes."""
    def decorator(cls):
        cls._tool_name = name
        cls._tool_group = group
        _TOOL_REGISTRY[name] = cls
        return cls
    return decorator

def discover_tools() -> dict[str, ToolDefinition]:
    """Import all tool modules and return discovered tools."""
    import importlib
    tool_modules = [
        "src.tools.web_search", "src.tools.shell", "src.tools.file_ops",
        "src.tools.git_ops", "src.tools.docker_ops", "src.tools.http_client",
        "src.tools.network", "src.tools.code_exec", "src.tools.browser",
        "src.tools.crypto_utils", "src.tools.recon", "src.tools.code_analysis",
        "src.tools.data_tools", "src.tools.vision", "src.tools.document",
        "src.tools.tts",
    ]
    for mod in tool_modules:
        importlib.import_module(mod)
    return dict(_TOOL_REGISTRY)
```

**Migration:** Each tool file adds `@register_tool` to existing ToolDefinition. No behavior change.

#### 1b. JarvisApp container

```python
# src/app.py — NEW

class JarvisApp:
    """Central application container — single source of truth.

    All components are created here once. Adapters receive this container
    instead of creating their own component graphs.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()

        # Core infrastructure
        self.event_bus = get_event_bus()
        self.db = get_database()

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

        # Tool layer (auto-discovery)
        self.tool_registry = ToolRegistry()
        for tool in discover_all_tools():
            self.tool_registry.register(tool)

        # Intelligence layer
        skill_summary = self.skill_loader.get_metadata_summary()
        self.router = LLMRouter(
            skill_summary=skill_summary,
            tool_registry=self.tool_registry,
        )

        # Brain Independence
        self.collector = DataCollector()
        self.processor = DataProcessor()

        # Swarm
        self.swarm = SwarmCoordinator(
            decomposer=TaskDecomposer(),
            factory=AgentFactory(tool_registry=self.tool_registry),
        )

        # Dreamtime
        self.dreamtime = DreamtimeScheduler(idle_minutes=30, cron_hour=2)
        self.memory_consolidator = MemoryConsolidator(self.memory.semantic)
        self.dreamer = Dreamer(collector=self.collector, skill_registry=self.skill_registry)
        self.evolver = SkillEvolver(self.skill_registry, self.skill_loader)

        # Health & Safety
        self.health_monitor = HealthMonitor()
        self.safety = SafetyGuard()

        # MCP (connect after init)
        self.mcp_bridge = None  # Connected via connect_mcp()

    async def connect_mcp(self):
        """Connect MCP servers and register their tools."""
        from src.skills.mcp_bridge import MCPBridge
        self.mcp_bridge = MCPBridge(tool_registry=self.tool_registry)
        count = await self.mcp_bridge.connect_all()
        log.info("mcp_connected", tool_count=count)

    async def shutdown(self):
        """Graceful shutdown."""
        if self.mcp_bridge:
            await self.mcp_bridge.disconnect_all()
        self.dreamtime.stop()
```

#### 1c. Adapter simplification

```python
# BEFORE (telegram.py): 87 imports, 80 lines of __init__
class TelegramAdapter:
    def __init__(self, token):
        self._sessions = SessionManager()
        self._collector = DataCollector()
        self._processor = DataProcessor()
        # ... 22 more components ...

# AFTER: 3 imports, 5 lines of __init__
class TelegramAdapter:
    def __init__(self, app: JarvisApp, token: str):
        self.app = app
        self._token = token
        self._sessions = SessionManager()  # Channel-specific state
        self._response_cache: dict[int, dict] = {}
        self._retry_cache: dict[str, dict] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
```

**Test impact:** Fixtures create `JarvisApp(test_config)` instead of mocking 25 components.

---

### Phase 2: Router Decomposition

**Goal:** Split 903-LOC router into focused components.

```
LLMRouter (903 LOC) →
  ├── ResponseCache (cache.py — already exists, 189 LOC)
  ├── QueryClassifier (NEW — ~100 LOC)
  │   ├── classify_complexity()
  │   ├── needs_tool_access()
  │   └── check_response_quality()
  ├── LocalExecutor (NEW — ~150 LOC)
  │   └── run_local_model()
  ├── CloudExecutor (agent_loop.py — already exists, 714 LOC)
  │   └── run() with tool calling
  └── LLMRouter (SIMPLIFIED — ~200 LOC)
      └── route() orchestrates: cache → classify → local/cloud
```

**Key change:** Router becomes orchestrator only. Each sub-component independently testable.

---

### Phase 3: Typed EventBus + Message System

**Goal:** Type safety for events and multimodal messages.

#### 3a. Typed events

```python
# Use EventType enum everywhere (replace raw strings)
# EventBus.publish() accepts EventType instead of str

async def publish(self, event_type: EventType, data: dict, source: str = ""):
    # EventType enum enforced at type level
```

#### 3b. Content types for multimodal

```python
class ContentType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"

@dataclass
class ContentItem:
    type: ContentType
    value: str  # text content or file path/URL
    metadata: dict = field(default_factory=dict)

# MessageEnvelope.content becomes:
class MessageEnvelope(BaseModel):
    content: str  # Backward compat: plain text
    content_items: list[ContentItem] = []  # NEW: multimodal
```

---

### Phase 4: MCPManager Rewrite

**Goal:** Production-grade MCP (inspired by Qwen-Agent pattern).

```python
class MCPManager:
    """Singleton MCP manager with background event loop.

    Runs MCP async operations in a dedicated daemon thread,
    safely callable from sync or async contexts.
    """
    _instance = None

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = Thread(target=self._run_loop, daemon=True)
        self._servers: dict[str, MCPConnection] = {}
        self._tools: dict[str, MCPTool] = {}
        self._thread.start()

    @classmethod
    def get_instance(cls) -> MCPManager:
        if cls._instance is None:
            cls._instance = MCPManager()
        return cls._instance

    async def connect_server(self, config: MCPServerConfig) -> int:
        """Connect to MCP server, discover tools. Returns tool count."""

    async def call_tool(self, name: str, args: dict) -> ToolResult:
        """Execute MCP tool. Transparent to agents."""

    async def reconnect(self, server_name: str) -> bool:
        """Reconnect crashed server."""

    def shutdown(self):
        """Graceful shutdown all servers."""
```

**Key improvements over current:**
- Thread isolation (no event loop conflicts)
- Auto-reconnect on crash
- Tool name conflict resolution (prefix with server name)
- Integrated into JarvisApp container

---

### Phase 5: Adapter Protocol

**Goal:** Thin adapters that only handle channel protocol.

```python
class BaseAdapter(Protocol):
    """Contract for channel adapters."""

    app: JarvisApp

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def handle_message(self, envelope: MessageEnvelope) -> AgentResponse: ...
```

**Common logic moves to shared middleware:**

```python
# src/gateway/middleware.py — NEW
class MessagePipeline:
    """Shared message processing pipeline for all adapters."""

    def __init__(self, app: JarvisApp):
        self.app = app

    async def process(self, envelope: MessageEnvelope) -> AgentResponse:
        """Full pipeline: safety → memory → skill match → route → collect data."""
        # 1. Safety check
        if not self.app.safety.check(envelope.content):
            return self._blocked_response(envelope)

        # 2. Get/create session
        session = self.app.sessions.get_or_create(
            envelope.channel, envelope.user_id, envelope.username
        )
        session.add_user_message(envelope.content)

        # 3. Memory recall
        memory_ctx = await self.app.memory.recall(envelope.content)

        # 4. Skill matching
        skill_ctx = self.app.skill_router.match(envelope.content)

        # 5. Route to LLM
        response = await self.app.router.route(
            session=session,
            text=envelope.content,
            memory_context=memory_ctx,
            skill_context=skill_ctx,
        )

        # 6. Post-processing (memory store, data collection, user model update)
        await self._post_process(envelope, response)

        return response
```

**Result:** Each adapter becomes ~200 LOC (protocol handling only).

---

## Phase Order & Dependencies

```
Phase 1 (JarvisApp + @register_tool)
    │
    ├── Phase 2 (Router decomposition) — depends on Phase 1
    │
    ├── Phase 3 (Typed events) — independent, can parallel with Phase 2
    │
    └── Phase 4 (MCP rewrite) — depends on Phase 1
         │
         └── Phase 5 (Adapter protocol) — depends on Phase 1 + 2

Timeline estimate:
  Phase 1: Foundation (this session)
  Phase 2: Router split (next session)
  Phase 3: Type safety (can interleave)
  Phase 4: MCP (after Phase 1 stable)
  Phase 5: Adapters (after Phase 2 stable)
```

## Success Criteria

- [ ] All 935 tests pass after each phase
- [ ] Telegram bot runs without regression
- [ ] No adapter imports >20 modules (currently 87 in Telegram)
- [ ] Router.py < 250 LOC (currently 903)
- [ ] Single `JarvisApp` instance shared across all adapters
- [ ] MCP tools actually work in production
- [ ] Tool registration: 0 manual imports needed (auto-discover)

## Non-Goals

- Not changing Memory system (already solid)
- Not changing Skill format (SKILL.md works well)
- Not changing training pipeline
- Not changing LLM providers
- Not adding new features (pure refactor)
