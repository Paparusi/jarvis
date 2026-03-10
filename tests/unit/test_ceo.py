"""Tests for CEO orchestrator."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.company.ceo import CEO
from src.company.departments import Department
from src.gateway.models import AgentResponse, SessionState, Channel


class TestCEOInit:
    @pytest.fixture
    def mock_deps(self):
        loop = MagicMock()
        loop.run = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="direct response", model_used="test-model",
        ))
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        assembler = MagicMock()
        tracer = MagicMock()
        return loop, registry, assembler, tracer

    def test_creates_all_departments(self, mock_deps):
        loop, registry, assembler, tracer = mock_deps
        ceo = CEO(loop, registry, assembler, tracer)
        assert len(ceo._departments) == 5  # All except GENERAL

    def test_has_finance(self, mock_deps):
        loop, registry, assembler, tracer = mock_deps
        ceo = CEO(loop, registry, assembler, tracer)
        assert Department.FINANCE in ceo._departments

    def test_has_security(self, mock_deps):
        loop, registry, assembler, tracer = mock_deps
        ceo = CEO(loop, registry, assembler, tracer)
        assert Department.SECURITY in ceo._departments

    def test_has_engineering(self, mock_deps):
        loop, registry, assembler, tracer = mock_deps
        ceo = CEO(loop, registry, assembler, tracer)
        assert Department.ENGINEERING in ceo._departments

    def test_has_research(self, mock_deps):
        loop, registry, assembler, tracer = mock_deps
        ceo = CEO(loop, registry, assembler, tracer)
        assert Department.RESEARCH in ceo._departments

    def test_has_operations(self, mock_deps):
        loop, registry, assembler, tracer = mock_deps
        ceo = CEO(loop, registry, assembler, tracer)
        assert Department.OPERATIONS in ceo._departments

    def test_no_general_department(self, mock_deps):
        loop, registry, assembler, tracer = mock_deps
        ceo = CEO(loop, registry, assembler, tracer)
        assert Department.GENERAL not in ceo._departments


class TestCEOHandle:
    @pytest.fixture
    def session(self):
        return SessionState(channel=Channel.CLI, user_id="test")

    @pytest.fixture
    def ceo(self):
        loop = MagicMock()
        loop.run = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="direct response", model_used="test-model",
        ))
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        assembler = MagicMock()
        tracer = MagicMock()
        return CEO(loop, registry, assembler, tracer)

    @pytest.mark.asyncio
    async def test_general_handled_directly(self, ceo, session):
        result = await ceo.handle(session, "xin chao")
        assert result.content == "direct response"
        ceo._agent_loop.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_finance_delegated(self, ceo, session):
        finance_head = ceo._departments[Department.FINANCE]
        finance_head.handle = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="XAUUSD analysis", model_used="test-model",
        ))

        result = await ceo.handle(session, "phan tich XAUUSD")
        assert result.content == "XAUUSD analysis"
        finance_head.handle.assert_called_once()
        ceo._agent_loop.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_security_delegated(self, ceo, session):
        security_head = ceo._departments[Department.SECURITY]
        security_head.handle = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="scan results", model_used="test-model",
        ))

        result = await ceo.handle(session, "scan vuln target.com")
        assert result.content == "scan results"

    @pytest.mark.asyncio
    async def test_response_tagged_with_department(self, ceo, session):
        finance_head = ceo._departments[Department.FINANCE]
        finance_head.handle = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="trading info", model_used="test-model",
        ))

        result = await ceo.handle(session, "giá vàng XAUUSD hôm nay")
        assert "Tai chinh" in result.reasoning_trace

    @pytest.mark.asyncio
    async def test_general_no_department_tag(self, ceo, session):
        result = await ceo.handle(session, "hello there")
        assert result.reasoning_trace is None


class TestCEOStatus:
    @pytest.fixture
    def ceo(self):
        loop = MagicMock()
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        assembler = MagicMock()
        tracer = MagicMock()
        return CEO(loop, registry, assembler, tracer)

    def test_get_status_departments_count(self, ceo):
        status = ceo.get_status()
        assert status["total_departments"] == 5

    def test_get_status_has_finance(self, ceo):
        status = ceo.get_status()
        assert "finance" in status["departments"]

    def test_get_department_returns_head(self, ceo):
        head = ceo.get_department(Department.FINANCE)
        assert head is not None
        assert head.dept == Department.FINANCE

    def test_get_department_general_returns_none(self, ceo):
        assert ceo.get_department(Department.GENERAL) is None


class TestCEOWithWorkers:
    @pytest.fixture
    def mock_deps(self):
        loop = MagicMock()
        loop.run = AsyncMock(return_value=AgentResponse(
            request_id="test", session_id="test",
            content="direct response", model_used="test-model",
        ))
        registry = MagicMock()
        registry.get_all.return_value = []
        registry.get_schemas.return_value = []
        registry.get_filtered_schemas.return_value = []
        assembler = MagicMock()
        tracer = MagicMock()
        return loop, registry, assembler, tracer

    @pytest.fixture
    def ceo_with_workers(self, mock_deps):
        loop, registry, assembler, tracer = mock_deps
        worker_reg = MagicMock()
        worker_reg.get_department_workers.return_value = [MagicMock(), MagicMock()]
        worker_reg.cost_guard.get_daily_usage.return_value = {
            "total": 0.5,
            "limit": 10.0,
            "per_worker": {},
        }
        worker_reg.start_all = MagicMock()
        worker_reg.stop_all = AsyncMock()
        ceo = CEO(loop, registry, assembler, tracer, worker_registry=worker_reg)
        return ceo

    def test_workers_wired_to_heads(self, ceo_with_workers):
        ceo = ceo_with_workers
        for head in ceo._departments.values():
            assert len(head._workers) == 2

    def test_status_includes_workers(self, ceo_with_workers):
        status = ceo_with_workers.get_status()
        assert status["total_workers"] > 0
        assert "cost" in status

    def test_status_includes_cost(self, ceo_with_workers):
        status = ceo_with_workers.get_status()
        assert status["cost"]["total"] == 0.5
