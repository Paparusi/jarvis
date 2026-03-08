"""AI Bug Hunter Pipeline — chains 7 agents for autonomous vulnerability hunting.

Agents: Recon → LiveScan → Crawler → JSAnalysis → VulnScan → AIAnalyzer → Reporter
Each agent passes structured context to the next via a shared dict.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.bounty.agents.base import AgentResult
from src.bounty.agents.recon import ReconAgent
from src.bounty.agents.livescan import LiveScanAgent
from src.bounty.agents.crawler import CrawlerAgent
from src.bounty.agents.js_analyzer import JSAnalysisAgent
from src.bounty.agents.vuln_scanner import VulnScanAgent
from src.bounty.agents.ai_analyzer import AIAnalyzerAgent
from src.bounty.agents.reporter import ReportAgent
from src.bounty.models import BountyFinding, BountyProgram
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("bounty.hunter")


@dataclass
class HuntResult:
    """Result of a full hunt pipeline execution."""
    domain: str
    findings: list[BountyFinding] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    agent_results: list[AgentResult] = field(default_factory=list)
    execution_time_ms: int = 0
    deep_scan_performed: bool = False


class HunterPipeline:
    """AI Bug Hunter Pipeline — chains 7 agents sequentially.

    Usage:
        pipeline = HunterPipeline(tool_registry, llm_fn=my_llm_call)
        result = await pipeline.hunt("target.com")
        result = await pipeline.hunt("target.com", mode="quick")
        result = await pipeline.hunt("target.com", mode="deep")
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_fn: Callable[..., Awaitable[str]] | None = None,
        progress_fn: Callable[[str, str], Any] | None = None,
    ):
        self.registry = tool_registry
        self.llm = llm_fn
        self._progress_fn = progress_fn

        # Initialize all 7 agents
        self._agents = [
            ReconAgent(tool_registry, llm_fn),
            LiveScanAgent(tool_registry, llm_fn),
            CrawlerAgent(tool_registry, llm_fn),
            JSAnalysisAgent(tool_registry, llm_fn),
            VulnScanAgent(tool_registry, llm_fn),
            AIAnalyzerAgent(tool_registry, llm_fn),
            ReportAgent(tool_registry, llm_fn),
        ]

    def _report_progress(self, agent_name: str, message: str) -> None:
        """Send progress update if callback is registered."""
        if self._progress_fn:
            try:
                self._progress_fn(agent_name, message)
            except Exception:
                pass

    # Pipeline timeout per mode (seconds)
    _MODE_TIMEOUTS = {"quick": 120, "full": 600, "deep": 900}

    # Per-agent max time budget (seconds).  Prevents any single agent from
    # consuming the entire pipeline budget.
    _AGENT_MAX = {
        "recon": 120,
        "livescan": 120,
        "crawler": 120,
        "js_analyzer": 60,
        "vuln_scanner": 180,
        "ai_analyzer": 60,
        "reporter": 30,
    }

    # Agents that MUST run — we reserve enough time for them by skipping
    # optional agents (crawler, js_analyzer) when the budget is tight.
    _MUST_RUN = {"vuln_scanner", "ai_analyzer", "reporter"}
    _MUST_RUN_RESERVE = 200  # seconds reserved for must-run agents

    async def hunt(
        self,
        domain: str,
        program: BountyProgram | None = None,
        mode: str = "full",
    ) -> HuntResult:
        """Run the hunting pipeline on a domain.

        Modes:
            full  — All 7 agents (timeout 600s)
            quick — Recon + LiveScan only (timeout 120s)
            deep  — Full pipeline + autonomous deep scan (timeout 900s)
        """
        start = time.time()
        pipeline_timeout = self._MODE_TIMEOUTS.get(mode, 600)
        context: dict[str, Any] = {"domain": domain, "program": program}
        all_agent_results: list[AgentResult] = []

        # Determine which agents to run based on mode
        if mode == "quick":
            agents_to_run = self._agents[:2]  # Recon + LiveScan
        else:
            agents_to_run = self._agents  # All 7

        log.info("hunt_start", domain=domain, mode=mode, agents=len(agents_to_run))

        for agent in agents_to_run:
            agent_name = agent.name

            # Check pipeline-level timeout before each agent
            elapsed_so_far = time.time() - start
            remaining = pipeline_timeout - elapsed_so_far
            if remaining < 20:
                log.warning(
                    "pipeline_timeout",
                    domain=domain,
                    agent=agent_name,
                    elapsed=int(elapsed_so_far),
                    timeout=pipeline_timeout,
                )
                self._report_progress(
                    agent_name,
                    f"Pipeline timeout ({pipeline_timeout}s) — returning partial results",
                )
                break

            # Skip optional agents if doing so is needed to protect the
            # budget for must-run agents (vuln_scanner, ai_analyzer, reporter).
            if agent_name not in self._MUST_RUN:
                # After this agent runs, will there be enough time for must-run?
                agent_budget = min(remaining, self._AGENT_MAX.get(agent_name, 180))
                time_after_agent = remaining - agent_budget
                if time_after_agent < self._MUST_RUN_RESERVE and agent_name in ("crawler", "js_analyzer"):
                    log.info(
                        "agent_skipped_budget",
                        agent=agent_name,
                        remaining=int(remaining),
                        reserve=self._MUST_RUN_RESERVE,
                    )
                    self._report_progress(
                        agent_name,
                        f"Skipping {agent_name} — reserving time for vuln scanning",
                    )
                    continue

            self._report_progress(agent_name, f"Running {agent_name} agent...")

            try:
                # Per-agent timeout: min(remaining, agent max budget)
                agent_timeout = min(
                    remaining,
                    self._AGENT_MAX.get(agent_name, 180),
                )
                agent_timeout = max(30, agent_timeout)
                result = await asyncio.wait_for(
                    agent.run(context), timeout=agent_timeout,
                )
                all_agent_results.append(result)

                # Pass data to next agent via context
                context.update(result.data)
                if result.findings:
                    existing = context.get("findings", [])
                    existing.extend(result.findings)
                    context["findings"] = existing

                log.info(
                    "agent_complete",
                    agent=agent_name,
                    success=result.success,
                    findings=len(result.findings),
                    data_keys=list(result.data.keys()),
                    time_ms=result.execution_time_ms,
                )

                self._report_progress(
                    agent_name,
                    f"{agent_name} done: {len(result.findings)} findings, "
                    f"{result.execution_time_ms}ms",
                )

                # Early exit if livescan finds no alive hosts
                if agent_name == "livescan" and not result.data.get("alive_hosts"):
                    log.warning("no_alive_hosts", domain=domain)
                    self._report_progress("livescan", "No alive hosts found — stopping")
                    break

            except asyncio.TimeoutError:
                log.warning("agent_timeout", agent=agent_name, domain=domain)
                all_agent_results.append(AgentResult(
                    agent_name=agent_name,
                    success=False,
                    errors=[f"{agent_name} timed out"],
                ))
                self._report_progress(agent_name, f"{agent_name} timed out — skipping")

            except Exception as exc:
                log.error("agent_failed", agent=agent_name, error=str(exc))
                all_agent_results.append(AgentResult(
                    agent_name=agent_name,
                    success=False,
                    errors=[str(exc)],
                ))
                self._report_progress(agent_name, f"{agent_name} failed: {exc}")
                # Continue to next agent — don't break the pipeline

        # Collect all findings
        all_findings = context.get("findings", [])
        reports = context.get("reports", [])

        # Deep mode: autonomous deep scan on promising targets
        deep_performed = False
        if mode == "deep":
            remaining = pipeline_timeout - (time.time() - start)
            if remaining > 120:  # Only deep scan if enough time left
                deep_targets = context.get("deep_scan_targets", [])
                if deep_targets:
                    self._report_progress("deep_scan", f"Deep scanning {len(deep_targets[:3])} targets...")
                    try:
                        deep_findings = await asyncio.wait_for(
                            self._deep_scan(deep_targets[:3], context),
                            timeout=remaining,
                        )
                        all_findings.extend(deep_findings)
                        deep_performed = True
                    except asyncio.TimeoutError:
                        log.warning("deep_scan_timeout", domain=domain)

        elapsed = int((time.time() - start) * 1000)
        log.info(
            "hunt_complete",
            domain=domain,
            mode=mode,
            total_findings=len(all_findings),
            reports=len(reports),
            time_ms=elapsed,
        )

        return HuntResult(
            domain=domain,
            findings=all_findings,
            reports=reports,
            agent_results=all_agent_results,
            execution_time_ms=elapsed,
            deep_scan_performed=deep_performed,
        )

    async def _deep_scan(self, targets: list[str], context: dict) -> list[BountyFinding]:
        """AI-directed deeper scanning on promising targets.

        Re-runs VulnScan + AIAnalyzer on specific URLs.
        """
        findings: list[BountyFinding] = []
        vuln_scanner = self._agents[4]  # VulnScanAgent
        ai_analyzer = self._agents[5]  # AIAnalyzerAgent

        for target_url in targets:
            try:
                deep_ctx = {
                    "domain": context.get("domain", ""),
                    "alive_hosts": [{"url": target_url, "priority": 10}],
                    "endpoints": [],
                    "js_files": [],
                }
                vuln_result = await vuln_scanner.run(deep_ctx)

                if vuln_result.findings:
                    ai_ctx = {"findings": vuln_result.findings}
                    ai_result = await ai_analyzer.run(ai_ctx)
                    findings.extend(ai_result.findings)

            except Exception as exc:
                log.warning("deep_scan_failed", target=target_url, error=str(exc))

        return findings

    def format_summary(self, result: HuntResult) -> str:
        """Format a human-readable summary of hunt results."""
        lines = [
            f"🎯 Hunt Results: {result.domain}",
            f"⏱ Duration: {result.execution_time_ms / 1000:.1f}s",
            "",
        ]

        # Agent summary
        for ar in result.agent_results:
            status = "✅" if ar.success else "❌"
            lines.append(f"  {status} {ar.agent_name}: {len(ar.findings)} findings ({ar.execution_time_ms}ms)")

        lines.append("")

        if result.findings:
            # Group by severity
            by_sev: dict[str, int] = {}
            for f in result.findings:
                by_sev[f.severity] = by_sev.get(f.severity, 0) + 1

            lines.append(f"📊 Total Findings: {len(result.findings)}")
            for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
                count = by_sev.get(sev, 0)
                if count:
                    emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢", "INFO": "ℹ️"}.get(sev, "•")
                    lines.append(f"  {emoji} {sev}: {count}")
        else:
            lines.append("📊 No findings")

        if result.deep_scan_performed:
            lines.append("\n🔍 Deep scan was performed on promising targets")

        if result.reports:
            lines.append(f"\n📝 {len(result.reports)} reports generated")

        return "\n".join(lines)
