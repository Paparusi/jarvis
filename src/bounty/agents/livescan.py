"""LiveScanAgent — Alive host probing and classification.

Probes discovered subdomains with httpx to identify live hosts, then
classifies each by priority based on page titles, URLs, and status codes
to focus scanning efforts on the most promising targets.
"""

from __future__ import annotations

import time

from src.bounty.agents.base import AgentResult, BaseHunterAgent, log


class LiveScanAgent(BaseHunterAgent):
    """Probe subdomains for alive hosts and classify by priority."""

    name = "livescan"

    async def run(self, context: dict) -> AgentResult:
        """Probe context['subdomains'] for alive hosts via httpx."""
        start = time.time()
        subdomains = context.get("subdomains", [])
        errors: list[str] = []

        if not subdomains:
            return self._make_result(
                success=True,
                data={"alive_hosts": [], "alive_count": 0, "dead_count": 0},
                start_time=start,
            )

        # 1. httpx probe subdomains in batches (200 per batch to avoid timeout)
        alive = []
        batch_size = 200
        for i in range(0, len(subdomains), batch_size):
            batch = subdomains[i : i + batch_size]
            targets = "\n".join(batch)
            try:
                result = await self.registry.execute("httpx_probe", targets=targets)
                if result.success:
                    alive.extend(result.data.get("alive", []))
            except Exception as e:
                errors.append(f"httpx_probe batch {i // batch_size}: {e}")

        # 2. Classify each host by priority
        for host in alive:
            host["priority"] = self._classify(host)

        # 3. Sort by priority (highest first)
        alive.sort(key=lambda h: h.get("priority", 0), reverse=True)

        log.info("livescan_complete", alive=len(alive), total=len(subdomains))

        return self._make_result(
            success=True,
            data={
                "alive_hosts": alive,
                "alive_count": len(alive),
                "dead_count": len(subdomains) - len(alive),
            },
            errors=errors,
            start_time=start,
        )

    def _classify(self, host: dict) -> int:
        """Assign a priority score (1-10) to a probed host.

        Priority levels:
        - 10: Admin/dashboard panels (high misconfig risk)
        -  9: CI/CD and monitoring tools (Jenkins, GitLab, Grafana...)
        -  8: API endpoints (/api, /graphql)
        -  7: Dev/staging/test environments
        -  6: Login/authentication pages
        -  3: Default (unclassified)
        -  2: Generic marketing/welcome pages
        -  1: Parked or error pages (403, 503)
        """
        title = (host.get("title") or "").lower()
        url = (host.get("url") or "").lower()

        # Admin/dashboard panels — highest priority
        if any(k in title for k in ("admin", "dashboard", "manage", "control panel", "backoffice")):
            return 10

        # CI/CD and monitoring — misconfigs common
        if any(k in title for k in (
            "jenkins", "gitlab", "jira", "grafana", "kibana",
            "prometheus", "sonarqube",
        )):
            return 9

        # API endpoints
        if any(k in url for k in ("/api", "api.", "/graphql", "/graphiql")):
            return 8

        # Dev/staging environments
        if any(k in url for k in (
            "dev.", "staging.", "test.", "uat.", "stg.",
            "preprod.", "sandbox.",
        )):
            return 7

        # Login/auth pages
        if any(k in title for k in ("login", "sign in", "authentication")):
            return 6

        # Default/generic pages — lower priority
        if any(k in title for k in ("welcome", "homepage", "marketing")):
            return 2

        # Parked/error pages
        if host.get("status_code", 0) in (403, 503):
            return 1

        # Default
        return 3
