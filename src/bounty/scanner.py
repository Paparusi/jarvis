"""Vulnerability Scanner for the Bug Bounty Pipeline.

Runs a battery of scan tools against a target URL and produces
BountyFinding instances for any vulnerabilities discovered.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

# Sensitive paths that indicate real findings when discovered
_SENSITIVE_PATHS = {
    "/.env", "/.git", "/.git/config", "/wp-config.php", "/config.php",
    "/phpinfo.php", "/.htpasswd", "/server-status", "/server-info",
    "/debug", "/.svn", "/backup", "/db", "/.DS_Store", "/elmah.axd",
    "/trace.axd", "/actuator", "/actuator/env", "/api/swagger",
}


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

    def _extract_findings(
        self, tool_name: str, tool_result: ToolResult, url: str,
    ) -> list[BountyFinding]:
        """Extract BountyFinding instances from a tool result.

        Each tool has different data structures — handle them individually.
        """
        findings: list[BountyFinding] = []
        data = tool_result.data or {}

        # --- Tools with explicit "vulnerable" flag ---
        if tool_name in ("sqli_test", "xss_scan", "lfi_test", "cors_check"):
            if not data.get("vulnerable"):
                return []
            for st_name, vuln_type, severity, cvss in _SCAN_TOOLS:
                if st_name == tool_name:
                    # Extract individual findings from the tool
                    tool_findings = data.get("findings", [])
                    if tool_findings:
                        for tf in tool_findings:
                            payload = tf.get("payload", "") or tf.get("origin_sent", "")
                            desc = tf.get("description", "") or tool_result.output or ""
                            findings.append(BountyFinding(
                                target_id=0,
                                vuln_type=vuln_type,
                                severity=tf.get("severity", severity),
                                cvss=cvss,
                                confidence=0.7,
                                title=f"{vuln_type.upper()} on {url}",
                                description=desc[:2000],
                                poc=str(payload)[:500],
                            ))
                    else:
                        findings.append(BountyFinding(
                            target_id=0,
                            vuln_type=vuln_type,
                            severity=severity,
                            cvss=cvss,
                            confidence=0.6,
                            title=f"{vuln_type.upper()} on {url}",
                            description=(tool_result.output or "")[:2000],
                            poc=data.get("payload", ""),
                        ))
                    break
            return findings

        # --- header_audit: missing security headers ---
        if tool_name == "header_audit":
            missing = data.get("missing_count", 0)
            cookie_issues = data.get("cookie_issues", 0)
            grade = data.get("grade", "")

            # Grade D or F = significant missing headers
            if grade in ("D", "F"):
                findings.append(BountyFinding(
                    target_id=0,
                    vuln_type="missing_headers",
                    severity="LOW",
                    cvss=3.0,
                    confidence=0.9,
                    title=f"Missing security headers (Grade {grade}) on {url}",
                    description=(tool_result.output or "")[:2000],
                    poc=f"Missing {missing} headers, {cookie_issues} cookie issues",
                ))

            # Cookie issues are more interesting
            if cookie_issues >= 2:
                findings.append(BountyFinding(
                    target_id=0,
                    vuln_type="insecure_cookies",
                    severity="LOW",
                    cvss=3.5,
                    confidence=0.8,
                    title=f"Insecure cookie flags ({cookie_issues} issues) on {url}",
                    description=(tool_result.output or "")[:2000],
                    poc=f"{cookie_issues} cookies missing Secure/HttpOnly/SameSite flags",
                ))
            return findings

        # --- dir_bruteforce: sensitive files/directories ---
        if tool_name == "dir_bruteforce":
            found_dirs = data.get("found", [])
            for entry in found_dirs:
                path = entry.get("path", "")
                status = entry.get("status", 0)
                # Only flag sensitive paths with meaningful status codes
                # 200 = content accessible, 403 = exists but forbidden
                # 301/302 = redirect (often catch-all, likely false positive)
                if path.lower() in _SENSITIVE_PATHS and status in (200, 403):
                    if status == 200:
                        sev = "HIGH" if path in ("/.env", "/.git/config", "/wp-config.php") else "MEDIUM"
                        cvss = 7.5 if sev == "HIGH" else 5.0
                        confidence = 0.85
                    else:  # 403
                        sev = "LOW"
                        cvss = 2.5
                        confidence = 0.6
                    findings.append(BountyFinding(
                        target_id=0,
                        vuln_type="info_disclosure",
                        severity=sev,
                        cvss=cvss,
                        confidence=confidence,
                        title=f"Sensitive file exposed: {path} (HTTP {status}) on {url}",
                        description=f"Found {path} returning HTTP {status}",
                        poc=entry.get("url", ""),
                    ))
                # Interesting admin/debug paths accessible
                elif status == 200 and path.startswith(("/admin", "/debug", "/api/internal")):
                    findings.append(BountyFinding(
                        target_id=0,
                        vuln_type="info_disclosure",
                        severity="LOW",
                        cvss=2.0,
                        confidence=0.5,
                        title=f"Interesting path accessible: {path} on {url}",
                        description=f"Found {path} returning HTTP {status}",
                        poc=entry.get("url", ""),
                    ))
            return findings

        return findings

    async def scan(self, url: str, recon: ReconResult) -> ScanResult:
        """Scan a URL for vulnerabilities.

        1. WAF detection first.
        2. Parallel scan with all _SCAN_TOOLS.
        3. Extract findings from each tool result.
        4. Check SSL issues from recon data.
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

        # 2. Build test URL with params for injection tests
        test_url = url
        if "?" not in url:
            test_url = f"{url}?id=1"

        # 3. Parallel scan tools
        tool_names = [t[0] for t in _SCAN_TOOLS]

        async def _run_scan_tool(tool_name: str) -> tuple[str, ToolResult | None]:
            try:
                # Injection tools need URLs with params
                tool_url = test_url if tool_name in ("sqli_test", "xss_scan", "lfi_test") else url
                kwargs: dict[str, Any] = {"url": tool_url}
                # lfi_test requires a 'param' argument
                if tool_name == "lfi_test":
                    kwargs["param"] = "id"
                tr = await self.registry.execute(tool_name, **kwargs)
                return tool_name, tr
            except Exception as exc:
                result.errors.append(f"{tool_name}: {exc}")
                return tool_name, None

        tasks = [_run_scan_tool(name) for name in tool_names]
        gathered = await asyncio.gather(*tasks)

        # 4. Process results with enhanced finding extraction
        for tool_name, tool_result in gathered:
            if tool_result is None:
                continue
            result.tool_results[tool_name] = tool_result

            if not tool_result.success:
                result.errors.append(f"{tool_name}: {tool_result.error}")
                continue

            new_findings = self._extract_findings(tool_name, tool_result, url)
            result.findings.extend(new_findings)

        # 5. Check SSL issues from recon
        ssl_result = recon.tool_results.get("ssl_check")
        if ssl_result and ssl_result.success and ssl_result.data:
            not_after = ssl_result.data.get("not_after", "")
            if not_after:
                try:
                    # Parse "Jan 15 12:34:56 2025 GMT" format
                    expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y GMT")
                    expiry = expiry.replace(tzinfo=timezone.utc)
                    days_left = (expiry - datetime.now(timezone.utc)).days
                    if days_left < 0:
                        result.findings.append(BountyFinding(
                            target_id=0,
                            vuln_type="expired_ssl",
                            severity="HIGH",
                            cvss=7.0,
                            confidence=0.95,
                            title=f"SSL certificate expired {abs(days_left)} days ago on {url}",
                            description=f"Certificate expired on {not_after}",
                            poc=f"openssl s_client -connect {recon.domain}:443",
                        ))
                    elif days_left < 14:
                        result.findings.append(BountyFinding(
                            target_id=0,
                            vuln_type="expiring_ssl",
                            severity="LOW",
                            cvss=2.0,
                            confidence=0.9,
                            title=f"SSL certificate expires in {days_left} days on {url}",
                            description=f"Certificate expires on {not_after}",
                            poc=f"openssl s_client -connect {recon.domain}:443",
                        ))
                except (ValueError, TypeError):
                    pass

        log.info(
            "scan_complete",
            target=url,
            findings=len(result.findings),
            waf=result.waf_detected,
        )
        return result
