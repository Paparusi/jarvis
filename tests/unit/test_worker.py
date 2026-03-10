"""Tests for Worker -- autonomous agent with event loop, memory, and task queue."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.company.worker import Worker, WorkerStatus
from src.gateway.models import AgentResponse, Channel, SessionState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(content: str = "test response") -> AgentResponse:
    return AgentResponse(
        request_id="req1",
        session_id="sess1",
        content=content,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.01,
    )


def _make_worker(**overrides) -> Worker:
    """Create a Worker with mocked dependencies."""
    kwargs = {
        "worker_id": "finance.market_analyst",
        "name": "Market Analyst",
        "department": "finance",
        "role": "Analyze market trends and provide trading signals.",
        "tools": {"web_search", "mt5_get_price"},
        "tool_registry": MagicMock(),
        "assembler": MagicMock(),
        "tracer": MagicMock(),
    }
    kwargs.update(overrides)

    with patch("src.company.worker.AgentLoop"):
        with patch("src.company.worker.TaskStore"):
            with patch("src.company.worker.WorkerMemoryStore"):
                with patch("src.company.worker.MetricsStore"):
                    worker = Worker(**kwargs)

    # Replace stores with fresh mocks so tests can assert on them
    worker._task_store = MagicMock()
    worker._memory_store = MagicMock()
    worker._metrics_store = MagicMock()
    worker._agent_loop = MagicMock()
    worker._agent_loop.run = AsyncMock(return_value=_mock_response())

    return worker


def _make_task(task_id: int = 1, instruction: str = "Analyze XAUUSD") -> dict:
    return {
        "id": task_id,
        "instruction": instruction,
        "session_id": "task_sess_1",
        "context": {"priority": "high"},
        "department": "finance",
        "worker_id": "finance.market_analyst",
        "status": "assigned",
    }


# ===========================================================================
# TestWorkerInit
# ===========================================================================


class TestWorkerInit:
    """Test worker construction and attribute storage."""

    def test_create_worker(self) -> None:
        worker = _make_worker()
        assert worker.worker_id == "finance.market_analyst"
        assert worker.name == "Market Analyst"
        assert worker.department == "finance"
        assert worker.status == WorkerStatus.IDLE

    def test_tools_stored(self) -> None:
        worker = _make_worker(tools={"web_search", "mt5_get_price", "read_file"})
        assert worker.tools == {"web_search", "mt5_get_price", "read_file"}
        assert len(worker.tools) == 3

    def test_default_params(self) -> None:
        worker = _make_worker()
        assert worker._poll_interval == 2.0
        assert worker._task_timeout == 120.0
        assert worker._running is False
        assert worker._task is None


# ===========================================================================
# TestWorkerLifecycle
# ===========================================================================


class TestWorkerLifecycle:
    """Test start/stop lifecycle."""

    def test_start(self) -> None:
        worker = _make_worker()

        loop = asyncio.new_event_loop()
        try:
            # start() needs a running loop for create_task
            async def _do():
                worker.start()
                assert worker.is_running is True
                assert worker._task is not None
                # Clean up
                await worker.stop()

            loop.run_until_complete(_do())
        finally:
            loop.close()

    def test_start_idempotent(self) -> None:
        """Calling start() twice should not create a second task."""
        loop = asyncio.new_event_loop()
        try:
            async def _do():
                worker = _make_worker()
                worker._task_store.claim_task.return_value = None
                worker.start()
                first_task = worker._task
                worker.start()
                assert worker._task is first_task
                await worker.stop()

            loop.run_until_complete(_do())
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_stop(self) -> None:
        worker = _make_worker()
        worker._task_store.claim_task.return_value = None
        worker.start()
        assert worker.is_running is True

        await worker.stop()
        assert worker.is_running is False
        assert worker.status == WorkerStatus.OFFLINE
        assert worker._task is None


# ===========================================================================
# TestWorkerExecute
# ===========================================================================


class TestWorkerExecute:
    """Test task execution (both direct and from queue)."""

    @pytest.mark.asyncio
    async def test_execute_direct_returns_response(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []

        result = await worker.execute_direct("Analyze XAUUSD")

        assert isinstance(result, AgentResponse)
        assert result.content == "test response"

    @pytest.mark.asyncio
    async def test_execute_direct_logs_memory(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []

        await worker.execute_direct("Analyze XAUUSD")

        worker._memory_store.log.assert_called_once()
        call_kwargs = worker._memory_store.log.call_args
        assert call_kwargs.kwargs["worker_id"] == "finance.market_analyst"
        assert call_kwargs.kwargs["memory_type"] == "task_result"
        assert call_kwargs.kwargs["metadata"]["direct"] is True

    @pytest.mark.asyncio
    async def test_execute_direct_updates_metrics(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []

        await worker.execute_direct("Analyze XAUUSD")

        worker._metrics_store.update.assert_called_once()
        call_kwargs = worker._metrics_store.update.call_args.kwargs
        assert call_kwargs["worker_id"] == "finance.market_analyst"
        assert call_kwargs["tokens"] == 150  # 100 in + 50 out
        assert call_kwargs["cost"] == 0.01
        assert call_kwargs["success"] is True

    @pytest.mark.asyncio
    async def test_execute_direct_resets_status_to_idle(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []

        await worker.execute_direct("Analyze XAUUSD")
        assert worker.status == WorkerStatus.IDLE

    @pytest.mark.asyncio
    async def test_execute_direct_failure_raises(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []
        worker._agent_loop.run = AsyncMock(side_effect=RuntimeError("LLM down"))

        with pytest.raises(RuntimeError, match="LLM down"):
            await worker.execute_direct("fail please")

        # Metrics updated with failure
        call_kwargs = worker._metrics_store.update.call_args.kwargs
        assert call_kwargs["success"] is False
        # Status resets to IDLE in finally
        assert worker.status == WorkerStatus.IDLE

    @pytest.mark.asyncio
    async def test_execute_task_from_queue(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []
        task = _make_task()

        await worker._execute_task(task)

        worker._task_store.start_task.assert_called_once_with(1)
        worker._task_store.complete_task.assert_called_once()
        call_args = worker._task_store.complete_task.call_args
        assert call_args[0][0] == 1  # task_id
        assert call_args[0][1] == "test response"  # result content

    @pytest.mark.asyncio
    async def test_execute_task_failure(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []
        worker._agent_loop.run = AsyncMock(side_effect=ValueError("bad input"))
        task = _make_task()

        await worker._execute_task(task)

        worker._task_store.start_task.assert_called_once_with(1)
        worker._task_store.fail_task.assert_called_once()
        fail_args = worker._task_store.fail_task.call_args[0]
        assert fail_args[0] == 1  # task_id
        assert "bad input" in fail_args[1]  # error message
        assert worker.status == WorkerStatus.IDLE

    @pytest.mark.asyncio
    async def test_execute_task_timeout(self) -> None:
        worker = _make_worker(task_timeout=0.01)
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []

        async def _slow_run(**kwargs):
            await asyncio.sleep(10)
            return _mock_response()

        worker._agent_loop.run = _slow_run
        task = _make_task()

        await worker._execute_task(task)

        worker._task_store.fail_task.assert_called_once()
        fail_args = worker._task_store.fail_task.call_args[0]
        assert fail_args[0] == 1
        assert "timed out" in fail_args[1].lower()
        assert worker.status == WorkerStatus.IDLE

    @pytest.mark.asyncio
    async def test_execute_task_uses_session_id_from_task(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []
        task = _make_task()

        await worker._execute_task(task)

        # The agent_loop.run should have been called with a session
        # whose session_id matches the task's session_id
        call_kwargs = worker._agent_loop.run.call_args.kwargs
        session = call_kwargs["session"]
        assert session.session_id == "task_sess_1"
        assert session.channel == Channel.CLI
        assert session.user_id == "worker_finance.market_analyst"


# ===========================================================================
# TestWorkerContext
# ===========================================================================


class TestWorkerContext:
    """Test context building, pattern learning, and status dict."""

    def test_build_context_includes_worker_name(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []

        ctx = worker._build_context({"context": {}})

        assert "Market Analyst" in ctx
        assert "finance.market_analyst" in ctx

    def test_build_context_includes_recent(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = [
            {"content": "XAUUSD broke resistance at 2050", "metadata": {}},
        ]
        worker._memory_store.get_patterns.return_value = []

        ctx = worker._build_context({"context": {}})

        assert "Recent work:" in ctx
        assert "XAUUSD broke resistance" in ctx

    def test_build_context_includes_patterns(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = [
            {"content": "XAUUSD tends to rally on NFP days", "metadata": {}},
        ]

        ctx = worker._build_context({"context": {}})

        assert "Patterns:" in ctx
        assert "NFP days" in ctx

    def test_build_context_includes_task_context(self) -> None:
        worker = _make_worker()
        worker._memory_store.get_recent.return_value = []
        worker._memory_store.get_patterns.return_value = []

        ctx = worker._build_context({"context": {"timeframe": "H4"}})

        assert "timeframe" in ctx
        assert "H4" in ctx

    def test_learn_pattern(self) -> None:
        worker = _make_worker()
        worker._memory_store.log.return_value = 42

        result = worker.learn_pattern(
            "Asian session has lower volatility",
            metadata={"source": "observation"},
        )

        assert result == 42
        worker._memory_store.log.assert_called_once_with(
            worker_id="finance.market_analyst",
            memory_type="pattern",
            content="Asian session has lower volatility",
            metadata={"source": "observation"},
        )

    def test_get_metrics_returns_none_when_empty(self) -> None:
        worker = _make_worker()
        worker._metrics_store.get.return_value = None

        assert worker.get_metrics() is None

    def test_get_metrics_returns_dict(self) -> None:
        worker = _make_worker()
        worker._metrics_store.get.return_value = {
            "worker_id": "finance.market_analyst",
            "tasks_completed": 10,
            "tasks_failed": 2,
            "total_cost": 0.50,
        }

        metrics = worker.get_metrics()
        assert metrics["tasks_completed"] == 10

    def test_get_status_dict(self) -> None:
        worker = _make_worker()
        worker._metrics_store.get.return_value = {
            "tasks_completed": 5,
            "tasks_failed": 1,
            "total_cost": 0.25,
        }

        status = worker.get_status_dict()

        assert status["worker_id"] == "finance.market_analyst"
        assert status["name"] == "Market Analyst"
        assert status["department"] == "finance"
        assert status["status"] == "idle"
        assert status["tools_count"] == 2
        assert status["tasks_completed"] == 5
        assert status["tasks_failed"] == 1
        assert status["total_cost"] == 0.25

    def test_get_status_dict_no_metrics(self) -> None:
        worker = _make_worker()
        worker._metrics_store.get.return_value = None

        status = worker.get_status_dict()

        assert status["tasks_completed"] == 0
        assert status["tasks_failed"] == 0
        assert status["total_cost"] == 0.0


# ===========================================================================
# TestWorkerStatus
# ===========================================================================


class TestWorkerStatus:
    """Test WorkerStatus enum values."""

    def test_status_values(self) -> None:
        assert WorkerStatus.IDLE.value == "idle"
        assert WorkerStatus.BUSY.value == "busy"
        assert WorkerStatus.ERROR.value == "error"
        assert WorkerStatus.OFFLINE.value == "offline"

    def test_status_is_string(self) -> None:
        assert isinstance(WorkerStatus.IDLE, str)
        assert WorkerStatus.IDLE == "idle"
