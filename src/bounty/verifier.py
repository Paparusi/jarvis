"""Verifier for the Bug Bounty Pipeline.

Re-runs scan tools to confirm findings, estimates bounty amounts,
and deduplicates findings by vulnerability type.
"""

from __future__ import annotations

from src.bounty.models import BountyFinding
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("bounty.verifier")

_VULN_TO_TOOL: dict[str, str] = {
    "sqli": "sqli_test",
    "xss": "xss_scan",
    "lfi": "lfi_test",
    "cors": "cors_check",
    "headers": "header_audit",
    "missing_headers": "header_audit",
    "insecure_cookies": "header_audit",
    "js_secrets": "js_secrets_scan",
    "open_redirect": "open_redirect_test",
    "subdomain_takeover": "subdomain_takeover",
}

_SEVERITY_MULTIPLIER: dict[str, tuple[float, float]] = {
    "CRITICAL": (0.7, 1.0),
    "HIGH": (0.4, 0.7),
    "MEDIUM": (0.15, 0.4),
    "LOW": (0.05, 0.15),
    "INFO": (0.0, 0.05),
}


class Verifier:
    """Verifies, estimates bounties for, and deduplicates findings."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.registry = tool_registry

    async def verify(self, finding: BountyFinding, url: str) -> BountyFinding:
        """Re-run the scan tool to confirm or downgrade a finding.

        - If confirmed (data["vulnerable"]): confidence = max(current, 0.90)
        - If not confirmed: confidence = min(current, 0.40)
        - On error: keep original confidence
        - info_disclosure: follow redirect with http_request to check real status
        """
        # Special handling for info_disclosure (dir_bruteforce findings)
        if finding.vuln_type == "info_disclosure" and finding.poc:
            return await self._verify_info_disclosure(finding)

        # Nuclei findings: keep original confidence (already verified by nuclei engine)
        if finding.vuln_type.startswith("nuclei_"):
            return finding

        # js_secrets: uses "secrets_found" not "vulnerable"
        if finding.vuln_type == "js_secrets":
            try:
                result = await self.registry.execute("js_secrets_scan", url=url)
                if result.success and result.data and result.data.get("secrets_found"):
                    finding.confidence = max(finding.confidence, 0.90)
                else:
                    finding.confidence = min(finding.confidence, 0.40)
            except Exception:
                pass
            return finding

        tool_name = _VULN_TO_TOOL.get(finding.vuln_type)
        if not tool_name:
            log.warning("no_verify_tool", vuln_type=finding.vuln_type)
            return finding

        try:
            result = await self.registry.execute(tool_name, url=url)
            if result.success and result.data and result.data.get("vulnerable"):
                finding.confidence = max(finding.confidence, 0.90)
                log.info("finding_confirmed", vuln_type=finding.vuln_type)
            else:
                finding.confidence = min(finding.confidence, 0.40)
                log.info("finding_not_reproduced", vuln_type=finding.vuln_type)
        except Exception as exc:
            log.warning("verify_error", vuln_type=finding.vuln_type, error=str(exc))
            # Keep original confidence on error

        return finding

    async def _verify_info_disclosure(self, finding: BountyFinding) -> BountyFinding:
        """Verify info_disclosure by following redirects with http_request."""
        try:
            result = await self.registry.execute(
                "http_request", url=finding.poc, method="GET",
            )
            if result.success and result.data:
                status = result.data.get("status_code", 0)
                body_len = len(result.data.get("body", ""))
                if status == 200 and body_len > 50:
                    finding.confidence = max(finding.confidence, 0.92)
                    log.info("info_disclosure_confirmed", poc=finding.poc, status=status)
                elif status == 403:
                    finding.confidence = max(finding.confidence, 0.70)
                    log.info("info_disclosure_forbidden", poc=finding.poc)
                else:
                    # Redirected to homepage or 404 = false positive
                    finding.confidence = min(finding.confidence, 0.20)
                    log.info("info_disclosure_not_confirmed", poc=finding.poc, status=status)
            else:
                finding.confidence = min(finding.confidence, 0.30)
        except Exception as exc:
            log.warning("verify_info_disclosure_error", error=str(exc))
        return finding

    def estimate_bounty(
        self,
        finding: BountyFinding,
        bounty_low: int = 100,
        bounty_high: int = 5000,
    ) -> BountyFinding:
        """Estimate bounty range based on severity multiplier.

        Uses _SEVERITY_MULTIPLIER to compute:
          estimated_bounty_low  = bounty_low  + (bounty_high - bounty_low) * mult_low
          estimated_bounty_high = bounty_low  + (bounty_high - bounty_low) * mult_high
        """
        mult = _SEVERITY_MULTIPLIER.get(finding.severity, (0.0, 0.05))
        spread = bounty_high - bounty_low
        finding.estimated_bounty_low = int(bounty_low + spread * mult[0])
        finding.estimated_bounty_high = int(bounty_low + spread * mult[1])
        return finding

    def dedup_findings(self, findings: list[BountyFinding]) -> list[BountyFinding]:
        """Deduplicate findings, keeping the highest confidence per vuln_type."""
        best: dict[str, BountyFinding] = {}
        for f in findings:
            existing = best.get(f.vuln_type)
            if existing is None or f.confidence > existing.confidence:
                best[f.vuln_type] = f
        return list(best.values())
