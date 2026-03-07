"""Discovery Tools — ProjectDiscovery binary wrappers for recon and fuzzing.

Wraps 5 ProjectDiscovery Go binaries (subfinder, httpx, katana, gau, ffuf)
as async JARVIS tools. Each binary runs as a subprocess with timeout protection,
input validation, and structured output parsing.

For authorized security testing, bug bounty, and CTF challenges only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.discovery")

# Path to the bin/ directory at the project root
BIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "bin",
)

# Characters that could enable command injection
_DANGEROUS_CHARS = re.compile(r"[;&|`$()\\]")

# Static asset extensions to filter out (gau_urls)
_STATIC_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".css",
    ".woff", ".woff2", ".ttf", ".eot", ".ico",
})

# API endpoint patterns (katana_crawl)
_API_PATTERNS = re.compile(r"/api/|/graphql|/v1/|/v2/|/v3/", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

def _validate_domain(domain: str) -> str | None:
    """Validate domain format. Returns error message or None if valid."""
    if not domain or not domain.strip():
        return "Domain must not be empty"
    domain = domain.strip()
    if _DANGEROUS_CHARS.search(domain):
        return "Invalid domain: contains disallowed characters"
    if not re.match(
        r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?"
        r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$",
        domain,
    ):
        return f"Invalid domain format: {domain}"
    return None


def _validate_url(url: str) -> str | None:
    """Validate URL format. Returns error message or None if valid."""
    if not url or not url.strip():
        return "URL must not be empty"
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "URL must start with http:// or https://"
    if any(c in url for c in [";", "|", "`", "$"]):
        return "Invalid URL: contains disallowed characters"
    return None


def _get_binary(name: str) -> str | None:
    """Return full path to a binary if it exists, else None."""
    path = os.path.join(BIN_DIR, name)
    if os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    return None


def _parse_jsonl(raw: str) -> list[dict]:
    """Parse newline-delimited JSON, skipping invalid lines."""
    results = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            log.debug("jsonl_parse_skip", line=line[:120])
    return results


async def _run_binary(
    args: list[str],
    timeout: int,
    stdin_data: bytes | None = None,
) -> tuple[str, str, int]:
    """Run a binary with timeout. Returns (stdout, stderr, returncode).

    Raises asyncio.TimeoutError on timeout.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=stdin_data),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        # Kill the process on timeout
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        raise

    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    return stdout, stderr, proc.returncode or 0


# ---------------------------------------------------------------------------
# 1. subfinder_enum
# ---------------------------------------------------------------------------

async def subfinder_enum(domain: str) -> ToolResult:
    """Enumerate subdomains using subfinder (passive sources).

    Queries multiple passive sources (crt.sh, VirusTotal, Shodan, etc.)
    and returns unique subdomains with source attribution.
    """
    start = time.monotonic()

    err = _validate_domain(domain)
    if err:
        return ToolResult(success=False, output="", error=err)

    binary = _get_binary("subfinder")
    if not binary:
        return ToolResult(
            success=False, output="",
            error=f"subfinder binary not found at {os.path.join(BIN_DIR, 'subfinder')}",
        )

    try:
        stdout, stderr, rc = await _run_binary(
            [binary, "-d", domain, "-silent", "-all", "-json"],
            timeout=110,
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error="subfinder timed out after 110s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("subfinder_exec_error", domain=domain, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"subfinder execution failed: {e}",
            execution_time_ms=elapsed,
        )

    elapsed = int((time.monotonic() - start) * 1000)

    # Parse JSONL output
    records = _parse_jsonl(stdout)

    subdomains: set[str] = set()
    sources: dict[str, int] = {}

    for rec in records:
        host = rec.get("host", "").strip().lower()
        if host:
            subdomains.add(host)
        source = rec.get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1

    sorted_subs = sorted(subdomains)
    sorted_sources = dict(sorted(sources.items(), key=lambda x: x[1], reverse=True))

    # Build human-readable output
    lines = [
        f"Subdomain enumeration for: {domain}",
        f"Tool: subfinder (passive, all sources)",
        f"Found: {len(sorted_subs)} unique subdomains from {len(sorted_sources)} sources",
        "",
    ]
    if sorted_sources:
        lines.append("Sources:")
        for src, count in sorted_sources.items():
            lines.append(f"  {src}: {count}")
        lines.append("")

    lines.append("Subdomains:")
    for sub in sorted_subs:
        lines.append(f"  {sub}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={
            "subdomains": sorted_subs,
            "count": len(sorted_subs),
            "sources": sorted_sources,
        },
    )


# ---------------------------------------------------------------------------
# 2. httpx_probe
# ---------------------------------------------------------------------------

async def httpx_probe(targets: str) -> ToolResult:
    """Probe hosts for alive HTTP services using httpx.

    Takes newline-separated domains/URLs, writes them to a temp file,
    then runs httpx to detect live hosts with status codes, titles,
    technologies, and content length.
    """
    start = time.monotonic()

    if not targets or not targets.strip():
        return ToolResult(success=False, output="", error="targets must not be empty")

    binary = _get_binary("httpx")
    if not binary:
        return ToolResult(
            success=False, output="",
            error=f"httpx binary not found at {os.path.join(BIN_DIR, 'httpx')}",
        )

    # Validate each target line
    target_lines = [t.strip() for t in targets.strip().splitlines() if t.strip()]
    if not target_lines:
        return ToolResult(success=False, output="", error="No valid targets provided")

    for line in target_lines:
        if _DANGEROUS_CHARS.search(line):
            return ToolResult(
                success=False, output="",
                error=f"Invalid target: contains disallowed characters: {line[:80]}",
            )

    # Dynamic timeout: base 120s + 0.5s per target, capped at 360s
    dynamic_timeout = min(120 + len(target_lines) // 2, 360)

    tmpfile = None
    try:
        # Write targets to temp file
        tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="httpx_targets_", delete=False,
        )
        tmpfile.write("\n".join(target_lines))
        tmpfile.flush()
        tmpfile.close()

        stdout, stderr, rc = await _run_binary(
            [
                binary, "-l", tmpfile.name, "-json", "-silent",
                "-sc", "-title", "-td", "-cl", "-fr", "-threads", "30",
            ],
            timeout=dynamic_timeout,
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"httpx timed out after {dynamic_timeout}s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("httpx_exec_error", error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"httpx execution failed: {e}",
            execution_time_ms=elapsed,
        )
    finally:
        if tmpfile and os.path.exists(tmpfile.name):
            try:
                os.unlink(tmpfile.name)
            except OSError:
                pass

    elapsed = int((time.monotonic() - start) * 1000)

    # Parse JSONL output
    records = _parse_jsonl(stdout)

    alive = []
    for rec in records:
        status_code = rec.get("status_code") or rec.get("status-code")
        if status_code is None:
            continue

        entry = {
            "url": rec.get("url", ""),
            "status_code": status_code,
            "title": rec.get("title", ""),
            "tech": rec.get("tech", []) or [],
            "content_length": rec.get("content_length", rec.get("content-length", 0)),
        }
        alive.append(entry)

    dead_count = len(target_lines) - len(alive)

    # Build output
    lines = [
        "HTTP Probe Results",
        f"Targets: {len(target_lines)} | Alive: {len(alive)} | Dead/Filtered: {max(dead_count, 0)}",
        "",
    ]
    for entry in alive:
        tech_str = ", ".join(entry["tech"]) if entry["tech"] else "—"
        title_str = entry["title"] or "—"
        lines.append(
            f"  [{entry['status_code']}] {entry['url']}"
            f"  |  Title: {title_str}  |  Tech: {tech_str}"
            f"  |  Size: {entry['content_length']}"
        )

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={
            "alive": alive,
            "count": len(alive),
            "dead_count": max(dead_count, 0),
        },
    )


# ---------------------------------------------------------------------------
# 3. katana_crawl
# ---------------------------------------------------------------------------

async def katana_crawl(url: str, depth: int = 2) -> ToolResult:
    """Crawl a target URL using katana (headless crawler).

    Performs JS-aware crawling to discover URLs, JavaScript files,
    and API endpoints. Supports configurable crawl depth.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    # Clamp depth to sane range
    depth = max(1, min(depth, 5))

    binary = _get_binary("katana")
    if not binary:
        return ToolResult(
            success=False, output="",
            error=f"katana binary not found at {os.path.join(BIN_DIR, 'katana')}",
        )

    try:
        stdout, stderr, rc = await _run_binary(
            [
                binary, "-u", url, "-d", str(depth),
                "-jsonl", "-silent", "-js-crawl", "-known-files", "all",
                "-timeout", "10",
            ],
            timeout=170,
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error="katana timed out after 170s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("katana_exec_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"katana execution failed: {e}",
            execution_time_ms=elapsed,
        )

    elapsed = int((time.monotonic() - start) * 1000)

    # Parse JSONL output
    records = _parse_jsonl(stdout)

    all_urls: set[str] = set()
    js_files: set[str] = set()
    endpoints: set[str] = set()

    for rec in records:
        # katana JSON has various fields depending on version;
        # common: "request.endpoint", "response.url", or just "endpoint"
        found_url = (
            rec.get("request", {}).get("endpoint", "")
            or rec.get("endpoint", "")
            or rec.get("url", "")
        )
        if not found_url:
            continue

        all_urls.add(found_url)

        # Classify JS files
        lower = found_url.lower().split("?")[0]  # strip query params
        if lower.endswith((".js", ".mjs")):
            js_files.add(found_url)

        # Classify API endpoints
        if _API_PATTERNS.search(found_url):
            endpoints.add(found_url)

    # If JSONL parsing yielded nothing, try plain-text lines as fallback
    if not all_urls and stdout.strip():
        for line in stdout.strip().splitlines():
            line = line.strip()
            if line.startswith(("http://", "https://")):
                all_urls.add(line)
                lower = line.lower().split("?")[0]
                if lower.endswith((".js", ".mjs")):
                    js_files.add(line)
                if _API_PATTERNS.search(line):
                    endpoints.add(line)

    sorted_urls = sorted(all_urls)
    sorted_js = sorted(js_files)
    sorted_endpoints = sorted(endpoints)

    # Build output
    lines = [
        f"Katana Crawl: {url} (depth={depth})",
        f"URLs: {len(sorted_urls)} | JS files: {len(sorted_js)} | API endpoints: {len(sorted_endpoints)}",
        "",
    ]

    if sorted_endpoints:
        lines.append("API Endpoints:")
        for ep in sorted_endpoints[:50]:
            lines.append(f"  {ep}")
        lines.append("")

    if sorted_js:
        lines.append("JavaScript Files:")
        for js in sorted_js[:50]:
            lines.append(f"  {js}")
        lines.append("")

    if sorted_urls:
        lines.append(f"All URLs ({len(sorted_urls)}, showing first 100):")
        for u in sorted_urls[:100]:
            lines.append(f"  {u}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={
            "urls": sorted_urls,
            "js_files": sorted_js,
            "endpoints": sorted_endpoints,
            "url_count": len(sorted_urls),
            "js_count": len(sorted_js),
        },
    )


# ---------------------------------------------------------------------------
# 4. gau_urls
# ---------------------------------------------------------------------------

async def gau_urls(domain: str) -> ToolResult:
    """Fetch known URLs for a domain from web archives (Wayback, Common Crawl, etc.).

    Uses gau (GetAllUrls) to query multiple passive URL sources.
    Filters out static assets and separates JS files.
    """
    start = time.monotonic()

    err = _validate_domain(domain)
    if err:
        return ToolResult(success=False, output="", error=err)

    binary = _get_binary("gau")
    if not binary:
        return ToolResult(
            success=False, output="",
            error=f"gau binary not found at {os.path.join(BIN_DIR, 'gau')}",
        )

    try:
        stdout, stderr, rc = await _run_binary(
            [binary, "--threads", "5", "--subs", domain],
            timeout=110,
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error="gau timed out after 110s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("gau_exec_error", domain=domain, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"gau execution failed: {e}",
            execution_time_ms=elapsed,
        )

    elapsed = int((time.monotonic() - start) * 1000)

    urls: set[str] = set()
    js_files: set[str] = set()

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue

        # Get path without query string for extension check
        path_lower = line.lower().split("?")[0]
        ext = os.path.splitext(path_lower)[1]

        # Skip static assets
        if ext in _STATIC_EXTENSIONS:
            continue

        # Classify JS
        if ext in (".js", ".mjs"):
            js_files.add(line)
        else:
            urls.add(line)

    sorted_urls = sorted(urls)
    sorted_js = sorted(js_files)

    # Build output
    lines = [
        f"GAU (GetAllUrls) for: {domain}",
        f"URLs: {len(sorted_urls)} | JS files: {len(sorted_js)} (static assets filtered out)",
        "",
    ]

    if sorted_js:
        lines.append(f"JavaScript Files ({len(sorted_js)}, showing first 50):")
        for js in sorted_js[:50]:
            lines.append(f"  {js}")
        lines.append("")

    lines.append(f"URLs ({len(sorted_urls)}, showing first 100):")
    for u in sorted_urls[:100]:
        lines.append(f"  {u}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={
            "urls": sorted_urls,
            "js_files": sorted_js,
            "url_count": len(sorted_urls),
            "js_count": len(sorted_js),
        },
    )


# ---------------------------------------------------------------------------
# 5. ffuf_fuzz
# ---------------------------------------------------------------------------

async def ffuf_fuzz(url: str, wordlist: str = "bounty") -> ToolResult:
    """Fuzz directories/files on a target URL using ffuf.

    Uses built-in wordlists (common, medium, small, api, backup, bounty)
    to discover hidden paths. Matches on 200, 301, 302, 403 status codes.
    """
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    binary = _get_binary("ffuf")
    if not binary:
        return ToolResult(
            success=False, output="",
            error=f"ffuf binary not found at {os.path.join(BIN_DIR, 'ffuf')}",
        )

    # Import wordlists from web_attack module
    try:
        from src.tools.web_attack import _WORDLISTS
    except ImportError as e:
        return ToolResult(
            success=False, output="",
            error=f"Failed to import wordlists from web_attack: {e}",
        )

    valid_lists = list(_WORDLISTS.keys())
    if wordlist not in _WORDLISTS:
        return ToolResult(
            success=False, output="",
            error=f"Invalid wordlist: '{wordlist}'. Choose from: {', '.join(valid_lists)}",
        )

    words = _WORDLISTS[wordlist]
    if not words:
        return ToolResult(
            success=False, output="",
            error=f"Wordlist '{wordlist}' is empty",
        )

    # Ensure URL doesn't end with /FUZZ already — we append it
    target_url = url.rstrip("/") + "/FUZZ"

    wordfile = None
    outfile = None
    try:
        # Write wordlist to temp file (one word per line, strip leading /)
        wordfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="ffuf_words_", delete=False,
        )
        for word in words:
            # ffuf expects words without leading slash when URL has /FUZZ
            wordfile.write(word.lstrip("/") + "\n")
        wordfile.flush()
        wordfile.close()

        # Output file for JSON results
        outfile_fd, outfile_path = tempfile.mkstemp(suffix=".json", prefix="ffuf_out_")
        os.close(outfile_fd)
        outfile = outfile_path

        stdout, stderr, rc = await _run_binary(
            [
                binary, "-u", target_url,
                "-w", wordfile.name,
                "-mc", "200,301,302,403",
                "-o", outfile,
                "-of", "json",
                "-t", "20",
                "-timeout", "10",
                "-s",
            ],
            timeout=110,
        )

        # Parse output JSON file
        results_data = []
        if os.path.isfile(outfile):
            try:
                with open(outfile, "r") as f:
                    content = f.read().strip()
                if content:
                    parsed = json.loads(content)
                    results_data = parsed.get("results", [])
            except (json.JSONDecodeError, OSError) as e:
                log.warning("ffuf_output_parse_error", error=str(e))

    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error="ffuf timed out after 110s",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("ffuf_exec_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="",
            error=f"ffuf execution failed: {e}",
            execution_time_ms=elapsed,
        )
    finally:
        # Clean up temp files
        if wordfile and os.path.exists(wordfile.name):
            try:
                os.unlink(wordfile.name)
            except OSError:
                pass
        if outfile and os.path.exists(outfile):
            try:
                os.unlink(outfile)
            except OSError:
                pass

    elapsed = int((time.monotonic() - start) * 1000)

    found = []
    for r in results_data:
        result_url = r.get("url", "")
        # Extract the discovered path from the full URL
        path = result_url.replace(url.rstrip("/"), "") if result_url else r.get("input", {}).get("FUZZ", "")
        found.append({
            "path": path or "/",
            "url": result_url,
            "status": r.get("status", 0),
            "length": r.get("length", 0),
        })

    # Sort by status code, then path
    found.sort(key=lambda x: (x["status"], x["path"]))

    # Build output
    lines = [
        f"FFUF Directory Fuzz: {url}",
        f"Wordlist: {wordlist} ({len(words)} words)",
        f"Match codes: 200, 301, 302, 403",
        f"Found: {len(found)} paths",
        "",
    ]
    for entry in found:
        lines.append(
            f"  [{entry['status']}] {entry['path']}"
            f"  ({entry['length']} bytes)  ->  {entry['url']}"
        )

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={
            "found": found,
            "count": len(found),
        },
    )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

subfinder_enum_tool = ToolDefinition(
    name="subfinder_enum",
    description=(
        "Enumerate subdomains using subfinder (passive reconnaissance). "
        "Queries 40+ passive sources (crt.sh, VirusTotal, Shodan, etc.) "
        "and returns unique subdomains with source attribution. "
        "For authorized security testing and bug bounty only."
    ),
    parameters=[
        ToolParameter(
            name="domain",
            type="string",
            description="Target domain (e.g. 'example.com')",
        ),
    ],
    handler=subfinder_enum,
    timeout_seconds=120,
)

httpx_probe_tool = ToolDefinition(
    name="httpx_probe",
    description=(
        "Probe hosts for alive HTTP services. Takes newline-separated "
        "domains or URLs as input and returns live hosts with status codes, "
        "page titles, detected technologies, and content length. "
        "Useful after subdomain enumeration to find active web services."
    ),
    parameters=[
        ToolParameter(
            name="targets",
            type="string",
            description=(
                "Newline-separated list of domains or URLs to probe "
                "(e.g. 'sub1.example.com\\nsub2.example.com')"
            ),
        ),
    ],
    handler=httpx_probe,
    timeout_seconds=180,
)

katana_crawl_tool = ToolDefinition(
    name="katana_crawl",
    description=(
        "Crawl a target URL using katana (JS-aware headless crawler). "
        "Discovers URLs, JavaScript files, and API endpoints. "
        "Supports configurable depth and known-files detection."
    ),
    parameters=[
        ToolParameter(
            name="url",
            type="string",
            description="Target URL to crawl (e.g. 'https://example.com')",
        ),
        ToolParameter(
            name="depth",
            type="integer",
            description="Crawl depth (1-5, default 2)",
            required=False,
            default=2,
        ),
    ],
    handler=katana_crawl,
    timeout_seconds=180,
)

gau_urls_tool = ToolDefinition(
    name="gau_urls",
    description=(
        "Fetch known URLs for a domain from web archives and passive sources "
        "(Wayback Machine, Common Crawl, AlienVault OTX, URLScan). "
        "Filters static assets and separates JavaScript files. "
        "Useful for finding historical endpoints and hidden resources."
    ),
    parameters=[
        ToolParameter(
            name="domain",
            type="string",
            description="Target domain (e.g. 'example.com')",
        ),
    ],
    handler=gau_urls,
    timeout_seconds=120,
)

ffuf_fuzz_tool = ToolDefinition(
    name="ffuf_fuzz",
    description=(
        "Fuzz directories and files on a target URL using ffuf. "
        "Uses built-in wordlists to discover hidden paths, admin panels, "
        "config files, and sensitive endpoints. "
        "Matches on 200, 301, 302, 403 status codes."
    ),
    parameters=[
        ToolParameter(
            name="url",
            type="string",
            description="Target base URL (e.g. 'https://example.com'). /FUZZ is appended automatically.",
        ),
        ToolParameter(
            name="wordlist",
            type="string",
            description="Wordlist to use for fuzzing",
            required=False,
            default="bounty",
            enum=["common", "medium", "small", "api", "backup", "bounty"],
        ),
    ],
    handler=ffuf_fuzz,
    timeout_seconds=120,
    requires_confirmation=True,
)
