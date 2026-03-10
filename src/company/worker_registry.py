"""WorkerRegistry and CostGuard -- manages all workers and enforces budgets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.company.departments import Department
from src.company.worker import Worker
from src.company.worker_store import MetricsStore
from src.intelligence.prompt_assembler import PromptAssembler
from src.metacognition.tracer import ReasoningTracer
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("company.worker_registry")


# ---------------------------------------------------------------------------
# CostGuard -- daily budget enforcement
# ---------------------------------------------------------------------------


class CostGuard:
    """Tracks per-worker and global daily spending, blocking execution when over budget."""

    MAX_DAILY_COST: float = 10.0
    MAX_TASK_COST: float = 2.0
    MAX_WORKER_DAILY_COST: float = 3.0

    def __init__(self) -> None:
        self._daily_totals: dict[str, float] = {}
        self._last_reset: str = _today_utc()

    def can_execute(self, worker_id: str) -> bool:
        """Return True if the worker is within both per-worker and global limits."""
        self._maybe_reset()
        worker_total = self._daily_totals.get(worker_id, 0.0)
        global_total = sum(self._daily_totals.values())
        return (
            worker_total < self.MAX_WORKER_DAILY_COST
            and global_total < self.MAX_DAILY_COST
        )

    def log_cost(self, worker_id: str, cost: float) -> None:
        """Record a cost for a worker."""
        self._maybe_reset()
        self._daily_totals[worker_id] = self._daily_totals.get(worker_id, 0.0) + cost

    def get_daily_usage(self) -> dict[str, Any]:
        """Return a summary of today's spending."""
        self._maybe_reset()
        return {
            "total": round(sum(self._daily_totals.values()), 4),
            "limit": self.MAX_DAILY_COST,
            "per_worker": dict(self._daily_totals),
        }

    def _maybe_reset(self) -> None:
        """Reset daily totals if the UTC date has changed."""
        today = _today_utc()
        if today != self._last_reset:
            self._daily_totals.clear()
            self._last_reset = today


def _today_utc() -> str:
    """Return current UTC date as YYYY-MM-DD string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Worker roster -- the 10 workers that comprise the JARVIS company
# ---------------------------------------------------------------------------

WORKER_ROSTER: list[dict[str, Any]] = [
    # Finance (3)
    {
        "worker_id": "finance.market_analyst",
        "name": "Market Analyst",
        "department": "finance",
        "role": (
            "Bạn là Market Analyst chuyên phân tích XAUUSD.\n"
            "Nhiệm vụ: phân tích kỹ thuật (chart patterns, volume profile, SMC), "
            "phân tích cơ bản (tin tức, events), đánh giá market regime.\n"
            "Luôn đưa ra bias (bullish/bearish/neutral) kèm reasoning."
        ),
        "tools": {
            "mt5_candles", "mt5_analyze", "mt5_smc", "mt5_price",
            "technical_indicators", "market_session", "trading_calendar",
            "web_search", "fetch_url",
        },
    },
    {
        "worker_id": "finance.trader",
        "name": "Trader",
        "department": "finance",
        "role": (
            "Bạn là Trader chuyên thực thi giao dịch XAUUSD trên MT5.\n"
            "Nhiệm vụ: đặt lệnh, quản lý positions, pending orders, "
            "theo dõi P&L, close positions khi cần.\n"
            "Luôn tuân thủ risk management rules."
        ),
        "tools": {
            "mt5_order", "mt5_close", "mt5_positions", "mt5_account",
            "mt5_price", "mt5_history", "trade_plan", "trade_status",
            "trade_config", "trade_control", "trade_pending",
        },
    },
    {
        "worker_id": "finance.crypto_specialist",
        "name": "Crypto Specialist",
        "department": "finance",
        "role": (
            "Bạn là Crypto Specialist chuyên về airdrop hunting và DeFi research.\n"
            "Nhiệm vụ: tìm airdrop campaigns mới, đánh giá tiềm năng, "
            "theo dõi crypto market trends, phân tích tokenomics.\n"
            "Focus: early-stage airdrops, DeFi protocols, market opportunities."
        ),
        "tools": {"web_search", "browse_web", "deep_search", "fetch_url"},
    },
    # Security (2)
    {
        "worker_id": "security.pen_tester",
        "name": "Pen Tester",
        "department": "security",
        "role": (
            "Bạn là Penetration Tester chuyên kiểm tra bảo mật.\n"
            "Nhiệm vụ: scan vulnerabilities, test web app security, "
            "tìm XSS/SQLi/SSRF, report findings.\n"
            "Luôn document tất cả findings chi tiết."
        ),
        "tools": {
            "subdomain_enum", "http_headers", "tech_detect", "xss_scan",
            "sqli_scan", "ssrf_scan", "nuclei_scan", "port_scan",
            "web_search", "fetch_url",
        },
    },
    {
        "worker_id": "security.researcher",
        "name": "Security Researcher",
        "department": "security",
        "role": (
            "Bạn là Security Researcher chuyên nghiên cứu vulnerabilities.\n"
            "Nhiệm vụ: CVE tracking, threat intelligence, security advisories, "
            "đánh giá impact, recommend patches.\n"
            "Focus: actionable intelligence, not just raw data."
        ),
        "tools": {
            "cve_lookup", "reverse_dns", "tech_detect", "http_headers",
            "web_search", "fetch_url", "browse_web",
        },
    },
    # Engineering (2)
    {
        "worker_id": "engineering.developer",
        "name": "Developer",
        "department": "engineering",
        "role": (
            "Bạn là Developer chuyên phân tích và viết code.\n"
            "Nhiệm vụ: code review, debugging, code generation, "
            "refactoring, dependency analysis.\n"
            "Focus: clean code, SOLID principles, testing."
        ),
        "tools": {
            "ast_analyze", "complexity_check", "code_search", "diff_summary",
            "dependency_graph", "run_python", "code_exec",
            "read_file", "write_file", "list_dir",
        },
    },
    {
        "worker_id": "engineering.devops",
        "name": "DevOps",
        "department": "engineering",
        "role": (
            "Bạn là DevOps Engineer chuyên vận hành và tự động hóa.\n"
            "Nhiệm vụ: deployment, monitoring, CI/CD, automation scripts, "
            "system administration.\n"
            "Focus: reliability, automation, efficiency."
        ),
        "tools": {"run_python", "code_exec", "read_file", "write_file", "list_dir"},
    },
    # Research (2)
    {
        "worker_id": "research.product_researcher",
        "name": "Product Researcher",
        "department": "research",
        "role": (
            "Bạn là Product Researcher chuyên tìm kiếm cơ hội kinh doanh mới.\n"
            "Nhiệm vụ: market research, competitor analysis, trend spotting, "
            "đánh giá product-market fit, tìm niches có tiềm năng.\n"
            "Focus: actionable insights, data-driven recommendations."
        ),
        "tools": {"web_search", "browse_web", "deep_search", "fetch_url"},
    },
    {
        "worker_id": "research.data_analyst",
        "name": "Data Analyst",
        "department": "research",
        "role": (
            "Bạn là Data Analyst chuyên xử lý và phân tích dữ liệu.\n"
            "Nhiệm vụ: data processing, statistical analysis, visualization, "
            "extract insights từ raw data.\n"
            "Focus: accuracy, clear presentation, actionable metrics."
        ),
        "tools": {
            "csv_analyze", "json_query", "sqlite_query", "text_stats",
            "json_transform", "run_python",
        },
    },
    # Operations (1)
    {
        "worker_id": "operations.office_manager",
        "name": "Office Manager",
        "department": "operations",
        "role": (
            "Bạn là Office Manager quản lý công việc hàng ngày.\n"
            "Nhiệm vụ: scheduling, reminders, daily digest, communications, "
            "media processing (TTS, image analysis).\n"
            "Focus: organization, timeliness, clear communication."
        ),
        "tools": {
            "set_reminder", "list_reminders", "daily_digest",
            "text_to_speech", "analyze_image", "ocr_image",
        },
    },
]


# ---------------------------------------------------------------------------
# WorkerRegistry -- creates and manages all Worker instances
# ---------------------------------------------------------------------------


class WorkerRegistry:
    """Creates Worker instances from WORKER_ROSTER and provides lookup/lifecycle helpers."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        assembler: PromptAssembler,
        tracer: ReasoningTracer,
        cloud_model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._workers: dict[str, Worker] = {}
        self._cost_guard = CostGuard()

        for spec in WORKER_ROSTER:
            worker = Worker(
                worker_id=spec["worker_id"],
                name=spec["name"],
                department=spec["department"],
                role=spec["role"],
                tools=spec["tools"],
                tool_registry=tool_registry,
                assembler=assembler,
                tracer=tracer,
                cloud_model=cloud_model,
            )
            self._workers[worker.worker_id] = worker

        log.info("worker_registry_created", total=len(self._workers))

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, worker_id: str) -> Worker | None:
        """Get a worker by id. Returns None if not found."""
        return self._workers.get(worker_id)

    def get_department_workers(self, department: str) -> list[Worker]:
        """Get all workers belonging to a department."""
        return [w for w in self._workers.values() if w.department == department]

    def get_all(self) -> list[Worker]:
        """Get all registered workers."""
        return list(self._workers.values())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_all(self) -> None:
        """Start background loops for all workers."""
        for worker in self._workers.values():
            worker.start()
        log.info("all_workers_started", count=len(self._workers))

    async def stop_all(self) -> None:
        """Stop background loops for all workers."""
        for worker in self._workers.values():
            await worker.stop()
        log.info("all_workers_stopped", count=len(self._workers))

    def start_department(self, department: str) -> int:
        """Start all workers in a department. Returns the number started."""
        workers = self.get_department_workers(department)
        for worker in workers:
            worker.start()
        return len(workers)

    async def stop_department(self, department: str) -> int:
        """Stop all workers in a department. Returns the number stopped."""
        workers = self.get_department_workers(department)
        for worker in workers:
            await worker.stop()
        return len(workers)

    # ------------------------------------------------------------------
    # Cost guard
    # ------------------------------------------------------------------

    @property
    def cost_guard(self) -> CostGuard:
        """Access the shared CostGuard instance."""
        return self._cost_guard

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return a status summary of all workers grouped by department."""
        departments: dict[str, list[dict[str, Any]]] = {}
        for worker in self._workers.values():
            dept_list = departments.setdefault(worker.department, [])
            dept_list.append(worker.get_status_dict())

        return {
            "total_workers": len(self._workers),
            "departments": departments,
            "cost": self._cost_guard.get_daily_usage(),
        }
