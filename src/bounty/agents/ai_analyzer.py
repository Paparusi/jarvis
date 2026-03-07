"""AIAnalyzerAgent — AI-powered false positive filtering and severity assessment.

Takes all findings from previous pipeline stages and applies:
1. Heuristic filtering (remove INFO, low-confidence duplicates)
2. AI-powered analysis by vulnerability type (batch LLM calls)
3. Deep scan target identification (hosts with HIGH+ findings)

Falls back to heuristic-only filtering when no LLM is available.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from urllib.parse import urlparse

from src.bounty.agents.base import AgentResult, BaseHunterAgent
from src.bounty.models import BountyFinding
from src.utils.logging import get_logger

log = get_logger("bounty.agents.ai_analyzer")


class AIAnalyzerAgent(BaseHunterAgent):
    """AI-powered analysis, deduplication, and severity refinement."""

    name = "ai_analyzer"

    async def run(self, context: dict) -> AgentResult:
        """Analyze and filter all findings from previous agents."""
        start = time.time()
        all_findings: list[BountyFinding] = context.get("findings", [])
        errors: list[str] = []

        if not all_findings:
            return self._make_result(
                success=True,
                data={
                    "verified_count": 0,
                    "filtered_count": 0,
                    "deep_scan_targets": [],
                },
                findings=[],
                start_time=start,
            )

        # Deduplicate findings before analysis
        deduped = self._deduplicate(all_findings)
        dedup_removed = len(all_findings) - len(deduped)

        # Without AI: heuristic filtering only
        if not self.llm:
            filtered = self._heuristic_filter(deduped)
            deep_targets = self._identify_deep_scan_targets(filtered, context)
            return self._make_result(
                success=True,
                data={
                    "verified_count": len(filtered),
                    "filtered_count": len(all_findings) - len(filtered),
                    "dedup_removed": dedup_removed,
                    "deep_scan_targets": deep_targets,
                },
                findings=filtered,
                start_time=start,
            )

        # With AI: batch analyze by vulnerability type
        by_type: dict[str, list[BountyFinding]] = defaultdict(list)
        for f in deduped:
            by_type[f.vuln_type].append(f)

        verified: list[BountyFinding] = []
        filtered_count = 0

        for vuln_type, group in by_type.items():
            try:
                result = await self._ai_analyze_group(vuln_type, group)
                verified.extend(result)
                filtered_count += len(group) - len(result)
            except Exception as e:
                errors.append(f"ai_analyze {vuln_type}: {e}")
                # On AI failure, fall back to heuristic filter for this group
                verified.extend(self._heuristic_filter(group))

        # Identify promising targets for deeper scanning
        deep_targets = self._identify_deep_scan_targets(verified, context)

        log.info(
            "ai_analyzer_complete",
            input=len(all_findings),
            dedup_removed=dedup_removed,
            verified=len(verified),
            filtered=filtered_count,
            deep_targets=len(deep_targets),
        )

        return self._make_result(
            success=True,
            data={
                "verified_count": len(verified),
                "filtered_count": filtered_count,
                "dedup_removed": dedup_removed,
                "deep_scan_targets": deep_targets,
            },
            findings=verified,
            errors=errors,
            start_time=start,
        )

    async def _ai_analyze_group(
        self, vuln_type: str, findings: list[BountyFinding]
    ) -> list[BountyFinding]:
        """Use LLM to classify a group of same-type findings."""
        # Cap at 15 per group to limit token usage
        batch = findings[:15]
        overflow = findings[15:]

        items = []
        for i, f in enumerate(batch):
            items.append(
                f"[{i}] {f.severity} | {f.title}\n"
                f"    PoC: {f.poc}\n"
                f"    Desc: {f.description[:200]}"
            )

        prompt = (
            f"You are an expert bug bounty analyst. Review these {vuln_type} findings.\n\n"
            "For EACH finding, classify as:\n"
            "- TRUE_POSITIVE (real vulnerability, worth reporting)\n"
            "- FALSE_POSITIVE (not exploitable, duplicate, or informational)\n"
            "- NEEDS_VERIFICATION (might be real but needs manual check)\n\n"
            "Also assess the correct severity (CRITICAL/HIGH/MEDIUM/LOW) and confidence (0.0-1.0).\n\n"
            f"Findings:\n" + "\n".join(items) + "\n\n"
            "Respond with ONLY a JSON array:\n"
            '[{"index": 0, "verdict": "TRUE_POSITIVE", "confidence": 0.9, "severity": "HIGH"}, ...]\n'
        )

        response = await self.llm(prompt)

        # Parse AI response
        try:
            match = re.search(r"\[.*\]", response, re.DOTALL)
            if match:
                verdicts = json.loads(match.group())
                verified: list[BountyFinding] = []
                for v in verdicts:
                    idx = v.get("index", -1)
                    verdict = v.get("verdict", "")
                    if 0 <= idx < len(batch) and verdict != "FALSE_POSITIVE":
                        f = batch[idx]
                        f.confidence = v.get("confidence", f.confidence)
                        new_severity = v.get("severity")
                        if new_severity and new_severity in (
                            "CRITICAL", "HIGH", "MEDIUM", "LOW"
                        ):
                            f.severity = new_severity
                        verified.append(f)
                # Keep overflow findings with original confidence
                verified.extend(overflow)
                return verified
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log.warning("ai_parse_failed", vuln_type=vuln_type, error=str(e))

        # Fallback: keep all with original values
        return findings

    def _heuristic_filter(self, findings: list[BountyFinding]) -> list[BountyFinding]:
        """Filter without AI — remove obvious false positives."""
        filtered = []
        for f in findings:
            # Skip INFO severity
            if f.severity == "INFO":
                continue
            # Skip very low confidence
            if f.confidence < 0.3:
                continue
            # Skip duplicate-looking titles (generic template matches)
            if "test" in f.title.lower() and f.confidence < 0.5:
                continue
            filtered.append(f)
        return filtered

    def _deduplicate(self, findings: list[BountyFinding]) -> list[BountyFinding]:
        """Remove duplicate findings based on title + vuln_type similarity."""
        seen: set[str] = set()
        unique: list[BountyFinding] = []
        for f in findings:
            # Create a dedup key from normalized title and type
            key = f"{f.vuln_type}::{f.title.lower().strip()}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(f)
        return unique

    def _identify_deep_scan_targets(
        self, findings: list[BountyFinding], context: dict
    ) -> list[str]:
        """Identify hosts that deserve deeper scanning based on initial findings.

        Hosts with HIGH or CRITICAL findings are worth deeper investigation
        (e.g., SQLi testing, XSS scanning, parameter fuzzing).
        """
        promising: set[str] = set()
        for f in findings:
            if f.severity not in ("CRITICAL", "HIGH"):
                continue
            # Extract hostname from PoC URL
            if f.poc and f.poc.startswith("http"):
                try:
                    host = urlparse(f.poc).hostname
                    if host:
                        promising.add(host)
                except Exception:
                    pass
            # Extract hostname from title (look for domain-like words)
            for word in f.title.split():
                word = word.strip("()[]<>,;:'\"")
                if "." in word and "/" not in word and len(word) > 4:
                    # Basic domain validation
                    parts = word.split(".")
                    if len(parts) >= 2 and all(p.isalnum() or "-" in p for p in parts):
                        promising.add(word)
        return list(promising)[:5]
