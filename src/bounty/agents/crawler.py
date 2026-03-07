"""CrawlerAgent — URL, JS file, and API endpoint discovery.

Crawls alive hosts using katana (active crawling with JS rendering) and
gau (passive URL collection from web archives) in parallel, then extracts
JavaScript files and API endpoints from the collected URLs.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from src.bounty.agents.base import AgentResult, BaseHunterAgent, log


class CrawlerAgent(BaseHunterAgent):
    """Discover URLs, JS files, and API endpoints from alive hosts."""

    name = "crawler"

    # URL path patterns that indicate API endpoints
    API_PATTERNS = ("/api/", "/graphql", "/v1/", "/v2/", "/v3/", "/rest/", "/ws/")

    async def run(self, context: dict) -> AgentResult:
        """Crawl context['alive_hosts'] for URLs, JS files, and endpoints."""
        start = time.time()
        alive_hosts = context.get("alive_hosts", [])
        errors: list[str] = []

        if not alive_hosts:
            return self._make_result(
                success=True,
                data={
                    "urls": [],
                    "js_files": [],
                    "endpoints": [],
                    "url_count": 0,
                    "js_count": 0,
                    "endpoint_count": 0,
                },
                start_time=start,
            )

        # Take top 5 by priority (most interesting hosts first)
        top_hosts = alive_hosts[:5]

        all_urls: set[str] = set()
        all_js: set[str] = set()
        all_endpoints: set[str] = set()

        # Parallel crawl with semaphore (max 3 concurrent to be polite)
        sem = asyncio.Semaphore(3)

        async def _crawl_host(host: dict) -> tuple[set[str], set[str], set[str]]:
            async with sem:
                url = host.get("url", "")
                domain = urlparse(url).hostname or ""
                host_urls: set[str] = set()
                host_js: set[str] = set()
                host_endpoints: set[str] = set()

                # katana: active crawling with JS rendering
                try:
                    kr = await self.registry.execute("katana_crawl", url=url, depth=2)
                    if kr.success:
                        host_urls.update(kr.data.get("urls", []))
                        host_js.update(kr.data.get("js_files", []))
                        host_endpoints.update(kr.data.get("endpoints", []))
                except Exception as e:
                    errors.append(f"katana {url}: {e}")

                # gau: passive URL collection from web archives
                try:
                    gr = await self.registry.execute("gau_urls", domain=domain)
                    if gr.success:
                        host_urls.update(gr.data.get("urls", []))
                        host_js.update(gr.data.get("js_files", []))
                except Exception as e:
                    errors.append(f"gau {domain}: {e}")

                return host_urls, host_js, host_endpoints

        results = await asyncio.gather(
            *[_crawl_host(h) for h in top_hosts],
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, Exception):
                errors.append(str(r))
                continue
            urls, js, endpoints = r
            all_urls.update(urls)
            all_js.update(js)
            all_endpoints.update(endpoints)

        # Also detect API endpoints from all collected URLs
        for u in all_urls:
            path = urlparse(u).path.lower()
            if any(p in path for p in self.API_PATTERNS):
                all_endpoints.add(u)

        sorted_urls = sorted(all_urls)
        sorted_js = sorted(all_js)
        sorted_endpoints = sorted(all_endpoints)

        log.info(
            "crawler_complete",
            urls=len(sorted_urls),
            js=len(sorted_js),
            endpoints=len(sorted_endpoints),
        )

        return self._make_result(
            success=True,
            data={
                "urls": sorted_urls,
                "js_files": sorted_js,
                "endpoints": sorted_endpoints,
                "url_count": len(sorted_urls),
                "js_count": len(sorted_js),
                "endpoint_count": len(sorted_endpoints),
            },
            errors=errors,
            start_time=start,
        )
