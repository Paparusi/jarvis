"""Recon Tools — Security reconnaissance for authorized testing and CTF challenges.

Provides subdomain enumeration, HTTP security header analysis, CVE lookup,
reverse DNS, and web technology detection. All tools use httpx for async HTTP.
For authorized security testing and CTF challenges only.
"""

from __future__ import annotations

import asyncio
import re
import socket
import time

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.recon")

_DANGEROUS_CHARS = [";", "&", "|", "`", "$", "(", ")"]
_TIMEOUT = 15  # seconds


def _validate_input(value: str, label: str) -> str | None:
    """Validate input against injection characters. Returns error message or None."""
    if not value or not value.strip():
        return f"{label} must not be empty"
    if any(c in value for c in _DANGEROUS_CHARS):
        return f"Invalid {label}: contains disallowed characters"
    return None


def _validate_domain(domain: str) -> str | None:
    """Validate domain format. Returns error message or None."""
    err = _validate_input(domain, "domain")
    if err:
        return err
    # Basic domain pattern: alphanumeric, hyphens, dots
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$', domain):
        return f"Invalid domain format: {domain}"
    return None


def _validate_ip(ip: str) -> str | None:
    """Validate IP address format. Returns error message or None."""
    err = _validate_input(ip, "IP address")
    if err:
        return err
    # IPv4 pattern
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
        parts = ip.split(".")
        if all(0 <= int(p) <= 255 for p in parts):
            return None
    # IPv6 basic check
    if ":" in ip and re.match(r'^[0-9a-fA-F:]+$', ip):
        return None
    return f"Invalid IP address format: {ip}"


def _validate_url(url: str) -> str | None:
    """Validate URL format. Returns error message or None."""
    if not url or not url.strip():
        return "URL must not be empty"
    if not url.startswith(("http://", "https://")):
        return "URL must start with http:// or https://"
    if any(c in url for c in [";", "|", "`", "$"]):
        return "Invalid URL: contains disallowed characters"
    return None


# ---------------------------------------------------------------------------
# 1. Subdomain Enumeration (crt.sh)
# ---------------------------------------------------------------------------

async def subdomain_enum(domain: str) -> ToolResult:
    """Enumerate subdomains for a domain using the crt.sh certificate transparency API.

    Queries crt.sh for SSL certificates issued for the domain and extracts
    unique subdomain names from the results.
    """
    start = time.monotonic()

    err = _validate_domain(domain)
    if err:
        return ToolResult(success=False, output="", error=err)

    url = f"https://crt.sh/?q=%25.{domain}&output=json"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "JARVIS/2.0"})
            resp.raise_for_status()

        data = resp.json()
        elapsed = int((time.monotonic() - start) * 1000)

        if not data:
            return ToolResult(
                success=True,
                output=f"No subdomains found for {domain}",
                execution_time_ms=elapsed,
                data={"domain": domain, "subdomains": [], "count": 0},
            )

        # Extract unique subdomains, handling wildcard entries and newline-separated names
        subdomains = set()
        for entry in data:
            name_value = entry.get("name_value", "")
            for name in name_value.split("\n"):
                name = name.strip().lower()
                if name and not name.startswith("*"):
                    subdomains.add(name)

        sorted_subs = sorted(subdomains)

        lines = [
            f"Subdomain enumeration for: {domain}",
            f"Source: crt.sh (Certificate Transparency)",
            f"Found: {len(sorted_subs)} unique subdomains",
            "",
        ]
        for sub in sorted_subs:
            lines.append(f"  {sub}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={"domain": domain, "subdomains": sorted_subs, "count": len(sorted_subs)},
        )

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"crt.sh request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("subdomain_enum_error", domain=domain, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Subdomain enumeration failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 2. HTTP Security Headers Analysis
# ---------------------------------------------------------------------------

_SECURITY_HEADERS = {
    "Strict-Transport-Security": "HSTS — enforces HTTPS connections",
    "Content-Security-Policy": "CSP — prevents XSS and injection attacks",
    "X-Frame-Options": "Clickjacking protection",
    "X-Content-Type-Options": "Prevents MIME-type sniffing",
    "X-XSS-Protection": "Legacy XSS filter (deprecated but still checked)",
    "Referrer-Policy": "Controls referrer information leakage",
    "Permissions-Policy": "Controls browser feature access",
    "X-Permitted-Cross-Domain-Policies": "Controls Flash/PDF cross-domain access",
    "Cross-Origin-Opener-Policy": "COOP — isolates browsing context",
    "Cross-Origin-Resource-Policy": "CORP — controls cross-origin resource loading",
    "Cross-Origin-Embedder-Policy": "COEP — controls cross-origin embedding",
}


async def http_headers(url: str) -> ToolResult:
    """Fetch and analyze HTTP security headers for a URL.

    Checks for the presence of important security headers and rates
    the overall security posture based on headers found vs missing.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "JARVIS/2.0"})

        elapsed = int((time.monotonic() - start) * 1000)
        headers = resp.headers

        present = []
        missing = []

        for header, desc in _SECURITY_HEADERS.items():
            value = headers.get(header)
            if value:
                present.append(f"  [PRESENT] {header}: {value}")
                present.append(f"            {desc}")
            else:
                missing.append(f"  [MISSING] {header}")
                missing.append(f"            {desc}")

        total = len(_SECURITY_HEADERS)
        found = len(present) // 2  # Each present entry is 2 lines
        score = int((found / total) * 100)

        # Grade
        if score >= 80:
            grade = "A"
        elif score >= 60:
            grade = "B"
        elif score >= 40:
            grade = "C"
        elif score >= 20:
            grade = "D"
        else:
            grade = "F"

        # Additional info headers
        server = headers.get("Server", "Not disclosed")
        powered_by = headers.get("X-Powered-By", "Not disclosed")

        lines = [
            f"Security Header Analysis: {url}",
            f"Status: {resp.status_code} | Server: {server} | X-Powered-By: {powered_by}",
            f"Score: {found}/{total} ({score}%) — Grade: {grade}",
            "",
            "Present headers:",
        ]
        if present:
            lines.extend(present)
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("Missing headers:")
        if missing:
            lines.extend(missing)
        else:
            lines.append("  (none — all security headers present!)")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "url": url,
                "score": score,
                "grade": grade,
                "present_count": found,
                "total_count": total,
                "status_code": resp.status_code,
            },
        )

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"Request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("http_headers_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"HTTP header analysis failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 3. CVE Lookup (NVD API)
# ---------------------------------------------------------------------------

async def cve_lookup(query: str, max_results: int = 5) -> ToolResult:
    """Search the NIST NVD database for CVEs matching a keyword query.

    Uses the NVD CVE 2.0 API to find vulnerabilities by keyword. Returns
    CVE ID, description, severity score, and published date.
    """
    start = time.monotonic()

    err = _validate_input(query, "query")
    if err:
        return ToolResult(success=False, output="", error=err)

    max_results = max(1, min(max_results, 20))

    url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    params = {
        "keywordSearch": query,
        "resultsPerPage": max_results,
    }

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(
                url, params=params,
                headers={"User-Agent": "JARVIS/2.0"},
            )
            resp.raise_for_status()

        data = resp.json()
        elapsed = int((time.monotonic() - start) * 1000)

        total_results = data.get("totalResults", 0)
        vulnerabilities = data.get("vulnerabilities", [])

        if not vulnerabilities:
            return ToolResult(
                success=True,
                output=f"No CVEs found for query: '{query}'",
                execution_time_ms=elapsed,
                data={"query": query, "total_results": 0, "cves": []},
            )

        lines = [
            f"CVE Search: '{query}'",
            f"Total results: {total_results} (showing {len(vulnerabilities)})",
            "",
        ]

        cve_list = []
        for vuln in vulnerabilities:
            cve = vuln.get("cve", {})
            cve_id = cve.get("id", "N/A")
            published = cve.get("published", "N/A")[:10]  # Date only

            # Get description (prefer English)
            desc = "No description"
            descriptions = cve.get("descriptions", [])
            for d in descriptions:
                if d.get("lang") == "en":
                    desc = d.get("value", desc)
                    break

            # Truncate long descriptions
            if len(desc) > 200:
                desc = desc[:200] + "..."

            # Get CVSS score
            metrics = cve.get("metrics", {})
            score = "N/A"
            severity = "N/A"

            # Try CVSS v3.1 first, then v3.0, then v2.0
            for metric_key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                metric_list = metrics.get(metric_key, [])
                if metric_list:
                    cvss_data = metric_list[0].get("cvssData", {})
                    score = cvss_data.get("baseScore", "N/A")
                    severity = cvss_data.get("baseSeverity", "N/A")
                    break

            lines.append(f"  {cve_id} | Score: {score} ({severity}) | Published: {published}")
            lines.append(f"    {desc}")
            lines.append("")

            cve_list.append({
                "id": cve_id,
                "score": score,
                "severity": severity,
                "published": published,
                "description": desc,
            })

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={"query": query, "total_results": total_results, "cves": cve_list},
        )

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error="NVD API request timed out (API may be rate-limited, try again later)",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("cve_lookup_error", query=query, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"CVE lookup failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 4. Reverse DNS Lookup
# ---------------------------------------------------------------------------

async def reverse_dns(ip: str) -> ToolResult:
    """Perform reverse DNS lookup for an IP address.

    Uses socket.gethostbyaddr to resolve an IP address to its hostname.
    Runs in a thread to avoid blocking the event loop.
    """
    start = time.monotonic()

    err = _validate_ip(ip)
    if err:
        return ToolResult(success=False, output="", error=err)

    try:
        hostname, aliases, addresses = await asyncio.wait_for(
            asyncio.to_thread(socket.gethostbyaddr, ip),
            timeout=10,
        )

        elapsed = int((time.monotonic() - start) * 1000)

        lines = [
            f"Reverse DNS for: {ip}",
            f"Hostname: {hostname}",
        ]
        if aliases:
            lines.append(f"Aliases: {', '.join(aliases)}")
        if addresses:
            lines.append(f"Addresses: {', '.join(addresses)}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "ip": ip,
                "hostname": hostname,
                "aliases": aliases,
                "addresses": addresses,
            },
        )

    except socket.herror as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output=f"Reverse DNS for {ip}: No PTR record found ({e})",
            execution_time_ms=elapsed,
            data={"ip": ip, "hostname": None},
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"Reverse DNS lookup timed out for {ip}",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("reverse_dns_error", ip=ip, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Reverse DNS lookup failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 5. Technology Detection
# ---------------------------------------------------------------------------

_TECH_SIGNATURES = {
    # Server / runtime
    "nginx": {"category": "Web Server", "pattern": r"nginx", "source": "headers"},
    "Apache": {"category": "Web Server", "pattern": r"Apache", "source": "headers"},
    "IIS": {"category": "Web Server", "pattern": r"Microsoft-IIS", "source": "headers"},
    "LiteSpeed": {"category": "Web Server", "pattern": r"LiteSpeed", "source": "headers"},
    "Cloudflare": {"category": "CDN/WAF", "pattern": r"cloudflare", "source": "headers"},
    "AWS ELB": {"category": "CDN/Load Balancer", "pattern": r"awselb|ELB|amazons3", "source": "headers"},

    # Frameworks (header-based)
    "PHP": {"category": "Language", "pattern": r"PHP/[\d.]", "source": "headers"},
    "ASP.NET": {"category": "Framework", "pattern": r"ASP\.NET", "source": "headers"},
    "Express": {"category": "Framework", "pattern": r"Express", "source": "headers"},
    "Django": {"category": "Framework", "pattern": r"csrftoken|djangorestframework", "source": "headers"},

    # CMS / Frameworks (HTML-based)
    "WordPress": {"category": "CMS", "pattern": r"wp-content|wp-includes|wordpress", "source": "html"},
    "Drupal": {"category": "CMS", "pattern": r"Drupal|drupal\.js|sites/default", "source": "html"},
    "Joomla": {"category": "CMS", "pattern": r"/media/jui/|joomla", "source": "html"},
    "Shopify": {"category": "E-commerce", "pattern": r"cdn\.shopify\.com|shopify", "source": "html"},

    # JS Frameworks (HTML-based)
    "React": {"category": "JS Framework", "pattern": r"react\.production|_next/static|__NEXT_DATA__", "source": "html"},
    "Vue.js": {"category": "JS Framework", "pattern": r"vue\.js|vue\.min\.js|vue\.runtime", "source": "html"},
    "Angular": {"category": "JS Framework", "pattern": r"ng-version|angular\.js|angular\.min\.js", "source": "html"},
    "jQuery": {"category": "JS Library", "pattern": r"jquery[\.-][\d]|jquery\.min\.js", "source": "html"},
    "Bootstrap": {"category": "CSS Framework", "pattern": r"bootstrap\.min\.(css|js)|bootstrap\.css", "source": "html"},
    "Tailwind CSS": {"category": "CSS Framework", "pattern": r"tailwindcss|tailwind\.css", "source": "html"},

    # Analytics / Tools (HTML-based)
    "Google Analytics": {"category": "Analytics", "pattern": r"google-analytics\.com|gtag|GoogleAnalyticsObject", "source": "html"},
    "Google Tag Manager": {"category": "Analytics", "pattern": r"googletagmanager\.com", "source": "html"},
}


async def tech_detect(url: str) -> ToolResult:
    """Detect web technologies from HTTP headers and HTML content.

    Inspects Server, X-Powered-By, and other response headers, plus
    scans HTML source for common framework and CMS signatures.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "JARVIS/2.0"})

        elapsed = int((time.monotonic() - start) * 1000)

        headers_str = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
        html_body = resp.text[:100000]  # Limit HTML scan to 100KB

        detected = []

        for tech_name, sig in _TECH_SIGNATURES.items():
            source = sig["source"]
            pattern = sig["pattern"]
            search_text = headers_str if source == "headers" else html_body

            if re.search(pattern, search_text, re.IGNORECASE):
                detected.append({
                    "name": tech_name,
                    "category": sig["category"],
                    "source": source,
                })

        # Extract explicit header values
        server = resp.headers.get("Server", "")
        powered_by = resp.headers.get("X-Powered-By", "")
        via = resp.headers.get("Via", "")

        lines = [
            f"Technology Detection: {url}",
            f"Status: {resp.status_code}",
            "",
            "Server headers:",
            f"  Server: {server or 'Not disclosed'}",
            f"  X-Powered-By: {powered_by or 'Not disclosed'}",
            f"  Via: {via or 'Not disclosed'}",
            "",
        ]

        if detected:
            lines.append(f"Detected technologies ({len(detected)}):")
            # Group by category
            by_category: dict[str, list[str]] = {}
            for tech in detected:
                cat = tech["category"]
                by_category.setdefault(cat, []).append(tech["name"])

            for category, techs in sorted(by_category.items()):
                lines.append(f"  [{category}] {', '.join(techs)}")
        else:
            lines.append("No technologies detected from known signatures.")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "url": url,
                "detected": detected,
                "server": server,
                "powered_by": powered_by,
            },
        )

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"Request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("tech_detect_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Technology detection failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

subdomain_enum_tool = ToolDefinition(
    name="subdomain_enum",
    description=(
        "Enumerate subdomains for a domain using Certificate Transparency (crt.sh). "
        "Finds subdomains from SSL certificate records. For authorized recon and CTF."
    ),
    parameters=[
        ToolParameter(
            name="domain", type="string",
            description="Target domain (e.g. 'example.com')",
        ),
    ],
    handler=subdomain_enum,
    timeout_seconds=20,
)

http_headers_tool = ToolDefinition(
    name="http_headers",
    description=(
        "Analyze HTTP security headers for a URL. Checks for HSTS, CSP, "
        "X-Frame-Options, X-Content-Type-Options, and other security headers. "
        "Rates overall security posture with a grade (A-F)."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="Full URL to analyze (e.g. 'https://example.com')",
        ),
    ],
    handler=http_headers,
    timeout_seconds=20,
)

cve_lookup_tool = ToolDefinition(
    name="cve_lookup",
    description=(
        "Search the NIST NVD database for CVEs (Common Vulnerabilities and Exposures). "
        "Find known vulnerabilities by keyword (e.g. software name, CVE ID)."
    ),
    parameters=[
        ToolParameter(
            name="query", type="string",
            description="Search keyword (e.g. 'Apache 2.4', 'CVE-2021-44228', 'log4j')",
        ),
        ToolParameter(
            name="max_results", type="integer",
            description="Maximum results to return (1-20)",
            required=False, default=5,
        ),
    ],
    handler=cve_lookup,
    timeout_seconds=25,
)

reverse_dns_tool = ToolDefinition(
    name="reverse_dns",
    description=(
        "Reverse DNS lookup — resolve an IP address to its hostname (PTR record). "
        "Useful for identifying what domain an IP belongs to."
    ),
    parameters=[
        ToolParameter(
            name="ip", type="string",
            description="IP address to lookup (IPv4 or IPv6)",
        ),
    ],
    handler=reverse_dns,
    timeout_seconds=15,
)

tech_detect_tool = ToolDefinition(
    name="tech_detect",
    description=(
        "Detect web technologies used by a website. Analyzes HTTP headers and HTML "
        "to identify web servers, frameworks, CMS, JS libraries, CDNs, and analytics tools."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="Full URL to analyze (e.g. 'https://example.com')",
        ),
    ],
    handler=tech_detect,
    timeout_seconds=30,
)
