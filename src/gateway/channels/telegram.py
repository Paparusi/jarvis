"""Telegram Channel Adapter — Chat với JARVIS qua Telegram.

Phase 5: Event Bus + Streaming + Smart Tool Routing.
"""

from __future__ import annotations

import asyncio
import os
import time as _time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.brain.collector import DataCollector
from src.brain.feedback import FeedbackStore
from src.brain.processor import DataProcessor
from src.digital_twin.decision_engine import DecisionEngine
from src.digital_twin.user_model import UserModel
from src.gateway.event_bus import EventType, get_event_bus
from src.metacognition.health_monitor import HealthMonitor
from src.gateway.models import AgentResponse, Channel, MessageEnvelope
from src.gateway.safety import SafetyGuard
from src.gateway.session import SessionManager
from src.intelligence.intent_tracker import ConversationTracker
from src.intelligence.router import LLMRouter
from src.memory.manager import MemoryManager
from src.scheduling.scheduler import Scheduler, detect_reminder_intent, parse_reminder_time
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry
from src.skills.executor import SkillExecutor
from src.skills.router import SkillRouter
from src.tools.base import ToolRegistry
from src.tools.registry_all import ALL_TOOLS
from src.dreamtime.scheduler import DreamtimeScheduler
from src.dreamtime.consolidator import MemoryConsolidator
from src.dreamtime.dreamer import Dreamer
from src.skills.evolver import SkillEvolver
from src.swarm.coordinator import SwarmCoordinator
from src.swarm.decomposer import TaskDecomposer
from src.swarm.factory import AgentFactory
from src.app import JarvisApp
from src.utils.config import get_project_root
from src.utils.logging import get_logger

log = get_logger("telegram")

# Tool usage indicators in user messages
_TOOL_INDICATORS = {
    "web_search": [
        "tìm", "search", "tra cứu", "google", "tin tức", "news", "giá",
        "thời tiết", "weather", "mới nhất", "latest", "bao nhiêu",
        "so sánh", "compare", "review", "đánh giá",
        "thông tin", "hôm nay", "today", "hiện tại", "current",
        "update", "cập nhật", "xu hướng", "trend",
        "tỷ giá", "stock", "crypto", "bitcoin", "xauusd",
        "ai mới", "ra mắt", "released", "announce",
        "url", "http", "www", "link", "website",
    ],
    "run_command": [
        "chạy lệnh", "run", "execute", "terminal", "shell", "command",
        "pip", "npm", "python3",
    ],
    "file_ops": [
        "đọc file", "read file", "ghi file", "write file", "tạo file",
        "create file", "list file", "xem file", "open file",
    ],
    "run_python": [
        "tính", "calculate", "compute", "code", "python", "script",
        "chạy code", "run code", "thử code", "viết code",
    ],
    "http_request": [
        "api", "request", "post", "put", "delete", "patch",
        "webhook", "endpoint", "rest", "graphql", "curl",
    ],
    "network": [
        "scan", "nmap", "port", "ping", "traceroute", "dns",
        "dig", "network", "mạng", "quét", "vulnerability",
        "pentest", "recon", "reconnaissance",
    ],
    "git": [
        "git", "commit", "branch", "diff", "merge", "push",
        "pull", "clone", "checkout", "stash",
    ],
    "docker": [
        "docker", "container", "image", "compose",
        "dockerfile", "build image",
    ],
    "browser": [
        "screenshot", "chụp màn hình", "chụp web", "render",
        "spa", "javascript page", "headless", "browse",
        "truy cập trang", "truy cap trang", "google search",
    ],
    "crypto": [
        "base64", "encode", "decode", "hash", "md5", "sha",
        "jwt", "hex", "password", "mật khẩu", "regex",
        "timestamp", "unix time", "epoch", "cidr", "subnet",
    ],
    "recon": [
        "subdomain", "cve", "vulnerability", "header", "security header",
        "whois", "ssl", "certificate", "cert", "reverse dns",
        "tech detect", "wappalyzer", "fingerprint",
        "ip info", "geolocation",
    ],
    "code_analysis": [
        "phân tích code", "analyze code", "ast", "complexity",
        "cyclomatic", "dependency", "import graph",
        "tìm trong code", "search code", "diff", "so sánh code",
    ],
    "data": [
        "csv", "json", "sqlite", "database", "query", "sql",
        "phân tích dữ liệu", "data analysis", "text stats",
        "transform", "flatten", "parse",
    ],
}


def needs_tools(text: str) -> bool:
    """Determine if a user message likely needs tool access."""
    text_lower = text.lower()
    for _tool, indicators in _TOOL_INDICATORS.items():
        for indicator in indicators:
            if indicator in text_lower:
                return True
    return False


_REQUEST_TIMEOUT = 90  # seconds — max time for a single request end-to-end


class TelegramAdapter:
    """Telegram bot adapter for JARVIS with memory, skills, and routing."""

    def __init__(self, app_or_token, token: str | None = None) -> None:
        # Support both: TelegramAdapter(app, token="xxx") and TelegramAdapter("xxx")
        if isinstance(app_or_token, JarvisApp):
            app = app_or_token
            self._token = token
        else:
            app = None
            self._token = app_or_token

        if app is not None:
            # --- New path: receive components from JarvisApp container ---
            self._sessions = app.sessions
            self._collector = app.collector
            self._processor = app.processor
            self._feedback = FeedbackStore()       # Telegram-specific, keep local
            self._scheduler = Scheduler()           # Telegram-specific, keep local
            self._safety = SafetyGuard()            # Telegram-specific, keep local
            self._response_cache: dict[int, dict] = {}
            self._retry_cache: dict[str, dict] = {}
            self._user_locks: dict[str, asyncio.Lock] = {}
            self._memory = app.memory
            self._user_model = app.user_model
            self._decision_engine = DecisionEngine(self._user_model)
            self._intent_tracker = ConversationTracker()
            self._skill_loader = app.skill_loader
            self._skill_router = app.skill_router
            self._skill_executor = app.skill_executor
            self._bus = app.event_bus
            self._health_monitor = app.health_monitor   # Must call app.init_health() first
            self._app: Application | None = None         # telegram.ext Application (set in start())
            self._skill_registry = app.skill_registry
            self._tool_registry = app.tool_registry
            self._router = app.router
            self._dreamtime = app.dreamtime              # Must call app.init_dreamtime() first
            self._memory_consolidator = app.memory_consolidator
            self._dreamer = app.dreamer
            self._evolver = app.evolver
            self._decomposer = app.decomposer            # Must call app.init_swarm() first
            self._swarm = app.swarm
            self._proactive = app.proactive              # Must call app.init_proactive() first
            self._mcp_bridge = app._mcp_bridge
            self._bounty_pipeline = app.bounty_pipeline

            # Wire dreamtime callback if dreamtime was initialized
            if self._dreamtime is not None:
                self._dreamtime.set_dream_callback(self._run_dreamtime_cycle)

            # Wire health monitor to router if available
            if self._health_monitor is not None:
                self._router._health_monitor = self._health_monitor
        else:
            # --- Legacy path: self-contained initialization (backward compat) ---
            self._sessions = SessionManager()
            self._collector = DataCollector()
            self._processor = DataProcessor()
            self._feedback = FeedbackStore()
            self._scheduler = Scheduler()
            self._safety = SafetyGuard()
            # Cache recent responses for feedback buttons (message_id -> context)
            self._response_cache: dict[int, dict] = {}
            # Cache failed requests for retry buttons (retry_id -> context)
            self._retry_cache: dict[str, dict] = {}
            # Per-user lock -- prevents concurrent processing for same user
            self._user_locks: dict[str, asyncio.Lock] = {}
            self._memory = MemoryManager()
            self._user_model = UserModel()
            self._decision_engine = DecisionEngine(self._user_model)
            self._intent_tracker = ConversationTracker()
            self._skill_loader = SkillLoader()
            self._skill_router = SkillRouter(self._skill_loader)
            self._skill_executor = SkillExecutor()
            self._bus = get_event_bus()
            self._health_monitor = HealthMonitor()
            self._app: Application | None = None

            # Load skills on init
            self._skill_loader.load_all()
            self._skill_registry = SkillRegistry(self._skill_loader)
            self._skill_registry._apply_metrics()

            # Register tools (centralized in registry_all.py)
            self._tool_registry = ToolRegistry()
            for tool in ALL_TOOLS:
                self._tool_registry.register(tool)

            # Init router with skill metadata summary + tools
            skill_summary = self._skill_loader.get_metadata_summary()
            self._router = LLMRouter(
                skill_summary=skill_summary,
                tool_registry=self._tool_registry,
            )

            # Expose health monitor for router degradation
            self._router._health_monitor = self._health_monitor

            # Dreamtime Engine — consolidate memory + analyze skills during idle
            self._dreamtime = DreamtimeScheduler(idle_minutes=30, cron_hour=2, enabled=True)
            self._memory_consolidator = MemoryConsolidator(
                semantic_memory=self._memory.semantic,
            )
            self._dreamer = Dreamer(
                collector=self._collector,
                skill_registry=self._skill_registry,
            )
            self._dreamtime.set_dream_callback(self._run_dreamtime_cycle)

            # Skill Evolver — optimize/merge/prune skills during Dreamtime
            self._evolver = SkillEvolver(
                registry=self._skill_registry,
                loader=self._skill_loader,
            )

            # Swarm Coordinator — parallel multi-agent task decomposition
            self._decomposer = TaskDecomposer(complexity_threshold=40)
            self._swarm = SwarmCoordinator(
                decomposer=self._decomposer,
                factory=AgentFactory(tool_registry=self._tool_registry),
            )

            # Proactive Engine — morning briefing + daily digest
            from src.intelligence.proactive import ProactiveEngine
            self._proactive = ProactiveEngine(
                memory=self._memory.semantic,
                user_model=self._user_model,
                collector=self._collector,
                user_id=f"telegram_{os.environ.get('JARVIS_OWNER_ID', '1991690969')}",
            )

            # MCP Bridge — lazy connect (no blocking init)
            self._mcp_bridge = None

            # Bug Bounty Pipeline — not available in legacy path
            self._bounty_pipeline = None

        # Subscribe event bus — DataCollector auto-logs via events (BOTH paths)
        self._bus.subscribe(EventType.TOOL_CALLED, self._on_tool_called)

    async def _on_tool_called(self, event) -> None:
        """Handle tool_called events for status updates."""
        log.debug("event_tool_called", tool=event.data.get("tool"))

    async def start(self) -> None:
        """Start the Telegram bot."""
        self._app = (
            Application.builder()
            .token(self._token)
            .build()
        )

        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(CommandHandler("health", self._handle_health))
        self._app.add_handler(CommandHandler("reset", self._handle_reset))
        self._app.add_handler(CommandHandler("memory", self._handle_memory))
        self._app.add_handler(CommandHandler("remember", self._handle_remember))
        self._app.add_handler(CommandHandler("skills", self._handle_skills))
        self._app.add_handler(CommandHandler("train", self._handle_train))
        self._app.add_handler(CommandHandler("explain", self._handle_explain))
        self._app.add_handler(CommandHandler("stats", self._handle_stats))
        self._app.add_handler(CommandHandler("profile", self._handle_profile))
        self._app.add_handler(CommandHandler("remind", self._handle_remind))
        self._app.add_handler(CommandHandler("reminders", self._handle_reminders))
        self._app.add_handler(CommandHandler("dream", self._handle_dream))
        self._app.add_handler(CommandHandler("redteam", self._handle_redteam))
        self._app.add_handler(CommandHandler("swarm", self._handle_swarm))
        self._app.add_handler(CommandHandler("eval", self._handle_eval))
        self._app.add_handler(CommandHandler("digest", self._handle_digest))
        self._app.add_handler(CommandHandler("pentest", self._handle_pentest))
        self._app.add_handler(CommandHandler("bounty", self._handle_bounty))
        self._app.add_handler(CallbackQueryHandler(self._handle_feedback))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )
        self._app.add_handler(
            MessageHandler(filters.Document.ALL, self._handle_document)
        )
        self._app.add_handler(
            MessageHandler(filters.PHOTO, self._handle_photo)
        )
        self._app.add_handler(
            MessageHandler(filters.VOICE | filters.AUDIO, self._handle_voice)
        )

        log.info("telegram_bot_starting")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        # Start scheduler with Telegram notification callback
        self._scheduler.set_notification_callback(self._send_notification)
        await self._scheduler.start()

        # Start background health monitor
        admin_ids = self._get_admin_user_ids()
        self._health_monitor.set_notification_callback(
            self._send_notification, admin_ids,
        )
        await self._health_monitor.start()

        # Start Dreamtime scheduler (idle 30min + cron 2AM)
        await self._dreamtime.start()

        # Start Proactive Engine (morning briefing + daily digest)
        owner_id = os.environ.get("JARVIS_OWNER_ID", "")
        if owner_id:
            self._proactive.on_notify(self._handle_proactive_insight)
            await self._proactive.start()
            log.info("proactive_engine_started", owner=owner_id)

        # Connect MCP Bridge (non-blocking)
        try:
            from src.skills.mcp_bridge import MCPBridge
            self._mcp_bridge = MCPBridge(tool_registry=self._tool_registry)
            asyncio.create_task(self._connect_mcp())
        except Exception as e:
            log.debug("mcp_bridge_init_skip", error=str(e))

        log.info("telegram_bot_running")

    async def _connect_mcp(self) -> None:
        """Connect MCP servers and generate skill skeletons."""
        try:
            tools_count = await self._mcp_bridge.connect_all()
            if tools_count > 0:
                created = self._mcp_bridge.generate_skill_skeletons()
                log.info("mcp_bridge_connected", tools=tools_count, skills_created=len(created))
            else:
                log.info("mcp_bridge_no_tools")
        except Exception as e:
            log.warning("mcp_bridge_connect_error", error=str(e))

    async def stop(self) -> None:
        await self._dreamtime.stop()
        await self._health_monitor.stop()
        await self._scheduler.stop()
        if self._mcp_bridge:
            await self._mcp_bridge.disconnect_all()
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            log.info("telegram_bot_stopped")

    def _get_admin_user_ids(self) -> list[str]:
        """Get admin user IDs from config for authorization."""
        from src.utils.config import load_config
        config = load_config()
        # Check both config paths (channels.telegram and telegram)
        tg_config = config.get("channels", {}).get("telegram", {})
        if not tg_config:
            tg_config = config.get("telegram", {})
        admin_ids = tg_config.get("admin_user_ids", [])
        if not admin_ids:
            owner = tg_config.get("owner_id", "")
            if owner:
                return [str(owner)]
        return [str(uid) for uid in admin_ids]

    async def _send_notification(self, user_id: str, message: str) -> None:
        """Send a proactive notification to a user via Telegram."""
        if not self._app:
            return
        try:
            await self._app.bot.send_message(
                chat_id=int(user_id),
                text=message,
                parse_mode="Markdown",
            )
            log.info("notification_sent", user_id=user_id)
        except Exception as e:
            log.error("notification_failed", user_id=user_id, error=str(e))

    async def _handle_proactive_insight(self, insight) -> None:
        """Handle ProactiveInsight → send to owner via Telegram."""
        owner_id = os.environ.get("JARVIS_OWNER_ID", "")
        if not owner_id or not self._app:
            return
        try:
            # Digest uses Markdown format with links
            parse_mode = "Markdown" if insight.metadata.get("format") == "markdown" else "Markdown"
            await self._app.bot.send_message(
                chat_id=int(owner_id),
                text=insight.content,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            log.info("proactive_sent", type=insight.type, title=insight.title)
        except Exception as e:
            # Fallback: send as plain text if Markdown fails
            try:
                from src.intelligence.daily_digest import DailyDigest
                if insight.type == "digest" and hasattr(self._proactive, '_digest'):
                    last = self._proactive._digest.last_digest
                    if last:
                        await self._app.bot.send_message(
                            chat_id=int(owner_id),
                            text=last.to_plain(),
                        )
                        return
                await self._app.bot.send_message(
                    chat_id=int(owner_id),
                    text=insight.content,
                )
            except Exception:
                log.error("proactive_send_failed", type=insight.type, error=str(e))

    # --- Command Handlers ---

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        skills_count = len(self._skill_loader.get_all_metadata())
        tools_count = len(self._tool_registry.get_all())
        await update.message.reply_text(
            f"Xin chào {user.first_name}! 👋\n\n"
            f"Tôi là **JARVIS** — trợ lý AI cá nhân của bạn.\n"
            f"🧠 Bộ nhớ dài hạn | ⚡ Local AI + Cloud | 🎯 {skills_count} skills | 🔧 {tools_count} tools\n\n"
            f"💡 **Tôi có thể:**\n"
            f"• Chat text, voice, gửi file/ảnh\n"
            f"• Nhớ thông tin qua các cuộc trò chuyện\n"
            f"• Tìm kiếm web, chạy code, phân tích dữ liệu\n"
            f"• Đặt nhắc nhở bằng ngôn ngữ tự nhiên\n\n"
            f"⌨️ **Commands:**\n"
            f"/status — Trạng thái hệ thống\n"
            f"/health — Kiểm tra sức khỏe\n"
            f"/stats — Thống kê chi tiết\n"
            f"/profile — Xem Digital Twin\n"
            f"/memory — Xem bộ nhớ\n"
            f"/remember `<text>` — Ghi nhớ\n"
            f"/remind `<text>` — Đặt nhắc nhở\n"
            f"/reminders — Xem nhắc nhở\n"
            f"/skills — Xem kỹ năng\n"
            f"/explain — Giải thích reasoning\n"
            f"/train — Training data\n"
            f"/reset — Reset trò chuyện",
            parse_mode="Markdown",
        )

    async def _handle_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Run self-diagnostics health check."""
        await update.message.reply_text("🏥 Đang kiểm tra sức khỏe hệ thống...")
        checks = await self._health_monitor.run_check()
        from src.metacognition.diagnostics import SelfDiagnostics
        report = SelfDiagnostics().format_report(checks)
        # Add degradation mode info
        mode = self._health_monitor.health.degraded_mode
        if mode:
            report += f"\n\n🔶 **Chế độ**: {mode}"
        try:
            await update.message.reply_text(report, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(report)

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        brain_stats = self._collector.get_stats()
        mem_stats = self._memory.get_stats()
        router_stats = self._router.get_stats()
        cost_stats = router_stats["cost"]
        cache_stats = router_stats["cache"]
        trace_stats = router_stats.get("traces", {})
        user_id = str(update.effective_user.id)
        session_key = self._memory.session_key("telegram", user_id)
        msg_count = self._memory.episodic.count_messages(session_key)
        skills_count = len(self._skill_loader.get_all_metadata())
        user_stats = self._user_model.get_stats(user_id)

        local_status = "ON" if router_stats["local_enabled"] else "OFF"
        tool_stats = router_stats.get("tools", {})

        # Per-model breakdown
        model_lines = ""
        for m in cost_stats.get("per_model", []):
            model_lines += f"\n    `{m['model']}`: {m['calls']} calls, ${m['cost']:.4f}"

        # Tool usage breakdown
        tool_lines = ""
        tool_usage = tool_stats.get("usage", {})
        if any(v > 0 for v in tool_usage.values()):
            for name, count in tool_usage.items():
                if count > 0:
                    tool_lines += f"\n    `{name}`: {count} calls"

        await update.message.reply_text(
            f"🤖 **JARVIS Status**\n\n"
            f"🧠 Semantic memories: {mem_stats['semantic_memories']}\n"
            f"💬 Messages (bạn): {msg_count}\n"
            f"📊 Training data: {brain_stats['total_records']} records\n"
            f"🎯 Skills loaded: {skills_count}\n"
            f"🔧 Tools: {tool_stats.get('registered_tools', 0)} registered\n"
            f"📁 Active sessions: {self._sessions.count()}\n\n"
            f"👤 **Digital Twin**\n"
            f"  Topics tracked: {user_stats['topics_tracked']}\n"
            f"  Avg msg length: {user_stats['avg_msg_length']:.0f} chars\n\n"
            f"⚡ **Router**\n"
            f"  Local: {local_status} (`{router_stats['local_model']}`)\n"
            f"  Cloud: `{router_stats['cloud_model']}`\n"
            f"  Cache: {cache_stats['cached_entries']} entries, {cache_stats['total_hits']} hits\n"
            f"  Escalation rate: {trace_stats.get('escalation_rate', 'N/A')}\n"
            f"  Avg confidence: {trace_stats.get('avg_confidence', 'N/A')}\n\n"
            f"🔧 **Tools**{tool_lines if tool_lines else chr(10) + '  Chưa có tool nào được gọi'}\n\n"
            f"📈 **Metrics (30 ngày)**\n"
            f"  Total: {cost_stats['total_calls']} calls, ${cost_stats['total_cost_usd']:.4f}\n"
            f"  Local/Cloud: {cost_stats.get('local_calls', 0)}/{cost_stats.get('cloud_calls', 0)}"
            f" ({cost_stats.get('local_ratio', 0) * 100:.0f}% local)\n"
            f"  Avg latency: {cost_stats['avg_latency_ms']}ms"
            f"{model_lines}",
            parse_mode="Markdown",
        )

    async def _handle_memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show what JARVIS remembers."""
        memories = self._memory.semantic.get_all(limit=10)
        if not memories:
            await update.message.reply_text("🧠 Bộ nhớ trống — chưa có gì để nhớ cả!")
            return

        lines = ["🧠 **Bộ nhớ JARVIS:**\n"]
        for m in memories:
            lines.append(f"• {m['content']} `[{m['category']}]`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _handle_remember(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Explicitly store a memory: /remember <text>"""
        text = update.message.text.replace("/remember", "", 1).strip()
        if not text:
            await update.message.reply_text("Dùng: `/remember <thông tin cần nhớ>`", parse_mode="Markdown")
            return

        await self._memory.remember_fact(text, category="user_stated", importance=0.9)
        await update.message.reply_text(f"✅ Đã ghi nhớ: *{text}*", parse_mode="Markdown")

    async def _handle_skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show loaded skills."""
        skills = self._skill_loader.get_all_metadata()
        if not skills:
            await update.message.reply_text("🎯 Chưa có skill nào được tải.")
            return

        lines = ["🎯 **JARVIS Skills:**\n"]
        for s in sorted(skills, key=lambda x: -x.priority):
            emoji = s.emoji or "•"
            rate = f"{s.success_rate:.0%}"
            lines.append(f"{emoji} **{s.name}** v{s.version} — {s.description[:60]}")
            lines.append(f"   Priority: {s.priority} | Success: {rate} | Uses: {s.usage_count}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _handle_train(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process training data and optionally trigger training.

        /train              — Show brain independence report
        /train now [4b|14b] — Force-trigger SFT training (optional profile)
        /train check        — Check if auto-retrain threshold met
        """
        args = (update.message.text or "").replace("/train", "", 1).strip().lower().split()
        subcmd = args[0] if args else ""
        profile = args[1] if len(args) > 1 else None

        if subcmd == "now":
            await self._handle_train_now(update, profile=profile)
            return
        if subcmd == "check":
            await self._handle_train_check(update)
            return

        # Default: process data + show report
        await update.message.reply_text("⏳ Đang xử lý training data...")
        stats = self._processor.process_all()
        brain_stats = self._collector.get_stats()
        proc_stats = self._processor.get_stats()
        router_stats = self._router.get_stats()
        cost_stats = router_stats["cost"]

        # Calculate brain independence progress
        per_model = cost_stats.get("per_model", [])
        local_calls = sum(m["calls"] for m in per_model if "ollama" in m["model"])
        cloud_calls = sum(m["calls"] for m in per_model if "claude" in m["model"])
        total_calls = local_calls + cloud_calls
        local_pct = (local_calls / total_calls * 100) if total_calls > 0 else 0

        # Auto-trainer status
        from src.brain.auto_trainer import AutoTrainer
        auto = AutoTrainer()
        auto_stats = auto.get_stats()

        await update.message.reply_text(
            f"🧠 **Brain Independence Report**\n\n"
            f"📊 **Data Collection**\n"
            f"  Total records: {brain_stats['total_records']}\n"
            f"  Data files: {brain_stats['total_files']}\n\n"
            f"🔄 **Processing Results**\n"
            f"  Raw: {stats['raw']} → Filter: {stats.get('after_filter', 0)}"
            f" → Dedup: {stats.get('after_dedup', 0)}\n"
            f"  SFT samples: {stats['sft']}\n"
            f"  DPO pairs: {stats['dpo']}\n"
            f"  SFT datasets: {proc_stats['sft_datasets']}\n"
            f"  DPO datasets: {proc_stats['dpo_datasets']}\n\n"
            f"🎯 **Independence Progress**\n"
            f"  Local calls: {local_calls} ({local_pct:.0f}%)\n"
            f"  Cloud calls: {cloud_calls}\n"
            f"  Target: 90%+ local (Month 6)\n"
            f"  API cost: ${cost_stats['total_cost_usd']:.4f}\n\n"
            f"🤖 **Auto-Trainer**\n"
            f"  Should retrain: {'✅' if auto_stats['should_retrain'] else '❌'}\n"
            f"  {auto_stats['reason']}\n"
            f"  Training runs: {auto_stats['train_count']}\n"
            f"  Last train: {auto_stats['last_train_at'] or 'Never'}\n\n"
            f"_Commands: /train now [4b|14b] | /train check_",
            parse_mode="Markdown",
        )

    async def _handle_train_now(self, update: Update, profile: str | None = None) -> None:
        """Force-trigger SFT training pipeline with optional model profile."""
        if profile and profile not in ("4b", "14b"):
            await update.message.reply_text(f"❌ Unknown profile '{profile}'. Use: 4b or 14b")
            return

        profile_label = profile or "default"
        model_name = "jarvis-brain-14b" if profile == "14b" else "jarvis-brain"
        await update.message.reply_text(
            f"🚀 Starting training pipeline (profile={profile_label}, force=True)...\n"
            "This may take 10-40 minutes depending on model size."
        )

        from src.brain.auto_trainer import AutoTrainer
        trainer = AutoTrainer(model_profile=profile)

        try:
            result = await trainer.run(force=True)
            status = result.get("status", "unknown")

            if status == "completed":
                sft = result.get("steps", {}).get("sft", {})
                loss = sft.get("metrics", {}).get("train_loss", "N/A")
                mode = sft.get("metrics", {}).get("mode", "N/A")
                eval_data = result.get("steps", {}).get("evaluation", {})
                text = (
                    f"✅ **Training Complete!**\n\n"
                    f"  Profile: {profile_label}\n"
                    f"  Mode: {mode}\n"
                    f"  Loss: {loss}\n"
                    f"  Accuracy: {eval_data.get('accuracy', 'N/A')}\n"
                )
                export = result.get("steps", {}).get("export", {})
                if export.get("status") == "completed":
                    text += f"  Model deployed: {model_name} ✅\n"
                else:
                    text += f"  Export: {export.get('status', 'N/A')}\n"
            elif status == "sft_failed":
                text = f"❌ **SFT Training Failed**\n  {result.get('error', 'Unknown error')}"
            else:
                text = f"⚠️ **Training status: {status}**\n  {result.get('reason', '')}"

            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Training error: {e}")

    async def _handle_train_check(self, update: Update) -> None:
        """Check if auto-retrain threshold is met."""
        from src.brain.auto_trainer import AutoTrainer
        trainer = AutoTrainer()
        should, reason = trainer.should_retrain()
        stats = trainer.get_stats()

        history = ""
        for h in stats.get("history", []):
            history += f"\n  • {h['at'][:10]}: loss={h.get('loss', 'N/A')}, mode={h.get('mode', 'N/A')}"

        await update.message.reply_text(
            f"🔍 **Training Check**\n\n"
            f"  Ready: {'✅ Yes' if should else '❌ No'}\n"
            f"  {reason}\n"
            f"  Last SFT count: {stats['last_sft_count']}\n"
            f"  Training runs: {stats['train_count']}\n"
            f"\n📜 **History:**{history if history else ' None'}",
            parse_mode="Markdown",
        )

    async def _handle_explain(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Explain JARVIS's reasoning for the last request."""
        from src.metacognition.explainer import ExplainerEngine

        user_id = str(update.effective_user.id)
        session = self._sessions.get_or_create(
            Channel.TELEGRAM, user_id, update.effective_user.first_name,
        )

        explainer = ExplainerEngine(self._router._tracer)

        # Check if user wants recent overview: /explain all
        args = (update.message.text or "").replace("/explain", "", 1).strip()
        if args.lower() in ("all", "recent", "gần đây"):
            text = explainer.explain_recent(limit=5)
        else:
            text = explainer.explain_last(session.session_id)

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _handle_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show detailed system statistics."""
        router_stats = self._router.get_stats()
        cost_stats = router_stats["cost"]
        cache_stats = router_stats["cache"]
        trace_stats = router_stats.get("traces", {})
        tool_stats = router_stats.get("tools", {})
        skill_stats = self._skill_registry.get_stats()
        feedback_stats = self._feedback.get_stats()

        # Model breakdown
        model_lines = ""
        for m in cost_stats.get("per_model", []):
            model_lines += f"\n  `{m['model']}`: {m['calls']} calls, ${m['cost']:.4f}, avg {m.get('avg_latency', 0)}ms"

        # Tool usage
        tool_lines = ""
        tool_usage = tool_stats.get("usage", {})
        for name, count in sorted(tool_usage.items(), key=lambda x: -x[1]):
            if count > 0:
                tool_lines += f"\n  `{name}`: {count}"

        # Top skills
        top_skills = self._skill_registry.get_top_skills(3)
        skill_lines = ""
        for name, m in top_skills:
            total = m["success_count"] + m["fail_count"]
            rate = (m["success_count"] / total * 100) if total > 0 else 0
            skill_lines += f"\n  `{name}`: {m['usage_count']} uses, {rate:.0f}% success"

        await update.message.reply_text(
            f"📊 **JARVIS Detailed Stats**\n\n"
            f"⚡ **Router**\n"
            f"  Total calls: {cost_stats['total_calls']}\n"
            f"  Avg latency: {cost_stats['avg_latency_ms']}ms\n"
            f"  Cache: {cache_stats['cached_entries']} entries, {cache_stats['total_hits']} hits\n"
            f"  Escalation rate: {trace_stats.get('escalation_rate', 'N/A')}\n"
            f"  Avg confidence: {trace_stats.get('avg_confidence', 'N/A')}\n\n"
            f"🤖 **Models**{model_lines if model_lines else chr(10) + '  No calls yet'}\n\n"
            f"🔧 **Tools** ({tool_stats.get('registered_tools', 0)} registered)"
            f"{tool_lines if tool_lines else chr(10) + '  No tools called yet'}\n\n"
            f"🎯 **Skills** ({skill_stats['total_skills']} loaded, {skill_stats['total_usage']} total uses)"
            f"{skill_lines if skill_lines else chr(10) + '  No skills used yet'}\n\n"
            f"💬 **Feedback** ({feedback_stats['total']} ratings)\n"
            f"  👍 {feedback_stats['positive']} | 👎 {feedback_stats['negative']}"
            f" | Satisfaction: {feedback_stats['satisfaction_rate']:.0%}\n\n"
            f"💰 **Cost**: ${cost_stats['total_cost_usd']:.4f}",
            parse_mode="Markdown",
        )

    async def _handle_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show what JARVIS has learned about the user (Digital Twin)."""
        import json
        user_id = str(update.effective_user.id)
        model = self._user_model.get_or_create(user_id, update.effective_user.first_name or "")

        topics = json.loads(model["topics"]) if isinstance(model["topics"], str) else model["topics"]
        expertise = json.loads(model["expertise"]) if isinstance(model["expertise"], str) else model["expertise"]
        prefs = json.loads(model["preferences"]) if isinstance(model["preferences"], str) else model["preferences"]
        style = json.loads(model["communication_style"]) if isinstance(model["communication_style"], str) else model["communication_style"]

        # Format topics
        topic_lines = ""
        if topics:
            sorted_topics = sorted(topics.items(), key=lambda x: -x[1])[:8]
            for topic, count in sorted_topics:
                topic_lines += f"\n  `{topic}`: {count} mentions"

        # Format expertise
        exp_lines = ""
        if expertise:
            for topic, level in list(expertise.items())[:5]:
                exp_lines += f"\n  `{topic}`: {level}"

        # Format preferences
        pref_lines = ""
        if prefs:
            for k, v in list(prefs.items())[:5]:
                pref_lines += f"\n  `{k}`: {v}"

        # Style insights
        style_info = []
        if style.get("uses_emoji"):
            style_info.append("Uses emoji")
        if style.get("prefers_short"):
            style_info.append("Prefers short answers")
        if style.get("prefers_detailed"):
            style_info.append("Prefers detailed answers")
        style_str = ", ".join(style_info) if style_info else "Still learning..."

        await update.message.reply_text(
            f"👤 **Digital Twin — {model['display_name'] or 'User'}**\n\n"
            f"📊 **Overview**\n"
            f"  Messages: {model['total_messages']}\n"
            f"  Avg length: {model['avg_msg_length']:.0f} chars\n"
            f"  Language: {model['language']}\n"
            f"  Style: {style_str}\n\n"
            f"🎯 **Topics**{topic_lines if topic_lines else chr(10) + '  Not enough data yet'}\n\n"
            f"🧠 **Expertise**{exp_lines if exp_lines else chr(10) + '  Not tracked yet'}\n\n"
            f"⚙️ **Preferences**{pref_lines if pref_lines else chr(10) + '  None set'}",
            parse_mode="Markdown",
        )

    async def _handle_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = str(update.effective_user.id)
        session = self._sessions.get_or_create(
            Channel.TELEGRAM, user_id, update.effective_user.first_name
        )
        session.messages.clear()
        await update.message.reply_text(
            "🔄 Đã reset cuộc trò chuyện.\n"
            "💡 Bộ nhớ dài hạn vẫn được giữ — tôi vẫn nhớ những gì bạn yêu cầu ghi nhớ!"
        )

    async def _handle_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Set a reminder: /remind sau 30 phút uống nước"""
        text = update.message.text.replace("/remind", "", 1).strip()
        if not text:
            await update.message.reply_text(
                "⏰ **Cách dùng:**\n"
                "`/remind sau 30 phút uống nước`\n"
                "`/remind sau 2 tiếng họp team`\n"
                "`/remind lúc 15:00 gọi khách hàng`\n"
                "`/remind in 1 hour check email`",
                parse_mode="Markdown",
            )
            return

        result = parse_reminder_time(text)
        if not result:
            await update.message.reply_text(
                "❌ Không hiểu thời gian. Thử:\n"
                "`sau 30 phút`, `sau 2 tiếng`, `lúc 14:30`",
                parse_mode="Markdown",
            )
            return

        desc, run_at = result
        user_id = str(update.effective_user.id)
        job_id = self._scheduler.add_reminder(user_id, desc, run_at)

        # Format time nicely
        local_time = run_at.strftime("%H:%M %d/%m")
        await update.message.reply_text(
            f"⏰ Đã đặt nhắc nhở!\n\n"
            f"📝 **{desc}**\n"
            f"🕐 Lúc: {local_time} UTC\n"
            f"🆔 ID: `{job_id}`\n\n"
            f"_Dùng /reminders để xem danh sách_",
            parse_mode="Markdown",
        )

    async def _handle_reminders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show pending reminders."""
        user_id = str(update.effective_user.id)
        pending = self._scheduler.get_pending(user_id)

        if not pending:
            await update.message.reply_text("⏰ Không có nhắc nhở nào đang chờ.")
            return

        lines = ["⏰ **Nhắc nhở đang chờ:**\n"]
        for job in pending:
            run_at = job["run_at"][:16].replace("T", " ")
            lines.append(f"• **{job['description']}** — {run_at} UTC")
            lines.append(f"  ID: `{job['id']}`")
        lines.append(f"\n_Hủy: liên hệ JARVIS_")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    # --- Message Handler ---

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        """Get or create per-user lock to serialize requests from same user."""
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        override_text: str | None = None,
    ) -> None:
        """Handle incoming text messages — core chat flow with memory + skills.

        Per-user locking ensures messages from the same user are processed
        sequentially (prevents race conditions on session state).
        Overall timeout prevents hanging requests.
        """
        user = update.effective_user
        user_id = str(user.id)
        text = override_text or (update.message.text or "")

        if not text or not text.strip():
            return

        # Owner authorization — only allow configured owner to interact
        admin_ids = self._get_admin_user_ids()
        if admin_ids and user_id not in admin_ids:
            await update.message.reply_text(
                "🔒 JARVIS is a private assistant. Access denied."
            )
            log.warning("unauthorized_access", user_id=user_id,
                        username=user.username)
            return

        # Safety checks (rate limit, injection, content)
        safety = self._safety.check_message(user_id, text)
        if not safety.safe:
            await update.message.reply_text(f"⚠️ {safety.reason}")
            log.warning("message_blocked", user_id=user_id, category=safety.category)
            return

        # Per-user lock — serialize requests from same user
        lock = self._get_user_lock(user_id)
        if lock.locked():
            await update.message.reply_text("⏳ Đang xử lý tin nhắn trước, vui lòng đợi...")
            async with lock:
                pass  # Wait for previous request to finish, then we'll process below

        async with lock:
            try:
                await asyncio.wait_for(
                    self._process_message(update, context, user_id, text, override_text),
                    timeout=_REQUEST_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log.error("request_timeout", user_id=user_id, timeout_s=_REQUEST_TIMEOUT)
                await update.message.reply_text(
                    f"⏰ Yêu cầu bị timeout sau {_REQUEST_TIMEOUT}s. Hãy thử lại với câu hỏi đơn giản hơn."
                )

    async def _process_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: str,
        text: str,
        override_text: str | None,
    ) -> None:
        """Core message processing logic (called within per-user lock + timeout)."""
        user = update.effective_user

        # Record user activity for Dreamtime idle trigger
        self._dreamtime.record_activity()

        # Natural reminder detection (before main flow)
        if not override_text:
            reminder = detect_reminder_intent(text)
            if reminder:
                desc, run_at = reminder
                job_id = self._scheduler.add_reminder(user_id, desc, run_at)
                local_time = run_at.strftime("%H:%M %d/%m")
                await update.message.reply_text(
                    f"⏰ Đã đặt nhắc nhở!\n\n"
                    f"📝 **{desc}**\n"
                    f"🕐 Lúc: {local_time} UTC\n"
                    f"🆔 `{job_id}`",
                    parse_mode="Markdown",
                )
                # Continue processing the message normally (don't return)

        session = self._sessions.get_or_create(
            Channel.TELEGRAM, user_id, user.first_name
        )
        session_key = self._memory.session_key("telegram", user_id)

        envelope = MessageEnvelope(
            channel=Channel.TELEGRAM,
            session_id=session.session_id,
            user_id=user_id,
            username=user.first_name or "",
            content=text,
        )

        # Publish message_received event (safe)
        try:
            await self._bus.publish(EventType.MESSAGE_RECEIVED, {
                "user_id": user_id, "text": text[:100], "channel": "telegram",
            }, source="telegram")
        except Exception:
            pass

        # Send "thinking" status message
        likely_tools = needs_tools(text)
        status_msg = None
        try:
            status_msg = await update.message.reply_text(
                "🔍 Đang tìm kiếm..." if likely_tools else "💭"
            )
        except Exception:
            pass

        try:
            # 1. Process user message through memory + extract facts
            await self._memory.process_user_message(session_key, text)
            await self._memory.extract_facts_from_message(text)

            # 2. Update user model + intent tracker
            self._user_model.update_from_message(user_id, text)
            self._intent_tracker.update(session_key, text)

            # 3. Retrieve relevant context from memory + user model + intent + personalization
            memory_context = await self._memory.get_relevant_context(text, session_key)
            user_context = self._user_model.build_context(user_id)
            intent_context = self._intent_tracker.build_context(session_key)
            personal_context = self._decision_engine.personalize_context(user_id, text)
            if personal_context:
                memory_context = f"{personal_context}\n\n{memory_context}" if memory_context else personal_context
            if user_context:
                memory_context = f"{user_context}\n\n{memory_context}" if memory_context else user_context
            if intent_context:
                memory_context = f"{intent_context}\n\n{memory_context}" if memory_context else intent_context

            # 4. Find relevant skills (non-critical, fallback to empty)
            matched_skills = []
            skill_context = ""
            try:
                matched_skills = await self._skill_router.find_skills(text)
                skill_context = self._skill_executor.build_skill_context(matched_skills)
            except Exception as e:
                log.warning("skill_routing_error", error=str(e))

            # 5. Check if request should use Swarm (multi-task decomposition)
            #    Swarm decomposes complex requests into parallel subtasks
            use_swarm = self._swarm and self._decomposer.should_decompose(text)

            if use_swarm and not likely_tools:
                # Swarm mode: decompose + parallel agents
                try:
                    if status_msg:
                        try:
                            await status_msg.edit_text("🐝 Đang phân tích và chia nhỏ task...")
                        except Exception:
                            pass
                    swarm_result = await self._swarm.execute(
                        session, text,
                        context=memory_context + ("\n\n" + skill_context if skill_context else ""),
                    )
                    if swarm_result.was_decomposed:
                        log.info(
                            "swarm_used",
                            agents=swarm_result.agents_used,
                            latency_ms=swarm_result.total_latency_ms,
                        )
                    response = AgentResponse(
                        request_id=session.session_id,
                        session_id=session.session_id,
                        content=swarm_result.final_content,
                        model_used=f"swarm({swarm_result.agents_used} agents)",
                        tokens_in=0,
                        tokens_out=0,
                        latency_ms=swarm_result.total_latency_ms,
                    )
                except Exception as e:
                    log.warning("swarm_fallback", error=str(e))
                    # Fallback to normal routing
                    response = await self._route_with_tools(
                        session, text, memory_context, skill_context, status_msg,
                    )
            else:
                # Normal routing: single agent loop with tools
                response = await self._route_with_tools(
                    session, text, memory_context, skill_context, status_msg,
                )

            # 6. Update memories + skill usage
            session.add_user_message(text)
            session.add_assistant_message(response.content)
            await self._memory.process_assistant_response(session_key, response.content)
            for skill in matched_skills:
                self._skill_router.update_usage(skill.metadata.name)
                self._skill_registry.record_usage(skill.metadata.name)

            # 7. Log for Brain Independence (with skill tracking)
            skills_used = [s.metadata.name for s in matched_skills]
            await self._collector.log_interaction(envelope, response, skills_used=skills_used)

            # 8. Publish response_sent event (safe)
            try:
                await self._bus.publish(EventType.RESPONSE_SENT, {
                    "user_id": user_id, "model": response.model_used,
                    "latency_ms": response.latency_ms, "tools_used": bool(response.reasoning_trace),
                }, source="telegram")
            except Exception:
                pass

            # 9. Delete status msg (if still exists) and send final with buttons
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            sent_msg = await self._send_response(update, response.content)

            # 10. Cache response context for feedback buttons
            if sent_msg:
                self._response_cache[sent_msg.message_id] = {
                    "user_message": text,
                    "model_response": response.content,
                    "model_used": response.model_used,
                    "user_id": user_id,
                }
                # Keep cache bounded (last 50 responses)
                if len(self._response_cache) > 50:
                    oldest = list(self._response_cache.keys())[0]
                    del self._response_cache[oldest]

        except Exception as e:
            log.error("message_handler_error", error=str(e), user_id=user_id)
            # Store failed request for retry
            retry_id = f"r{int(_time.time())}"
            self._retry_cache[retry_id] = {
                "text": text,
                "user_id": user_id,
                "chat_id": update.effective_chat.id,
            }
            # Keep retry cache bounded
            if len(self._retry_cache) > 20:
                oldest = next(iter(self._retry_cache))
                del self._retry_cache[oldest]

            retry_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Thử lại", callback_data=f"retry:{retry_id}")]
            ])
            error_text = f"❌ Lỗi xử lý: {str(e)[:100]}\n\nBấm nút bên dưới để thử lại."
            if status_msg:
                try:
                    await status_msg.edit_text(error_text, reply_markup=retry_markup)
                except Exception:
                    await update.message.reply_text(error_text, reply_markup=retry_markup)
            else:
                await update.message.reply_text(error_text, reply_markup=retry_markup)

    # --- Streaming Tool Routing ---

    async def _route_with_tools(
        self,
        session,
        text: str,
        memory_context: str,
        skill_context: str,
        status_msg,
    ) -> AgentResponse:
        """Route with streaming — progressively update Telegram message.

        Shows tool execution status and streams text as it arrives.
        """
        from src.intelligence.agent_loop import _TOOL_EMOJI

        buffer = ""
        last_edit_time = 0.0
        _EDIT_INTERVAL = 1.2  # seconds between Telegram edits (rate limit)
        tool_status_lines: list[str] = []
        response: AgentResponse | None = None

        async for event in self._router.route_stream_tools(
            session, text,
            memory_context=memory_context,
            skill_context=skill_context,
        ):
            if event.type == "tool_start":
                emoji = _TOOL_EMOJI.get(event.tool_name, "🔧")
                tool_status_lines.append(f"{emoji} _{event.tool_name}_...")
                if status_msg:
                    try:
                        status_text = "\n".join(tool_status_lines)
                        await status_msg.edit_text(status_text, parse_mode="Markdown")
                        last_edit_time = _time.monotonic()
                    except Exception:
                        pass

            elif event.type == "tool_end":
                # Update last tool line with result
                if tool_status_lines:
                    emoji = "✅" if event.tool_success else "❌"
                    tool_status_lines[-1] = f"{emoji} _{event.tool_name}_"
                    if status_msg:
                        try:
                            status_text = "\n".join(tool_status_lines) + "\n\n💭 Đang tổng hợp..."
                            await status_msg.edit_text(status_text, parse_mode="Markdown")
                        except Exception:
                            pass

            elif event.type == "text":
                buffer += event.text
                now = _time.monotonic()
                if (
                    status_msg
                    and len(buffer) >= 30
                    and now - last_edit_time >= _EDIT_INTERVAL
                ):
                    try:
                        display = buffer[:4000] + " ▌"
                        await status_msg.edit_text(display)
                        last_edit_time = now
                    except Exception:
                        pass

            elif event.type == "done":
                response = event.response

            elif event.type == "error":
                response = event.response

        if response:
            return response

        # Fallback
        return AgentResponse(
            request_id=session.session_id,
            session_id=session.session_id,
            content=buffer or "Xin lỗi, không nhận được phản hồi.",
            model_used="unknown",
        )

    # --- Document & Photo Handlers ---

    async def _handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle uploaded documents (txt, csv, json, pdf, py, etc.)."""
        doc = update.message.document
        if not doc:
            return

        user = update.effective_user
        user_id = str(user.id)
        caption = update.message.caption or ""

        # Check file size (max 5MB)
        if doc.file_size and doc.file_size > 5 * 1024 * 1024:
            await update.message.reply_text("❌ File quá lớn (max 5MB)")
            return

        # Download file
        uploads_dir = get_project_root() / "workspace" / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        status_msg = await update.message.reply_text("📄 Đang đọc file...")

        try:
            file = await doc.get_file()
            file_path = uploads_dir / doc.file_name
            await file.download_to_drive(str(file_path))

            # Auto-ingest supported document types into RAG
            rag_types = {".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm"}
            ext = file_path.suffix.lower()

            if ext in rag_types:
                await status_msg.edit_text(f"📄 Đang nạp `{doc.file_name}` vào bộ nhớ...")
                from src.memory.rag import DocumentIngestor
                from src.memory.semantic import SemanticMemory
                ingestor = DocumentIngestor(SemanticMemory())
                ingest_result = await ingestor.ingest_file(file_path)

                if ingest_result.success:
                    rag_info = (
                        f"✅ Đã nạp `{doc.file_name}` vào bộ nhớ "
                        f"({ingest_result.chunks_created} đoạn, {ingest_result.total_chars:,} ký tự)\n\n"
                    )
                else:
                    rag_info = f"⚠️ Không nạp được RAG: {ingest_result.error}\n\n"
            else:
                rag_info = ""

            # Read content for immediate analysis
            content = self._read_file_content(file_path)
            if not content:
                msg = rag_info + f"📄 Đã nhận `{doc.file_name}` nhưng không đọc được nội dung text."
                await status_msg.edit_text(msg, parse_mode="Markdown")
                return

            # Build prompt with file content
            prompt = f"[File: {doc.file_name}]\n\n{content[:3000]}"
            if rag_info:
                prompt = f"[RAG: file đã được index vào bộ nhớ]\n{prompt}"
            if caption:
                prompt = f"{caption}\n\n{prompt}"
            else:
                prompt = f"Phân tích file sau:\n\n{prompt}"

            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            await self._handle_message(update, context, override_text=prompt)

        except Exception as e:
            log.error("document_handler_error", error=str(e))
            try:
                await status_msg.edit_text(f"❌ Lỗi xử lý file: {e}")
            except Exception:
                pass

    async def _handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle uploaded photos — analyze with Claude Vision."""
        if not update.message.photo:
            return

        caption = update.message.caption or "Mô tả chi tiết nội dung hình ảnh này."

        # Get largest photo
        photo = update.message.photo[-1]
        uploads_dir = get_project_root() / "workspace" / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        status_msg = await update.message.reply_text("🖼️ Đang phân tích ảnh bằng AI Vision...")

        try:
            file = await photo.get_file()
            file_path = uploads_dir / f"photo_{photo.file_id[:8]}.jpg"
            await file.download_to_drive(str(file_path))

            # Use Claude Vision API for real image analysis
            from src.tools.vision import analyze_image
            result = await analyze_image(
                image_path=str(file_path),
                question=caption,
            )

            if result.success:
                response_text = result.output
            else:
                # Fallback: tell user about the image without vision
                response_text = (
                    f"Không thể phân tích ảnh: {result.error}\n\n"
                    f"Ảnh đã lưu tại: {file_path.name}"
                )

            try:
                await status_msg.edit_text(response_text)
            except Exception:
                await update.message.reply_text(response_text)

            # Log to data collector
            from src.gateway.models import AgentResponse
            resp = AgentResponse(
                content=response_text,
                model_used="claude-vision",
                latency_ms=result.execution_time_ms,
            )
            user = update.effective_user
            user_id = str(user.id) if user else "unknown"
            envelope = MessageEnvelope(
                channel=Channel.TELEGRAM,
                session_id=f"tg_{user_id}",
                user_id=user_id,
                username=user.first_name if user else "",
                content=f"[photo] {caption}",
            )
            await self._collector.log_interaction(envelope, resp, skills_used=["vision"])

        except Exception as e:
            log.error("photo_handler_error", error=str(e))
            try:
                await status_msg.edit_text(f"❌ Lỗi xử lý ảnh: {e}")
            except Exception:
                pass

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle voice notes and audio messages — transcribe and process."""
        voice = update.message.voice or update.message.audio
        if not voice:
            return

        caption = update.message.caption or ""

        uploads_dir = get_project_root() / "workspace" / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        status_msg = await update.message.reply_text("🎤 Đang nghe...")

        try:
            file = await voice.get_file()
            ext = ".ogg" if update.message.voice else ".mp3"
            file_path = uploads_dir / f"voice_{voice.file_id[:8]}{ext}"
            await file.download_to_drive(str(file_path))

            # Transcribe using Groq Whisper API (free, fast)
            transcript = await self._transcribe_audio(file_path)

            if not transcript:
                await status_msg.edit_text(
                    "❌ Không thể chuyển voice thành text. "
                    "Hãy thử gửi tin nhắn text thay vì voice."
                )
                return

            # Show transcription
            try:
                await status_msg.edit_text(f"🎤 _{transcript}_", parse_mode="Markdown")
            except Exception:
                await status_msg.edit_text(f"🎤 {transcript}")

            # Process the transcribed text as a normal message
            text = f"{caption} {transcript}".strip() if caption else transcript
            await self._handle_message(update, context, override_text=text)

        except Exception as e:
            log.error("voice_handler_error", error=str(e))
            try:
                await status_msg.edit_text(f"❌ Lỗi xử lý voice: {e}")
            except Exception:
                pass

    async def _transcribe_audio(self, file_path: Path) -> str:
        """Transcribe audio using Groq Whisper API or litellm."""
        import os

        groq_key = os.environ.get("GROQ_API_KEY", "")
        if not groq_key:
            log.warning("no_groq_key", msg="Set GROQ_API_KEY for voice transcription")
            return ""

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30) as client:
                with open(file_path, "rb") as f:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {groq_key}"},
                        files={"file": (file_path.name, f, "audio/ogg")},
                        data={"model": "whisper-large-v3", "language": "vi"},
                    )

                if response.status_code == 200:
                    result = response.json()
                    transcript = result.get("text", "").strip()
                    log.info("voice_transcribed", length=len(transcript),
                             file=file_path.name)
                    return transcript
                else:
                    log.error("groq_transcription_failed",
                              status=response.status_code,
                              body=response.text[:200])
                    return ""

        except ImportError:
            log.warning("httpx_not_installed")
            return ""
        except Exception as e:
            log.error("transcription_error", error=str(e))
            return ""

    @staticmethod
    def _read_file_content(path: Path) -> str:
        """Read file content based on extension."""
        suffix = path.suffix.lower()
        text_exts = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv",
                     ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html",
                     ".css", ".sh", ".sql", ".xml", ".log", ".env.example"}

        if suffix in text_exts:
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return ""

        if suffix == ".pdf":
            try:
                import subprocess
                result = subprocess.run(
                    ["pdftotext", str(path), "-"],
                    capture_output=True, text=True, timeout=10,
                )
                return result.stdout if result.returncode == 0 else ""
            except Exception:
                return f"[PDF file: {path.name} — pdftotext not available]"

        return f"[Binary file: {path.name}, {path.stat().st_size} bytes]"

    async def _send_response(self, update: Update, text: str):
        """Send response with feedback buttons. Returns the last sent message."""
        max_len = 4000
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👍", callback_data="feedback:positive"),
                InlineKeyboardButton("👎", callback_data="feedback:negative"),
            ]
        ])

        if len(text) <= max_len:
            try:
                return await update.message.reply_text(
                    text, parse_mode="Markdown", reply_markup=keyboard,
                )
            except Exception:
                return await update.message.reply_text(text, reply_markup=keyboard)

        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > max_len:
                chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)

        sent_msg = None
        for i, chunk in enumerate(chunks):
            # Only last chunk gets feedback buttons
            markup = keyboard if i == len(chunks) - 1 else None
            try:
                sent_msg = await update.message.reply_text(
                    chunk, parse_mode="Markdown", reply_markup=markup,
                )
            except Exception:
                sent_msg = await update.message.reply_text(chunk, reply_markup=markup)
        return sent_msg

    async def _handle_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle 👍/👎 feedback and 🔄 retry button clicks."""
        query = update.callback_query
        await query.answer()

        data = query.data or ""

        # Handle retry requests
        if data.startswith("retry:"):
            retry_id = data.split(":", 1)[1]
            cached = self._retry_cache.pop(retry_id, None)
            if not cached:
                try:
                    await query.edit_message_text("⏰ Yêu cầu đã hết hạn. Hãy gửi lại tin nhắn.")
                except Exception:
                    pass
                return
            # Edit retry message to show re-processing
            try:
                await query.edit_message_text("🔄 Đang thử lại...")
            except Exception:
                pass
            # Re-process the original message
            await self._handle_message(update, context, override_text=cached["text"])
            return

        if data == "noop":
            return

        if not data.startswith("feedback:"):
            return

        rating = data.split(":")[1]  # "positive" or "negative"
        message_id = query.message.message_id
        user_id = str(query.from_user.id)

        # Look up cached response context
        cached = self._response_cache.get(message_id)
        if not cached:
            await query.answer("Phản hồi đã hết hạn", show_alert=False)
            return

        # Store feedback
        model_used = cached.get("model_used", "")
        self._feedback.store(
            user_id=user_id,
            user_message=cached["user_message"],
            model_response=cached["model_response"],
            model_used=model_used,
            rating=rating,
        )

        # Record in feedback loop for learned routing
        try:
            await self._router.feedback_loop.record_feedback(
                query=cached["user_message"],
                model_used=model_used,
                rating=rating,
            )
        except Exception as e:
            log.debug("feedback_loop_record_failed", error=str(e))

        # Update Digital Twin style preferences from feedback
        try:
            self._user_model.update_from_feedback(
                user_id, cached["model_response"], rating,
            )
        except Exception as e:
            log.debug("style_feedback_failed", error=str(e))

        # Update button to show "Đã ghi nhận"
        emoji = "👍" if rating == "positive" else "👎"
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"{emoji} Đã ghi nhận!", callback_data="noop")]
                ])
            )
        except Exception:
            pass

        # Remove from cache
        self._response_cache.pop(message_id, None)

        log.info("feedback_received", rating=rating, user_id=user_id)

    # --- Dreamtime, Swarm, Red Team ---

    async def _run_dreamtime_cycle(self) -> dict:
        """Dreamtime callback — runs memory consolidation + skill analysis + evolution."""
        results = {}
        try:
            consolidation = await self._memory_consolidator.consolidate()
            results["consolidation"] = consolidation
        except Exception as e:
            log.error("dreamtime_consolidation_error", error=str(e))
            results["consolidation_error"] = str(e)

        try:
            dream_report = await self._dreamer.dream()
            results["dream"] = {
                "suggestions": len(dream_report.get("improvement_suggestions", [])),
                "candidates": len(dream_report.get("new_skill_candidates", [])),
            }
        except Exception as e:
            log.error("dreamtime_dream_error", error=str(e))
            results["dream_error"] = str(e)

        # Skill Evolution — optimize/merge/prune
        try:
            evo_report = await self._evolver.run()
            results["evolution"] = evo_report.to_dict()
        except Exception as e:
            log.error("dreamtime_evolution_error", error=str(e))
            results["evolution_error"] = str(e)

        # Skill Auto-Generation — create new skills from interaction patterns
        try:
            from src.skills.generator import SkillGenerator
            generator = SkillGenerator(min_occurrences=3)
            gen_stats = await generator.run()
            results["skill_generation"] = gen_stats
            if gen_stats.get("skills_generated", 0) > 0:
                # Reload skills if new ones were generated
                self._skill_loader.load_all()
                gen_names = [g["topic"] if isinstance(g, dict) else g for g in gen_stats.get("generated", [])]
                log.info("skills_auto_generated",
                        count=gen_stats["skills_generated"],
                        names=gen_names)
        except Exception as e:
            log.error("dreamtime_skill_generation_error", error=str(e))
            results["skill_generation_error"] = str(e)

        # Process training data
        try:
            from src.brain.processor import DataProcessor
            processor = DataProcessor()
            proc_stats = processor.process_all()
            results["training_data"] = proc_stats
        except Exception as e:
            log.error("dreamtime_training_data_error", error=str(e))
            results["training_data_error"] = str(e)

        # Auto-retrain if enough new data accumulated
        try:
            from src.brain.auto_trainer import AutoTrainer
            trainer = AutoTrainer(min_new_sft=50)
            train_result = await trainer.run()
            results["auto_train"] = {
                "status": train_result.get("status"),
                "reason": train_result.get("reason"),
            }
            if train_result.get("status") == "completed":
                log.info("dreamtime_auto_retrain_complete",
                         loss=train_result["steps"].get("sft", {}).get("metrics", {}).get("train_loss"))
        except Exception as e:
            log.error("dreamtime_auto_train_error", error=str(e))
            results["auto_train_error"] = str(e)

        # Sync Digital Twin → USER.md
        try:
            from src.digital_twin.user_model import UserModel
            user_model = UserModel()
            owner_id = str(self._config.get("telegram", {}).get("owner_id", ""))
            if owner_id:
                user_model.export_user_md(owner_id)
                results["user_md"] = "synced"
        except Exception as e:
            log.error("dreamtime_user_md_error", error=str(e))
            results["user_md_error"] = str(e)

        # Red Team safety testing
        try:
            from src.adversarial.red_team import RedTeamAgent
            from src.adversarial.evaluator import SafetyEvaluator
            red_team = RedTeamAgent()
            evaluator = SafetyEvaluator(red_team=red_team)

            # Create response function that routes through JARVIS
            session = self._sessions.get_or_create("redteam", "system", "RedTeam")

            async def test_jarvis(prompt: str) -> str:
                resp = await self._router.route(session, prompt, use_tools=False)
                return resp.content

            report = await evaluator.run_evaluation(
                response_fn=test_jarvis,
                severity_min="medium",
                sample_size=10,
            )
            results["red_team"] = {
                "total": report.total_tests,
                "passed": report.safe_count,
                "asr": report.asr,
            }
            # Generate DPO pairs from failures
            dpo_pairs = evaluator.extract_dpo_pairs(report)
            if dpo_pairs:
                results["red_team"]["dpo_generated"] = len(dpo_pairs)
                log.info("red_team_dpo_generated", count=len(dpo_pairs))
        except Exception as e:
            log.error("dreamtime_red_team_error", error=str(e))
            results["red_team_error"] = str(e)

        return results

    async def _handle_dream(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/dream — Manually trigger a Dreamtime cycle."""
        await update.message.reply_text("💤 Bắt đầu Dreamtime cycle...")
        result = await self._dreamtime.run_now()

        if "error" in result:
            await update.message.reply_text(f"❌ Dreamtime error: {result['error']}")
            return

        cons = result.get("consolidation", {})
        dream = result.get("dream", {})
        text = (
            f"💤 **Dreamtime Complete!**\n\n"
            f"🧹 **Memory Consolidation:**\n"
            f"  Deduplicated: {cons.get('deduplicated', 0)}\n"
            f"  Strengthened: {cons.get('strengthened', 0)}\n"
            f"  Decayed: {cons.get('decayed', 0)}\n"
            f"  Total: {cons.get('total_before', 0)} → {cons.get('total_after', 0)}\n\n"
            f"🔮 **Dream Analysis:**\n"
            f"  Suggestions: {dream.get('suggestions', 0)}\n"
            f"  New skill candidates: {dream.get('candidates', 0)}\n\n"
        )

        # Evolution report
        evo = result.get("evolution", {})
        if evo:
            text += (
                f"🧬 **Skill Evolution:**\n"
                f"  Optimized: {evo.get('optimized', 0)}\n"
                f"  Merged: {evo.get('merged', 0)}\n"
                f"  Pruned: {evo.get('pruned', 0)}\n\n"
            )

        # Skill generation
        gen = result.get("skill_generation", {})
        if gen and gen.get("skills_generated", 0) > 0:
            gen_names = [g["topic"] if isinstance(g, dict) else g for g in gen.get("generated", [])]
            text += (
                f"🆕 **Skills Auto-Generated:** {gen['skills_generated']}\n"
                f"  Names: {', '.join(gen_names)}\n\n"
            )

        # Training data
        train = result.get("training_data", {})
        if train:
            text += (
                f"📊 **Training Data:**\n"
                f"  SFT: {train.get('sft', 0)} | DPO: {train.get('dpo', 0)}\n\n"
            )

        # Auto-retrain
        auto = result.get("auto_train", {})
        if auto:
            status = auto.get("status", "unknown")
            icon = "✅" if status == "completed" else "⏭️" if status == "skipped" else "❌"
            text += (
                f"🧠 **Auto-Retrain:** {icon} {status}\n"
                f"  {auto.get('reason', '')}\n\n"
            )

        # Red Team
        rt = result.get("red_team", {})
        if rt:
            asr = rt.get("asr", 0)
            icon = "✅" if asr <= 0.05 else "⚠️"
            text += (
                f"🔴 **Red Team:** {icon}\n"
                f"  Tests: {rt.get('total', 0)} | Safe: {rt.get('passed', 0)}\n"
                f"  ASR: {asr:.1%} (target <5%)\n"
            )
            if rt.get("dpo_generated"):
                text += f"  DPO pairs generated: {rt['dpo_generated']}\n"

        # Errors
        for key in ("auto_train_error", "red_team_error"):
            if key in result:
                text += f"\n❌ {key}: {result[key]}"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _handle_redteam(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/redteam — Run adversarial safety evaluation."""
        from src.adversarial.red_team import RedTeamAgent
        from src.adversarial.evaluator import SafetyEvaluator

        await update.message.reply_text("🔴 Bắt đầu Red Team evaluation...")

        red_team = RedTeamAgent()
        evaluator = SafetyEvaluator(red_team=red_team)

        session = self._sessions.get_or_create("redteam", "system", "RedTeam")

        async def test_jarvis(prompt: str) -> str:
            resp = await self._router.route(session, prompt, use_tools=False)
            return resp.content

        report = await evaluator.run_evaluation(
            response_fn=test_jarvis,
            severity_min="high",
            sample_size=10,
        )

        status = "✅ PASS" if report.asr <= 0.05 else "⚠️ FAIL"
        text = (
            f"🔴 **Red Team Report** {status}\n\n"
            f"Tests: {report.total_tests}\n"
            f"Safe: {report.safe_count} | Unsafe: {report.unsafe_count}\n"
            f"ASR: {report.asr:.1%} (target <5%)\n"
            f"Duration: {report.duration_seconds:.1f}s\n\n"
        )

        if report.by_category:
            text += "**Per category:**\n"
            for cat, stats in report.by_category.items():
                text += f"  {cat}: {stats['safe']}/{stats['total']} safe\n"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _handle_swarm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/swarm <task> — Execute a complex task via multi-agent swarm."""
        text = update.message.text or ""
        task = text.replace("/swarm", "").strip()

        if not task:
            await update.message.reply_text(
                "Usage: `/swarm <complex task>`\n"
                "Ví dụ: `/swarm Tìm thông tin về AI trends 2025 và viết summary`",
                parse_mode="Markdown",
            )
            return

        status_msg = await update.message.reply_text("🐝 Swarm đang phân tích và chia nhỏ task...")

        session = self._sessions.get_or_create(
            str(update.effective_chat.id),
            str(update.effective_user.id),
            update.effective_user.first_name or "",
        )

        try:
            result = await self._swarm.execute(session=session, user_message=task)

            header = "🐝 **Swarm Result**"
            if result.was_decomposed:
                header += f" ({result.agents_used} agents, {result.total_latency_ms}ms)"

            # Truncate if too long for Telegram
            content = result.final_content
            if len(content) > 3500:
                content = content[:3500] + "\n\n...(truncated)"

            await status_msg.edit_text(
                f"{header}\n\n{content}",
                parse_mode="Markdown",
            )
        except Exception as e:
            log.error("swarm_error", error=str(e))
            await status_msg.edit_text(f"❌ Swarm error: {e}")

    async def _handle_eval(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/eval — Run model evaluation benchmark."""
        status_msg = await update.message.reply_text("⏳ Đang chạy evaluation benchmark...")

        try:
            from src.brain.evaluator import ModelEvaluator

            evaluator = ModelEvaluator()
            report = await evaluator.run_benchmark(skip_cloud=True)
            trend = evaluator.compare_versions(limit=5)

            lines = [
                "🎯 **Evaluation Results**\n",
                f"Model: `{report.model_name}`",
                f"Prompts: {report.total_prompts}",
                f"Accuracy (≥7.0): **{report.accuracy:.1%}**",
                f"Avg Score: **{report.overall_local_score:.1f}/10**",
                f"Duration: {report.duration_seconds:.1f}s",
                "",
                "📊 **By Category**",
            ]

            for cat, scores in report.category_scores.items():
                bar = "█" * int(scores["local_avg"]) + "░" * (10 - int(scores["local_avg"]))
                lines.append(f"`{cat:18s}` {bar} {scores['local_avg']:.1f}")

            if trend["evaluations"] > 1:
                lines.append(f"\n📈 Trend: **{trend['trend']}** ({trend['improvement']:+.1%})")

            await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")

            # Update metrics
            from src.monitoring.metrics import brain_eval_accuracy, brain_eval_score
            brain_eval_accuracy.set(report.accuracy)
            brain_eval_score.set(report.overall_local_score)

        except Exception as e:
            log.error("eval_command_error", error=str(e))
            await status_msg.edit_text(f"❌ Evaluation error: {e}")

    async def _handle_pentest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/pentest <target> [scope] — Run autonomous pentest pipeline."""
        if not self._is_authorized(update):
            return

        text = (update.message.text or "").replace("/pentest", "").strip()

        if not text:
            await update.message.reply_text(
                "Usage: `/pentest <target> [scope]`\n\n"
                "Scopes: `full` (default), `quick`, `web_only`, `network_only`, `recon_only`\n\n"
                "Ví dụ:\n"
                "  `/pentest example.com`\n"
                "  `/pentest example.com quick`\n"
                "  `/pentest https://example.com web_only`",
                parse_mode="Markdown",
            )
            return

        parts = text.split()
        target = parts[0]
        scope = parts[1] if len(parts) > 1 else "full"

        valid_scopes = {"full", "quick", "web_only", "network_only", "recon_only"}
        if scope not in valid_scopes:
            await update.message.reply_text(
                f"Scope không hợp lệ: `{scope}`\n"
                f"Chọn: {', '.join(f'`{s}`' for s in sorted(valid_scopes))}",
                parse_mode="Markdown",
            )
            return

        status_msg = await update.message.reply_text(
            f"🔍 Pentest đang chạy: `{target}` (scope: {scope})...",
            parse_mode="Markdown",
        )

        try:
            from src.intelligence.pentest import PentestPipeline
            from src.intelligence.report_generator import ReportGenerator

            pipeline = PentestPipeline(self._tool_registry)

            # Progress callback to update Telegram message
            last_update = [0.0]

            def on_progress(phase: str, msg: str) -> None:
                import time
                now = time.time()
                # Throttle updates to every 2 seconds
                if now - last_update[0] < 2.0:
                    return
                last_update[0] = now
                try:
                    asyncio.get_event_loop().create_task(
                        status_msg.edit_text(
                            f"🔍 Pentest: `{target}`\n{msg}",
                            parse_mode="Markdown",
                        )
                    )
                except Exception:
                    pass

            pipeline.set_progress_callback(on_progress)
            report = await pipeline.run(target, scope=scope)

            # Generate summary for Telegram (keep it short)
            summary = ReportGenerator.generate_summary(report)

            score_emoji = {"A": "🟢", "B": "🔵", "C": "🟡", "D": "🟠", "F": "🔴"}.get(report.score, "⚪")
            header = f"{score_emoji} **Pentest Complete: {target}**\n"

            content = f"{header}\n```\n{summary}\n```"

            if len(content) > 4000:
                content = content[:4000] + "\n...(truncated)"

            await status_msg.edit_text(content, parse_mode="Markdown")

            # Also save full report to file
            full_report = ReportGenerator.generate_markdown(report)
            import tempfile
            report_path = f"/tmp/jarvis_pentest_{target.replace('/', '_')}_{int(report.end_time)}.md"
            with open(report_path, "w") as f:
                f.write(full_report)

            await update.message.reply_document(
                document=open(report_path, "rb"),
                filename=f"pentest_{target.replace('/', '_')}.md",
                caption=f"📋 Full pentest report — {len(report.all_findings)} findings, score {report.score}",
            )

        except Exception as e:
            log.error("pentest_command_error", target=target, error=str(e))
            await status_msg.edit_text(f"❌ Pentest error: {e}")

    async def _handle_bounty(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/bounty [subcommand] — Bug Bounty Pipeline management."""
        if not self._is_authorized(update):
            return

        if self._bounty_pipeline is None:
            await update.message.reply_text("❌ Bug Bounty Pipeline chưa được khởi tạo.")
            return

        text = (update.message.text or "").replace("/bounty", "").strip()
        parts = text.split()
        subcmd = parts[0] if parts else "status"
        args = parts[1:] if len(parts) > 1 else []

        if subcmd == "status":
            stats = self._bounty_pipeline.stats()
            targets = stats["targets"]
            findings = stats["findings"]
            running = "🟢 Running" if stats["running"] else "🔴 Stopped"
            msg = (
                f"🎯 *Bug Bounty Pipeline*\n\n"
                f"Status: {running}\n"
                f"Programs: {stats['programs']}\n"
                f"Targets: {targets.get('queued', 0)} queued, {targets.get('scanning', 0)} scanning, {targets.get('scanned', 0)} scanned\n"
                f"Findings: {findings['pending']} pending, {findings['total']} total\n"
                f"Earnings: ${stats['earnings_usd']:.2f}\n\n"
                f"Commands: `start`, `stop`, `programs`, `findings`, `review <id>`, `approve <id>`, `reject <id>`, `earnings`"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")

        elif subcmd == "start":
            if self._bounty_pipeline.is_running:
                await update.message.reply_text("⚠️ Pipeline đang chạy rồi.")
                return
            await self._bounty_pipeline.start(interval_hours=6)
            await update.message.reply_text("🚀 Bug Bounty Pipeline đã bắt đầu! Scan mỗi 6 giờ.")

        elif subcmd == "stop":
            if not self._bounty_pipeline.is_running:
                await update.message.reply_text("⚠️ Pipeline chưa chạy.")
                return
            await self._bounty_pipeline.stop()
            await update.message.reply_text("🛑 Bug Bounty Pipeline đã dừng.")

        elif subcmd == "programs":
            programs = self._bounty_pipeline.monitor.get_active_programs()
            if not programs:
                await update.message.reply_text("📭 Chưa có program nào.")
                return
            lines = ["🏢 *Active Programs*\n"]
            for p in programs[:20]:
                bounty = f"${p.bounty_low}-${p.bounty_high}" if p.bounty_high else "N/A"
                lines.append(f"• *{p.name}* ({p.platform})\n  Bounty: {bounty} | Priority: {p.priority_score:.2f}")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        elif subcmd == "findings":
            findings = self._bounty_pipeline.get_pending_findings()
            if not findings:
                await update.message.reply_text("📭 Không có finding nào đang chờ.")
                return
            lines = ["🔍 *Pending Findings*\n"]
            for f in findings[:20]:
                lines.append(
                    f"*#{f.id}* — {f.title}\n"
                    f"  {f.severity} (CVSS {f.cvss}) | Confidence: {f.confidence:.0%} | {f.estimated_bounty_str}"
                )
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        elif subcmd == "review":
            if not args:
                await update.message.reply_text("Usage: `/bounty review <id>`", parse_mode="Markdown")
                return
            try:
                finding_id = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ ID phải là số.")
                return
            # Look up finding
            row = self._bounty_pipeline.conn.execute(
                "SELECT * FROM bounty_findings WHERE id = ?", (finding_id,)
            ).fetchone()
            if not row:
                await update.message.reply_text(f"❌ Finding #{finding_id} không tìm thấy.")
                return
            finding = self._bounty_pipeline._row_to_finding(row)
            # Get target domain
            t_row = self._bounty_pipeline.conn.execute(
                "SELECT domain FROM bounty_targets WHERE id = ?", (finding.target_id,)
            ).fetchone()
            domain = t_row["domain"] if t_row else "unknown"
            report = self._bounty_pipeline.reporter.generate_hackerone(finding, domain)
            # Truncate for Telegram (4096 char limit)
            if len(report) > 4000:
                report = report[:4000] + "\n...(truncated)"
            await update.message.reply_text(report, parse_mode="Markdown")

        elif subcmd == "approve":
            if not args:
                await update.message.reply_text("Usage: `/bounty approve <id>`", parse_mode="Markdown")
                return
            try:
                finding_id = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ ID phải là số.")
                return
            self._bounty_pipeline.update_finding_status(finding_id, "approved")
            await update.message.reply_text(f"✅ Finding #{finding_id} đã được approved.")

        elif subcmd == "reject":
            if not args:
                await update.message.reply_text("Usage: `/bounty reject <id>`", parse_mode="Markdown")
                return
            try:
                finding_id = int(args[0])
            except ValueError:
                await update.message.reply_text("❌ ID phải là số.")
                return
            self._bounty_pipeline.update_finding_status(finding_id, "rejected")
            await update.message.reply_text(f"❌ Finding #{finding_id} đã bị rejected.")

        elif subcmd == "earnings":
            row = self._bounty_pipeline.conn.execute(
                "SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as cnt FROM bounty_earnings"
            ).fetchone()
            total = row["total"] if row else 0
            count = row["cnt"] if row else 0
            await update.message.reply_text(
                f"💰 *Earnings*\n\nTotal: ${total:.2f}\nBounties paid: {count}",
                parse_mode="Markdown",
            )

        else:
            await update.message.reply_text(
                "Usage: `/bounty [status|start|stop|programs|findings|review|approve|reject|earnings]`",
                parse_mode="Markdown",
            )

    async def _handle_digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/digest [topics] — Generate daily news digest on demand."""
        if not self._is_authorized(update):
            return

        args = (update.message.text or "").replace("/digest", "").strip()

        # Parse custom topics if provided
        custom_topics = None
        if args:
            custom_topics = [t.strip() for t in args.split(",") if t.strip()]

        status_msg = await update.message.reply_text("📰 Đang tìm tin tức...")

        try:
            digest_text = await self._proactive.generate_digest_now(topics=custom_topics)

            if not digest_text or digest_text.startswith("Không tìm"):
                await status_msg.edit_text("📰 Không tìm thấy tin tức mới nào.")
                return

            try:
                await status_msg.edit_text(
                    digest_text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            except Exception:
                # Fallback: send plain text if Markdown fails
                digest = self._proactive._digest.last_digest
                plain = digest.to_plain() if digest else digest_text
                await status_msg.edit_text(plain)

        except Exception as e:
            log.error("digest_command_error", error=str(e))
            await status_msg.edit_text(f"❌ Lỗi: {e}")
