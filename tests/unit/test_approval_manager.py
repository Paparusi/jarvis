"""Tests for ApprovalManager."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from src.trading.approval_manager import ApprovalManager


@dataclass
class MockEntryDecision:
    action: str = "ENTER"
    direction: str = "buy"
    reasoning: str = "Bullish OB + FVG"
    lot_size: float = 0.03
    sl: float = 2338.0
    tp1: float = 2344.0
    tp2: float = 2347.0
    risk_approved: bool = True
    risk_pct: float = 0.8
    risk_usd: float = 24.0
    smc_summary: str = "OB + FVG overlap"


@dataclass
class MockVetoResult:
    approved: bool = True
    reason: str = ""
    rule: str = ""


@pytest.fixture
def mock_persistence():
    p = MagicMock()
    p.save_approval = MagicMock()
    p.get_approval = MagicMock(return_value={
        "id": "apr_test", "status": "pending", "symbol": "XAUUSD",
        "direction": "buy", "lot": 0.03, "price": 2340.5,
        "sl": 2338.0, "tp1": 2344.0, "risk_pct": 0.8,
    })
    p.update_approval = MagicMock()
    p.get_pending_approvals = MagicMock(return_value=[])
    return p


@pytest.fixture
def mock_mt5():
    m = AsyncMock()
    m.place_order = AsyncMock(return_value={"ticket": 12345, "retcode": 10009})
    m.get_tick = AsyncMock(return_value={"bid": 2340.5, "ask": 2340.8})
    return m


@pytest.fixture
def mock_risk_guard():
    rg = MagicMock()
    rg.check_entry = MagicMock(return_value=MockVetoResult(approved=True))
    return rg


@pytest.fixture
def manager(mock_persistence, mock_mt5, mock_risk_guard):
    return ApprovalManager(mock_persistence, mock_mt5, mock_risk_guard)


class TestCreateApproval:
    @pytest.mark.asyncio
    async def test_creates_and_persists(self, manager, mock_persistence):
        decision = MockEntryDecision()
        zone = {"price_high": 2341.0, "price_low": 2340.0, "direction": "buy", "score": 85}
        result = await manager.create_approval(decision, zone, plan_id=1, current_price=2342.0)

        assert result["id"].startswith("apr_")
        assert result["direction"] == "buy"
        assert result["lot"] == 0.03
        assert result["status"] == "pending"
        mock_persistence.save_approval.assert_called_once()

    @pytest.mark.asyncio
    async def test_notifies_callbacks(self, manager):
        cb = AsyncMock()
        manager.on_notify(cb)
        decision = MockEntryDecision()
        zone = {"price_high": 2341.0, "price_low": 2340.0, "score": 85}
        await manager.create_approval(decision, zone, plan_id=1)
        cb.assert_called_once()
        assert cb.call_args[0][0] == "approval"

    @pytest.mark.asyncio
    async def test_pushes_ws_event(self, manager):
        ws_cb = AsyncMock()
        manager.on_ws_event(ws_cb)
        decision = MockEntryDecision()
        zone = {"price_high": 2341.0, "price_low": 2340.0, "score": 85}
        await manager.create_approval(decision, zone, plan_id=1)
        ws_cb.assert_called_once()
        event = ws_cb.call_args[0][0]
        assert event["type"] == "approval"


class TestApprove:
    @pytest.mark.asyncio
    async def test_approve_places_order(self, manager, mock_mt5, mock_persistence):
        result = await manager.approve("apr_test", via="telegram")
        assert result["status"] == "approved"
        assert result["ticket"] == 12345
        mock_mt5.place_order.assert_called_once()
        mock_persistence.update_approval.assert_called()

    @pytest.mark.asyncio
    async def test_approve_risk_veto(self, manager, mock_risk_guard, mock_persistence):
        mock_risk_guard.check_entry.return_value = MockVetoResult(
            approved=False, reason="Daily loss limit", rule="max_daily_loss"
        )
        result = await manager.approve("apr_test")
        assert "error" in result
        assert "RiskGuard" in result["error"]

    @pytest.mark.asyncio
    async def test_approve_not_found(self, manager, mock_persistence):
        mock_persistence.get_approval.return_value = None
        result = await manager.approve("apr_missing")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_approve_already_approved(self, manager, mock_persistence):
        mock_persistence.get_approval.return_value = {"id": "apr_test", "status": "approved"}
        result = await manager.approve("apr_test")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_approve_price_moved(self, manager, mock_mt5, mock_persistence):
        mock_mt5.get_tick.return_value = {"bid": 2350.0, "ask": 2350.3}
        result = await manager.approve("apr_test")
        assert "error" in result
        assert "Price moved" in result["error"]


class TestReject:
    @pytest.mark.asyncio
    async def test_reject(self, manager, mock_persistence):
        result = await manager.reject("apr_test", via="dashboard", reason="Not confident")
        assert result["status"] == "rejected"
        mock_persistence.update_approval.assert_called()

    @pytest.mark.asyncio
    async def test_reject_not_found(self, manager, mock_persistence):
        mock_persistence.get_approval.return_value = None
        result = await manager.reject("apr_missing")
        assert "error" in result


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancel_all(self, manager, mock_persistence):
        mock_persistence.get_pending_approvals.return_value = [
            {"id": "apr_1"}, {"id": "apr_2"},
        ]
        count = await manager.cancel_all("Brain stopped")
        assert count == 2
        assert mock_persistence.update_approval.call_count == 2


class TestCancelZone:
    @pytest.mark.asyncio
    async def test_cancel_zone(self, manager, mock_persistence):
        mock_persistence.get_pending_approvals.return_value = [
            {"id": "apr_1", "zone_id": "z1"},
            {"id": "apr_2", "zone_id": "z2"},
        ]
        await manager.cancel_zone("z1")
        mock_persistence.update_approval.assert_called_once()


class TestRecover:
    def test_recover_loads_pending(self, manager, mock_persistence):
        mock_persistence.get_pending_approvals.return_value = [
            {"id": "apr_1"}, {"id": "apr_2"},
        ]
        result = manager.recover()
        assert len(result) == 2
        assert "apr_1" in manager._pending


class TestDetermineOrderType:
    def test_buy_below_price(self):
        assert ApprovalManager._determine_order_type("buy", 2340, 2345) == "buy_limit"

    def test_buy_above_price(self):
        assert ApprovalManager._determine_order_type("buy", 2340, 2335) == "buy_stop"

    def test_sell_above_price(self):
        assert ApprovalManager._determine_order_type("sell", 2340, 2335) == "sell_limit"

    def test_sell_below_price(self):
        assert ApprovalManager._determine_order_type("sell", 2340, 2345) == "sell_stop"
