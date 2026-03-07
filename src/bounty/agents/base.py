"""Base class and result model for all Bug Bounty Hunter agents.

Every agent in the pipeline inherits from BaseHunterAgent and returns
an AgentResult with structured data, findings, and timing information.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.bounty.models import BountyFinding
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("bounty.agents")


@dataclass
class AgentResult:
    """Standardized result from any hunter agent execution."""

    agent_name: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    findings: list[BountyFinding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    execution_time_ms: int = 0


class BaseHunterAgent:
    """Abstract base for all bug bounty pipeline agents.

    Each agent receives a ToolRegistry for invoking recon/scan tools and
    an optional LLM function for AI-powered analysis steps.
    """

    name: str = "base"

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_fn: Callable[..., Awaitable[str]] | None = None,
    ):
        self.registry = tool_registry
        self.llm = llm_fn  # async fn(prompt: str) -> str

    async def run(self, context: dict) -> AgentResult:
        """Execute this agent's phase. Override in subclasses."""
        raise NotImplementedError

    def _make_result(
        self,
        success: bool = True,
        data: dict[str, Any] | None = None,
        findings: list[BountyFinding] | None = None,
        errors: list[str] | None = None,
        start_time: float = 0.0,
    ) -> AgentResult:
        """Helper to build an AgentResult with elapsed time calculation."""
        return AgentResult(
            agent_name=self.name,
            success=success,
            data=data or {},
            findings=findings or [],
            errors=errors or [],
            execution_time_ms=int((time.time() - start_time) * 1000) if start_time else 0,
        )
