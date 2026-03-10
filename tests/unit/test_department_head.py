"""Tests for DepartmentHead base class."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.company.department_head import DepartmentHead, _DEPARTMENT_PROMPTS
from src.company.departments import Department
from src.gateway.models import AgentResponse, SessionState, Channel


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
