"""Tests for src.bounty.hunter — HunterPipeline orchestrator."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from src.bounty.agents.base import AgentResult
from src.bounty.hunter import HunterPipeline, HuntResult
from src.bounty.models import BountyFinding, BountyProgram
from src.tools.base import ToolRegistry


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _make_finding(severity: str = "HIGH", vuln_type: str = "xss", title: str = "Test finding") -> BountyFinding:
    """Create a BountyFinding with sensible defaults for testing."""
    return BountyFinding(
        target_id=1,
        vuln_type=vuln_type,
        severity=severity,
        cvss=7.5,
        confidence=0.9,
        title=title,
    )


def _make_agent_result(
    agent_name: str = "recon",
    success: bool = True,
    data: dict | None = None,
    findings: list[BountyFinding] | None = None,
    errors: list[str] | None = None,
    execution_time_ms: int = 100,
) -> AgentResult:
    """Build an AgentResult with controlled values."""
    return AgentResult(
        agent_name=agent_name,
        success=success,
        data=data or {},
        findings=findings or [],
        errors=errors or [],
        execution_time_ms=execution_time_ms,
    )


# ================================================================== #
# HuntResult dataclass
# ================================================================== #

class TestHuntResult:
    # ---------------------------------------------------------------- #
    # 1. test_hunt_result_defaults
    # ---------------------------------------------------------------- #
    def test_hunt_result_defaults(self):
        """Default HuntResult has empty lists, zero time, no deep scan."""
        result = HuntResult(domain="test.com")
        assert result.domain == "test.com"
        assert result.findings == []
        assert result.reports == []
        assert result.agent_results == []
        assert result.execution_time_ms == 0
        assert result.deep_scan_performed is False

    # ---------------------------------------------------------------- #
    # 2. test_hunt_result_with_data
    # ---------------------------------------------------------------- #
    def test_hunt_result_with_data(self):
        """HuntResult stores all fields when fully populated."""
        finding = _make_finding()
        ar = _make_agent_result(agent_name="recon")
        result = HuntResult(
            domain="example.com",
            findings=[finding],
            reports=["## Report markdown"],
            agent_results=[ar],
            execution_time_ms=5000,
            deep_scan_performed=True,
        )
        assert result.domain == "example.com"
        assert len(result.findings) == 1
        assert result.findings[0].vuln_type == "xss"
        assert result.reports == ["## Report markdown"]
        assert len(result.agent_results) == 1
        assert result.agent_results[0].agent_name == "recon"
        assert result.execution_time_ms == 5000
        assert result.deep_scan_performed is True


# ================================================================== #
# HunterPipeline Init
# ================================================================== #

class TestHunterPipelineInit:
    def setup_method(self):
        self.registry = MagicMock(spec=ToolRegistry)
        self.llm_fn = AsyncMock(return_value="test response")

    # ---------------------------------------------------------------- #
    # 3. test_init_creates_7_agents
    # ---------------------------------------------------------------- #
    def test_init_creates_7_agents(self):
        """Pipeline creates exactly 7 agents in the correct order."""
        pipeline = HunterPipeline(self.registry, self.llm_fn)
        assert len(pipeline._agents) == 7
        expected_names = ["recon", "livescan", "crawler", "js_analyzer",
                          "vuln_scanner", "ai_analyzer", "reporter"]
        actual_names = [a.name for a in pipeline._agents]
        assert actual_names == expected_names

    # ---------------------------------------------------------------- #
    # 4. test_init_without_llm
    # ---------------------------------------------------------------- #
    def test_init_without_llm(self):
        """Pipeline can be initialized without an LLM function."""
        pipeline = HunterPipeline(self.registry, llm_fn=None)
        assert pipeline.llm is None
        assert len(pipeline._agents) == 7
        for agent in pipeline._agents:
            assert agent.llm is None

    # ---------------------------------------------------------------- #
    # 5. test_init_with_progress
    # ---------------------------------------------------------------- #
    def test_init_with_progress(self):
        """Progress callback is stored when provided."""
        progress_fn = MagicMock()
        pipeline = HunterPipeline(self.registry, self.llm_fn, progress_fn=progress_fn)
        assert pipeline._progress_fn is progress_fn


# ================================================================== #
# hunt() Method
# ================================================================== #

class TestHunt:
    def setup_method(self):
        self.registry = MagicMock(spec=ToolRegistry)
        self.llm_fn = AsyncMock(return_value="test response")
        self.pipeline = HunterPipeline(self.registry, self.llm_fn)

    def _mock_all_agents(self, alive_hosts=True):
        """Set up mock run() on every agent in the pipeline."""
        for agent in self.pipeline._agents:
            data = {}
            if agent.name == "livescan" and alive_hosts:
                data = {"alive_hosts": [{"url": "https://test.com", "priority": 5}]}
            elif agent.name == "livescan":
                data = {"alive_hosts": []}
            agent.run = AsyncMock(return_value=_make_agent_result(
                agent_name=agent.name,
                success=True,
                data=data,
                findings=[],
                execution_time_ms=100,
            ))

    # ---------------------------------------------------------------- #
    # 6. test_hunt_full_mode
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_hunt_full_mode(self):
        """Full mode runs all 7 agents."""
        self._mock_all_agents()

        result = await self.pipeline.hunt("test.com", mode="full")

        assert len(result.agent_results) == 7
        for agent in self.pipeline._agents:
            agent.run.assert_called_once()
        assert result.domain == "test.com"
        assert result.deep_scan_performed is False

    # ---------------------------------------------------------------- #
    # 7. test_hunt_quick_mode
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_hunt_quick_mode(self):
        """Quick mode only runs the first 2 agents (recon + livescan)."""
        self._mock_all_agents()

        result = await self.pipeline.hunt("test.com", mode="quick")

        # Only recon and livescan should have been called
        assert len(result.agent_results) == 2
        assert result.agent_results[0].agent_name == "recon"
        assert result.agent_results[1].agent_name == "livescan"
        # Remaining agents should NOT be called
        for agent in self.pipeline._agents[2:]:
            agent.run.assert_not_called()

    # ---------------------------------------------------------------- #
    # 8. test_hunt_deep_mode
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_hunt_deep_mode(self):
        """Deep mode runs all 7 agents plus _deep_scan when targets present."""
        self._mock_all_agents()
        # Inject deep_scan_targets into one agent's data so _deep_scan triggers
        self.pipeline._agents[5].run = AsyncMock(return_value=_make_agent_result(
            agent_name="ai_analyzer",
            success=True,
            data={"deep_scan_targets": ["https://test.com/admin"]},
            findings=[],
            execution_time_ms=50,
        ))
        # Patch _deep_scan to avoid running actual logic
        self.pipeline._deep_scan = AsyncMock(return_value=[_make_finding(severity="CRITICAL")])

        result = await self.pipeline.hunt("test.com", mode="deep")

        assert len(result.agent_results) == 7
        assert result.deep_scan_performed is True
        self.pipeline._deep_scan.assert_called_once()
        # The deep scan finding should be in the result
        assert any(f.severity == "CRITICAL" for f in result.findings)

    # ---------------------------------------------------------------- #
    # 9. test_hunt_early_exit_no_alive
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_hunt_early_exit_no_alive(self):
        """Pipeline stops after livescan when no alive hosts found."""
        self._mock_all_agents(alive_hosts=False)

        result = await self.pipeline.hunt("dead.com", mode="full")

        # Only recon + livescan should have results (early exit after livescan)
        assert len(result.agent_results) == 2
        assert result.agent_results[0].agent_name == "recon"
        assert result.agent_results[1].agent_name == "livescan"
        for agent in self.pipeline._agents[2:]:
            agent.run.assert_not_called()

    # ---------------------------------------------------------------- #
    # 10. test_hunt_agent_error_continues
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_hunt_agent_error_continues(self):
        """If one agent raises an exception, pipeline continues to the next."""
        self._mock_all_agents()
        # Make crawler raise an exception
        self.pipeline._agents[2].run = AsyncMock(side_effect=RuntimeError("Crawler crashed"))

        result = await self.pipeline.hunt("test.com", mode="full")

        # All 7 agents should have results (including the failed one)
        assert len(result.agent_results) == 7
        # The crawler result should be a failure
        crawler_result = result.agent_results[2]
        assert crawler_result.agent_name == "crawler"
        assert crawler_result.success is False
        assert "Crawler crashed" in crawler_result.errors
        # Agents after crawler should still run
        for agent in self.pipeline._agents[3:]:
            agent.run.assert_called_once()

    # ---------------------------------------------------------------- #
    # 11. test_hunt_context_passing
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_hunt_context_passing(self):
        """Data from earlier agents flows into context for later agents."""
        self._mock_all_agents()
        # Recon returns subdomains
        self.pipeline._agents[0].run = AsyncMock(return_value=_make_agent_result(
            agent_name="recon",
            data={"subdomains": ["sub.test.com"]},
        ))
        # LiveScan returns alive hosts
        self.pipeline._agents[1].run = AsyncMock(return_value=_make_agent_result(
            agent_name="livescan",
            data={"alive_hosts": [{"url": "https://sub.test.com", "priority": 5}]},
        ))

        await self.pipeline.hunt("test.com", mode="full")

        # The crawler (agent[2]) should have received context with both
        # subdomains and alive_hosts
        crawler_call_args = self.pipeline._agents[2].run.call_args[0][0]
        assert "subdomains" in crawler_call_args
        assert "alive_hosts" in crawler_call_args
        assert crawler_call_args["domain"] == "test.com"

    # ---------------------------------------------------------------- #
    # 12. test_hunt_findings_accumulated
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_hunt_findings_accumulated(self):
        """Findings from multiple agents are collected into the result."""
        self._mock_all_agents()
        finding_1 = _make_finding(severity="HIGH", title="XSS in search")
        finding_2 = _make_finding(severity="CRITICAL", vuln_type="sqli", title="SQL Injection")

        # Crawler returns one finding
        self.pipeline._agents[2].run = AsyncMock(return_value=_make_agent_result(
            agent_name="crawler",
            data={},
            findings=[finding_1],
        ))
        # VulnScanner returns another finding
        self.pipeline._agents[4].run = AsyncMock(return_value=_make_agent_result(
            agent_name="vuln_scanner",
            data={},
            findings=[finding_2],
        ))

        result = await self.pipeline.hunt("test.com", mode="full")

        assert len(result.findings) == 2
        severities = [f.severity for f in result.findings]
        assert "HIGH" in severities
        assert "CRITICAL" in severities

    # ---------------------------------------------------------------- #
    # 13. test_hunt_progress_callback
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_hunt_progress_callback(self):
        """Progress function is called for each agent start and completion."""
        progress_fn = MagicMock()
        pipeline = HunterPipeline(self.registry, self.llm_fn, progress_fn=progress_fn)
        # Mock all agents
        for agent in pipeline._agents:
            agent.run = AsyncMock(return_value=_make_agent_result(
                agent_name=agent.name,
                data={"alive_hosts": [{"url": "https://test.com", "priority": 5}]}
                if agent.name == "livescan" else {},
            ))

        await pipeline.hunt("test.com", mode="full")

        # Should have been called at least twice per agent (start + done)
        assert progress_fn.call_count >= 14  # 7 agents * 2 calls each
        # Check that agent names appear in calls
        all_call_args = [c[0][0] for c in progress_fn.call_args_list]
        assert "recon" in all_call_args
        assert "livescan" in all_call_args
        assert "reporter" in all_call_args


# ================================================================== #
# Deep Scan
# ================================================================== #

class TestDeepScan:
    def setup_method(self):
        self.registry = MagicMock(spec=ToolRegistry)
        self.llm_fn = AsyncMock(return_value="test response")
        self.pipeline = HunterPipeline(self.registry, self.llm_fn)

    # ---------------------------------------------------------------- #
    # 14. test_deep_scan_runs
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_deep_scan_runs(self):
        """_deep_scan re-runs VulnScan and AIAnalyzer on given targets."""
        finding = _make_finding(severity="CRITICAL", title="Deep vuln")
        # Mock the VulnScanAgent (index 4)
        self.pipeline._agents[4].run = AsyncMock(return_value=_make_agent_result(
            agent_name="vuln_scanner",
            findings=[finding],
        ))
        # Mock the AIAnalyzerAgent (index 5)
        ai_finding = _make_finding(severity="CRITICAL", title="AI-confirmed vuln")
        self.pipeline._agents[5].run = AsyncMock(return_value=_make_agent_result(
            agent_name="ai_analyzer",
            findings=[ai_finding],
        ))

        targets = ["https://test.com/admin"]
        context = {"domain": "test.com"}
        results = await self.pipeline._deep_scan(targets, context)

        assert len(results) == 1
        assert results[0].title == "AI-confirmed vuln"
        self.pipeline._agents[4].run.assert_called_once()
        self.pipeline._agents[5].run.assert_called_once()

    # ---------------------------------------------------------------- #
    # 15. test_deep_scan_max_3
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_deep_scan_max_3(self):
        """_deep_scan is called with at most 3 targets from hunt()."""
        # Set up all agents mocked
        for agent in self.pipeline._agents:
            data = {}
            if agent.name == "livescan":
                data = {"alive_hosts": [{"url": "https://test.com", "priority": 5}]}
            agent.run = AsyncMock(return_value=_make_agent_result(
                agent_name=agent.name,
                data=data,
            ))

        # The ai_analyzer returns 5 deep scan targets
        self.pipeline._agents[5].run = AsyncMock(return_value=_make_agent_result(
            agent_name="ai_analyzer",
            data={"deep_scan_targets": [
                "https://t.com/a", "https://t.com/b", "https://t.com/c",
                "https://t.com/d", "https://t.com/e",
            ]},
        ))
        self.pipeline._deep_scan = AsyncMock(return_value=[])

        await self.pipeline.hunt("test.com", mode="deep")

        # _deep_scan should be called with the list sliced to first 3
        called_targets = self.pipeline._deep_scan.call_args[0][0]
        assert len(called_targets) == 3
        assert called_targets == ["https://t.com/a", "https://t.com/b", "https://t.com/c"]

    # ---------------------------------------------------------------- #
    # 16. test_deep_scan_no_targets
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_deep_scan_no_targets(self):
        """Deep mode with no deep_scan_targets does not perform deep scan."""
        for agent in self.pipeline._agents:
            data = {}
            if agent.name == "livescan":
                data = {"alive_hosts": [{"url": "https://test.com", "priority": 5}]}
            agent.run = AsyncMock(return_value=_make_agent_result(
                agent_name=agent.name,
                data=data,
            ))
        self.pipeline._deep_scan = AsyncMock(return_value=[])

        result = await self.pipeline.hunt("test.com", mode="deep")

        self.pipeline._deep_scan.assert_not_called()
        assert result.deep_scan_performed is False


# ================================================================== #
# format_summary()
# ================================================================== #

class TestFormatSummary:
    def setup_method(self):
        self.registry = MagicMock(spec=ToolRegistry)
        self.pipeline = HunterPipeline(self.registry)

    # ---------------------------------------------------------------- #
    # 17. test_format_summary_with_findings
    # ---------------------------------------------------------------- #
    def test_format_summary_with_findings(self):
        """Summary shows correct severity breakdown."""
        findings = [
            _make_finding(severity="CRITICAL"),
            _make_finding(severity="HIGH"),
            _make_finding(severity="HIGH"),
            _make_finding(severity="MEDIUM"),
            _make_finding(severity="LOW"),
        ]
        hunt_result = HuntResult(
            domain="test.com",
            findings=findings,
            agent_results=[
                _make_agent_result("recon", success=True),
            ],
            execution_time_ms=3000,
        )

        summary = self.pipeline.format_summary(hunt_result)

        assert "test.com" in summary
        assert "3.0s" in summary
        assert "Total Findings: 5" in summary
        assert "CRITICAL: 1" in summary
        assert "HIGH: 2" in summary
        assert "MEDIUM: 1" in summary
        assert "LOW: 1" in summary

    # ---------------------------------------------------------------- #
    # 18. test_format_summary_no_findings
    # ---------------------------------------------------------------- #
    def test_format_summary_no_findings(self):
        """Summary shows 'No findings' when findings list is empty."""
        hunt_result = HuntResult(
            domain="clean.com",
            findings=[],
            agent_results=[],
            execution_time_ms=1000,
        )

        summary = self.pipeline.format_summary(hunt_result)

        assert "clean.com" in summary
        assert "No findings" in summary
        assert "Total Findings" not in summary

    # ---------------------------------------------------------------- #
    # 19. test_format_summary_deep_scan
    # ---------------------------------------------------------------- #
    def test_format_summary_deep_scan(self):
        """Summary includes deep scan note when deep_scan_performed is True."""
        hunt_result = HuntResult(
            domain="test.com",
            findings=[],
            agent_results=[],
            execution_time_ms=5000,
            deep_scan_performed=True,
        )

        summary = self.pipeline.format_summary(hunt_result)

        assert "Deep scan was performed" in summary

    # ---------------------------------------------------------------- #
    # 20. test_format_summary_agent_status
    # ---------------------------------------------------------------- #
    def test_format_summary_agent_status(self):
        """Summary shows agent status with check/cross marks."""
        agent_results = [
            _make_agent_result("recon", success=True, execution_time_ms=200),
            _make_agent_result("livescan", success=True, execution_time_ms=300),
            _make_agent_result("crawler", success=False, execution_time_ms=50),
        ]
        hunt_result = HuntResult(
            domain="test.com",
            findings=[],
            agent_results=agent_results,
            execution_time_ms=2000,
        )

        summary = self.pipeline.format_summary(hunt_result)

        # Successful agents get checkmark, failed agents get cross
        assert "recon" in summary
        assert "livescan" in summary
        assert "crawler" in summary
        # Check for the status indicators
        lines = summary.split("\n")
        recon_line = [l for l in lines if "recon" in l][0]
        crawler_line = [l for l in lines if "crawler" in l][0]
        assert "\u2705" in recon_line  # checkmark
        assert "\u274c" in crawler_line  # cross mark

    # ---------------------------------------------------------------- #
    # 21. test_format_summary_with_reports
    # ---------------------------------------------------------------- #
    def test_format_summary_with_reports(self):
        """Summary shows report count when reports are present."""
        hunt_result = HuntResult(
            domain="test.com",
            findings=[],
            reports=["report1.md", "report2.md"],
            agent_results=[],
            execution_time_ms=1000,
        )

        summary = self.pipeline.format_summary(hunt_result)

        assert "2 reports generated" in summary

    # ---------------------------------------------------------------- #
    # 22. test_hunt_with_program
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_hunt_with_program(self):
        """Program object is passed into the context dict."""
        registry = MagicMock(spec=ToolRegistry)
        llm_fn = AsyncMock(return_value="ok")
        pipeline = HunterPipeline(registry, llm_fn)

        program = BountyProgram(
            platform="hackerone",
            program_id="test-prog",
            name="Test Program",
        )

        for agent in pipeline._agents:
            data = {}
            if agent.name == "livescan":
                data = {"alive_hosts": [{"url": "https://test.com", "priority": 5}]}
            agent.run = AsyncMock(return_value=_make_agent_result(
                agent_name=agent.name,
                data=data,
            ))

        await pipeline.hunt("test.com", program=program, mode="full")

        # The first agent (recon) should receive context with the program
        recon_ctx = pipeline._agents[0].run.call_args[0][0]
        assert recon_ctx["program"] is program
        assert recon_ctx["domain"] == "test.com"

    # ---------------------------------------------------------------- #
    # 23. test_deep_scan_target_failure_continues
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_deep_scan_target_failure_continues(self):
        """If one deep scan target fails, others still proceed."""
        registry = MagicMock(spec=ToolRegistry)
        llm_fn = AsyncMock(return_value="ok")
        pipeline = HunterPipeline(registry, llm_fn)

        call_count = 0

        async def vuln_side_effect(ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("First target failed")
            return _make_agent_result(
                agent_name="vuln_scanner",
                findings=[_make_finding(severity="HIGH", title="Found on second")],
            )

        pipeline._agents[4].run = AsyncMock(side_effect=vuln_side_effect)

        ai_finding = _make_finding(severity="HIGH", title="AI analyzed")
        pipeline._agents[5].run = AsyncMock(return_value=_make_agent_result(
            agent_name="ai_analyzer",
            findings=[ai_finding],
        ))

        targets = ["https://fail.com/a", "https://ok.com/b"]
        context = {"domain": "test.com"}
        results = await pipeline._deep_scan(targets, context)

        # The first target fails but the second should still produce findings
        assert len(results) == 1
        assert results[0].title == "AI analyzed"

    # ---------------------------------------------------------------- #
    # 24. test_deep_scan_no_vuln_findings_skips_ai
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_deep_scan_no_vuln_findings_skips_ai(self):
        """If VulnScan finds nothing, AIAnalyzer is not called for that target."""
        registry = MagicMock(spec=ToolRegistry)
        llm_fn = AsyncMock(return_value="ok")
        pipeline = HunterPipeline(registry, llm_fn)

        # VulnScan returns no findings
        pipeline._agents[4].run = AsyncMock(return_value=_make_agent_result(
            agent_name="vuln_scanner",
            findings=[],
        ))
        pipeline._agents[5].run = AsyncMock(return_value=_make_agent_result(
            agent_name="ai_analyzer",
            findings=[],
        ))

        targets = ["https://clean.com/page"]
        context = {"domain": "clean.com"}
        results = await pipeline._deep_scan(targets, context)

        assert results == []
        pipeline._agents[4].run.assert_called_once()
        pipeline._agents[5].run.assert_not_called()

    # ---------------------------------------------------------------- #
    # 25. test_progress_fn_exception_ignored
    # ---------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_progress_fn_exception_ignored(self):
        """If progress_fn raises, the pipeline does not break."""
        registry = MagicMock(spec=ToolRegistry)
        llm_fn = AsyncMock(return_value="ok")
        progress_fn = MagicMock(side_effect=RuntimeError("callback error"))
        pipeline = HunterPipeline(registry, llm_fn, progress_fn=progress_fn)

        for agent in pipeline._agents:
            data = {}
            if agent.name == "livescan":
                data = {"alive_hosts": [{"url": "https://test.com", "priority": 5}]}
            agent.run = AsyncMock(return_value=_make_agent_result(
                agent_name=agent.name,
                data=data,
            ))

        # Should not raise even though progress_fn throws
        result = await pipeline.hunt("test.com", mode="full")

        assert len(result.agent_results) == 7
        assert progress_fn.call_count > 0
