"""Vulnerability Scanner for the Bug Bounty Pipeline.

Runs a battery of scan tools against a target URL and produces
BountyFinding instances for any vulnerabilities discovered.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.bounty.models import BountyFinding
from src.bounty.recon import ReconResult
from src.tools.base import ToolRegistry, ToolResult
from src.utils.logging import get_logger

log = get_logger("bounty.scanner")

# (tool_name, vuln_type, severity, cvss_score)
_SCAN_TOOLS: list[tuple[str, str, str, float]] = [
    ("sqli_test", "sqli", "CRITICAL", 9.8),
    ("xss_scan", "xss", "HIGH", 7.5),
    ("lfi_test", "lfi", "HIGH", 8.0),
    ("cors_check", "cors", "MEDIUM", 5.3),
    ("header_audit", "headers", "LOW", 3.0),
    ("dir_bruteforce", "dir_enum", "INFO", 0.0),
]


@dataclass
class ScanResult:
    """Aggregated results from a vulnerability scan."""

    target: str = ""
    findings: list[BountyFinding] = field(default_factory=list)
    tool_results: dict[str, ToolResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    waf_detected: str | None = None

    def summary(self) -> str:
        """Human-readable summary of the scan results."""
        lines = [
            f"Scan Summary: {self.target}",
            f"  Findings: {len(self.findings)}",
            f"  WAF: {self.waf_detected or 'None'}",
            f"  Errors: {len(self.errors)}",
        ]
        return "\n".join(lines)


class VulnScanner:
    """Runs vulnerability scan tools against a target URL."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.registry = tool_registry

    async def scan(self, url: str, recon: ReconResult) -> ScanResult:
        """Scan a URL for vulnerabilities.

        1. WAF detection first.
        2. Parallel scan with all _SCAN_TOOLS.
        3. Create BountyFinding for each vulnerable result.
        """
        result = ScanResult(target=url)

        # 1. WAF detection
        try:
            waf_result = await self.registry.execute("waf_detect", url=url)
            result.tool_results["waf_detect"] = waf_result
            if waf_result.success and waf_result.data:
                result.waf_detected = waf_result.data.get("waf")
        except Exception as exc:
            result.errors.append(f"waf_detect: {exc}")

        # 2. Parallel scan tools
        tool_names = [t[0] for t in _SCAN_TOOLS]

        async def _run_scan_tool(tool_name: str) -> tuple[str, ToolResult | None]:
            try:
                tr = await self.registry.execute(tool_name, url=url)
                return tool_name, tr
            except Exception as exc:
                result.errors.append(f"{tool_name}: {exc}")
                return tool_name, None

        tasks = [_run_scan_tool(name) for name in tool_names]
        gathered = await asyncio.gather(*tasks)

        # 3. Process results
        for tool_name, tool_result in gathered:
            if tool_result is None:
                continue
            result.tool_results[tool_name] = tool_result

            if not tool_result.success:
                result.errors.append(f"{tool_name}: {tool_result.error}")
                continue

            # Check if vulnerable
            if tool_result.data and tool_result.data.get("vulnerable"):
                # Find the matching scan tool config
                for st_name, vuln_type, severity, cvss in _SCAN_TOOLS:
                    if st_name == tool_name:
                        output = tool_result.output or ""
                        finding = BountyFinding(
                            target_id=0,
                            vuln_type=vuln_type,
                            severity=severity,
                            cvss=cvss,
                            confidence=0.6,
                            title=f"{vuln_type.upper()} vulnerability found",
                            description=output,
                            poc=tool_result.data.get("payload", ""),
                        )
                        result.findings.append(finding)
                        break

        log.info(
            "scan_complete",
            target=url,
            findings=len(result.findings),
            waf=result.waf_detected,
        )
        return result
