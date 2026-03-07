"""Bug Bounty Hunter Agents — Specialized agents for the scanning pipeline.

Each agent handles one phase of the recon-to-report workflow:
- ReconAgent: subdomain enumeration (subfinder + crt.sh)
- LiveScanAgent: alive host probing (httpx)
- CrawlerAgent: URL/JS/endpoint discovery (katana + gau)
- JSAnalysisAgent: JavaScript secret & endpoint extraction
- VulnScanAgent: vulnerability scanning (nuclei + custom checks)
- AIAnalyzerAgent: AI-powered analysis and deduplication
- ReportAgent: finding report generation
"""

from __future__ import annotations

from src.bounty.agents.base import AgentResult, BaseHunterAgent

# Lazy imports — agents may not all exist yet during development
try:
    from src.bounty.agents.recon import ReconAgent
except ImportError:
    ReconAgent = None  # type: ignore[assignment,misc]

try:
    from src.bounty.agents.livescan import LiveScanAgent
except ImportError:
    LiveScanAgent = None  # type: ignore[assignment,misc]

try:
    from src.bounty.agents.crawler import CrawlerAgent
except ImportError:
    CrawlerAgent = None  # type: ignore[assignment,misc]

try:
    from src.bounty.agents.js_analyzer import JSAnalysisAgent
except ImportError:
    JSAnalysisAgent = None  # type: ignore[assignment,misc]

try:
    from src.bounty.agents.vuln_scanner import VulnScanAgent
except ImportError:
    VulnScanAgent = None  # type: ignore[assignment,misc]

try:
    from src.bounty.agents.ai_analyzer import AIAnalyzerAgent
except ImportError:
    AIAnalyzerAgent = None  # type: ignore[assignment,misc]

try:
    from src.bounty.agents.reporter import ReportAgent
except ImportError:
    ReportAgent = None  # type: ignore[assignment,misc]

__all__ = [
    "BaseHunterAgent",
    "AgentResult",
    "ReconAgent",
    "LiveScanAgent",
    "CrawlerAgent",
    "JSAnalysisAgent",
    "VulnScanAgent",
    "AIAnalyzerAgent",
    "ReportAgent",
]
