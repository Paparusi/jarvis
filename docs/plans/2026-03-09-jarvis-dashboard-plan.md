# JARVIS Dashboard v1 Implementation Plan

> **For Claude:** Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Build a full 7-page Next.js dashboard for JARVIS with trading cockpit, memory browser, skills manager, health monitor, chat, and system overview.

**Architecture:** Next.js 15 (App Router) frontend at `dashboard/` calling existing FastAPI backend at port 8000. Extend FastAPI with new REST + WebSocket endpoints. TradingView Lightweight Charts for candlestick, Recharts for stats.

**Tech Stack:** Next.js 15, React 19, Tailwind CSS, Recharts, TradingView Lightweight Charts, SWR, Lucide React, react-markdown, FastAPI (existing).

---

## Phase 1: Project Setup + Backend APIs

### Task 0: Scaffold Next.js Project

**Files:**
- Create: `dashboard/` directory (Next.js app)

**Step 1: Create Next.js app**

```bash
cd ~/projects/jarvis
npx create-next-app@latest dashboard --typescript --tailwind --eslint --app --src-dir --no-import-alias --use-npm
```

Accept defaults. This creates `dashboard/` with App Router structure.

**Step 2: Install dependencies**

```bash
cd ~/projects/jarvis/dashboard
npm install recharts lightweight-charts swr lucide-react react-markdown rehype-highlight remark-gfm
```

**Step 3: Configure API proxy**

Create `dashboard/next.config.ts`:
```typescript
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
      {
        source: "/ws/:path*",
        destination: "http://localhost:8000/ws/:path*",
      },
    ];
  },
};

export default nextConfig;
```

**Step 4: Verify it runs**

```bash
cd ~/projects/jarvis/dashboard && npm run dev
```

Visit http://localhost:3000 — should see Next.js welcome page.

**Step 5: Commit**

```bash
git add dashboard/
git commit -m "feat: scaffold Next.js dashboard project"
```

---

### Task 1: Backend API Extensions — Trading

**Files:**
- Modify: `src/gateway/channels/web.py`

Add trading-related API endpoints to FastAPI. These call existing trading brain methods.

**Step 1: Add CORS middleware**

At the top of `create_app()`, add:
```python
from fastapi.middleware.cors import CORSMiddleware

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Step 2: Add trading endpoints**

After existing endpoints in `create_app()`:

```python
@fastapi_app.get("/api/trading/status")
async def api_trading_status():
    return JSONResponse(adapter.get_trading_status())

@fastapi_app.get("/api/trading/positions")
async def api_trading_positions():
    return JSONResponse(await adapter.get_trading_positions())

@fastapi_app.get("/api/trading/history")
async def api_trading_history():
    return JSONResponse(await adapter.get_trading_history())

@fastapi_app.get("/api/trading/zones")
async def api_trading_zones():
    return JSONResponse(adapter.get_trading_zones())

@fastapi_app.get("/api/trading/pnl")
async def api_trading_pnl():
    return JSONResponse(await adapter.get_trading_pnl())

@fastapi_app.post("/api/trading/control")
async def api_trading_control(request: dict):
    action = request.get("action", "")
    return JSONResponse(await adapter.trading_control(action))
```

**Step 3: Implement WebAdapter trading methods**

Add to `WebAdapter` class:

```python
def get_trading_status(self) -> dict:
    if not self._app or not self._app.trading_brain:
        return {"status": "unavailable", "running": False}
    brain = self._app.trading_brain
    return brain.get_status()

async def get_trading_positions(self) -> dict:
    if not self._app or not self._app.trading_brain:
        return {"positions": []}
    try:
        from src.trading.mt5_client import MT5Client
        client = MT5Client()
        positions = await client.get_positions()
        return {"positions": positions}
    except Exception as e:
        return {"positions": [], "error": str(e)}

async def get_trading_history(self) -> dict:
    if not self._app or not self._app.trading_brain:
        return {"trades": []}
    try:
        from src.trading.persistence import TradingPersistence
        db = TradingPersistence()
        trades = db.get_recent_trades(limit=50)
        return {"trades": trades}
    except Exception as e:
        return {"trades": [], "error": str(e)}

def get_trading_zones(self) -> dict:
    if not self._app or not self._app.trading_brain:
        return {"zones": []}
    status = self._app.trading_brain.get_status()
    return {"zones": status.get("active_zones", [])}

async def get_trading_pnl(self) -> dict:
    if not self._app or not self._app.trading_brain:
        return {"daily": 0, "weekly": 0, "monthly": 0}
    try:
        brain = self._app.trading_brain
        status = brain.get_status()
        rg = status.get("risk_guard", {})
        return {
            "daily_pnl": rg.get("daily_pnl", 0),
            "daily_pnl_pct": rg.get("daily_pnl_pct", 0),
            "weekly_pnl": rg.get("weekly_pnl", 0),
            "weekly_pnl_pct": rg.get("weekly_pnl_pct", 0),
            "daily_trades": rg.get("daily_trades", 0),
            "consecutive_losses": rg.get("consecutive_losses", 0),
        }
    except Exception as e:
        return {"error": str(e)}

async def trading_control(self, action: str) -> dict:
    if not self._app or not self._app.trading_brain:
        return {"error": "Trading brain not available"}
    brain = self._app.trading_brain
    if action == "start":
        await brain.start()
        return {"status": "started"}
    elif action == "stop":
        await brain.stop()
        return {"status": "stopped"}
    elif action == "plan":
        result = await brain.plan_now()
        return {"status": "planned", "result": result}
    return {"error": f"Unknown action: {action}"}
```

**Step 4: Run existing tests**

```bash
python -m pytest tests/unit/ -x -q 2>&1 | tail -5
```
Expected: All pass (no breaking changes — only additions)

**Step 5: Commit**

```bash
git add src/gateway/channels/web.py
git commit -m "feat: add trading REST API endpoints to FastAPI"
```

---

### Task 2: Backend API Extensions — Memory, Activity, System

**Files:**
- Modify: `src/gateway/channels/web.py`

**Step 1: Add memory/system endpoints to `create_app()`**

```python
from fastapi import Query, Body

@fastapi_app.get("/api/memory/search")
async def api_memory_search(q: str = Query("")):
    return JSONResponse(await adapter.search_memories(q))

@fastapi_app.post("/api/memory")
async def api_memory_add(data: dict):
    return JSONResponse(await adapter.add_memory(data))

@fastapi_app.delete("/api/memory/{memory_id}")
async def api_memory_delete(memory_id: str):
    return JSONResponse(adapter.delete_memory(memory_id))

@fastapi_app.get("/api/activity")
async def api_activity():
    return JSONResponse(adapter.get_activity())

@fastapi_app.get("/api/system/metrics")
async def api_system_metrics():
    return JSONResponse(adapter.get_system_metrics())

@fastapi_app.get("/api/dreamtime/status")
async def api_dreamtime_status():
    return JSONResponse(adapter.get_dreamtime_status())
```

**Step 2: Implement WebAdapter methods**

```python
async def search_memories(self, query: str) -> dict:
    if not query:
        return self.get_memories()
    try:
        results = await self._memory.semantic.search(query, limit=20)
        return {
            "memories": [
                {
                    "content": r["content"],
                    "category": r.get("category", ""),
                    "importance": r.get("importance", 0),
                    "score": r.get("score", 0),
                }
                for r in results
            ]
        }
    except Exception as e:
        return {"memories": [], "error": str(e)}

async def add_memory(self, data: dict) -> dict:
    content = data.get("content", "")
    category = data.get("category", "user_stated")
    if not content:
        return {"error": "Content required"}
    await self._memory.remember_fact(content, category=category)
    return {"status": "saved"}

def delete_memory(self, memory_id: str) -> dict:
    try:
        self._memory.semantic.delete(memory_id)
        return {"status": "deleted"}
    except Exception as e:
        return {"error": str(e)}

def get_activity(self) -> dict:
    try:
        stats = self._collector.get_stats()
        return {
            "total_records": stats.get("total_records", 0),
            "recent": stats.get("recent_records", [])[:10],
        }
    except Exception:
        return {"total_records": 0, "recent": []}

def get_system_metrics(self) -> dict:
    import psutil
    return {
        "cpu_percent": psutil.cpu_percent(),
        "ram_percent": psutil.virtual_memory().percent,
        "ram_used_gb": round(psutil.virtual_memory().used / 1e9, 1),
        "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
        "disk_percent": psutil.disk_usage("/").percent,
        "disk_used_gb": round(psutil.disk_usage("/").used / 1e9, 1),
    }

def get_dreamtime_status(self) -> dict:
    if not self._app or not self._app.dreamtime:
        return {"status": "unavailable"}
    dt = self._app.dreamtime
    return {
        "enabled": dt._enabled if hasattr(dt, '_enabled') else False,
        "last_run": str(dt._last_run) if hasattr(dt, '_last_run') else None,
        "idle_minutes": dt._idle_minutes if hasattr(dt, '_idle_minutes') else 30,
    }
```

**Step 3: Install psutil if needed**

```bash
pip install psutil
```

**Step 4: Test**

```bash
python -m pytest tests/unit/ -x -q 2>&1 | tail -5
```

**Step 5: Commit**

```bash
git add src/gateway/channels/web.py
git commit -m "feat: add memory, activity, system metrics API endpoints"
```

---

## Phase 2: Dashboard Layout + Overview Page

### Task 3: Dashboard Layout (Sidebar + Shell)

**Files:**
- Create: `dashboard/src/app/layout.tsx` (replace default)
- Create: `dashboard/src/components/Sidebar.tsx`
- Create: `dashboard/src/components/StatusBar.tsx`
- Modify: `dashboard/src/app/globals.css`

**Step 1: Update globals.css for dark theme**

Replace `dashboard/src/app/globals.css`:
```css
@import "tailwindcss";

:root {
  --background: #0a0a0f;
  --foreground: #e0e0e8;
}

body {
  background: var(--background);
  color: var(--foreground);
}
```

**Step 2: Create Sidebar component**

Create `dashboard/src/components/Sidebar.tsx`:
```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, MessageSquare, TrendingUp,
  Brain, Target, HeartPulse, Settings,
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/trading", label: "Trading", icon: TrendingUp },
  { href: "/memory", label: "Memory", icon: Brain },
  { href: "/skills", label: "Skills", icon: Target },
  { href: "/health", label: "Health", icon: HeartPulse },
  { href: "/settings", label: "Settings", icon: Settings },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-16 lg:w-56 bg-[#12121a] border-r border-[#2a2a3e] flex flex-col py-4 shrink-0">
      <div className="px-4 mb-8 hidden lg:block">
        <h1 className="text-xl font-bold bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
          JARVIS
        </h1>
      </div>
      <nav className="flex-1 space-y-1">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                active
                  ? "bg-blue-500/10 text-blue-400 border-r-2 border-blue-400"
                  : "text-gray-400 hover:text-gray-200 hover:bg-white/5"
              }`}
            >
              <Icon size={18} />
              <span className="hidden lg:inline">{label}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
```

**Step 3: Create StatusBar component**

Create `dashboard/src/components/StatusBar.tsx`:
```tsx
"use client";

import useSWR from "swr";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function StatusBar() {
  const { data } = useSWR("/api/status", fetcher, { refreshInterval: 30000 });

  return (
    <footer className="h-8 bg-[#12121a] border-t border-[#2a2a3e] px-4 flex items-center text-xs text-gray-500 gap-4">
      <span className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full ${data ? "bg-green-400" : "bg-red-400"}`} />
        {data ? "Connected" : "Disconnected"}
      </span>
      {data && (
        <>
          <span>{data.cloud_model}</span>
          <span>{data.tools} tools</span>
          <span>{data.skills} skills</span>
          <span>Cache: {data.cache_entries}</span>
        </>
      )}
    </footer>
  );
}
```

**Step 4: Update root layout**

Replace `dashboard/src/app/layout.tsx`:
```tsx
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import StatusBar from "@/components/StatusBar";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "JARVIS Dashboard",
  description: "JARVIS AI Assistant Dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" className="dark">
      <body className={`${inter.className} h-screen flex flex-col overflow-hidden`}>
        <div className="flex flex-1 overflow-hidden">
          <Sidebar />
          <main className="flex-1 overflow-y-auto p-6">{children}</main>
        </div>
        <StatusBar />
      </body>
    </html>
  );
}
```

**Step 5: Verify**

```bash
cd ~/projects/jarvis/dashboard && npm run dev
```

Visit http://localhost:3000 — should see dark sidebar + empty main area + status bar.

**Step 6: Commit**

```bash
git add dashboard/src/
git commit -m "feat: dashboard layout with sidebar navigation and status bar"
```

---

### Task 4: Overview Page (Home Dashboard)

**Files:**
- Create: `dashboard/src/app/page.tsx`
- Create: `dashboard/src/components/StatCard.tsx`
- Create: `dashboard/src/lib/api.ts`

**Step 1: Create API helper**

Create `dashboard/src/lib/api.ts`:
```typescript
export const API_BASE = "";  // Uses Next.js proxy

export const fetcher = (url: string) => fetch(url).then((r) => r.json());
```

**Step 2: Create StatCard component**

Create `dashboard/src/components/StatCard.tsx`:
```tsx
import { LucideIcon } from "lucide-react";

interface Props {
  title: string;
  value: string | number;
  icon: LucideIcon;
  subtitle?: string;
  color?: string;
}

export default function StatCard({ title, value, icon: Icon, subtitle, color = "blue" }: Props) {
  const colors: Record<string, string> = {
    blue: "from-blue-500/20 to-blue-600/5 text-blue-400",
    green: "from-green-500/20 to-green-600/5 text-green-400",
    purple: "from-purple-500/20 to-purple-600/5 text-purple-400",
    yellow: "from-yellow-500/20 to-yellow-600/5 text-yellow-400",
    red: "from-red-500/20 to-red-600/5 text-red-400",
  };

  return (
    <div className={`bg-gradient-to-br ${colors[color]} rounded-xl p-4 border border-white/5`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-gray-400">{title}</span>
        <Icon size={18} className="opacity-50" />
      </div>
      <div className="text-2xl font-bold">{value}</div>
      {subtitle && <div className="text-xs text-gray-500 mt-1">{subtitle}</div>}
    </div>
  );
}
```

**Step 3: Create Overview page**

Replace `dashboard/src/app/page.tsx`:
```tsx
"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import StatCard from "@/components/StatCard";
import {
  Wrench, Target, Database, Zap,
  DollarSign, TrendingUp, Brain, Activity,
} from "lucide-react";

export default function OverviewPage() {
  const { data: status } = useSWR("/api/status", fetcher, { refreshInterval: 10000 });
  const { data: health } = useSWR("/api/health", fetcher, { refreshInterval: 30000 });
  const { data: trading } = useSWR("/api/trading/pnl", fetcher, { refreshInterval: 10000 });
  const { data: metrics } = useSWR("/api/system/metrics", fetcher, { refreshInterval: 5000 });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      {/* Status Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Tools"
          value={status?.tools ?? "—"}
          icon={Wrench}
          color="blue"
        />
        <StatCard
          title="Skills"
          value={status?.skills ?? "—"}
          icon={Target}
          color="purple"
        />
        <StatCard
          title="Cache Hits"
          value={status?.cache_entries ?? "—"}
          icon={Zap}
          color="yellow"
        />
        <StatCard
          title="API Cost"
          value={status?.total_cost ?? "—"}
          icon={DollarSign}
          color="green"
        />
      </div>

      {/* Trading + System Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Trading PnL */}
        <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <TrendingUp size={18} /> Trading
          </h2>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-xs text-gray-500">Daily PnL</div>
              <div className={`text-xl font-bold ${(trading?.daily_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                ${(trading?.daily_pnl ?? 0).toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">Weekly PnL</div>
              <div className={`text-xl font-bold ${(trading?.weekly_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                ${(trading?.weekly_pnl ?? 0).toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">Trades Today</div>
              <div className="text-xl font-bold">{trading?.daily_trades ?? 0}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">Loss Streak</div>
              <div className="text-xl font-bold">{trading?.consecutive_losses ?? 0}</div>
            </div>
          </div>
        </div>

        {/* System Metrics */}
        <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Activity size={18} /> System
          </h2>
          <div className="space-y-3">
            {[
              { label: "CPU", value: metrics?.cpu_percent ?? 0 },
              { label: "RAM", value: metrics?.ram_percent ?? 0, detail: `${metrics?.ram_used_gb ?? 0}/${metrics?.ram_total_gb ?? 0} GB` },
              { label: "Disk", value: metrics?.disk_percent ?? 0 },
            ].map(({ label, value, detail }) => (
              <div key={label}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-gray-400">{label}</span>
                  <span>{value}%{detail ? ` (${detail})` : ""}</span>
                </div>
                <div className="h-2 bg-[#1a1a2e] rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      value > 80 ? "bg-red-400" : value > 60 ? "bg-yellow-400" : "bg-green-400"
                    }`}
                    style={{ width: `${value}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Health Checks */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Brain size={18} /> Health
        </h2>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {(health?.checks ?? []).map((c: any) => (
            <div key={c.name} className="flex items-center gap-2 text-sm">
              <span className={`w-2 h-2 rounded-full ${
                c.status === "ok" || c.status === "pass" ? "bg-green-400" : "bg-red-400"
              }`} />
              <span className="text-gray-400">{c.name}</span>
              <span className="text-gray-600 text-xs ml-auto">{c.latency_ms}ms</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

**Step 4: Verify**

```bash
cd ~/projects/jarvis/dashboard && npm run dev
```

Visit http://localhost:3000 — should see full overview dashboard with stats, trading PnL, system metrics, health checks.

**Step 5: Commit**

```bash
git add dashboard/src/
git commit -m "feat: overview dashboard page with stats, trading, system metrics"
```

---

## Phase 3: Chat + Trading Pages

### Task 5: Chat Page

**Files:**
- Create: `dashboard/src/app/chat/page.tsx`
- Create: `dashboard/src/components/ChatMessage.tsx`
- Create: `dashboard/src/hooks/useWebSocket.ts`

**Step 1: Create WebSocket hook**

Create `dashboard/src/hooks/useWebSocket.ts`:
```typescript
"use client";

import { useEffect, useRef, useState, useCallback } from "react";

interface WSMessage {
  type: string;
  text?: string;
  model?: string;
  latency_ms?: number;
  tools_used?: any[];
  [key: string]: any;
}

export function useWebSocket(url: string) {
  const ws = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<WSMessage[]>([]);

  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}${url}`;
    const socket = new WebSocket(wsUrl);

    socket.onopen = () => setConnected(true);
    socket.onclose = () => {
      setConnected(false);
      // Auto-reconnect after 3s
      setTimeout(() => {
        ws.current = new WebSocket(wsUrl);
      }, 3000);
    };
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setMessages((prev) => [...prev, data]);
    };

    ws.current = socket;
    return () => socket.close();
  }, [url]);

  const send = useCallback((data: any) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(data));
    }
  }, []);

  return { connected, messages, send, setMessages };
}
```

**Step 2: Create ChatMessage component**

Create `dashboard/src/components/ChatMessage.tsx`:
```tsx
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

interface Props {
  role: "user" | "assistant";
  content: string;
  model?: string;
  latency_ms?: number;
  tools_used?: string[];
}

export default function ChatMessage({ role, content, model, latency_ms, tools_used }: Props) {
  return (
    <div className={`flex gap-3 ${role === "user" ? "flex-row-reverse" : ""}`}>
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm shrink-0 ${
        role === "assistant"
          ? "bg-gradient-to-br from-blue-500 to-purple-500"
          : "bg-[#1a1a2e]"
      }`}>
        {role === "assistant" ? "J" : "B"}
      </div>
      <div className={`max-w-[80%] rounded-xl px-4 py-3 text-sm leading-relaxed ${
        role === "user"
          ? "bg-blue-600 text-white"
          : "bg-[#12121a] border border-[#2a2a3e]"
      }`}>
        {role === "assistant" ? (
          <ReactMarkdown rehypePlugins={[rehypeHighlight]} remarkPlugins={[remarkGfm]}>
            {content}
          </ReactMarkdown>
        ) : (
          <p>{content}</p>
        )}
        {role === "assistant" && (model || tools_used?.length) && (
          <div className="flex items-center gap-2 mt-2 text-xs text-gray-500">
            {model && <span>{model}</span>}
            {latency_ms && <span>{latency_ms}ms</span>}
            {tools_used?.map((t: any) => (
              <span key={typeof t === "string" ? t : t.name} className="px-1.5 py-0.5 bg-blue-500/10 text-blue-400 rounded text-[10px]">
                {typeof t === "string" ? t : t.name}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

**Step 3: Create Chat page**

Create `dashboard/src/app/chat/page.tsx`:
```tsx
"use client";

import { useState, useRef, useEffect } from "react";
import { Send } from "lucide-react";
import { useWebSocket } from "@/hooks/useWebSocket";
import ChatMessage from "@/components/ChatMessage";

interface Message {
  role: "user" | "assistant";
  content: string;
  model?: string;
  latency_ms?: number;
  tools_used?: any[];
}

export default function ChatPage() {
  const [input, setInput] = useState("");
  const [chatMessages, setChatMessages] = useState<Message[]>([]);
  const [thinking, setThinking] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { connected, messages, send } = useWebSocket("/ws/chat");

  // Process incoming WebSocket messages
  useEffect(() => {
    if (messages.length === 0) return;
    const last = messages[messages.length - 1];

    if (last.type === "thinking") {
      setThinking(true);
    } else if (last.type === "response") {
      setThinking(false);
      setChatMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: last.text || "",
          model: last.model,
          latency_ms: last.latency_ms,
          tools_used: last.tools_used,
        },
      ]);
    } else if (last.type === "error") {
      setThinking(false);
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${last.text}` },
      ]);
    }
  }, [messages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, thinking]);

  const handleSend = () => {
    if (!input.trim() || !connected) return;
    setChatMessages((prev) => [...prev, { role: "user", content: input }]);
    send({ type: "message", text: input });
    setInput("");
  };

  return (
    <div className="flex flex-col h-full -m-6">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {chatMessages.map((msg, i) => (
          <ChatMessage key={i} {...msg} />
        ))}
        {thinking && (
          <div className="flex items-center gap-2 text-gray-500 text-sm">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-500 flex items-center justify-center text-sm">J</div>
            <span className="animate-pulse">Thinking...</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-[#2a2a3e] p-4">
        <div className="flex gap-2 max-w-3xl mx-auto">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder={connected ? "Nhập tin nhắn..." : "Đang kết nối..."}
            disabled={!connected}
            className="flex-1 bg-[#1a1a2e] border border-[#2a2a3e] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-blue-500 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={!connected || !input.trim()}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg px-4 py-2.5 transition-colors"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
```

**Step 4: Verify**

Visit http://localhost:3000/chat — should see chat interface. Type a message (requires FastAPI running on 8000).

**Step 5: Commit**

```bash
git add dashboard/src/
git commit -m "feat: chat page with WebSocket streaming and markdown rendering"
```

---

### Task 6: Trading Page

**Files:**
- Create: `dashboard/src/app/trading/page.tsx`
- Create: `dashboard/src/components/TradingChart.tsx`
- Create: `dashboard/src/components/PositionsTable.tsx`

**Step 1: Create TradingChart component**

Create `dashboard/src/components/TradingChart.tsx`:
```tsx
"use client";

import { useEffect, useRef } from "react";
import { createChart, IChartApi, CandlestickData, Time } from "lightweight-charts";

interface Props {
  data: CandlestickData<Time>[];
  zones?: { price_high: number; price_low: number; direction: string; score: number }[];
}

export default function TradingChart({ data, zones = [] }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#12121a" },
        textColor: "#8888a0",
      },
      grid: {
        vertLines: { color: "#1a1a2e" },
        horzLines: { color: "#1a1a2e" },
      },
      width: containerRef.current.clientWidth,
      height: 400,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#4ade80",
      downColor: "#f87171",
      borderUpColor: "#4ade80",
      borderDownColor: "#f87171",
      wickUpColor: "#4ade80",
      wickDownColor: "#f87171",
    });

    if (data.length > 0) {
      candleSeries.setData(data);
    }

    chartRef.current = chart;

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [data]);

  return <div ref={containerRef} className="w-full rounded-lg overflow-hidden" />;
}
```

**Step 2: Create PositionsTable component**

Create `dashboard/src/components/PositionsTable.tsx`:
```tsx
interface Position {
  ticket: number;
  type: string;
  volume: number;
  symbol: string;
  price_open: number;
  sl: number;
  tp: number;
  profit: number;
}

interface Props {
  positions: Position[];
}

export default function PositionsTable({ positions }: Props) {
  if (positions.length === 0) {
    return <p className="text-gray-500 text-sm">No open positions</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-500 border-b border-[#2a2a3e]">
            <th className="text-left py-2 px-3">Ticket</th>
            <th className="text-left py-2 px-3">Type</th>
            <th className="text-right py-2 px-3">Volume</th>
            <th className="text-right py-2 px-3">Entry</th>
            <th className="text-right py-2 px-3">SL</th>
            <th className="text-right py-2 px-3">TP</th>
            <th className="text-right py-2 px-3">P&L</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.ticket} className="border-b border-[#1a1a2e] hover:bg-white/5">
              <td className="py-2 px-3 text-gray-400">{p.ticket}</td>
              <td className={`py-2 px-3 font-medium ${p.type === "buy" ? "text-green-400" : "text-red-400"}`}>
                {p.type.toUpperCase()}
              </td>
              <td className="py-2 px-3 text-right">{p.volume}</td>
              <td className="py-2 px-3 text-right">{p.price_open}</td>
              <td className="py-2 px-3 text-right text-red-400">{p.sl || "—"}</td>
              <td className="py-2 px-3 text-right text-green-400">{p.tp || "—"}</td>
              <td className={`py-2 px-3 text-right font-medium ${p.profit >= 0 ? "text-green-400" : "text-red-400"}`}>
                ${p.profit.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

**Step 3: Create Trading page**

Create `dashboard/src/app/trading/page.tsx`:
```tsx
"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { Play, Square, RefreshCw } from "lucide-react";
import TradingChart from "@/components/TradingChart";
import PositionsTable from "@/components/PositionsTable";

export default function TradingPage() {
  const { data: status } = useSWR("/api/trading/status", fetcher, { refreshInterval: 5000 });
  const { data: positions } = useSWR("/api/trading/positions", fetcher, { refreshInterval: 5000 });
  const { data: pnl } = useSWR("/api/trading/pnl", fetcher, { refreshInterval: 10000 });
  const { data: zones } = useSWR("/api/trading/zones", fetcher, { refreshInterval: 30000 });

  const handleControl = async (action: string) => {
    await fetch("/api/trading/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  };

  const running = status?.running ?? false;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Trading</h1>
        <div className="flex gap-2">
          {!running ? (
            <button onClick={() => handleControl("start")}
              className="flex items-center gap-2 bg-green-600 hover:bg-green-700 px-4 py-2 rounded-lg text-sm">
              <Play size={14} /> Start Brain
            </button>
          ) : (
            <button onClick={() => handleControl("stop")}
              className="flex items-center gap-2 bg-red-600 hover:bg-red-700 px-4 py-2 rounded-lg text-sm">
              <Square size={14} /> Stop Brain
            </button>
          )}
          <button onClick={() => handleControl("plan")}
            className="flex items-center gap-2 bg-[#1a1a2e] border border-[#2a2a3e] hover:bg-[#2a2a3e] px-4 py-2 rounded-lg text-sm">
            <RefreshCw size={14} /> New Plan
          </button>
        </div>
      </div>

      {/* Brain Status */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4">
        <div className="flex items-center gap-3 mb-3">
          <span className={`w-2.5 h-2.5 rounded-full ${running ? "bg-green-400 animate-pulse" : "bg-gray-600"}`} />
          <span className="font-medium">{running ? "Brain Active" : "Brain Stopped"}</span>
          {status?.session && <span className="text-gray-500 text-sm">Session: {status.session}</span>}
        </div>
        {status?.current_plan && (
          <div className="text-sm text-gray-400">
            <span className="text-gray-500">Bias:</span> {status.current_plan.bias} |
            <span className="text-gray-500 ml-2">Zones:</span> {status.current_plan.zones_count ?? 0} |
            <span className="text-gray-500 ml-2">Trades:</span> {status.current_plan.trades_taken ?? 0}/{status.current_plan.max_trades ?? 3}
          </div>
        )}
      </div>

      {/* PnL Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: "Daily PnL", value: pnl?.daily_pnl ?? 0, pct: pnl?.daily_pnl_pct ?? 0 },
          { label: "Weekly PnL", value: pnl?.weekly_pnl ?? 0, pct: pnl?.weekly_pnl_pct ?? 0 },
          { label: "Trades Today", value: pnl?.daily_trades ?? 0 },
          { label: "Loss Streak", value: pnl?.consecutive_losses ?? 0 },
        ].map(({ label, value, pct }) => (
          <div key={label} className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4">
            <div className="text-xs text-gray-500">{label}</div>
            <div className={`text-xl font-bold ${
              typeof pct === "number" ? (value >= 0 ? "text-green-400" : "text-red-400") : ""
            }`}>
              {typeof pct === "number" ? `$${Number(value).toFixed(2)}` : value}
            </div>
            {typeof pct === "number" && (
              <div className={`text-xs ${value >= 0 ? "text-green-600" : "text-red-600"}`}>
                {value >= 0 ? "+" : ""}{Number(pct).toFixed(2)}%
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Chart placeholder */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4">
        <h2 className="text-lg font-semibold mb-3">XAUUSD Chart</h2>
        <TradingChart data={[]} zones={zones?.zones ?? []} />
      </div>

      {/* Positions */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-3">Open Positions</h2>
        <PositionsTable positions={positions?.positions ?? []} />
      </div>
    </div>
  );
}
```

**Step 4: Verify**

Visit http://localhost:3000/trading — should see trading cockpit.

**Step 5: Commit**

```bash
git add dashboard/src/
git commit -m "feat: trading page with chart, positions, PnL, brain controls"
```

---

## Phase 4: Memory, Skills, Health, Settings Pages

### Task 7: Memory Browser Page

**Files:**
- Create: `dashboard/src/app/memory/page.tsx`

**Step 1: Create Memory page**

Create `dashboard/src/app/memory/page.tsx`:
```tsx
"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { fetcher } from "@/lib/api";
import { Search, Plus, Trash2 } from "lucide-react";

export default function MemoryPage() {
  const [query, setQuery] = useState("");
  const [newMemory, setNewMemory] = useState("");
  const url = query ? `/api/memory/search?q=${encodeURIComponent(query)}` : "/api/memory";
  const { data } = useSWR(url, fetcher);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    mutate(url);
  };

  const handleAdd = async () => {
    if (!newMemory.trim()) return;
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: newMemory }),
    });
    setNewMemory("");
    mutate(url);
  };

  const handleDelete = async (id: string) => {
    await fetch(`/api/memory/${id}`, { method: "DELETE" });
    mutate(url);
  };

  const memories = data?.memories ?? [];

  const categoryColors: Record<string, string> = {
    user_stated: "bg-blue-500/20 text-blue-400",
    preference: "bg-purple-500/20 text-purple-400",
    identity: "bg-green-500/20 text-green-400",
    user_saved: "bg-yellow-500/20 text-yellow-400",
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Memory</h1>

      {/* Search */}
      <form onSubmit={handleSearch} className="flex gap-2">
        <div className="relative flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Tìm kiếm trong bộ nhớ..."
            className="w-full bg-[#1a1a2e] border border-[#2a2a3e] rounded-lg pl-10 pr-4 py-2.5 text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
      </form>

      {/* Add memory */}
      <div className="flex gap-2">
        <input
          type="text"
          value={newMemory}
          onChange={(e) => setNewMemory(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="Thêm memory mới..."
          className="flex-1 bg-[#1a1a2e] border border-[#2a2a3e] rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-blue-500"
        />
        <button onClick={handleAdd} className="bg-blue-600 hover:bg-blue-700 rounded-lg px-4 py-2.5">
          <Plus size={16} />
        </button>
      </div>

      {/* Memory list */}
      <div className="space-y-2">
        {memories.length === 0 && (
          <p className="text-gray-500 text-sm">No memories found</p>
        )}
        {memories.map((m: any, i: number) => (
          <div key={i} className="bg-[#12121a] border border-[#2a2a3e] rounded-lg p-4 flex items-start gap-3 group">
            <div className="flex-1">
              <p className="text-sm">{m.content}</p>
              <div className="flex items-center gap-2 mt-2">
                <span className={`text-[10px] px-2 py-0.5 rounded-full ${categoryColors[m.category] ?? "bg-gray-500/20 text-gray-400"}`}>
                  {m.category}
                </span>
                {m.importance > 0 && (
                  <span className="text-[10px] text-gray-600">importance: {m.importance}</span>
                )}
                {m.score > 0 && (
                  <span className="text-[10px] text-gray-600">score: {m.score.toFixed(2)}</span>
                )}
              </div>
            </div>
            <button
              onClick={() => handleDelete(m.id)}
              className="opacity-0 group-hover:opacity-100 text-gray-600 hover:text-red-400 transition-all"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>

      {/* Stats */}
      <div className="text-xs text-gray-600">
        Total: {memories.length} memories
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add dashboard/src/app/memory/
git commit -m "feat: memory browser page with search, add, delete"
```

---

### Task 8: Skills Page

**Files:**
- Create: `dashboard/src/app/skills/page.tsx`

**Step 1: Create Skills page**

Create `dashboard/src/app/skills/page.tsx`:
```tsx
"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";

export default function SkillsPage() {
  const { data } = useSWR("/api/skills", fetcher);
  const skills = data?.skills ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Skills</h1>
        <span className="text-sm text-gray-500">{skills.length} skills loaded</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {skills.map((s: any) => (
          <div key={s.name} className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4 hover:border-blue-500/30 transition-colors">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-lg">{s.emoji || "🎯"}</span>
              <h3 className="font-semibold text-sm">{s.name}</h3>
              <span className="text-[10px] text-gray-600 ml-auto">v{s.version}</span>
            </div>
            <p className="text-xs text-gray-400 mb-3 line-clamp-2">{s.description}</p>
            <div className="flex items-center gap-3 text-[11px] text-gray-500">
              <span className={`px-1.5 py-0.5 rounded ${
                s.category === "core" ? "bg-blue-500/10 text-blue-400" :
                s.category === "security" ? "bg-red-500/10 text-red-400" :
                s.category === "auto" ? "bg-green-500/10 text-green-400" :
                "bg-gray-500/10 text-gray-400"
              }`}>{s.category}</span>
              <span>Priority: {s.priority}</span>
              <span>Uses: {s.usage_count}</span>
              <span className={s.success_rate >= 0.8 ? "text-green-400" : s.success_rate >= 0.5 ? "text-yellow-400" : "text-red-400"}>
                {(s.success_rate * 100).toFixed(0)}%
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add dashboard/src/app/skills/
git commit -m "feat: skills manager page with grid view"
```

---

### Task 9: Health Monitor Page

**Files:**
- Create: `dashboard/src/app/health/page.tsx`

**Step 1: Create Health page**

Create `dashboard/src/app/health/page.tsx`:
```tsx
"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { RefreshCw } from "lucide-react";

export default function HealthPage() {
  const { data: health, mutate: refreshHealth } = useSWR("/api/health", fetcher);
  const { data: metrics } = useSWR("/api/system/metrics", fetcher, { refreshInterval: 5000 });
  const { data: dreamtime } = useSWR("/api/dreamtime/status", fetcher, { refreshInterval: 30000 });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Health Monitor</h1>
        <button onClick={() => refreshHealth()} className="flex items-center gap-2 bg-[#1a1a2e] border border-[#2a2a3e] px-4 py-2 rounded-lg text-sm hover:bg-[#2a2a3e]">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {/* Health Checks */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4">Health Checks</h2>
        <div className="space-y-3">
          {(health?.checks ?? []).map((c: any) => (
            <div key={c.name} className="flex items-center justify-between py-2 border-b border-[#1a1a2e] last:border-0">
              <div className="flex items-center gap-3">
                <span className={`w-3 h-3 rounded-full ${
                  c.status === "ok" || c.status === "pass" ? "bg-green-400" : "bg-red-400"
                }`} />
                <span className="font-medium text-sm">{c.name}</span>
              </div>
              <div className="flex items-center gap-4 text-sm">
                <span className="text-gray-400">{c.message}</span>
                <span className="text-gray-600 text-xs">{c.latency_ms}ms</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* System Metrics */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4">System Resources</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            { label: "CPU", value: metrics?.cpu_percent ?? 0 },
            { label: "RAM", value: metrics?.ram_percent ?? 0, detail: `${metrics?.ram_used_gb ?? 0}/${metrics?.ram_total_gb ?? 0} GB` },
            { label: "Disk", value: metrics?.disk_percent ?? 0, detail: `${metrics?.disk_used_gb ?? 0} GB used` },
          ].map(({ label, value, detail }) => (
            <div key={label} className="text-center">
              <div className="relative w-24 h-24 mx-auto mb-2">
                <svg className="w-24 h-24 -rotate-90" viewBox="0 0 100 100">
                  <circle cx="50" cy="50" r="40" fill="none" stroke="#1a1a2e" strokeWidth="8" />
                  <circle cx="50" cy="50" r="40" fill="none"
                    stroke={value > 80 ? "#f87171" : value > 60 ? "#fbbf24" : "#4ade80"}
                    strokeWidth="8" strokeLinecap="round"
                    strokeDasharray={`${value * 2.51} 251`} />
                </svg>
                <div className="absolute inset-0 flex items-center justify-center text-lg font-bold">
                  {value}%
                </div>
              </div>
              <div className="text-sm font-medium">{label}</div>
              {detail && <div className="text-xs text-gray-500">{detail}</div>}
            </div>
          ))}
        </div>
      </div>

      {/* Dreamtime */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4">Dreamtime</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-gray-500">Status:</span>
            <span className="ml-2">{dreamtime?.enabled ? "Enabled" : "Disabled"}</span>
          </div>
          <div>
            <span className="text-gray-500">Idle trigger:</span>
            <span className="ml-2">{dreamtime?.idle_minutes ?? 30} min</span>
          </div>
          <div>
            <span className="text-gray-500">Last run:</span>
            <span className="ml-2">{dreamtime?.last_run ?? "Never"}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add dashboard/src/app/health/
git commit -m "feat: health monitor page with checks, system resources, dreamtime"
```

---

### Task 10: Settings Page

**Files:**
- Create: `dashboard/src/app/settings/page.tsx`

**Step 1: Create Settings page**

Create `dashboard/src/app/settings/page.tsx`:
```tsx
"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { Shield, Bell, Key } from "lucide-react";

export default function SettingsPage() {
  const { data: status } = useSWR("/api/status", fetcher);
  const { data: tradingStatus } = useSWR("/api/trading/status", fetcher);

  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="text-2xl font-bold">Settings</h1>

      {/* System Info */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Key size={18} /> System
        </h2>
        <div className="space-y-3 text-sm">
          <div className="flex justify-between py-2 border-b border-[#1a1a2e]">
            <span className="text-gray-400">Version</span>
            <span>{status?.version ?? "—"}</span>
          </div>
          <div className="flex justify-between py-2 border-b border-[#1a1a2e]">
            <span className="text-gray-400">LLM Model</span>
            <span>{status?.cloud_model ?? "—"}</span>
          </div>
          <div className="flex justify-between py-2 border-b border-[#1a1a2e]">
            <span className="text-gray-400">Tools</span>
            <span>{status?.tools ?? "—"}</span>
          </div>
          <div className="flex justify-between py-2">
            <span className="text-gray-400">Skills</span>
            <span>{status?.skills ?? "—"}</span>
          </div>
        </div>
      </div>

      {/* Trading Config */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Shield size={18} /> Risk Guard
        </h2>
        <div className="space-y-3 text-sm">
          {[
            { label: "Max Risk Per Trade", value: "1%" },
            { label: "Max Lot Size", value: "0.1" },
            { label: "Min R:R Ratio", value: "1.5" },
            { label: "Max Daily Loss", value: "3%" },
            { label: "Max Weekly Loss", value: "8%" },
            { label: "Max Open Positions", value: "3" },
            { label: "Max Daily Trades", value: "5" },
          ].map(({ label, value }) => (
            <div key={label} className="flex justify-between py-2 border-b border-[#1a1a2e] last:border-0">
              <span className="text-gray-400">{label}</span>
              <span className="font-mono">{value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Notifications */}
      <div className="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Bell size={18} /> Notifications
        </h2>
        <div className="space-y-3 text-sm">
          <div className="flex justify-between items-center py-2">
            <span className="text-gray-400">Telegram Alerts</span>
            <span className="text-green-400">Active</span>
          </div>
          <div className="flex justify-between items-center py-2">
            <span className="text-gray-400">Owner ID</span>
            <span className="font-mono text-gray-500">1991690969</span>
          </div>
        </div>
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add dashboard/src/app/settings/
git commit -m "feat: settings page with system info, risk guard config, notifications"
```

---

## Phase 5: Polish + Integration

### Task 11: Enable FastAPI to serve alongside Next.js

**Files:**
- Modify: `src/gateway/channels/web.py` — ensure CORS is set
- Modify: `src/main.py` — ensure web adapter starts

**Step 1: Verify web adapter starts in main.py**

Check that `src/main.py` starts the FastAPI server. If not, add:

```python
# In main(), after telegram starts:
import uvicorn
from src.gateway.channels.web import create_app

web_app = create_app(jarvis_app)
config = uvicorn.Config(web_app, host="0.0.0.0", port=8000, log_level="warning")
server = uvicorn.Server(config)
asyncio.create_task(server.serve())
```

**Step 2: Test full stack**

Terminal 1:
```bash
cd ~/projects/jarvis && python -m src.main
```

Terminal 2:
```bash
cd ~/projects/jarvis/dashboard && npm run dev
```

Visit http://localhost:3000 — all pages should work.

**Step 3: Commit**

```bash
git add src/main.py src/gateway/channels/web.py
git commit -m "feat: enable FastAPI + Next.js full stack integration"
```

---

### Task 12: Final Verification

**Step 1: Run Python tests**

```bash
python -m pytest tests/unit/ -x -q
```
Expected: All pass

**Step 2: Run Next.js build**

```bash
cd ~/projects/jarvis/dashboard && npm run build
```
Expected: Build succeeds

**Step 3: Test all pages**

1. http://localhost:3000 — Overview with stats, PnL, system metrics
2. http://localhost:3000/chat — Chat with WebSocket
3. http://localhost:3000/trading — Trading cockpit
4. http://localhost:3000/memory — Memory browser with search
5. http://localhost:3000/skills — Skills grid
6. http://localhost:3000/health — Health checks + system resources
7. http://localhost:3000/settings — Settings display

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: JARVIS Dashboard v1 complete — 7-page Next.js dashboard"
```

---

## Execution Order

```
Task 0  (Next.js scaffold)     — no deps
Task 1  (Trading APIs)         — no deps
Task 2  (Memory/System APIs)   — no deps
Task 3  (Layout + Sidebar)     — after T0
Task 4  (Overview page)        — after T3
Task 5  (Chat page)            — after T3
Task 6  (Trading page)         — after T1, T3
Task 7  (Memory page)          — after T2, T3
Task 8  (Skills page)          — after T3
Task 9  (Health page)          — after T2, T3
Task 10 (Settings page)        — after T3
Task 11 (Integration)          — after all pages
Task 12 (Verification)         — after all
```

**Parallelizable:**
- T0 + T1 + T2 (independent)
- T4 + T5 + T6 + T7 + T8 + T9 + T10 (independent after T3)
