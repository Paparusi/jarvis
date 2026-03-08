"""Advanced Security Tools — High-value vulnerability scanners for bug bounty.

Provides subdomain takeover detection, JS secrets scanning, open redirect
testing, and Nuclei integration. For authorized security testing only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from urllib.parse import urljoin, urlparse

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.security_advanced")

_TIMEOUT = 15
_USER_AGENT = "Mozilla/5.0 (compatible; JARVIS/2.0; +https://github.com/Paparusi/jarvis)"

# ---------------------------------------------------------------------------
# Subdomain Takeover — CNAME fingerprints
# ---------------------------------------------------------------------------

_TAKEOVER_FINGERPRINTS: dict[str, tuple[str, str]] = {
    # CNAME suffix -> (service_name, body_fingerprint)
    "s3.amazonaws.com": ("AWS S3", "NoSuchBucket"),
    "herokuapp.com": ("Heroku", "No such app"),
    "github.io": ("GitHub Pages", "There isn't a GitHub Pages site here"),
    "azurewebsites.net": ("Azure", "not found"),
    "cloudapp.net": ("Azure", "not found"),
    "myshopify.com": ("Shopify", "Sorry, this shop is currently unavailable"),
    "shopify.com": ("Shopify", "Sorry, this shop is currently unavailable"),
    "cloudfront.net": ("CloudFront", "Bad request"),
    "netlify.app": ("Netlify", "Not Found - Request ID"),
    "netlify.com": ("Netlify", "Not Found - Request ID"),
    "vercel.app": ("Vercel", "NOT_FOUND"),
    "now.sh": ("Vercel", "NOT_FOUND"),
    "fastly.net": ("Fastly", "Fastly error"),
    "web.app": ("Firebase", "not found"),
    "firebaseapp.com": ("Firebase", "not found"),
    "tumblr.com": ("Tumblr", "not found"),
    "zendesk.com": ("Zendesk", "Help Center Closed"),
    "ghost.io": ("Ghost", "not found"),
    "wordpress.com": ("WordPress.com", "doesn't exist"),
    "surge.sh": ("Surge", "project not found"),
    "bitbucket.io": ("Bitbucket", "Repository not found"),
    "pantheon.io": ("Pantheon", "not found"),
    "readme.io": ("ReadMe", "Project not found"),
    "cargo.site": ("Cargo", "not found"),
    "helpjuice.com": ("HelpJuice", "not found"),
    "helpscoutdocs.com": ("HelpScout", "not found"),
    "cargocollective.com": ("Cargo Collective", "not found"),
    "fly.dev": ("Fly.io", "not found"),
    "unbouncepages.com": ("Unbounce", "not found"),
    "trafficmanager.net": ("Azure Traffic Manager", "not found"),
}


async def _resolve_cname(subdomain: str) -> str | None:
    """Resolve CNAME for a subdomain using dig (no shell injection risk)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "dig", "+short", "CNAME", subdomain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        cname = stdout.decode().strip().rstrip(".")
        return cname if cname else None
    except (asyncio.TimeoutError, FileNotFoundError, OSError):
        return None


async def _check_takeover(
    client: httpx.AsyncClient, subdomain: str, cname: str,
) -> dict | None:
    """Check if a CNAME points to an unclaimed service."""
    for suffix, (service, fingerprint) in _TAKEOVER_FINGERPRINTS.items():
        if cname.endswith(suffix):
            # Probe HTTP for the error fingerprint
            for scheme in ("https", "http"):
                try:
                    resp = await client.get(
                        f"{scheme}://{subdomain}",
                        follow_redirects=True,
                        timeout=10,
                    )
                    body = resp.text[:5000].lower()
                    if fingerprint.lower() in body:
                        return {
                            "subdomain": subdomain,
                            "cname": cname,
                            "service": service,
                            "fingerprint": fingerprint,
                            "status": resp.status_code,
                        }
                except Exception:
                    continue
            # CNAME matches but fingerprint not found — check if unreachable
            try:
                await client.get(
                    f"http://{subdomain}", follow_redirects=False, timeout=5,
                )
            except httpx.ConnectError:
                return {
                    "subdomain": subdomain,
                    "cname": cname,
                    "service": service,
                    "fingerprint": "DNS resolves but connection refused",
                    "status": 0,
                }
            except Exception:
                pass
            return None
    return None


async def subdomain_takeover_check(subdomains: str) -> ToolResult:
    """Check subdomains for takeover vulnerabilities via dangling CNAME records."""
    start = time.time()
    sub_list = [s.strip() for s in subdomains.split(",") if s.strip()]
    if not sub_list:
        return ToolResult(
            success=False, output="", error="No subdomains provided",
            execution_time_ms=0,
        )

    sem = asyncio.Semaphore(10)
    vulnerable: list[dict] = []
    checked = 0

    async def _check_one(sub: str) -> None:
        nonlocal checked
        async with sem:
            cname = await _resolve_cname(sub)
            checked += 1
            if not cname:
                return
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                verify=False,
            ) as client:
                result = await _check_takeover(client, sub, cname)
                if result:
                    vulnerable.append(result)

    await asyncio.gather(
        *[_check_one(s) for s in sub_list],
        return_exceptions=True,
    )

    elapsed = int((time.time() - start) * 1000)
    lines = [
        f"Subdomain Takeover Check: {checked} subdomains checked",
        f"Vulnerable: {len(vulnerable)}",
    ]
    for v in vulnerable:
        lines.append(f"  [{v['service']}] {v['subdomain']} -> {v['cname']}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        data={
            "subdomains_checked": checked,
            "vulnerable": vulnerable,
            "count": len(vulnerable),
        },
        execution_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# JS Secrets Scanner — regex patterns for exposed credentials
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: dict[str, re.Pattern] = {
    "AWS Access Key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "GitHub Token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,255}"),
    "Google API Key": re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    "Stripe Secret Key": re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),
    "Stripe Publishable": re.compile(r"pk_live_[0-9a-zA-Z]{24,}"),
    "Slack Token": re.compile(r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9\-]*"),
    "SendGrid Key": re.compile(r"SG\.[a-zA-Z0-9_\-]{22}\.[a-zA-Z0-9_\-]{43}"),
    "Twilio Key": re.compile(r"SK[0-9a-fA-F]{32}"),
    "Mailgun Key": re.compile(r"key-[0-9a-zA-Z]{32}"),
    "Square OAuth": re.compile(r"sq0atp-[0-9A-Za-z\-_]{22}"),
    "Telegram Bot Token": re.compile(r"\b[0-9]{8,10}:AA[0-9A-Za-z\-_]{33}\b"),
    "Private Key": re.compile(r"-----BEGIN\s+(?:RSA|OPENSSH|EC|PGP|DSA)\s+PRIVATE\s+KEY"),
    "JWT Token": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_\-]+"),
    "Heroku API Key": re.compile(r"(?:HEROKU_API_KEY|heroku[_-]?api[_-]?key)\s*[=:]\s*['\"]?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})['\"]?"),
    "Password in URL": re.compile(r"https?://[^\s/:]{1,80}:([^\s/@]{8,64})@[a-zA-Z0-9]"),
    "AWS Secret Key": re.compile(r"(?:aws_secret_access_key|AWS_SECRET_ACCESS_KEY)\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"),
    "Generic API Key": re.compile(r"(?:api[_-]?key|apikey|api_secret)\s*[=:]\s*['\"]([A-Za-z0-9\-_]{20,})['\"]"),
}

_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


async def js_secrets_scan(url: str) -> ToolResult:
    """Scan JavaScript files linked from a webpage for exposed secrets."""
    start = time.time()

    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        follow_redirects=True,
        verify=False,
        timeout=_TIMEOUT,
    ) as client:
        try:
            resp = await client.get(url)
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return ToolResult(
                success=False, output="", error=str(exc),
                execution_time_ms=elapsed,
            )

        html = resp.text
        script_urls: list[str] = []
        for match in _SCRIPT_SRC_RE.finditer(html):
            src = match.group(1)
            if src.startswith("data:") or src.startswith("blob:"):
                continue
            absolute = urljoin(url, src)
            if absolute not in script_urls:
                script_urls.append(absolute)

        sem = asyncio.Semaphore(10)
        findings: list[dict] = []

        async def _scan_js(js_url: str) -> None:
            async with sem:
                try:
                    resp = await client.get(js_url, timeout=15)
                    if resp.status_code != 200:
                        return
                    content = resp.text
                    if len(content) > 2_000_000:
                        return
                    for pattern_name, pattern in _SECRET_PATTERNS.items():
                        for m in pattern.finditer(content):
                            match_str = m.group(0)
                            start_idx = max(0, m.start() - 30)
                            end_idx = min(len(content), m.end() + 30)
                            context = content[start_idx:end_idx].replace("\n", " ").strip()
                            findings.append({
                                "pattern": pattern_name,
                                "match": match_str[:100],
                                "source": js_url,
                                "context": context[:200],
                            })
                except Exception:
                    pass

        await asyncio.gather(
            *[_scan_js(u) for u in script_urls],
            return_exceptions=True,
        )

    elapsed = int((time.time() - start) * 1000)
    lines = [
        f"JS Secrets Scan: {url}",
        f"Scripts found: {len(script_urls)}",
        f"Secrets found: {len(findings)}",
    ]
    for f in findings[:20]:
        lines.append(f"  [{f['pattern']}] {f['match'][:60]} in {f['source']}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        data={
            "url": url,
            "scripts_found": len(script_urls),
            "scripts_scanned": len(script_urls),
            "secrets_found": len(findings) > 0,
            "findings": findings[:50],
        },
        execution_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Open Redirect Scanner
# ---------------------------------------------------------------------------

_REDIRECT_PARAMS = [
    "redirect", "redir", "next", "url", "dest", "destination", "forward",
    "return", "goto", "callback", "continue", "returnUrl", "return_url",
    "ref", "to", "target", "redirect_uri", "redirect_to", "out", "view",
    "login", "logout", "ReturnUrl", "back", "backUrl",
]

_REDIRECT_PAYLOADS = [
    "//evil.com",
    "https://evil.com",
    "////evil.com",
    "//evil.com/%2f..",
    "/\\/evil.com",
    "https:evil.com",
]


async def open_redirect_test(url: str) -> ToolResult:
    """Test URL for open redirect vulnerabilities."""
    start = time.time()

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    sem = asyncio.Semaphore(15)
    found_params: set[str] = set()
    findings: list[dict] = []
    tested = 0

    async def _test_param(param: str) -> None:
        nonlocal tested
        if param in found_params:
            return
        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=False,
            verify=False,
            timeout=10,
        ) as client:
            for payload in _REDIRECT_PAYLOADS:
                if param in found_params:
                    break
                async with sem:
                    tested += 1
                    test_url = f"{base}?{param}={payload}"
                    try:
                        resp = await client.get(test_url)
                        if resp.status_code in (301, 302, 303, 307, 308):
                            location = resp.headers.get("location", "")
                            # Check that the redirect HOST is evil.com, not just a query param reflection
                            loc_parsed = urlparse(location)
                            loc_host = (loc_parsed.hostname or "").lower()
                            # Handle protocol-relative URLs like //evil.com
                            if not loc_host and location.lstrip("/").startswith("evil.com"):
                                loc_host = "evil.com"
                            if loc_host == "evil.com":
                                found_params.add(param)
                                findings.append({
                                    "param": param,
                                    "payload": payload,
                                    "status": resp.status_code,
                                    "location": location[:200],
                                })
                                break
                        elif resp.status_code == 200:
                            body = resp.text[:3000].lower()
                            if 'http-equiv="refresh"' in body and "evil.com" in body:
                                found_params.add(param)
                                findings.append({
                                    "param": param,
                                    "payload": payload,
                                    "status": 200,
                                    "location": "meta refresh to evil.com",
                                })
                                break
                    except Exception:
                        continue

    await asyncio.gather(
        *[_test_param(p) for p in _REDIRECT_PARAMS],
        return_exceptions=True,
    )

    elapsed = int((time.time() - start) * 1000)
    lines = [
        f"Open Redirect Test: {url}",
        f"Parameters tested: {len(_REDIRECT_PARAMS)}",
        f"Payloads per param: {len(_REDIRECT_PAYLOADS)}",
        f"Total requests: {tested}",
        f"Result: {'VULNERABLE' if findings else 'Not vulnerable'}",
    ]
    for f in findings:
        lines.append(f"  ?{f['param']}={f['payload']} -> {f['status']} {f['location']}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        data={
            "url": url,
            "vulnerable": len(findings) > 0,
            "tested": tested,
            "findings": findings,
        },
        execution_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Nuclei Integration
# ---------------------------------------------------------------------------

_NUCLEI_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "bin", "nuclei",
)


_TEMPLATE_DIR = os.path.expanduser("~/nuclei-templates/http")

# Map tag names to template directories
_TAG_TO_DIRS: dict[str, str] = {
    "cves": "cves",
    "misconfigurations": "misconfiguration",
    "exposed-panels": "exposed-panels",
    "takeovers": "takeovers",
    "technologies": "technologies",
    "exposures": "exposures",
    "default-logins": "default-logins",
}


async def nuclei_scan(
    url: str, templates: str = "cves,misconfigurations,exposed-panels,takeovers",
) -> ToolResult:
    """Run Nuclei vulnerability scanner against a target URL."""
    start = time.time()

    if not os.path.isfile(_NUCLEI_PATH):
        return ToolResult(
            success=False, output="",
            error=f"Nuclei binary not found at {_NUCLEI_PATH}. "
                  "Install: download from github.com/projectdiscovery/nuclei/releases",
            execution_time_ms=0,
        )

    # Build template path args from tag names (more reliable than -tags flag)
    template_args: list[str] = []
    for tag in templates.split(","):
        tag = tag.strip()
        dir_name = _TAG_TO_DIRS.get(tag, tag)
        tpl_path = os.path.join(_TEMPLATE_DIR, dir_name)
        if os.path.isdir(tpl_path):
            template_args.extend(["-t", tpl_path])

    if not template_args:
        elapsed = int((time.time() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"No valid template directories found for: {templates}",
            execution_time_ms=elapsed,
        )

    # All args passed as separate list items — no shell injection risk
    cmd = [
        _NUCLEI_PATH,
        "-u", url,
        *template_args,
        "-jsonl",
        "-silent",
        "-no-color",
        "-timeout", "15",
        "-rate-limit", "150",
        "-bulk-size", "50",
        "-concurrency", "30",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode not in (0, None):
            err_msg = stderr.decode().strip()[:500] if stderr else f"exit code {proc.returncode}"
            elapsed = int((time.time() - start) * 1000)
            return ToolResult(
                success=False, output="", error=f"Nuclei error: {err_msg}",
                execution_time_ms=elapsed,
            )
    except asyncio.TimeoutError:
        elapsed = int((time.time() - start) * 1000)
        return ToolResult(
            success=False, output="", error="Nuclei scan timed out (120s)",
            execution_time_ms=elapsed,
        )
    except Exception as exc:
        elapsed = int((time.time() - start) * 1000)
        return ToolResult(
            success=False, output="", error=str(exc),
            execution_time_ms=elapsed,
        )

    findings: list[dict] = []
    for line in stdout.decode().strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            info = data.get("info", {})
            findings.append({
                "template_id": data.get("template-id", "unknown"),
                "name": info.get("name", "Unknown"),
                "severity": info.get("severity", "info"),
                "matched_at": data.get("matched-at", url),
                "description": info.get("description", ""),
            })
        except json.JSONDecodeError:
            continue

    findings = findings[:50]

    elapsed = int((time.time() - start) * 1000)
    lines = [
        f"Nuclei Scan: {url}",
        f"Templates: {templates}",
        f"Findings: {len(findings)}",
    ]
    for f in findings[:20]:
        lines.append(f"  [{f['severity'].upper()}] {f['name']} at {f['matched_at']}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        data={
            "url": url,
            "templates": templates,
            "findings": findings,
            "count": len(findings),
        },
        execution_time_ms=elapsed,
    )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

subdomain_takeover_tool = ToolDefinition(
    name="subdomain_takeover",
    description=(
        "Check subdomains for takeover vulnerabilities. Resolves CNAME records "
        "and probes for dangling DNS entries pointing to unclaimed services "
        "(S3, Heroku, GitHub Pages, Azure, Shopify, CloudFront, etc.). "
        "High-value bug bounty finding ($500-$5000). "
        "For authorized security testing only."
    ),
    parameters=[
        ToolParameter(
            name="subdomains", type="string",
            description="Comma-separated subdomains to check",
        ),
    ],
    handler=subdomain_takeover_check,
    timeout_seconds=120,
)

js_secrets_scan_tool = ToolDefinition(
    name="js_secrets_scan",
    description=(
        "Scan JavaScript files linked from a webpage for exposed secrets, "
        "API keys, tokens, and credentials. Searches for AWS, GitHub, Stripe, "
        "Slack, Google, JWT, private keys, SendGrid, Twilio patterns. "
        "High-value bug bounty finding ($500-$10000). "
        "For authorized security testing only."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL of the webpage to scan",
        ),
    ],
    handler=js_secrets_scan,
    timeout_seconds=60,
)

open_redirect_tool = ToolDefinition(
    name="open_redirect_test",
    description=(
        "Test URL for open redirect vulnerabilities. Injects payloads into "
        "25 common redirect parameters and checks for external redirects. "
        "Bug bounty finding ($100-$1000). "
        "For authorized security testing only."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL to test for open redirects",
        ),
    ],
    handler=open_redirect_test,
    timeout_seconds=90,
)

nuclei_scan_tool = ToolDefinition(
    name="nuclei_scan",
    description=(
        "Run Nuclei vulnerability scanner with 8000+ templates for CVEs, "
        "misconfigurations, exposed panels, default logins, and takeovers. "
        "Requires nuclei binary at bin/nuclei. "
        "For authorized security testing only."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="Target URL to scan",
        ),
        ToolParameter(
            name="templates", type="string",
            description="Comma-separated template tags",
            required=False,
            default="cves,misconfigurations,exposed-panels,takeovers",
        ),
    ],
    handler=nuclei_scan,
    timeout_seconds=300,
)
