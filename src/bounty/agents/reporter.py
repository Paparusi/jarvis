"""ReportAgent — AI-powered bug bounty report generation.

Generates professional HackerOne-format vulnerability reports for findings
that meet the reporting threshold (confidence >= 0.70). Uses AI for
detailed, well-structured reports when available; falls back to template-
based reports otherwise.
"""

from __future__ import annotations

import time

from src.bounty.agents.base import AgentResult, BaseHunterAgent
from src.bounty.models import BountyFinding
from src.utils.logging import get_logger

log = get_logger("bounty.agents.reporter")


class ReportAgent(BaseHunterAgent):
    """Generate professional vulnerability reports for reportable findings."""

    name = "reporter"

    # Minimum confidence to generate a report
    REPORT_THRESHOLD = 0.70

    async def run(self, context: dict) -> AgentResult:
        """Generate reports for all reportable findings."""
        start = time.time()
        findings: list[BountyFinding] = context.get("findings", [])
        errors: list[str] = []
        reports: list[dict] = []

        # Extract program metadata
        program = context.get("program")
        program_name = ""
        if program:
            program_name = getattr(program, "name", str(program))
        domain = context.get("domain", "")

        # Only generate reports for findings above confidence threshold
        reportable = [f for f in findings if f.confidence >= self.REPORT_THRESHOLD]

        # Sort by severity for priority ordering
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
        reportable.sort(key=lambda f: (severity_order.get(f.severity, 5), -f.cvss))

        for finding in reportable:
            try:
                if self.llm:
                    report = await self._ai_report(finding, domain, program_name)
                else:
                    report = self._template_report(finding, domain, program_name)
                reports.append({
                    "finding": finding.title,
                    "severity": finding.severity,
                    "cvss": finding.cvss,
                    "confidence": finding.confidence,
                    "vuln_type": finding.vuln_type,
                    "report": report,
                })
            except Exception as e:
                errors.append(f"report {finding.title}: {e}")

        # Summary statistics
        severity_counts: dict[str, int] = {}
        for f in reportable:
            severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

        log.info(
            "reporter_complete",
            reports=len(reports),
            reportable=len(reportable),
            total_findings=len(findings),
            severity_counts=severity_counts,
        )

        return self._make_result(
            success=True,
            data={
                "reports": reports,
                "report_count": len(reports),
                "reportable_count": len(reportable),
                "total_findings": len(findings),
                "skipped_low_confidence": len(findings) - len(reportable),
                "severity_breakdown": severity_counts,
            },
            findings=reportable,
            errors=errors,
            start_time=start,
        )

    async def _ai_report(
        self, finding: BountyFinding, domain: str, program: str
    ) -> str:
        """Generate a professional report using AI."""
        prompt = (
            "Write a professional HackerOne bug bounty report.\n\n"
            f"Program: {program or 'Unknown'}\n"
            f"Domain: {domain}\n"
            f"Title: {finding.title}\n"
            f"Type: {finding.vuln_type}\n"
            f"Severity: {finding.severity} (CVSS {finding.cvss})\n"
            f"Confidence: {finding.confidence:.0%}\n"
            f"PoC: {finding.poc}\n"
            f"Description: {finding.description}\n"
        )
        if finding.steps_to_reproduce:
            prompt += f"Steps to reproduce: {finding.steps_to_reproduce}\n"
        if finding.impact:
            prompt += f"Known impact: {finding.impact}\n"
        if finding.suggested_fix:
            prompt += f"Suggested fix: {finding.suggested_fix}\n"

        prompt += (
            "\nFormat with these sections:\n"
            "## Summary\n"
            "## Severity\n"
            "## Steps to Reproduce\n"
            "## Impact\n"
            "## Suggested Remediation\n\n"
            "Be concise, professional, and technically accurate. "
            "Include curl commands or browser steps in Steps to Reproduce. "
            "Do NOT fabricate information — only reference what's provided above."
        )

        return await self.llm(prompt)

    def _template_report(
        self, finding: BountyFinding, domain: str, program: str
    ) -> str:
        """Generate a template-based report without AI."""
        steps = finding.steps_to_reproduce or (
            f"1. Navigate to the target: {domain}\n"
            f"2. {finding.description}\n"
            f"3. Observe the vulnerability"
        )

        impact = finding.impact or (
            "An attacker could exploit this vulnerability to compromise "
            "the application or gain unauthorized access to sensitive data."
        )

        fix = finding.suggested_fix or (
            "Apply appropriate security controls to mitigate this vulnerability. "
            "Refer to OWASP guidelines for remediation best practices."
        )

        return (
            f"## Summary\n\n"
            f"A **{finding.severity.lower()}** severity **{finding.vuln_type}** "
            f"vulnerability was discovered on `{domain}`.\n\n"
            f"{finding.description}\n\n"
            f"## Severity\n\n"
            f"**{finding.severity}** (CVSS {finding.cvss})\n\n"
            f"## Steps to Reproduce\n\n"
            f"{steps}\n\n"
            f"## Proof of Concept\n\n"
            f"```\n{finding.poc}\n```\n\n"
            f"## Impact\n\n"
            f"{impact}\n\n"
            f"## Suggested Remediation\n\n"
            f"{fix}\n"
        )
