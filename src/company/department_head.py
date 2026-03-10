"""DepartmentHead — Base class for department heads in JARVIS Company.

Each department head:
1. Owns a filtered set of tools (only sees domain-relevant tools)
2. Has a domain-specific system prompt addition
3. Runs requests through AgentLoop with filtered tools
"""

from __future__ import annotations

from src.company.departments import (
    Department,
    get_department_display_name,
    get_department_tools,
)
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

        self._dept_prompt = _DEPARTMENT_PROMPTS.get(dept, "")
        log.info(
            "department_head_created",
            dept=dept.value,
            tools=len(self._tool_names),
        )

    @property
    def display_name(self) -> str:
        return get_department_display_name(self.dept)

    async def handle(
        self,
        session: SessionState,
        message: str,
        memory_context: str = "",
        skill_context: str = "",
    ) -> AgentResponse:
        """Handle a request using department-filtered tools."""
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

        log.info(
            "department_handled",
            dept=self.dept.value,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
        )

        return result
