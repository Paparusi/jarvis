# Company Phase 2: Full Autonomous Worker System

## Problem

JARVIS Company Phase 1 has CEO + 5 Department Heads with tool filtering, but Department Heads are just routing layers — they don't have specialized sub-agents (workers) that can operate autonomously. Bi wants JARVIS to operate like a real tech startup where each AI agent has clear hierarchy, assigned duties, and persistent memory.

## Goal

Transform JARVIS into a fully autonomous AI tech startup with 3-tier hierarchy: CEO -> Department Heads -> Persistent Workers. Each worker is a fully autonomous agent with its own event loop, memory, and task queue.

## Company Profile

**Type:** Tech Startup
**Products/Services:**
1. XAUUSD Trading Bot (existing)
2. Crypto Airdrop Tools (planned)
3. AI Automation SaaS (planned)
4. Security Services (existing tools)
5. Product Discovery (ongoing research)

## Architecture

### 3-Tier Hierarchy

```
User (Bi) — Owner
└── CEO (JARVIS) — Strategic routing, coordination
    ├── Finance Head
    │   ├── Market Analyst (XAUUSD analysis, news)
    │   ├── Trader (execution, positions)
    │   └── Crypto Specialist (airdrop, DeFi)
    ├── Security Head
    │   ├── Pen Tester (offensive scanning)
    │   └── Security Researcher (CVE, threat intel)
    ├── Engineering Head
    │   ├── Developer (code analysis, generation)
    │   └── DevOps (deploy, monitor, automation)
    ├── Research Head
    │   ├── Product Researcher (market gaps, competitors)
    │   └── Data Analyst (statistics, visualization)
    └── Operations Head
        └── Office Manager (scheduling, comms, TTS)
```

**Total: 10 workers, 5 departments**

### Worker Architecture

Each Worker is a **fully autonomous agent**:
- Own `AgentLoop` instance (independent LLM context)
- Own `WorkerMemory` (SQLite-backed, per-worker scoped)
- Runs persistent background event loop
- Polls `TaskQueue` for assigned tasks
- Reports results back to Department Head
- Tracks task history and learned patterns

### Task Flow

```
User message
  → CEO classifies department
    → Department Head decomposes into sub-tasks (if complex)
      → Tasks enter TaskQueue (SQLite)
        → Workers claim and execute
          → Results aggregate back
            → Head synthesizes response
              → CEO returns to user
```

### Task Lifecycle

```
PENDING → ASSIGNED → IN_PROGRESS → COMPLETED / FAILED / ESCALATED
```

### Worker States

```
IDLE → BUSY → IDLE (normal cycle)
          → ERROR (recoverable)
          → OFFLINE (disabled by CEO)
```

## Data Model

### worker_tasks table

```sql
CREATE TABLE worker_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    department TEXT NOT NULL,
    worker_id TEXT,
    parent_task_id INTEGER,
    priority INTEGER DEFAULT 5,
    status TEXT DEFAULT 'pending',
    instruction TEXT NOT NULL,
    context TEXT DEFAULT '{}',
    result TEXT,
    error TEXT,
    session_id TEXT,
    created_at TEXT NOT NULL,
    assigned_at TEXT,
    completed_at TEXT
);
```

### worker_memories table

```sql
CREATE TABLE worker_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
```

### worker_metrics table

```sql
CREATE TABLE worker_metrics (
    worker_id TEXT PRIMARY KEY,
    tasks_completed INTEGER DEFAULT 0,
    tasks_failed INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0.0,
    avg_response_ms REAL DEFAULT 0.0,
    updated_at TEXT NOT NULL
);
```

## Worker Roster

### Finance Department

| Worker ID | Name | Tools |
|-----------|------|-------|
| finance.market_analyst | Market Analyst | mt5_candles, mt5_analyze, mt5_smc, technical_indicators, web_search, fetch_url |
| finance.trader | Trader | mt5_order, mt5_close, mt5_positions, trade_plan, trade_control, trade_pending, trade_status |
| finance.crypto_specialist | Crypto Specialist | web_search, browse_web, fetch_url, deep_search |

### Security Department

| Worker ID | Name | Tools |
|-----------|------|-------|
| security.pen_tester | Pen Tester | subdomain_enum, xss_scan, sqli_scan, ssrf_scan, nuclei_scan, port_scan, http_headers |
| security.researcher | Security Researcher | cve_lookup, tech_detect, reverse_dns, web_search, fetch_url |

### Engineering Department

| Worker ID | Name | Tools |
|-----------|------|-------|
| engineering.developer | Developer | ast_analyze, complexity_check, code_search, diff_summary, dependency_graph, run_python, code_exec |
| engineering.devops | DevOps | read_file, write_file, list_dir, run_python, code_exec |

### Research Department

| Worker ID | Name | Tools |
|-----------|------|-------|
| research.product_researcher | Product Researcher | web_search, browse_web, deep_search, fetch_url |
| research.data_analyst | Data Analyst | csv_analyze, json_query, sqlite_query, text_stats, json_transform |

### Operations Department

| Worker ID | Name | Tools |
|-----------|------|-------|
| operations.office_manager | Office Manager | set_reminder, list_reminders, daily_digest, text_to_speech, analyze_image, ocr_image |

## Safety & Cost Controls

### CostGuard

- MAX_DAILY_COST = $10/day total
- MAX_TASK_COST = $2/task
- MAX_WORKER_DAILY_COST = $3/worker/day
- Usage tracked per worker per day in worker_metrics

### Worker Safety Rules

1. Max 8 agent loop iterations per task
2. 120-second task timeout
3. No recursive worker spawning
4. Escalation chain: Worker -> Head -> CEO
5. CEO kill switch per worker
6. Daily cost cap enforcement

### Performance Tracking

- Tasks completed/failed counts
- Average response time
- Total cost per worker
- Success rate calculation
- Viewable via /company command
