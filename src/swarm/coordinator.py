"""Swarm Coordinator — Orchestrate parallel agent execution.

Execution flow:
1. Receive TaskPlan from Decomposer
2. Create agents via Factory for each subtask
3. Execute in dependency order (parallel where possible)
4. Retry failed agents (max 2 retries, exponential backoff)
5. Aggregate results (dedup + conflict detection)
6. If synthesis needed, run a synthesis agent to merge results
7. Publish events and return final unified response
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from src.gateway.event_bus import get_event_bus
from src.gateway.models import AgentResponse, SessionState
from src.swarm.aggregator import ResultAggregator
from src.swarm.decomposer import TaskDecomposer, TaskPlan, Subtask, SubtaskType
from src.swarm.factory import AgentFactory, SwarmAgent
from src.swarm.message_bus import SwarmMessageBus
from src.utils.logging import get_logger

log = get_logger("swarm.coordinator")


@dataclass
class SwarmResult:
    """Result of a swarm execution."""

    final_content: str
    subtask_results: dict[str, str]  # subtask_id → result content
    total_tokens: int = 0
    total_latency_ms: int = 0
    agents_used: int = 0
    was_decomposed: bool = False
    conflicts: list[dict] = field(default_factory=list)
    duplicates_removed: int = 0
    retries: int = 0


class SwarmCoordinator:
    """Coordinate multi-agent task execution with retry, timeout, and aggregation."""

    def __init__(
        self,
        decomposer: TaskDecomposer,
        factory: AgentFactory,
        max_parallel: int = 3,
        agent_timeout: float = 60.0,  # seconds per agent
        max_retries: int = 2,
    ) -> None:
        self._decomposer = decomposer
        self._factory = factory
        self._max_parallel = max_parallel
        self._agent_timeout = agent_timeout
        self._max_retries = max_retries
        self._aggregator = ResultAggregator()
        self._total_runs = 0
        self._total_agents = 0
        self._total_retries = 0

    async def execute(
        self,
        session: SessionState,
        user_message: str,
        context: str = "",
    ) -> SwarmResult:
        """Decompose, execute agents, and return unified result."""
        start_time = time.monotonic()
        self._total_runs += 1
        bus = get_event_bus()
        message_bus = SwarmMessageBus()

        # Publish swarm start event
        await bus.publish("swarm_started", {
            "user_message": user_message[:200],
            "run_number": self._total_runs,
        }, source="swarm.coordinator")

        # 1. Decompose
        plan = await self._decomposer.decompose(user_message)

        # Single subtask — no swarm needed
        if len(plan.subtasks) == 1:
            agent = self._factory.create(plan.subtasks[0], session)
            response, _ = await self._execute_with_retry(
                agent, context, message_bus=message_bus,
            )
            elapsed = int((time.monotonic() - start_time) * 1000)

            await bus.publish("swarm_completed", {
                "subtasks": 1, "agents": 1, "latency_ms": elapsed,
            }, source="swarm.coordinator")

            return SwarmResult(
                final_content=response.content,
                subtask_results={plan.subtasks[0].id: response.content},
                total_tokens=response.tokens_in + response.tokens_out,
                total_latency_ms=elapsed,
                agents_used=1,
                was_decomposed=False,
            )

        # 2. Multi-subtask: execute in dependency order
        log.info(
            "swarm_starting",
            subtasks=len(plan.subtasks),
            parallel_groups=len(plan.parallel_groups),
        )

        results: dict[str, str] = {}
        total_tokens = 0
        total_retries = 0
        critical_failure = False

        for group_idx, group in enumerate(plan.parallel_groups):
            if critical_failure:
                # Skip remaining groups if critical subtask failed
                log.warning("swarm_early_termination", skipped_group=group_idx)
                break

            group_subtasks = [st for st in plan.subtasks if st.id in group]
            if not group_subtasks:
                continue

            # Check dependencies are satisfied
            for st in group_subtasks:
                for dep_id in st.depends_on:
                    if dep_id not in results:
                        log.warning(
                            "dependency_not_met",
                            subtask=st.id,
                            dependency=dep_id,
                        )

            # Execute group in parallel
            group_results, retries = await self._execute_group(
                group_subtasks, session, context, results, message_bus,
            )
            total_retries += retries

            for st_id, response in group_results.items():
                results[st_id] = response.content
                total_tokens += response.tokens_in + response.tokens_out

                # Check for critical failure
                st = next((s for s in group_subtasks if s.id == st_id), None)
                if st and st.priority >= 5 and not st.success:
                    # High-priority subtask failed — check if later groups depend on it
                    dependents = [
                        s for s in plan.subtasks
                        if st_id in s.depends_on and s.id not in results
                    ]
                    if dependents:
                        critical_failure = True
                        log.warning("critical_subtask_failed",
                                    subtask=st_id, dependents=len(dependents))

        self._total_agents += len(plan.subtasks)
        self._total_retries += total_retries

        # 3. Aggregate results
        aggregated = self._aggregator.aggregate(plan, results)

        # 4. Synthesis if needed
        if plan.requires_synthesis and len(results) > 1:
            final_content = await self._synthesize(
                plan, results, session, context, aggregated.conflicts,
            )
            self._total_agents += 1
        else:
            final_content = aggregated.content

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        agents_used = len(plan.subtasks) + (1 if plan.requires_synthesis else 0)

        log.info(
            "swarm_completed",
            subtasks=len(plan.subtasks),
            agents=agents_used,
            total_tokens=total_tokens,
            latency_ms=elapsed_ms,
            conflicts=len(aggregated.conflicts),
            dedup=aggregated.duplicates_removed,
            retries=total_retries,
        )

        # Publish completion event
        await bus.publish("swarm_completed", {
            "subtasks": len(plan.subtasks),
            "agents": agents_used,
            "total_tokens": total_tokens,
            "latency_ms": elapsed_ms,
            "conflicts": len(aggregated.conflicts),
            "retries": total_retries,
        }, source="swarm.coordinator")

        return SwarmResult(
            final_content=final_content,
            subtask_results=results,
            total_tokens=total_tokens,
            total_latency_ms=elapsed_ms,
            agents_used=agents_used,
            was_decomposed=True,
            conflicts=aggregated.conflicts,
            duplicates_removed=aggregated.duplicates_removed,
            retries=total_retries,
        )

    async def _execute_group(
        self,
        subtasks: list[Subtask],
        session: SessionState,
        context: str,
        previous_results: dict[str, str],
        message_bus: SwarmMessageBus,
    ) -> tuple[dict[str, AgentResponse], int]:
        """Execute a group of independent subtasks in parallel.

        Returns (results, retry_count).
        """
        semaphore = asyncio.Semaphore(self._max_parallel)
        total_retries = 0

        async def _run_one(subtask: Subtask) -> tuple[str, AgentResponse, int]:
            async with semaphore:
                agent = self._factory.create(subtask, session)
                dep_results = {
                    dep_id: previous_results[dep_id]
                    for dep_id in subtask.depends_on
                    if dep_id in previous_results
                }

                # Share peer findings via message bus context
                peer_findings = message_bus.get_findings(exclude_agent=agent.id)
                extra_context = context
                if peer_findings:
                    findings_text = "\n".join(f"• {f}" for f in peer_findings[-5:])
                    extra_context += f"\n\n--- Findings from peer agents ---\n{findings_text}"

                response, retries = await self._execute_with_retry(
                    agent, extra_context,
                    dependency_results=dep_results if dep_results else None,
                    message_bus=message_bus,
                )

                # Publish finding to message bus
                if subtask.success and response.content:
                    summary = response.content[:300]
                    message_bus.publish(
                        agent.id, "finding",
                        f"[{subtask.type.value}] {summary}",
                    )

                return subtask.id, response, retries

        tasks = [_run_one(st) for st in subtasks]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        results: dict[str, AgentResponse] = {}
        for i, item in enumerate(completed):
            if isinstance(item, Exception):
                st_id = subtasks[i].id if i < len(subtasks) else f"unknown_{i}"
                log.error("subtask_failed", subtask_id=st_id, error=str(item))
                results[st_id] = AgentResponse(
                    request_id="", session_id="",
                    content=f"[Subtask {st_id} failed: {str(item)[:200]}]",
                    latency_ms=0,
                )
                continue
            st_id, response, retries = item
            results[st_id] = response
            total_retries += retries

        return results, total_retries

    async def _execute_with_retry(
        self,
        agent: SwarmAgent,
        context: str,
        dependency_results: dict[str, str] | None = None,
        message_bus: SwarmMessageBus | None = None,
    ) -> tuple[AgentResponse, int]:
        """Execute an agent with timeout and retry logic.

        Returns (response, retry_count).
        """
        retries = 0

        for attempt in range(1 + self._max_retries):
            try:
                response = await asyncio.wait_for(
                    agent.execute(
                        context=context,
                        dependency_results=dependency_results,
                    ),
                    timeout=self._agent_timeout,
                )

                # Check if result is meaningful
                if response.content and len(response.content.strip()) > 5:
                    return response, retries

                # Empty/tiny response — retry if we have attempts left
                if attempt < self._max_retries:
                    retries += 1
                    backoff = 1.0 * (2 ** attempt)  # 1s, 2s
                    log.warning("agent_empty_response_retry",
                                agent=agent.id, attempt=attempt + 1,
                                backoff=backoff)
                    await asyncio.sleep(backoff)
                    # Re-create agent for fresh state
                    agent = self._factory.create(agent.subtask, agent.session)
                    continue

                return response, retries

            except asyncio.TimeoutError:
                retries += 1
                log.warning("agent_timeout",
                            agent=agent.id, timeout=self._agent_timeout,
                            attempt=attempt + 1)

                if attempt < self._max_retries:
                    backoff = 1.0 * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    agent = self._factory.create(agent.subtask, agent.session)
                    continue

                # Final timeout — return error response
                return AgentResponse(
                    request_id="", session_id="",
                    content=f"[Agent {agent.id} timed out after {self._agent_timeout}s]",
                    latency_ms=int(self._agent_timeout * 1000),
                ), retries

            except Exception as e:
                retries += 1
                log.error("agent_execution_error",
                          agent=agent.id, error=str(e), attempt=attempt + 1)

                if attempt < self._max_retries:
                    backoff = 1.0 * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    agent = self._factory.create(agent.subtask, agent.session)
                    continue

                return AgentResponse(
                    request_id="", session_id="",
                    content=f"[Agent {agent.id} error: {str(e)[:200]}]",
                    latency_ms=0,
                ), retries

        # Should not reach here
        return AgentResponse(
            request_id="", session_id="",
            content="[Agent exhausted retries]",
            latency_ms=0,
        ), retries

    async def _synthesize(
        self,
        plan: TaskPlan,
        results: dict[str, str],
        session: SessionState,
        context: str,
        conflicts: list[dict] | None = None,
    ) -> str:
        """Run a synthesis agent to merge all subtask results."""
        conflict_note = ""
        if conflicts:
            conflict_note = (
                "\n\nNote: Some results contain conflicting information. "
                "Please reconcile these conflicts and present a balanced view:\n"
                + "\n".join(f"- {c['description']}" for c in conflicts)
            )

        synthesis_task = Subtask(
            id="synthesis",
            description=(
                f"Combine the following results into a single, coherent response "
                f"to the original request: '{plan.original_request}'"
                f"{conflict_note}"
            ),
            type=SubtaskType.SYNTHESIS,
            priority=0,
        )

        agent = self._factory.create(synthesis_task, session)
        response, _ = await self._execute_with_retry(
            agent, context, dependency_results=results,
        )

        return response.content

    def get_stats(self) -> dict:
        return {
            "total_runs": self._total_runs,
            "total_agents": self._total_agents,
            "total_retries": self._total_retries,
            "max_parallel": self._max_parallel,
            "agent_timeout": self._agent_timeout,
        }
