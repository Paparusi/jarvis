"""Report Generator for the Bug Bounty Pipeline.

Produces formatted reports for HackerOne submission, generic Markdown,
and Telegram notification summaries.
"""

from __future__ import annotations

from src.bounty.models import BountyFinding


class BountyReportGenerator:
    """Generates bug bounty reports in various formats."""

    @staticmethod
    def generate_hackerone(finding: BountyFinding, domain: str) -> str:
        """Generate a HackerOne-style Markdown report.

        Sections: Summary, Severity, Steps to Reproduce, Impact,
                  Proof of Concept, Suggested Fix.
        """
        sections = [
            f"## Summary\n\n{finding.title}\n\n{finding.description}",
            f"## Severity\n\n{finding.severity} (CVSS {finding.cvss})",
            f"## Steps to Reproduce\n\n{finding.steps_to_reproduce or 'N/A'}",
            f"## Impact\n\n{finding.impact or 'N/A'}",
            f"## Proof of Concept\n\n{finding.poc or 'N/A'}",
            f"## Suggested Fix\n\n{finding.suggested_fix or 'N/A'}",
        ]
        header = f"# Bug Report: {finding.vuln_type.upper()} on {domain}\n\n"
        return header + "\n\n".join(sections)

    @staticmethod
    def generate_markdown(finding: BountyFinding, domain: str) -> str:
        """Generate a generic Markdown report."""
        lines = [
            f"# {finding.title}",
            "",
            f"**Domain:** {domain}",
            f"**Type:** {finding.vuln_type}",
            f"**Severity:** {finding.severity} (CVSS {finding.cvss})",
            f"**Confidence:** {finding.confidence:.0%} ({finding.confidence_level.value})",
            f"**Estimated Bounty:** {finding.estimated_bounty_str}",
            "",
            "## Description",
            "",
            finding.description or "N/A",
            "",
            "## Steps to Reproduce",
            "",
            finding.steps_to_reproduce or "N/A",
            "",
            "## Proof of Concept",
            "",
            finding.poc or "N/A",
            "",
            "## Impact",
            "",
            finding.impact or "N/A",
            "",
            "## Suggested Fix",
            "",
            finding.suggested_fix or "N/A",
        ]
        return "\n".join(lines)

    @staticmethod
    def generate_telegram_summary(
        finding: BountyFinding,
        program_name: str = "",
        platform: str = "",
    ) -> str:
        """Generate a short Telegram notification summary."""
        confidence_pct = f"{finding.confidence:.0%}"
        level = finding.confidence_level.value

        bounty_str = finding.estimated_bounty_str

        lines = [
            "Bug Bounty Finding!",
            "",
            f"Program: {program_name} ({platform})" if program_name else "",
            f"Vuln: {finding.title}",
            f"Severity: {finding.severity} (CVSS {finding.cvss})",
            f"Confidence: {confidence_pct} {level}",
            f"Est. Bounty: {bounty_str}",
        ]
        # Filter out empty lines from missing program_name
        return "\n".join(line for line in lines if line is not None)
