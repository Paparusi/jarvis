"""Tests for MT5 Trading tools, client, and analysis."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.base import ToolRegistry
from src.trading.risk import calculate_lot_from_risk


# ═══════════════════════════════════════════════════════════════════════
# MT5 Client Tests
# ═══════════════════════════════════════════════════════════════════════


class TestMT5Client:

    @pytest.mark.asyncio
    async def test_is_available_when_bridge_down(self):
        """Bridge unreachable -> is_available() returns False."""
        from src.trading.mt5_client import MT5Client

        client = MT5Client(base_url="http://127.0.0.1:19999")
        assert await client.is_available() is False
        await client.close()

    @pytest.mark.asyncio
    async def test_get_tick_success(self):
        """Mock successful tick response."""
        from src.trading.mt5_client import MT5Client

        client = MT5Client()
        client._symbol_map = {}  # Clear env-based mapping for test isolation
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"bid": 2650.50, "ask": 2650.80, "time": 1709900000}
        mock_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        client._client = mock_client

        tick = await client.get_tick("XAUUSD")
        assert tick["bid"] == 2650.50
        assert tick["ask"] == 2650.80
        mock_client.get.assert_called_once_with("/tick/XAUUSD")

    @pytest.mark.asyncio
    async def test_get_rates_success(self):
        """Mock successful rates response."""
        from src.trading.mt5_client import MT5Client

        client = MT5Client()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"time": 1709900000, "open": 2650, "high": 2660, "low": 2645, "close": 2655}
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        client._client = mock_client

        rates = await client.get_rates("XAUUSD", "H1", 100)
        assert len(rates) == 1
        assert rates[0]["close"] == 2655

    @pytest.mark.asyncio
    async def test_place_order_payload(self):
        """Verify order payload structure sent to bridge."""
        from src.trading.mt5_client import MT5Client

        client = MT5Client()
        client._symbol_map = {}  # Clear env-based mapping for test isolation
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"retcode": 10009, "deal": 123, "order": 456}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        client._client = mock_client

        result = await client.place_order("XAUUSD", "buy", 0.01, sl=2640, tp=2670)
        assert result["retcode"] == 10009

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["symbol"] == "XAUUSD"
        assert payload["side"] == "buy"
        assert payload["volume"] == 0.01
        assert payload["sl"] == 2640
        assert payload["tp"] == 2670

    @pytest.mark.asyncio
    async def test_close_position(self):
        """Verify close position sends correct ticket."""
        from src.trading.mt5_client import MT5Client

        client = MT5Client()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"retcode": 10009, "deal": 789}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        client._client = mock_client

        result = await client.close_position(12345)
        assert result["retcode"] == 10009
        mock_client.post.assert_called_once()
        assert "/close/12345" in str(mock_client.post.call_args)


# ═══════════════════════════════════════════════════════════════════════
# Trading Tool Handler Tests
# ═══════════════════════════════════════════════════════════════════════


class TestTradingToolHandlers:

    @pytest.mark.asyncio
    async def test_mt5_price_bridge_offline(self):
        """When bridge is down, return helpful error message."""
        from src.tools.trading import mt5_price
        import httpx

        with patch("src.tools.trading._get_client") as mock:
            client = MagicMock()
            client.get_tick = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock.return_value = client

            result = await mt5_price(symbol="XAUUSD")
            assert not result.success
            assert "MT5 Bridge" in result.error

    @pytest.mark.asyncio
    async def test_mt5_price_success(self):
        """Successful price fetch returns formatted output."""
        from src.tools.trading import mt5_price

        with patch("src.tools.trading._get_client") as mock:
            client = MagicMock()
            client.get_tick = AsyncMock(return_value={
                "bid": 2650.50, "ask": 2650.80, "time": 1709900000,
            })
            mock.return_value = client

            result = await mt5_price(symbol="XAUUSD")
            assert result.success
            assert "2650.50" in result.output
            assert "2650.80" in result.output
            assert "Spread" in result.output

    @pytest.mark.asyncio
    async def test_mt5_order_volume_cap(self):
        """Volume > 1.0 lot should be rejected."""
        from src.tools.trading import mt5_order

        result = await mt5_order(symbol="XAUUSD", side="buy", volume="2.0")
        assert not result.success
        assert "1.0" in result.error

    @pytest.mark.asyncio
    async def test_mt5_order_invalid_side(self):
        """Side must be 'buy' or 'sell'."""
        from src.tools.trading import mt5_order

        result = await mt5_order(symbol="XAUUSD", side="hold", volume="0.01")
        assert not result.success
        assert "buy" in result.error or "sell" in result.error

    @pytest.mark.asyncio
    async def test_mt5_order_invalid_volume(self):
        """Non-numeric volume should be rejected."""
        from src.tools.trading import mt5_order

        result = await mt5_order(symbol="XAUUSD", side="buy", volume="abc")
        assert not result.success
        assert "Volume" in result.error

    @pytest.mark.asyncio
    async def test_mt5_order_zero_volume(self):
        """Zero volume should be rejected."""
        from src.tools.trading import mt5_order

        result = await mt5_order(symbol="XAUUSD", side="buy", volume="0")
        assert not result.success

    @pytest.mark.asyncio
    async def test_mt5_close_invalid_ticket(self):
        """Invalid ticket should be rejected."""
        from src.tools.trading import mt5_close

        result = await mt5_close(ticket=0)
        assert not result.success
        assert "ticket" in result.error.lower() or "Ticket" in result.error

    @pytest.mark.asyncio
    async def test_mt5_candles_invalid_timeframe(self):
        """Invalid timeframe should produce error."""
        from src.tools.trading import mt5_candles

        result = await mt5_candles(symbol="XAUUSD", timeframe="INVALID")
        assert not result.success
        assert "Timeframe" in result.error

    @pytest.mark.asyncio
    async def test_mt5_candles_count_clamped(self):
        """Count should be clamped to 1-500."""
        from src.tools.trading import mt5_candles

        with patch("src.tools.trading._get_client") as mock:
            client = MagicMock()
            client.get_rates = AsyncMock(return_value=[
                {"time": 1709900000, "open": 2650, "high": 2660,
                 "low": 2645, "close": 2655, "tick_volume": 100}
            ])
            mock.return_value = client

            result = await mt5_candles(symbol="XAUUSD", timeframe="H1", count=9999)
            assert result.success
            # Verify count was clamped to 500
            call_args = client.get_rates.call_args
            assert call_args[1].get("count", call_args[0][2] if len(call_args[0]) > 2 else 500) <= 500

    @pytest.mark.asyncio
    async def test_mt5_account_success(self):
        """Successful account fetch returns formatted output."""
        from src.tools.trading import mt5_account

        with patch("src.tools.trading._get_client") as mock:
            client = MagicMock()
            client.get_account = AsyncMock(return_value={
                "login": 12345, "name": "Test", "server": "Demo",
                "balance": 10000, "equity": 10500, "margin": 500,
                "margin_free": 10000, "profit": 500, "leverage": 100,
            })
            mock.return_value = client

            result = await mt5_account()
            assert result.success
            assert "10,000.00" in result.output
            assert "Balance" in result.output

    @pytest.mark.asyncio
    async def test_mt5_positions_empty(self):
        """No positions returns appropriate message."""
        from src.tools.trading import mt5_positions

        with patch("src.tools.trading._get_client") as mock:
            client = MagicMock()
            client.get_positions = AsyncMock(return_value=[])
            mock.return_value = client

            result = await mt5_positions()
            assert result.success
            assert "Không có vị thế" in result.output

    @pytest.mark.asyncio
    async def test_mt5_history_success(self):
        """Successful history fetch with deals."""
        from src.tools.trading import mt5_history

        with patch("src.tools.trading._get_client") as mock:
            client = MagicMock()
            client.get_history = AsyncMock(return_value=[
                {"ticket": 1, "symbol": "XAUUSD", "type": 0, "volume": 0.01,
                 "price": 2650, "profit": 50, "time": 1709900000},
                {"ticket": 2, "symbol": "XAUUSD", "type": 1, "volume": 0.01,
                 "price": 2660, "profit": -20, "time": 1709910000},
            ])
            mock.return_value = client

            result = await mt5_history(days=7)
            assert result.success
            assert "2 deals" in result.output
            assert "Win: 1" in result.output
            assert "Loss: 1" in result.output

    @pytest.mark.asyncio
    async def test_market_session_info(self):
        """Market session returns valid session info."""
        from src.tools.trading import market_session_info

        result = await market_session_info()
        assert result.success
        assert "Phiên" in result.output
        assert result.data.get("session") is not None

    @pytest.mark.asyncio
    async def test_trading_calendar(self):
        """Trading calendar returns events."""
        from src.tools.trading import trading_calendar

        result = await trading_calendar()
        assert result.success
        assert "FOMC" in result.output
        assert "NFP" in result.output


# ═══════════════════════════════════════════════════════════════════════
# Tool Definition Tests
# ═══════════════════════════════════════════════════════════════════════


class TestTradingToolDefinitions:

    def test_all_tools_exist(self):
        from src.tools.trading import (
            mt5_price_tool, mt5_candles_tool, mt5_account_tool,
            mt5_positions_tool, mt5_order_tool, mt5_close_tool,
            mt5_history_tool, market_session_tool,
            technical_indicators_tool, trading_calendar_tool,
        )
        assert mt5_price_tool.name == "mt5_price"
        assert mt5_candles_tool.name == "mt5_candles"
        assert mt5_account_tool.name == "mt5_account"
        assert mt5_positions_tool.name == "mt5_positions"
        assert mt5_order_tool.name == "mt5_order"
        assert mt5_close_tool.name == "mt5_close"
        assert mt5_history_tool.name == "mt5_history"
        assert market_session_tool.name == "market_session"
        assert technical_indicators_tool.name == "technical_indicators"
        assert trading_calendar_tool.name == "trading_calendar"

    def test_order_requires_confirmation(self):
        from src.tools.trading import mt5_order_tool, mt5_close_tool

        assert mt5_order_tool.requires_confirmation is True
        assert mt5_close_tool.requires_confirmation is True

    def test_data_tools_no_confirmation(self):
        from src.tools.trading import (
            mt5_price_tool, mt5_candles_tool, mt5_account_tool,
            mt5_positions_tool, mt5_history_tool,
            market_session_tool, technical_indicators_tool,
            trading_calendar_tool,
        )
        for tool in [
            mt5_price_tool, mt5_candles_tool, mt5_account_tool,
            mt5_positions_tool, mt5_history_tool,
            market_session_tool, technical_indicators_tool,
            trading_calendar_tool,
        ]:
            assert tool.requires_confirmation is False, f"{tool.name} should not require confirmation"

    def test_all_schemas_valid(self):
        from src.tools.trading import (
            mt5_price_tool, mt5_candles_tool, mt5_account_tool,
            mt5_positions_tool, mt5_order_tool, mt5_close_tool,
            mt5_history_tool, market_session_tool,
            technical_indicators_tool, trading_calendar_tool,
        )
        all_tools = [
            mt5_price_tool, mt5_candles_tool, mt5_account_tool,
            mt5_positions_tool, mt5_order_tool, mt5_close_tool,
            mt5_history_tool, market_session_tool,
            technical_indicators_tool, trading_calendar_tool,
        ]
        for tool in all_tools:
            schema = tool.to_openai_schema()
            assert schema["type"] == "function", f"{tool.name} schema type wrong"
            assert "name" in schema["function"]
            assert "parameters" in schema["function"]
            assert schema["function"]["parameters"]["type"] == "object"

    def test_tools_register_in_registry(self):
        from src.tools.trading import (
            mt5_price_tool, mt5_candles_tool, mt5_account_tool,
            mt5_positions_tool, mt5_order_tool, mt5_close_tool,
            mt5_history_tool, market_session_tool,
            technical_indicators_tool, trading_calendar_tool,
        )
        registry = ToolRegistry()
        tools = [
            mt5_price_tool, mt5_candles_tool, mt5_account_tool,
            mt5_positions_tool, mt5_order_tool, mt5_close_tool,
            mt5_history_tool, market_session_tool,
            technical_indicators_tool, trading_calendar_tool,
        ]
        for t in tools:
            registry.register(t)
        assert len(registry.get_all()) == 10
        assert registry.get("mt5_price") is not None
        assert registry.get("mt5_order") is not None


# ═══════════════════════════════════════════════════════════════════════
# Analysis Module Tests
# ═══════════════════════════════════════════════════════════════════════


class TestTradingAnalysis:

    def test_market_session_london(self):
        from src.trading.analysis import get_market_session

        result = get_market_session(utc_hour=9)
        assert "session" in result
        assert "London" in result["session"]
        assert result["volatility"] in ("high", "medium-high")

    def test_market_session_dead_zone(self):
        from src.trading.analysis import get_market_session

        result = get_market_session(utc_hour=22)
        assert result["is_dead_zone"] is True or "Sydney" in result["session"]

    def test_market_session_all_hours(self):
        """Every hour should return a valid session."""
        from src.trading.analysis import get_market_session

        for hour in range(24):
            result = get_market_session(utc_hour=hour)
            assert "session" in result
            assert "volatility" in result
            assert "recommendation" in result

    def test_market_session_overlap_london_ny(self):
        from src.trading.analysis import get_market_session

        result = get_market_session(utc_hour=14)
        assert result["is_overlap"] is True
        assert "London+NY" in result["session"]
        assert result["volatility"] == "very-high"

    def test_calc_sma(self):
        from src.trading.analysis import calc_sma

        closes = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = calc_sma(closes, period=3)
        assert len(result) == 5
        assert result[0] is None
        assert result[1] is None
        assert result[2] == pytest.approx(20.0)
        assert result[3] == pytest.approx(30.0)
        assert result[4] == pytest.approx(40.0)

    def test_calc_sma_period_equals_length(self):
        from src.trading.analysis import calc_sma

        closes = [10.0, 20.0, 30.0]
        result = calc_sma(closes, period=3)
        assert result[2] == pytest.approx(20.0)
        assert result[0] is None
        assert result[1] is None

    def test_calc_ema(self):
        from src.trading.analysis import calc_ema

        closes = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = calc_ema(closes, period=3)
        assert len(result) == 5
        assert result[0] is None
        assert result[1] is None
        assert result[2] is not None  # SMA seed
        # EMA should be closer to recent prices
        assert result[4] > result[3]

    def test_calc_rsi_increasing(self):
        """Monotonically increasing prices -> RSI near 100."""
        from src.trading.analysis import calc_rsi

        closes = [float(x) for x in range(1, 30)]
        rsi = calc_rsi(closes, period=14)
        valid = [v for v in rsi if v is not None]
        assert valid[-1] > 90

    def test_calc_rsi_decreasing(self):
        """Monotonically decreasing prices -> RSI near 0."""
        from src.trading.analysis import calc_rsi

        closes = [float(x) for x in range(30, 1, -1)]
        rsi = calc_rsi(closes, period=14)
        valid = [v for v in rsi if v is not None]
        assert valid[-1] < 10

    def test_calc_rsi_mixed(self):
        """Mixed prices -> RSI between 30-70."""
        from src.trading.analysis import calc_rsi

        closes = [100.0 + (i % 5) * 2 - 4 for i in range(30)]
        rsi = calc_rsi(closes, period=14)
        valid = [v for v in rsi if v is not None]
        assert 10 < valid[-1] < 90

    def test_calc_atr(self):
        from src.trading.analysis import calc_atr

        highs = [float(x + 5) for x in range(20)]
        lows = [float(x - 5) for x in range(20)]
        closes = [float(x) for x in range(20)]
        atr = calc_atr(highs, lows, closes, period=14)
        assert len(atr) == 20
        valid = [v for v in atr if v is not None]
        assert len(valid) > 0
        assert all(v > 0 for v in valid)

    def test_calc_bollinger_bands(self):
        from src.trading.analysis import calc_bollinger_bands

        closes = [float(x) for x in range(1, 30)]
        bb = calc_bollinger_bands(closes, period=20, std_dev=2.0)
        assert "upper" in bb
        assert "middle" in bb
        assert "lower" in bb
        assert len(bb["upper"]) == len(closes)
        # Where middle exists, upper > middle > lower
        for i in range(len(closes)):
            if bb["middle"][i] is not None:
                assert bb["upper"][i] > bb["middle"][i]
                assert bb["lower"][i] < bb["middle"][i]

    def test_calc_macd(self):
        from src.trading.analysis import calc_macd

        closes = [float(x) for x in range(1, 50)]
        macd = calc_macd(closes)
        assert "macd_line" in macd
        assert "signal_line" in macd
        assert "histogram" in macd
        assert len(macd["macd_line"]) == len(closes)

    def test_detect_support_resistance(self):
        from src.trading.analysis import detect_support_resistance

        # Create data with clear swing points
        highs = [10, 15, 12, 18, 14, 20, 16, 22, 17, 19] * 3
        lows = [5, 8, 6, 10, 7, 12, 9, 14, 10, 11] * 3
        sr = detect_support_resistance(highs, lows, window=5)
        assert "support" in sr
        assert "resistance" in sr
        assert isinstance(sr["support"], list)
        assert isinstance(sr["resistance"], list)

    def test_determine_trend_uptrend(self):
        from src.trading.analysis import determine_trend, calc_sma

        closes = [float(x) for x in range(1, 60)]
        sma_short = calc_sma(closes, 20)
        sma_long = calc_sma(closes, 50)
        trend = determine_trend(closes, sma_short, sma_long)
        assert trend == "Uptrend"

    def test_determine_trend_downtrend(self):
        from src.trading.analysis import determine_trend, calc_sma

        closes = [float(x) for x in range(60, 1, -1)]
        sma_short = calc_sma(closes, 20)
        sma_long = calc_sma(closes, 50)
        trend = determine_trend(closes, sma_short, sma_long)
        assert trend == "Downtrend"

    def test_analyze_candles_integration(self):
        """Full integration test: analyze_candles produces all fields."""
        from src.trading.analysis import analyze_candles

        candles = [
            {
                "time": f"2026-03-{i:02d}T10:00:00",
                "open": 2650.0 + i,
                "high": 2660.0 + i,
                "low": 2640.0 + i,
                "close": 2655.0 + i,
                "tick_volume": 100 + i,
            }
            for i in range(1, 61)
        ]
        result = analyze_candles(candles)

        # All expected keys present
        assert "current_price" in result
        assert "rsi_14" in result
        assert "sma_20" in result
        assert "atr_14" in result
        assert "trend" in result
        assert "signal_summary" in result
        assert "support" in result
        assert "resistance" in result

        # Values reasonable
        assert result["current_price"] == 2655.0 + 60
        assert result["trend"] == "Uptrend"
        assert result["signal_summary"] in ("Bullish", "Bearish", "Neutral")

    def test_analyze_candles_empty(self):
        from src.trading.analysis import analyze_candles

        result = analyze_candles([])
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════════
# Risk: calculate_lot_from_risk Tests
# ═══════════════════════════════════════════════════════════════════════


class TestCalculateLotFromRisk:
    """Tests for dynamic lot sizing."""

    def test_small_account(self):
        """$1000, 1% risk, 15 pip SL -> min lot."""
        lot = calculate_lot_from_risk(1000.0, 1.0, 2650.0, 2635.0)
        assert lot == 0.01

    def test_large_account(self):
        """$10000, 1% risk, 10 pip SL -> 0.10."""
        lot = calculate_lot_from_risk(10000.0, 1.0, 2650.0, 2640.0)
        assert lot == 0.10

    def test_capped_at_max(self):
        """Very large account -> capped at 0.10."""
        lot = calculate_lot_from_risk(100000.0, 1.0, 2650.0, 2649.0)
        assert lot == 0.10

    def test_zero_equity(self):
        lot = calculate_lot_from_risk(0, 1.0, 2650.0, 2635.0)
        assert lot == 0.01

    def test_same_entry_sl(self):
        lot = calculate_lot_from_risk(1000.0, 1.0, 2650.0, 2650.0)
        assert lot == 0.01

    def test_custom_max_lot(self):
        lot = calculate_lot_from_risk(10000.0, 1.0, 2650.0, 2640.0, max_lot=0.05)
        assert lot == 0.05
