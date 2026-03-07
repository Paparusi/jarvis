"""Tests for security recon tools."""
from __future__ import annotations

import pytest

from src.tools.recon import (
    subdomain_enum_tool,
    http_headers_tool,
    cve_lookup_tool,
    reverse_dns_tool,
    tech_detect_tool,
    reverse_dns,
)


class TestToolDefinitions:
    def test_all_tools_defined(self):
        tools = [
            subdomain_enum_tool, http_headers_tool, cve_lookup_tool,
            reverse_dns_tool, tech_detect_tool,
        ]
        for tool in tools:
            assert tool.name
            assert tool.description
            assert tool.handler is not None
            assert tool.timeout_seconds > 0

    def test_tool_names(self):
        assert subdomain_enum_tool.name == "subdomain_enum"
        assert http_headers_tool.name == "http_headers"
        assert cve_lookup_tool.name == "cve_lookup"
        assert reverse_dns_tool.name == "reverse_dns"
        assert tech_detect_tool.name == "tech_detect"


class TestReverseDns:
    @pytest.mark.asyncio
    async def test_reverse_dns_localhost(self):
        result = await reverse_dns("127.0.0.1")
        assert result.success
        assert "localhost" in result.output.lower() or "127.0.0.1" in result.output

    @pytest.mark.asyncio
    async def test_reverse_dns_invalid(self):
        result = await reverse_dns("999.999.999.999")
        # Should handle gracefully — either error or "no PTR"
        assert result.output or result.error

    @pytest.mark.asyncio
    async def test_reverse_dns_empty(self):
        result = await reverse_dns("")
        assert not result.success
