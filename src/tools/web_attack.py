"""Web Attack Tools — Pentest/security testing for authorized targets and CTF challenges.

Provides directory bruteforce, SQL injection testing, XSS scanning, CORS
misconfiguration detection, WAF fingerprinting, LFI testing, and security
header auditing. All tools use httpx for async HTTP.
For authorized security testing and CTF challenges only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.web_attack")

_TIMEOUT = 15  # seconds per request
_USER_AGENT = "JARVIS/2.0"

_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "metadata.google.internal", "169.254.169.254",
}


def _validate_url(url: str) -> str | None:
    """Validate URL format and block SSRF targets. Returns error message or None."""
    if not url or not url.strip():
        return "URL must not be empty"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "URL must start with http:// or https://"
    if not parsed.netloc:
        return "Invalid URL — missing hostname"
    if any(c in url for c in [";", "|", "`", "$"]):
        return "Invalid URL: contains disallowed characters"

    hostname = parsed.hostname or ""
    if hostname.lower() in _BLOCKED_HOSTS:
        return "Blocked: internal/private host"
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return "Blocked: private/loopback IP address"
    except ValueError:
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            for _, _, _, _, addr in resolved:
                ip = ipaddress.ip_address(addr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return "Blocked: domain resolves to private IP"
        except (socket.gaierror, OSError):
            pass
    return None


# ---------------------------------------------------------------------------
# Wordlists for directory bruteforce
# ---------------------------------------------------------------------------

_WORDLISTS: dict[str, list[str]] = {
    "common": [
        "/admin", "/login", "/dashboard", "/api", "/wp-admin", "/wp-login.php",
        "/phpmyadmin", "/.env", "/config", "/backup", "/.git", "/.git/HEAD",
        "/.gitignore", "/robots.txt", "/sitemap.xml", "/.htaccess",
        "/wp-content", "/wp-includes", "/administrator", "/admin/login",
        "/console", "/manager", "/cpanel", "/webmail", "/cgi-bin",
        "/server-status", "/server-info", "/.svn", "/.svn/entries",
        "/config.php", "/config.yml", "/config.json", "/web.config",
        "/.DS_Store", "/crossdomain.xml", "/favicon.ico", "/humans.txt",
        "/info.php", "/phpinfo.php", "/test", "/test.php", "/debug",
        "/status", "/health", "/healthcheck", "/ping", "/version",
        "/readme.html", "/README.md", "/CHANGELOG.md", "/LICENSE",
        "/package.json", "/composer.json", "/Gemfile", "/requirements.txt",
        "/.well-known/security.txt", "/security.txt",
    ],
    "small": [
        "/admin", "/login", "/.env", "/.git/HEAD", "/robots.txt",
        "/api", "/config", "/backup", "/wp-admin", "/phpmyadmin",
        "/dashboard", "/console", "/status", "/health", "/test",
        "/sitemap.xml", "/.htaccess", "/server-status", "/info.php",
        "/debug",
    ],
    "medium": [
        "/admin", "/login", "/dashboard", "/api", "/wp-admin", "/wp-login.php",
        "/phpmyadmin", "/.env", "/config", "/backup", "/.git", "/.git/HEAD",
        "/robots.txt", "/sitemap.xml", "/.htaccess", "/wp-content",
        "/administrator", "/console", "/manager", "/cpanel", "/cgi-bin",
        "/server-status", "/.svn", "/config.php", "/config.yml",
        "/.DS_Store", "/phpinfo.php", "/test", "/debug", "/status",
        "/health", "/version", "/readme.html", "/package.json",
        "/security.txt", "/graphql", "/swagger", "/api/v1", "/api/v2",
        "/docs", "/uploads", "/images", "/static", "/assets",
        "/media", "/files", "/download", "/tmp", "/temp",
        "/old", "/new", "/dev", "/staging", "/beta",
        "/internal", "/private", "/secret", "/hidden",
        "/.env.bak", "/db", "/database", "/sql", "/data",
        "/log", "/logs", "/error_log", "/access_log",
        "/wp-json", "/xmlrpc.php", "/feed", "/rss",
        "/app", "/application", "/portal", "/panel",
        "/user", "/users", "/account", "/profile", "/register",
        "/signup", "/signin", "/auth", "/oauth", "/sso",
        "/forgot", "/reset", "/password", "/2fa",
    ],
    "api": [
        "/api", "/api/v1", "/api/v2", "/api/v3", "/api/v1/users",
        "/api/v1/auth", "/api/v1/login", "/api/v1/admin",
        "/swagger", "/swagger-ui", "/swagger-ui.html", "/swagger.json",
        "/swagger.yaml", "/api-docs", "/api/docs", "/docs",
        "/graphql", "/graphiql", "/playground",
        "/health", "/healthz", "/health/live", "/health/ready",
        "/status", "/info", "/version", "/metrics", "/prometheus",
        "/openapi", "/openapi.json", "/openapi.yaml", "/spec",
        "/api/config", "/api/debug", "/api/test", "/api/status",
        "/api/health", "/api/info", "/api/version",
        "/rest", "/rest/api", "/json", "/xml", "/rpc",
        "/oauth/token", "/oauth/authorize", "/token", "/auth/token",
        "/.well-known/openid-configuration", "/userinfo",
        "/api/users", "/api/admin", "/api/upload", "/api/files",
        "/api/search", "/api/export", "/api/import",
    ],
    "backup": [
        "/backup", "/backup.zip", "/backup.tar.gz", "/backup.sql",
        "/db.sql", "/dump.sql", "/database.sql", "/data.sql",
        "/backup.bak", "/site.bak", "/web.bak",
        "/old", "/old.zip", "/archive", "/archive.zip",
        "/.bak", "/index.bak", "/index.php.bak", "/config.bak",
        "/config.php.bak", "/wp-config.php.bak",
        "/.env.bak", "/.env.old", "/.env.backup",
        "/db-backup", "/sql-backup", "/mysql-dump",
        "/export.sql", "/export.csv", "/export.json",
        "/temp", "/tmp", "/cache",
        "/debug.log", "/error.log", "/access.log",
        "/.git/config", "/.git/HEAD", "/.svn/entries",
        "/composer.lock", "/package-lock.json", "/yarn.lock",
        "/Dockerfile", "/docker-compose.yml", "/.dockerenv",
        "/.aws/credentials", "/.ssh/id_rsa", "/id_rsa",
    ],
    "bounty": [
        # Env/config files (high value)
        "/.env", "/.env.bak", "/.env.local", "/.env.production", "/.env.staging",
        "/.env.old", "/.env.dev", "/.env.example", "/.env.backup",
        "/config.php", "/config.yml", "/config.json", "/config.bak",
        "/wp-config.php", "/wp-config.php.bak", "/wp-config.php.old",
        "/web.config", "/appsettings.json", "/application.yml", "/application.properties",
        # Git exposure
        "/.git", "/.git/HEAD", "/.git/config", "/.git/index", "/.gitignore",
        "/.git/logs/HEAD", "/.git/refs/heads/master", "/.git/refs/heads/main",
        # Source maps (JS secrets)
        "/main.js.map", "/app.js.map", "/bundle.js.map", "/vendor.js.map",
        "/static/js/main.js.map", "/static/js/app.js.map",
        "/assets/js/app.js.map", "/dist/main.js.map",
        # API docs / GraphQL
        "/swagger.json", "/swagger.yaml", "/swagger-ui.html", "/swagger-ui/",
        "/openapi.json", "/openapi.yaml", "/api-docs", "/api-docs.json",
        "/graphql", "/graphiql", "/playground", "/altair",
        "/api/v1", "/api/v2", "/api/v3",
        # Debug / Monitoring endpoints
        "/actuator", "/actuator/env", "/actuator/heapdump", "/actuator/configprops",
        "/actuator/mappings", "/actuator/beans", "/actuator/trace",
        "/debug", "/debug/pprof", "/debug/vars", "/__debug__",
        "/metrics", "/prometheus", "/health", "/healthz",
        "/server-status", "/server-info", "/status",
        "/elmah.axd", "/trace.axd", "/glimpse.axd",
        # Cloud metadata
        "/.aws/credentials", "/.aws/config",
        "/metadata", "/latest/meta-data",
        "/.docker/config.json", "/docker-compose.yml", "/docker-compose.yaml",
        "/Dockerfile",
        # WordPress
        "/wp-json/wp/v2/users", "/xmlrpc.php", "/wp-login.php",
        "/wp-content/debug.log", "/wp-content/uploads/",
        "/wp-includes/version.php",
        # Laravel / PHP
        "/storage/logs/laravel.log", "/_ignition/health-check",
        "/telescope", "/horizon", "/nova",
        "/phpinfo.php", "/info.php", "/php_info.php",
        "/.htpasswd", "/.htaccess",
        # Node.js
        "/package.json", "/package-lock.json", "/.npmrc",
        "/node_modules/.package-lock.json",
        "/yarn.lock", "/.yarnrc",
        # CI/CD
        "/.github/workflows", "/.gitlab-ci.yml", "/Jenkinsfile",
        "/.circleci/config.yml", "/.travis.yml",
        "/.github/CODEOWNERS",
        # Backup / Database
        "/backup.sql", "/dump.sql", "/backup.zip", "/backup.tar.gz",
        "/data.json", "/data.sql", "/export.csv", "/export.json",
        "/db.sql", "/database.sql", "/site.bak", "/web.bak",
        # Admin panels
        "/admin", "/admin/login", "/manage", "/console", "/terminal",
        "/portal", "/dashboard", "/cpanel", "/panel",
        "/administrator", "/phpmyadmin", "/adminer", "/adminer.php",
        # Security / Well-known
        "/.well-known/security.txt", "/robots.txt", "/sitemap.xml",
        "/crossdomain.xml", "/clientaccesspolicy.xml",
        "/security.txt", "/humans.txt",
        # SVN / Hg
        "/.svn", "/.svn/entries", "/.svn/wc.db",
        "/.hg", "/.hgrc",
        # Misc sensitive
        "/.DS_Store", "/Thumbs.db",
        "/composer.json", "/composer.lock",
        "/Gemfile", "/Gemfile.lock",
        "/requirements.txt", "/Pipfile", "/Pipfile.lock",
        "/id_rsa", "/.ssh/id_rsa", "/.ssh/authorized_keys",
        "/error_log", "/access_log", "/debug.log",
        # Spring Boot
        "/env", "/configprops", "/mappings", "/beans", "/autoconfig",
        # Django
        "/__debug__/", "/admin/doc/", "/api/schema/",
        # Ruby on Rails
        "/rails/info", "/rails/info/routes",
        # ASP.NET
        "/elmah", "/Elmah.axd", "/trace.axd",
        # Firebase
        "/.firebaserc", "/firebase.json", "/firestore.rules",
        # Kubernetes
        "/.kube/config", "/api/v1/namespaces",
    ],
}


# ---------------------------------------------------------------------------
# 1. Directory Bruteforce
# ---------------------------------------------------------------------------

async def dir_bruteforce(url: str, wordlist: str = "common") -> ToolResult:
    """Enumerate directories and paths on a target web server.

    Sends concurrent HEAD requests using a built-in wordlist to discover
    hidden paths, backup files, admin panels, and configuration files.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    if wordlist not in _WORDLISTS:
        return ToolResult(
            success=False, output="",
            error=f"Invalid wordlist: {wordlist}. Choose from: {', '.join(_WORDLISTS.keys())}",
        )

    paths = _WORDLISTS[wordlist]
    base_url = url.rstrip("/")
    found: list[dict[str, str | int]] = []
    semaphore = asyncio.Semaphore(10)

    async def _check_path(client: httpx.AsyncClient, path: str) -> dict[str, str | int] | None:
        async with semaphore:
            target = base_url + path
            try:
                resp = await client.head(
                    target,
                    headers={"User-Agent": _USER_AGENT},
                    follow_redirects=False,
                )
                if resp.status_code in (200, 301, 302, 403):
                    return {
                        "path": path,
                        "status": resp.status_code,
                        "url": target,
                    }
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError):
                pass
            return None

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            tasks = [_check_path(client, path) for path in paths]
            results = await asyncio.gather(*tasks)

        elapsed = int((time.monotonic() - start) * 1000)
        found = [r for r in results if r is not None]

        if not found:
            return ToolResult(
                success=True,
                output=f"Directory bruteforce on {base_url}\nWordlist: {wordlist} ({len(paths)} paths)\nNo accessible paths found.",
                execution_time_ms=elapsed,
                data={"url": base_url, "wordlist": wordlist, "found": [], "count": 0},
            )

        lines = [
            f"Directory bruteforce: {base_url}",
            f"Wordlist: {wordlist} ({len(paths)} paths)",
            f"Found: {len(found)} accessible paths",
            "",
        ]
        for entry in sorted(found, key=lambda x: x["status"]):
            status = entry["status"]
            if status == 200:
                label = "OK"
            elif status == 301:
                label = "REDIRECT"
            elif status == 302:
                label = "REDIRECT"
            else:
                label = "FORBIDDEN"
            lines.append(f"  [{status} {label}] {entry['path']}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={"url": base_url, "wordlist": wordlist, "found": found, "count": len(found)},
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("dir_bruteforce_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Directory bruteforce failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 2. SQL Injection Test
# ---------------------------------------------------------------------------

_SQLI_PAYLOADS = [
    "' OR 1=1--",
    '" OR 1=1--',
    "'; DROP TABLE--",
    "1 UNION SELECT NULL--",
    "1' AND '1'='1",
    "1 OR 1=1",
    "' OR ''='",
    "1; WAITFOR DELAY '0:0:5'--",
    "' OR 1=1#",
    "admin'--",
]

_SQLI_ERROR_PATTERNS = [
    # MySQL
    (r"SQL syntax.*MySQL", "MySQL"),
    (r"Warning.*mysql_", "MySQL"),
    (r"MySqlException", "MySQL"),
    (r"valid MySQL result", "MySQL"),
    (r"check the manual that corresponds to your MySQL server version", "MySQL"),
    # PostgreSQL
    (r"PostgreSQL.*ERROR", "PostgreSQL"),
    (r"Warning.*\bpg_", "PostgreSQL"),
    (r"valid PostgreSQL result", "PostgreSQL"),
    (r"Npgsql\.", "PostgreSQL"),
    (r"PG::SyntaxError", "PostgreSQL"),
    # SQLite
    (r"SQLite/JDBCDriver", "SQLite"),
    (r"SQLite\.Exception", "SQLite"),
    (r"System\.Data\.SQLite\.SQLiteException", "SQLite"),
    (r"Warning.*sqlite_", "SQLite"),
    (r"SQLite3::SQLException", "SQLite"),
    (r"\[SQLITE_ERROR\]", "SQLite"),
    # MSSQL
    (r"Driver.*SQL[\-\_\ ]*Server", "MSSQL"),
    (r"OLE DB.*SQL Server", "MSSQL"),
    (r"\bSQL Server\b.*Driver", "MSSQL"),
    (r"Warning.*mssql_", "MSSQL"),
    (r"Msg \d+, Level \d+, State \d+", "MSSQL"),
    (r"Unclosed quotation mark after the character string", "MSSQL"),
    # Oracle
    (r"\bORA-\d{5}", "Oracle"),
    (r"Oracle error", "Oracle"),
    (r"Warning.*oci_", "Oracle"),
    # Generic
    (r"quoted string not properly terminated", "Generic SQL"),
    (r"SQL command not properly ended", "Generic SQL"),
    (r"unterminated quoted string", "Generic SQL"),
    (r"you have an error in your SQL syntax", "Generic SQL"),
]


async def sqli_test(url: str, method: str = "GET", params: str = "") -> ToolResult:
    """Test a URL for SQL injection vulnerabilities.

    Sends common SQLi payloads and checks responses for SQL error message
    patterns that indicate the input was interpreted as SQL.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    method = method.upper()
    if method not in ("GET", "POST"):
        return ToolResult(success=False, output="", error="Method must be GET or POST")

    # Parse target params
    parsed = urlparse(url)
    if method == "GET":
        existing_params = parse_qs(parsed.query, keep_blank_values=True)
        if not existing_params and not params:
            return ToolResult(
                success=False, output="",
                error="No query parameters found in URL. Add ?param=value or specify params.",
            )
        if params:
            for p in params.split(","):
                p = p.strip()
                if p and p not in existing_params:
                    existing_params[p] = ["test"]
    else:
        if not params:
            return ToolResult(
                success=False, output="",
                error="POST method requires params (comma-separated param names to test).",
            )
        existing_params = {p.strip(): ["test"] for p in params.split(",") if p.strip()}

    vulnerabilities: list[dict[str, str]] = []
    tested_count = 0

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            # Get baseline response first (establishes connection)
            await client.get(url, headers={"User-Agent": _USER_AGENT})

            for param_name in existing_params:
                for payload in _SQLI_PAYLOADS:
                    tested_count += 1

                    if method == "GET":
                        test_params = dict(existing_params)
                        test_params[param_name] = [payload]
                        query_string = urlencode(test_params, doseq=True)
                        test_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query_string}"
                        resp = await client.get(
                            test_url,
                            headers={"User-Agent": _USER_AGENT},
                        )
                    else:
                        post_data = {param_name: payload}
                        resp = await client.post(
                            url,
                            data=post_data,
                            headers={"User-Agent": _USER_AGENT},
                        )

                    body = resp.text
                    for pattern, db_type in _SQLI_ERROR_PATTERNS:
                        if re.search(pattern, body, re.IGNORECASE):
                            vulnerabilities.append({
                                "param": param_name,
                                "payload": payload,
                                "db_type": db_type,
                                "pattern": pattern,
                                "status": resp.status_code,
                            })
                            break

        elapsed = int((time.monotonic() - start) * 1000)

        if not vulnerabilities:
            return ToolResult(
                success=True,
                output=(
                    f"SQL Injection Test: {url}\n"
                    f"Method: {method} | Payloads tested: {tested_count}\n"
                    f"Result: No SQL injection indicators detected.\n"
                    f"Note: This does not guarantee the target is safe — "
                    f"blind/time-based SQLi requires deeper testing."
                ),
                execution_time_ms=elapsed,
                data={"url": url, "vulnerable": False, "tested": tested_count},
            )

        # Deduplicate by param
        seen = set()
        unique_vulns = []
        for v in vulnerabilities:
            key = (v["param"], v["db_type"])
            if key not in seen:
                seen.add(key)
                unique_vulns.append(v)

        lines = [
            f"SQL Injection Test: {url}",
            f"Method: {method} | Payloads tested: {tested_count}",
            f"VULNERABLE — {len(unique_vulns)} finding(s):",
            "",
        ]
        for v in unique_vulns:
            lines.append(f"  Parameter: {v['param']}")
            lines.append(f"  Payload:   {v['payload']}")
            lines.append(f"  DB Type:   {v['db_type']}")
            lines.append(f"  Status:    {v['status']}")
            lines.append("")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "url": url,
                "vulnerable": True,
                "findings": unique_vulns,
                "tested": tested_count,
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
        log.error("sqli_test_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"SQL injection test failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 3. XSS Scan
# ---------------------------------------------------------------------------

_XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "<svg onload=alert(1)>",
    "'><marquee>xss</marquee>",
    "<body onload=alert(1)>",
    "'-alert(1)-'",
    "<iframe src=javascript:alert(1)>",
]


async def xss_scan(url: str, params: str = "") -> ToolResult:
    """Scan a URL for reflected XSS vulnerabilities.

    Injects XSS payloads into query parameters and checks if they appear
    unescaped in the response body, indicating potential reflected XSS.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    parsed = urlparse(url)
    existing_params = parse_qs(parsed.query, keep_blank_values=True)

    if params:
        for p in params.split(","):
            p = p.strip()
            if p and p not in existing_params:
                existing_params[p] = ["test"]

    if not existing_params:
        return ToolResult(
            success=False, output="",
            error="No parameters to test. Add ?param=value to URL or specify params.",
        )

    vulnerabilities: list[dict[str, str]] = []
    tested_count = 0

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            for param_name in existing_params:
                for payload in _XSS_PAYLOADS:
                    tested_count += 1
                    test_params = dict(existing_params)
                    test_params[param_name] = [payload]
                    query_string = urlencode(test_params, doseq=True)
                    test_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query_string}"

                    resp = await client.get(
                        test_url,
                        headers={"User-Agent": _USER_AGENT},
                    )
                    body = resp.text

                    if payload in body:
                        vulnerabilities.append({
                            "param": param_name,
                            "payload": payload,
                            "status": resp.status_code,
                            "context": "reflected_unescaped",
                        })

        elapsed = int((time.monotonic() - start) * 1000)

        if not vulnerabilities:
            return ToolResult(
                success=True,
                output=(
                    f"XSS Scan: {url}\n"
                    f"Parameters tested: {list(existing_params.keys())}\n"
                    f"Payloads tested: {tested_count}\n"
                    f"Result: No reflected XSS detected.\n"
                    f"Note: DOM-based and stored XSS require manual testing."
                ),
                execution_time_ms=elapsed,
                data={"url": url, "vulnerable": False, "tested": tested_count},
            )

        # Deduplicate by param
        seen = set()
        unique_vulns = []
        for v in vulnerabilities:
            if v["param"] not in seen:
                seen.add(v["param"])
                unique_vulns.append(v)

        lines = [
            f"XSS Scan: {url}",
            f"Payloads tested: {tested_count}",
            f"VULNERABLE — {len(unique_vulns)} parameter(s) reflect input unescaped:",
            "",
        ]
        for v in unique_vulns:
            lines.append(f"  Parameter: {v['param']}")
            lines.append(f"  Payload:   {v['payload']}")
            lines.append(f"  Status:    {v['status']}")
            lines.append("")

        lines.append(f"Total reflected payloads: {len(vulnerabilities)}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "url": url,
                "vulnerable": True,
                "findings": unique_vulns,
                "tested": tested_count,
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
        log.error("xss_scan_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"XSS scan failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 4. CORS Misconfiguration Check
# ---------------------------------------------------------------------------

async def cors_check(url: str) -> ToolResult:
    """Check a URL for CORS misconfiguration vulnerabilities.

    Tests various Origin headers to detect dangerous CORS configurations
    like wildcard origins, null origin acceptance, and origin reflection.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    parsed = urlparse(url)
    target_domain = parsed.hostname or ""

    test_origins = [
        ("https://evil.com", "arbitrary_origin"),
        ("null", "null_origin"),
        (f"https://{target_domain}.evil.com", "subdomain_hijack"),
        (f"https://evil-{target_domain}", "prefix_match"),
        (f"https://{target_domain}", "same_origin"),
    ]

    misconfigurations: list[dict[str, str]] = []

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            for origin, test_type in test_origins:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": _USER_AGENT,
                        "Origin": origin,
                    },
                )

                acao = resp.headers.get("Access-Control-Allow-Origin", "")
                acac = resp.headers.get("Access-Control-Allow-Credentials", "")

                if not acao:
                    continue

                if acao == "*":
                    severity = "HIGH" if acac.lower() == "true" else "MEDIUM"
                    misconfigurations.append({
                        "type": "wildcard_origin",
                        "origin_sent": origin,
                        "acao": acao,
                        "acac": acac,
                        "severity": severity,
                        "description": "Access-Control-Allow-Origin is wildcard (*)",
                    })
                elif acao == "null" and test_type == "null_origin":
                    misconfigurations.append({
                        "type": "null_origin_allowed",
                        "origin_sent": origin,
                        "acao": acao,
                        "acac": acac,
                        "severity": "HIGH",
                        "description": "Null origin is allowed — exploitable via sandboxed iframes",
                    })
                elif acao == origin and test_type in ("arbitrary_origin", "subdomain_hijack", "prefix_match"):
                    severity = "CRITICAL" if acac.lower() == "true" else "HIGH"
                    misconfigurations.append({
                        "type": "origin_reflection",
                        "origin_sent": origin,
                        "acao": acao,
                        "acac": acac,
                        "severity": severity,
                        "description": f"Origin '{origin}' is reflected — attacker can read responses",
                    })

                if acao == "*" and acac.lower() == "true":
                    misconfigurations.append({
                        "type": "wildcard_with_credentials",
                        "origin_sent": origin,
                        "acao": acao,
                        "acac": acac,
                        "severity": "CRITICAL",
                        "description": "Wildcard origin with credentials — maximum risk",
                    })

        elapsed = int((time.monotonic() - start) * 1000)

        if not misconfigurations:
            return ToolResult(
                success=True,
                output=(
                    f"CORS Check: {url}\n"
                    f"Origins tested: {len(test_origins)}\n"
                    f"Result: No CORS misconfigurations detected."
                ),
                execution_time_ms=elapsed,
                data={"url": url, "vulnerable": False},
            )

        # Deduplicate by type
        seen_types = set()
        unique_misconfigs = []
        for m in misconfigurations:
            if m["type"] not in seen_types:
                seen_types.add(m["type"])
                unique_misconfigs.append(m)

        lines = [
            f"CORS Check: {url}",
            f"Origins tested: {len(test_origins)}",
            f"MISCONFIGURED — {len(unique_misconfigs)} issue(s):",
            "",
        ]
        for m in unique_misconfigs:
            lines.append(f"  [{m['severity']}] {m['type']}")
            lines.append(f"    Origin sent: {m['origin_sent']}")
            lines.append(f"    ACAO:        {m['acao']}")
            lines.append(f"    ACAC:        {m['acac']}")
            lines.append(f"    {m['description']}")
            lines.append("")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "url": url,
                "vulnerable": True,
                "findings": unique_misconfigs,
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
        log.error("cors_check_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"CORS check failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 5. WAF Detection
# ---------------------------------------------------------------------------

_WAF_SIGNATURES: dict[str, dict[str, str | list[str]]] = {
    "Cloudflare": {
        "headers": ["cf-ray", "cf-cache-status", "cf-request-id"],
        "cookies": ["__cfduid", "__cf_bm", "cf_clearance"],
        "body_patterns": ["cloudflare", "cf-browser-verification", "attention required"],
    },
    "AWS WAF": {
        "headers": ["x-amzn-requestid", "x-amz-cf-id", "x-amz-apigw-id"],
        "cookies": ["awsalb", "awsalbcors"],
        "body_patterns": ["aws", "request blocked"],
    },
    "Akamai": {
        "headers": ["x-akamai-transformed", "akamai-ghost-ip", "x-akamai-session-info"],
        "cookies": ["akamai_", "ak_bmsc"],
        "body_patterns": ["akamai", "ghost"],
    },
    "ModSecurity": {
        "headers": ["mod_security", "modsecurity"],
        "cookies": [],
        "body_patterns": ["mod_security", "modsecurity", "not acceptable", "noyb"],
    },
    "Sucuri": {
        "headers": ["x-sucuri-id", "x-sucuri-cache"],
        "cookies": ["sucuri_cloudproxy"],
        "body_patterns": ["sucuri", "cloudproxy", "access denied - sucuri"],
    },
    "Imperva/Incapsula": {
        "headers": ["x-iinfo", "x-cdn"],
        "cookies": ["incap_ses_", "visid_incap_", "_incapsula_"],
        "body_patterns": ["incapsula", "imperva", "incident id"],
    },
    "F5 BIG-IP": {
        "headers": ["x-wa-info", "x-cnection"],
        "cookies": ["bigipserver", "ts", "f5_cspm"],
        "body_patterns": ["the requested url was rejected", "big-ip"],
    },
    "Barracuda": {
        "headers": ["barra_counter_session"],
        "cookies": ["barra_counter_session", "bnn"],
        "body_patterns": ["barracuda"],
    },
    "DenyAll": {
        "headers": [],
        "cookies": ["sessioncookie"],
        "body_patterns": ["conditionblocked", "denyall"],
    },
    "Fortinet/FortiWeb": {
        "headers": [],
        "cookies": ["fortiwafsid", "cookiesession1"],
        "body_patterns": ["fortigate", "fortiweb", ".fyi."],
    },
}


def _waf_confidence(evidence_count: int) -> str:
    """Determine WAF detection confidence from evidence count."""
    if evidence_count >= 3:
        return "HIGH"
    if evidence_count >= 2:
        return "MEDIUM"
    return "LOW"


_WAF_TRIGGER_PAYLOADS = [
    "/<script>alert(1)</script>",
    "/?q=' OR 1=1--",
    "/etc/passwd",
    "/?q=../../../etc/passwd",
    "/?q=<img src=x onerror=alert(1)>",
]


async def waf_detect(url: str) -> ToolResult:
    """Fingerprint the Web Application Firewall (WAF) protecting a target.

    Sends normal and attack-like requests, then analyzes response headers,
    cookies, and body content for known WAF signatures.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    detected_wafs: dict[str, dict[str, list[str]]] = {}

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True,
        ) as client:
            # Normal request first
            normal_resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
            normal_status = normal_resp.status_code

            # Collect all responses (normal + trigger payloads)
            responses = [normal_resp]

            base_url = url.rstrip("/")
            for payload in _WAF_TRIGGER_PAYLOADS:
                try:
                    resp = await client.get(
                        base_url + payload,
                        headers={"User-Agent": _USER_AGENT},
                    )
                    responses.append(resp)
                except (httpx.TimeoutException, httpx.ConnectError):
                    pass

            # Analyze all responses
            for resp in responses:
                headers_lower = {k.lower(): v.lower() for k, v in resp.headers.items()}
                cookies_str = "; ".join(
                    f"{k}={v}" for k, v in resp.cookies.items()
                ).lower()
                body_lower = resp.text[:50000].lower()

                for waf_name, sigs in _WAF_SIGNATURES.items():
                    evidence = []

                    for hdr in sigs.get("headers", []):
                        if hdr.lower() in headers_lower:
                            evidence.append(f"header:{hdr}={headers_lower[hdr.lower()]}")

                    for cookie in sigs.get("cookies", []):
                        if cookie.lower() in cookies_str:
                            evidence.append(f"cookie:{cookie}")

                    for pattern in sigs.get("body_patterns", []):
                        if pattern.lower() in body_lower:
                            evidence.append(f"body:{pattern}")

                    if evidence:
                        if waf_name not in detected_wafs:
                            detected_wafs[waf_name] = {"evidence": []}
                        for e in evidence:
                            if e not in detected_wafs[waf_name]["evidence"]:
                                detected_wafs[waf_name]["evidence"].append(e)

            # Check for generic WAF behavior
            block_count = sum(1 for r in responses if r.status_code in (403, 406, 429, 503))
            rate_limited = any(r.status_code == 429 for r in responses)

        elapsed = int((time.monotonic() - start) * 1000)

        if not detected_wafs and block_count == 0:
            return ToolResult(
                success=True,
                output=(
                    f"WAF Detection: {url}\n"
                    f"Normal status: {normal_status}\n"
                    f"Trigger payloads tested: {len(_WAF_TRIGGER_PAYLOADS)}\n"
                    f"Result: No WAF detected.\n"
                    f"Note: The target may use a WAF that is not in our signature database."
                ),
                execution_time_ms=elapsed,
                data={"url": url, "waf_detected": False},
            )

        lines = [
            f"WAF Detection: {url}",
            f"Normal status: {normal_status}",
            f"Trigger payloads tested: {len(_WAF_TRIGGER_PAYLOADS)}",
            f"Blocked responses: {block_count}/{len(responses)}",
            "",
        ]

        if detected_wafs:
            lines.append(f"Detected WAF(s):")
            for waf_name, info in detected_wafs.items():
                evidence_list = info["evidence"]
                if len(evidence_list) >= 3:
                    confidence = "HIGH"
                elif len(evidence_list) >= 2:
                    confidence = "MEDIUM"
                else:
                    confidence = "LOW"
                lines.append(f"  [{confidence}] {waf_name}")
                for ev in evidence_list[:5]:
                    lines.append(f"    - {ev}")
                lines.append("")
        elif block_count > 0:
            lines.append("Unknown WAF — attack payloads were blocked but no known signatures matched.")

        if rate_limited:
            lines.append("Rate limiting detected (HTTP 429).")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "url": url,
                "waf_detected": True,
                "wafs": {
                    name: {
                        "evidence": info["evidence"],
                        "confidence": _waf_confidence(len(info["evidence"])),
                    }
                    for name, info in detected_wafs.items()
                },
                "blocked_count": block_count,
                "rate_limited": rate_limited,
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
        log.error("waf_detect_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"WAF detection failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 6. LFI Test
# ---------------------------------------------------------------------------

_LFI_PAYLOADS = [
    "../../../etc/passwd",
    "....//....//....//etc/passwd",
    "..%2f..%2f..%2fetc/passwd",
    "/etc/passwd",
    "..\\..\\..\\etc\\passwd",
    "....\\\\....\\\\etc\\\\passwd",
    "php://filter/convert.base64-encode/resource=index",
    "php://filter/convert.base64-encode/resource=../config",
    "/proc/self/environ",
    "..%252f..%252f..%252fetc/passwd",
]

_LFI_SUCCESS_PATTERNS = [
    (r"root:x:0:0:", "etc/passwd content"),
    (r"root:.*:0:0:", "etc/passwd content (variant)"),
    (r"daemon:.*:1:1:", "etc/passwd content (daemon line)"),
    (r"DOCUMENT_ROOT=", "/proc/self/environ content"),
    (r"SERVER_SOFTWARE=", "/proc/self/environ content"),
]


async def lfi_test(url: str, param: str) -> ToolResult:
    """Test a URL parameter for Local File Inclusion (LFI) vulnerabilities.

    Injects path traversal payloads and checks for file content patterns
    like /etc/passwd in the response.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    if not param or not param.strip():
        return ToolResult(success=False, output="", error="Parameter name must not be empty")

    param = param.strip()
    parsed = urlparse(url)
    existing_params = parse_qs(parsed.query, keep_blank_values=True)

    vulnerabilities: list[dict[str, str]] = []
    tested_count = 0

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            for payload in _LFI_PAYLOADS:
                tested_count += 1
                test_params = dict(existing_params)
                test_params[param] = [payload]
                query_string = urlencode(test_params, doseq=True)
                test_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query_string}"

                resp = await client.get(
                    test_url,
                    headers={"User-Agent": _USER_AGENT},
                )
                body = resp.text

                for pattern, description in _LFI_SUCCESS_PATTERNS:
                    if re.search(pattern, body):
                        vulnerabilities.append({
                            "param": param,
                            "payload": payload,
                            "evidence": description,
                            "pattern": pattern,
                            "status": resp.status_code,
                        })
                        break

        elapsed = int((time.monotonic() - start) * 1000)

        if not vulnerabilities:
            return ToolResult(
                success=True,
                output=(
                    f"LFI Test: {url}\n"
                    f"Parameter: {param}\n"
                    f"Payloads tested: {tested_count}\n"
                    f"Result: No LFI indicators detected.\n"
                    f"Note: The parameter may still be vulnerable to blind LFI or "
                    f"wrappers not tested here."
                ),
                execution_time_ms=elapsed,
                data={"url": url, "param": param, "vulnerable": False, "tested": tested_count},
            )

        lines = [
            f"LFI Test: {url}",
            f"Parameter: {param}",
            f"Payloads tested: {tested_count}",
            f"VULNERABLE — {len(vulnerabilities)} payload(s) succeeded:",
            "",
        ]
        for v in vulnerabilities:
            lines.append(f"  Payload:  {v['payload']}")
            lines.append(f"  Evidence: {v['evidence']}")
            lines.append(f"  Status:   {v['status']}")
            lines.append("")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "url": url,
                "param": param,
                "vulnerable": True,
                "findings": vulnerabilities,
                "tested": tested_count,
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
        log.error("lfi_test_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"LFI test failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 7. Security Headers Audit
# ---------------------------------------------------------------------------

_AUDIT_HEADERS: list[dict[str, str]] = [
    {
        "name": "Strict-Transport-Security",
        "severity": "CRITICAL",
        "description": "HSTS — forces HTTPS connections, prevents downgrade attacks",
        "recommendation": "Add: Strict-Transport-Security: max-age=31536000; includeSubDomains; preload",
    },
    {
        "name": "Content-Security-Policy",
        "severity": "HIGH",
        "description": "CSP — prevents XSS, clickjacking, and code injection",
        "recommendation": "Add: Content-Security-Policy: default-src 'self'; script-src 'self'",
    },
    {
        "name": "X-Frame-Options",
        "severity": "HIGH",
        "description": "Clickjacking protection — prevents embedding in iframes",
        "recommendation": "Add: X-Frame-Options: DENY (or SAMEORIGIN)",
    },
    {
        "name": "X-Content-Type-Options",
        "severity": "MEDIUM",
        "description": "Prevents MIME-type sniffing attacks",
        "recommendation": "Add: X-Content-Type-Options: nosniff",
    },
    {
        "name": "X-XSS-Protection",
        "severity": "LOW",
        "description": "Legacy XSS filter (deprecated in modern browsers, but still checked)",
        "recommendation": "Add: X-XSS-Protection: 0 (disable legacy filter, rely on CSP instead)",
    },
    {
        "name": "Referrer-Policy",
        "severity": "MEDIUM",
        "description": "Controls referrer information leakage to other origins",
        "recommendation": "Add: Referrer-Policy: strict-origin-when-cross-origin",
    },
    {
        "name": "Permissions-Policy",
        "severity": "MEDIUM",
        "description": "Controls browser feature access (camera, microphone, geolocation, etc.)",
        "recommendation": "Add: Permissions-Policy: camera=(), microphone=(), geolocation=()",
    },
    {
        "name": "Cache-Control",
        "severity": "MEDIUM",
        "description": "Controls caching behavior — sensitive pages should not be cached",
        "recommendation": "Add: Cache-Control: no-store, no-cache, must-revalidate",
    },
]

_SEVERITY_WEIGHTS = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


async def header_audit(url: str) -> ToolResult:
    """Comprehensive security headers audit for a URL.

    Checks for all important security headers with severity ratings,
    analyzes Set-Cookie flags, and assigns an overall grade (A-F).
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers={"User-Agent": _USER_AGENT})

        elapsed = int((time.monotonic() - start) * 1000)
        headers = resp.headers

        present_headers: list[dict[str, str]] = []
        missing_headers: list[dict[str, str]] = []
        total_weight = 0
        earned_weight = 0

        for hdr_info in _AUDIT_HEADERS:
            weight = _SEVERITY_WEIGHTS[hdr_info["severity"]]
            total_weight += weight
            value = headers.get(hdr_info["name"])

            if value:
                earned_weight += weight
                present_headers.append({
                    "name": hdr_info["name"],
                    "value": value,
                    "severity": hdr_info["severity"],
                    "description": hdr_info["description"],
                })
            else:
                missing_headers.append({
                    "name": hdr_info["name"],
                    "severity": hdr_info["severity"],
                    "description": hdr_info["description"],
                    "recommendation": hdr_info["recommendation"],
                })

        # Check Set-Cookie flags
        cookie_issues: list[str] = []
        set_cookie_headers: list[str] = []
        if hasattr(headers, "get_list"):
            set_cookie_headers = headers.get_list("set-cookie")
        if not set_cookie_headers and hasattr(headers, "raw"):
            try:
                raw_cookies = [v for k, v in headers.raw if k.lower() == b"set-cookie"]
                set_cookie_headers = [c.decode(errors="replace") for c in raw_cookies]
            except (TypeError, AttributeError):
                pass

        for cookie in set_cookie_headers:
            cookie_lower = cookie.lower()
            cookie_name = cookie.split("=")[0].strip() if "=" in cookie else "unknown"
            if "secure" not in cookie_lower:
                cookie_issues.append(f"Cookie '{cookie_name}' missing Secure flag")
            if "httponly" not in cookie_lower:
                cookie_issues.append(f"Cookie '{cookie_name}' missing HttpOnly flag")
            if "samesite" not in cookie_lower:
                cookie_issues.append(f"Cookie '{cookie_name}' missing SameSite flag")

        if cookie_issues:
            total_weight += 3  # HIGH severity for cookie issues
        else:
            earned_weight += 3
            total_weight += 3

        # Calculate grade
        score = int((earned_weight / total_weight) * 100) if total_weight > 0 else 0
        if score >= 90:
            grade = "A"
        elif score >= 75:
            grade = "B"
        elif score >= 55:
            grade = "C"
        elif score >= 35:
            grade = "D"
        else:
            grade = "F"

        # Server info
        server = headers.get("Server", "Not disclosed")
        powered_by = headers.get("X-Powered-By", "Not disclosed")

        lines = [
            f"Security Headers Audit: {url}",
            f"Status: {resp.status_code} | Server: {server} | X-Powered-By: {powered_by}",
            f"Score: {score}% — Grade: {grade}",
            "",
        ]

        if present_headers:
            lines.append(f"Present headers ({len(present_headers)}):")
            for h in present_headers:
                lines.append(f"  [OK] {h['name']}: {h['value'][:80]}")
            lines.append("")

        if missing_headers:
            lines.append(f"Missing headers ({len(missing_headers)}):")
            for h in missing_headers:
                lines.append(f"  [{h['severity']}] {h['name']}")
                lines.append(f"    {h['description']}")
                lines.append(f"    Fix: {h['recommendation']}")
            lines.append("")

        if cookie_issues:
            lines.append(f"Cookie security issues ({len(cookie_issues)}):")
            for issue in cookie_issues:
                lines.append(f"  [HIGH] {issue}")
            lines.append("")

        if not missing_headers and not cookie_issues:
            lines.append("All security headers present and cookies properly configured.")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "url": url,
                "grade": grade,
                "score": score,
                "present_count": len(present_headers),
                "missing_count": len(missing_headers),
                "cookie_issues": len(cookie_issues),
                "status_code": resp.status_code,
                "server": server,
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
        log.error("header_audit_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Security header audit failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

dir_bruteforce_tool = ToolDefinition(
    name="dir_bruteforce",
    description=(
        "Quét thư mục/đường dẫn ẩn trên web server. Gửi HEAD request đồng thời "
        "với wordlist tích hợp để tìm admin panel, backup file, config file. "
        "Chỉ dùng cho pentest được ủy quyền và CTF."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL mục tiêu (VD: 'https://example.com')",
        ),
        ToolParameter(
            name="wordlist", type="string",
            description="Wordlist để sử dụng",
            required=False, default="common",
            enum=["common", "medium", "small", "api", "backup", "bounty"],
        ),
    ],
    handler=dir_bruteforce,
    timeout_seconds=60,
)

sqli_test_tool = ToolDefinition(
    name="sqli_test",
    description=(
        "Kiểm tra lỗ hổng SQL Injection trên URL. Gửi các payload SQLi phổ biến "
        "và phát hiện thông báo lỗi SQL trong response. Hỗ trợ GET và POST. "
        "Chỉ dùng cho pentest được ủy quyền và CTF."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL mục tiêu có parameter (VD: 'https://example.com/search?q=test')",
        ),
        ToolParameter(
            name="method", type="string",
            description="HTTP method",
            required=False, default="GET",
            enum=["GET", "POST"],
        ),
        ToolParameter(
            name="params", type="string",
            description="Tên parameter cần test, cách nhau bằng dấu phẩy (VD: 'id,name')",
            required=False, default="",
        ),
    ],
    handler=sqli_test,
    timeout_seconds=45,
)

xss_scan_tool = ToolDefinition(
    name="xss_scan",
    description=(
        "Quét lỗ hổng Reflected XSS trên URL. Inject payload XSS vào các parameter "
        "và kiểm tra xem có bị reflect trong response không. "
        "Chỉ dùng cho pentest được ủy quyền và CTF."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL mục tiêu có parameter (VD: 'https://example.com/search?q=test')",
        ),
        ToolParameter(
            name="params", type="string",
            description="Tên parameter cần test, cách nhau bằng dấu phẩy",
            required=False, default="",
        ),
    ],
    handler=xss_scan,
    timeout_seconds=45,
)

cors_check_tool = ToolDefinition(
    name="cors_check",
    description=(
        "Kiểm tra cấu hình CORS sai trên URL. Phát hiện wildcard origin, null origin, "
        "origin reflection, và credentials với wildcard. "
        "Chỉ dùng cho pentest được ủy quyền và CTF."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL mục tiêu (VD: 'https://api.example.com')",
        ),
    ],
    handler=cors_check,
    timeout_seconds=30,
)

waf_detect_tool = ToolDefinition(
    name="waf_detect",
    description=(
        "Nhận diện WAF (Web Application Firewall) bảo vệ website. "
        "Phân tích header, cookie, và nội dung response để xác định loại WAF: "
        "Cloudflare, AWS WAF, Akamai, ModSecurity, Sucuri, Imperva, F5, v.v. "
        "Chỉ dùng cho pentest được ủy quyền và CTF."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL mục tiêu (VD: 'https://example.com')",
        ),
    ],
    handler=waf_detect,
    timeout_seconds=45,
)

lfi_test_tool = ToolDefinition(
    name="lfi_test",
    description=(
        "Kiểm tra lỗ hổng Local File Inclusion (LFI) trên parameter. "
        "Inject path traversal payload và kiểm tra nội dung file hệ thống "
        "trong response (VD: /etc/passwd). "
        "Chỉ dùng cho pentest được ủy quyền và CTF."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL mục tiêu (VD: 'https://example.com/page?file=home')",
        ),
        ToolParameter(
            name="param", type="string",
            description="Tên parameter cần test (VD: 'file', 'page', 'path')",
        ),
    ],
    handler=lfi_test,
    timeout_seconds=30,
)

header_audit_tool = ToolDefinition(
    name="header_audit",
    description=(
        "Kiểm tra toàn diện security headers của website. Đánh giá HSTS, CSP, "
        "X-Frame-Options, cookie flags, v.v. với mức độ nghiêm trọng và "
        "cho điểm tổng thể (A-F) kèm khuyến nghị sửa. "
        "Chỉ dùng cho pentest được ủy quyền và CTF."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL mục tiêu (VD: 'https://example.com')",
        ),
    ],
    handler=header_audit,
    timeout_seconds=30,
)
