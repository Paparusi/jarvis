# Unified WebSocket + Pixel Office Dashboard

## Problem

JARVIS Company Phase 2 has 16 AI agents (CEO + 5 Heads + 10 Workers) but no visibility into their real-time operations. Current WebSocket is fragmented (`/ws/chat` + `/ws/trading`), each self-managed with duplicate logic. Bi wants to see the entire company hierarchy working in real-time — how CEO delegates, departments assign, workers execute.

## Goal

1. Redesign WebSocket into a unified hub with channel-based subscriptions
2. Add company event emissions throughout CEO/DeptHead/Worker
3. Build an isometric 2.5D pixel art office dashboard showing all 16 agents working in real-time

## Architecture

### Unified WebSocket Hub

Single WebSocket endpoint `/ws` with channel-based pub/sub. Replaces fragmented `/ws/chat` + `/ws/trading`.

**Protocol:**
```
Client → Server: {"action": "subscribe", "channels": ["company", "trading"]}
Client → Server: {"action": "unsubscribe", "channels": ["trading"]}
Server → Client: {"channel": "company", "event": "ceo_route", "data": {...}, "ts": "2026-03-10T12:00:00Z"}
Server → Client: {"type": "pong"}
```

**4 Channels:**

| Channel | Events | Source |
|---------|--------|--------|
| `company` | `ceo_route`, `dept_assign`, `dept_direct`, `worker_start`, `worker_done`, `worker_fail`, `worker_status` | CEO, DeptHead, Worker |
| `trading` | `approval_created`, `approval_response`, `position_open`, `position_close`, `plan_created` | TradingBrain |
| `chat` | `message_chunk`, `message_done`, `tool_call`, `tool_result` | AgentLoop streaming |
| `system` | `health_check`, `error`, `cost_alert`, `startup`, `shutdown` | HealthMonitor, CostGuard |

**WebSocketHub class:**
- `connections: dict[WebSocket, set[str]]` — tracks subscribed channels per connection
- `broadcast(channel, event, data)` — push to all subscribers of that channel
- `connect(ws)` / `disconnect(ws)` — lifecycle management
- Hooks into EventBus: subscribe event types → translate → broadcast

### Company Event Emissions

**New EventBus event types:**

| EventBus Event | WS Channel | WS Event Name |
|---|---|---|
| `COMPANY_CEO_ROUTE` | `company` | `ceo_route` |
| `COMPANY_DEPT_ASSIGN` | `company` | `dept_assign` |
| `COMPANY_DEPT_DIRECT` | `company` | `dept_direct` |
| `COMPANY_WORKER_BUSY` | `company` | `worker_start` |
| `COMPANY_WORKER_DONE` | `company` | `worker_done` |
| `COMPANY_WORKER_FAIL` | `company` | `worker_fail` |

**CEO** emits after classify + route:
```python
await event_bus.publish("COMPANY_CEO_ROUTE", {
    "message": message[:100], "department": dept.value, "session_id": session_id
})
```

**DepartmentHead** emits when delegating to worker:
```python
await event_bus.publish("COMPANY_DEPT_ASSIGN", {
    "department": self._dept.value, "worker_id": worker.worker_id,
    "worker_name": worker.name, "instruction": message[:100]
})
```

**Worker** emits task lifecycle:
```python
# Start
await event_bus.publish("COMPANY_WORKER_BUSY", {"worker_id": self.worker_id, "task_id": task_id})
# Done
await event_bus.publish("COMPANY_WORKER_DONE", {"worker_id": self.worker_id, "task_id": task_id, "duration_ms": duration})
# Fail
await event_bus.publish("COMPANY_WORKER_FAIL", {"worker_id": self.worker_id, "error": str(exc)[:200]})
```

### REST APIs

- `GET /api/company/status` — full org chart (hierarchy + worker states + metrics)
- `GET /api/company/activity?limit=50` — recent activity feed from EventBus history

## Dashboard UI: Pixel Office

### Concept

Isometric 2.5D pixel art office. 16 AI agents work in a virtual office. Real-time visualization via WebSocket `company` channel events.

### Office Layout

- **CEO Room** — center top, largest room, glass walls
- **5 Department Rooms** — arranged below, connected by corridor
  - Finance (left), Security, Engineering (center), Research, Operations (right)
- **Furniture per room:** desks, monitors (glow when active), chairs, plants
- **Common area:** corridor, coffee machine, whiteboard

### Character Design (32x32 pixel sprites)

| Role | Visual | Busy Animation |
|------|--------|----------------|
| CEO (JARVIS) | Black vest, glasses | Walks to department rooms |
| Finance Head | Navy vest | Calls workers |
| Market Analyst | Shirt, headphones | Typing + charts on monitor |
| Trader | Casual vest | Dual monitors glowing |
| Crypto Specialist | Hoodie, laptop | Laptop glows green |
| Security Head | Black shirt | Warning monitor |
| Pen Tester | Black hoodie | Matrix-style terminal |
| Security Researcher | Glasses, books | Notes + search |
| Engineering Head | Polo | Standing desk, whiteboard |
| Developer | T-shirt, coffee | Code on dual monitors |
| DevOps | T-shirt | Terminal commands running |
| Research Head | Casual blazer | Team coordination |
| Product Researcher | Notebook | Writing + web search |
| Data Analyst | Glasses | Charts/graphs on monitor |
| Operations Head | Smart casual | Calendar organizing |
| Office Manager | Office wear | Reception desk, phone |

### Animation States (per character)

1. **idle** — sitting at desk, occasional blink/look around (3 frames loop)
2. **walking** — 4 isometric directions NE/NW/SE/SW (4 frames each)
3. **working** — typing/writing/searching (4 frames loop)
4. **done** — checkmark pop-up + look at camera (3 frames)

### Interaction Animations (triggered by WS events)

| WS Event | Animation |
|----------|-----------|
| `ceo_route` | CEO stands → walks to target department room → speech bubble with task |
| `dept_assign` | Head gestures → selected worker starts working animation |
| `dept_direct` | Head starts working directly (no worker available) |
| `worker_start` | Worker typing animation + monitor glow + speech bubble "Working..." |
| `worker_done` | ✅ icon pop → worker looks up → brief celebration |
| `worker_fail` | ❌ flash + worker scratches head → head walks over |

### Activity Panel (overlay bottom)

- Semi-transparent dark bar at bottom of screen
- Live scrolling text: `[12:05:03] CEO → Finance: "phân tích XAUUSD"`
- Stats badges: workers busy count, daily cost/budget, total tasks today
- Clickable to expand full activity log

### Tech Stack

- **PixiJS 8** — 2D WebGL rendering, sprite animations
- **Sprite sheets** — TexturePacker format, 1 sheet per character type
- **Isometric map** — Tiled Map Editor JSON format or hand-coded grid
- **Pathfinding** — simple A* on isometric grid for CEO walking
- **React integration** — PixiJS canvas inside Next.js component

## Files to Create/Modify

### Backend (Python)
- Create: `src/gateway/ws_hub.py` — WebSocketHub class
- Modify: `src/gateway/channels/web.py` — replace `/ws/chat` + `/ws/trading` with unified `/ws`
- Modify: `src/gateway/event_bus.py` — add COMPANY_* event types
- Modify: `src/company/ceo.py` — emit COMPANY_CEO_ROUTE events
- Modify: `src/company/department_head.py` — emit COMPANY_DEPT_ASSIGN/DIRECT events
- Modify: `src/company/worker.py` — emit COMPANY_WORKER_* events
- Add: REST endpoints `/api/company/status`, `/api/company/activity`

### Frontend (Next.js/TypeScript)
- Create: `dashboard/hooks/useWebSocketHub.ts` — unified WS hook
- Create: `dashboard/app/company/page.tsx` — company page
- Create: `dashboard/components/company/PixelOffice.tsx` — PixiJS canvas component
- Create: `dashboard/components/company/ActivityFeed.tsx` — activity log overlay
- Create: `dashboard/components/company/OrgChart.tsx` — fallback static org chart
- Create: `dashboard/public/sprites/` — pixel art sprite sheets
- Modify: existing pages to use new `useWebSocketHub` instead of `useWebSocket`

### Assets
- Isometric office tileset (floor, walls, furniture)
- 4 character base types (CEO, Head, Worker male, Worker female) x 4 states x 4 directions
- UI elements: speech bubbles, status icons, activity panel
