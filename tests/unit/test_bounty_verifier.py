"""Tests for src.bounty.verifier — Verifier."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.bounty.verifier import Verifier, _VULN_TO_TOOL, _SEVERITY_MULTIPLIER
from src.bounty.models import BountyFinding
from src.tools.base import ToolRegistry, ToolResult


@pytest.fixture
def registry():
    reg = MagicMock(spec=ToolRegistry)
    reg.execute = AsyncMock()
    return reg


@pytest.fixture
def verifier(registry):
    return Verifier(registry)


def _make_finding(vuln_type="sqli", severity="CRITICAL", cvss=9.8, confidence=0.6):
    return BountyFinding(
        target_id=0,
        vuln_type=vuln_type,
        severity=severity,
        cvss=cvss,
        confidence=confidence,
        title=f"{vuln_type.upper()} vulnerability",
    )


def _make_result(success=True, output="ok", data=None, error=""):
    return ToolResult(success=success, output=output, data=data or {}, error=error)


class TestVerifierInit:
    def test_init_stores_registry(self, registry):
        v = Verifier(registry)
        assert v.registry is registry


class TestVerify:
    @pytest.mark.asyncio
    async def test_verify_confirmed(self, verifier, registry):
        registry.execute = AsyncMock(
            return_value=_make_result(data={"vulnerable": True})
        )
        finding = _make_finding(confidence=0.6)
        result = await verifier.verify(finding, "https://test.com")

        assert result.confidence == 0.90
        registry.execute.assert_called_once_with("sqli_test", url="https://test.com")

    @pytest.mark.asyncio
    async def test_verify_not_reproduced(self, verifier, registry):
        registry.execute = AsyncMock(
            return_value=_make_result(data={"vulnerable": False})
        )
        finding = _make_finding(confidence=0.6)
        result = await verifier.verify(finding, "https://test.com")

        assert result.confidence == 0.40

    @pytest.mark.asyncio
    async def test_verify_tool_error_keeps_confidence(self, verifier, registry):
        registry.execute = AsyncMock(side_effect=Exception("connection error"))
        finding = _make_finding(confidence=0.6)
        result = await verifier.verify(finding, "https://test.com")

        assert result.confidence == 0.6

    @pytest.mark.asyncio
    async def test_verify_unknown_vuln_type(self, verifier, registry):
        """Finding with vuln_type not in _VULN_TO_TOOL should be returned unchanged."""
        finding = _make_finding(vuln_type="dir_enum", confidence=0.5)
        result = await verifier.verify(finding, "https://test.com")

        assert result.confidence == 0.5
        registry.execute.assert_not_called()


class TestEstimateBounty:
    def test_estimate_critical(self, verifier):
        finding = _make_finding(severity="CRITICAL")
        result = verifier.estimate_bounty(finding, bounty_low=100, bounty_high=5000)

        # CRITICAL: (0.7, 1.0) → low = 100 + 4900*0.7 = 3530, high = 100 + 4900*1.0 = 5000
        assert result.estimated_bounty_low == 3530
        assert result.estimated_bounty_high == 5000

    def test_estimate_low(self, verifier):
        finding = _make_finding(severity="LOW")
        result = verifier.estimate_bounty(finding, bounty_low=100, bounty_high=5000)

        # LOW: (0.05, 0.15) → low = 100 + 4900*0.05 = 345, high = 100 + 4900*0.15 = 835
        assert result.estimated_bounty_low == 345
        assert result.estimated_bounty_high == 835


class TestDedupFindings:
    def test_dedup_keeps_highest_confidence(self, verifier):
        findings = [
            _make_finding(vuln_type="sqli", confidence=0.6),
            _make_finding(vuln_type="sqli", confidence=0.9),
            _make_finding(vuln_type="xss", confidence=0.7),
        ]
        deduped = verifier.dedup_findings(findings)

        assert len(deduped) == 2
        sqli = [f for f in deduped if f.vuln_type == "sqli"][0]
        assert sqli.confidence == 0.9

    def test_dedup_empty(self, verifier):
        assert verifier.dedup_findings([]) == []
