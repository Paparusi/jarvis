"""Recon Engine for the Bug Bounty Pipeline.

Runs passive and active reconnaissance against a target domain using
registered JARVIS tools (subdomain_enum, tech_detect, whois, etc.).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.tools.base import ToolRegistry, ToolResult
from src.utils.logging import get_logger

log = get_logger("bounty.recon")


@dataclass
class ReconResult:
    """Aggregated results from reconnaissance scans."""

    domain: str = ""
    subdomains: list[str] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)
    interesting_endpoints: list[str] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)
    cves: list[dict[str, Any]] = field(default_factory=list)
    tool_results: dict[str, ToolResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary of the recon results."""
        lines = [
            f"Recon Summary: {self.domain}",
            f"  Subdomains: {len(self.subdomains)}",
            f"  Tech stack: {len(self.tech_stack)}",
            f"  Interesting endpoints: {len(self.interesting_endpoints)}",
            f"  Leaks: {len(self.leaks)}",
            f"  CVEs: {len(self.cves)}",
            f"  Errors: {len(self.errors)}",
        ]
        return "\n".join(lines)


class ReconEngine:
    """Runs passive and active recon using JARVIS tools."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.registry = tool_registry

    async def _run_tool(self, name: str, **kwargs: Any) -> ToolResult:
        """Execute a tool, catching exceptions gracefully."""
        try:
            return await self.registry.execute(name, **kwargs)
        except Exception as exc:
            log.warning("recon_tool_error", tool=name, error=str(exc))
            return ToolResult(success=False, output="", error=str(exc))

    async def run_passive(self, domain: str) -> ReconResult:
        """Run passive recon tools in parallel.

        Tools: subdomain_enum, google_dork, wayback_lookup, github_leaks,
               tech_detect, whois.
        """
        result = ReconResult(domain=domain)

        tasks = {
            "subdomain_enum": self._run_tool("subdomain_enum", domain=domain),
            "google_dork": self._run_tool("google_dork", target=domain),
            "wayback_lookup": self._run_tool("wayback_lookup", url=f"https://{domain}"),
            "github_leaks": self._run_tool("github_leaks", query=domain),
            "tech_detect": self._run_tool("tech_detect", url=f"https://{domain}"),
            "whois": self._run_tool("whois", domain=domain),
        }

        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for tool_name, tool_result in zip(tasks.keys(), gathered):
            if isinstance(tool_result, Exception):
                result.errors.append(f"{tool_name}: {tool_result}")
                continue
            result.tool_results[tool_name] = tool_result
            if not tool_result.success:
                result.errors.append(f"{tool_name}: {tool_result.error}")

        # Extract subdomains
        sub_result = result.tool_results.get("subdomain_enum")
        if sub_result and sub_result.success and sub_result.data:
            subs = sub_result.data.get("subdomains", [])
            if isinstance(subs, list):
                result.subdomains = subs

        # Extract tech stack
        tech_result = result.tool_results.get("tech_detect")
        if tech_result and tech_result.success and tech_result.data:
            techs = tech_result.data.get("technologies", [])
            if isinstance(techs, list):
                result.tech_stack = techs

        # Extract leaks
        leak_result = result.tool_results.get("github_leaks")
        if leak_result and leak_result.success and leak_result.data:
            leak_items = leak_result.data.get("leaks", [])
            if isinstance(leak_items, list):
                result.leaks = leak_items

        log.info("passive_recon_complete", domain=domain, subdomains=len(result.subdomains))
        return result

    async def run_active(self, domain: str, url: str) -> ReconResult:
        """Run active recon tools in parallel.

        Tools: http_headers, ssl_check, cve_lookup.
        """
        result = ReconResult(domain=domain)

        tasks = {
            "http_headers": self._run_tool("http_headers", url=url),
            "ssl_check": self._run_tool("ssl_check", hostname=domain),
            "cve_lookup": self._run_tool("cve_lookup", query=domain),
        }

        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for tool_name, tool_result in zip(tasks.keys(), gathered):
            if isinstance(tool_result, Exception):
                result.errors.append(f"{tool_name}: {tool_result}")
                continue
            result.tool_results[tool_name] = tool_result
            if not tool_result.success:
                result.errors.append(f"{tool_name}: {tool_result.error}")

        # Extract CVEs
        cve_result = result.tool_results.get("cve_lookup")
        if cve_result and cve_result.success and cve_result.data:
            cve_items = cve_result.data.get("cves", [])
            if isinstance(cve_items, list):
                result.cves = cve_items

        log.info("active_recon_complete", domain=domain, cves=len(result.cves))
        return result

    async def run_full(self, domain: str, url: str) -> ReconResult:
        """Run both passive and active recon, merging results."""
        passive, active = await asyncio.gather(
            self.run_passive(domain),
            self.run_active(domain, url),
        )

        # Merge active into passive
        merged = ReconResult(
            domain=domain,
            subdomains=passive.subdomains,
            tech_stack=passive.tech_stack,
            interesting_endpoints=passive.interesting_endpoints + active.interesting_endpoints,
            leaks=passive.leaks,
            cves=active.cves,
            tool_results={**passive.tool_results, **active.tool_results},
            errors=passive.errors + active.errors,
        )

        log.info("full_recon_complete", domain=domain)
        return merged
