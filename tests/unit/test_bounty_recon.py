"""Tests for src.bounty.recon — ReconEngine and ReconResult."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.bounty.recon import ReconEngine, ReconResult
from src.tools.base import ToolRegistry, ToolResult


@pytest.fixture
def registry():
    """Create a ToolRegistry with a mocked execute method."""
    reg = MagicMock(spec=ToolRegistry)
    reg.execute = AsyncMock()
    return reg


@pytest.fixture
def engine(registry):
    return ReconEngine(registry)


def _make_result(success=True, output="ok", data=None, error=""):
    return ToolResult(success=success, output=output, data=data or {}, error=error)


class TestReconResult:
    def test_summary_format(self):
        r = ReconResult(
            domain="example.com",
            subdomains=["a.example.com", "b.example.com"],
            tech_stack=["nginx"],
            cves=[{"id": "CVE-2024-0001"}],
        )
        s = r.summary()
        assert "example.com" in s
        assert "Subdomains: 2" in s
        assert "Tech stack: 1" in s
        assert "CVEs: 1" in s

    def test_summary_empty(self):
        r = ReconResult(domain="empty.com")
        s = r.summary()
        assert "empty.com" in s
        assert "Subdomains: 0" in s


class TestReconEngineInit:
    def test_init_stores_registry(self, registry):
        engine = ReconEngine(registry)
        assert engine.registry is registry


class TestRunPassive:
    @pytest.mark.asyncio
    async def test_run_passive_extracts_subdomains(self, engine, registry):
        async def mock_execute(name, **kwargs):
            if name == "subdomain_enum":
                return _make_result(data={"subdomains": ["a.test.com", "b.test.com"]})
            if name == "tech_detect":
                return _make_result(data={"technologies": ["nginx", "php"]})
            if name == "github_leaks":
                return _make_result(data={"leaks": ["api_key in .env"]})
            return _make_result()

        registry.execute = AsyncMock(side_effect=mock_execute)
        result = await engine.run_passive("test.com")

        assert result.domain == "test.com"
        assert result.subdomains == ["a.test.com", "b.test.com"]
        assert result.tech_stack == ["nginx", "php"]
        assert result.leaks == ["api_key in .env"]
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_run_passive_handles_tool_failure(self, engine, registry):
        async def mock_execute(name, **kwargs):
            if name == "subdomain_enum":
                return _make_result(success=False, error="timeout")
            return _make_result()

        registry.execute = AsyncMock(side_effect=mock_execute)
        result = await engine.run_passive("fail.com")

        assert result.domain == "fail.com"
        assert len(result.errors) >= 1
        assert any("subdomain_enum" in e for e in result.errors)
        # Should still have other tool results
        assert "tech_detect" in result.tool_results


class TestRunActive:
    @pytest.mark.asyncio
    async def test_run_active_extracts_cves(self, engine, registry):
        async def mock_execute(name, **kwargs):
            if name == "cve_lookup":
                return _make_result(data={"cves": [{"id": "CVE-2024-1234"}]})
            return _make_result()

        registry.execute = AsyncMock(side_effect=mock_execute)
        result = await engine.run_active("test.com", "https://test.com")

        assert result.cves == [{"id": "CVE-2024-1234"}]
        assert "http_headers" in result.tool_results
        assert "ssl_check" in result.tool_results


class TestRunFull:
    @pytest.mark.asyncio
    async def test_run_full_merges_passive_and_active(self, engine, registry):
        async def mock_execute(name, **kwargs):
            if name == "subdomain_enum":
                return _make_result(data={"subdomains": ["sub.test.com"]})
            if name == "tech_detect":
                return _make_result(data={"technologies": ["apache"]})
            if name == "cve_lookup":
                return _make_result(data={"cves": [{"id": "CVE-2024-9999"}]})
            return _make_result()

        registry.execute = AsyncMock(side_effect=mock_execute)
        result = await engine.run_full("test.com", "https://test.com")

        assert result.domain == "test.com"
        assert result.subdomains == ["sub.test.com"]
        assert result.tech_stack == ["apache"]
        assert result.cves == [{"id": "CVE-2024-9999"}]
        # Both passive and active tools should be present
        assert "subdomain_enum" in result.tool_results
        assert "cve_lookup" in result.tool_results
