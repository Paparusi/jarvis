"""ReconAgent — Subdomain enumeration with subfinder + crt.sh.

Discovers subdomains for a target domain using multiple sources, then
applies heuristic prioritization to surface the most interesting assets
(dev, staging, admin, API endpoints, CI/CD panels, etc.).
"""

from __future__ import annotations

import time

from src.bounty.agents.base import AgentResult, BaseHunterAgent, log


class ReconAgent(BaseHunterAgent):
    """Subdomain enumeration agent using subfinder (primary) and crt.sh (supplement)."""

    name = "recon"

    # Prefixes that indicate high-interest subdomains for bug hunting
    HIGH_PREFIXES = {
        "dev", "staging", "stg", "admin", "api", "test", "uat", "internal",
        "beta", "debug", "graphql", "jenkins", "gitlab", "jira", "grafana",
        "kibana", "elastic", "mongo", "redis", "postgres", "mysql", "phpmyadmin",
        "console", "portal", "dashboard", "manage", "cms", "vpn", "mail",
        "webmail", "ftp", "ssh", "git", "ci", "cd", "build", "deploy",
        "stage", "preprod", "pre-prod", "sandbox", "qa",
    }

    async def run(self, context: dict) -> AgentResult:
        """Enumerate subdomains for context['domain']."""
        start = time.time()
        domain = context["domain"]
        errors: list[str] = []

        # 1. Run subfinder (primary, 40+ sources)
        try:
            sf_result = await self.registry.execute("subfinder_enum", domain=domain)
            sf_subs = sf_result.data.get("subdomains", []) if sf_result.success else []
            sources = sf_result.data.get("sources", {}) if sf_result.success else {}
        except Exception as e:
            sf_subs, sources = [], {}
            errors.append(f"subfinder: {e}")

        # 2. Supplement with crt.sh (our existing tool)
        try:
            crt_result = await self.registry.execute("subdomain_enum", domain=domain)
            crt_subs = crt_result.data.get("subdomains", []) if crt_result.success else []
        except Exception as e:
            crt_subs = []
            errors.append(f"crt.sh: {e}")

        # 3. Merge and deduplicate
        all_subs = sorted(set(sf_subs + crt_subs))

        # 4. Prioritize by interest level
        prioritized = self._heuristic_prioritize(all_subs)

        log.info(
            "recon_complete",
            domain=domain,
            subdomains=len(prioritized),
            subfinder=len(sf_subs),
            crtsh=len(crt_subs),
            sources=len(sources),
        )

        return self._make_result(
            success=True,
            data={
                "domain": domain,
                "subdomains": prioritized,
                "subdomain_count": len(prioritized),
                "sources": sources,
            },
            errors=errors,
            start_time=start,
        )

    def _heuristic_prioritize(self, subs: list[str]) -> list[str]:
        """Sort subdomains by interest score (highest-interest first).

        Scoring:
        - 10: exact match on high-interest prefix (dev., admin., api., etc.)
        -  5: partial match (prefix contains a high-interest keyword)
        -  1: default/generic subdomain
        """

        def _score(subdomain: str) -> int:
            prefix = subdomain.split(".")[0].lower()
            if prefix in self.HIGH_PREFIXES:
                return 10
            # Partial match — keyword appears within the prefix
            for hp in self.HIGH_PREFIXES:
                if hp in prefix:
                    return 5
            return 1

        return [
            sub
            for _, sub in sorted(
                ((_score(s), s) for s in subs),
                key=lambda x: (-x[0], x[1]),
            )
        ]
