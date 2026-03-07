"""JSAnalysisAgent — JavaScript secret and sensitive data extraction.

Scans JS files discovered by CrawlerAgent and alive hosts for exposed
secrets (API keys, tokens, credentials). Uses regex-based scanning via
the js_secrets_scan tool, then applies AI validation to filter false
positives (e.g., example keys, public Google Maps keys).
"""

from __future__ import annotations

import json
import re
import time

from src.bounty.agents.base import AgentResult, BaseHunterAgent
from src.bounty.models import BountyFinding
from src.utils.logging import get_logger

log = get_logger("bounty.agents.js_analyzer")


class JSAnalysisAgent(BaseHunterAgent):
    """Scan JavaScript files and HTML pages for exposed secrets."""

    name = "js_analyzer"

    async def run(self, context: dict) -> AgentResult:
        """Scan JS files and alive hosts for leaked secrets."""
        start = time.time()
        js_files: list[str] = context.get("js_files", [])
        alive_hosts: list[dict] = context.get("alive_hosts", [])
        errors: list[str] = []
        findings: list[BountyFinding] = []

        if not js_files and not alive_hosts:
            return self._make_result(
                success=True,
                data={"secrets_count": 0, "hosts_scanned": 0, "js_scanned": 0},
                start_time=start,
            )

        scanned: set[str] = set()

        # 1. Scan top alive hosts (js_secrets_scan finds <script> tags from HTML)
        for host in alive_hosts[:10]:
            url = host.get("url", "")
            if not url or url in scanned:
                continue
            scanned.add(url)
            try:
                result = await self.registry.execute("js_secrets_scan", url=url)
                if result.success and result.data.get("secrets_found"):
                    for secret in result.data.get("findings", []):
                        findings.append(self._secret_to_finding(secret, url))
            except Exception as e:
                errors.append(f"js_scan {url}: {e}")

        # 2. Scan individual JS file URLs not already covered by host scans
        #    Group by host to avoid duplicate scanning, cap at 20 unique JS files
        js_scanned = 0
        for js_url in js_files[:20]:
            if js_url in scanned:
                continue
            scanned.add(js_url)
            try:
                result = await self.registry.execute("js_secrets_scan", url=js_url)
                js_scanned += 1
                if result.success and result.data.get("secrets_found"):
                    for secret in result.data.get("findings", []):
                        findings.append(self._secret_to_finding(secret, js_url))
            except Exception as e:
                errors.append(f"js_scan {js_url}: {e}")

        # 3. AI validation to filter false positives
        if self.llm and findings:
            findings = await self._ai_validate(findings)

        log.info(
            "js_analysis_complete",
            secrets=len(findings),
            hosts_scanned=len(scanned),
        )

        return self._make_result(
            success=True,
            data={
                "secrets_count": len(findings),
                "hosts_scanned": min(len(alive_hosts), 10),
                "js_scanned": js_scanned,
            },
            findings=findings,
            errors=errors,
            start_time=start,
        )

    def _secret_to_finding(self, secret: dict, url: str) -> BountyFinding:
        """Convert a raw secret detection into a BountyFinding."""
        pattern = secret.get("pattern", "unknown")
        pn_lower = pattern.lower()

        # Severity assignment based on secret type
        if "aws" in pn_lower or "private key" in pn_lower:
            severity, cvss = "CRITICAL", 9.5
        elif any(
            k in pn_lower
            for k in ("github", "stripe secret", "slack", "sendgrid", "twilio", "database")
        ):
            severity, cvss = "HIGH", 8.5
        elif any(k in pn_lower for k in ("firebase", "jwt", "bearer", "password")):
            severity, cvss = "HIGH", 8.0
        else:
            severity, cvss = "HIGH", 8.2

        source = secret.get("source", "JS file")
        match_preview = str(secret.get("match", ""))[:100]
        context_text = secret.get("context", "")

        return BountyFinding(
            target_id=0,
            vuln_type="js_secrets",
            severity=severity,
            cvss=cvss,
            confidence=0.7,  # Before AI validation
            title=f"Secret exposed: {pattern} on {url}",
            description=(
                f"Found {pattern} pattern in {source}. "
                f"Context: {context_text[:200]}" if context_text else f"Found {pattern} pattern in {source}."
            ),
            poc=f"Pattern: {pattern}, Match: {match_preview}",
            impact=(
                "Exposed secrets in client-side JavaScript can be harvested by any visitor. "
                "Depending on the key type, an attacker could gain unauthorized access to "
                "backend services, cloud infrastructure, or sensitive data."
            ),
            suggested_fix=(
                "Remove the secret from client-side code. Use environment variables or a "
                "backend proxy to handle sensitive API calls. Rotate the exposed credential "
                "immediately."
            ),
        )

    async def _ai_validate(self, findings: list[BountyFinding]) -> list[BountyFinding]:
        """Use LLM to distinguish real secrets from false positives.

        Public/non-sensitive keys (Google Maps JS key, Stripe publishable key,
        example/test keys) are filtered out. True positives get boosted confidence.
        """
        # Cap at 20 findings to limit token usage
        batch = findings[:20]
        overflow = findings[20:]

        items = []
        for i, f in enumerate(batch):
            items.append(f"[{i}] {f.title}\n    PoC: {f.poc}")

        prompt = (
            "You are a security analyst reviewing potential secrets found in JavaScript files.\n"
            "For each finding, determine if it's a REAL secret or FALSE POSITIVE.\n"
            "Consider: test/example keys, public API keys (like Google Maps JS API key, "
            "Stripe publishable key pk_live/pk_test), placeholder values (xxx, example, test123) "
            "vs actual secret keys (AWS Secret Access Key, Stripe secret sk_live, private keys, "
            "database passwords, OAuth client secrets).\n\n"
            "Findings:\n" + "\n".join(items) + "\n\n"
            "Respond with ONLY a JSON array of indices that are TRUE POSITIVES.\n"
            "Example: [0, 3, 5]\n"
            "If none are real: []\n"
        )

        try:
            response = await self.llm(prompt)
            match = re.search(r"\[[\d,\s]*\]", response)
            if match:
                real_indices = json.loads(match.group())
                verified: list[BountyFinding] = []
                for i, f in enumerate(batch):
                    if i in real_indices:
                        f.confidence = 0.90  # AI confirmed as real
                        verified.append(f)
                    # else: dropped as false positive
                # Keep overflow findings with original confidence
                verified.extend(overflow)
                return verified
        except Exception as e:
            log.warning("ai_validate_failed", error=str(e))
            # AI failure — keep all findings with original confidence

        return findings
