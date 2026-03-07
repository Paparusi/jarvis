"""VulnScanAgent — Vulnerability scanning with nuclei, ffuf, and attack tools.

Runs automated vulnerability checks against discovered assets:
- Subdomain takeover detection on enumerated subdomains
- Nuclei template scanning (CVEs, misconfigs, exposed panels)
- Directory fuzzing with ffuf for sensitive files (.env, .git, etc.)
- Open redirect testing on discovered endpoints
"""

from __future__ import annotations

import asyncio
import time

from src.bounty.agents.base import AgentResult, BaseHunterAgent
from src.bounty.models import BountyFinding
from src.utils.logging import get_logger

log = get_logger("bounty.agents.vuln_scanner")

# CVSS score mapping by severity string
_CVSS_MAP = {"CRITICAL": 9.5, "HIGH": 8.0, "MEDIUM": 5.5, "LOW": 3.0}

# Sensitive paths worth flagging from directory fuzzing
_SENSITIVE_PATHS: dict[str, str] = {
    "/.env": "HIGH",
    "/.git": "HIGH",
    "/.git/config": "HIGH",
    "/.git/HEAD": "HIGH",
    "/.aws/credentials": "CRITICAL",
    "/wp-config.php": "HIGH",
    "/wp-config.php.bak": "HIGH",
    "/.htpasswd": "HIGH",
    "/phpinfo.php": "MEDIUM",
    "/server-status": "MEDIUM",
    "/server-info": "MEDIUM",
    "/actuator/env": "HIGH",
    "/actuator/health": "MEDIUM",
    "/swagger.json": "MEDIUM",
    "/swagger-ui.html": "MEDIUM",
    "/graphql": "MEDIUM",
    "/debug": "MEDIUM",
    "/.DS_Store": "LOW",
    "/robots.txt": "INFO",
    "/.well-known/security.txt": "INFO",
    "/crossdomain.xml": "LOW",
    "/elmah.axd": "MEDIUM",
    "/trace.axd": "MEDIUM",
    "/backup.sql": "CRITICAL",
    "/dump.sql": "CRITICAL",
    "/database.sql": "CRITICAL",
}


class VulnScanAgent(BaseHunterAgent):
    """Run nuclei, ffuf, and attack tools against discovered targets."""

    name = "vuln_scanner"

    async def run(self, context: dict) -> AgentResult:
        """Execute vulnerability scanning pipeline."""
        start = time.time()
        alive_hosts: list[dict] = context.get("alive_hosts", [])
        endpoints: list[str] = context.get("endpoints", [])
        subdomains: list[str] = context.get("subdomains", [])
        errors: list[str] = []
        findings: list[BountyFinding] = []

        # Run all scan phases concurrently where possible
        tasks = []
        if subdomains:
            tasks.append(self._check_takeover(subdomains, findings, errors))
        if alive_hosts:
            tasks.append(self._nuclei_scan(alive_hosts, findings, errors))
            tasks.append(self._ffuf_scan(alive_hosts, findings, errors))
        if endpoints:
            tasks.append(self._open_redirect_scan(endpoints, findings, errors))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        log.info(
            "vulnscan_complete",
            findings=len(findings),
            hosts=len(alive_hosts),
            endpoints=len(endpoints),
        )

        return self._make_result(
            success=True,
            data={
                "scan_findings": len(findings),
                "hosts_scanned": min(len(alive_hosts), 5),
                "endpoints_tested": min(len(endpoints), 10),
            },
            findings=findings,
            errors=errors,
            start_time=start,
        )

    async def _check_takeover(
        self,
        subdomains: list[str],
        findings: list[BountyFinding],
        errors: list[str],
    ) -> None:
        """Check subdomains for takeover vulnerabilities."""
        try:
            # Filter out wildcards and cap at 50
            clean_subs = [s for s in subdomains if not s.startswith("*")][:50]
            if not clean_subs:
                return

            result = await self.registry.execute(
                "subdomain_takeover", subdomains=",".join(clean_subs)
            )
            if result.success and result.data.get("count", 0) > 0:
                for v in result.data.get("vulnerable", []):
                    subdomain = v.get("subdomain", "unknown")
                    service = v.get("service", "unknown")
                    cname = v.get("cname", "unknown")
                    findings.append(
                        BountyFinding(
                            target_id=0,
                            vuln_type="subdomain_takeover",
                            severity="HIGH",
                            cvss=8.8,
                            confidence=0.85,
                            title=f"Subdomain takeover: {subdomain} -> {service}",
                            description=(
                                f"CNAME record for {subdomain} points to {cname} which "
                                f"resolves to an unclaimed {service} resource. An attacker "
                                f"can claim this resource and serve arbitrary content."
                            ),
                            steps_to_reproduce=(
                                f"1. Run: dig CNAME {subdomain}\n"
                                f"2. Observe CNAME points to {cname}\n"
                                f"3. Verify {service} resource is unclaimed\n"
                                f"4. Register the resource on {service} to take over"
                            ),
                            poc=f"dig CNAME {subdomain} -> {cname}",
                            impact=(
                                "An attacker can serve arbitrary content on this subdomain, "
                                "enabling phishing, cookie theft, and trust abuse."
                            ),
                            suggested_fix=(
                                f"Remove the dangling DNS record for {subdomain} or "
                                f"reclaim the {service} resource."
                            ),
                        )
                    )
        except Exception as e:
            errors.append(f"takeover: {e}")

    async def _nuclei_scan(
        self,
        alive_hosts: list[dict],
        findings: list[BountyFinding],
        errors: list[str],
    ) -> None:
        """Run nuclei template scans on top alive hosts."""
        for host in alive_hosts[:5]:
            url = host.get("url", "")
            if not url:
                continue
            try:
                result = await self.registry.execute(
                    "nuclei_scan",
                    url=url,
                    templates="cves,misconfigurations,exposed-panels,takeovers",
                )
                if not result.success or result.data.get("count", 0) == 0:
                    continue
                for nf in result.data.get("findings", []):
                    severity = nf.get("severity", "info").upper()
                    if severity == "INFO":
                        continue  # Skip informational findings
                    findings.append(
                        BountyFinding(
                            target_id=0,
                            vuln_type=f"nuclei_{nf.get('template_id', 'unknown')}",
                            severity=severity,
                            cvss=_CVSS_MAP.get(severity, 5.0),
                            confidence=0.85,
                            title=f"{nf.get('name', 'Unknown vulnerability')} on {url}",
                            description=nf.get("description", "")[:2000],
                            poc=nf.get("matched_at", url),
                            impact=f"Vulnerability detected by nuclei template {nf.get('template_id', '?')}.",
                            suggested_fix="Refer to the CVE/advisory for remediation guidance.",
                        )
                    )
            except Exception as e:
                errors.append(f"nuclei {url}: {e}")

    async def _ffuf_scan(
        self,
        alive_hosts: list[dict],
        findings: list[BountyFinding],
        errors: list[str],
    ) -> None:
        """Run directory fuzzing on top alive hosts."""
        for host in alive_hosts[:3]:
            url = host.get("url", "")
            if not url:
                continue
            try:
                result = await self.registry.execute("ffuf_fuzz", url=url, wordlist="bounty")
                if not result.success:
                    continue
                for item in result.data.get("found", []):
                    path = item.get("path", "")
                    status = item.get("status", 0)

                    # Check against known sensitive paths
                    base_severity = _SENSITIVE_PATHS.get(path)
                    if not base_severity:
                        continue
                    if base_severity == "INFO":
                        continue  # Not worth reporting
                    if status not in (200, 403):
                        continue

                    # Downgrade severity if 403 (accessible but forbidden)
                    if status == 403 and base_severity in ("HIGH", "CRITICAL"):
                        effective_severity = "MEDIUM"
                        confidence = 0.50
                    else:
                        effective_severity = base_severity
                        confidence = 0.80

                    full_url = item.get("url", f"{url}{path}")
                    findings.append(
                        BountyFinding(
                            target_id=0,
                            vuln_type="info_disclosure",
                            severity=effective_severity,
                            cvss=_CVSS_MAP.get(effective_severity, 5.0),
                            confidence=confidence,
                            title=f"Sensitive file: {path} (HTTP {status}) on {url}",
                            description=(
                                f"Directory fuzzing revealed {path} returning HTTP {status}. "
                                f"Content length: {item.get('length', 'unknown')} bytes."
                            ),
                            steps_to_reproduce=(
                                f"1. Send GET request to {full_url}\n"
                                f"2. Observe HTTP {status} response"
                            ),
                            poc=full_url,
                            impact=(
                                "Sensitive files exposed on the web server may leak "
                                "credentials, source code, configuration, or internal data."
                            ),
                            suggested_fix=(
                                f"Remove or restrict access to {path}. Configure the web "
                                "server to deny access to sensitive files and directories."
                            ),
                        )
                    )
            except Exception as e:
                errors.append(f"ffuf {url}: {e}")

    async def _open_redirect_scan(
        self,
        endpoints: list[str],
        findings: list[BountyFinding],
        errors: list[str],
    ) -> None:
        """Test endpoints for open redirect vulnerabilities."""
        for endpoint in endpoints[:10]:
            try:
                result = await self.registry.execute("open_redirect_test", url=endpoint)
                if not result.success or not result.data.get("vulnerable"):
                    continue
                for rf in result.data.get("findings", []):
                    param = rf.get("param", "?")
                    payload = rf.get("payload", "")
                    findings.append(
                        BountyFinding(
                            target_id=0,
                            vuln_type="open_redirect",
                            severity="MEDIUM",
                            cvss=6.1,
                            confidence=0.80,
                            title=f"Open redirect via '{param}' on {endpoint}",
                            description=(
                                f"Parameter '{param}' accepts arbitrary URLs and redirects "
                                f"the user to an attacker-controlled domain."
                            ),
                            steps_to_reproduce=(
                                f"1. Navigate to {endpoint}\n"
                                f"2. Set parameter '{param}' to an external URL\n"
                                f"3. Observe redirect to attacker domain"
                            ),
                            poc=f"param={param}, payload={payload}",
                            impact=(
                                "Open redirect can be chained with phishing attacks. "
                                "Victims trust the original domain and may enter credentials "
                                "on the attacker's site. Can also be used for OAuth token theft."
                            ),
                            suggested_fix=(
                                "Validate redirect targets against a whitelist of allowed "
                                "domains. Use relative URLs instead of accepting full URLs. "
                                "Implement a warning page before redirecting to external sites."
                            ),
                        )
                    )
            except Exception as e:
                errors.append(f"redirect {endpoint}: {e}")
