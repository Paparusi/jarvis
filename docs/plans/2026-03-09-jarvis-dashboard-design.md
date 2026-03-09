# JARVIS Dashboard v1 — Design Doc

> Date: 2026-03-09
> Status: Approved

## Problem Statement

JARVIS has 203 tools, 33+ skills, Trading Brain, Memory system, Health Monitor — but the Web UI is only a basic chat box with 4 side panels in a single 869-line HTML file. No dashboard, no charts, no trading view, no memory browser. Users must use Telegram or CLI to access most features.

## Goals

- Full multi-page dashboard showing everything JARVIS can do
- Trading cockpit with live charts, positions, PnL tracking
- Memory browser with semantic search
- System monitoring and health overview
- Chat interface upgrade with streaming and tool badges
- Professional, modern UI

## Architecture

**Full-stack app:** Next.js frontend (React, App Router) + existing FastAPI backend.

- Next.js runs on WSL2 port 3000
- FastAPI runs on port 8000 (existing)
- WebSocket for real-time: chat streaming + live trading data
- SWR for data fetching with auto-refresh

## Pages (7)

### 1. Overview (Home Dashboard)
- Status cards: uptime, model (Claude Sonnet), tools count, skills count, cache hits
- LLM usage chart (Recharts — calls/day, cost/day over 30 days)
- Recent activity feed (last 10 interactions)
- Quick stats: memory count, active sessions, trading PnL today

### 2. Chat
- WebSocket streaming (upgrade of current)
- Markdown rendering + code syntax highlighting
- File upload (drag & drop)
- Feedback buttons (thumbs up/down)
- Tool usage badges per message
- Conversation history sidebar

### 3. Trading (Main cockpit)
- TradingView Lightweight Charts: XAUUSD candlestick (H1/M15)
- Active confluence zones overlay on chart
- Open positions table (ticket, type, entry, SL, TP, PnL)
- Trading Brain status card (running/stopped, current plan summary)
- PnL summary cards (daily, weekly, monthly)
- Trade history table with filters
- Start/Stop Brain controls
- Risk Guard status (circuit breaker state, daily stats)

### 4. Memory Browser
- Semantic search input (calls existing memory search)
- Memory list with category tags, importance score, timestamps
- Add new memory (manual input)
- Delete memories
- Knowledge graph visualization (entities + relations)
- Stats: total memories, breakdown by category

### 5. Skills Manager
- Grid/list view of all 33+ skills with metadata
- Cards showing: name, version, success rate, usage count, priority, emoji
- Skill detail modal (renders full SKILL.md content as markdown)
- Enable/disable toggle per skill
- Filter by category (core/productivity/analysis/meta/security/auto)

### 6. Health Monitor
- Health check results (API keys, DB, disk) with status indicators
- System metrics: CPU, RAM, disk usage
- Error log viewer (recent errors from structlog)
- Dreamtime status (last run time, next scheduled, stages completed)
- MCP server status (6 servers, connected/disconnected)

### 7. Settings
- Trading Brain config (risk params: max lot, max risk%, session schedule)
- Notification preferences
- API key status (masked display)
- Theme toggle (dark/light)

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Frontend | Next.js 15 (App Router) |
| Styling | Tailwind CSS |
| Charts (general) | Recharts |
| Charts (trading) | TradingView Lightweight Charts |
| State management | React hooks + SWR |
| WebSocket | Native WebSocket |
| Backend | Existing FastAPI (extend APIs) |
| Icons | Lucide React |
| Markdown | react-markdown + rehype-highlight |

## Backend API Extensions

### Existing (no changes needed)
```
GET  /api/status     — system status
GET  /api/health     — health checks
GET  /api/skills     — skill list
GET  /api/memory     — recent memories
WS   /ws/chat        — chat streaming
```

### New Endpoints Required
```
GET  /api/trading/status    — brain running/stopped, current plan summary
GET  /api/trading/positions — open positions from MT5
GET  /api/trading/history   — closed trades from persistence.py
GET  /api/trading/zones     — active confluence zones
GET  /api/trading/pnl       — PnL summary (daily/weekly/monthly)
POST /api/trading/control   — start/stop brain (body: {action: "start"|"stop"})
GET  /api/memory/search?q=  — semantic memory search
DELETE /api/memory/{id}     — delete a memory
POST /api/memory            — add a memory (body: {content, category})
GET  /api/activity          — recent interactions (last N)
GET  /api/system/metrics    — CPU, RAM, disk, uptime
GET  /api/system/errors     — recent error logs
GET  /api/dreamtime/status  — dreamtime scheduler state
WS   /ws/trading            — live price ticks + position updates
```

## UI Layout

```
┌──────────────────────────────────────────────────────────┐
│  JARVIS    [🏠] [💬] [📈] [🧠] [🎯] [🏥] [⚙️]          │
├────────┬─────────────────────────────────────────────────┤
│        │                                                 │
│  Side  │              Page Content                       │
│  bar   │                                                 │
│  Nav   │                                                 │
│        │                                                 │
│  🏠    │                                                 │
│  💬    │                                                 │
│  📈    │                                                 │
│  🧠    │                                                 │
│  🎯    │                                                 │
│  🏥    │                                                 │
│  ⚙️    │                                                 │
│        │                                                 │
├────────┴─────────────────────────────────────────────────┤
│  Status bar: Connected | Claude Sonnet | 203 tools       │
└──────────────────────────────────────────────────────────┘
```

## Data Flow

```
Next.js (port 3000)
    │
    ├── SWR fetch ──→ FastAPI REST APIs (port 8000)
    │                     │
    │                     ├── /api/trading/* → TradingBrain / MT5Client
    │                     ├── /api/memory/*  → MemoryManager
    │                     ├── /api/skills    → SkillLoader
    │                     └── /api/health    → HealthMonitor
    │
    ├── WebSocket ──→ /ws/chat    → LLMRouter → AgentLoop
    │
    └── WebSocket ──→ /ws/trading → MT5Client (price ticks)
```

## Non-Goals (v1)

- Mobile app (responsive web only)
- User authentication (single-user, localhost only)
- Real-time collaboration
- Custom theme editor
- Plugin/extension system

## Success Metrics

| Metric | Target |
|--------|--------|
| Pages | 7 fully functional |
| Load time | < 2s initial, < 500ms navigation |
| Trading chart | Live candles updating |
| Chat | Streaming with < 300ms TTFB |
| All JARVIS features accessible | Via UI (no CLI needed) |
