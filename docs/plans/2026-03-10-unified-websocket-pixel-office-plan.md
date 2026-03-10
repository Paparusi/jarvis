# Unified WebSocket + Pixel Office Dashboard — Implementation Plan

> **For Claude:** Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Replace fragmented WebSocket endpoints with a unified hub, add company event emissions, and build an isometric pixel art office dashboard showing all 16 AI agents working in real-time.

**Architecture:** Unified WebSocketHub (`/ws`) with channel-based subscriptions (company, trading, chat, system). EventBus bridges internal events to WS clients. Pixel office rendered with PixiJS in Next.js, driven by `company` channel events.

**Tech Stack:** Python (FastAPI WebSocket), EventBus, Next.js 16, TypeScript, PixiJS 8, Tailwind CSS, SWR

---

## Task 1: WebSocketHub backend

**Files:**
- Create: `src/gateway/ws_hub.py`
- Create: `tests/unit/test_ws_hub.py`

### `src/gateway/ws_hub.py` (~120 lines)

```python
"""WebSocketHub — Unified WebSocket with channel-based subscriptions.

Protocol:
    Client → {"action": "subscribe", "channels": ["company", "trading"]}
    Client → {"action": "unsubscribe", "channels": ["trading"]}
    Client → {"type": "ping"}
    Server → {"channel": "company", "event": "ceo_route", "data": {...}, "ts": "..."}
    Server → {"type": "pong"}
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Protocol

from src.utils.logging import get_logger

log = get_logger("gateway.ws_hub")

VALID_CHANNELS = frozenset({"company", "trading", "chat", "system"})


class WSConnection(Protocol):
    """Minimal WebSocket interface for testability."""
    async def send_json(self, data: dict) -> None: ...
    async def receive_json(self) -> dict: ...


class WebSocketHub:
    """Unified WebSocket hub — manages connections and channel subscriptions."""

    def __init__(self) -> None:
        # ws -> set of subscribed channels
        self._connections: dict[Any, set[str]] = {}

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def connect(self, ws: Any) -> None:
        """Register a new connection (no channels subscribed yet)."""
        self._connections[ws] = set()
        log.info("ws_hub_connect", total=len(self._connections))

    def disconnect(self, ws: Any) -> None:
        """Remove a connection."""
        self._connections.pop(ws, None)
        log.info("ws_hub_disconnect", total=len(self._connections))

    def subscribe(self, ws: Any, channels: list[str]) -> list[str]:
        """Subscribe a connection to channels. Returns actually subscribed list."""
        if ws not in self._connections:
            return []
        valid = [ch for ch in channels if ch in VALID_CHANNELS]
        self._connections[ws].update(valid)
        return valid

    def unsubscribe(self, ws: Any, channels: list[str]) -> None:
        """Unsubscribe a connection from channels."""
        if ws in self._connections:
            self._connections[ws] -= set(channels)

    def get_subscriptions(self, ws: Any) -> set[str]:
        """Get channels a connection is subscribed to."""
        return self._connections.get(ws, set()).copy()

    async def broadcast(self, channel: str, event: str, data: dict[str, Any] | None = None) -> int:
        """Broadcast an event to all subscribers of a channel. Returns count sent."""
        if channel not in VALID_CHANNELS:
            return 0

        message = {
            "channel": channel,
            "event": event,
            "data": data or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        dead: list[Any] = []
        sent = 0
        for ws, channels in self._connections.items():
            if channel in channels:
                try:
                    await ws.send_json(message)
                    sent += 1
                except Exception:
                    dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

        return sent

    async def handle_client_message(self, ws: Any, raw: dict) -> dict | None:
        """Process a message from a client. Returns response dict or None."""
        action = raw.get("action")
        msg_type = raw.get("type")

        if msg_type == "ping":
            return {"type": "pong"}

        if action == "subscribe":
            channels = raw.get("channels", [])
            subscribed = self.subscribe(ws, channels)
            return {"type": "subscribed", "channels": subscribed}

        if action == "unsubscribe":
            channels = raw.get("channels", [])
            self.unsubscribe(ws, channels)
            return {"type": "unsubscribed", "channels": channels}

        return None


# Singleton
_hub: WebSocketHub | None = None


def get_ws_hub() -> WebSocketHub:
    """Get the global WebSocketHub singleton."""
    global _hub
    if _hub is None:
        _hub = WebSocketHub()
    return _hub
```

### `tests/unit/test_ws_hub.py` (~20 tests)

Test using a mock WS that has `send_json`/`receive_json`:

```python
class MockWS:
    def __init__(self):
        self.sent = []
        self.closed = False
    async def send_json(self, data):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(data)
    async def receive_json(self):
        return {}
```

| Class | Tests |
|-------|-------|
| TestConnect | connect_adds, disconnect_removes, disconnect_unknown_ok, connection_count |
| TestSubscribe | subscribe_valid, subscribe_invalid_channel, subscribe_multiple, unsubscribe, get_subscriptions |
| TestBroadcast | broadcast_to_subscribers, broadcast_skips_unsubscribed, broadcast_invalid_channel, broadcast_removes_dead, broadcast_returns_count, broadcast_empty_data |
| TestHandleMessage | ping_pong, subscribe_action, unsubscribe_action, unknown_returns_none |
| TestSingleton | get_ws_hub_returns_same |

---

## Task 2: Company event emissions

**Files:**
- Modify: `src/gateway/event_bus.py` — add COMPANY_* event types to EventType enum
- Modify: `src/company/ceo.py` — emit COMPANY_CEO_ROUTE after classify
- Modify: `src/company/department_head.py` — emit COMPANY_DEPT_ASSIGN / COMPANY_DEPT_DIRECT
- Modify: `src/company/worker.py` — emit COMPANY_WORKER_BUSY / DONE / FAIL

### event_bus.py — Add to EventType enum:

```python
# Company events
COMPANY_CEO_ROUTE = "company_ceo_route"
COMPANY_DEPT_ASSIGN = "company_dept_assign"
COMPANY_DEPT_DIRECT = "company_dept_direct"
COMPANY_WORKER_BUSY = "company_worker_busy"
COMPANY_WORKER_DONE = "company_worker_done"
COMPANY_WORKER_FAIL = "company_worker_fail"
```

### ceo.py — In `handle()`, after classify department:

```python
# Import at top:
from src.gateway.event_bus import get_event_bus

# After dept = classify(message) and before delegation:
bus = get_event_bus()
asyncio.create_task(bus.publish(
    "company_ceo_route",
    {"message": message[:100], "department": dept.value, "session_id": session.session_id},
    source="ceo",
))
```

Use `asyncio.create_task` so event emission doesn't slow down the main request path. Fire-and-forget.

### department_head.py — In `handle()`:

```python
from src.gateway.event_bus import get_event_bus

# When selecting and delegating to worker:
bus = get_event_bus()
asyncio.create_task(bus.publish(
    "company_dept_assign",
    {"department": self._dept.value, "worker_id": worker.worker_id,
     "worker_name": worker.name, "instruction": message[:100]},
    source=f"dept_{self._dept.value}",
))

# When falling back to direct handling (no workers available):
asyncio.create_task(bus.publish(
    "company_dept_direct",
    {"department": self._dept.value, "reason": "no_idle_workers"},
    source=f"dept_{self._dept.value}",
))
```

### worker.py — In `_execute_task()` and `execute_direct()`:

```python
from src.gateway.event_bus import get_event_bus

# At start of _execute_task / execute_direct:
bus = get_event_bus()
asyncio.create_task(bus.publish(
    "company_worker_busy",
    {"worker_id": self.worker_id, "worker_name": self.name,
     "department": self.department, "task_id": task_id},
    source=self.worker_id,
))

# On success:
asyncio.create_task(bus.publish(
    "company_worker_done",
    {"worker_id": self.worker_id, "worker_name": self.name,
     "department": self.department, "task_id": task_id,
     "duration_ms": elapsed_ms, "result_preview": response.content[:100]},
    source=self.worker_id,
))

# On fail/timeout:
asyncio.create_task(bus.publish(
    "company_worker_fail",
    {"worker_id": self.worker_id, "worker_name": self.name,
     "department": self.department, "error": str(exc)[:200]},
    source=self.worker_id,
))
```

**No new tests needed** — existing tests mock EventBus and don't break. Events are fire-and-forget side effects.

---

## Task 3: Wire WebSocketHub into web.py + REST APIs

**Files:**
- Modify: `src/gateway/channels/web.py` — replace `/ws/chat` + `/ws/trading` with unified `/ws`, add company REST APIs

### Replace WebSocket endpoints with unified `/ws`:

```python
from src.gateway.ws_hub import get_ws_hub

# Remove old /ws/chat and /ws/trading endpoints.
# Replace with:

hub = get_ws_hub()

@fastapi_app.websocket("/ws")
async def websocket_unified(ws: WebSocket):
    await ws.accept()
    hub.connect(ws)
    try:
        while True:
            raw = await ws.receive_json()
            response = await hub.handle_client_message(ws, raw)
            if response:
                await ws.send_json(response)
            # Also handle chat messages inline (for /ws/chat backward compat):
            if raw.get("type") == "message":
                text = raw.get("text", "").strip()
                if text:
                    async for event in adapter.handle_message_stream(text, raw.get("session_id")):
                        await ws.send_json({"channel": "chat", **event})
            elif raw.get("type") == "command":
                result = await adapter.handle_command(raw.get("command", ""))
                await ws.send_json({"channel": "chat", "type": "command_result", "data": result})
    except Exception:
        pass
    finally:
        hub.disconnect(ws)
```

### Wire EventBus → WebSocketHub bridge:

After creating hub, subscribe company events to broadcast:

```python
# Bridge EventBus → WebSocketHub
async def _bridge_event(event):
    """Forward EventBus events to WebSocket clients."""
    # Map event types to WS channel + event name
    mapping = {
        "company_ceo_route": ("company", "ceo_route"),
        "company_dept_assign": ("company", "dept_assign"),
        "company_dept_direct": ("company", "dept_direct"),
        "company_worker_busy": ("company", "worker_start"),
        "company_worker_done": ("company", "worker_done"),
        "company_worker_fail": ("company", "worker_fail"),
    }
    pair = mapping.get(event.type)
    if pair:
        await hub.broadcast(pair[0], pair[1], event.data)

bus = get_event_bus()
for evt_type in [
    "company_ceo_route", "company_dept_assign", "company_dept_direct",
    "company_worker_busy", "company_worker_done", "company_worker_fail",
]:
    bus.subscribe(evt_type, _bridge_event)
```

### Wire trading approval WS push through hub:

```python
# Replace old trading_ws_clients approach:
async def _push_trading_ws(event: dict):
    await hub.broadcast("trading", event.get("event", "update"), event)

# Wire to approval_manager same as before
if adapter._app and getattr(adapter._app, 'trading_brain', None):
    if hasattr(adapter._app.trading_brain, 'approval_manager'):
        adapter._app.trading_brain.approval_manager.on_ws_event(_push_trading_ws)
```

### Add company REST API endpoints:

```python
@fastapi_app.get("/api/company/status")
async def api_company_status():
    """Full org chart with worker states and metrics."""
    if not adapter._app or not adapter._app._ceo:
        return JSONResponse({"error": "Company not initialized"})
    return JSONResponse(adapter._app._ceo.get_status())

@fastapi_app.get("/api/company/activity")
async def api_company_activity(limit: int = 50):
    """Recent company activity from EventBus history."""
    bus = get_event_bus()
    events = bus.get_recent_events(limit=limit)
    company_events = [
        {
            "type": e.type,
            "data": e.data,
            "source": e.source,
            "ts": datetime.fromtimestamp(e.timestamp, tz=timezone.utc).isoformat(),
        }
        for e in events
        if e.type.startswith("company_")
    ]
    return JSONResponse({"events": company_events[-limit:]})
```

### Update next.config.ts rewrite:

The existing `/ws/:path*` rewrite already covers `/ws`. But we should add explicit `/ws` (no path) support. Actually the Next.js proxy pattern `source: "/ws/:path*"` may not match bare `/ws`. We need to add:

```typescript
{ source: "/ws", destination: "http://localhost:8000/ws" },
```

---

## Task 4: Frontend — useWebSocketHub hook

**Files:**
- Create: `dashboard/src/hooks/useWebSocketHub.ts`

### `useWebSocketHub.ts` (~80 lines)

```typescript
"use client";

import { useEffect, useRef, useState, useCallback } from "react";

export interface WSEvent {
  channel: string;
  event: string;
  data: Record<string, any>;
  ts: string;
}

interface UseWebSocketHubOptions {
  channels: string[];
  maxEvents?: number;
}

export function useWebSocketHub({ channels, maxEvents = 200 }: UseWebSocketHubOptions) {
  const ws = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<WSEvent[]>([]);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);

    socket.onopen = () => {
      setConnected(true);
      // Subscribe to channels
      socket.send(JSON.stringify({ action: "subscribe", channels }));
    };

    socket.onclose = () => {
      setConnected(false);
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    socket.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        // Only track channel events (not pong/subscribed)
        if (data.channel && data.event) {
          setEvents((prev) => {
            const next = [...prev, data as WSEvent];
            return next.length > maxEvents ? next.slice(-maxEvents) : next;
          });
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.current = socket;
  }, [channels, maxEvents]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: any) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(data));
    }
  }, []);

  const clearEvents = useCallback(() => setEvents([]), []);

  return { connected, events, send, clearEvents };
}
```

---

## Task 5: Frontend — Company page with org chart + activity feed

**Files:**
- Create: `dashboard/src/app/company/page.tsx`
- Modify: `dashboard/src/components/Sidebar.tsx` — add Company nav item

### Sidebar.tsx — Add Company to NAV_ITEMS:

```typescript
import { Building2 } from "lucide-react"; // add import

// Add after Trading item:
{ href: "/company", label: "Company", icon: Building2 },
```

### `dashboard/src/app/company/page.tsx` (~200 lines)

Two-panel layout: Org Chart (left) + Activity Feed (right) + Stats Bar (bottom).

This is the **non-pixel** version that works immediately. The pixel office (Task 6) will be an enhancement layer on top.

```typescript
"use client";

import { useWebSocketHub, WSEvent } from "@/hooks/useWebSocketHub";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { useRef, useEffect } from "react";

const STATUS_ICONS: Record<string, string> = {
  idle: "🟢", busy: "🔵", error: "🔴", offline: "⚫",
};

const EVENT_COLORS: Record<string, string> = {
  ceo_route: "text-yellow-400",
  dept_assign: "text-blue-400",
  dept_direct: "text-blue-300",
  worker_start: "text-cyan-400",
  worker_done: "text-green-400",
  worker_fail: "text-red-400",
};

export default function CompanyPage() {
  const { connected, events } = useWebSocketHub({ channels: ["company", "system"] });
  const { data: status, mutate } = useSWR("/api/company/status", fetcher, { refreshInterval: 5000 });
  const feedRef = useRef<HTMLDivElement>(null);

  // Auto-scroll activity feed
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [events]);

  // Refresh org chart on worker events
  useEffect(() => {
    const last = events[events.length - 1];
    if (last && ["worker_start", "worker_done", "worker_fail"].includes(last.event)) {
      mutate();
    }
  }, [events, mutate]);

  const departments = status?.departments || {};
  const totalWorkers = status?.total_workers || 0;
  const busyCount = Object.values(departments).reduce((acc: number, dept: any) => {
    return acc + (dept.workers || []).filter((w: any) => w.status === "busy").length;
  }, 0);

  return (
    <div className="h-full flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Company HQ</h1>
        <span className={`text-xs px-2 py-1 rounded-full ${connected ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}>
          {connected ? "LIVE" : "OFFLINE"}
        </span>
      </div>

      <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-4 min-h-0">
        {/* Left: Org Chart */}
        <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5 overflow-y-auto">
          <h2 className="text-lg font-semibold mb-4">🏢 Organization</h2>
          <div className="space-y-4">
            {/* CEO */}
            <div className="flex items-center gap-2 text-yellow-400 font-medium">
              <span>👔</span> CEO (JARVIS)
            </div>

            {/* Departments */}
            {Object.entries(departments).map(([deptName, dept]: [string, any]) => (
              <div key={deptName} className="ml-4">
                <div className="flex items-center gap-2 text-sm font-medium text-gray-300 mb-1">
                  <span className="text-gray-500">├──</span>
                  {deptName.charAt(0).toUpperCase() + deptName.slice(1)} Head
                </div>
                {(dept.workers || []).map((w: any) => (
                  <div key={w.worker_id} className="ml-6 flex items-center justify-between text-sm py-0.5">
                    <div className="flex items-center gap-2">
                      <span>{STATUS_ICONS[w.status] || "⚫"}</span>
                      <span className="text-gray-400">{w.name}</span>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-gray-600">
                      <span>{w.tasks_completed || 0} tasks</span>
                      <span>${(w.total_cost || 0).toFixed(2)}</span>
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* Right: Activity Feed */}
        <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5 flex flex-col min-h-0">
          <h2 className="text-lg font-semibold mb-4">📡 Activity Feed</h2>
          <div ref={feedRef} className="flex-1 overflow-y-auto space-y-1 text-sm font-mono">
            {events.length === 0 && (
              <div className="text-gray-600 text-center py-8">Waiting for events...</div>
            )}
            {events.map((e, i) => (
              <div key={i} className="flex gap-2">
                <span className="text-gray-600 shrink-0">{e.ts?.slice(11, 19) || ""}</span>
                <span className={`${EVENT_COLORS[e.event] || "text-gray-400"}`}>
                  {formatEvent(e)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Bottom Stats Bar */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl px-5 py-3 flex items-center gap-6 text-sm">
        <span className="text-gray-400">
          Workers: <span className="text-white">{totalWorkers}</span>
        </span>
        <span className="text-gray-400">
          Busy: <span className="text-cyan-400">{busyCount}</span>
        </span>
        <span className="text-gray-400">
          Cost: <span className="text-green-400">${(status?.cost?.daily_spent || 0).toFixed(2)}</span>
          <span className="text-gray-600">/$10</span>
        </span>
        <span className="text-gray-400">
          Events: <span className="text-white">{events.length}</span>
        </span>
      </div>
    </div>
  );
}

function formatEvent(e: WSEvent): string {
  const d = e.data;
  switch (e.event) {
    case "ceo_route":
      return `CEO → ${d.department}: "${d.message || ""}"`;
    case "dept_assign":
      return `${d.department} → ${d.worker_name}: "${d.instruction || ""}"`;
    case "dept_direct":
      return `${d.department}: handling directly (${d.reason})`;
    case "worker_start":
      return `${d.worker_name} started working`;
    case "worker_done":
      return `${d.worker_name} ✅ done (${d.duration_ms}ms)`;
    case "worker_fail":
      return `${d.worker_name} ❌ ${d.error || "failed"}`;
    default:
      return `${e.event}: ${JSON.stringify(d).slice(0, 80)}`;
  }
}
```

---

## Task 6: Pixel Office — PixiJS isometric rendering

**Files:**
- Create: `dashboard/src/components/company/PixelOffice.tsx` — main PixiJS canvas component
- Create: `dashboard/src/components/company/sprites.ts` — sprite generation (programmatic pixel art)
- Create: `dashboard/src/components/company/office-map.ts` — isometric office layout constants
- Modify: `dashboard/src/app/company/page.tsx` — add PixelOffice as top panel

### Approach: Programmatic pixel art (no external assets needed)

Instead of loading sprite sheets (which would need a pixel artist), generate pixel art programmatically using PixiJS Graphics primitives. This gives us:
- Zero external dependencies (no sprite files)
- Dynamic coloring per worker role
- Immediate results without asset pipeline

### Install PixiJS:

```bash
cd dashboard && npm install pixi.js@^8
```

### `office-map.ts` (~60 lines)

```typescript
// Isometric grid constants
export const TILE_W = 64;
export const TILE_H = 32;
export const OFFICE_COLS = 16;
export const OFFICE_ROWS = 12;

// Convert grid coords to isometric screen coords
export function toIso(col: number, row: number): { x: number; y: number } {
  return {
    x: (col - row) * (TILE_W / 2) + 500,  // center offset
    y: (col + row) * (TILE_H / 2) + 50,
  };
}

// Room definitions: {id, col, row, w, h}
export const ROOMS = [
  { id: "ceo", label: "CEO", col: 6, row: 1, w: 4, h: 3 },
  { id: "finance", label: "Finance", col: 1, row: 5, w: 3, h: 3 },
  { id: "security", label: "Security", col: 4, row: 5, w: 3, h: 3 },
  { id: "engineering", label: "Engineering", col: 7, row: 5, w: 3, h: 3 },
  { id: "research", label: "Research", col: 10, row: 5, w: 3, h: 3 },
  { id: "operations", label: "Operations", col: 13, row: 5, w: 2, h: 3 },
];

// Worker positions within rooms (relative to room origin)
export interface WorkerPos {
  workerId: string;
  col: number;
  row: number;
  room: string;
}

export const WORKER_POSITIONS: WorkerPos[] = [
  // CEO room
  { workerId: "ceo", col: 7, row: 2, room: "ceo" },
  // Finance
  { workerId: "finance.market_analyst", col: 1, row: 5, room: "finance" },
  { workerId: "finance.trader", col: 2, row: 6, room: "finance" },
  { workerId: "finance.crypto_specialist", col: 3, row: 5, room: "finance" },
  // Security
  { workerId: "security.pen_tester", col: 4, row: 5, room: "security" },
  { workerId: "security.researcher", col: 5, row: 6, room: "security" },
  // Engineering
  { workerId: "engineering.developer", col: 7, row: 5, room: "engineering" },
  { workerId: "engineering.devops", col: 8, row: 6, room: "engineering" },
  // Research
  { workerId: "research.product_researcher", col: 10, row: 5, room: "research" },
  { workerId: "research.data_analyst", col: 11, row: 6, room: "research" },
  // Operations
  { workerId: "operations.office_manager", col: 13, row: 5, room: "operations" },
];
```

### `sprites.ts` (~150 lines)

Programmatic pixel character + desk drawing using PixiJS Graphics:

```typescript
import { Graphics, Container, Text, TextStyle } from "pixi.js";

const ROLE_COLORS: Record<string, number> = {
  ceo: 0x1a1a2e,        // dark navy vest
  finance: 0x1e3a5f,    // navy blue
  security: 0x2d2d2d,   // dark gray/black
  engineering: 0x4a3080, // purple
  research: 0xcc6600,    // orange
  operations: 0x555555,  // gray
};

export function createCharacter(department: string, name: string): Container {
  const container = new Container();

  const color = ROLE_COLORS[department] || 0x444444;

  // Body (isometric figure: simple colored rectangle with head)
  const body = new Graphics();
  // Torso
  body.rect(-6, -16, 12, 12);
  body.fill(color);
  // Head
  body.circle(0, -22, 5);
  body.fill(0xffcc99); // skin
  // Legs
  body.rect(-5, -4, 4, 6);
  body.fill(0x333333);
  body.rect(1, -4, 4, 6);
  body.fill(0x333333);

  container.addChild(body);

  // Name label
  const label = new Text({
    text: name,
    style: new TextStyle({ fontSize: 8, fill: 0xcccccc, fontFamily: "monospace" }),
  });
  label.anchor.set(0.5, 0);
  label.y = 4;
  container.addChild(label);

  return container;
}

export function createDesk(): Graphics {
  const desk = new Graphics();
  // Isometric desk top
  desk.moveTo(0, 0);
  desk.lineTo(20, -10);
  desk.lineTo(40, 0);
  desk.lineTo(20, 10);
  desk.closePath();
  desk.fill(0x8b6914);
  // Monitor
  desk.rect(14, -18, 12, 8);
  desk.fill(0x222244);
  desk.rect(15, -17, 10, 6);
  desk.fill(0x3344aa);  // screen glow
  return desk;
}

export function createStatusBubble(status: string): Container {
  const container = new Container();
  const bg = new Graphics();
  bg.roundRect(-12, -30, 24, 14, 4);
  bg.fill(0x000000, 0.7);
  container.addChild(bg);

  const icons: Record<string, string> = {
    working: "⌨️",
    done: "✅",
    fail: "❌",
    idle: "",
  };
  const text = new Text({
    text: icons[status] || "",
    style: new TextStyle({ fontSize: 10 }),
  });
  text.anchor.set(0.5, 0.5);
  text.y = -23;
  container.addChild(text);
  return container;
}

export function createSpeechBubble(message: string): Container {
  const container = new Container();
  const truncated = message.length > 30 ? message.slice(0, 30) + "..." : message;

  const bg = new Graphics();
  bg.roundRect(-60, -45, 120, 20, 6);
  bg.fill(0xffffff, 0.9);
  // Tail
  bg.moveTo(-2, -25);
  bg.lineTo(2, -25);
  bg.lineTo(0, -20);
  bg.fill(0xffffff, 0.9);
  container.addChild(bg);

  const text = new Text({
    text: truncated,
    style: new TextStyle({ fontSize: 8, fill: 0x111111, fontFamily: "monospace" }),
  });
  text.anchor.set(0.5, 0.5);
  text.y = -35;
  container.addChild(text);
  return container;
}
```

### `PixelOffice.tsx` (~200 lines)

```typescript
"use client";

import { useEffect, useRef, useCallback } from "react";
import { Application, Container, Graphics, Text, TextStyle } from "pixi.js";
import { WSEvent } from "@/hooks/useWebSocketHub";
import { ROOMS, WORKER_POSITIONS, toIso } from "./office-map";
import { createCharacter, createDesk, createStatusBubble, createSpeechBubble } from "./sprites";

interface PixelOfficeProps {
  events: WSEvent[];
  workerStatuses: Record<string, string>; // workerId -> "idle"|"busy"|"error"|"offline"
}

export default function PixelOffice({ events, workerStatuses }: PixelOfficeProps) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<Application | null>(null);
  const workersRef = useRef<Map<string, Container>>(new Map());
  const bubblesRef = useRef<Map<string, Container>>(new Map());

  // Initialize PixiJS app
  useEffect(() => {
    if (!canvasRef.current) return;
    const app = new Application();

    (async () => {
      await app.init({
        width: 1000,
        height: 500,
        backgroundColor: 0x0a0a14,
        antialias: true,
        resolution: window.devicePixelRatio || 1,
        autoDensity: true,
      });
      canvasRef.current?.appendChild(app.canvas);
      appRef.current = app;

      // Draw floor tiles
      drawFloor(app.stage);

      // Draw rooms
      ROOMS.forEach((room) => drawRoom(app.stage, room));

      // Place workers
      WORKER_POSITIONS.forEach((wp) => {
        const dept = wp.room;
        const pos = toIso(wp.col, wp.row);
        const name = wp.workerId.split(".").pop() || wp.workerId;

        // Desk
        const desk = createDesk();
        desk.x = pos.x - 10;
        desk.y = pos.y - 5;
        app.stage.addChild(desk);

        // Character
        const char = createCharacter(dept, name);
        char.x = pos.x;
        char.y = pos.y;
        app.stage.addChild(char);
        workersRef.current.set(wp.workerId, char);
      });
    })();

    return () => {
      app.destroy(true);
    };
  }, []);

  // React to WS events — animate workers
  useEffect(() => {
    const lastEvent = events[events.length - 1];
    if (!lastEvent || !appRef.current) return;

    const workerId = lastEvent.data?.worker_id;
    const charContainer = workerId ? workersRef.current.get(workerId) : null;

    // Remove old bubble for this worker
    if (workerId && bubblesRef.current.has(workerId)) {
      const old = bubblesRef.current.get(workerId)!;
      old.parent?.removeChild(old);
      bubblesRef.current.delete(workerId);
    }

    if (charContainer) {
      let bubble: Container | null = null;

      switch (lastEvent.event) {
        case "worker_start":
          bubble = createStatusBubble("working");
          break;
        case "worker_done":
          bubble = createStatusBubble("done");
          // Auto-remove after 3s
          setTimeout(() => {
            bubble?.parent?.removeChild(bubble);
            if (workerId) bubblesRef.current.delete(workerId);
          }, 3000);
          break;
        case "worker_fail":
          bubble = createStatusBubble("fail");
          setTimeout(() => {
            bubble?.parent?.removeChild(bubble);
            if (workerId) bubblesRef.current.delete(workerId);
          }, 5000);
          break;
      }

      if (bubble) {
        bubble.x = charContainer.x;
        bubble.y = charContainer.y;
        appRef.current.stage.addChild(bubble);
        bubblesRef.current.set(workerId, bubble);
      }
    }

    // CEO speech bubble for routing events
    if (lastEvent.event === "ceo_route") {
      const ceo = workersRef.current.get("ceo");
      if (ceo && appRef.current) {
        const msg = `→ ${lastEvent.data.department}: "${lastEvent.data.message?.slice(0, 25) || ""}"`;
        const speechBubble = createSpeechBubble(msg);
        speechBubble.x = ceo.x;
        speechBubble.y = ceo.y;
        appRef.current.stage.addChild(speechBubble);
        setTimeout(() => speechBubble.parent?.removeChild(speechBubble), 4000);
      }
    }
  }, [events]);

  // Update status colors
  useEffect(() => {
    workersRef.current.forEach((container, workerId) => {
      const status = workerStatuses[workerId] || "idle";
      // Could tint the character based on status
      container.alpha = status === "offline" ? 0.3 : 1.0;
    });
  }, [workerStatuses]);

  return (
    <div
      ref={canvasRef}
      className="w-full h-full rounded-xl overflow-hidden bg-[#0a0a14]"
      style={{ minHeight: 400 }}
    />
  );
}

function drawFloor(stage: Container) {
  const floor = new Graphics();
  // Draw a subtle isometric grid
  for (let row = 0; row < 12; row++) {
    for (let col = 0; col < 16; col++) {
      const { x, y } = toIso(col, row);
      floor.moveTo(x, y);
      floor.lineTo(x + 32, y - 16);
      floor.lineTo(x + 64, y);
      floor.lineTo(x + 32, y + 16);
      floor.closePath();
      floor.fill((col + row) % 2 === 0 ? 0x111122 : 0x0f0f1e);
      floor.stroke({ width: 0.5, color: 0x1a1a2e });
    }
  }
  stage.addChild(floor);
}

function drawRoom(stage: Container, room: { id: string; label: string; col: number; row: number; w: number; h: number }) {
  const g = new Graphics();
  const tl = toIso(room.col, room.row);
  const tr = toIso(room.col + room.w, room.row);
  const br = toIso(room.col + room.w, room.row + room.h);
  const bl = toIso(room.col, room.row + room.h);

  // Room outline
  g.moveTo(tl.x, tl.y);
  g.lineTo(tr.x, tr.y);
  g.lineTo(br.x, br.y);
  g.lineTo(bl.x, bl.y);
  g.closePath();
  g.fill(0x12121a, 0.5);
  g.stroke({ width: 1.5, color: 0x2a2a3e });

  stage.addChild(g);

  // Room label
  const center = toIso(room.col + room.w / 2, room.row + 0.3);
  const label = new Text({
    text: room.label,
    style: new TextStyle({ fontSize: 10, fill: 0x555577, fontFamily: "monospace" }),
  });
  label.anchor.set(0.5, 0.5);
  label.x = center.x;
  label.y = center.y;
  stage.addChild(label);
}
```

### Update `company/page.tsx` — integrate PixelOffice:

Add at top of the page component, above the org chart / activity feed panels:

```typescript
import dynamic from "next/dynamic";
const PixelOffice = dynamic(() => import("@/components/company/PixelOffice"), { ssr: false });

// In the JSX, add above the grid:
<div className="h-[400px] bg-[#12121a] border border-[#2a2a3e] rounded-xl overflow-hidden">
  <PixelOffice events={events} workerStatuses={buildWorkerStatuses(status)} />
</div>

// Helper:
function buildWorkerStatuses(status: any): Record<string, string> {
  const result: Record<string, string> = {};
  if (!status?.departments) return result;
  Object.values(status.departments).forEach((dept: any) => {
    (dept.workers || []).forEach((w: any) => {
      result[w.worker_id] = w.status;
    });
  });
  return result;
}
```

---

## Task 7: Full test run + integration verification

1. `pytest tests/unit/test_ws_hub.py -v` — new WS hub tests
2. `pytest tests/unit/ -x -q` — full suite, 0 regressions
3. `cd dashboard && npm run build` — verify Next.js builds clean
4. Restart JARVIS, verify:
   - Startup logs show "ws_hub" events
   - Company events show in EventBus
   - Dashboard `/company` page loads with org chart
   - WebSocket connects and receives events
5. Send a test message via Telegram/CLI → watch company events flow in dashboard

---

## Execution Order

```
Task 1 (WebSocketHub)           — foundation, no deps
Task 2 (Company events)         — independent of Task 1
Task 3 (Wire web.py + REST)     — depends on Task 1
Task 4 (useWebSocketHub hook)   — depends on Task 1 (protocol)
Task 5 (Company page)           — depends on Task 4
Task 6 (Pixel Office)           — depends on Task 5
Task 7 (Tests + verification)   — after all
```

**Parallelizable:** Tasks 1 + 2, Tasks 4 + 5 (after Task 1+3)
