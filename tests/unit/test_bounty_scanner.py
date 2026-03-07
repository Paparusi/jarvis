"""Tests for src.bounty.scanner — VulnScanner and ScanResult."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.bounty.scanner import VulnScanner, ScanResult, _SCAN_TOOLS
from src.bounty.recon import ReconResult
from src.tools.base import ToolRegistry, ToolResult


@pytest.fixture
def registry():
    reg = MagicMock(spec=ToolRegistry)
    reg.execute = AsyncMock()
    return reg


@pytest.fixture
def scanner(registry):
    return VulnScanner(registry)


@pytest.fixture
def recon():
    return ReconResult(domain="test.com")


def _make_result(success=True, output="ok", data=None, error=""):
    return ToolResult(success=success, output=output, data=data or {}, error=error)


class TestScanResult:
    def test_summary_format(self):
        sr = ScanResult(target="https://test.com", waf_detected="Cloudflare")
        s = sr.summary()
        assert "https://test.com" in s
        assert "Findings: 0" in s
        assert "Cloudflare" in s

    def test_summary_no_waf(self):
        sr = ScanResult(target="https://test.com")
        s = sr.summary()
        assert "None" in s


class TestVulnScannerInit:
    def test_init_stores_registry(self, registry):
        scanner = VulnScanner(registry)
        assert scanner.registry is registry


class TestScan:
    @pytest.mark.asyncio
    async def test_scan_finds_vulnerability(self, scanner, registry, recon):
        async def mock_execute(name, **kwargs):
            if name == "waf_detect":
                return _make_result(data={"waf": None})
            if name == "sqli_test":
                return _make_result(
                    output="SQL injection found",
                    data={"vulnerable": True, "payload": "' OR 1=1--"},
                )
            return _make_result(data={"vulnerable": False})

        registry.execute = AsyncMock(side_effect=mock_execute)
        result = await scanner.scan("https://test.com", recon)

        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.vuln_type == "sqli"
        assert finding.severity == "CRITICAL"
        assert finding.cvss == 9.8
        assert finding.confidence == 0.6
        assert finding.poc == "' OR 1=1--"

    @pytest.mark.asyncio
    async def test_scan_no_vulns(self, scanner, registry, recon):
        async def mock_execute(name, **kwargs):
            return _make_result(data={"vulnerable": False})

        registry.execute = AsyncMock(side_effect=mock_execute)
        result = await scanner.scan("https://safe.com", recon)

        assert len(result.findings) == 0
        assert result.target == "https://safe.com"

    @pytest.mark.asyncio
    async def test_scan_tool_failure_handled(self, scanner, registry, recon):
        async def mock_execute(name, **kwargs):
            if name == "xss_scan":
                return _make_result(success=False, error="connection refused")
            return _make_result(data={"vulnerable": False})

        registry.execute = AsyncMock(side_effect=mock_execute)
        result = await scanner.scan("https://test.com", recon)

        assert len(result.findings) == 0
        assert any("xss_scan" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_scan_waf_detected(self, scanner, registry, recon):
        async def mock_execute(name, **kwargs):
            if name == "waf_detect":
                return _make_result(data={"waf": "Cloudflare"})
            return _make_result(data={"vulnerable": False})

        registry.execute = AsyncMock(side_effect=mock_execute)
        result = await scanner.scan("https://test.com", recon)

        assert result.waf_detected == "Cloudflare"

    @pytest.mark.asyncio
    async def test_scan_multiple_vulns(self, scanner, registry, recon):
        async def mock_execute(name, **kwargs):
            if name == "waf_detect":
                return _make_result(data={})
            if name == "sqli_test":
                return _make_result(data={"vulnerable": True, "payload": "' OR 1=1"})
            if name == "xss_scan":
                return _make_result(data={"vulnerable": True, "payload": "<script>alert(1)</script>"})
            return _make_result(data={"vulnerable": False})

        registry.execute = AsyncMock(side_effect=mock_execute)
        result = await scanner.scan("https://test.com", recon)

        assert len(result.findings) == 2
        vuln_types = {f.vuln_type for f in result.findings}
        assert "sqli" in vuln_types
        assert "xss" in vuln_types
