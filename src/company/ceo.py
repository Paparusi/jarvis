"""CEO — JARVIS Company orchestrator.

The CEO is the smart router that:
1. Classifies incoming requests by department
2. Handles simple/general requests directly (same as before — all tools)
3. Delegates domain-specific requests to department heads (filtered tools)
"""

from __future__ import annotations

from typing import Any

from src.company.department_head import DepartmentHead
from src.company.departments import (
    Department,
    classify_department,
    get_department_display_name,
)
from src.company.worker_registry import WorkerRegistry
from src.gateway.models import AgentResponse, SessionState
from src.intelligence.agent_loop import AgentLoop
from src.intelligence.prompt_assembler import PromptAssembler
from src.metacognition.tracer import ReasoningTracer
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("company.ceo")


class CEO:
    """JARVIS CEO — classifies and routes requests.

    Simple/general requests are handled directly via the main AgentLoop
    (all tools available, same behavior as before).
    Domain-specific requests are delegated to specialized department heads
    (filtered tools, domain prompts).
    """

    def __init__(
        self,
        agent_loop: AgentLoop,
        tool_registry: ToolRegistry,
        assembler: PromptAssembler,
        tracer: ReasoningTracer,
        cloud_model: str = "claude-sonnet-4-20250514",
        worker_registry: WorkerRegistry | None = None,
    ) -> None:
        self._agent_loop = agent_loop  # Main loop for direct handling
        self._tool_registry = tool_registry
        self._cloud_model = cloud_model
        self._worker_registry = worker_registry

        # Initialize department heads
        self._departments: dict[Department, DepartmentHead] = {}
        for dept in Department:
            if dept == Department.GENERAL:
                continue
            self._departments[dept] = DepartmentHead(
                dept=dept,
                tool_registry=tool_registry,
                assembler=assembler,
                tracer=tracer,
                cloud_model=cloud_model,
            )

        # Wire workers to department heads
        if worker_registry:
            for dept, head in self._departments.items():
                workers = worker_registry.get_department_workers(dept.value)
                head.set_workers(workers)

        log.info(
            "ceo_initialized",
            departments=len(self._departments),
            department_names=[d.value for d in self._departments],
        )

    async def handle(
        self,
        session: SessionState,
        message: str,
        memory_context: str = "",
        skill_context: str = "",
        use_tools: bool = True,
    ) -> AgentResponse:
        """Route request to appropriate handler."""
        dept = classify_department(message)

        log.info("ceo_classify", dept=dept.value, message=message[:80])

        # GENERAL → handle directly (same as before, all tools)
        if dept == Department.GENERAL:
            return await self._handle_direct(
                session, message, memory_context, skill_context, use_tools
            )

        # Domain → delegate to department head
        head = self._departments.get(dept)
        if not head:
            return await self._handle_direct(
                session, message, memory_context, skill_context, use_tools
            )

        log.info("ceo_delegate", dept=dept.value, head=head.display_name)

        result = await head.handle(
            session=session,
            message=message,
            memory_context=memory_context,
            skill_context=skill_context,
        )

        # Tag response with department info
        dept_tag = f"[{get_department_display_name(dept)}]"
        if result.reasoning_trace:
            result.reasoning_trace = f"{dept_tag} {result.reasoning_trace}"
        else:
            result.reasoning_trace = dept_tag

        return result

    async def _handle_direct(
        self,
        session: SessionState,
        message: str,
        memory_context: str,
        skill_context: str,
        use_tools: bool,
    ) -> AgentResponse:
        """Handle request directly via main AgentLoop (all tools)."""
        return await self._agent_loop.run(
            session=session,
            user_message=message,
            memory_context=memory_context,
            skill_context=skill_context,
            use_tools=use_tools,
        )

    def start_workers(self) -> None:
        """Start all worker event loops."""
        if self._worker_registry:
            self._worker_registry.start_all()

    async def stop_workers(self) -> None:
        """Stop all worker event loops."""
        if self._worker_registry:
            await self._worker_registry.stop_all()

    def get_department(self, dept: Department) -> DepartmentHead | None:
        """Get a department head by department enum."""
        return self._departments.get(dept)

    def get_status(self) -> dict[str, Any]:
        """Company status with departments, workers, and cost."""
        status: dict[str, Any] = {
            "departments": {
                dept.value: {
                    "name": head.display_name,
                    "tools": len(head._tool_names),
                    "workers": [w.get_status_dict() for w in head._workers],
                }
                for dept, head in self._departments.items()
            },
            "total_departments": len(self._departments),
            "total_workers": sum(len(h._workers) for h in self._departments.values()),
        }
        if self._worker_registry:
            status["cost"] = self._worker_registry.cost_guard.get_daily_usage()
        return status
