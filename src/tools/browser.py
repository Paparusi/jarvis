"""Browser Tool — Playwright-powered web browsing, search, and screenshots.

Uses headless Chromium for:
- Full JS-rendered page browsing with smart content extraction
- Google search via real browser (better than DDG for many queries)
- Taking screenshots of web pages
- Extracting text from JS-heavy SPAs

Falls back to httpx + html2text if playwright is not installed.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from urllib.parse import urlparse, quote_plus

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.browser")

_TIMEOUT = 30000  # ms for playwright
_MAX_TEXT = 8000   # chars before truncation
_BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet"}


# ---------------------------------------------------------------------------
# SSRF protection (reuse logic from web_search)
# ---------------------------------------------------------------------------

def _validate_url(url: str) -> str | None:
    """Validate URL and block SSRF targets. Returns error or None."""
    if not url or not url.strip():
        return "URL không được để trống"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"URL phải bắt đầu bằng http:// hoặc https://"
    if not parsed.netloc:
        return "URL không hợp lệ — thiếu hostname"

    import ipaddress
    import socket
    hostname = parsed.hostname or ""
    blocked = {"localhost", "127.0.0.1", "0.0.0.0", "::1",
               "metadata.google.internal", "169.254.169.254"}
    if hostname.lower() in blocked:
        return "Blocked: internal/private host"
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return "Blocked: private/loopback IP"
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
# Browser context helpers
# ---------------------------------------------------------------------------

async def _create_page(playwright, block_resources: bool = True):
    """Create a browser page with optional resource blocking for speed."""
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    page = await context.new_page()

    if block_resources:
        await page.route("**/*", _block_handler)

    return browser, page


async def _block_handler(route):
    """Block heavy resources (images, fonts, video) to speed up loading."""
    if route.request.resource_type in _BLOCKED_RESOURCES:
        await route.abort()
    else:
        await route.continue_()


def _extract_main_content(text: str) -> str:
    """Clean extracted text — remove excessive whitespace."""
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove lines that are just whitespace
    lines = [line for line in text.split("\n") if line.strip() or line == ""]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# browse_web — Full JS-rendered page access
# ---------------------------------------------------------------------------

async def browse_web(url: str, wait_seconds: int = 2, selector: str = "") -> ToolResult:
    """Browse a URL with full JavaScript rendering and extract content.

    Better than fetch_url for:
    - SPAs (React, Vue, Angular)
    - Pages with dynamic/lazy-loaded content
    - Sites that block simple HTTP clients
    """
    start = time.monotonic()

    # Validate URL
    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    wait_seconds = min(max(0, wait_seconds), 10)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return await _fallback_extract(url)

    try:
        async with async_playwright() as p:
            browser, page = await _create_page(p, block_resources=True)

            await page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT)

            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)

            title = await page.title()
            final_url = page.url

            # Smart content extraction
            if selector:
                element = await page.query_selector(selector)
                if element:
                    text = await element.inner_text()
                else:
                    text = f"Selector '{selector}' not found on page"
            else:
                # Try article/main content first, fall back to body
                for sel in ["article", "main", "[role='main']", ".content", "#content"]:
                    el = await page.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        if len(text) > 200:
                            break
                else:
                    text = await page.inner_text("body")

            await browser.close()

        text = _extract_main_content(text)
        if len(text) > _MAX_TEXT:
            text = text[:_MAX_TEXT] + "\n\n...(truncated)"

        elapsed = int((time.monotonic() - start) * 1000)

        output = f"**{title}**\nURL: {final_url}\n\n{text}"
        return ToolResult(
            success=True,
            output=output,
            execution_time_ms=elapsed,
            data={"title": title, "url": final_url},
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("browse_web_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="", error=f"Browse failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# deep_search — DDG search + Playwright content extraction
# ---------------------------------------------------------------------------

async def deep_search(query: str, max_results: int = 3) -> ToolResult:
    """Deep search: DDG finds URLs, then Playwright extracts full content.

    Better than web_search alone because:
    - Gets full page content (not just snippets)
    - Handles JS-rendered pages
    - Provides much more data for the LLM to synthesize
    """
    start = time.monotonic()

    if not query or not query.strip():
        return ToolResult(success=False, output="", error="Query không được để trống")

    max_results = max(1, min(max_results, 5))

    # Step 1: DDG search for URLs
    try:
        from src.tools.web_search import _sync_ddg_search
        ddg_results = await asyncio.to_thread(_sync_ddg_search, query, max_results + 2)
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Search failed: {e}")

    if not ddg_results:
        return ToolResult(
            success=True,
            output=f"Không tìm thấy kết quả cho: '{query}'",
            data={"results": []},
        )

    # Step 2: Visit top URLs with Playwright to get full content
    try:
        from playwright.async_api import async_playwright
        has_playwright = True
    except ImportError:
        has_playwright = False

    enriched = []
    urls_visited = 0

    if has_playwright:
        try:
            async with async_playwright() as p:
                browser, page = await _create_page(p, block_resources=True)

                for r in ddg_results[:max_results]:
                    href = r.get("href", "")
                    title = r.get("title", "")
                    body = r.get("body", "")

                    if not href or _validate_url(href):
                        enriched.append({
                            "title": title, "href": href,
                            "content": body,
                        })
                        continue

                    try:
                        await page.goto(href, wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(1)

                        # Smart extract
                        page_text = ""
                        for sel in ["article", "main", "[role='main']", ".content", "#content"]:
                            el = await page.query_selector(sel)
                            if el:
                                page_text = await el.inner_text()
                                if len(page_text) > 200:
                                    break
                        if not page_text or len(page_text) < 100:
                            page_text = await page.inner_text("body")

                        page_text = _extract_main_content(page_text)
                        # Limit per-page content
                        if len(page_text) > 3000:
                            page_text = page_text[:3000] + "\n...(truncated)"

                        enriched.append({
                            "title": title, "href": href,
                            "content": page_text,
                        })
                        urls_visited += 1
                    except Exception as e:
                        log.warning("deep_search_page_error", url=href, error=str(e))
                        enriched.append({
                            "title": title, "href": href,
                            "content": body,  # Fall back to DDG snippet
                        })

                await browser.close()
        except Exception as e:
            log.error("deep_search_browser_error", error=str(e))
            # Fall back to DDG snippets only
            for r in ddg_results[:max_results]:
                enriched.append({
                    "title": r.get("title", ""),
                    "href": r.get("href", ""),
                    "content": r.get("body", ""),
                })
    else:
        # No Playwright — just use DDG snippets
        for r in ddg_results[:max_results]:
            enriched.append({
                "title": r.get("title", ""),
                "href": r.get("href", ""),
                "content": r.get("body", ""),
            })

    elapsed = int((time.monotonic() - start) * 1000)

    # Format output
    lines = [f"Deep search: '{query}' ({len(enriched)} sources, {urls_visited} pages visited)\n"]
    for i, r in enumerate(enriched, 1):
        lines.append(f"{'='*60}")
        lines.append(f"## {i}. {r['title']}")
        lines.append(f"URL: {r['href']}\n")
        lines.append(r["content"])
        lines.append("")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        execution_time_ms=elapsed,
        data={"results": enriched, "count": len(enriched), "pages_visited": urls_visited},
    )


# ---------------------------------------------------------------------------
# screenshot_page — Take screenshots
# ---------------------------------------------------------------------------

async def screenshot_page(url: str, full_page: bool = False, wait_seconds: int = 2) -> ToolResult:
    """Take a screenshot of a web page."""
    start = time.monotonic()

    err = _validate_url(url)
    if err:
        return ToolResult(success=False, output="", error=err)

    wait_seconds = min(max(0, wait_seconds), 10)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ToolResult(
            success=False, output="",
            error="Playwright chưa cài. Chạy: pip install playwright && playwright install chromium",
        )

    try:
        async with async_playwright() as p:
            browser, page = await _create_page(p, block_resources=False)

            await page.goto(url, wait_until="networkidle", timeout=_TIMEOUT)

            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)

            screenshot_dir = Path("/tmp/jarvis_screenshots")
            screenshot_dir.mkdir(exist_ok=True)
            ts = int(time.time())
            screenshot_path = screenshot_dir / f"screenshot_{ts}.png"

            await page.screenshot(path=str(screenshot_path), full_page=full_page)
            title = await page.title()

            await browser.close()

        elapsed = int((time.monotonic() - start) * 1000)
        size = screenshot_path.stat().st_size

        return ToolResult(
            success=True,
            output=f"Screenshot saved: {screenshot_path}\nTitle: {title}\nSize: {size:,} bytes",
            execution_time_ms=elapsed,
            data={"path": str(screenshot_path), "title": title, "size": size},
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        log.error("screenshot_error", url=url, error=str(e))
        return ToolResult(
            success=False, output="", error=f"Screenshot failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Fallback (no Playwright)
# ---------------------------------------------------------------------------

async def _fallback_extract(url: str) -> ToolResult:
    """Fallback extraction using httpx + html2text (no JS rendering)."""
    start = time.monotonic()
    try:
        import httpx
        import html2text

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "JARVIS/2.0"})
            resp.raise_for_status()

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0
        text = h.handle(resp.text)

        if len(text) > _MAX_TEXT:
            text = text[:_MAX_TEXT] + "\n\n...(truncated)"

        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output=f"(fallback — no JS rendering)\n\n{text}",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="",
            error=f"Fallback extraction failed: {e}",
            execution_time_ms=elapsed,
        )


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

browse_web_tool = ToolDefinition(
    name="browse_web",
    description="Truy cập trang web với full JavaScript rendering. Tốt hơn fetch_url cho SPA, trang động, và trang chặn bot. Dùng khi cần đọc nội dung chi tiết từ một URL.",
    parameters=[
        ToolParameter(name="url", type="string", description="URL cần truy cập"),
        ToolParameter(name="wait_seconds", type="integer", description="Thời gian chờ JS render (0-10s)", required=False, default=2),
        ToolParameter(name="selector", type="string", description="CSS selector cụ thể (để trống = auto-detect nội dung chính)", required=False, default=""),
    ],
    handler=browse_web,
    timeout_seconds=40,
)

deep_search_tool = ToolDefinition(
    name="deep_search",
    description="Tìm kiếm sâu: search DDG + truy cập các trang kết quả để lấy nội dung chi tiết. Tốt hơn web_search khi cần thông tin đầy đủ, so sánh nhiều nguồn, hoặc nghiên cứu chuyên sâu.",
    parameters=[
        ToolParameter(name="query", type="string", description="Câu truy vấn tìm kiếm"),
        ToolParameter(name="max_results", type="integer", description="Số trang cần đọc chi tiết (mặc định 3, tối đa 5)", required=False, default=3),
    ],
    handler=deep_search,
    timeout_seconds=60,
)

# Backward compatibility alias
google_search_tool = deep_search_tool

screenshot_tool = ToolDefinition(
    name="screenshot",
    description="Chụp screenshot trang web. Dùng khi cần xem giao diện, layout, hoặc kiểm tra visual.",
    parameters=[
        ToolParameter(name="url", type="string", description="URL cần chụp"),
        ToolParameter(name="full_page", type="boolean", description="Chụp toàn bộ trang (scroll)", required=False, default=False),
        ToolParameter(name="wait_seconds", type="integer", description="Thời gian chờ load (0-10s)", required=False, default=2),
    ],
    handler=screenshot_page,
    timeout_seconds=40,
)

# Keep backward compatibility
extract_page_tool = browse_web_tool
