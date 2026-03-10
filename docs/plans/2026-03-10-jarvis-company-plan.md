# JARVIS Company Structure — Implementation Plan

> **For Claude:** Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Transform JARVIS into an AI Company hierarchy with CEO orchestrator, department heads, and workers — improving tool focus, reducing context bloat, and enabling domain specialization.

**Architecture:** CEO classifies requests → handles simple ones directly → delegates domain tasks to Department Heads with filtered tool sets. Existing TradingBrain becomes Finance Department. Existing Swarm becomes Worker Pool.

**Tech Stack:** Python 3.11+, existing JARVIS infrastructure (SQLite, ToolRegistry, AgentLoop, Swarm)

---

## Task 1: Create Department definitions + tool allocation

**Files:**
- Create: `src/company/__init__.py`
- Create: `src/company/departments.py`
- Create: `tests/unit/test_departments.py`

### `src/company/__init__.py`
```python
"""JARVIS Company Structure — AI Company hierarchy."""
```

### `src/company/departments.py` (~120 lines)

```python
"""Department definitions and tool allocation for JARVIS Company."""

from __future__ import annotations

from enum import Enum

from src.utils.logging import get_logger

log = get_logger("company.departments")


class Department(str, Enum):
    """JARVIS company departments."""
    GENERAL = "general"        # CEO handles directly
    FINANCE = "finance"        # Trading, market analysis, MT5
    SECURITY = "security"      # Pentesting, OSINT, vulnerability scanning
    ENGINEERING = "engineering" # Code analysis, git, docker, development
    RESEARCH = "research"      # Web search, document analysis, deep research
    OPERATIONS = "operations"  # Scheduling, TTS, image analysis, system ops


# Tool name prefixes/patterns → Department mapping
# Tools not matching any pattern go to SHARED (available to all departments)
TOOL_ALLOCATION: dict[Department, set[str]] = {
    Department.FINANCE: {
        "mt5_price", "mt5_candles", "mt5_account", "mt5_positions",
        "mt5_order", "mt5_close", "mt5_history", "market_session",
        "technical_indicators", "trading_calendar",
        "mt5_analyze", "mt5_signal", "mt5_risk",
        "mt5_journal_log", "mt5_journal_stats", "mt5_journal_sync",
        "mt5_smc",
        "trade_plan", "trade_status", "trade_config",
        "trade_control", "trade_pending", "trade_history",
    },
    Department.SECURITY: {
        "subdomain_enum", "http_headers", "cve_lookup", "reverse_dns",
        "tech_detect", "port_scan", "dns_lookup",
        "google_dork", "username_search", "email_harvest",
        "wayback_lookup", "github_leaks",
        "dir_bruteforce", "sqli_test", "xss_scan", "cors_check",
        "waf_detect", "lfi_test", "header_audit",
        "hash_identify", "hash_crack", "cipher_decode", "encoding_chain",
        "exploit_search", "reverse_shell_gen", "payload_encode", "gtfobins_lookup",
        "file_metadata", "stego_detect", "ioc_extract", "log_analyze",
        "virustotal_lookup", "abuseipdb_check", "malware_hash_check", "shodan_search",
        "subdomain_takeover", "js_secrets_scan", "open_redirect", "nuclei_scan",
        "subfinder_enum", "httpx_probe", "katana_crawl", "gau_urls", "ffuf_fuzz",
    },
    Department.ENGINEERING: {
        "ast_analyze", "complexity_check", "dependency_graph",
        "code_search", "diff_summary",
        "git_status", "git_diff", "git_log", "git_commit", "git_branch",
        "docker_ps", "docker_logs", "docker_exec", "docker_images", "docker_compose",
        "run_python", "shell",
        "read_file", "write_file", "list_dir",
    },
    Department.RESEARCH: {
        "web_search", "fetch_url", "browse_web", "deep_search", "screenshot",
        "ingest_document", "query_documents",
        "http_request",
    },
    Department.OPERATIONS: {
        "text_to_speech", "analyze_image", "ocr_image",
        "base64_encode", "hash_generate", "url_encode",
        "jwt_decode", "hex_convert", "regex_test", "timestamp_convert",
        "ip_info", "whois_lookup", "ssl_check",
        "generate_password", "cidr_calc",
    },
}

# Tools available to ALL departments (shared utilities)
SHARED_TOOLS: set[str] = {
    "web_search", "fetch_url", "read_file", "write_file", "list_dir",
    "run_python", "code_exec",
}

# Keyword patterns for fast department classification (no LLM needed)
DEPARTMENT_KEYWORDS: dict[Department, list[str]] = {
    Department.FINANCE: [
        "xauusd", "gold", "vàng", "giá vàng", "mt5", "trading", "trade",
        "lệnh", "position", "pending", "buy", "sell", "sl", "tp", "lot",
        "pip", "spread", "bid", "ask", "order", "entry", "profit", "loss",
        "phân tích thị trường", "phân tích kỹ thuật", "setup", "zone",
        "confluence", "risk", "reward", "breakeven", "trailing",
        "fibonacci", "session", "london", "new york",
        "/mt5", "/trade", "/signal",
    ],
    Department.SECURITY: [
        "scan", "vuln", "vulnerability", "exploit", "pentest", "recon",
        "subdomain", "cve", "xss", "sqli", "sql injection", "lfi",
        "brute", "fuzz", "osint", "dork", "hack", "bounty", "hunt",
        "nuclei", "nmap", "port scan", "reverse shell", "payload",
        "forensic", "malware", "threat", "stego", "ioc",
        "/pentest", "/bounty", "/hunt",
    ],
    Department.ENGINEERING: [
        "code", "debug", "refactor", "git", "commit", "branch", "merge",
        "docker", "container", "deploy", "build", "test", "lint",
        "function", "class", "module", "import", "error", "bug", "fix",
        "python", "javascript", "typescript", "rust", "ast", "complexity",
        "dependency", "diff",
    ],
    Department.RESEARCH: [
        "tìm kiếm", "search", "tra cứu", "research", "tìm hiểu",
        "tin tức", "news", "article", "paper",
        "document", "pdf", "summarize", "tóm tắt",
        "deep search", "browse",
    ],
    Department.OPERATIONS: [
        "nhắc nhở", "remind", "lịch", "schedule", "hẹn",
        "đọc ảnh", "image", "ocr", "screenshot",
        "tts", "đọc", "voice", "speak",
        "convert", "encode", "decode", "hash",
        "/remind", "/digest",
    ],
}


def classify_department(message: str) -> Department:
    """Classify a message to a department using keyword matching.

    Returns Department.GENERAL if no strong match (CEO handles directly).
    """
    text_lower = message.lower()
    scores: dict[Department, int] = {}

    for dept, keywords in DEPARTMENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[dept] = score

    if not scores:
        return Department.GENERAL

    # Return highest scoring department (need at least 1 keyword match)
    best_dept = max(scores, key=scores.get)
    return best_dept


def get_department_tools(dept: Department) -> set[str]:
    """Get tool names allocated to a department + shared tools."""
    dept_tools = TOOL_ALLOCATION.get(dept, set())
    return dept_tools | SHARED_TOOLS


def get_department_display_name(dept: Department) -> str:
    """Vietnamese display names for departments."""
    names = {
        Department.GENERAL: "CEO (General)",
        Department.FINANCE: "Phòng Tài chính",
        Department.SECURITY: "Phòng An ninh",
        Department.ENGINEERING: "Phòng Kỹ thuật",
        Department.RESEARCH: "Phòng Nghiên cứu",
        Department.OPERATIONS: "Phòng Vận hành",
    }
    return names.get(dept, dept.value)
```

### Tests: `tests/unit/test_departments.py` (~15 tests)

```python
"""Tests for company department definitions and classification."""

import pytest

from src.company.departments import (
    Department,
    TOOL_ALLOCATION,
    SHARED_TOOLS,
    classify_department,
    get_department_tools,
    get_department_display_name,
)


class TestClassifyDepartment:
    def test_finance_xauusd(self):
        assert classify_department("giá XAUUSD hôm nay") == Department.FINANCE

    def test_finance_trading(self):
        assert classify_department("mở lệnh buy gold") == Department.FINANCE

    def test_security_scan(self):
        assert classify_department("scan vuln target.com") == Department.SECURITY

    def test_security_pentest(self):
        assert classify_department("pentest website này") == Department.SECURITY

    def test_engineering_code(self):
        assert classify_department("debug function này giúp") == Department.ENGINEERING

    def test_engineering_git(self):
        assert classify_department("git commit và push") == Department.ENGINEERING

    def test_research_search(self):
        assert classify_department("tìm kiếm thông tin về AI") == Department.RESEARCH

    def test_research_news(self):
        assert classify_department("tin tức mới nhất") == Department.RESEARCH

    def test_operations_remind(self):
        assert classify_department("nhắc nhở tôi lúc 5h") == Department.OPERATIONS

    def test_general_greeting(self):
        assert classify_department("xin chào") == Department.GENERAL

    def test_general_chitchat(self):
        assert classify_department("bạn khỏe không") == Department.GENERAL

    def test_general_ambiguous(self):
        assert classify_department("ok") == Department.GENERAL


class TestGetDepartmentTools:
    def test_finance_includes_mt5(self):
        tools = get_department_tools(Department.FINANCE)
        assert "mt5_price" in tools
        assert "trade_plan" in tools

    def test_includes_shared(self):
        tools = get_department_tools(Department.FINANCE)
        assert "web_search" in tools  # Shared tool

    def test_general_only_shared(self):
        tools = get_department_tools(Department.GENERAL)
        assert tools == SHARED_TOOLS


class TestGetDisplayName:
    def test_finance_vn(self):
        assert "Tài chính" in get_department_display_name(Department.FINANCE)

    def test_general(self):
        assert "CEO" in get_department_display_name(Department.GENERAL)
```

---

## Task 2: Add tool filtering to ToolRegistry

**Files:**
- Modify: `src/tools/base.py` (add `get_filtered` and `get_filtered_schemas`)
- Modify: `tests/unit/test_tools_base.py` (add tests if exists, or create)

### `src/tools/base.py` changes

Add 2 methods to `ToolRegistry` class:

```python
def get_filtered(self, tool_names: set[str]) -> list[ToolDefinition]:
    """Get tools filtered by name set."""
    return [t for name, t in self._tools.items() if name in tool_names]

def get_filtered_schemas(self, tool_names: set[str]) -> list[dict[str, Any]]:
    """Get tool schemas filtered by name set (for LLM function calling)."""
    return [
        t.to_openai_schema()
        for name, t in self._tools.items()
        if name in tool_names
    ]
```

### Tests: 3 new tests

```python
class TestToolRegistryFiltering:
    def test_get_filtered_returns_matching(self):
        registry = ToolRegistry()
        # Register 3 tools
        for name in ["tool_a", "tool_b", "tool_c"]:
            registry.register(ToolDefinition(name=name, description=f"desc {name}"))
        result = registry.get_filtered({"tool_a", "tool_c"})
        names = {t.name for t in result}
        assert names == {"tool_a", "tool_c"}

    def test_get_filtered_empty_set(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="tool_a", description="desc"))
        result = registry.get_filtered(set())
        assert result == []

    def test_get_filtered_schemas(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="tool_a", description="desc a"))
        registry.register(ToolDefinition(name="tool_b", description="desc b"))
        schemas = registry.get_filtered_schemas({"tool_a"})
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "tool_a"
```

---

## Task 3: Create DepartmentHead base class

**Files:**
- Create: `src/company/department_head.py`
- Create: `tests/unit/test_department_head.py`

### `src/company/department_head.py` (~130 lines)

```python
"""DepartmentHead — Base class for department heads in JARVIS Company.

Each department head:
1. Owns a filtered set of tools (only sees domain-relevant tools)
2. Has a domain-specific system prompt
3. Runs requests through AgentLoop with filtered tools
4. Can delegate to workers (via Swarm) for complex multi-step tasks
"""

from __future__ import annotations

from typing import Any

from src.company.departments import Department, get_department_tools, get_department_display_name
from src.gateway.models import AgentResponse, SessionState
from src.intelligence.agent_loop import AgentLoop
from src.intelligence.prompt_assembler import PromptAssembler
from src.metacognition.tracer import ReasoningTracer
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("company.department_head")

# Department-specific system prompt additions
_DEPARTMENT_PROMPTS: dict[Department, str] = {
    Department.FINANCE: (
        "Bạn là Trưởng phòng Tài chính của JARVIS Company. "
        "Chuyên về phân tích XAUUSD, MT5 trading, quản lý rủi ro, "
        "và chiến lược giao dịch. Luôn dùng dữ liệu real-time từ MT5."
    ),
    Department.SECURITY: (
        "Bạn là Trưởng phòng An ninh của JARVIS Company. "
        "Chuyên về penetration testing, vulnerability assessment, OSINT, "
        "và threat intelligence. Tuân thủ quy trình có authorization."
    ),
    Department.ENGINEERING: (
        "Bạn là Trưởng phòng Kỹ thuật của JARVIS Company. "
        "Chuyên về code review, debugging, Docker, Git, "
        "và phát triển phần mềm. Code clean, test-driven."
    ),
    Department.RESEARCH: (
        "Bạn là Trưởng phòng Nghiên cứu của JARVIS Company. "
        "Chuyên về tìm kiếm thông tin, phân tích tài liệu, "
        "và tổng hợp báo cáo. Luôn trích nguồn."
    ),
    Department.OPERATIONS: (
        "Bạn là Trưởng phòng Vận hành của JARVIS Company. "
        "Chuyên về lập lịch, nhắc nhở, xử lý media, "
        "và các tiện ích hệ thống."
    ),
}


class DepartmentHead:
    """Base department head — runs AgentLoop with filtered tools."""

    def __init__(
        self,
        dept: Department,
        tool_registry: ToolRegistry,
        cloud_model: str = "claude-sonnet-4-20250514",
        max_iterations: int = 8,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> None:
        self.dept = dept
        self._tool_registry = tool_registry

        # Get department-specific tool names
        self._tool_names = get_department_tools(dept)

        # Create department-specific assembler with domain prompt
        dept_prompt = _DEPARTMENT_PROMPTS.get(dept, "")
        self._assembler = PromptAssembler(
            max_context_tokens=30000,
            skill_summary="",
            tool_registry=tool_registry,  # Full registry for schema generation
            system_prompt_override=None,  # Use default JARVIS.md + department addition
        )

        # Create department-specific agent loop
        self._tracer = ReasoningTracer()
        self._agent_loop = AgentLoop(
            tool_registry=tool_registry,
            assembler=self._assembler,
            tracer=self._tracer,
            cloud_model=cloud_model,
            max_iterations=max_iterations,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        self._dept_prompt = dept_prompt
        log.info(
            "department_head_created",
            dept=dept.value,
            tools=len(self._tool_names),
        )

    @property
    def display_name(self) -> str:
        return get_department_display_name(self.dept)

    async def handle(
        self,
        session: SessionState,
        message: str,
        memory_context: str = "",
        skill_context: str = "",
    ) -> AgentResponse:
        """Handle a request using department-filtered tools."""
        # Prepend department role to skill context
        dept_context = self._dept_prompt
        if skill_context:
            dept_context = f"{dept_context}\n\n{skill_context}"

        result = await self._agent_loop.run(
            session=session,
            user_message=message,
            memory_context=memory_context,
            skill_context=dept_context,
            use_tools=True,
            tool_filter=self._tool_names,
        )

        log.info(
            "department_handled",
            dept=self.dept.value,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
        )

        return result
```

### Tests: `tests/unit/test_department_head.py` (~8 tests)

```python
"""Tests for DepartmentHead base class."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.company.department_head import DepartmentHead, _DEPARTMENT_PROMPTS
from src.company.departments import Department
from src.gateway.models import AgentResponse, SessionState, Channel


class TestDepartmentHead:
    @pytest.fixture
    def mock_registry(self):
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        return registry

    @pytest.fixture
    def session(self):
        return SessionState(channel=Channel.CLI, user_id="test")

    def test_create_finance_head(self, mock_registry):
        head = DepartmentHead(Department.FINANCE, mock_registry)
        assert head.dept == Department.FINANCE
        assert "mt5_price" in head._tool_names

    def test_create_security_head(self, mock_registry):
        head = DepartmentHead(Department.SECURITY, mock_registry)
        assert "nuclei_scan" in head._tool_names

    def test_display_name(self, mock_registry):
        head = DepartmentHead(Department.FINANCE, mock_registry)
        assert "Tài chính" in head.display_name

    def test_all_departments_have_prompts(self):
        for dept in Department:
            if dept != Department.GENERAL:
                assert dept in _DEPARTMENT_PROMPTS

    @pytest.mark.asyncio
    async def test_handle_delegates_to_agent_loop(self, mock_registry, session):
        head = DepartmentHead(Department.RESEARCH, mock_registry)
        mock_response = AgentResponse(
            request_id="test", session_id="test",
            content="research result", model_used="test-model",
        )
        head._agent_loop.run = AsyncMock(return_value=mock_response)

        result = await head.handle(session, "search for AI news")
        assert result.content == "research result"
        head._agent_loop.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_passes_tool_filter(self, mock_registry, session):
        head = DepartmentHead(Department.SECURITY, mock_registry)
        mock_response = AgentResponse(
            request_id="test", session_id="test",
            content="scan done", model_used="test-model",
        )
        head._agent_loop.run = AsyncMock(return_value=mock_response)

        await head.handle(session, "scan target.com")
        call_kwargs = head._agent_loop.run.call_args
        assert "tool_filter" in call_kwargs.kwargs
        assert "nuclei_scan" in call_kwargs.kwargs["tool_filter"]
```

---

## Task 4: Add `tool_filter` support to AgentLoop

**Files:**
- Modify: `src/intelligence/agent_loop.py` (add tool_filter param to run/run_stream)
- Modify: `tests/unit/test_agent_loop.py` (add tool_filter tests)

### `agent_loop.py` changes

**1. Add `tool_filter` parameter to `run()`:**

In the `run()` method signature, add `tool_filter: set[str] | None = None`:

```python
async def run(
    self,
    session: SessionState,
    user_message: str,
    memory_context: str = "",
    skill_context: str = "",
    use_tools: bool = True,
    model: str | None = None,
    tool_filter: set[str] | None = None,  # NEW: filter tools by name
) -> AgentResponse:
```

**2. Use tool_filter when building tool schemas:**

In the section where tool schemas are prepared (early in run()):
```python
# Build tool schemas — optionally filtered by department
if use_tools:
    if tool_filter:
        tool_schemas = self._tools.get_filtered_schemas(tool_filter)
    else:
        tool_schemas = self._tools.get_schemas()
else:
    tool_schemas = []
```

**3. Same change for `run_stream()`:**

Add `tool_filter: set[str] | None = None` parameter and same filtering logic.

### Tests: 2 new tests

```python
@pytest.mark.asyncio
async def test_run_with_tool_filter(self):
    """When tool_filter is set, only filtered tools are sent to LLM."""
    # Verify get_filtered_schemas is called instead of get_schemas

@pytest.mark.asyncio
async def test_run_without_tool_filter_uses_all(self):
    """When tool_filter is None, all tools are used."""
    # Verify get_schemas is called
```

---

## Task 5: Create CEO Orchestrator

**Files:**
- Create: `src/company/ceo.py`
- Create: `tests/unit/test_ceo.py`

### `src/company/ceo.py` (~150 lines)

```python
"""CEO — JARVIS Company orchestrator.

The CEO is the smart router that:
1. Classifies incoming requests by department
2. Handles simple/general requests directly (80% of traffic)
3. Delegates domain-specific requests to department heads (20%)

This replaces the flat LLMRouter for the routing decision,
while reusing the existing AgentLoop for actual execution.
"""

from __future__ import annotations

from typing import Any

from src.company.department_head import DepartmentHead
from src.company.departments import (
    Department,
    classify_department,
    get_department_display_name,
)
from src.gateway.models import AgentResponse, SessionState
from src.intelligence.agent_loop import AgentLoop
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("company.ceo")


class CEO:
    """JARVIS CEO — classifies and routes requests.

    Simple/general requests are handled directly via the main AgentLoop
    (same behavior as before — all tools available).
    Domain-specific requests are delegated to specialized department heads
    (filtered tools, domain prompts).
    """

    def __init__(
        self,
        agent_loop: AgentLoop,
        tool_registry: ToolRegistry,
        cloud_model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._agent_loop = agent_loop  # Main loop for direct handling
        self._tool_registry = tool_registry
        self._cloud_model = cloud_model

        # Initialize department heads
        self._departments: dict[Department, DepartmentHead] = {}
        for dept in Department:
            if dept == Department.GENERAL:
                continue  # CEO handles GENERAL directly
            self._departments[dept] = DepartmentHead(
                dept=dept,
                tool_registry=tool_registry,
                cloud_model=cloud_model,
            )

        log.info(
            "ceo_initialized",
            departments=len(self._departments),
            department_names=[d.value for d in self._departments],
        )

    async def handle(
        self,
        session: SessionState,
        message: str,
        memory_context: str = "",
        skill_context: str = "",
        use_tools: bool = True,
    ) -> AgentResponse:
        """Route request to appropriate handler.

        Returns:
            AgentResponse with department info in reasoning_trace
        """
        # 1. Classify department
        dept = classify_department(message)

        log.info("ceo_classify", dept=dept.value, message=message[:80])

        # 2. GENERAL → handle directly (same as before, all tools)
        if dept == Department.GENERAL:
            return await self._handle_direct(
                session, message, memory_context, skill_context, use_tools
            )

        # 3. Domain → delegate to department head
        head = self._departments.get(dept)
        if not head:
            # Fallback to direct handling
            return await self._handle_direct(
                session, message, memory_context, skill_context, use_tools
            )

        log.info("ceo_delegate", dept=dept.value, head=head.display_name)

        result = await head.handle(
            session=session,
            message=message,
            memory_context=memory_context,
            skill_context=skill_context,
        )

        # Tag response with department info
        dept_tag = f"[{get_department_display_name(dept)}]"
        if result.reasoning_trace:
            result.reasoning_trace = f"{dept_tag} {result.reasoning_trace}"
        else:
            result.reasoning_trace = dept_tag

        return result

    async def _handle_direct(
        self,
        session: SessionState,
        message: str,
        memory_context: str,
        skill_context: str,
        use_tools: bool,
    ) -> AgentResponse:
        """Handle request directly via main AgentLoop (all tools)."""
        return await self._agent_loop.run(
            session=session,
            user_message=message,
            memory_context=memory_context,
            skill_context=skill_context,
            use_tools=use_tools,
        )

    def get_department(self, dept: Department) -> DepartmentHead | None:
        """Get a department head by department enum."""
        return self._departments.get(dept)

    def get_status(self) -> dict[str, Any]:
        """Get CEO and department status."""
        return {
            "departments": {
                dept.value: {
                    "name": head.display_name,
                    "tools": len(head._tool_names),
                }
                for dept, head in self._departments.items()
            },
            "total_departments": len(self._departments),
        }
```

### Tests: `tests/unit/test_ceo.py` (~10 tests)

```python
"""Tests for CEO orchestrator."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.company.ceo import CEO
from src.company.departments import Department
from src.gateway.models import AgentResponse, SessionState, Channel


class TestCEO:
    @pytest.fixture
    def mock_agent_loop(self):
        loop = MagicMock()
        loop.run = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="direct response", model_used="test-model",
        ))
        return loop

    @pytest.fixture
    def mock_registry(self):
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        return registry

    @pytest.fixture
    def session(self):
        return SessionState(channel=Channel.CLI, user_id="test")

    @pytest.fixture
    def ceo(self, mock_agent_loop, mock_registry):
        return CEO(mock_agent_loop, mock_registry)

    def test_init_creates_departments(self, ceo):
        assert len(ceo._departments) == 5  # All except GENERAL

    def test_init_has_finance(self, ceo):
        assert Department.FINANCE in ceo._departments

    def test_init_has_security(self, ceo):
        assert Department.SECURITY in ceo._departments

    @pytest.mark.asyncio
    async def test_general_handled_directly(self, ceo, session):
        result = await ceo.handle(session, "xin chào")
        assert result.content == "direct response"
        ceo._agent_loop.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_finance_delegated(self, ceo, session):
        # Mock the finance department head
        finance_head = ceo._departments[Department.FINANCE]
        finance_head.handle = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="XAUUSD analysis", model_used="test-model",
        ))

        result = await ceo.handle(session, "phân tích XAUUSD")
        assert result.content == "XAUUSD analysis"
        finance_head.handle.assert_called_once()
        # Direct handler should NOT be called
        ceo._agent_loop.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_security_delegated(self, ceo, session):
        security_head = ceo._departments[Department.SECURITY]
        security_head.handle = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="scan results", model_used="test-model",
        ))

        result = await ceo.handle(session, "scan vuln target.com")
        assert result.content == "scan results"

    @pytest.mark.asyncio
    async def test_response_tagged_with_department(self, ceo, session):
        finance_head = ceo._departments[Department.FINANCE]
        finance_head.handle = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="trading info", model_used="test-model",
        ))

        result = await ceo.handle(session, "giá vàng")
        assert "Tài chính" in result.reasoning_trace

    def test_get_status(self, ceo):
        status = ceo.get_status()
        assert status["total_departments"] == 5
        assert "finance" in status["departments"]

    def test_get_department(self, ceo):
        head = ceo.get_department(Department.FINANCE)
        assert head is not None
        assert head.dept == Department.FINANCE

    def test_get_department_general_returns_none(self, ceo):
        assert ceo.get_department(Department.GENERAL) is None
```

---

## Task 6: Wire CEO into LLMRouter + JarvisApp

**Files:**
- Modify: `src/intelligence/router.py` (add CEO integration)
- Modify: `src/app.py` (add `init_company()`)

### `router.py` changes

Add CEO as optional delegation layer. The key change: when CEO is set, `route()` delegates to CEO instead of directly calling `_call_cloud()`.

```python
# In LLMRouter.__init__(), add:
self._ceo = None  # Set by JarvisApp.init_company()

# Add property:
@property
def ceo(self):
    return self._ceo

@ceo.setter
def ceo(self, value):
    self._ceo = value
```

In `route()` method, after cache check (line ~494), before calling `_call_cloud()`:
```python
# 3. If CEO is set, delegate routing to Company structure
if self._ceo and use_tools:
    trace.add_step("route", "ceo", "delegating")
    result = await self._ceo.handle(
        session=session,
        message=user_message,
        memory_context=memory_context,
        skill_context=skill_context,
        use_tools=use_tools,
    )
    # Track cost
    cost = self._tracker.estimate_cost(
        self._cloud_model, result.tokens_in, result.tokens_out
    )
    self._tracker.log_usage(
        model=self._cloud_model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        latency_ms=result.latency_ms,
        source="cloud",
    )
    result.cost_usd = cost

    # Cache response (skip for real-time queries)
    if result.content and not skip_cache:
        await self._cache.put(
            user_message, result.content, result.model_used,
            result.tokens_in, result.tokens_out,
        )

    trace.final_model = result.model_used
    trace.add_step("route", "department", result.reasoning_trace or "general")
    self._tracer.save_trace(trace)
    return result

# 4. Fallback: direct cloud call (when no CEO or use_tools=False)
trace.add_step("route", "cloud", self._cloud_model, use_tools=use_tools)
result = await self._call_cloud(...)
```

### `app.py` changes

Add `init_company()` method:
```python
def init_company(self) -> None:
    """Initialize Company Structure — CEO + Department Heads."""
    from src.company.ceo import CEO

    ceo = CEO(
        agent_loop=self.router._agent_loop,
        tool_registry=self.tool_registry,
    )
    self.router.ceo = ceo
    self._ceo = ceo
    log.info("company_initialized", departments=ceo.get_status()["total_departments"])
```

Add `self._ceo = None` in `__init__()`.

---

## Task 7: Add /company command + status integration

**Files:**
- Modify: `src/gateway/channels/telegram.py` (add /company command)
- Modify: `src/gateway/channels/cli.py` (add /company command)

### Telegram command: `/company`
```python
async def _cmd_company(self, update, context):
    """Show company structure and department status."""
    if not self._app._ceo:
        await update.message.reply_text("Company structure chưa được khởi tạo.")
        return

    status = self._app._ceo.get_status()
    lines = ["🏢 **JARVIS Company**\n"]

    for dept_name, info in status["departments"].items():
        lines.append(f"📋 **{info['name']}** — {info['tools']} tools")

    lines.append(f"\n📊 Tổng: {status['total_departments']} phòng ban")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
```

### CLI command: `/company`
Same info, formatted for terminal.

---

## Task 8: Full test run + verification

1. `pytest tests/unit/test_departments.py -v` — department classification
2. `pytest tests/unit/test_department_head.py -v` — department head base
3. `pytest tests/unit/test_ceo.py -v` — CEO orchestrator
4. `pytest tests/unit/ -x -q` — full suite, 0 regressions
5. Verify JARVIS startup with Company structure
6. Test classification: greeting → GENERAL, "giá vàng" → FINANCE, "scan vuln" → SECURITY

---

## Execution Order

```
Task 1 (Departments + classification)  — foundation, no deps
Task 2 (Tool filtering in ToolRegistry) — foundation, no deps
Task 3 (DepartmentHead base class)      — depends on Tasks 1 + 2
Task 4 (AgentLoop tool_filter support)   — depends on Task 2
Task 5 (CEO Orchestrator)               — depends on Tasks 1 + 3
Task 6 (Wire into Router + App)         — depends on Tasks 4 + 5
Task 7 (/company command)               — depends on Task 6
Task 8 (Full test run)                  — after all
```

**Parallelizable:** Tasks 1 + 2, Tasks 3 + 4 (after their deps), Tasks 5 (after 3)
