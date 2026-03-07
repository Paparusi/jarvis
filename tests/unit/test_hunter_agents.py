"""Tests for src.bounty.agents — 7 AI Hunter pipeline agents.

Covers BaseHunterAgent, AgentResult, ReconAgent, LiveScanAgent,
CrawlerAgent, JSAnalysisAgent, VulnScanAgent, AIAnalyzerAgent,
and ReportAgent with mocked ToolRegistry and LLM calls.
"""

from __future__ import annotations

import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.bounty.agents.base import AgentResult, BaseHunterAgent
from src.bounty.agents.recon import ReconAgent
from src.bounty.agents.livescan import LiveScanAgent
from src.bounty.agents.crawler import CrawlerAgent
from src.bounty.agents.js_analyzer import JSAnalysisAgent
from src.bounty.agents.vuln_scanner import VulnScanAgent
from src.bounty.agents.ai_analyzer import AIAnalyzerAgent
from src.bounty.agents.reporter import ReportAgent
from src.bounty.models import BountyFinding
from src.tools.base import ToolRegistry, ToolResult


# ---------------------------------------------------------------------------
# Shared helpers and fixtures
# ---------------------------------------------------------------------------

def _tool_result(success=True, output="ok", data=None, error=""):
    """Build a ToolResult with sensible defaults."""
    return ToolResult(success=success, output=output, data=data or {}, error=error)


def _finding(
    title="Test vuln",
    vuln_type="xss",
    severity="HIGH",
    cvss=8.0,
    confidence=0.85,
    poc="https://test.com/poc",
    description="Test description",
    impact="Test impact",
    suggested_fix="Fix it",
):
    """Build a BountyFinding with sensible defaults."""
    return BountyFinding(
        target_id=0,
        vuln_type=vuln_type,
        severity=severity,
        cvss=cvss,
        confidence=confidence,
        title=title,
        description=description,
        poc=poc,
        impact=impact,
        suggested_fix=suggested_fix,
    )


@pytest.fixture
def registry():
    """Mocked ToolRegistry with async execute."""
    reg = MagicMock(spec=ToolRegistry)
    reg.execute = AsyncMock(return_value=_tool_result())
    return reg


@pytest.fixture
def llm_fn():
    """Mocked LLM callable returning an empty JSON array by default."""
    return AsyncMock(return_value="[]")


# ===========================================================================
# 1-2: BaseHunterAgent + AgentResult
# ===========================================================================


class TestAgentResultAndBase:
    def test_agent_result_defaults(self):
        """AgentResult fields have correct default values."""
        r = AgentResult(agent_name="test", success=True)
        assert r.agent_name == "test"
        assert r.success is True
        assert r.data == {}
        assert r.findings == []
        assert r.errors == []
        assert r.execution_time_ms == 0

    def test_make_result_timing(self, registry):
        """_make_result computes elapsed time from start_time."""
        agent = BaseHunterAgent(registry)
        agent.name = "test"
        start = time.time() - 0.15  # simulate 150 ms elapsed
        result = agent._make_result(success=True, start_time=start)
        assert result.execution_time_ms >= 100  # at least 100 ms
        assert result.agent_name == "test"
        assert result.success is True


# ===========================================================================
# 3-6: ReconAgent
# ===========================================================================


class TestReconAgent:
    @pytest.mark.asyncio
    async def test_recon_success(self, registry):
        """Recon merges subfinder + crt.sh results and deduplicates."""
        async def _mock_exec(name, **kw):
            if name == "subfinder_enum":
                return _tool_result(data={
                    "subdomains": ["api.test.com", "www.test.com", "dev.test.com"],
                    "sources": {"crtsh": 2, "dnsdumpster": 1},
                })
            if name == "subdomain_enum":
                return _tool_result(data={
                    "subdomains": ["www.test.com", "mail.test.com"],
                })
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = ReconAgent(registry)
        result = await agent.run({"domain": "test.com"})

        assert result.success is True
        subs = result.data["subdomains"]
        # 4 unique subdomains after merge + dedup
        assert len(subs) == 4
        assert "api.test.com" in subs
        assert "mail.test.com" in subs
        assert result.data["subdomain_count"] == 4

    @pytest.mark.asyncio
    async def test_recon_subfinder_only(self, registry):
        """Recon works when crt.sh fails — subfinder results preserved."""
        call_count = 0

        async def _mock_exec(name, **kw):
            nonlocal call_count
            call_count += 1
            if name == "subfinder_enum":
                return _tool_result(data={
                    "subdomains": ["api.test.com", "dev.test.com"],
                    "sources": {},
                })
            if name == "subdomain_enum":
                raise ConnectionError("crt.sh timeout")
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = ReconAgent(registry)
        result = await agent.run({"domain": "test.com"})

        assert result.success is True
        assert len(result.data["subdomains"]) == 2
        assert any("crt.sh" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_recon_heuristic_priority(self, registry):
        """High-interest subdomains (dev, admin, api) appear before generic ones."""
        async def _mock_exec(name, **kw):
            if name == "subfinder_enum":
                return _tool_result(data={
                    "subdomains": [
                        "www.test.com", "blog.test.com",
                        "dev.test.com", "admin.test.com", "api.test.com",
                    ],
                    "sources": {},
                })
            if name == "subdomain_enum":
                return _tool_result(data={"subdomains": []})
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = ReconAgent(registry)
        result = await agent.run({"domain": "test.com"})

        subs = result.data["subdomains"]
        # High-priority subdomains should come first
        high_priority = {"admin.test.com", "api.test.com", "dev.test.com"}
        # First 3 should all be high-priority
        for s in subs[:3]:
            assert s in high_priority, f"{s} should be in top 3"

    @pytest.mark.asyncio
    async def test_recon_empty(self, registry):
        """Recon returns empty list when no subdomains are found."""
        async def _mock_exec(name, **kw):
            return _tool_result(data={"subdomains": [], "sources": {}})

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = ReconAgent(registry)
        result = await agent.run({"domain": "test.com"})

        assert result.success is True
        assert result.data["subdomains"] == []
        assert result.data["subdomain_count"] == 0


# ===========================================================================
# 7-10: LiveScanAgent
# ===========================================================================


class TestLiveScanAgent:
    @pytest.mark.asyncio
    async def test_livescan_success(self, registry):
        """LiveScan correctly returns alive hosts from httpx probe."""
        registry.execute = AsyncMock(return_value=_tool_result(data={
            "alive": [
                {"url": "https://api.test.com", "status_code": 200, "title": "API"},
                {"url": "https://www.test.com", "status_code": 200, "title": "Welcome"},
            ],
        }))
        agent = LiveScanAgent(registry)
        result = await agent.run({"subdomains": ["api.test.com", "www.test.com"]})

        assert result.success is True
        assert result.data["alive_count"] == 2
        assert result.data["dead_count"] == 0

    @pytest.mark.asyncio
    async def test_livescan_classification(self, registry):
        """Classification assigns correct priorities: admin=10, CI/CD=9, api=8, dev=7."""
        registry.execute = AsyncMock(return_value=_tool_result(data={
            "alive": [
                {"url": "https://admin.test.com", "status_code": 200, "title": "Admin Dashboard"},
                {"url": "https://jenkins.test.com", "status_code": 200, "title": "Jenkins CI"},
                {"url": "https://api.test.com/api/v1", "status_code": 200, "title": ""},
                {"url": "https://dev.test.com", "status_code": 200, "title": "Dev App"},
            ],
        }))
        agent = LiveScanAgent(registry)
        result = await agent.run({
            "subdomains": [
                "admin.test.com", "jenkins.test.com",
                "api.test.com", "dev.test.com",
            ],
        })

        hosts = result.data["alive_hosts"]
        # Verify classification via priority field
        priorities = {h["url"]: h["priority"] for h in hosts}
        assert priorities["https://admin.test.com"] == 10
        assert priorities["https://jenkins.test.com"] == 9
        assert priorities["https://api.test.com/api/v1"] == 8
        assert priorities["https://dev.test.com"] == 7

        # Sorted by priority descending
        assert hosts[0]["priority"] >= hosts[-1]["priority"]

    @pytest.mark.asyncio
    async def test_livescan_empty(self, registry):
        """LiveScan handles no alive hosts gracefully."""
        registry.execute = AsyncMock(return_value=_tool_result(data={"alive": []}))
        agent = LiveScanAgent(registry)
        result = await agent.run({"subdomains": ["dead1.test.com", "dead2.test.com"]})

        assert result.success is True
        assert result.data["alive_count"] == 0
        assert result.data["dead_count"] == 2

    @pytest.mark.asyncio
    async def test_livescan_no_subdomains(self, registry):
        """LiveScan returns early when no subdomains are provided."""
        agent = LiveScanAgent(registry)
        result = await agent.run({"subdomains": []})

        assert result.success is True
        assert result.data["alive_hosts"] == []
        assert result.data["alive_count"] == 0
        # execute should not be called when there are no subdomains
        registry.execute.assert_not_called()


# ===========================================================================
# 11-15: CrawlerAgent
# ===========================================================================


class TestCrawlerAgent:
    @pytest.mark.asyncio
    async def test_crawler_success(self, registry):
        """Crawler merges katana + gau results."""
        async def _mock_exec(name, **kw):
            if name == "katana_crawl":
                return _tool_result(data={
                    "urls": ["https://test.com/page1", "https://test.com/page2"],
                    "js_files": ["https://test.com/app.js"],
                    "endpoints": ["https://test.com/api/v1/users"],
                })
            if name == "gau_urls":
                return _tool_result(data={
                    "urls": ["https://test.com/page2", "https://test.com/old"],
                    "js_files": ["https://test.com/vendor.js"],
                })
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = CrawlerAgent(registry)
        result = await agent.run({
            "alive_hosts": [{"url": "https://test.com", "priority": 5}],
        })

        assert result.success is True
        # 3 unique URLs (page1, page2, old)
        assert result.data["url_count"] == 3
        # 2 unique JS files (app.js, vendor.js)
        assert result.data["js_count"] == 2
        assert "https://test.com/app.js" in result.data["js_files"]
        assert "https://test.com/vendor.js" in result.data["js_files"]

    @pytest.mark.asyncio
    async def test_crawler_js_detection(self, registry):
        """JS files from both katana and gau are collected and deduped."""
        async def _mock_exec(name, **kw):
            if name == "katana_crawl":
                return _tool_result(data={
                    "urls": [],
                    "js_files": ["https://test.com/a.js", "https://test.com/b.js"],
                    "endpoints": [],
                })
            if name == "gau_urls":
                return _tool_result(data={
                    "urls": [],
                    "js_files": ["https://test.com/b.js", "https://test.com/c.js"],
                })
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = CrawlerAgent(registry)
        result = await agent.run({
            "alive_hosts": [{"url": "https://test.com", "priority": 5}],
        })

        js = result.data["js_files"]
        assert len(js) == 3  # a.js, b.js, c.js (deduped)
        assert result.data["js_count"] == 3

    @pytest.mark.asyncio
    async def test_crawler_endpoint_detection(self, registry):
        """API endpoints in collected URLs are detected via pattern matching."""
        async def _mock_exec(name, **kw):
            if name == "katana_crawl":
                return _tool_result(data={
                    "urls": [
                        "https://test.com/api/v1/users",
                        "https://test.com/graphql",
                        "https://test.com/about",
                    ],
                    "js_files": [],
                    "endpoints": [],
                })
            if name == "gau_urls":
                return _tool_result(data={
                    "urls": ["https://test.com/v2/items", "https://test.com/rest/data"],
                    "js_files": [],
                })
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = CrawlerAgent(registry)
        result = await agent.run({
            "alive_hosts": [{"url": "https://test.com", "priority": 5}],
        })

        endpoints = result.data["endpoints"]
        # /api/v1/users, /graphql, /v2/items, /rest/data should be detected
        assert len(endpoints) >= 4
        assert any("/api/" in e for e in endpoints)
        assert any("/graphql" in e for e in endpoints)
        assert any("/v2/" in e for e in endpoints)
        assert any("/rest/" in e for e in endpoints)

    @pytest.mark.asyncio
    async def test_crawler_max_hosts(self, registry):
        """Crawler caps at top 5 hosts by priority."""
        # Build 15 hosts
        hosts = [
            {"url": f"https://host{i}.test.com", "priority": 15 - i}
            for i in range(15)
        ]

        call_urls = []

        async def _mock_exec(name, **kw):
            if name == "katana_crawl":
                call_urls.append(kw.get("url"))
                return _tool_result(data={
                    "urls": [], "js_files": [], "endpoints": [],
                })
            if name == "gau_urls":
                return _tool_result(data={"urls": [], "js_files": []})
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = CrawlerAgent(registry)
        await agent.run({"alive_hosts": hosts})

        # katana_crawl should only be invoked for the first 5 hosts
        assert len(call_urls) == 5

    @pytest.mark.asyncio
    async def test_crawler_empty_hosts(self, registry):
        """Crawler returns early when no alive hosts are provided."""
        agent = CrawlerAgent(registry)
        result = await agent.run({"alive_hosts": []})

        assert result.success is True
        assert result.data["url_count"] == 0
        assert result.data["js_count"] == 0
        assert result.data["endpoint_count"] == 0
        registry.execute.assert_not_called()


# ===========================================================================
# 16-19: JSAnalysisAgent
# ===========================================================================


class TestJSAnalysisAgent:
    @pytest.mark.asyncio
    async def test_js_analyzer_success(self, registry):
        """JSAnalyzer finds secrets from scanned hosts."""
        async def _mock_exec(name, **kw):
            if name == "js_secrets_scan":
                return _tool_result(data={
                    "secrets_found": True,
                    "findings": [
                        {
                            "pattern": "AWS Access Key",
                            "match": "AKIAIOSFODNN7EXAMPLE",
                            "source": "inline script",
                            "context": "var key = 'AKIAIOSFODNN7EXAMPLE'",
                        },
                    ],
                })
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = JSAnalysisAgent(registry)
        result = await agent.run({
            "js_files": [],
            "alive_hosts": [{"url": "https://test.com"}],
        })

        assert result.success is True
        assert result.data["secrets_count"] == 1
        assert len(result.findings) == 1
        assert result.findings[0].vuln_type == "js_secrets"
        assert "AWS" in result.findings[0].title

    @pytest.mark.asyncio
    async def test_js_analyzer_no_secrets(self, registry):
        """JSAnalyzer returns zero findings when no secrets are detected."""
        registry.execute = AsyncMock(return_value=_tool_result(data={
            "secrets_found": False,
            "findings": [],
        }))
        agent = JSAnalysisAgent(registry)
        result = await agent.run({
            "js_files": ["https://test.com/app.js"],
            "alive_hosts": [{"url": "https://test.com"}],
        })

        assert result.success is True
        assert result.data["secrets_count"] == 0
        assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_js_analyzer_ai_validation(self, registry, llm_fn):
        """AI validation filters false positives and boosts confidence."""
        async def _mock_exec(name, **kw):
            if name == "js_secrets_scan":
                return _tool_result(data={
                    "secrets_found": True,
                    "findings": [
                        {
                            "pattern": "AWS Secret Key",
                            "match": "wJalrXUtnFEMI/K7MDENG",
                            "source": "JS",
                            "context": "",
                        },
                        {
                            "pattern": "Google Maps Key",
                            "match": "AIzaSyA-EXAMPLE-KEY",
                            "source": "JS",
                            "context": "",
                        },
                    ],
                })
            return _tool_result()

        # LLM says only index 0 is a true positive
        llm_fn.return_value = "[0]"

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = JSAnalysisAgent(registry, llm_fn=llm_fn)
        result = await agent.run({
            "js_files": [],
            "alive_hosts": [{"url": "https://test.com"}],
        })

        assert result.data["secrets_count"] == 1
        assert len(result.findings) == 1
        assert result.findings[0].confidence == 0.90  # Boosted by AI
        llm_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_js_analyzer_no_llm(self, registry):
        """JSAnalyzer works without LLM — keeps all findings at default confidence."""
        async def _mock_exec(name, **kw):
            if name == "js_secrets_scan":
                return _tool_result(data={
                    "secrets_found": True,
                    "findings": [
                        {
                            "pattern": "Generic API Key",
                            "match": "key-123-abc",
                            "source": "JS",
                            "context": "",
                        },
                    ],
                })
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = JSAnalysisAgent(registry, llm_fn=None)
        result = await agent.run({
            "js_files": [],
            "alive_hosts": [{"url": "https://test.com"}],
        })

        assert len(result.findings) == 1
        assert result.findings[0].confidence == 0.7  # Default, no AI boost


# ===========================================================================
# 20-23: VulnScanAgent
# ===========================================================================


class TestVulnScanAgent:
    @pytest.mark.asyncio
    async def test_vuln_scanner_success(self, registry):
        """VulnScanner runs all scan phases and collects findings."""
        async def _mock_exec(name, **kw):
            if name == "subdomain_takeover":
                return _tool_result(data={
                    "count": 1,
                    "vulnerable": [
                        {
                            "subdomain": "old.test.com",
                            "service": "GitHub Pages",
                            "cname": "old-test.github.io",
                        },
                    ],
                })
            if name == "nuclei_scan":
                return _tool_result(data={"count": 0, "findings": []})
            if name == "ffuf_fuzz":
                return _tool_result(data={"found": []})
            if name == "open_redirect_test":
                return _tool_result(data={
                    "vulnerable": True,
                    "findings": [
                        {"param": "next", "payload": "https://evil.com"},
                    ],
                })
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = VulnScanAgent(registry)
        result = await agent.run({
            "alive_hosts": [{"url": "https://test.com", "priority": 5}],
            "endpoints": ["https://test.com/login?next="],
            "subdomains": ["old.test.com", "test.com"],
        })

        assert result.success is True
        assert len(result.findings) == 2
        vuln_types = {f.vuln_type for f in result.findings}
        assert "subdomain_takeover" in vuln_types
        assert "open_redirect" in vuln_types

    @pytest.mark.asyncio
    async def test_vuln_scanner_nuclei_findings(self, registry):
        """VulnScanner processes nuclei template scan findings."""
        async def _mock_exec(name, **kw):
            if name == "nuclei_scan":
                return _tool_result(data={
                    "count": 1,
                    "findings": [
                        {
                            "template_id": "cve-2024-1234",
                            "name": "Critical RCE",
                            "severity": "critical",
                            "description": "Remote code execution",
                            "matched_at": "https://test.com/api",
                        },
                    ],
                })
            if name == "ffuf_fuzz":
                return _tool_result(data={"found": []})
            return _tool_result(data={})

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = VulnScanAgent(registry)
        result = await agent.run({
            "alive_hosts": [{"url": "https://test.com", "priority": 5}],
            "endpoints": [],
            "subdomains": [],
        })

        assert result.success is True
        assert len(result.findings) == 1
        f = result.findings[0]
        assert f.vuln_type == "nuclei_cve-2024-1234"
        assert f.severity == "CRITICAL"
        assert f.cvss == 9.5

    @pytest.mark.asyncio
    async def test_vuln_scanner_empty(self, registry):
        """VulnScanner returns empty findings when nothing is found."""
        async def _mock_exec(name, **kw):
            if name == "nuclei_scan":
                return _tool_result(data={"count": 0, "findings": []})
            if name == "ffuf_fuzz":
                return _tool_result(data={"found": []})
            if name == "subdomain_takeover":
                return _tool_result(data={"count": 0, "vulnerable": []})
            if name == "open_redirect_test":
                return _tool_result(data={
                    "vulnerable": False, "findings": [],
                })
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = VulnScanAgent(registry)
        result = await agent.run({
            "alive_hosts": [{"url": "https://test.com", "priority": 5}],
            "endpoints": ["https://test.com/login"],
            "subdomains": ["test.com"],
        })

        assert result.success is True
        assert len(result.findings) == 0
        assert result.data["scan_findings"] == 0

    @pytest.mark.asyncio
    async def test_vuln_scanner_partial_failure(self, registry):
        """VulnScanner handles partial tool failures gracefully."""
        async def _mock_exec(name, **kw):
            if name == "subdomain_takeover":
                raise RuntimeError("Tool crashed")
            if name == "nuclei_scan":
                return _tool_result(data={
                    "count": 1,
                    "findings": [{
                        "template_id": "cve-2024-9999",
                        "name": "XSS in admin",
                        "severity": "high",
                        "description": "Reflected XSS",
                        "matched_at": "https://test.com/admin",
                    }],
                })
            if name == "ffuf_fuzz":
                return _tool_result(data={"found": []})
            return _tool_result()

        registry.execute = AsyncMock(side_effect=_mock_exec)
        agent = VulnScanAgent(registry)
        result = await agent.run({
            "alive_hosts": [{"url": "https://test.com", "priority": 5}],
            "endpoints": [],
            "subdomains": ["test.com"],
        })

        assert result.success is True
        # Nuclei finding still collected despite takeover failure
        assert len(result.findings) >= 1
        assert any("takeover" in e for e in result.errors)


# ===========================================================================
# 24-28: AIAnalyzerAgent
# ===========================================================================


class TestAIAnalyzerAgent:
    @pytest.mark.asyncio
    async def test_ai_analyzer_success(self, registry, llm_fn):
        """AIAnalyzer classifies findings via LLM — true positives kept."""
        llm_fn.return_value = (
            '[{"index": 0, "verdict": "TRUE_POSITIVE",'
            ' "confidence": 0.95, "severity": "HIGH"}]'
        )

        findings = [
            _finding(title="XSS in admin panel", vuln_type="xss"),
        ]

        agent = AIAnalyzerAgent(registry, llm_fn=llm_fn)
        result = await agent.run({"findings": findings})

        assert result.success is True
        assert result.data["verified_count"] == 1
        assert len(result.findings) == 1
        assert result.findings[0].confidence == 0.95
        llm_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_ai_analyzer_dedup(self, registry, llm_fn):
        """AIAnalyzer removes duplicate findings (same title + vuln_type)."""
        llm_fn.return_value = (
            '[{"index": 0, "verdict": "TRUE_POSITIVE",'
            ' "confidence": 0.90, "severity": "HIGH"}]'
        )

        findings = [
            _finding(title="XSS in search", vuln_type="xss"),
            _finding(title="XSS in search", vuln_type="xss"),  # duplicate
            _finding(title="SQLi in login", vuln_type="sqli"),
        ]

        agent = AIAnalyzerAgent(registry, llm_fn=llm_fn)
        result = await agent.run({"findings": findings})

        assert result.success is True
        assert result.data["dedup_removed"] == 1
        # After dedup there should be 2 unique findings fed to AI

    @pytest.mark.asyncio
    async def test_ai_analyzer_no_findings(self, registry, llm_fn):
        """AIAnalyzer returns early when no findings are provided."""
        agent = AIAnalyzerAgent(registry, llm_fn=llm_fn)
        result = await agent.run({"findings": []})

        assert result.success is True
        assert result.data["verified_count"] == 0
        assert result.data["deep_scan_targets"] == []
        assert len(result.findings) == 0
        llm_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_ai_analyzer_heuristic_fallback(self, registry):
        """AIAnalyzer uses heuristic filtering when no LLM is available."""
        findings = [
            _finding(
                title="Real XSS",
                vuln_type="xss",
                severity="HIGH",
                confidence=0.85,
            ),
            _finding(
                title="INFO item",
                vuln_type="info",
                severity="INFO",
                confidence=0.60,
            ),
            _finding(
                title="Low confidence",
                vuln_type="xss",
                severity="MEDIUM",
                confidence=0.20,
            ),
        ]

        agent = AIAnalyzerAgent(registry, llm_fn=None)
        result = await agent.run({"findings": findings})

        assert result.success is True
        # INFO and low-confidence should be filtered out
        assert result.data["verified_count"] == 1
        assert len(result.findings) == 1
        assert result.findings[0].title == "Real XSS"

    @pytest.mark.asyncio
    async def test_ai_analyzer_deep_targets(self, registry, llm_fn):
        """AIAnalyzer identifies hosts with HIGH/CRITICAL findings for deep scan."""
        llm_fn.return_value = (
            '[{"index": 0, "verdict": "TRUE_POSITIVE",'
            ' "confidence": 0.95, "severity": "CRITICAL"}]'
        )

        findings = [
            _finding(
                title="RCE on admin.test.com",
                vuln_type="rce",
                severity="CRITICAL",
                confidence=0.90,
                poc="https://admin.test.com/exploit",
            ),
        ]

        agent = AIAnalyzerAgent(registry, llm_fn=llm_fn)
        result = await agent.run({"findings": findings})

        deep_targets = result.data["deep_scan_targets"]
        assert len(deep_targets) >= 1
        assert "admin.test.com" in deep_targets


# ===========================================================================
# 29-32: ReportAgent
# ===========================================================================


class TestReportAgent:
    @pytest.mark.asyncio
    async def test_reporter_success(self, registry, llm_fn):
        """ReportAgent generates AI-powered reports for high-confidence findings."""
        llm_fn.return_value = (
            "## Summary\nA critical XSS was found...\n"
            "## Impact\nFull account takeover."
        )

        findings = [
            _finding(title="XSS in admin", severity="HIGH", confidence=0.90),
        ]

        agent = ReportAgent(registry, llm_fn=llm_fn)
        result = await agent.run({
            "findings": findings,
            "domain": "test.com",
            "program": None,
        })

        assert result.success is True
        assert result.data["report_count"] == 1
        reports = result.data["reports"]
        assert len(reports) == 1
        assert "Summary" in reports[0]["report"]
        assert reports[0]["severity"] == "HIGH"
        llm_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_reporter_template_fallback(self, registry):
        """ReportAgent uses template-based reports when no LLM is available."""
        findings = [
            _finding(
                title="Open redirect",
                vuln_type="open_redirect",
                severity="MEDIUM",
                cvss=6.1,
                confidence=0.80,
            ),
        ]

        agent = ReportAgent(registry, llm_fn=None)
        result = await agent.run({
            "findings": findings,
            "domain": "test.com",
            "program": None,
        })

        assert result.success is True
        assert result.data["report_count"] == 1
        report_text = result.data["reports"][0]["report"]
        # Template report should contain standard sections
        assert "## Summary" in report_text
        assert "## Severity" in report_text
        assert "## Steps to Reproduce" in report_text
        assert "## Impact" in report_text
        assert "test.com" in report_text

    @pytest.mark.asyncio
    async def test_reporter_filters_low_confidence(self, registry, llm_fn):
        """ReportAgent excludes findings below the 0.70 confidence threshold."""
        llm_fn.return_value = "## Report\nDetails..."

        findings = [
            _finding(
                title="High conf XSS",
                severity="HIGH",
                confidence=0.90,
            ),
            _finding(
                title="Low conf XSS",
                severity="HIGH",
                confidence=0.50,
            ),
            _finding(
                title="Borderline XSS",
                severity="MEDIUM",
                confidence=0.69,
            ),
        ]

        agent = ReportAgent(registry, llm_fn=llm_fn)
        result = await agent.run({
            "findings": findings,
            "domain": "test.com",
            "program": None,
        })

        # Only the 0.90 confidence finding should generate a report
        assert result.data["report_count"] == 1
        assert result.data["reportable_count"] == 1
        assert result.data["skipped_low_confidence"] == 2
        assert result.data["reports"][0]["finding"] == "High conf XSS"

    @pytest.mark.asyncio
    async def test_reporter_severity_sort(self, registry, llm_fn):
        """ReportAgent orders reports: CRITICAL before HIGH before MEDIUM."""
        llm_fn.return_value = "## Report\nDetails..."

        findings = [
            _finding(
                title="Medium vuln",
                severity="MEDIUM",
                cvss=5.5,
                confidence=0.80,
            ),
            _finding(
                title="Critical vuln",
                severity="CRITICAL",
                cvss=9.8,
                confidence=0.95,
            ),
            _finding(
                title="High vuln",
                severity="HIGH",
                cvss=8.0,
                confidence=0.85,
            ),
        ]

        agent = ReportAgent(registry, llm_fn=llm_fn)
        result = await agent.run({
            "findings": findings,
            "domain": "test.com",
            "program": None,
        })

        reports = result.data["reports"]
        assert len(reports) == 3
        assert reports[0]["severity"] == "CRITICAL"
        assert reports[1]["severity"] == "HIGH"
        assert reports[2]["severity"] == "MEDIUM"

        # Findings on the result should also be sorted
        assert result.findings[0].severity == "CRITICAL"
        assert result.findings[1].severity == "HIGH"
        assert result.findings[2].severity == "MEDIUM"
