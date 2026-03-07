"""Agent Factory — Create specialized agents dynamically for subtasks.

Each agent is a lightweight wrapper around AgentLoop with a focused system
prompt tailored to its subtask type. Agents are ephemeral — created for a
task, executed, then discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.gateway.models import AgentResponse, SessionState
from src.intelligence.agent_loop import AgentLoop
from src.intelligence.prompt_assembler import PromptAssembler
from src.metacognition.tracer import ReasoningTracer
from src.swarm.decomposer import Subtask, SubtaskType
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("swarm.factory")


# Specialized system prompt prefixes per subtask type
_TYPE_PROMPTS = {
    SubtaskType.RESEARCH: (
        "You are a research agent. Your job is to gather accurate information. "
        "Use web_search and fetch_url tools when needed. "
        "Return well-structured findings with sources."
    ),
    SubtaskType.ANALYSIS: (
        "You are an analysis agent. Your job is to analyze data, code, or information. "
        "Be thorough and identify patterns, issues, and insights. "
        "Provide clear conclusions with supporting evidence."
    ),
    SubtaskType.GENERATION: (
        "You are a generation agent. Your job is to produce content, code, or solutions. "
        "Follow best practices and produce high-quality output. "
        "Be specific and actionable."
    ),
    SubtaskType.EXECUTION: (
        "You are an execution agent. Your job is to carry out specific actions. "
        "Use available tools (run_command, run_python, file operations) to complete the task. "
        "Report results clearly."
    ),
    SubtaskType.SYNTHESIS: (
        "You are a synthesis agent. Your job is to combine multiple pieces of information "
        "into a coherent, unified response. "
        "Resolve any conflicts between sources and present a clear summary."
    ),
}


@dataclass
class SwarmAgent:
    """A lightweight agent created for a specific subtask."""

    id: str
    subtask: Subtask
    session: SessionState
    _agent_loop: AgentLoop | None = field(default=None, repr=False)

    async def execute(
        self,
        context: str = "",
        dependency_results: dict[str, str] | None = None,
    ) -> AgentResponse:
        """Execute the subtask and return the response."""
        if not self._agent_loop:
            raise RuntimeError(f"Agent {self.id} not initialized — use AgentFactory.create()")

        # Build prompt with dependency context
        prompt_parts = [self.subtask.description]

        if dependency_results:
            prompt_parts.append("\n--- Results from previous steps ---")
            for dep_id, result in dependency_results.items():
                # Truncate long results
                truncated = result[:2000] if len(result) > 2000 else result
                prompt_parts.append(f"\n[{dep_id}]: {truncated}")

        if context:
            prompt_parts.append(f"\n--- Additional context ---\n{context}")

        full_prompt = "\n".join(prompt_parts)

        log.info(
            "agent_executing",
            agent_id=self.id,
            subtask_type=self.subtask.type.value,
            prompt_len=len(full_prompt),
        )

        response = await self._agent_loop.run(
            session=self.session,
            user_message=full_prompt,
            use_tools=self.subtask.type in (
                SubtaskType.RESEARCH,
                SubtaskType.EXECUTION,
            ),
        )

        self.subtask.result = response.content
        self.subtask.success = bool(response.content and len(response.content) > 5)

        log.info(
            "agent_completed",
            agent_id=self.id,
            success=self.subtask.success,
            tokens=response.tokens_in + response.tokens_out,
            latency_ms=response.latency_ms,
        )

        return response


class AgentFactory:
    """Create specialized agents for subtasks."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        cloud_model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
    ) -> None:
        self._tool_registry = tool_registry
        self._cloud_model = cloud_model
        self._max_tokens = max_tokens
        self._agents_created = 0

    def create(self, subtask: Subtask, session: SessionState) -> SwarmAgent:
        """Create a new agent tailored to the subtask."""
        self._agents_created += 1
        agent_id = f"agent_{self._agents_created}_{subtask.type.value}"

        # Build specialized system prompt
        type_prompt = _TYPE_PROMPTS.get(subtask.type, _TYPE_PROMPTS[SubtaskType.GENERATION])

        # Create a focused assembler with the type-specific prompt
        assembler = PromptAssembler(
            max_context_tokens=15000,  # Smaller context for focused agents
            skill_summary="",  # Agents don't need full skill context
            tool_registry=self._tool_registry,
            system_prompt_override=type_prompt,
        )

        tracer = ReasoningTracer()

        agent_loop = AgentLoop(
            tool_registry=self._tool_registry,
            assembler=assembler,
            tracer=tracer,
            cloud_model=self._cloud_model,
            max_iterations=3,  # Agents should be focused — fewer iterations
            max_tokens=self._max_tokens,
            temperature=0.5,  # Slightly lower for more focused output
        )

        agent = SwarmAgent(
            id=agent_id,
            subtask=subtask,
            session=session,
            _agent_loop=agent_loop,
        )

        log.info(
            "agent_created",
            agent_id=agent_id,
            subtask_type=subtask.type.value,
            description=subtask.description[:80],
        )

        return agent

    def get_stats(self) -> dict:
        return {"agents_created": self._agents_created}
