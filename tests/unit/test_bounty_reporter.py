"""Tests for src.bounty.reporter — BountyReportGenerator."""

from __future__ import annotations

import pytest

from src.bounty.reporter import BountyReportGenerator
from src.bounty.models import BountyFinding


def _make_finding(**kwargs):
    defaults = dict(
        target_id=1,
        vuln_type="sqli",
        severity="CRITICAL",
        cvss=9.8,
        confidence=0.9,
        title="SQL Injection in login form",
        description="The login endpoint is vulnerable to SQL injection.",
        steps_to_reproduce="1. Go to /login\n2. Enter payload",
        poc="' OR 1=1--",
        impact="Full database access",
        suggested_fix="Use parameterized queries",
        estimated_bounty_low=3000,
        estimated_bounty_high=5000,
    )
    defaults.update(kwargs)
    return BountyFinding(**defaults)


class TestGenerateHackerOne:
    def test_has_all_sections(self):
        finding = _make_finding()
        report = BountyReportGenerator.generate_hackerone(finding, "example.com")

        assert "## Summary" in report
        assert "## Severity" in report
        assert "## Steps to Reproduce" in report
        assert "## Impact" in report
        assert "## Proof of Concept" in report
        assert "## Suggested Fix" in report
        assert "SQLI" in report
        assert "example.com" in report

    def test_empty_fields_show_na(self):
        finding = _make_finding(
            steps_to_reproduce="",
            impact="",
            poc="",
            suggested_fix="",
        )
        report = BountyReportGenerator.generate_hackerone(finding, "test.com")
        assert "N/A" in report


class TestGenerateMarkdown:
    def test_contains_key_info(self):
        finding = _make_finding()
        report = BountyReportGenerator.generate_markdown(finding, "example.com")

        assert "SQL Injection in login form" in report
        assert "example.com" in report
        assert "CRITICAL" in report
        assert "9.8" in report
        assert "$3000-$5000" in report


class TestGenerateTelegramSummary:
    def test_summary_with_program(self):
        finding = _make_finding()
        summary = BountyReportGenerator.generate_telegram_summary(
            finding, program_name="ExampleCorp", platform="HackerOne"
        )

        assert "Bug Bounty Finding!" in summary
        assert "ExampleCorp" in summary
        assert "HackerOne" in summary
        assert "CRITICAL" in summary
        assert "90%" in summary
        assert "$3000-$5000" in summary

    def test_summary_without_program(self):
        finding = _make_finding(
            estimated_bounty_low=None,
            estimated_bounty_high=None,
        )
        summary = BountyReportGenerator.generate_telegram_summary(finding)

        assert "Bug Bounty Finding!" in summary
        assert "N/A" in summary
        # Should not have empty "Program:" line
        assert "Program:" not in summary
