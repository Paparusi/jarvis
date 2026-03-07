"""Data models for the Bug Bounty Pipeline.

Dataclasses and enums for programs, targets, findings, and earnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TargetState(str, Enum):
    """State of a bounty target in the scanning pipeline."""

    QUEUED = "queued"
    SCANNING = "scanning"
    SCANNED = "scanned"
    RESCAN_SCHEDULED = "rescan_scheduled"


class FindingStatus(str, Enum):
    """Lifecycle status of a vulnerability finding."""

    PENDING = "pending"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Confidence(str, Enum):
    """Confidence level for a finding.

    CONFIRMED: 90%+ certainty
    LIKELY: 70-90% certainty
    POSSIBLE: 50-70% certainty
    FALSE_POSITIVE: <50% certainty
    """

    CONFIRMED = "confirmed"
    LIKELY = "likely"
    POSSIBLE = "possible"
    FALSE_POSITIVE = "false_positive"


@dataclass
class BountyProgram:
    """A bug bounty program on a platform (e.g., HackerOne, Bugcrowd)."""

    platform: str
    program_id: str
    name: str
    url: str = ""
    scope_domains: list[str] = field(default_factory=list)
    bounty_low: int = 0
    bounty_high: int = 0
    priority_score: float = 0.0
    status: str = "active"
    id: int | None = None


@dataclass
class BountyTarget:
    """A specific domain/asset within a bounty program's scope."""

    program_id: int
    domain: str
    scope_type: str = "domain"
    state: TargetState = TargetState.QUEUED
    scan_count: int = 0
    findings_count: int = 0
    id: int | None = None


@dataclass
class BountyFinding:
    """A vulnerability finding discovered during scanning."""

    target_id: int
    vuln_type: str
    severity: str
    cvss: float
    confidence: float
    title: str
    description: str = ""
    steps_to_reproduce: str = ""
    poc: str = ""
    impact: str = ""
    suggested_fix: str = ""
    estimated_bounty_low: int | None = None
    estimated_bounty_high: int | None = None
    status: FindingStatus = FindingStatus.PENDING
    id: int | None = None

    @property
    def confidence_level(self) -> Confidence:
        """Map numeric confidence to a Confidence enum level."""
        if self.confidence >= 0.9:
            return Confidence.CONFIRMED
        elif self.confidence >= 0.7:
            return Confidence.LIKELY
        elif self.confidence >= 0.5:
            return Confidence.POSSIBLE
        else:
            return Confidence.FALSE_POSITIVE

    @property
    def should_report(self) -> bool:
        """Only report findings with CONFIRMED or LIKELY confidence."""
        return self.confidence_level in (Confidence.CONFIRMED, Confidence.LIKELY)

    @property
    def estimated_bounty_str(self) -> str:
        """Human-readable bounty estimate, e.g. '$500-$2000' or 'N/A'."""
        if self.estimated_bounty_low is not None and self.estimated_bounty_high is not None:
            return f"${self.estimated_bounty_low}-${self.estimated_bounty_high}"
        return "N/A"
