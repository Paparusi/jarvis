"""Company Operations Engine — scheduler, autonomous bridges, KPI aggregation.

Makes JARVIS Company a real company by:
1. Scheduling daily routines for each department
2. Bridging autonomous systems (TradingBrain, BountyPipeline) to workers
3. Aggregating KPIs and storing reports
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.company.kpi_store import KPIStore
from src.company.worker_store import MetricsStore, TaskStore
from src.gateway.event_bus import get_event_bus
from src.utils.logging import get_logger

log = get_logger("company.ops_engine")

# ── Schedule Definition ─────────────────────────────────────────────────


@dataclass
class ScheduledRoutine:
    id: str
    department: str
    name: str
    instruction: str
    hour: int               # UTC hour (-1 for hourly)
    minute: int = 0
    worker_id: str | None = None  # preferred worker
    frequency: str = "daily"      # daily | hourly | weekly
    priority: int = 5             # 1=highest
    weekday: int | None = None    # 0=Mon..6=Sun (for weekly)
    hour_range: tuple[int, int] | None = None  # for hourly
    report_type: str = ""         # non-empty → save result as report


DAILY_SCHEDULE: list[ScheduledRoutine] = [
    # ── Finance ──────────────────────────────────────────────────────
    ScheduledRoutine(
        id="finance.morning_scan",
        department="finance",
        worker_id="finance.market_analyst",
        hour=7, minute=0,
        name="Morning Market Scan",
        instruction=(
            "Morning XAUUSD analysis. Check overnight moves, key support/resistance levels, "
            "SMC zones, and provide session bias with reasoning. "
            "Use mt5_candles, mt5_analyze, mt5_smc, and technical_indicators."
        ),
        report_type="morning_scan",
        priority=3,
    ),
    ScheduledRoutine(
        id="finance.hourly_monitor",
        department="finance",
        worker_id="finance.market_analyst",
        hour=-1,
        hour_range=(8, 22),
        name="Hourly Price Monitor",
        instruction=(
            "Quick XAUUSD update: current price, distance to key levels, any zone breaches. "
            "Keep it brief. Use mt5_price and market_session."
        ),
        frequency="hourly",
        priority=6,
    ),
    ScheduledRoutine(
        id="finance.eod_report",
        department="finance",
        worker_id="finance.trader",
        hour=22, minute=0,
        name="EOD Trading Report",
        instruction=(
            "End-of-day trading summary. Include: trades taken today, P&L, win rate, "
            "risk budget usage, key market observations. Use mt5_history, mt5_account, mt5_positions."
        ),
        report_type="eod_report",
        priority=2,
    ),

    # ── Security ─────────────────────────────────────────────────────
    ScheduledRoutine(
        id="security.daily_monitor",
        department="security",
        worker_id="security.researcher",
        hour=8, minute=30,
        name="Daily Vulnerability Monitor",
        instruction=(
            "Check for new critical CVEs and security advisories affecting: "
            "Python, Node.js, Linux kernel, Docker, common web frameworks. "
            "Use cve_lookup and web_search. Summarize top 3 findings."
        ),
        report_type="vuln_monitor",
        priority=3,
    ),
    ScheduledRoutine(
        id="security.weekly_recon",
        department="security",
        worker_id="security.pen_tester",
        weekday=1,  # Monday
        hour=9, minute=0,
        name="Weekly Recon Scan",
        frequency="weekly",
        instruction=(
            "Run reconnaissance on priority targets. Enumerate subdomains, "
            "check HTTP headers, detect technologies, look for new assets. "
            "Use subdomain_enum, http_headers, tech_detect."
        ),
        report_type="weekly_recon",
        priority=3,
    ),

    # ── Engineering ──────────────────────────────────────────────────
    ScheduledRoutine(
        id="engineering.health_check",
        department="engineering",
        worker_id="engineering.developer",
        hour=9, minute=30,
        name="Code Health Check",
        instruction=(
            "Run code quality check on the JARVIS codebase at ~/projects/jarvis. "
            "Check for: complexity hotspots, potential bugs, test coverage. "
            "Use ast_analyze and complexity_check. Keep report concise."
        ),
        report_type="code_health",
        priority=4,
    ),
    ScheduledRoutine(
        id="engineering.dep_audit",
        department="engineering",
        worker_id="engineering.devops",
        weekday=3,  # Wednesday
        hour=10, minute=0,
        name="Dependency Audit",
        frequency="weekly",
        instruction=(
            "Audit Python dependencies for security vulnerabilities and outdated packages. "
            "Use run_python to check pip list --outdated. Summarize critical updates needed."
        ),
        report_type="dep_audit",
        priority=4,
    ),

    # ── Research ─────────────────────────────────────────────────────
    ScheduledRoutine(
        id="research.daily_digest",
        department="research",
        worker_id="research.product_researcher",
        hour=7, minute=30,
        name="Daily News Digest",
        instruction=(
            "Search for today's top news in: AI agents, cryptocurrency, cybersecurity, fintech. "
            "Summarize top 5 stories with key takeaways. Use web_search and deep_search."
        ),
        report_type="daily_digest",
        priority=3,
    ),
    ScheduledRoutine(
        id="research.weekly_analysis",
        department="research",
        worker_id="research.data_analyst",
        weekday=5,  # Friday
        hour=14, minute=0,
        name="Weekly Trend Analysis",
        frequency="weekly",
        instruction=(
            "Analyze weekly trends in AI assistant space. Compare: Claude, GPT, Gemini, "
            "open-source alternatives. Focus on new features and market moves. "
            "Use web_search and browse_web."
        ),
        report_type="trend_analysis",
        priority=4,
    ),

    # ── Sales ─────────────────────────────────────────────────────────
    ScheduledRoutine(
        id="sales.pipeline_review",
        department="sales",
        worker_id="sales.account_exec",
        hour=9, minute=0,
        name="Sales Pipeline Review",
        instruction=(
            "Review CRM pipeline. Check leads by status, follow up on stale leads, "
            "identify deals close to closing. Use crm_pipeline and crm_search. "
            "Summarize: total leads, conversion rate, deals to follow up today."
        ),
        report_type="pipeline_review",
        priority=3,
    ),
    ScheduledRoutine(
        id="sales.lead_prospecting",
        department="sales",
        worker_id="sales.lead_gen",
        hour=10, minute=0,
        name="Lead Prospecting",
        instruction=(
            "Search for new potential customers interested in AI assistants, "
            "automation tools, or trading bots. Use web_search and deep_search "
            "to find companies/individuals that could benefit from our services. "
            "Add promising leads to CRM with crm_add_lead."
        ),
        report_type="lead_prospecting",
        priority=4,
    ),

    # ── Marketing ────────────────────────────────────────────────────
    ScheduledRoutine(
        id="marketing.social_post",
        department="marketing",
        worker_id="marketing.content_creator",
        hour=11, minute=0,
        name="Daily Social Content",
        instruction=(
            "Create and post engaging content about AI, automation, or fintech. "
            "Write a tweet about a trending topic in AI/trading/cybersecurity. "
            "Use twitter_search to find trending topics, then twitter_post to share. "
            "Keep it informative and professional."
        ),
        report_type="social_post",
        priority=4,
    ),
    ScheduledRoutine(
        id="marketing.social_monitor",
        department="marketing",
        worker_id="marketing.social_manager",
        hour=-1,
        hour_range=(9, 21),
        name="Social Media Monitor",
        frequency="hourly",
        instruction=(
            "Monitor social media for mentions of JARVIS, AI agents, and relevant keywords. "
            "Use social_monitor to track brand mentions. Report any significant engagement "
            "or trending discussions we should participate in."
        ),
        priority=6,
    ),
    ScheduledRoutine(
        id="marketing.weekly_analytics",
        department="marketing",
        worker_id="marketing.social_manager",
        weekday=5,  # Friday
        hour=16, minute=0,
        name="Weekly Marketing Report",
        frequency="weekly",
        instruction=(
            "Weekly marketing performance report. Use social_analytics to check brand presence. "
            "Summarize: social engagement trends, best performing content, "
            "audience growth, recommendations for next week."
        ),
        report_type="marketing_weekly",
        priority=3,
    ),

    # ── Operations ───────────────────────────────────────────────────
    ScheduledRoutine(
        id="operations.daily_standup",
        department="operations",
        worker_id="operations.office_manager",
        hour=8, minute=0,
        name="Daily Standup Summary",
        instruction=(
            "Generate daily standup report. Summarize: what was accomplished yesterday, "
            "what's planned for today, any blockers or issues. Format as a brief company update."
        ),
        report_type="daily_standup",
        priority=2,
    ),
    ScheduledRoutine(
        id="operations.eod_kpi",
        department="operations",
        worker_id="operations.office_manager",
        hour=21, minute=0,
        name="EOD KPI Summary",
        instruction=(
            "Compile today's company KPIs: tasks completed per department, total API costs, "
            "trading P&L, system uptime, any incidents. Format as a metrics dashboard."
        ),
        report_type="kpi_summary",
        priority=3,
    ),
]

# ── Engine ────────────────────────────────────────────────────────────


class CompanyOpsEngine:
    """Orchestrates daily operations: scheduling, bridges, KPIs."""

    def __init__(
        self,
        worker_registry: Any = None,
        trading_brain: Any = None,
        bounty_pipeline: Any = None,
        proactive_engine: Any = None,
    ) -> None:
        self._worker_registry = worker_registry
        self._trading_brain = trading_brain
        self._bounty_pipeline = bounty_pipeline
        self._proactive_engine = proactive_engine

        self._task_store = TaskStore()
        self._kpi_store = KPIStore()
        self._metrics_store = MetricsStore()

        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._executed_today: set[str] = set()  # routine IDs executed today
        self._last_date: str = ""

        log.info("ops_engine_created")

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start scheduler loop and wire autonomous system bridges."""
        self._running = True
        self._wire_bridges()
        self._loop_task = asyncio.create_task(self._scheduler_loop())
        log.info("ops_engine_started")

        try:
            bus = get_event_bus()
            asyncio.create_task(bus.publish(
                "company_ops_started",
                {"routines": len(DAILY_SCHEDULE)},
                source="ops_engine",
            ))
        except Exception:
            pass

    async def stop(self) -> None:
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        log.info("ops_engine_stopped")

    # ── Scheduler Loop ────────────────────────────────────────────────

    async def _scheduler_loop(self) -> None:
        """Check every 60 seconds for routines that should fire."""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                today = now.strftime("%Y-%m-%d")

                # Reset daily tracking at midnight
                if today != self._last_date:
                    self._executed_today.clear()
                    self._last_date = today

                for routine in DAILY_SCHEDULE:
                    if self._should_run(routine, now):
                        await self._dispatch_routine(routine)

            except Exception as e:
                log.error("scheduler_error", error=str(e))

            await asyncio.sleep(60)

    def _should_run(self, r: ScheduledRoutine, now: datetime) -> bool:
        """Check if a routine should run at the current time."""
        rid = r.id

        # Weekly: check weekday
        if r.frequency == "weekly":
            if r.weekday is not None and now.weekday() != r.weekday:
                return False

        # Hourly: check range and per-hour dedup
        if r.frequency == "hourly":
            if r.hour_range:
                if not (r.hour_range[0] <= now.hour < r.hour_range[1]):
                    return False
            hour_key = f"{rid}:{now.hour}"
            if hour_key in self._executed_today:
                return False
            if now.minute != 0:  # only at top of hour
                return False
            return True

        # Daily/weekly: check time and dedup
        if rid in self._executed_today:
            return False
        if now.hour != r.hour:
            return False
        if abs(now.minute - r.minute) > 1:  # 1-minute tolerance
            return False

        return True

    async def _dispatch_routine(self, r: ScheduledRoutine) -> None:
        """Create a task in TaskStore for a scheduled routine."""
        # Avoid task flooding: skip if department already has 3+ pending
        pending = self._task_store.get_pending_count(r.department)
        if pending >= 3:
            log.warning("routine_skipped_queue_full", routine=r.id, pending=pending)
            return

        context = {
            "source": "ops_engine",
            "routine_id": r.id,
            "routine_name": r.name,
        }
        if r.report_type:
            context["report_type"] = r.report_type
            context["title"] = r.name

        task_id = self._task_store.create_task(
            department=r.department,
            instruction=r.instruction,
            worker_id=r.worker_id,
            priority=r.priority,
            context=context,
        )

        # Mark as executed
        if r.frequency == "hourly":
            now = datetime.now(timezone.utc)
            self._executed_today.add(f"{r.id}:{now.hour}")
        else:
            self._executed_today.add(r.id)

        self._kpi_store.log_schedule_execution(r.id, task_id)

        log.info("routine_dispatched", routine=r.id, task_id=task_id, department=r.department)

        try:
            bus = get_event_bus()
            asyncio.create_task(bus.publish(
                "company_ops_task_created",
                {
                    "routine_id": r.id,
                    "routine_name": r.name,
                    "department": r.department,
                    "task_id": task_id,
                },
                source="ops_engine",
            ))
        except Exception:
            pass

    # ── Autonomous System Bridges ─────────────────────────────────────

    def _wire_bridges(self) -> None:
        """Connect autonomous systems to company workers via EventBus."""
        self._wire_trading_brain()
        self._wire_bounty_pipeline()
        self._wire_proactive_engine()

    def _wire_trading_brain(self) -> None:
        if not self._trading_brain:
            return
        original_cb = getattr(self._trading_brain, "_notify_cb", None)

        async def _combined(message: str) -> None:
            if original_cb:
                try:
                    await original_cb(message)
                except Exception:
                    pass
            await self._on_trading_alert(message)

        self._trading_brain.on_notify(_combined)
        log.info("bridge_wired", system="trading_brain")

    def _wire_bounty_pipeline(self) -> None:
        if not self._bounty_pipeline:
            return
        original_cb = getattr(self._bounty_pipeline, "_notify_callback", None)

        async def _combined(finding: Any, report: str) -> None:
            if original_cb:
                try:
                    result = original_cb(finding, report)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
            await self._on_bounty_finding(finding, report)

        self._bounty_pipeline.set_notify_callback(_combined)
        log.info("bridge_wired", system="bounty_pipeline")

    def _wire_proactive_engine(self) -> None:
        if not self._proactive_engine:
            return
        # ProactiveEngine.on_notify appends to a list, so additive
        self._proactive_engine.on_notify(self._on_proactive_insight)
        log.info("bridge_wired", system="proactive_engine")

    async def _on_trading_alert(self, message: str) -> None:
        """TradingBrain notification -> Finance worker task."""
        significant = ["PLAN", "FILLED", "KILL", "DAILY", "zone", "ENTER", "SKIP", "P&L", "ALERT"]
        if not any(kw.lower() in message.lower() for kw in significant):
            return
        pending = self._task_store.get_pending_count("finance")
        if pending >= 3:
            return
        self._task_store.create_task(
            department="finance",
            instruction=f"Trading alert. Analyze and respond:\n\n{message[:500]}",
            worker_id="finance.market_analyst",
            priority=2,
            context={"source": "ops_engine", "bridge": "trading_brain", "report_type": "trading_alert", "title": "Trading Alert"},
        )
        log.info("bridge_task_created", bridge="trading_brain")

    async def _on_bounty_finding(self, finding: Any, report: str) -> None:
        """BountyPipeline finding -> Security worker task."""
        pending = self._task_store.get_pending_count("security")
        if pending >= 3:
            return
        self._task_store.create_task(
            department="security",
            instruction=f"New vulnerability finding from bounty scanner. Review and validate:\n\n{report[:800]}\n\nProvide: severity assessment, false positive check, next steps.",
            worker_id="security.researcher",
            priority=2,
            context={"source": "ops_engine", "bridge": "bounty_pipeline", "report_type": "bounty_finding", "title": "Bounty Finding Review"},
        )
        log.info("bridge_task_created", bridge="bounty_pipeline")

    async def _on_proactive_insight(self, insight: Any) -> None:
        """ProactiveEngine insight -> Research worker task."""
        pending = self._task_store.get_pending_count("research")
        if pending >= 3:
            return
        title = getattr(insight, "title", str(insight)[:80])
        content = getattr(insight, "content", str(insight)[:500])
        itype = getattr(insight, "type", "insight")
        self._task_store.create_task(
            department="research",
            instruction=f"Proactive insight received. Elaborate and research:\n\nType: {itype}\nTitle: {title}\nContent: {content}\n\nProvide detailed analysis with sources.",
            worker_id="research.product_researcher",
            priority=4,
            context={"source": "ops_engine", "bridge": "proactive_engine", "report_type": "proactive_insight", "title": title},
        )
        log.info("bridge_task_created", bridge="proactive_engine")

    # ── KPI Aggregation ───────────────────────────────────────────────

    def get_kpis(self) -> dict:
        """Aggregate company KPIs from all sources."""
        all_metrics = self._metrics_store.get_all()

        total_completed = sum(m["tasks_completed"] for m in all_metrics)
        total_failed = sum(m["tasks_failed"] for m in all_metrics)
        total_cost = sum(m["total_cost"] for m in all_metrics)

        # Trading P&L
        trading_pnl = 0.0
        if self._trading_brain:
            try:
                status = self._trading_brain.get_status()
                risk = status.get("risk", {})
                state = risk.get("state", {})
                trading_pnl = state.get("daily_pnl", 0.0)
            except Exception:
                pass

        # Department breakdown
        dept_kpis: dict[str, dict] = {}
        for dept in ["finance", "security", "engineering", "research", "operations", "sales", "marketing"]:
            dept_m = [m for m in all_metrics if m["worker_id"].startswith(dept)]
            dept_kpis[dept] = {
                "tasks_completed": sum(m["tasks_completed"] for m in dept_m),
                "tasks_failed": sum(m["tasks_failed"] for m in dept_m),
                "cost": round(sum(m["total_cost"] for m in dept_m), 4),
                "reports_today": self._kpi_store.get_today_reports_count(dept),
            }

        # Schedule progress
        sched_log = self._kpi_store.get_schedule_log()
        routines_done = len([s for s in sched_log if s["status"] == "completed"])
        routines_dispatched = len(sched_log)

        return {
            "revenue": {
                "trading_pnl": round(trading_pnl, 2),
                "total": round(trading_pnl, 2),
            },
            "costs": {
                "api_cost": round(total_cost, 4),
                "daily_budget": 10.0,
                "budget_used_pct": round((total_cost / 10.0) * 100, 1),
            },
            "productivity": {
                "tasks_completed": total_completed,
                "tasks_failed": total_failed,
                "success_rate": round(
                    (total_completed / max(total_completed + total_failed, 1)) * 100, 1
                ),
                "reports_today": self._kpi_store.get_today_reports_count(),
            },
            "schedule": {
                "routines_dispatched": routines_dispatched,
                "routines_done": routines_done,
            },
            "departments": dept_kpis,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_kpi_history(self, metric: str, days: int = 30) -> list[dict]:
        return self._kpi_store.get_kpi_history(metric, days)

    def get_reports(self, limit: int = 20, department: str | None = None) -> list[dict]:
        return self._kpi_store.get_reports(limit=limit, department=department)

    def get_schedule(self) -> list[dict]:
        """Return today's schedule with execution status."""
        now = datetime.now(timezone.utc)
        sched_log = self._kpi_store.get_schedule_log()
        executed_ids = {s["routine_id"] for s in sched_log}

        result = []
        for r in DAILY_SCHEDULE:
            # Skip weekly routines not due today
            if r.frequency == "weekly" and r.weekday is not None and now.weekday() != r.weekday:
                continue
            # Skip hourly in summary (too many)
            if r.frequency == "hourly":
                continue

            status = "done" if r.id in executed_ids else ("next" if now.hour <= r.hour else "pending")
            result.append({
                "id": r.id,
                "name": r.name,
                "department": r.department,
                "hour": r.hour,
                "minute": r.minute,
                "status": status,
                "frequency": r.frequency,
            })

        result.sort(key=lambda x: (x["hour"], x["minute"]))
        return result
