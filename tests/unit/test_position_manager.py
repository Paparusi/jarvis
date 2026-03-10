"""Tests for PositionManager — trailing SL, partial TP, break-even, emergency exit."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.trading.position_manager import PositionManager, ManagedPosition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pos(
    direction="buy",
    entry=2650.0,
    sl=2640.0,
    tp1=2660.0,
    tp2=2675.0,
    volume=0.03,
    **kwargs,
) -> ManagedPosition:
    return ManagedPosition(
        ticket=12345,
        symbol="XAUUSD",
        direction=direction,
        volume=volume,
        entry_price=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        original_sl=sl,
        remaining_volume=volume,
        **kwargs,
    )


def _make_mock_mt5():
    mt5 = AsyncMock()
    mt5.get_tick.return_value = {"bid": 2655.0, "ask": 2655.5}
    mt5.close_position.return_value = {"success": True}
    mt5.modify_position.return_value = {"success": True}
    mt5.get_rates.return_value = [
        {
            "open": 2650.0 + i,
            "high": 2655.0 + i,
            "low": 2645.0 + i,
            "close": 2652.0 + i,
        }
        for i in range(20)
    ]
    return mt5


# ===========================================================================
# TestBreakEven
# ===========================================================================


class TestBreakEven:
    """Test break-even SL movement."""

    @pytest.mark.asyncio
    async def test_be_triggered_buy(self):
        """Price at 55% toward TP1 -> SL moved to entry+0.01, be_moved=True."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", entry=2650.0, tp1=2660.0, sl=2640.0)

        # 55% toward TP1: entry + 0.55 * (2660-2650) = 2650 + 5.5 = 2655.5
        price = 2655.5

        await pm._check_breakeven(pos, price)

        assert pos.be_moved is True
        assert pos.sl == 2650.01  # entry + 0.01
        mt5.modify_position.assert_awaited_once_with(pos.ticket, sl=2650.01)

    @pytest.mark.asyncio
    async def test_be_not_triggered(self):
        """Price at 40% toward TP1 -> SL unchanged, be_moved=False."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", entry=2650.0, tp1=2660.0, sl=2640.0)

        # 40% toward TP1: entry + 0.4 * 10 = 2654.0
        price = 2654.0

        await pm._check_breakeven(pos, price)

        assert pos.be_moved is False
        assert pos.sl == 2640.0  # unchanged
        mt5.modify_position.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_be_already_moved(self):
        """be_moved=True -> no action even if conditions met."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", entry=2650.0, tp1=2660.0, sl=2650.01)
        pos.be_moved = True

        price = 2658.0  # Well past 50%

        await pm._check_breakeven(pos, price)

        # No modify_position call
        mt5.modify_position.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_be_triggered_sell(self):
        """Sell position, price dropped 55% toward TP1 -> SL moved."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        # Sell: entry=2660, tp1=2650 (below entry), sl=2670 (above entry)
        pos = _make_pos(
            direction="sell", entry=2660.0, tp1=2650.0, tp2=2635.0, sl=2670.0
        )

        # 55% toward TP1: entry - 0.55 * (2660 - 2650) = 2660 - 5.5 = 2654.5
        price = 2654.5

        await pm._check_breakeven(pos, price)

        assert pos.be_moved is True
        assert pos.sl == 2659.99  # entry - 0.01
        mt5.modify_position.assert_awaited_once_with(pos.ticket, sl=2659.99)


# ===========================================================================
# TestTP1
# ===========================================================================


class TestTP1:
    """Test TP1 partial close."""

    @pytest.mark.asyncio
    async def test_tp1_buy(self):
        """Price >= TP1 -> partial close 50%, tp1_hit=True, SL moved to entry."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", entry=2650.0, tp1=2660.0, volume=0.04)

        price = 2660.5  # Above TP1

        await pm._check_tp1(pos, price)

        assert pos.tp1_hit is True
        assert pos.remaining_volume == 0.02  # 0.04 - 0.02
        assert pos.sl == 2650.0  # entry
        mt5.close_position.assert_awaited_once_with(pos.ticket, volume=0.02)
        mt5.modify_position.assert_awaited_once_with(pos.ticket, sl=2650.0)

    @pytest.mark.asyncio
    async def test_tp1_sell(self):
        """Price <= TP1 for sell -> partial close 50%."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = _make_pos(
            direction="sell", entry=2660.0, tp1=2650.0, tp2=2635.0, sl=2670.0, volume=0.06
        )

        price = 2649.5  # Below TP1

        await pm._check_tp1(pos, price)

        assert pos.tp1_hit is True
        assert pos.remaining_volume == 0.03  # 0.06 - 0.03
        mt5.close_position.assert_awaited_once_with(pos.ticket, volume=0.03)

    @pytest.mark.asyncio
    async def test_tp1_already_hit(self):
        """tp1_hit=True -> no action."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", tp1=2660.0)
        pos.tp1_hit = True

        price = 2665.0  # Above TP1

        await pm._check_tp1(pos, price)

        mt5.close_position.assert_not_awaited()
        mt5.modify_position.assert_not_awaited()


# ===========================================================================
# TestTrailing
# ===========================================================================


class TestTrailing:
    """Test ATR-based trailing stop."""

    @pytest.mark.asyncio
    async def test_trail_after_tp1(self):
        """After TP1 hit, trail SL based on ATR."""
        mt5 = _make_mock_mt5()
        # Set tick price high so trailing SL > current SL
        mt5.get_tick.return_value = {"bid": 2670.0, "ask": 2670.5}
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", entry=2650.0, sl=2650.0)
        pos.tp1_hit = True

        await pm._check_trailing(pos)

        # ATR from test candles is small (~5 range per candle)
        # new_sl should be > old sl (2650.0) since price is 2670.5
        mt5.modify_position.assert_awaited_once()
        call_kwargs = mt5.modify_position.call_args
        new_sl = call_kwargs.kwargs.get("sl") or call_kwargs[1].get("sl")
        assert new_sl > 2650.0
        assert pos.sl == new_sl

    @pytest.mark.asyncio
    async def test_trail_only_moves_favorable(self):
        """New SL must be better than current — no downward move for buy."""
        mt5 = _make_mock_mt5()
        # Set price low so computed trailing SL < current SL
        mt5.get_tick.return_value = {"bid": 2645.0, "ask": 2645.5}
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", entry=2650.0, sl=2655.0)
        pos.tp1_hit = True

        await pm._check_trailing(pos)

        # new_sl = 2645.5 - 1.5*ATR would be below 2655.0 → no move
        mt5.modify_position.assert_not_awaited()
        assert pos.sl == 2655.0  # unchanged

    @pytest.mark.asyncio
    async def test_no_trail_before_tp1(self):
        """tp1_hit=False -> no trailing."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", sl=2640.0)
        pos.tp1_hit = False

        await pm._check_trailing(pos)

        mt5.modify_position.assert_not_awaited()
        mt5.get_rates.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trail_sell(self):
        """Sell position trailing moves SL down."""
        mt5 = _make_mock_mt5()
        # Sell: price dropped, SL should trail downward
        mt5.get_tick.return_value = {"bid": 2630.0, "ask": 2630.5}
        pm = PositionManager(mt5)
        pos = _make_pos(
            direction="sell", entry=2660.0, tp1=2650.0, tp2=2635.0, sl=2670.0
        )
        pos.tp1_hit = True

        await pm._check_trailing(pos)

        # new_sl = 2630.0 + 1.5*ATR; should be < 2670.0
        mt5.modify_position.assert_awaited_once()
        call_kwargs = mt5.modify_position.call_args
        new_sl = call_kwargs.kwargs.get("sl") or call_kwargs[1].get("sl")
        assert new_sl < 2670.0
        assert pos.sl == new_sl


# ===========================================================================
# TestEmergency
# ===========================================================================


class TestEmergency:
    """Test emergency close on ChoCH reversal and news flatten."""

    @pytest.mark.asyncio
    async def test_emergency_choch_close(self):
        """ChoCH bearish detected on buy -> position closed."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", entry=2650.0, sl=2640.0)
        await pm.add_position(pos)

        # Mock SMC to return a bearish ChoCH
        mock_smc_result = MagicMock()
        mock_event = MagicMock()
        mock_event.event_type = "ChoCH"
        mock_event.direction = "bearish"
        mock_smc_result.structure_events = [mock_event]

        with patch(
            "src.trading.smc.analyze_smc", return_value=mock_smc_result
        ) as mock_analyze:
            # Provide enough candles
            mt5.get_rates.return_value = [
                {"open": 2650.0, "high": 2655.0, "low": 2645.0, "close": 2652.0, "time": "2026-01-01"}
                for _ in range(20)
            ]
            await pm._check_emergency(pos)

        # Position should be closed and removed
        assert pos not in pm.active_positions
        mt5.close_position.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_emergency_normal(self):
        """No reversal -> position stays."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = _make_pos(direction="buy", entry=2650.0, sl=2640.0)
        await pm.add_position(pos)

        # Mock SMC — only BOS (no ChoCH)
        mock_smc_result = MagicMock()
        mock_event = MagicMock()
        mock_event.event_type = "BOS"
        mock_event.direction = "bullish"
        mock_smc_result.structure_events = [mock_event]

        with patch(
            "src.trading.smc.analyze_smc", return_value=mock_smc_result
        ):
            mt5.get_rates.return_value = [
                {"open": 2650.0, "high": 2655.0, "low": 2645.0, "close": 2652.0, "time": "2026-01-01"}
                for _ in range(20)
            ]
            await pm._check_emergency(pos)

        # Position should still be there
        assert pos in pm.active_positions

    @pytest.mark.asyncio
    async def test_flatten_for_news(self):
        """should_flatten_for_news returns True -> close position."""
        mt5 = _make_mock_mt5()
        risk_guard = MagicMock()
        risk_guard.should_flatten_for_news.return_value = True
        pm = PositionManager(mt5, risk_guard=risk_guard)
        pos = _make_pos(direction="buy", entry=2650.0, sl=2640.0)
        await pm.add_position(pos)

        await pm._check_emergency(pos)

        # Position should be closed due to news
        assert pos not in pm.active_positions
        mt5.close_position.assert_awaited()
        risk_guard.should_flatten_for_news.assert_called_once_with([])


# ===========================================================================
# TestTP2AndCloseAll
# ===========================================================================


class TestTP2AndCloseAll:
    """Test TP2 full close and emergency close all."""

    @pytest.mark.asyncio
    async def test_tp2_full_close(self):
        """Price >= TP2 -> full close, removed from list."""
        mt5 = _make_mock_mt5()
        journal = AsyncMock()
        pm = PositionManager(mt5, journal=journal)
        pos = _make_pos(direction="buy", entry=2650.0, tp2=2675.0, volume=0.03)
        await pm.add_position(pos)

        price = 2676.0  # Above TP2

        await pm._check_tp2(pos, price)

        # Position should be closed and removed
        assert pos not in pm.active_positions
        assert pm.position_count == 0
        mt5.close_position.assert_awaited()
        journal.log_trade.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emergency_close_all(self):
        """Closes all positions, returns count."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)

        pos1 = _make_pos(direction="buy", entry=2650.0)
        pos1.ticket = 11111
        pos2 = _make_pos(direction="sell", entry=2660.0, tp1=2650.0, tp2=2635.0, sl=2670.0)
        pos2.ticket = 22222

        await pm.add_position(pos1)
        await pm.add_position(pos2)
        assert pm.position_count == 2

        count = await pm.emergency_close_all("kill_switch")

        assert count == 2
        assert pm.position_count == 0
        assert mt5.close_position.await_count == 2


# ===========================================================================
# TestAddAndProperties
# ===========================================================================


class TestAddAndProperties:
    """Test add_position, active_positions, position_count."""

    @pytest.mark.asyncio
    async def test_add_position_sets_remaining_volume(self):
        """Add position with remaining_volume=0 -> set to volume."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        pos = ManagedPosition(
            ticket=99999,
            symbol="XAUUSD",
            direction="buy",
            volume=0.05,
            entry_price=2650.0,
            sl=2640.0,
            tp1=2660.0,
            tp2=2675.0,
            original_sl=2640.0,
            remaining_volume=0.0,  # Should be set to volume
        )

        await pm.add_position(pos)

        assert pos.remaining_volume == 0.05
        assert pm.position_count == 1
        assert len(pm.active_positions) == 1


# ===========================================================================
# TestNotifyCallback
# ===========================================================================


class TestNotifyCallback:
    """Test notification callback integration."""

    @pytest.mark.asyncio
    async def test_notify_on_be_move(self):
        """Notification sent when break-even triggered."""
        mt5 = _make_mock_mt5()
        pm = PositionManager(mt5)
        notify = AsyncMock()
        pm.on_notify(notify)

        pos = _make_pos(direction="buy", entry=2650.0, tp1=2660.0, sl=2640.0)
        price = 2656.0  # > 50% toward TP1

        await pm._check_breakeven(pos, price)

        notify.assert_awaited_once()
        msg = notify.call_args[0][0]
        assert "BE moved" in msg
        assert "12345" in msg
