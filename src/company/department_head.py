"""DepartmentHead — Base class for department heads in JARVIS Company.

Each department head:
1. Owns a filtered set of tools (only sees domain-relevant tools)
2. Has a domain-specific system prompt addition
3. Runs requests through AgentLoop with filtered tools
"""

from __future__ import annotations

import asyncio

from src.company.departments import (
    Department,
    get_department_display_name,
    get_department_tools,
)
from src.company.worker import Worker
from src.gateway.event_bus import get_event_bus
from src.gateway.models import AgentResponse, SessionState
from src.intelligence.agent_loop import AgentLoop
from src.intelligence.prompt_assembler import PromptAssembler
from src.metacognition.tracer import ReasoningTracer
from src.tools.base import ToolRegistry
from src.utils.logging import get_logger

log = get_logger("company.department_head")

# Department-specific system prompt additions
_DEPARTMENT_PROMPTS: dict[Department, str] = {
    Department.FINANCE: (
        "Ban la Truong phong Tai chinh cua JARVIS Company. "
        "Chuyen ve phan tich XAUUSD, MT5 trading, quan ly rui ro, "
        "va chien luoc giao dich. Luon dung du lieu real-time tu MT5."
    ),
    Department.SECURITY: (
        "Ban la Truong phong An ninh cua JARVIS Company. "
        "Chuyen ve penetration testing, vulnerability assessment, OSINT, "
        "va threat intelligence. Tuan thu quy trinh co authorization."
    ),
    Department.ENGINEERING: (
        "Ban la Truong phong Ky thuat cua JARVIS Company. "
        "Chuyen ve code review, debugging, Docker, Git, "
        "va phat trien phan mem. Code clean, test-driven."
    ),
    Department.RESEARCH: (
        "Ban la Truong phong Nghien cuu cua JARVIS Company. "
        "Chuyen ve tim kiem thong tin, phan tich tai lieu, "
        "va tong hop bao cao. Luon trich nguon."
    ),
    Department.OPERATIONS: (
        "Ban la Truong phong Van hanh cua JARVIS Company. "
        "Chuyen ve lap lich, nhac nho, xu ly media, "
        "va cac tien ich he thong."
    ),
}


class DepartmentHead:
    """Base department head — runs AgentLoop with filtered tools."""

    def __init__(
        self,
        dept: Department,
        tool_registry: ToolRegistry,
        assembler: PromptAssembler,
        tracer: ReasoningTracer,
        cloud_model: str = "claude-sonnet-4-20250514",
        max_iterations: int = 8,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> None:
        self.dept = dept
        self._tool_registry = tool_registry
        self._tool_names = get_department_tools(dept)

        # Reuse shared assembler and tracer (no need to duplicate)
        self._agent_loop = AgentLoop(
            tool_registry=tool_registry,
            assembler=assembler,
            tracer=tracer,
            cloud_model=cloud_model,
            max_iterations=max_iterations,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        self._workers: list[Worker] = []
        self._dept_prompt = _DEPARTMENT_PROMPTS.get(dept, "")
        log.info(
            "department_head_created",
            dept=dept.value,
            tools=len(self._tool_names),
        )

    @property
    def display_name(self) -> str:
        return get_department_display_name(self.dept)

    def set_workers(self, workers: list[Worker]) -> None:
        """Attach workers to this department head."""
        self._workers = workers
        log.info("dept_workers_set", dept=self.dept.value, workers=len(workers))

    def _select_worker(self, message: str) -> Worker | None:
        """Select best worker for this message based on tool overlap."""
        if not self._workers:
            return None

        msg_lower = message.lower()
        best_worker = None
        best_score = -1

        for w in self._workers:
            if w.status.value != "idle":
                continue
            score = sum(
                1 for tool in w.tools
                if tool.replace("_", " ") in msg_lower or tool in msg_lower
            )
            if score > best_score:
                best_score = score
                best_worker = w

        # If no keyword match, pick first idle worker
        if best_worker is None:
            for w in self._workers:
                if w.status.value == "idle":
                    return w

        return best_worker

    async def handle(
        self,
        session: SessionState,
        message: str,
        memory_context: str = "",
        skill_context: str = "",
    ) -> AgentResponse:
        """Handle request -- delegate to worker if available, else self."""
        worker = self._select_worker(message)

        if worker:
            log.info(
                "dept_delegating_to_worker",
                dept=self.dept.value,
                worker=worker.worker_id,
            )
            try:
                bus = get_event_bus()
                asyncio.create_task(bus.publish(
                    "company_dept_assign",
                    {
                        "department": self.dept.value,
                        "worker_id": worker.worker_id,
                        "worker_name": worker.name,
                        "instruction": message[:100],
                    },
                    source=f"dept_{self.dept.value}",
                ))
            except Exception:
                pass

            result = await worker.execute_direct(
                instruction=message,
                session_id=session.session_id,
            )
            dept_name = get_department_display_name(self.dept)
            result.reasoning_trace = f"[{dept_name} → {worker.name}]"
            return result

        # Fallback: handle directly (existing behavior)
        try:
            bus = get_event_bus()
            asyncio.create_task(bus.publish(
                "company_dept_direct",
                {
                    "department": self.dept.value,
                    "reason": "no_idle_workers",
                },
                source=f"dept_{self.dept.value}",
            ))
        except Exception:
            pass

        dept_context = self._dept_prompt
        if skill_context:
            dept_context = f"{dept_context}\n\n{skill_context}"

        result = await self._agent_loop.run(
            session=session,
            user_message=message,
            memory_context=memory_context,
            skill_context=dept_context,
            use_tools=True,
            tool_filter=self._tool_names,
        )

        return result
