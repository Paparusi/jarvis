"""Web Search Tool — Search the internet using DuckDuckGo.

No API key required. Uses duckduckgo-search library.
DuckDuckGo SDK is synchronous — wrapped in asyncio.to_thread() to avoid blocking.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.utils.logging import get_logger

log = get_logger("tools.web_search")


def _sync_ddg_search(query: str, max_results: int) -> list[dict]:
    """Run DuckDuckGo search synchronously (called via to_thread).

    Uses region='wt-wt' (worldwide) to avoid geo-locked junk results.
    Retries with English query variant if first attempt returns poor results.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results, region="wt-wt"))

        # If results look bad (Chinese/irrelevant pages), retry with region=en-us
        if results and _results_look_bad(results):
            log.warning("ddg_bad_results", query=query, first_title=results[0].get("title", "")[:50])
            results = list(ddgs.text(query, max_results=max_results, region="en-us"))

        return results


def _results_look_bad(results: list[dict]) -> bool:
    """Check if DDG results look like junk (wrong language, irrelevant)."""
    if not results:
        return True

    # Check first 2 results for non-Latin script (Chinese, etc.)
    import re
    for r in results[:2]:
        title = r.get("title", "")
        # Detect CJK characters (Chinese/Japanese/Korean)
        if re.search(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]", title):
            return True
        # Detect completely unrelated domains
        href = r.get("href", "")
        if any(d in href for d in ["baidu.com", "zhidao.", "community.tim.it"]):
            return True

    return False


async def search_web(query: str, max_results: int = 5) -> ToolResult:
    """Search the web using DuckDuckGo.

    DuckDuckGo SDK is synchronous, so we run it in a thread to avoid
    blocking the asyncio event loop.
    """
    if not query or not query.strip():
        return ToolResult(success=False, output="", error="Query không được để trống")

    # Clamp max_results
    max_results = max(1, min(max_results, 10))

    try:
        results = await asyncio.to_thread(_sync_ddg_search, query, max_results)

        if not results:
            return ToolResult(
                success=True,
                output=f"Không tìm thấy kết quả cho: '{query}'",
                data={"results": []},
            )

        lines = [f"Kết quả tìm kiếm cho: '{query}'\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            href = r.get("href", "")
            body = r.get("body", "")
            lines.append(f"{i}. **{title}**")
            lines.append(f"   {href}")
            lines.append(f"   {body}\n")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"results": results, "count": len(results)},
        )

    except ImportError:
        return ToolResult(
            success=False,
            output="",
            error="duckduckgo-search chưa được cài đặt. Chạy: pip install duckduckgo-search",
        )
    except Exception as e:
        log.error("web_search_error", query=query, error=str(e))
        return ToolResult(
            success=False,
            output="",
            error=f"Lỗi tìm kiếm: {e}",
        )


def _validate_url(url: str) -> str | None:
    """Validate URL format and block SSRF targets. Returns error message or None."""
    if not url or not url.strip():
        return "URL không được để trống"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"URL phải bắt đầu bằng http:// hoặc https:// (nhận được: '{parsed.scheme}')"
    if not parsed.netloc:
        return "URL không hợp lệ — thiếu hostname"

    # SSRF protection — block private/internal IPs
    import ipaddress
    import socket
    hostname = parsed.hostname or ""
    _BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1",
                      "metadata.google.internal", "169.254.169.254"}
    if hostname.lower() in _BLOCKED_HOSTS:
        return "Blocked: internal/private host"
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return "Blocked: private/loopback IP address"
    except ValueError:
        # hostname is a domain name — resolve and check
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            for _, _, _, _, addr in resolved:
                ip = ipaddress.ip_address(addr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return "Blocked: domain resolves to private IP"
        except (socket.gaierror, OSError):
            pass  # DNS resolution failed — let httpx handle it
    return None


async def fetch_url(url: str) -> ToolResult:
    """Fetch content from a URL and extract text."""
    # Validate URL
    url_error = _validate_url(url)
    if url_error:
        return ToolResult(success=False, output="", error=url_error)

    try:
        import httpx
        from html2text import HTML2Text

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "JARVIS/2.0"})
            resp.raise_for_status()

        h = HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0
        text = h.handle(resp.text)

        # Truncate to ~3000 chars to not blow up context
        if len(text) > 3000:
            text = text[:3000] + "\n\n...(truncated)"

        return ToolResult(
            success=True,
            output=text,
            data={"url": url, "status_code": resp.status_code},
        )

    except ImportError as e:
        missing = "httpx" if "httpx" in str(e) else "html2text"
        return ToolResult(
            success=False,
            output="",
            error=f"{missing} chưa được cài đặt. Chạy: pip install {missing}",
        )
    except Exception as e:
        log.error("fetch_url_error", url=url, error=str(e))
        return ToolResult(
            success=False,
            output="",
            error=f"Lỗi fetch URL: {e}",
        )


# --- Tool Definitions ---

web_search_tool = ToolDefinition(
    name="web_search",
    description="Tìm kiếm thông tin trên internet. Dùng khi cần tra cứu thông tin mới, tin tức, so sánh sản phẩm, hoặc bất kỳ thông tin nào cần dữ liệu cập nhật.",
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Câu truy vấn tìm kiếm (tiếng Việt hoặc tiếng Anh)",
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="Số kết quả tối đa (mặc định 5)",
            required=False,
            default=5,
        ),
    ],
    handler=search_web,
    timeout_seconds=20,
)

fetch_url_tool = ToolDefinition(
    name="fetch_url",
    description="Đọc nội dung từ một URL cụ thể. Dùng khi cần xem chi tiết một trang web cụ thể.",
    parameters=[
        ToolParameter(
            name="url",
            type="string",
            description="URL cần đọc nội dung",
        ),
    ],
    handler=fetch_url,
    timeout_seconds=15,
)
