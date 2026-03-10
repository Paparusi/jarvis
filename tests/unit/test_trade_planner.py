"""Tests for TradePlanner module.

Covers plan creation, data fetching, LLM reasoning, formatting,
and TradePlan dataclass defaults. All MT5 and ClaudeClient calls are mocked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trading.trade_planner import TradePlanner, TradePlan, Scenario


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _mock_mt5():
    """Create a mock MT5Client with sensible defaults."""
    mt5 = AsyncMock()
    mt5.get_tick.return_value = {"bid": 2655.0, "ask": 2655.5}
    mt5.get_account.return_value = {"balance": 10000.0, "equity": 10000.0}
    mt5.get_rates.side_effect = _fake_get_rates
    return mt5


def _fake_get_rates(symbol: str, timeframe: str, count: int) -> list[dict]:
    """Generate simple candle data for any timeframe."""
    candles = []
    base_price = 2650.0
    for i in range(count):
        t = f"2026-03-07T{(i % 24):02d}:00:00"
        candles.append({
            "time": t,
            "open": base_price + i * 0.5,
            "high": base_price + i * 0.5 + 3.0,
            "low": base_price + i * 0.5 - 2.0,
            "close": base_price + i * 0.5 + 1.0,
            "tick_volume": 1000 + i * 10,
        })
    return candles


def _mock_llm_response(content_dict: dict):
    """Create a mock LLMResponse with the given JSON content."""
    from src.intelligence.llm_models import LLMResponse, Choice, Message
    return LLMResponse(
        choices=[Choice(message=Message(content=json.dumps(content_dict)))],
    )


def _valid_llm_result() -> dict:
    """Valid LLM reasoning result."""
    return {
        "bias": "bearish",
        "bias_reasoning": "Structure is bearish with recent ChoCH. Price below PDH.",
        "market_regime": "trending",
        "scenarios": [
            {
                "condition": "If price breaks above 2670",
                "action": "Cancel sell zones, re-scan for buy",
                "new_bias": "bullish",
            }
        ],
        "zone_adjustments": "Top sell zone near VAH looks strong.",
        "invalidation": "Plan invalid if price closes above 2690 on H4.",
        "dxy_context": "DXY trending higher, supports bearish gold bias.",
    }


# ---------------------------------------------------------------------------
# TestCreatePlan
# ---------------------------------------------------------------------------


class TestCreatePlan:
    """Tests for TradePlanner.create_plan()."""

    @pytest.mark.asyncio
    @patch("src.trading.trade_planner.get_claude_client")
    async def test_create_plan_basic(self, mock_get_client):
        """Mock MT5 + ClaudeClient -> returns TradePlan with bias, zones, scenarios."""
        mt5 = _mock_mt5()
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(
            return_value=_mock_llm_response(_valid_llm_result())
        )
        mock_get_client.return_value = mock_client

        planner = TradePlanner(mt5_client=mt5)
        plan = await planner.create_plan(session="London")

        assert isinstance(plan, TradePlan)
        assert plan.session == "London"
        assert plan.bias == "bearish"
        assert plan.bias_reasoning != ""
        assert plan.market_regime == "trending"
        assert plan.active is True
        assert plan.trades_taken == 0
        assert plan.invalidation != ""
        assert len(plan.scenarios) == 1
        assert plan.scenarios[0].condition == "If price breaks above 2670"
        assert plan.scenarios[0].new_bias == "bullish"

    @pytest.mark.asyncio
    @patch("src.trading.trade_planner.get_claude_client")
    async def test_create_plan_llm_failure(self, mock_get_client):
        """LLM raises exception -> plan still created with neutral bias and empty scenarios."""
        mt5 = _mock_mt5()
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(side_effect=Exception("API down"))
        mock_get_client.return_value = mock_client

        planner = TradePlanner(mt5_client=mt5)
        plan = await planner.create_plan(session="New York")

        assert isinstance(plan, TradePlan)
        assert plan.session == "New York"
        assert plan.bias == "neutral"
        assert plan.scenarios == []
        assert plan.active is True


# ---------------------------------------------------------------------------
# TestFetchData
# ---------------------------------------------------------------------------


class TestFetchData:
    """Tests for TradePlanner._fetch_market_data()."""

    @pytest.mark.asyncio
    async def test_fetch_parallel(self):
        """Mock MT5 -> returns dict with all expected keys."""
        mt5 = _mock_mt5()
        planner = TradePlanner(mt5_client=mt5)
        data = await planner._fetch_market_data()

        assert "candles_d1" in data
        assert "candles_h4" in data
        assert "candles_h1" in data
        assert "candles_m15" in data
        assert "tick" in data
        assert "account" in data
        assert "session" in data

        # Verify candle counts
        assert len(data["candles_d1"]) == 60
        assert len(data["candles_h4"]) == 60
        assert len(data["candles_h1"]) == 200
        assert len(data["candles_m15"]) == 100
        assert data["tick"]["bid"] == 2655.0

    @pytest.mark.asyncio
    async def test_fetch_mt5_error(self):
        """MT5 raises -> handles gracefully, returns empty candle lists."""
        mt5 = AsyncMock()
        mt5.get_rates.side_effect = Exception("Connection refused")
        mt5.get_tick.side_effect = Exception("Connection refused")
        mt5.get_account.side_effect = Exception("Connection refused")

        planner = TradePlanner(mt5_client=mt5)
        data = await planner._fetch_market_data()

        assert data["candles_d1"] == []
        assert data["candles_h4"] == []
        assert data["candles_h1"] == []
        assert data["candles_m15"] == []
        assert data["tick"] == {}
        assert data["account"] == {}


# ---------------------------------------------------------------------------
# TestLLMReason
# ---------------------------------------------------------------------------


class TestLLMReason:
    """Tests for TradePlanner._llm_reason()."""

    @pytest.mark.asyncio
    @patch("src.trading.trade_planner.get_claude_client")
    async def test_valid_json(self, mock_get_client):
        """Mock ClaudeClient returns valid JSON -> parsed correctly."""
        result_dict = _valid_llm_result()
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(
            return_value=_mock_llm_response(result_dict)
        )
        mock_get_client.return_value = mock_client

        mt5 = _mock_mt5()
        planner = TradePlanner(mt5_client=mt5)

        quant = {"smc": {}, "volume_profile": {}, "session_levels": {}, "signal_score": {}}
        data = {"session": {}, "tick": {"bid": 2655.0}}
        zones = []

        result = await planner._llm_reason(quant, zones, data)

        assert result["bias"] == "bearish"
        assert result["market_regime"] == "trending"
        assert len(result["scenarios"]) == 1
        assert result["invalidation"] != ""

        # Verify client.complete was called with correct params
        mock_client.complete.assert_called_once()
        call_kwargs = mock_client.complete.call_args
        assert call_kwargs.kwargs["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    @patch("src.trading.trade_planner.get_claude_client")
    async def test_invalid_json(self, mock_get_client):
        """Mock ClaudeClient returns garbage -> returns defaults (neutral bias)."""
        from src.intelligence.llm_models import LLMResponse, Choice, Message
        bad_resp = LLMResponse(
            choices=[Choice(message=Message(content="this is not json at all {{{{"))],
        )
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(return_value=bad_resp)
        mock_get_client.return_value = mock_client

        mt5 = _mock_mt5()
        planner = TradePlanner(mt5_client=mt5)

        quant = {"smc": {}, "volume_profile": {}, "session_levels": {}, "signal_score": {}}
        data = {"session": {}, "tick": {}}
        zones = []

        result = await planner._llm_reason(quant, zones, data)

        assert result["bias"] == "neutral"
        assert result["scenarios"] == []

    @pytest.mark.asyncio
    @patch("src.trading.trade_planner.get_claude_client")
    async def test_llm_exception(self, mock_get_client):
        """ClaudeClient raises -> returns defaults."""
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(
            side_effect=Exception("Rate limit exceeded")
        )
        mock_get_client.return_value = mock_client

        mt5 = _mock_mt5()
        planner = TradePlanner(mt5_client=mt5)

        quant = {"smc": {}, "volume_profile": {}, "session_levels": {}, "signal_score": {}}
        data = {"session": {}, "tick": {}}
        zones = []

        result = await planner._llm_reason(quant, zones, data)

        assert result["bias"] == "neutral"
        assert result["bias_reasoning"] != ""
        assert result["scenarios"] == []


# ---------------------------------------------------------------------------
# TestFormat
# ---------------------------------------------------------------------------


class TestFormat:
    """Tests for format_plan_telegram and format_plan_short."""

    def _make_plan(self) -> TradePlan:
        """Create a sample TradePlan for formatting tests."""
        return TradePlan(
            session="London",
            created_at=datetime(2026, 3, 7, 8, 0, 0, tzinfo=timezone.utc),
            bias="bearish",
            bias_reasoning="Bearish ChoCH on H1. Price below PDH.",
            market_regime="trending",
            weekly_structure="Bearish ChoCH",
            daily_structure="Bearish BOS",
            dxy_context="DXY strong",
            alert_zones=[
                {
                    "zone_id": "zone_1",
                    "direction": "sell",
                    "price_high": 2667.0,
                    "price_low": 2663.0,
                    "confluence_score": 85,
                    "factors": ["H1 OB", "Fib 0.705", "PDH", "VAH"],
                    "sl_price": 2672.0,
                    "tp1_price": 2650.0,
                    "tp2_price": 2638.0,
                    "rr_ratio": 3.2,
                    "reasoning": "Strong confluence",
                    "invalidation": "Zone invalid if price closes above 2680",
                },
            ],
            scenarios=[
                Scenario(
                    condition="Break 2670",
                    action="Cancel sell zones",
                    new_bias="bullish",
                ),
            ],
            risk_budget_pct=2.0,
            max_trades=3,
            invalidation="Plan invalid above 2690",
            trades_taken=0,
        )

    def test_format_telegram(self):
        """Format a plan -> contains session name, bias, zone details."""
        mt5 = _mock_mt5()
        planner = TradePlanner(mt5_client=mt5)
        plan = self._make_plan()

        text = planner.format_plan_telegram(plan)

        assert "London" in text
        assert "07/03/2026" in text
        assert "earish" in text  # Bearish or bearish
        assert "TRENDING" in text
        assert "zone" in text.lower() or "SELL" in text
        assert "2663" in text
        assert "2667" in text
        assert "85" in text
        assert "H1 OB" in text
        assert "SL:" in text
        assert "TP1:" in text
        assert "R:R" in text
        assert "Break 2670" in text
        assert "Budget" in text or "budget" in text

    def test_format_short(self):
        """Short format contains key info."""
        mt5 = _mock_mt5()
        planner = TradePlanner(mt5_client=mt5)
        plan = self._make_plan()

        text = planner.format_plan_short(plan)

        assert "London" in text
        assert "Bearish" in text
        assert "1 zones" in text
        assert "2.0%" in text
        assert "0/3" in text


# ---------------------------------------------------------------------------
# TestPlanDataclass
# ---------------------------------------------------------------------------


class TestPlanDataclass:
    """Tests for TradePlan dataclass defaults."""

    def test_defaults(self):
        """TradePlan defaults are correct (active=True, trades_taken=0)."""
        plan = TradePlan(
            session="London",
            created_at=datetime.now(tz=timezone.utc),
        )

        assert plan.active is True
        assert plan.trades_taken == 0
        assert plan.bias == "neutral"
        assert plan.bias_reasoning == ""
        assert plan.market_regime == ""
        assert plan.weekly_structure == ""
        assert plan.daily_structure == ""
        assert plan.dxy_context == ""
        assert plan.alert_zones == []
        assert plan.scenarios == []
        assert plan.risk_budget_pct == 2.0
        assert plan.max_trades == 3
        assert plan.invalidation == ""
