"""OSINT Tools — Open Source Intelligence gathering for authorized investigations.

Provides Google dork generation, username enumeration, email harvesting,
Wayback Machine lookup, and GitHub leak detection. All tools use httpx
for async HTTP. For authorized security testing and CTF challenges only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import time
from urllib.parse import urlparse

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.osint")

_TIMEOUT = 15  # seconds
_DANGEROUS_CHARS = [";", "&", "|", "`", "$", "(", ")"]

_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "metadata.google.internal", "169.254.169.254",
}


def _validate_input(value: str, label: str) -> str | None:
    """Validate input against injection characters. Returns error message or None."""
    if not value or not value.strip():
        return f"{label} không được để trống"
    if any(c in value for c in _DANGEROUS_CHARS):
        return f"{label} không hợp lệ: chứa ký tự không được phép"
    return None


def _validate_domain(domain: str) -> str | None:
    """Validate domain format. Returns error message or None."""
    err = _validate_input(domain, "Domain")
    if err:
        return err
    if not re.match(
        r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?'
        r'(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$',
        domain,
    ):
        return f"Domain không hợp lệ: {domain}"
    return None


def _validate_url(url: str) -> str | None:
    """Validate URL format and block SSRF targets. Returns error message or None."""
    if not url or not url.strip():
        return "URL không được để trống"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"URL phải bắt đầu bằng http:// hoặc https://"
    if not parsed.netloc:
        return "URL không hợp lệ — thiếu hostname"

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


def _validate_username(username: str) -> str | None:
    """Validate username format. Returns error message or None."""
    err = _validate_input(username, "Username")
    if err:
        return err
    if not re.match(r'^[a-zA-Z0-9._\-]+$', username):
        return f"Username không hợp lệ: chỉ cho phép a-z, 0-9, '.', '_', '-'"
    if len(username) > 64:
        return "Username quá dài (tối đa 64 ký tự)"
    return None


# ---------------------------------------------------------------------------
# 1. Google Dork Query Generator
# ---------------------------------------------------------------------------

_DORK_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "files": [
        ('site:{target} filetype:pdf', "PDF documents"),
        ('site:{target} filetype:doc OR filetype:docx', "Word documents"),
        ('site:{target} filetype:xls OR filetype:xlsx', "Excel spreadsheets"),
        ('site:{target} filetype:sql', "SQL database dumps"),
        ('site:{target} filetype:log', "Log files"),
        ('site:{target} filetype:bak OR filetype:old', "Backup files"),
        ('site:{target} filetype:csv', "CSV data files"),
    ],
    "login": [
        ('site:{target} inurl:login', "Login pages"),
        ('site:{target} inurl:admin', "Admin panels"),
        ('site:{target} inurl:signin OR inurl:sign-in', "Sign-in pages"),
        ('site:{target} intitle:"login" OR intitle:"sign in"', "Login portals"),
        ('site:{target} inurl:auth OR inurl:oauth', "Auth endpoints"),
        ('site:{target} inurl:wp-admin OR inurl:wp-login', "WordPress admin"),
    ],
    "config": [
        ('site:{target} ext:env OR ext:yml "password"', "Config files with passwords"),
        ('site:{target} ext:xml OR ext:conf', "XML/config files"),
        ('site:{target} ext:ini OR ext:cfg', "INI/CFG config files"),
        ('site:{target} filetype:json "api_key" OR "secret"', "JSON with secrets"),
        ('site:{target} inurl:.git', "Git repositories"),
        ('site:{target} intitle:"index of" ".env"', "Directory listings with .env"),
    ],
    "database": [
        ('site:{target} filetype:sql "INSERT INTO"', "SQL dumps with data"),
        ('site:{target} filetype:sql "CREATE TABLE"', "SQL schema files"),
        ('site:{target} inurl:phpmyadmin', "phpMyAdmin instances"),
        ('site:{target} intitle:"index of" "database"', "Database directory listings"),
        ('site:{target} ext:db OR ext:sqlite OR ext:mdb', "Database files"),
    ],
    "sensitive": [
        ('site:{target} "password" OR "passwd" filetype:txt', "Password files"),
        ('site:{target} "BEGIN RSA PRIVATE KEY"', "Exposed private keys"),
        ('site:{target} "api_key" OR "apikey" OR "api-key"', "API keys"),
        ('site:{target} "AWS_ACCESS_KEY" OR "aws_secret"', "AWS credentials"),
        ('site:{target} inurl:credentials OR inurl:secrets', "Credential pages"),
        ('site:{target} "Authorization: Bearer" OR "token"', "Auth tokens"),
    ],
    "dirs": [
        ('site:{target} intitle:"index of /"', "Open directory listings"),
        ('site:{target} intitle:"index of" "parent directory"', "Parent directory listings"),
        ('site:{target} inurl:/backup/ OR inurl:/backups/', "Backup directories"),
        ('site:{target} inurl:/temp/ OR inurl:/tmp/', "Temporary directories"),
        ('site:{target} inurl:/.well-known/', "Well-known directories"),
    ],
}


async def google_dork(target: str, dork_type: str = "all") -> ToolResult:
    """Generate Google dork queries for a target domain.

    Produces search queries that can be used in Google to find exposed
    files, login pages, config files, database dumps, and sensitive data.
    """
    start = time.monotonic()

    err = _validate_input(target, "Target")
    if err:
        return ToolResult(success=False, output="", error=err)

    dork_type = dork_type.lower()
    valid_types = {"files", "login", "config", "database", "sensitive", "dirs", "all"}
    if dork_type not in valid_types:
        return ToolResult(
            success=False, output="",
            error=f"dork_type không hợp lệ: '{dork_type}'. Chọn: {', '.join(sorted(valid_types))}",
        )

    if dork_type == "all":
        categories = list(_DORK_TEMPLATES.keys())
    else:
        categories = [dork_type]

    lines = [
        f"Google Dork Queries cho: {target}",
        f"Loại: {dork_type}",
        "",
    ]

    total_dorks = 0
    all_dorks: list[dict[str, str]] = []

    for category in categories:
        templates = _DORK_TEMPLATES.get(category, [])
        lines.append(f"--- {category.upper()} ---")
        for template, description in templates:
            query = template.format(target=target)
            lines.append(f"  {query}")
            lines.append(f"    ({description})")
            all_dorks.append({"query": query, "category": category, "description": description})
            total_dorks += 1
        lines.append("")

    lines.append(f"Tổng: {total_dorks} dork queries")
    lines.append("Lưu ý: Sử dụng có trách nhiệm, chỉ cho mục đích authorized testing.")

    elapsed = int((time.monotonic() - start) * 1000)
    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={"target": target, "dork_type": dork_type, "dorks": all_dorks, "count": total_dorks},
    )


# ---------------------------------------------------------------------------
# 2. Username Search Across Platforms
# ---------------------------------------------------------------------------

_PLATFORMS: dict[str, str] = {
    "github": "https://github.com/{username}",
    "twitter": "https://x.com/{username}",
    "instagram": "https://www.instagram.com/{username}/",
    "reddit": "https://www.reddit.com/user/{username}",
    "telegram": "https://t.me/{username}",
    "linkedin": "https://www.linkedin.com/in/{username}",
    "tiktok": "https://www.tiktok.com/@{username}",
    "youtube": "https://www.youtube.com/@{username}",
    "medium": "https://medium.com/@{username}",
    "keybase": "https://keybase.io/{username}",
}

_POPULAR_PLATFORMS = {"github", "twitter", "instagram", "reddit", "tiktok", "youtube"}


async def _check_platform(
    client: httpx.AsyncClient,
    platform: str,
    url: str,
) -> dict[str, str]:
    """Check if a username exists on a single platform."""
    try:
        resp = await client.get(
            url,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JARVIS/2.0)"},
        )
        if resp.status_code == 200:
            status = "found"
        elif resp.status_code == 404:
            status = "not_found"
        else:
            status = f"unknown ({resp.status_code})"
    except httpx.TimeoutException:
        status = "timeout"
    except Exception as e:
        status = f"error ({e})"

    return {"platform": platform, "url": url, "status": status}


async def username_search(username: str, platforms: str = "popular") -> ToolResult:
    """Check username availability across social media platforms.

    Queries profile URLs on multiple platforms concurrently and reports
    which ones have an account with the given username.
    """
    start = time.monotonic()

    err = _validate_username(username)
    if err:
        return ToolResult(success=False, output="", error=err)

    platforms_lower = platforms.lower()
    if platforms_lower == "popular":
        selected = _POPULAR_PLATFORMS
    elif platforms_lower == "all":
        selected = set(_PLATFORMS.keys())
    else:
        requested = {p.strip().lower() for p in platforms_lower.split(",")}
        unknown = requested - set(_PLATFORMS.keys())
        if unknown:
            return ToolResult(
                success=False, output="",
                error=f"Platform không hợp lệ: {', '.join(unknown)}. "
                      f"Có sẵn: {', '.join(sorted(_PLATFORMS.keys()))}",
            )
        selected = requested

    async with httpx.AsyncClient(timeout=5) as client:
        tasks = []
        for platform in sorted(selected):
            url_template = _PLATFORMS[platform]
            url = url_template.format(username=username)
            tasks.append(_check_platform(client, platform, url))

        results = await asyncio.gather(*tasks)

    elapsed = int((time.monotonic() - start) * 1000)

    found = [r for r in results if r["status"] == "found"]
    not_found = [r for r in results if r["status"] == "not_found"]
    other = [r for r in results if r["status"] not in ("found", "not_found")]

    lines = [
        f"Username Search: '{username}'",
        f"Checked: {len(results)} platforms | Found: {len(found)} | Not found: {len(not_found)}",
        "",
    ]

    if found:
        lines.append("Found on:")
        for r in found:
            lines.append(f"  [+] {r['platform']}: {r['url']}")
    if not_found:
        lines.append("Not found on:")
        for r in not_found:
            lines.append(f"  [-] {r['platform']}")
    if other:
        lines.append("Inconclusive:")
        for r in other:
            lines.append(f"  [?] {r['platform']}: {r['status']}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={
            "username": username,
            "results": results,
            "found_count": len(found),
            "checked_count": len(results),
        },
    )


# ---------------------------------------------------------------------------
# 3. Email Harvesting from Web Pages
# ---------------------------------------------------------------------------

_EMAIL_REGEX = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
)

_EXCLUDED_EMAIL_PATTERNS = {
    "example.com", "example.org", "test.com",
    "sentry.io", "wixpress.com", "schema.org",
}


def _sync_ddg_domain_search(domain: str, max_pages: int) -> list[dict]:
    """Search DDG for pages on a domain (synchronous, run via to_thread)."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    with DDGS() as ddgs:
        query = f"site:{domain} email OR contact OR @{domain}"
        return list(ddgs.text(query, max_results=max_pages, region="wt-wt"))


async def email_harvest(domain: str, max_pages: int = 5) -> ToolResult:
    """Extract email addresses from web pages on a domain.

    Uses DuckDuckGo to find relevant pages on the target domain, then
    fetches each page and extracts email addresses using regex.
    """
    start = time.monotonic()

    err = _validate_domain(domain)
    if err:
        return ToolResult(success=False, output="", error=err)

    max_pages = max(1, min(max_pages, 15))

    try:
        search_results = await asyncio.to_thread(_sync_ddg_domain_search, domain, max_pages)
    except ImportError:
        return ToolResult(
            success=False, output="",
            error="duckduckgo-search chưa được cài đặt. Chạy: pip install duckduckgo-search",
        )
    except Exception as e:
        log.error("email_harvest_search_error", domain=domain, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Lỗi tìm kiếm DDG: {e}",
        )

    if not search_results:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output=f"Không tìm thấy trang nào cho domain: {domain}",
            execution_time_ms=elapsed,
            data={"domain": domain, "emails": [], "sources": []},
        )

    emails_by_source: dict[str, set[str]] = {}
    all_emails: set[str] = set()

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for result in search_results:
            page_url = result.get("href", "")
            if not page_url:
                continue

            url_error = _validate_url(page_url)
            if url_error:
                continue

            try:
                resp = await client.get(
                    page_url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; JARVIS/2.0)"},
                )
                if resp.status_code != 200:
                    continue

                found = _EMAIL_REGEX.findall(resp.text)
                page_emails = set()
                for email in found:
                    email_lower = email.lower()
                    email_domain = email_lower.split("@")[1]
                    if email_domain in _EXCLUDED_EMAIL_PATTERNS:
                        continue
                    if email_lower.endswith((".png", ".jpg", ".gif", ".css", ".js")):
                        continue
                    page_emails.add(email_lower)

                if page_emails:
                    emails_by_source[page_url] = page_emails
                    all_emails.update(page_emails)

            except Exception as e:
                log.debug("email_harvest_fetch_error", url=page_url, error=str(e))
                continue

    elapsed = int((time.monotonic() - start) * 1000)
    sorted_emails = sorted(all_emails)

    lines = [
        f"Email Harvest cho: {domain}",
        f"Đã quét: {len(search_results)} trang | Tìm thấy: {len(sorted_emails)} email",
        "",
    ]

    if sorted_emails:
        lines.append("Emails tìm thấy:")
        for email in sorted_emails:
            lines.append(f"  {email}")
        lines.append("")
        lines.append("Nguồn:")
        for source_url, source_emails in emails_by_source.items():
            lines.append(f"  {source_url}")
            for e in sorted(source_emails):
                lines.append(f"    -> {e}")
    else:
        lines.append("Không tìm thấy email nào.")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={
            "domain": domain,
            "emails": sorted_emails,
            "sources": {url: sorted(emails) for url, emails in emails_by_source.items()},
            "count": len(sorted_emails),
        },
    )


# ---------------------------------------------------------------------------
# 4. Wayback Machine Snapshot Lookup
# ---------------------------------------------------------------------------

_WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"


async def wayback_lookup(url: str, limit: int = 10) -> ToolResult:
    """Look up historical snapshots of a URL in the Wayback Machine.

    Queries the Wayback Machine CDX API to find archived snapshots.
    Returns timestamps and archive URLs for browsing old versions.
    """
    start = time.monotonic()

    err = _validate_input(url, "URL")
    if err:
        return ToolResult(success=False, output="", error=err)

    limit = max(1, min(limit, 50))

    params = {
        "url": url,
        "output": "json",
        "limit": limit,
        "fl": "timestamp,original,statuscode,mimetype,length",
        "sort": "timestamp:desc",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                _WAYBACK_CDX_URL,
                params=params,
                headers={"User-Agent": "JARVIS/2.0"},
            )
            resp.raise_for_status()

        data = resp.json()
        elapsed = int((time.monotonic() - start) * 1000)

        if not data or len(data) < 2:
            return ToolResult(
                success=True,
                output=f"Không tìm thấy snapshot nào cho: {url}",
                execution_time_ms=elapsed,
                data={"url": url, "snapshots": [], "count": 0},
            )

        headers = data[0]
        rows = data[1:]

        snapshots = []
        lines = [
            f"Wayback Machine Snapshots cho: {url}",
            f"Tìm thấy: {len(rows)} snapshots (mới nhất trước)",
            "",
        ]

        for row in rows:
            entry = dict(zip(headers, row))
            timestamp = entry.get("timestamp", "")
            original = entry.get("original", url)
            status_code = entry.get("statuscode", "")
            mimetype = entry.get("mimetype", "")

            # Format timestamp: 20240101120000 -> 2024-01-01 12:00:00
            formatted_time = timestamp
            if len(timestamp) >= 14:
                formatted_time = (
                    f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]} "
                    f"{timestamp[8:10]}:{timestamp[10:12]}:{timestamp[12:14]}"
                )

            archive_url = f"https://web.archive.org/web/{timestamp}/{original}"

            lines.append(f"  [{formatted_time}] Status: {status_code} | {mimetype}")
            lines.append(f"    {archive_url}")

            snapshots.append({
                "timestamp": timestamp,
                "formatted_time": formatted_time,
                "original_url": original,
                "archive_url": archive_url,
                "status_code": status_code,
                "mimetype": mimetype,
            })

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={"url": url, "snapshots": snapshots, "count": len(snapshots)},
        )

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"Wayback Machine request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("wayback_lookup_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"Wayback Machine lookup failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# 5. GitHub Leak Search
# ---------------------------------------------------------------------------

_GITHUB_SEARCH_URL = "https://api.github.com/search"

_LEAK_PATTERNS: dict[str, list[str]] = {
    "code": [
        '"{query}" filename:.env',
        '"{query}" filename:config',
        '"{query}" "password" OR "secret" OR "api_key"',
        '"{query}" "AWS_ACCESS_KEY" OR "aws_secret_access_key"',
        '"{query}" "BEGIN RSA PRIVATE KEY"',
    ],
    "commits": [
        '"{query}" password',
        '"{query}" secret',
        '"{query}" api_key OR apikey',
        '"{query}" token',
    ],
    "repos": [
        '"{query}" in:name,description',
        '"{query}" credentials OR secrets',
    ],
}


async def github_leaks(query: str, search_type: str = "code") -> ToolResult:
    """Search GitHub for potentially exposed secrets and credentials.

    Uses the GitHub Search API to find leaked credentials, API keys,
    and sensitive data in code, commits, or repositories.
    """
    start = time.monotonic()

    err = _validate_input(query, "Query")
    if err:
        return ToolResult(success=False, output="", error=err)

    search_type = search_type.lower()
    valid_types = {"code", "commits", "repos"}
    if search_type not in valid_types:
        return ToolResult(
            success=False, output="",
            error=f"search_type không hợp lệ: '{search_type}'. Chọn: {', '.join(sorted(valid_types))}",
        )

    # Map search_type to GitHub API endpoint path
    endpoint_map = {
        "code": "code",
        "commits": "commits",
        "repos": "repositories",
    }
    endpoint = endpoint_map[search_type]

    github_token = os.environ.get("GITHUB_TOKEN", "")
    headers: dict[str, str] = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "JARVIS/2.0",
    }
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    # Build search query
    search_query = f"{query} password OR secret OR api_key OR token"
    api_url = f"{_GITHUB_SEARCH_URL}/{endpoint}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                api_url,
                params={"q": search_query, "per_page": 10},
                headers=headers,
            )

            if resp.status_code == 403:
                elapsed = int((time.monotonic() - start) * 1000)
                return ToolResult(
                    success=False, output="",
                    error="GitHub API rate limit exceeded. Set GITHUB_TOKEN env var for higher limits.",
                    execution_time_ms=elapsed,
                )

            resp.raise_for_status()

        data = resp.json()
        elapsed = int((time.monotonic() - start) * 1000)

        total_count = data.get("total_count", 0)
        items = data.get("items", [])

        lines = [
            f"GitHub Leak Search: '{query}'",
            f"Type: {search_type} | Total results: {total_count} (showing {len(items)})",
            "",
        ]

        result_items: list[dict] = []

        if search_type == "code":
            for item in items:
                repo_name = item.get("repository", {}).get("full_name", "N/A")
                file_path = item.get("path", "N/A")
                html_url = item.get("html_url", "")
                # Extract text_matches if available
                snippet = ""
                for match in item.get("text_matches", []):
                    snippet = match.get("fragment", "")[:200]
                    break

                lines.append(f"  [{repo_name}] {file_path}")
                lines.append(f"    {html_url}")
                if snippet:
                    lines.append(f"    Snippet: {snippet}")
                lines.append("")

                result_items.append({
                    "repo": repo_name,
                    "path": file_path,
                    "url": html_url,
                    "snippet": snippet,
                })

        elif search_type == "commits":
            for item in items:
                repo_name = item.get("repository", {}).get("full_name", "N/A")
                message = item.get("commit", {}).get("message", "")[:100]
                html_url = item.get("html_url", "")
                author = item.get("commit", {}).get("author", {}).get("name", "N/A")

                lines.append(f"  [{repo_name}] by {author}")
                lines.append(f"    {message}")
                lines.append(f"    {html_url}")
                lines.append("")

                result_items.append({
                    "repo": repo_name,
                    "message": message,
                    "url": html_url,
                    "author": author,
                })

        else:  # repos
            for item in items:
                full_name = item.get("full_name", "N/A")
                description = (item.get("description") or "")[:150]
                html_url = item.get("html_url", "")
                stars = item.get("stargazers_count", 0)

                lines.append(f"  [{full_name}] ({stars} stars)")
                lines.append(f"    {description}")
                lines.append(f"    {html_url}")
                lines.append("")

                result_items.append({
                    "repo": full_name,
                    "description": description,
                    "url": html_url,
                    "stars": stars,
                })

        if not items:
            lines.append("  Không tìm thấy kết quả.")

        # Append suggested dork patterns
        patterns = _LEAK_PATTERNS.get(search_type, [])
        if patterns:
            lines.append("")
            lines.append("Gợi ý search patterns:")
            for pattern in patterns:
                lines.append(f"  {pattern.format(query=query)}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            execution_time_ms=elapsed,
            data={
                "query": query,
                "search_type": search_type,
                "total_count": total_count,
                "items": result_items,
                "count": len(result_items),
            },
        )

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"GitHub API request timed out after {_TIMEOUT}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("github_leaks_error", query=query, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"GitHub leak search failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

google_dork_tool = ToolDefinition(
    name="google_dork",
    description=(
        "Tạo Google dork queries cho mục tiêu. Sinh ra các câu truy vấn tìm kiếm "
        "nâng cao để tìm file lộ, trang login, config, database dump, và dữ liệu nhạy cảm. "
        "Chỉ dùng cho authorized testing và CTF."
    ),
    parameters=[
        ToolParameter(
            name="target", type="string",
            description="Domain hoặc tên mục tiêu (ví dụ: 'example.com')",
        ),
        ToolParameter(
            name="dork_type", type="string",
            description="Loại dork: files, login, config, database, sensitive, dirs, all",
            required=False, default="all",
            enum=["files", "login", "config", "database", "sensitive", "dirs", "all"],
        ),
    ],
    handler=google_dork,
    timeout_seconds=10,
)

username_search_tool = ToolDefinition(
    name="username_search",
    description=(
        "Kiểm tra username trên các nền tảng mạng xã hội. Tìm tài khoản trên "
        "GitHub, Twitter/X, Instagram, Reddit, Telegram, LinkedIn, TikTok, YouTube, "
        "Medium, Keybase. Kiểm tra đồng thời tất cả platforms."
    ),
    parameters=[
        ToolParameter(
            name="username", type="string",
            description="Username cần tìm kiếm",
        ),
        ToolParameter(
            name="platforms", type="string",
            description="Chọn platforms: 'popular' (6 nền tảng phổ biến), 'all' (tất cả 10), hoặc danh sách cụ thể phân cách bằng dấu phẩy",
            required=False, default="popular",
        ),
    ],
    handler=username_search,
    timeout_seconds=30,
)

email_harvest_tool = ToolDefinition(
    name="email_harvest",
    description=(
        "Thu thập email từ các trang web trên một domain. Dùng DDG tìm trang liên quan, "
        "sau đó trích xuất email bằng regex. Loại bỏ trùng lặp và trả về nguồn. "
        "Chỉ dùng cho authorized testing."
    ),
    parameters=[
        ToolParameter(
            name="domain", type="string",
            description="Domain cần quét email (ví dụ: 'example.com')",
        ),
        ToolParameter(
            name="max_pages", type="integer",
            description="Số trang tối đa cần quét (1-15, mặc định 5)",
            required=False, default=5,
        ),
    ],
    handler=email_harvest,
    timeout_seconds=60,
)

wayback_lookup_tool = ToolDefinition(
    name="wayback_lookup",
    description=(
        "Tra cứu snapshots lịch sử của một URL trên Wayback Machine. Tìm phiên bản cũ "
        "của website, trang đã xóa, và nội dung đã thay đổi. Trả về timestamps "
        "và links đến bản lưu trữ."
    ),
    parameters=[
        ToolParameter(
            name="url", type="string",
            description="URL cần tra cứu (ví dụ: 'example.com' hoặc 'example.com/page')",
        ),
        ToolParameter(
            name="limit", type="integer",
            description="Số snapshots tối đa (1-50, mặc định 10)",
            required=False, default=10,
        ),
    ],
    handler=wayback_lookup,
    timeout_seconds=20,
)

github_leaks_tool = ToolDefinition(
    name="github_leaks",
    description=(
        "Tìm kiếm secrets và credentials bị lộ trên GitHub. Quét code, commits, "
        "hoặc repos cho API keys, passwords, tokens. Hỗ trợ GITHUB_TOKEN cho rate limit "
        "cao hơn. Chỉ dùng cho authorized testing."
    ),
    parameters=[
        ToolParameter(
            name="query", type="string",
            description="Từ khóa tìm kiếm (ví dụ: tên công ty, domain, project)",
        ),
        ToolParameter(
            name="search_type", type="string",
            description="Loại tìm kiếm: code (mã nguồn), commits (lịch sử commit), repos (repositories)",
            required=False, default="code",
            enum=["code", "commits", "repos"],
        ),
    ],
    handler=github_leaks,
    timeout_seconds=20,
)
