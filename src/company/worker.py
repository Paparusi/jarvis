"""Worker -- autonomous agent with own event loop, memory, and task queue."""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any
from uuid import uuid4

from src.company.worker_store import MetricsStore, TaskStore, WorkerMemoryStore
from src.gateway.event_bus import get_event_bus
from src.gateway.models import AgentResponse, Channel, SessionState
from src.intelligence.agent_loop import AgentLoop
from src.intelligence.prompt_assembler import PromptAssembler
from src.metacognition.tracer import ReasoningTracer
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("company.worker")


class WorkerStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class Worker:
    """Autonomous agent with its own event loop, memory, and task queue.

    Each Worker:
    - Polls for tasks from the shared TaskStore
    - Executes tasks via its own AgentLoop (with tool_filter)
    - Logs results to WorkerMemoryStore
    - Tracks metrics via MetricsStore
    - Can also execute direct (synchronous) requests from DepartmentHead
    """

    def __init__(
        self,
        worker_id: str,
        name: str,
        department: str,
        role: str,
        tools: set[str],
        tool_registry: ToolRegistry,
        assembler: PromptAssembler,
        tracer: ReasoningTracer,
        cloud_model: str = "claude-sonnet-4-20250514",
        poll_interval: float = 2.0,
        max_iterations: int = 8,
        task_timeout: float = 120.0,
    ) -> None:
        self.worker_id = worker_id
        self.name = name
        self.department = department
        self.role = role
        self.tools = tools

        self.status = WorkerStatus.IDLE
        self._poll_interval = poll_interval
        self._task_timeout = task_timeout
        self._running = False
        self._task: asyncio.Task[None] | None = None

        self._agent_loop = AgentLoop(
            tool_registry=tool_registry,
            assembler=assembler,
            tracer=tracer,
            cloud_model=cloud_model,
            max_iterations=max_iterations,
        )

        self._task_store = TaskStore()
        self._memory_store = WorkerMemoryStore()
        self._metrics_store = MetricsStore()

        log.info(
            "worker_created",
            worker_id=worker_id,
            name=name,
            department=department,
            tools=len(tools),
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the worker's background loop is active."""
        return self._running

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name=f"worker_{self.worker_id}")
        log.info("worker_started", worker_id=self.worker_id)

    async def stop(self) -> None:
        """Stop the background loop and set status to OFFLINE."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.status = WorkerStatus.OFFLINE
        log.info("worker_stopped", worker_id=self.worker_id)

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Poll for tasks and execute them."""
        while self._running:
            task = self._task_store.claim_task(self.worker_id, self.department)
            if task is not None:
                await self._execute_task(task)
            else:
                await asyncio.sleep(self._poll_interval)

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def _execute_task(self, task: dict[str, Any]) -> None:
        """Execute a single task from the queue.

        On success: complete_task, log memory, update metrics.
        On timeout or exception: fail_task, update metrics.
        Always resets status to IDLE in the finally block.
        """
        task_id: int = task["id"]
        instruction: str = task["instruction"]
        session_id = task.get("session_id") or str(uuid4())

        self.status = WorkerStatus.BUSY
        self._task_store.start_task(task_id)
        start_time = time.monotonic()

        try:
            bus = get_event_bus()
            asyncio.create_task(bus.publish(
                "company_worker_busy",
                {
                    "worker_id": self.worker_id,
                    "worker_name": self.name,
                    "department": self.department,
                },
                source=self.worker_id,
            ))
        except Exception:
            pass

        try:
            session = SessionState(
                session_id=session_id,
                channel=Channel.CLI,
                user_id=f"worker_{self.worker_id}",
            )

            context = self._build_context(task)
            response = await asyncio.wait_for(
                self._agent_loop.run(
                    session=session,
                    user_message=instruction,
                    skill_context=context,
                    tool_filter=self.tools,
                ),
                timeout=self._task_timeout,
            )

            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._task_store.complete_task(task_id, response.content)
            self._memory_store.log(
                worker_id=self.worker_id,
                memory_type="task_result",
                content=response.content[:500],
                metadata={"task_id": task_id, "latency_ms": elapsed_ms},
            )
            self._metrics_store.update(
                worker_id=self.worker_id,
                tokens=response.tokens_in + response.tokens_out,
                cost=response.cost_usd,
                response_ms=elapsed_ms,
                success=True,
            )
            log.info(
                "task_executed",
                worker_id=self.worker_id,
                task_id=task_id,
                latency_ms=elapsed_ms,
            )

            try:
                bus = get_event_bus()
                asyncio.create_task(bus.publish(
                    "company_worker_done",
                    {
                        "worker_id": self.worker_id,
                        "worker_name": self.name,
                        "department": self.department,
                        "duration_ms": elapsed_ms,
                        "result_preview": response.content[:100],
                    },
                    source=self.worker_id,
                ))
            except Exception:
                pass

        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._task_store.fail_task(task_id, "Task timed out")
            self._metrics_store.update(
                worker_id=self.worker_id,
                response_ms=elapsed_ms,
                success=False,
            )
            self.status = WorkerStatus.ERROR
            log.warning(
                "task_timeout",
                worker_id=self.worker_id,
                task_id=task_id,
                timeout=self._task_timeout,
            )
            try:
                bus = get_event_bus()
                asyncio.create_task(bus.publish(
                    "company_worker_fail",
                    {
                        "worker_id": self.worker_id,
                        "worker_name": self.name,
                        "department": self.department,
                        "error": "Task timed out",
                    },
                    source=self.worker_id,
                ))
            except Exception:
                pass

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._task_store.fail_task(task_id, str(exc))
            self._metrics_store.update(
                worker_id=self.worker_id,
                response_ms=elapsed_ms,
                success=False,
            )
            self.status = WorkerStatus.ERROR
            log.error(
                "task_error",
                worker_id=self.worker_id,
                task_id=task_id,
                error=str(exc),
            )
            try:
                bus = get_event_bus()
                asyncio.create_task(bus.publish(
                    "company_worker_fail",
                    {
                        "worker_id": self.worker_id,
                        "worker_name": self.name,
                        "department": self.department,
                        "error": str(exc)[:200],
                    },
                    source=self.worker_id,
                ))
            except Exception:
                pass

        finally:
            self.status = WorkerStatus.IDLE

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def _build_context(self, task: dict[str, Any]) -> str:
        """Build context string from role, recent work, and learned patterns."""
        parts: list[str] = [
            f"Worker: {self.name} ({self.worker_id})",
            f"Role: {self.role}",
        ]

        # Recent task results
        recent = self._memory_store.get_recent(
            self.worker_id, memory_type="task_result", limit=5,
        )
        if recent:
            parts.append("\nRecent work:")
            for mem in recent:
                parts.append(f"- {mem['content'][:120]}")

        # Learned patterns
        patterns = self._memory_store.get_patterns(self.worker_id, limit=5)
        if patterns:
            parts.append("\nPatterns:")
            for p in patterns:
                parts.append(f"- {p['content'][:120]}")

        # Task-specific context (from task.context dict)
        task_context = task.get("context")
        if isinstance(task_context, dict) and task_context:
            parts.append(f"\nTask context: {task_context}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def learn_pattern(self, pattern: str, metadata: dict[str, Any] | None = None) -> int:
        """Store a learned pattern in worker memory. Returns the memory row id."""
        return self._memory_store.log(
            worker_id=self.worker_id,
            memory_type="pattern",
            content=pattern,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Metrics / status
    # ------------------------------------------------------------------

    def get_metrics(self) -> dict[str, Any] | None:
        """Get this worker's metrics from the store."""
        return self._metrics_store.get(self.worker_id)

    def get_status_dict(self) -> dict[str, Any]:
        """Return a status summary suitable for API/UI display."""
        metrics = self._metrics_store.get(self.worker_id)
        return {
            "worker_id": self.worker_id,
            "name": self.name,
            "department": self.department,
            "status": self.status.value,
            "tools_count": len(self.tools),
            "tasks_completed": metrics["tasks_completed"] if metrics else 0,
            "tasks_failed": metrics["tasks_failed"] if metrics else 0,
            "total_cost": metrics["total_cost"] if metrics else 0.0,
        }

    # ------------------------------------------------------------------
    # Direct execution (bypass queue)
    # ------------------------------------------------------------------

    async def execute_direct(
        self,
        instruction: str,
        session_id: str | None = None,
    ) -> AgentResponse:
        """Execute an instruction directly, bypassing the task queue.

        Used by DepartmentHead for synchronous calls that need immediate results.
        """
        sid = session_id or str(uuid4())
        self.status = WorkerStatus.BUSY
        start_time = time.monotonic()

        try:
            bus = get_event_bus()
            asyncio.create_task(bus.publish(
                "company_worker_busy",
                {
                    "worker_id": self.worker_id,
                    "worker_name": self.name,
                    "department": self.department,
                },
                source=self.worker_id,
            ))
        except Exception:
            pass

        try:
            session = SessionState(
                session_id=sid,
                channel=Channel.CLI,
                user_id=f"worker_{self.worker_id}",
            )

            context = self._build_context({"context": {}})
            response = await asyncio.wait_for(
                self._agent_loop.run(
                    session=session,
                    user_message=instruction,
                    skill_context=context,
                    tool_filter=self.tools,
                ),
                timeout=self._task_timeout,
            )

            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._memory_store.log(
                worker_id=self.worker_id,
                memory_type="task_result",
                content=response.content[:500],
                metadata={"direct": True, "latency_ms": elapsed_ms},
            )
            self._metrics_store.update(
                worker_id=self.worker_id,
                tokens=response.tokens_in + response.tokens_out,
                cost=response.cost_usd,
                response_ms=elapsed_ms,
                success=True,
            )

            try:
                bus = get_event_bus()
                asyncio.create_task(bus.publish(
                    "company_worker_done",
                    {
                        "worker_id": self.worker_id,
                        "worker_name": self.name,
                        "department": self.department,
                        "duration_ms": elapsed_ms,
                        "result_preview": response.content[:100],
                    },
                    source=self.worker_id,
                ))
            except Exception:
                pass

            return response

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._metrics_store.update(
                worker_id=self.worker_id,
                response_ms=elapsed_ms,
                success=False,
            )
            self.status = WorkerStatus.ERROR
            log.error(
                "direct_execution_error",
                worker_id=self.worker_id,
                error=str(exc),
            )
            try:
                bus = get_event_bus()
                asyncio.create_task(bus.publish(
                    "company_worker_fail",
                    {
                        "worker_id": self.worker_id,
                        "worker_name": self.name,
                        "department": self.department,
                        "error": str(exc)[:200],
                    },
                    source=self.worker_id,
                ))
            except Exception:
                pass

            raise

        finally:
            self.status = WorkerStatus.IDLE
