"""Web Channel — FastAPI + WebSocket chat interface for JARVIS.

Provides:
- REST API for status/health/skills
- WebSocket for real-time chat with streaming responses
- Static file serving for the chat UI
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.brain.collector import DataCollector
from src.brain.processor import DataProcessor
from src.digital_twin.user_model import UserModel
from src.gateway.event_bus import get_event_bus
from src.gateway.models import AgentResponse, Channel, MessageEnvelope
from src.gateway.ws_hub import get_ws_hub
from src.gateway.session import SessionManager
from src.intelligence.router import LLMRouter
from src.memory.manager import MemoryManager
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry
from src.skills.router import SkillRouter
from src.tools.base import ToolRegistry
from src.tools.registry_all import ALL_TOOLS
from src.app import JarvisApp
from src.utils.logging import get_logger

log = get_logger("web")

# Static files directory
STATIC_DIR = Path(__file__).parent.parent.parent.parent / "web" / "static"


def create_app(app: JarvisApp | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    fastapi_app = FastAPI(title="JARVIS", version="2.0")

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    adapter = WebAdapter(app)

    # --- Unified WebSocket ---
    hub = get_ws_hub()

    @fastapi_app.websocket("/ws")
    async def websocket_unified(ws: WebSocket):
        await ws.accept()
        hub.connect(ws)
        try:
            while True:
                raw = await ws.receive_json()

                # Handle hub protocol (subscribe/unsubscribe/ping)
                response = await hub.handle_client_message(ws, raw)
                if response:
                    await ws.send_json(response)

                # Handle chat messages (backward compat with /ws/chat)
                msg_type = raw.get("type")
                if msg_type == "message":
                    text = raw.get("text", "").strip()
                    if text:
                        session_id = raw.get("session_id")
                        async for event in adapter.handle_message_stream(text, session_id):
                            await ws.send_json({"channel": "chat", **event})

                elif msg_type == "feedback":
                    rating = raw.get("rating", "")
                    message_id = raw.get("message_id", "")
                    if rating and message_id:
                        adapter.handle_feedback(message_id, rating)

                elif msg_type == "command":
                    cmd = raw.get("command", "")
                    result = await adapter.handle_command(cmd)
                    await ws.send_json({"channel": "chat", "type": "command_result", "data": result})

        except WebSocketDisconnect:
            log.info("ws_disconnected")
        except Exception as e:
            log.error("ws_error", error=str(e))
        finally:
            hub.disconnect(ws)

    # Bridge EventBus -> WebSocketHub for company events
    async def _bridge_company_event(event):
        """Forward company EventBus events to WS company channel."""
        mapping = {
            "company_ceo_route": "ceo_route",
            "company_dept_assign": "dept_assign",
            "company_dept_direct": "dept_direct",
            "company_worker_busy": "worker_start",
            "company_worker_done": "worker_done",
            "company_worker_fail": "worker_fail",
        }
        ws_event = mapping.get(event.type)
        if ws_event:
            await hub.broadcast("company", ws_event, event.data)

    event_bus = get_event_bus()
    for evt_type in [
        "company_ceo_route", "company_dept_assign", "company_dept_direct",
        "company_worker_busy", "company_worker_done", "company_worker_fail",
    ]:
        event_bus.subscribe(evt_type, _bridge_company_event)

    # Wire trading approval events through hub
    async def _push_trading_ws(event: dict):
        await hub.broadcast("trading", event.get("event", "update"), event)

    if (adapter._app
            and getattr(adapter._app, 'trading_brain', None)
            and hasattr(adapter._app.trading_brain, 'approval_manager')):
        adapter._app.trading_brain.approval_manager.on_ws_event(_push_trading_ws)

    # --- REST API ---
    @fastapi_app.get("/")
    async def index():
        html_path = STATIC_DIR / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>JARVIS Web UI</h1><p>Static files not found.</p>")

    @fastapi_app.get("/api/status")
    async def api_status():
        return JSONResponse(adapter.get_status())

    @fastapi_app.get("/api/health")
    async def api_health():
        return JSONResponse(await adapter.get_health())

    @fastapi_app.get("/api/skills")
    async def api_skills():
        return JSONResponse(adapter.get_skills())

    @fastapi_app.get("/api/memory")
    async def api_memory():
        return JSONResponse(adapter.get_memories())

    # --- Trading API ---
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

    # --- Trading Approvals & Candles API ---
    @fastapi_app.get("/api/trading/approvals")
    async def api_trading_approvals():
        if not adapter._app or not getattr(adapter._app, 'trading_brain', None):
            return JSONResponse({"approvals": []})
        approvals = adapter._app.trading_brain.approval_manager.get_pending()
        return JSONResponse({"approvals": approvals})

    @fastapi_app.post("/api/trading/approve/{approval_id}")
    async def api_trading_approve(approval_id: str):
        if not adapter._app or not getattr(adapter._app, 'trading_brain', None):
            return JSONResponse({"error": "Trading brain unavailable"})
        result = await adapter._app.trading_brain.handle_approval_response(
            approval_id, "approve", via="dashboard"
        )
        return JSONResponse(result)

    @fastapi_app.post("/api/trading/reject/{approval_id}")
    async def api_trading_reject(approval_id: str):
        if not adapter._app or not getattr(adapter._app, 'trading_brain', None):
            return JSONResponse({"error": "Trading brain unavailable"})
        result = await adapter._app.trading_brain.handle_approval_response(
            approval_id, "reject", via="dashboard"
        )
        return JSONResponse(result)

    @fastapi_app.get("/api/trading/candles")
    async def api_trading_candles(
        symbol: str = "XAUUSD", timeframe: str = "H1", count: int = 100
    ):
        try:
            from src.trading.mt5_client import MT5Client
            client = MT5Client()
            candles = await client.get_rates(symbol, timeframe, count)
            return JSONResponse({"candles": candles})
        except Exception as e:
            return JSONResponse({"candles": [], "error": str(e)})

    # --- Company API ---
    @fastapi_app.get("/api/company/status")
    async def api_company_status():
        if not adapter._app or not adapter._app._ceo:
            return JSONResponse({"error": "Company not initialized"})
        return JSONResponse(adapter._app._ceo.get_status())

    @fastapi_app.get("/api/company/activity")
    async def api_company_activity(limit: int = Query(50)):
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

    # --- Memory, Activity, System API ---
    @fastapi_app.get("/api/memory/search")
    async def api_memory_search(q: str = Query("")):
        return JSONResponse(await adapter.search_memories(q))

    @fastapi_app.post("/api/memory")
    async def api_memory_add(request: dict):
        return JSONResponse(await adapter.add_memory(request))

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

    # Mount static files (CSS, JS, etc.)
    if STATIC_DIR.exists():
        fastapi_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return fastapi_app


class WebAdapter:
    """Web channel adapter — manages state for web clients."""

    def __init__(self, app: JarvisApp | None = None) -> None:
        if app is not None:
            self._app = app
            self._sessions = app.sessions
            self._collector = app.collector
            self._processor = app.processor
            self._memory = app.memory
            self._user_model = app.user_model
            self._skill_loader = app.skill_loader
            self._skill_router = app.skill_router
            self._bus = app.event_bus
            self._skill_registry = app.skill_registry
            self._tool_registry = app.tool_registry
            self._router = app.router
        else:
            self._app = None
            self._sessions = SessionManager()
            self._collector = DataCollector()
            self._processor = DataProcessor()
            self._memory = MemoryManager()
            self._user_model = UserModel()
            self._skill_loader = SkillLoader()
            self._skill_router = SkillRouter(self._skill_loader)
            self._bus = get_event_bus()

            # Load skills
            self._skill_loader.load_all()
            self._skill_registry = SkillRegistry(self._skill_loader)
            self._skill_registry._apply_metrics()

            # Register tools (centralized in registry_all.py)
            self._tool_registry = ToolRegistry()
            for tool in ALL_TOOLS:
                self._tool_registry.register(tool)

            # Init router
            skill_summary = self._skill_loader.get_metadata_summary()
            self._router = LLMRouter(
                skill_summary=skill_summary,
                tool_registry=self._tool_registry,
            )

        self._user_id = "web_user"
        self._session = self._sessions.get_or_create(
            Channel.WEB, self._user_id, "Web User"
        )

    async def handle_message_stream(
        self, text: str, session_id: str | None = None,
    ):
        """Handle a chat message, yielding streaming events."""
        session_key = self._memory.session_key("web", self._user_id)

        # Send "thinking" indicator
        yield {"type": "thinking", "text": ""}

        try:
            # 1. Process through memory
            await self._memory.process_user_message(session_key, text)

            # 2. Update user model
            self._user_model.update_from_message(self._user_id, text)

            # 3. Get context
            memory_context = await self._memory.get_relevant_context(text, session_key)
            user_context = self._user_model.build_context(self._user_id)
            if user_context:
                memory_context = f"{user_context}\n\n{memory_context}" if memory_context else user_context

            # 4. Find skills
            matched_skills = []
            skill_context = ""
            try:
                matched_skills = await self._skill_router.find_skills(text)
                skill_context = self._skill_router.build_skill_context(matched_skills)
            except Exception as e:
                log.warning("skill_routing_error", error=str(e))

            # 5. Route through LLM with streaming
            start = time.monotonic()
            response: AgentResponse = await self._router.route(
                self._session, text,
                memory_context=memory_context,
                skill_context=skill_context,
            )
            elapsed = int((time.monotonic() - start) * 1000)

            # 6. Update state
            self._session.add_user_message(text)
            self._session.add_assistant_message(response.content)
            await self._memory.process_assistant_response(session_key, response.content)
            for skill in matched_skills:
                self._skill_router.update_usage(skill.metadata.name)

            # 7. Log
            envelope = MessageEnvelope(
                channel=Channel.WEB,
                session_id=self._session.session_id,
                user_id=self._user_id,
                username="Web User",
                content=text,
            )
            skills_used = [s.metadata.name for s in matched_skills]
            await self._collector.log_interaction(envelope, response, skills_used=skills_used)

            # 8. Stream response
            model_short = response.model_used.split("/")[-1] if "/" in response.model_used else response.model_used
            yield {
                "type": "response",
                "text": response.content,
                "model": model_short,
                "latency_ms": response.latency_ms or elapsed,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
                "cost": f"${response.cost_usd:.4f}" if response.cost_usd else "free",
                "tools_used": json.loads(response.reasoning_trace) if response.reasoning_trace else [],
            }

        except Exception as e:
            log.error("web_message_error", error=str(e))
            yield {"type": "error", "text": f"Lỗi: {e}"}

    def handle_feedback(self, message_id: str, rating: str) -> None:
        """Handle user feedback on a response."""
        log.info("web_feedback", message_id=message_id, rating=rating)

    async def handle_command(self, cmd: str) -> dict:
        """Handle slash commands via WebSocket."""
        if cmd == "status":
            return self.get_status()
        elif cmd == "health":
            return await self.get_health()
        elif cmd == "skills":
            return self.get_skills()
        elif cmd == "memory":
            return self.get_memories()
        elif cmd == "reset":
            self._session.messages.clear()
            return {"message": "Session reset"}
        return {"error": f"Unknown command: {cmd}"}

    def get_status(self) -> dict:
        """Get system status."""
        router_stats = self._router.get_stats()
        cost = router_stats["cost"]
        return {
            "status": "online",
            "version": "2.0",
            "skills": len(self._skill_loader.get_all_metadata()),
            "tools": len(self._tool_registry.get_all()),
            "cloud_model": router_stats.get("cloud_model", ""),
            "total_calls": cost.get("total_calls", 0),
            "local_ratio": f"{cost.get('local_ratio', 0) * 100:.0f}%",
            "total_cost": f"${cost.get('total_cost_usd', 0):.4f}",
            "cache_entries": router_stats.get("cache", {}).get("cached_entries", 0),
        }

    async def get_health(self) -> dict:
        """Run health checks."""
        try:
            from src.metacognition.diagnostics import SelfDiagnostics
            diag = SelfDiagnostics()
            checks = await diag.run_all()
            return {
                "checks": [
                    {
                        "name": c.name,
                        "status": c.status.value if hasattr(c.status, 'value') else str(c.status),
                        "message": c.message,
                        "latency_ms": c.latency_ms,
                    }
                    for c in checks
                ]
            }
        except Exception as e:
            return {"error": str(e)}

    def get_skills(self) -> dict:
        """Get all skills."""
        skills = self._skill_loader.get_all_metadata()
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description[:100],
                    "version": s.version,
                    "category": s.category,
                    "emoji": s.emoji or "",
                    "priority": s.priority,
                    "success_rate": s.success_rate,
                    "usage_count": s.usage_count,
                }
                for s in sorted(skills, key=lambda x: -x.priority)
            ]
        }

    def get_memories(self) -> dict:
        """Get recent memories."""
        memories = self._memory.semantic.get_all(limit=20)
        return {
            "memories": [
                {
                    "content": m["content"],
                    "category": m.get("category", ""),
                    "importance": m.get("importance", 0),
                }
                for m in memories
            ]
        }

    # --- Trading methods ---

    def get_trading_status(self) -> dict:
        if not self._app or not getattr(self._app, 'trading_brain', None):
            return {"status": "unavailable", "running": False}
        brain = self._app.trading_brain
        return brain.get_status()

    async def get_trading_positions(self) -> dict:
        if not self._app or not getattr(self._app, 'trading_brain', None):
            return {"positions": []}
        try:
            from src.trading.mt5_client import MT5Client
            client = MT5Client()
            positions = await client.get_positions()
            return {"positions": positions}
        except Exception as e:
            return {"positions": [], "error": str(e)}

    async def get_trading_history(self) -> dict:
        if not self._app or not getattr(self._app, 'trading_brain', None):
            return {"trades": []}
        try:
            from src.trading.persistence import TradingPersistence
            db = TradingPersistence()
            trades = db.get_recent_trades(limit=50)
            return {"trades": trades}
        except Exception as e:
            return {"trades": [], "error": str(e)}

    def get_trading_zones(self) -> dict:
        if not self._app or not getattr(self._app, 'trading_brain', None):
            return {"zones": []}
        status = self._app.trading_brain.get_status()
        return {"zones": status.get("active_zones", [])}

    async def get_trading_pnl(self) -> dict:
        if not self._app or not getattr(self._app, 'trading_brain', None):
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
        if not self._app or not getattr(self._app, 'trading_brain', None):
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

    # --- Memory, Activity, System methods ---

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
        if not self._app or not getattr(self._app, 'dreamtime', None):
            return {"status": "unavailable"}
        dt = self._app.dreamtime
        return {
            "enabled": dt._enabled if hasattr(dt, '_enabled') else False,
            "last_run": str(dt._last_run) if hasattr(dt, '_last_run') else None,
            "idle_minutes": dt._idle_minutes if hasattr(dt, '_idle_minutes') else 30,
        }
