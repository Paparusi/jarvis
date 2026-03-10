# JARVIS Company Structure — Design Document

**Date:** 2026-03-10
**Author:** CTO (Claude)
**Status:** Draft

## Problem

JARVIS hiện tại là "flat agent" — mọi request đi qua 1 LLMRouter → 1 AgentLoop. Không có chuyên môn hóa, không có delegation, không có quản lý phòng ban. Khi số lượng tools tăng (203+), LLM context bị overload vì phải biết TẤT CẢ tools cùng lúc.

## Goal

Transform JARVIS thành **AI Company** với cấu trúc:
```
User (Owner/CEO nhân loại)
  └─ JARVIS (CEO AI — orchestrator)
       ├─ Finance Department (Head + Workers)
       ├─ Security Department (Head + Workers)
       ├─ Engineering Department (Head + Workers)
       ├─ Research Department (Head + Workers)
       └─ Operations Department (Head + Workers)
```

## Key Insights từ Codebase Hiện Tại

### 1. TradingBrain = Blueprint cho Department Head
TradingBrain đã là department head pattern hoàn chỉnh:
- **Persistent lifecycle** (start/stop)
- **Sub-modules** (Planner, Monitor, Confirmer, PositionManager)
- **Event-driven** (callbacks, not polling)
- **State persistence** (SQLite)
- **Domain-specific tools** (22 trading tools)
- **Approval gate** (human-in-the-loop)

### 2. Swarm = Worker Pool
SwarmCoordinator đã có:
- Task decomposition (Decomposer)
- Agent factory (create ephemeral agents)
- Parallel execution + retry
- Result aggregation + dedup

### 3. 70% Foundation Exists
- Tool registry (203 tools, already grouped by domain)
- Memory system (semantic + episodic + knowledge graph)
- Agent loop (tool calling, 8 iterations, streaming)
- Event bus (pub/sub for inter-component communication)

## Architecture

### Layer 1: CEO Orchestrator (`src/company/ceo.py`)

CEO nhận MỌI request từ user, phân loại và quyết định:
1. **Handle trực tiếp** (80% simple requests): greeting, chitchat, simple facts
2. **Delegate cho Department** (20% domain tasks): trading analysis, security scan, code review

```python
class CEO:
    """JARVIS CEO — Smart router + orchestrator."""

    async def handle(self, session, message, memory_context, skill_context) -> AgentResponse:
        # 1. Classify request
        dept = self._classify(message)

        # 2. Simple → handle directly (1 LLM call, NO department overhead)
        if dept == Department.GENERAL:
            return await self._handle_direct(session, message, ...)

        # 3. Domain → delegate to department head
        head = self._departments[dept]
        return await head.handle(session, message, ...)
```

**Classification dùng keyword + LLM fallback:**
- Trading keywords → Finance
- Security keywords → Security
- Code/git keywords → Engineering
- Research keywords → Research
- Không match → GENERAL (CEO handle trực tiếp)

### Layer 2: Department Head (`src/company/department.py`)

Mỗi department head:
- **Owns domain tools** (chỉ thấy tools thuộc domain mình)
- **Has domain memory** (filtered context)
- **Manages workers** (delegate sub-tasks)
- **Reports to CEO** (structured response)

```python
class DepartmentHead:
    """Base class for department heads."""

    def __init__(self, dept: Department, tool_registry: ToolRegistry,
                 agent_loop: AgentLoop):
        self.dept = dept
        self.tools = tool_registry.filter_by_department(dept)
        self.agent_loop = agent_loop  # Shared, but with filtered tools

    async def handle(self, session, message, memory_context="") -> AgentResponse:
        # Run agent loop with ONLY department tools
        return await self.agent_loop.run(
            session, message,
            memory_context=memory_context,
            tool_filter=self.tools,
        )
```

### Layer 3: Workers (Existing Swarm)

Workers = current SwarmCoordinator agents. Department heads delegate complex multi-step tasks to worker pool.

### Department Tool Allocation

| Department | Tools | Count |
|-----------|-------|-------|
| Finance | mt5_*, trade_*, trading_calendar, technical_indicators | 23 |
| Security | subdomain_*, exploit_*, forensics_*, osint_*, nuclei_* | 35 |
| Engineering | code_*, git_*, docker_*, ast_*, run_python, shell | 20 |
| Research | web_search, fetch_url, browse_web, deep_search, query_documents | 8 |
| Operations | scheduler, tts, analyze_image, ingest_document | 6 |
| Shared | All departments can access: web_search, read_file, write_file | ~5 |

### Memory Isolation

```
Global Memory (user identity, preferences, directives)
  ├─ Finance Memory (trading events, P&L, market analysis)
  ├─ Security Memory (scan results, vulnerabilities, targets)
  ├─ Engineering Memory (code reviews, project state)
  └─ Research Memory (search results, documents, summaries)
```

## Implementation Strategy

### Phase 1: Foundation (This Sprint)

**Minimal viable Company:**
1. `Department` enum + `DepartmentRouter` (keyword classifier)
2. `DepartmentHead` base class
3. `CEO` orchestrator (classify → handle direct OR delegate)
4. Wire into existing `LLMRouter` (CEO replaces direct routing)
5. Tool filtering per department
6. **TradingBrain → FinanceDepartment** migration (wrap existing)

**Files:**
- Create: `src/company/__init__.py`
- Create: `src/company/departments.py` (Department enum, TOOL_ALLOCATION)
- Create: `src/company/department_head.py` (DepartmentHead base)
- Create: `src/company/ceo.py` (CEO orchestrator)
- Create: `src/company/router.py` (DepartmentRouter classifier)
- Modify: `src/intelligence/router.py` (wire CEO)
- Modify: `src/tools/base.py` (add filter_by_tags method)
- Modify: `src/app.py` (init_company)

### Phase 2: Specialization

- Each department gets custom system prompt (domain expertise)
- Department-level memory context
- Department heads can spawn workers (via Swarm)

### Phase 3: Inter-Department Communication

- Event-based messaging between departments
- CEO manages cross-department projects
- Shared knowledge base

### Phase 4: Autonomy

- Department heads make autonomous decisions
- Proactive department reports
- Budget/cost tracking per department

## Key Decisions

### 1. CEO = Smart Router, NOT Another Agent Loop
CEO is a thin classifier layer. Simple requests bypass ALL department overhead. Only domain-specific tasks trigger department delegation. This keeps latency low (80% requests = 1 LLM call, same as now).

### 2. Department Head = Filtered AgentLoop
Department heads don't need their own LLM implementation. They reuse the existing AgentLoop but with filtered tool set. This means:
- No code duplication
- Same streaming, caching, quality gates
- Just different tool visibility

### 3. TradingBrain Stays As-Is
TradingBrain is already more advanced than a generic DepartmentHead (it has lifecycle, monitoring, callbacks). We WRAP it, not replace it. Finance department delegates trading requests to TradingBrain.

### 4. Backward Compatible
Every current feature keeps working. The Company structure is an OVERLAY on existing code, not a rewrite. If CEO classification fails, request goes to GENERAL (same behavior as now).

## Cost Analysis

| Scenario | Before (flat) | After (company) |
|----------|--------------|-----------------|
| "xin chào" | 1 LLM call | 1 LLM call (CEO direct) |
| "giá vàng" | 1 LLM call + tools | 1 classify + 1 dept call |
| "scan vuln" | 1 LLM call + tools (ALL 203 tools in context) | 1 classify + 1 dept call (35 tools in context) |
| Complex research | 1 LLM call → swarm | 1 classify + dept → swarm |

**Net effect:** Simple requests = same cost. Domain requests = +1 classify call but BETTER quality (focused tool set, domain prompt).

## Risks

1. **Classification errors** → Wrong department gets request → Mitigate: fallback to GENERAL
2. **Latency overhead** → Extra classify step → Mitigate: keyword-based fast path (no LLM needed for 90%+ requests)
3. **Context fragmentation** → Department doesn't know about other domains → Mitigate: shared global memory
