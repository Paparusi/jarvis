"""Task Decomposer — Break complex requests into executable subtasks.

Uses LLM to analyze user requests and decompose them when:
1. Request contains multiple distinct objectives (AND/OR conjunctions)
2. Request requires sequential dependencies (first X, then Y)
3. Request is complex enough to benefit from parallel execution

Simple requests pass through unchanged (single subtask = original request).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum

import litellm

from src.utils.logging import get_logger

log = get_logger("swarm.decomposer")


class SubtaskType(str, Enum):
    RESEARCH = "research"      # Gather information
    ANALYSIS = "analysis"      # Analyze data/code
    GENERATION = "generation"  # Generate content/code
    EXECUTION = "execution"    # Execute commands/tools
    SYNTHESIS = "synthesis"    # Combine results from other subtasks


@dataclass
class Subtask:
    """A single decomposed subtask."""

    id: str
    description: str
    type: SubtaskType = SubtaskType.GENERATION
    depends_on: list[str] = field(default_factory=list)  # subtask IDs
    tools_hint: list[str] = field(default_factory=list)   # suggested tools
    priority: int = 0  # higher = more important
    result: str = ""
    success: bool = False


@dataclass
class TaskPlan:
    """A decomposed plan with subtasks and execution strategy."""

    original_request: str
    subtasks: list[Subtask]
    parallel_groups: list[list[str]]  # groups of subtask IDs to run in parallel
    requires_synthesis: bool = False   # needs a final merge step


_DECOMPOSE_PROMPT = """\
You are a task decomposition engine. Analyze the user's request and break it into subtasks.

Rules:
- If the request is simple (single objective), return exactly 1 subtask.
- If complex, break into 2-5 subtasks maximum.
- Identify dependencies: which subtasks must complete before others can start.
- Group independent subtasks for parallel execution.
- Each subtask should be self-contained and actionable.

Available tool hints: web_search, fetch_url, run_command, run_python, read_file, write_file, list_directory, http_request, port_scan, dns_lookup, ping, traceroute, git_status, git_diff, docker_ps, docker_exec, base64, hash, jwt_decode, ssl_check, whois, ip_info, subdomain_enum, http_headers, cve_lookup, tech_detect, csv_analyze, json_query, sqlite_query, code_search, ast_analyze

Respond in JSON:
{
  "subtasks": [
    {
      "id": "t1",
      "description": "What to do",
      "type": "research|analysis|generation|execution|synthesis",
      "depends_on": [],
      "tools_hint": ["web_search"],
      "priority": 1
    }
  ],
  "parallel_groups": [["t1", "t2"], ["t3"]],
  "requires_synthesis": true
}
"""


class TaskDecomposer:
    """Decompose complex requests into executable subtask plans."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        complexity_threshold: int = 30,
    ) -> None:
        self._model = model
        self._complexity_threshold = complexity_threshold

    def should_decompose(self, text: str) -> bool:
        """Quick heuristic: is this request complex enough to decompose?"""
        text_lower = text.lower()
        word_count = len(text.split())

        # Check for explicit multi-task signals first
        multi_signals = [
            " và ", " and ", " sau đó ", " then ",
            " đồng thời ", " meanwhile ", " also ",
            " ngoài ra ", " additionally ",
            " tiếp theo ", " next ", " after that ",
            " rồi ", " plus ", " cùng với ",
            "\n-", "\n1.", "\n*", "\n•",
        ]

        # Numbered list patterns (e.g., "1) do X  2) do Y")
        import re
        has_numbered = bool(re.search(r'\d+[.)]\s', text))

        signal_count = sum(1 for s in multi_signals if s in text_lower)

        # Strong signals: multiple conjunctions or numbered lists
        if signal_count >= 2 or has_numbered:
            return True

        # Short messages with a single signal might still be simple
        if word_count < self._complexity_threshold:
            if signal_count == 0:
                return False
            # Single "and" in a short message is likely simple
            if signal_count == 1 and word_count < 15:
                return False

        return True

    async def decompose(self, user_message: str) -> TaskPlan:
        """Decompose a user request into a TaskPlan.

        For simple requests, returns a single-subtask plan (no LLM call).
        For complex requests, uses LLM to analyze and decompose.
        """
        if not self.should_decompose(user_message):
            # Simple request — single subtask, no decomposition needed
            subtask = Subtask(
                id="t1",
                description=user_message,
                type=SubtaskType.GENERATION,
                priority=1,
            )
            return TaskPlan(
                original_request=user_message,
                subtasks=[subtask],
                parallel_groups=[["t1"]],
            )

        # Complex request — use LLM to decompose
        try:
            return await self._llm_decompose(user_message)
        except Exception as e:
            log.warning("decompose_fallback", error=str(e))
            # Fallback: treat as single task
            subtask = Subtask(
                id="t1",
                description=user_message,
                type=SubtaskType.GENERATION,
                priority=1,
            )
            return TaskPlan(
                original_request=user_message,
                subtasks=[subtask],
                parallel_groups=[["t1"]],
            )

    async def _llm_decompose(self, user_message: str) -> TaskPlan:
        """Use LLM to decompose a complex request."""
        response = await litellm.acompletion(
            model=self._model,
            messages=[
                {"role": "system", "content": _DECOMPOSE_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=1024,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or "{}"
        data = json.loads(content)

        subtasks = []
        for st in data.get("subtasks", []):
            try:
                task_type = SubtaskType(st.get("type", "generation"))
            except ValueError:
                task_type = SubtaskType.GENERATION

            subtasks.append(Subtask(
                id=st["id"],
                description=st["description"],
                type=task_type,
                depends_on=st.get("depends_on", []),
                tools_hint=st.get("tools_hint", []),
                priority=st.get("priority", 0),
            ))

        if not subtasks:
            # LLM returned empty — fallback
            subtasks = [Subtask(
                id="t1",
                description=user_message,
                type=SubtaskType.GENERATION,
                priority=1,
            )]

        parallel_groups = data.get("parallel_groups", [[st.id for st in subtasks]])
        requires_synthesis = data.get("requires_synthesis", len(subtasks) > 1)

        log.info(
            "task_decomposed",
            subtasks=len(subtasks),
            parallel_groups=len(parallel_groups),
            requires_synthesis=requires_synthesis,
        )

        return TaskPlan(
            original_request=user_message,
            subtasks=subtasks,
            parallel_groups=parallel_groups,
            requires_synthesis=requires_synthesis,
        )
