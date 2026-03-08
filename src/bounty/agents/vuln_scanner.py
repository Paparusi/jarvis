"""VulnScanAgent — Vulnerability scanning with nuclei, ffuf, and attack tools.

Runs automated vulnerability checks against discovered assets:
- Subdomain takeover detection on enumerated subdomains
- Tech-aware nuclei template scanning (CVEs, misconfigs, exposed panels)
- Directory fuzzing with ffuf + body verification for sensitive files
- Open redirect testing on discovered endpoints
- Security header analysis (missing HSTS, CSP, X-Frame-Options)
"""

from __future__ import annotations

import asyncio
import time

import httpx as httpx_client

from src.bounty.agents.base import AgentResult, BaseHunterAgent
from src.bounty.models import BountyFinding
from src.utils.logging import get_logger

log = get_logger("bounty.agents.vuln_scanner")

# CVSS score mapping by severity string
_CVSS_MAP = {"CRITICAL": 9.5, "HIGH": 8.0, "MEDIUM": 5.5, "LOW": 3.0}

# Base templates always scanned (low overhead, high signal)
_BASE_TEMPLATES = "exposed-panels,takeovers,misconfigurations,exposures,default-logins"

# Map httpx-detected tech → additional nuclei template categories to scan
# Keys are lowercased substrings matched against host["tech"] entries
_TECH_TO_TEMPLATES: dict[str, list[str]] = {
    # Web servers
    "apache": ["vulnerabilities", "cves"],
    "nginx": ["vulnerabilities", "cves"],
    "iis": ["vulnerabilities", "cves"],
    "tomcat": ["vulnerabilities", "cves"],
    "caddy": ["vulnerabilities"],
    # Languages / Frameworks
    "php": ["vulnerabilities", "cves"],
    "wordpress": ["vulnerabilities", "cves"],
    "drupal": ["vulnerabilities", "cves"],
    "joomla": ["vulnerabilities", "cves"],
    "laravel": ["vulnerabilities"],
    "django": ["vulnerabilities"],
    "rails": ["vulnerabilities"],
    "spring": ["vulnerabilities", "cves"],
    "express": ["vulnerabilities"],
    "next.js": ["vulnerabilities"],
    "nuxt": ["vulnerabilities"],
    # CMS / Platforms
    "confluence": ["vulnerabilities", "cves"],
    "jira": ["vulnerabilities", "cves"],
    "gitlab": ["vulnerabilities", "cves"],
    "jenkins": ["vulnerabilities", "cves"],
    "grafana": ["vulnerabilities", "cves"],
    "kibana": ["vulnerabilities", "cves"],
    "sonarqube": ["vulnerabilities", "cves"],
    "sharepoint": ["vulnerabilities", "cves"],
    # Infrastructure
    "docker": ["vulnerabilities"],
    "kubernetes": ["vulnerabilities"],
    "elastic": ["vulnerabilities", "cves"],
    "redis": ["vulnerabilities"],
    "mongodb": ["vulnerabilities"],
}

# Required security headers and their severities
_SECURITY_HEADERS: dict[str, tuple[str, str]] = {
    "strict-transport-security": (
        "MEDIUM",
        "Missing HSTS header. Site vulnerable to SSL stripping attacks.",
    ),
    "content-security-policy": (
        "LOW",
        "Missing CSP header. Site more vulnerable to XSS attacks.",
    ),
    "x-frame-options": (
        "LOW",
        "Missing X-Frame-Options. Site may be vulnerable to clickjacking.",
    ),
    "x-content-type-options": (
        "LOW",
        "Missing X-Content-Type-Options. Browser may MIME-sniff responses.",
    ),
}

# Sensitive paths: (severity, expected_content_type_prefix, body_signature)
# body_signature: if present in response body, confirms it's real (not SPA catch-all)
_SENSITIVE_PATHS: dict[str, tuple[str, str, str]] = {
    "/.env": ("HIGH", "text/plain", "="),
    "/.git/config": ("HIGH", "text/plain", "[core]"),
    "/.git/HEAD": ("HIGH", "text/plain", "ref:"),
    "/.aws/credentials": ("CRITICAL", "text/plain", "aws_access_key_id"),
    "/wp-config.php": ("HIGH", "text/", "DB_"),
    "/wp-config.php.bak": ("HIGH", "text/", "DB_"),
    "/.htpasswd": ("HIGH", "text/plain", ":"),
    "/phpinfo.php": ("MEDIUM", "text/html", "phpinfo()"),
    "/server-status": ("MEDIUM", "text/html", "Apache Server Status"),
    "/server-info": ("MEDIUM", "text/html", "Apache Server Information"),
    "/actuator/env": ("HIGH", "application/json", "propertySources"),
    "/actuator/health": ("MEDIUM", "application/json", "status"),
    "/swagger.json": ("MEDIUM", "application/json", "swagger"),
    "/swagger-ui.html": ("MEDIUM", "text/html", "swagger"),
    "/graphql": ("MEDIUM", "application/json", ""),
    "/backup.sql": ("CRITICAL", "application/", "CREATE TABLE"),
    "/dump.sql": ("CRITICAL", "application/", "CREATE TABLE"),
    "/database.sql": ("CRITICAL", "application/", "CREATE TABLE"),
    "/elmah.axd": ("MEDIUM", "text/html", "Error Log"),
    "/trace.axd": ("MEDIUM", "text/html", "Trace"),
}

_USER_AGENT = "Mozilla/5.0 (compatible; JARVIS/2.0)"


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
            tasks.append(self._cors_scan(alive_hosts, findings, errors))
            tasks.append(self._api_misconfig_scan(alive_hosts, findings, errors))
            tasks.append(self._security_header_scan(alive_hosts, findings, errors))
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

    def _build_templates_for_host(self, host: dict) -> str:
        """Build nuclei template string based on detected tech stack.

        Always includes base templates. Adds CVE/vulnerability templates
        when httpx detects specific technologies (e.g. Apache, WordPress).
        """
        templates = set(_BASE_TEMPLATES.split(","))
        tech_list = host.get("tech") or []
        for tech_name in tech_list:
            tech_lower = tech_name.lower()
            for key, extra_templates in _TECH_TO_TEMPLATES.items():
                if key in tech_lower:
                    templates.update(extra_templates)
        return ",".join(sorted(templates))

    async def _nuclei_scan(
        self,
        alive_hosts: list[dict],
        findings: list[BountyFinding],
        errors: list[str],
    ) -> None:
        """Run tech-aware nuclei template scans on top alive hosts (parallel)."""
        top_hosts = [h for h in alive_hosts[:3] if h.get("url")]
        if not top_hosts:
            return

        async def _scan_one(host: dict) -> None:
            url = host["url"]
            templates = self._build_templates_for_host(host)
            try:
                result = await self.registry.execute(
                    "nuclei_scan",
                    url=url,
                    templates=templates,
                )
                if not result.success or result.data.get("count", 0) == 0:
                    return
                for nf in result.data.get("findings", []):
                    severity = nf.get("severity", "info").upper()
                    if severity == "INFO":
                        continue
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

        await asyncio.gather(*[_scan_one(h) for h in top_hosts], return_exceptions=True)

    async def _ffuf_scan(
        self,
        alive_hosts: list[dict],
        findings: list[BountyFinding],
        errors: list[str],
    ) -> None:
        """Run directory fuzzing with body verification to filter FP.

        Steps per host:
        1. ffuf finds paths returning 200/301/302
        2. Detect SPA catch-all (baseline request to random path)
        3. For sensitive paths with 200: verify content-type + body signature
        4. Drop all 403s (WAF noise, not reportable)
        """
        for host in alive_hosts[:3]:
            url = host.get("url", "")
            if not url:
                continue
            try:
                result = await self.registry.execute("ffuf_fuzz", url=url, wordlist="bounty")
                if not result.success:
                    continue

                # Detect SPA catch-all: request a random nonsense path
                baseline_size = await self._get_baseline_size(url)

                for item in result.data.get("found", []):
                    path = item.get("path", "")
                    status = item.get("status", 0)
                    length = item.get("length", 0)

                    # Only care about 200 responses (403 = WAF, not reportable)
                    if status != 200:
                        continue

                    # Check against known sensitive paths
                    path_info = _SENSITIVE_PATHS.get(path)
                    if not path_info:
                        continue
                    severity, expected_ct, body_sig = path_info

                    # SPA detection: if response size matches baseline, it's catch-all
                    if baseline_size and abs(length - baseline_size) < 500:
                        log.debug("spa_catchall_skip", path=path, url=url,
                                  length=length, baseline=baseline_size)
                        continue

                    # Verify body content for high-confidence findings
                    full_url = item.get("url", f"{url}{path}")
                    verified = await self._verify_body(
                        full_url, expected_ct, body_sig,
                    )
                    if not verified:
                        log.debug("body_verify_fail", path=path, url=url)
                        continue

                    findings.append(
                        BountyFinding(
                            target_id=0,
                            vuln_type="info_disclosure",
                            severity=severity,
                            cvss=_CVSS_MAP.get(severity, 5.0),
                            confidence=0.90,
                            title=f"Exposed: {path} (verified) on {url}",
                            description=(
                                f"Sensitive file {path} is accessible (HTTP 200) and "
                                f"response body confirmed real content (not SPA catch-all). "
                                f"Content length: {length} bytes."
                            ),
                            steps_to_reproduce=(
                                f"1. Send GET request to {full_url}\n"
                                f"2. Observe HTTP 200 with real file content"
                            ),
                            poc=full_url,
                            impact=(
                                "Sensitive file exposed on the web server leaking "
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

    async def _get_baseline_size(self, base_url: str) -> int | None:
        """Fetch a random path to detect SPA catch-all response size."""
        try:
            async with httpx_client.AsyncClient(
                timeout=10, verify=False, follow_redirects=True,
            ) as client:
                resp = await client.get(
                    f"{base_url.rstrip('/')}/jarvis_nonexistent_path_xz42q",
                    headers={"User-Agent": _USER_AGENT},
                )
                if resp.status_code == 200:
                    return len(resp.content)
        except Exception:
            pass
        return None

    async def _verify_body(
        self, url: str, expected_ct: str, body_sig: str,
    ) -> bool:
        """Fetch URL and verify content-type + body signature."""
        try:
            async with httpx_client.AsyncClient(
                timeout=10, verify=False, follow_redirects=True,
            ) as client:
                resp = await client.get(
                    url, headers={"User-Agent": _USER_AGENT},
                )
                if resp.status_code != 200:
                    return False

                ct = (resp.headers.get("content-type") or "").lower()
                body_text = resp.text[:5000].lower()

                # Content-type must match expected prefix
                if expected_ct and not ct.startswith(expected_ct):
                    return False

                # Body must contain expected signature
                if body_sig and body_sig.lower() not in body_text:
                    return False

                return True
        except Exception:
            return False

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

    async def _cors_scan(
        self,
        alive_hosts: list[dict],
        findings: list[BountyFinding],
        errors: list[str],
    ) -> None:
        """Check top hosts for CORS misconfiguration ($500-3000 bounty)."""
        for host in alive_hosts[:5]:
            url = host.get("url", "")
            if not url:
                continue
            try:
                result = await self.registry.execute("cors_check", url=url)
                if not result.success:
                    continue
                vulns = result.data.get("vulnerabilities", [])
                for v in vulns:
                    vuln_name = v.get("type", "cors_misconfiguration")
                    findings.append(
                        BountyFinding(
                            target_id=0,
                            vuln_type="cors_misconfiguration",
                            severity="HIGH",
                            cvss=7.5,
                            confidence=0.85,
                            title=f"CORS misconfiguration on {url}",
                            description=(
                                f"{vuln_name}: {v.get('description', 'CORS policy allows '
                                'untrusted origins to read responses')}. "
                                f"Origin tested: {v.get('origin', 'N/A')}"
                            ),
                            steps_to_reproduce=(
                                f"1. Send request to {url} with Origin: https://evil.com\n"
                                f"2. Check Access-Control-Allow-Origin header in response\n"
                                f"3. Observe it reflects the attacker's origin"
                            ),
                            poc=f"curl -H 'Origin: https://evil.com' {url} -v",
                            impact=(
                                "CORS misconfiguration allows any website to read "
                                "authenticated responses. Attacker can steal user data, "
                                "tokens, and perform actions on behalf of the victim."
                            ),
                            suggested_fix=(
                                "Restrict Access-Control-Allow-Origin to a whitelist "
                                "of trusted domains. Never reflect the Origin header "
                                "directly. Avoid using wildcard (*) with credentials."
                            ),
                        )
                    )
            except Exception as e:
                errors.append(f"cors {url}: {e}")

    async def _api_misconfig_scan(
        self,
        alive_hosts: list[dict],
        findings: list[BountyFinding],
        errors: list[str],
    ) -> None:
        """Check for API misconfigurations: GraphQL introspection, debug mode.

        GraphQL introspection enabled = $500-2000 on most programs.
        Debug endpoints exposed = $300-1000.
        All checks run concurrently for speed.
        """
        # Shared client for all requests (connection pooling)
        async with httpx_client.AsyncClient(
            timeout=8, verify=False, follow_redirects=True,
        ) as client:

            async def _check_graphql(base: str) -> None:
                """Check 4 GraphQL paths concurrently, stop on first hit."""
                for gql_path in ("/graphql", "/graphiql", "/api/graphql", "/v1/graphql"):
                    try:
                        gql_url = base + gql_path
                        resp = await client.post(
                            gql_url,
                            json={"query": "{__schema{types{name}}}"},
                            headers={
                                "User-Agent": _USER_AGENT,
                                "Content-Type": "application/json",
                            },
                        )
                        if resp.status_code == 200:
                            body = resp.text[:2000].lower()
                            if "__schema" in body and "types" in body:
                                findings.append(
                                    BountyFinding(
                                        target_id=0,
                                        vuln_type="graphql_introspection",
                                        severity="MEDIUM",
                                        cvss=5.3,
                                        confidence=0.95,
                                        title=f"GraphQL introspection enabled on {gql_url}",
                                        description=(
                                            "GraphQL introspection is enabled, exposing the "
                                            "full API schema including types, queries, mutations, "
                                            "and potentially sensitive fields."
                                        ),
                                        steps_to_reproduce=(
                                            f'1. Send POST to {gql_url}\n'
                                            f'2. Body: {{"query": "{{__schema{{types{{name}}}}}}"}} \n'
                                            f"3. Observe full schema returned"
                                        ),
                                        poc=gql_url,
                                        impact=(
                                            "Attackers can enumerate all API operations, "
                                            "discover internal fields, and craft targeted "
                                            "attacks against the API."
                                        ),
                                        suggested_fix=(
                                            "Disable introspection in production. "
                                            "For Apollo: introspection: false. "
                                            "For graphql-java: use IntrospectionDisabler."
                                        ),
                                    )
                                )
                                return  # Found one, stop
                    except Exception:
                        pass

            async def _check_debug(base: str, url: str) -> None:
                """Check debug endpoints concurrently."""
                for debug_path, sig in [
                    ("/debug/vars", '"cmdline"'),
                    ("/debug/pprof", "Types of profiles"),
                    ("/actuator", '"_links"'),
                    ("/actuator/env", '"propertySources"'),
                ]:
                    try:
                        debug_url = base + debug_path
                        resp = await client.get(
                            debug_url,
                            headers={"User-Agent": _USER_AGENT},
                        )
                        if resp.status_code == 200:
                            body = resp.text[:3000].lower()
                            if sig.lower() in body:
                                findings.append(
                                    BountyFinding(
                                        target_id=0,
                                        vuln_type="debug_endpoint_exposed",
                                        severity="HIGH",
                                        cvss=7.5,
                                        confidence=0.90,
                                        title=f"Debug endpoint exposed: {debug_path} on {url}",
                                        description=(
                                            f"Debug endpoint {debug_path} is accessible and "
                                            f"returns internal application information."
                                        ),
                                        steps_to_reproduce=(
                                            f"1. Navigate to {debug_url}\n"
                                            f"2. Observe debug information in response"
                                        ),
                                        poc=debug_url,
                                        impact=(
                                            "Debug endpoints leak internal state, "
                                            "environment variables, memory profiles, "
                                            "and system configuration to attackers."
                                        ),
                                        suggested_fix=(
                                            f"Disable or restrict access to {debug_path} "
                                            "in production. Use authentication or IP "
                                            "whitelisting for debug endpoints."
                                        ),
                                    )
                                )
                    except Exception:
                        pass

            # Run all host checks concurrently (top 3 hosts)
            tasks = []
            for host in alive_hosts[:3]:
                url = host.get("url", "")
                if not url:
                    continue
                base = url.rstrip("/")
                tasks.append(_check_graphql(base))
                tasks.append(_check_debug(base, url))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _security_header_scan(
        self,
        alive_hosts: list[dict],
        findings: list[BountyFinding],
        errors: list[str],
    ) -> None:
        """Check top hosts for missing security headers.

        Missing HSTS is MEDIUM severity on most programs ($100-500).
        Missing CSP/X-Frame-Options are LOW but still worth flagging.
        Only reports on HTTPS hosts with 200 responses.
        """
        async with httpx_client.AsyncClient(
            timeout=8, verify=False, follow_redirects=True,
        ) as client:

            async def _check_host(host: dict) -> None:
                url = host.get("url", "")
                if not url or not url.startswith("https://"):
                    return
                try:
                    resp = await client.get(
                        url, headers={"User-Agent": _USER_AGENT},
                    )
                    if resp.status_code != 200:
                        return
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    missing = []
                    for header, (severity, desc) in _SECURITY_HEADERS.items():
                        if header not in resp_headers:
                            missing.append((header, severity, desc))
                    # Only report if HSTS is missing (most impactful)
                    if not any(h == "strict-transport-security" for h, _, _ in missing):
                        return
                    header_list = ", ".join(h for h, _, _ in missing)
                    findings.append(
                        BountyFinding(
                            target_id=0,
                            vuln_type="missing_security_headers",
                            severity="MEDIUM",
                            cvss=4.3,
                            confidence=0.95,
                            title=f"Missing security headers on {url}",
                            description=(
                                f"The following security headers are missing: {header_list}. "
                                + " ".join(d for _, _, d in missing)
                            ),
                            steps_to_reproduce=(
                                f"1. Send GET request to {url}\n"
                                f"2. Inspect response headers\n"
                                f"3. Observe missing: {header_list}"
                            ),
                            poc=f"curl -I {url}",
                            impact=(
                                "Missing security headers reduce defense-in-depth. "
                                "HSTS prevents SSL stripping, CSP mitigates XSS, "
                                "X-Frame-Options prevents clickjacking."
                            ),
                            suggested_fix=(
                                "Add the following response headers: "
                                "Strict-Transport-Security: max-age=31536000; includeSubDomains, "
                                "Content-Security-Policy: default-src 'self', "
                                "X-Frame-Options: DENY, "
                                "X-Content-Type-Options: nosniff"
                            ),
                        )
                    )
                except Exception:
                    pass

            tasks = [_check_host(h) for h in alive_hosts[:5]]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
