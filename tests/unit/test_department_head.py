"""Tests for DepartmentHead base class."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.company.department_head import DepartmentHead, _DEPARTMENT_PROMPTS
from src.company.departments import Department
from src.company.worker import WorkerStatus
from src.gateway.models import AgentResponse, Channel, SessionState


class TestDepartmentHeadInit:
    @pytest.fixture
    def mock_deps(self):
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        assembler = MagicMock()
        tracer = MagicMock()
        return registry, assembler, tracer

    def test_create_finance_head(self, mock_deps):
        registry, assembler, tracer = mock_deps
        head = DepartmentHead(Department.FINANCE, registry, assembler, tracer)
        assert head.dept == Department.FINANCE
        assert "mt5_price" in head._tool_names

    def test_create_security_head(self, mock_deps):
        registry, assembler, tracer = mock_deps
        head = DepartmentHead(Department.SECURITY, registry, assembler, tracer)
        assert "nuclei_scan" in head._tool_names

    def test_create_engineering_head(self, mock_deps):
        registry, assembler, tracer = mock_deps
        head = DepartmentHead(Department.ENGINEERING, registry, assembler, tracer)
        assert "git_status" in head._tool_names

    def test_display_name_finance(self, mock_deps):
        registry, assembler, tracer = mock_deps
        head = DepartmentHead(Department.FINANCE, registry, assembler, tracer)
        assert "Tai chinh" in head.display_name

    def test_display_name_security(self, mock_deps):
        registry, assembler, tracer = mock_deps
        head = DepartmentHead(Department.SECURITY, registry, assembler, tracer)
        assert "An ninh" in head.display_name

    def test_all_departments_have_prompts(self):
        for dept in Department:
            if dept != Department.GENERAL:
                assert dept in _DEPARTMENT_PROMPTS


class TestDepartmentHeadHandle:
    @pytest.fixture
    def session(self):
        return SessionState(channel=Channel.CLI, user_id="test")

    @pytest.fixture
    def head(self):
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        assembler = MagicMock()
        tracer = MagicMock()
        head = DepartmentHead(Department.RESEARCH, registry, assembler, tracer)
        # Mock the agent loop
        mock_response = AgentResponse(
            request_id="test", session_id="test",
            content="research result", model_used="test-model",
        )
        head._agent_loop.run = AsyncMock(return_value=mock_response)
        return head

    @pytest.mark.asyncio
    async def test_handle_returns_response(self, head, session):
        result = await head.handle(session, "search for AI news")
        assert result.content == "research result"

    @pytest.mark.asyncio
    async def test_handle_calls_agent_loop(self, head, session):
        await head.handle(session, "search for AI news")
        head._agent_loop.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_passes_tool_filter(self, head, session):
        await head.handle(session, "search something")
        call_kwargs = head._agent_loop.run.call_args.kwargs
        assert "tool_filter" in call_kwargs
        # Research department should have web tools
        assert "deep_search" in call_kwargs["tool_filter"]

    @pytest.mark.asyncio
    async def test_handle_includes_dept_prompt(self, head, session):
        await head.handle(session, "search info")
        call_kwargs = head._agent_loop.run.call_args.kwargs
        assert "Nghien cuu" in call_kwargs["skill_context"]

    @pytest.mark.asyncio
    async def test_handle_appends_skill_context(self, head, session):
        await head.handle(session, "search info", skill_context="extra context")
        call_kwargs = head._agent_loop.run.call_args.kwargs
        assert "extra context" in call_kwargs["skill_context"]
        assert "Nghien cuu" in call_kwargs["skill_context"]

    @pytest.mark.asyncio
    async def test_handle_passes_memory_context(self, head, session):
        await head.handle(session, "search info", memory_context="some memory")
        call_kwargs = head._agent_loop.run.call_args.kwargs
        assert call_kwargs["memory_context"] == "some memory"


class TestDepartmentHeadWithWorkers:
    @pytest.fixture
    def mock_deps(self):
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        assembler = MagicMock()
        tracer = MagicMock()
        return registry, assembler, tracer

    @pytest.fixture
    def head_with_workers(self, mock_deps):
        registry, assembler, tracer = mock_deps
        head = DepartmentHead(Department.FINANCE, registry, assembler, tracer)

        worker1 = MagicMock()
        worker1.worker_id = "finance.analyst"
        worker1.name = "Market Analyst"
        worker1.status = WorkerStatus.IDLE
        worker1.tools = {"mt5_candles", "mt5_analyze"}
        worker1.execute_direct = AsyncMock(return_value=AgentResponse(
            request_id="r1", session_id="s1", content="Analysis done",
        ))

        worker2 = MagicMock()
        worker2.worker_id = "finance.trader"
        worker2.name = "Trader"
        worker2.status = WorkerStatus.IDLE
        worker2.tools = {"mt5_order", "mt5_close"}
        worker2.execute_direct = AsyncMock(return_value=AgentResponse(
            request_id="r2", session_id="s1", content="Order placed",
        ))

        head.set_workers([worker1, worker2])
        return head, worker1, worker2

    @pytest.mark.asyncio
    async def test_delegates_to_worker(self, head_with_workers):
        head, w1, w2 = head_with_workers
        session = SessionState(channel=Channel.CLI, session_id="s1")
        result = await head.handle(session, "test message")
        assert result.content in ("Analysis done", "Order placed")

    @pytest.mark.asyncio
    async def test_worker_response_tagged(self, head_with_workers):
        head, w1, w2 = head_with_workers
        session = SessionState(channel=Channel.CLI, session_id="s1")
        result = await head.handle(session, "test")
        assert result.reasoning_trace is not None
        assert "\u2192" in result.reasoning_trace

    def test_set_workers(self, head_with_workers):
        head, _, _ = head_with_workers
        assert len(head._workers) == 2

    @pytest.mark.asyncio
    async def test_fallback_when_no_workers(self, mock_deps):
        registry, assembler, tracer = mock_deps
        head = DepartmentHead(Department.FINANCE, registry, assembler, tracer)
        head._agent_loop.run = AsyncMock(return_value=AgentResponse(
            request_id="r1", session_id="s1", content="Direct response",
        ))
        session = SessionState(channel=Channel.CLI, session_id="s1")
        result = await head.handle(session, "test")
        assert result.content == "Direct response"

    @pytest.mark.asyncio
    async def test_fallback_when_all_workers_busy(self, head_with_workers):
        head, w1, w2 = head_with_workers
        w1.status = WorkerStatus.BUSY
        w2.status = WorkerStatus.BUSY
        head._agent_loop.run = AsyncMock(return_value=AgentResponse(
            request_id="r1", session_id="s1", content="Head handles",
        ))
        session = SessionState(channel=Channel.CLI, session_id="s1")
        result = await head.handle(session, "test")
        assert result.content == "Head handles"
