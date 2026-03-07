"""Tests for Bug Bounty Pipeline — data models."""

import pytest

from src.bounty.models import (
    BountyFinding,
    BountyProgram,
    BountyTarget,
    Confidence,
    FindingStatus,
    TargetState,
)


class TestEnums:
    """Verify enum values."""

    def test_target_state_values(self):
        assert TargetState.QUEUED.value == "queued"
        assert TargetState.SCANNING.value == "scanning"
        assert TargetState.SCANNED.value == "scanned"
        assert TargetState.RESCAN_SCHEDULED.value == "rescan_scheduled"

    def test_finding_status_values(self):
        assert FindingStatus.PENDING.value == "pending"
        assert FindingStatus.APPROVED.value == "approved"
        assert FindingStatus.SUBMITTED.value == "submitted"
        assert FindingStatus.ACCEPTED.value == "accepted"
        assert FindingStatus.REJECTED.value == "rejected"

    def test_confidence_values(self):
        assert Confidence.CONFIRMED.value == "confirmed"
        assert Confidence.LIKELY.value == "likely"
        assert Confidence.POSSIBLE.value == "possible"
        assert Confidence.FALSE_POSITIVE.value == "false_positive"


class TestBountyProgram:
    """Test BountyProgram dataclass."""

    def test_create_program(self):
        prog = BountyProgram(
            platform="hackerone",
            program_id="acme_corp",
            name="Acme Corp",
        )
        assert prog.platform == "hackerone"
        assert prog.program_id == "acme_corp"
        assert prog.name == "Acme Corp"

    def test_program_defaults(self):
        prog = BountyProgram(platform="bugcrowd", program_id="bc1", name="Test")
        assert prog.url == ""
        assert prog.scope_domains == []
        assert prog.bounty_low == 0
        assert prog.bounty_high == 0
        assert prog.priority_score == 0.0
        assert prog.status == "active"
        assert prog.id is None

    def test_program_scope_domains(self):
        prog = BountyProgram(
            platform="hackerone",
            program_id="p1",
            name="Test",
            scope_domains=["example.com", "*.example.com"],
        )
        assert len(prog.scope_domains) == 2
        assert "example.com" in prog.scope_domains


class TestBountyTarget:
    """Test BountyTarget dataclass."""

    def test_create_target(self):
        target = BountyTarget(program_id=1, domain="api.example.com")
        assert target.program_id == 1
        assert target.domain == "api.example.com"

    def test_target_defaults(self):
        target = BountyTarget(program_id=1, domain="test.com")
        assert target.scope_type == "domain"
        assert target.state == TargetState.QUEUED
        assert target.scan_count == 0
        assert target.findings_count == 0
        assert target.id is None


class TestBountyFinding:
    """Test BountyFinding dataclass and computed properties."""

    def _make_finding(self, confidence: float = 0.95, **kwargs) -> BountyFinding:
        defaults = dict(
            target_id=1,
            vuln_type="XSS",
            severity="HIGH",
            cvss=7.5,
            confidence=confidence,
            title="Test XSS Finding",
        )
        defaults.update(kwargs)
        return BountyFinding(**defaults)

    def test_create_finding(self):
        f = self._make_finding()
        assert f.target_id == 1
        assert f.vuln_type == "XSS"
        assert f.status == FindingStatus.PENDING

    def test_finding_defaults(self):
        f = self._make_finding()
        assert f.description == ""
        assert f.steps_to_reproduce == ""
        assert f.poc == ""
        assert f.impact == ""
        assert f.suggested_fix == ""
        assert f.estimated_bounty_low is None
        assert f.estimated_bounty_high is None
        assert f.id is None

    def test_confidence_level_confirmed(self):
        f = self._make_finding(confidence=0.95)
        assert f.confidence_level == Confidence.CONFIRMED
        f2 = self._make_finding(confidence=0.90)
        assert f2.confidence_level == Confidence.CONFIRMED

    def test_confidence_level_likely(self):
        f = self._make_finding(confidence=0.85)
        assert f.confidence_level == Confidence.LIKELY
        f2 = self._make_finding(confidence=0.70)
        assert f2.confidence_level == Confidence.LIKELY

    def test_confidence_level_possible(self):
        f = self._make_finding(confidence=0.60)
        assert f.confidence_level == Confidence.POSSIBLE
        f2 = self._make_finding(confidence=0.50)
        assert f2.confidence_level == Confidence.POSSIBLE

    def test_confidence_level_false_positive(self):
        f = self._make_finding(confidence=0.40)
        assert f.confidence_level == Confidence.FALSE_POSITIVE
        f2 = self._make_finding(confidence=0.0)
        assert f2.confidence_level == Confidence.FALSE_POSITIVE

    def test_should_report_confirmed(self):
        f = self._make_finding(confidence=0.95)
        assert f.should_report is True

    def test_should_report_likely(self):
        f = self._make_finding(confidence=0.75)
        assert f.should_report is True

    def test_should_report_possible_false(self):
        f = self._make_finding(confidence=0.60)
        assert f.should_report is False

    def test_should_report_false_positive_false(self):
        f = self._make_finding(confidence=0.30)
        assert f.should_report is False

    def test_estimated_bounty_str_with_values(self):
        f = self._make_finding(estimated_bounty_low=500, estimated_bounty_high=2000)
        assert f.estimated_bounty_str == "$500-$2000"

    def test_estimated_bounty_str_na(self):
        f = self._make_finding()
        assert f.estimated_bounty_str == "N/A"
