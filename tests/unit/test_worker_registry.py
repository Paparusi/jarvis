"""Tests for WorkerRegistry and CostGuard."""

import pytest
from unittest.mock import MagicMock, patch

from src.company.worker_registry import CostGuard, WORKER_ROSTER, WorkerRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(**overrides) -> WorkerRegistry:
    """Create a WorkerRegistry with mocked dependencies."""
    kwargs = {
        "tool_registry": MagicMock(),
        "assembler": MagicMock(),
        "tracer": MagicMock(),
    }
    kwargs.update(overrides)

    with patch("src.company.worker_registry.Worker") as MockWorker:
        # Each call to Worker() returns a new MagicMock with the right attrs
        def _side_effect(**kw):
            w = MagicMock()
            w.worker_id = kw["worker_id"]
            w.name = kw["name"]
            w.department = kw["department"]
            w.tools = kw["tools"]
            w.get_status_dict.return_value = {
                "worker_id": kw["worker_id"],
                "name": kw["name"],
                "department": kw["department"],
                "status": "idle",
                "tools_count": len(kw["tools"]),
                "tasks_completed": 0,
                "tasks_failed": 0,
                "total_cost": 0.0,
            }
            return w

        MockWorker.side_effect = _side_effect
        registry = WorkerRegistry(**kwargs)

    return registry


# ===========================================================================
# TestCostGuard
# ===========================================================================


class TestCostGuard:
    """Test CostGuard budget enforcement."""

    def test_initial_can_execute(self) -> None:
        guard = CostGuard()
        assert guard.can_execute("finance.trader") is True

    def test_worker_limit(self) -> None:
        guard = CostGuard()
        guard.log_cost("finance.trader", 3.0)
        assert guard.can_execute("finance.trader") is False

    def test_other_worker_unaffected(self) -> None:
        guard = CostGuard()
        guard.log_cost("finance.trader", 3.0)
        assert guard.can_execute("security.pen_tester") is True

    def test_global_limit(self) -> None:
        guard = CostGuard()
        # Spread cost across 4 workers, each under their $3 limit
        guard.log_cost("finance.trader", 2.5)
        guard.log_cost("finance.market_analyst", 2.5)
        guard.log_cost("security.pen_tester", 2.5)
        guard.log_cost("engineering.developer", 2.5)
        # Global total = 10.0, at the limit
        assert guard.can_execute("research.data_analyst") is False

    def test_daily_usage(self) -> None:
        guard = CostGuard()
        guard.log_cost("finance.trader", 1.5)
        guard.log_cost("security.pen_tester", 0.5)

        usage = guard.get_daily_usage()

        assert usage["total"] == 2.0
        assert usage["limit"] == 10.0
        assert usage["per_worker"]["finance.trader"] == 1.5
        assert usage["per_worker"]["security.pen_tester"] == 0.5

    def test_midnight_reset(self) -> None:
        guard = CostGuard()
        guard.log_cost("finance.trader", 3.0)
        assert guard.can_execute("finance.trader") is False

        # Simulate date change
        guard._last_reset = "2020-01-01"
        assert guard.can_execute("finance.trader") is True
        assert guard.get_daily_usage()["total"] == 0.0


# ===========================================================================
# TestWorkerRegistry
# ===========================================================================


class TestWorkerRegistry:
    """Test WorkerRegistry creation and lookup."""

    def test_creates_all_workers(self) -> None:
        registry = _make_registry()
        assert len(registry.get_all()) == 10

    def test_get_by_id(self) -> None:
        registry = _make_registry()
        worker = registry.get("finance.trader")
        assert worker is not None
        assert worker.worker_id == "finance.trader"

    def test_get_nonexistent(self) -> None:
        registry = _make_registry()
        assert registry.get("nonexistent.worker") is None

    def test_get_department_workers_finance(self) -> None:
        registry = _make_registry()
        finance = registry.get_department_workers("finance")
        assert len(finance) == 3
        ids = {w.worker_id for w in finance}
        assert ids == {"finance.market_analyst", "finance.trader", "finance.crypto_specialist"}

    def test_get_department_workers_security(self) -> None:
        registry = _make_registry()
        security = registry.get_department_workers("security")
        assert len(security) == 2

    def test_get_status(self) -> None:
        registry = _make_registry()
        status = registry.get_status()

        assert status["total_workers"] == 10
        assert "departments" in status
        assert "cost" in status
        assert len(status["departments"]["finance"]) == 3
        assert len(status["departments"]["security"]) == 2
        assert len(status["departments"]["engineering"]) == 2
        assert len(status["departments"]["research"]) == 2
        assert len(status["departments"]["operations"]) == 1

    def test_cost_guard_property(self) -> None:
        registry = _make_registry()
        assert isinstance(registry.cost_guard, CostGuard)


# ===========================================================================
# TestWorkerRoster
# ===========================================================================


class TestWorkerRoster:
    """Test the WORKER_ROSTER data structure."""

    _REQUIRED_FIELDS = {"worker_id", "name", "department", "role", "tools"}

    def test_all_have_required_fields(self) -> None:
        for spec in WORKER_ROSTER:
            missing = self._REQUIRED_FIELDS - set(spec.keys())
            assert not missing, f"{spec.get('worker_id', '?')} missing fields: {missing}"

    def test_worker_ids_unique(self) -> None:
        ids = [spec["worker_id"] for spec in WORKER_ROSTER]
        assert len(ids) == len(set(ids)), f"Duplicate worker_ids: {ids}"

    def test_worker_id_format(self) -> None:
        for spec in WORKER_ROSTER:
            wid = spec["worker_id"]
            parts = wid.split(".")
            assert len(parts) == 2, f"Expected dept.name format, got: {wid}"
            assert parts[0] == spec["department"], (
                f"worker_id prefix '{parts[0]}' does not match department '{spec['department']}'"
            )

    def test_tools_are_sets(self) -> None:
        for spec in WORKER_ROSTER:
            assert isinstance(spec["tools"], set), (
                f"{spec['worker_id']}: tools should be a set, got {type(spec['tools'])}"
            )

    def test_roster_count(self) -> None:
        assert len(WORKER_ROSTER) == 10
