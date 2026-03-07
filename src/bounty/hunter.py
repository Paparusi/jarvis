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

    async def hunt(
        self,
        domain: str,
        program: BountyProgram | None = None,
        mode: str = "full",
    ) -> HuntResult:
        """Run the hunting pipeline on a domain.

        Modes:
            full  — All 7 agents
            quick — Recon + LiveScan only (fast reconnaissance)
            deep  — Full pipeline + autonomous deep scan on promising targets
        """
        start = time.time()
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
            self._report_progress(agent_name, f"Running {agent_name} agent...")

            try:
                result = await agent.run(context)
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
            deep_targets = context.get("deep_scan_targets", [])
            if deep_targets:
                self._report_progress("deep_scan", f"Deep scanning {len(deep_targets[:3])} targets...")
                deep_findings = await self._deep_scan(deep_targets[:3], context)
                all_findings.extend(deep_findings)
                deep_performed = True

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
