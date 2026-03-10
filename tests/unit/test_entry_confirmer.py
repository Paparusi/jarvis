"""Tests for src.trading.entry_confirmer — EntryConfirmer module."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.trading.entry_confirmer import EntryConfirmer, EntryDecision
from src.trading.risk_guard import RiskGuard, VetoResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_mt5() -> AsyncMock:
    """Create a mock MT5Client with sensible defaults."""
    mt5 = AsyncMock()
    mt5.get_rates.return_value = [
        {
            "time": f"2026-03-07T{h}:00:00",
            "open": 2650.0 + i,
            "high": 2655.0 + i,
            "low": 2645.0 + i,
            "close": 2652.0 + i,
            "tick_volume": 100,
        }
        for i, h in enumerate(range(10, 30))
    ]
    mt5.get_tick.return_value = {"bid": 2665.0, "ask": 2665.5, "spread": 0.5}
    return mt5


_ZONE: dict = {
    "zone_id": "zone_1",
    "direction": "sell",
    "price_high": 2667.0,
    "price_low": 2663.0,
    "confluence_score": 85,
    "sl_price": 2672.0,
    "tp1_price": 2650.0,
    "tp2_price": 2638.0,
    "lot_size": 0.03,
    "rr_ratio": 3.2,
}


def _make_llm_response(action: str, reasoning: str = "", confidence: float = 0.8):
    """Build a mock LLMResponse object."""
    from src.intelligence.llm_models import LLMResponse, Choice, Message
    payload = json.dumps(
        {"action": action, "reasoning": reasoning, "confidence": confidence}
    )
    return LLMResponse(
        choices=[Choice(message=Message(content=payload))],
    )


# ========================================================================
# TestConfirm
# ========================================================================


class TestConfirm:
    """End-to-end tests for EntryConfirmer.confirm()."""

    @pytest.mark.asyncio
    async def test_enter_approved(self):
        """LLM returns ENTER, RiskGuard approves -> ENTER decision."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)
        rg.check_entry.return_value = VetoResult(approved=True)

        confirmer = EntryConfirmer(mt5, rg, model="test-model")

        with patch("src.trading.entry_confirmer.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(
                return_value=_make_llm_response("ENTER", "M15 bearish structure confirmed")
            )
            mock_get_client.return_value = mock_client
            decision = await confirmer.confirm(_ZONE, current_price=2665.0)

        assert decision.action == "ENTER"
        assert decision.risk_approved is True
        assert decision.direction == "sell"
        assert decision.lot_size == 0.03
        assert decision.sl == 2672.0
        assert decision.tp1 == 2650.0
        assert decision.tp2 == 2638.0
        rg.check_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_enter_vetoed(self):
        """LLM returns ENTER, RiskGuard vetoes -> SKIP with veto reason."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)
        rg.check_entry.return_value = VetoResult(
            approved=False,
            reason="Daily loss limit exceeded",
            rule="max_daily_loss",
        )

        confirmer = EntryConfirmer(mt5, rg, model="test-model")

        with patch("src.trading.entry_confirmer.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(
                return_value=_make_llm_response("ENTER", "Looks good")
            )
            mock_get_client.return_value = mock_client
            decision = await confirmer.confirm(_ZONE, current_price=2665.0)

        assert decision.action == "SKIP"
        assert decision.risk_approved is False
        assert "Daily loss" in decision.risk_veto_reason
        assert decision.direction == "sell"

    @pytest.mark.asyncio
    async def test_skip(self):
        """LLM returns SKIP -> SKIP decision, RiskGuard NOT called."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)

        confirmer = EntryConfirmer(mt5, rg, model="test-model")

        with patch("src.trading.entry_confirmer.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(
                return_value=_make_llm_response("SKIP", "OB mitigated")
            )
            mock_get_client.return_value = mock_client
            decision = await confirmer.confirm(_ZONE, current_price=2665.0)

        assert decision.action == "SKIP"
        assert "OB mitigated" in decision.reasoning
        rg.check_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait(self):
        """LLM returns WAIT -> WAIT decision, RiskGuard NOT called."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)

        confirmer = EntryConfirmer(mt5, rg, model="test-model")

        with patch("src.trading.entry_confirmer.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(
                return_value=_make_llm_response("WAIT", "Waiting for candle close")
            )
            mock_get_client.return_value = mock_client
            decision = await confirmer.confirm(_ZONE, current_price=2665.0)

        assert decision.action == "WAIT"
        assert "candle close" in decision.reasoning
        rg.check_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_error_fallback(self):
        """LLM raises exception -> SKIP with error in reasoning."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)

        confirmer = EntryConfirmer(mt5, rg, model="test-model")

        with patch("src.trading.entry_confirmer.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(
                side_effect=RuntimeError("API timeout")
            )
            mock_get_client.return_value = mock_client
            decision = await confirmer.confirm(_ZONE, current_price=2665.0)

        assert decision.action == "SKIP"
        assert "error" in decision.reasoning.lower()
        rg.check_entry.assert_not_called()


# ========================================================================
# TestLLMDecide
# ========================================================================


class TestLLMDecide:
    """Tests for EntryConfirmer._llm_decide()."""

    @pytest.mark.asyncio
    async def test_valid_json_response(self):
        """Mock ClaudeClient returns valid JSON -> parsed correctly."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)
        confirmer = EntryConfirmer(mt5, rg, model="test-model")

        ltf_data = {
            "smc_analysis": {"current_trend": "bearish"},
            "spread": 0.5,
            "session": {"session": "London"},
        }

        with patch("src.trading.entry_confirmer.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(
                return_value=_make_llm_response(
                    "ENTER", "Strong bearish structure", 0.9
                )
            )
            mock_get_client.return_value = mock_client
            result = await confirmer._llm_decide(
                zone=_ZONE, ltf_data=ltf_data, current_price=2665.0
            )

        assert result["action"] == "ENTER"
        assert result["reasoning"] == "Strong bearish structure"
        assert result["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_invalid_json_fallback(self):
        """Mock ClaudeClient returns non-JSON -> returns SKIP with parse error."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)
        confirmer = EntryConfirmer(mt5, rg, model="test-model")

        ltf_data = {
            "smc_analysis": {},
            "spread": 0.5,
            "session": {"session": "London"},
        }

        # Build a response with non-JSON content
        from src.intelligence.llm_models import LLMResponse, Choice, Message
        bad_resp = LLMResponse(
            choices=[Choice(message=Message(content="This is not valid JSON at all"))],
        )

        with patch("src.trading.entry_confirmer.get_claude_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.complete = AsyncMock(return_value=bad_resp)
            mock_get_client.return_value = mock_client
            result = await confirmer._llm_decide(
                zone=_ZONE, ltf_data=ltf_data, current_price=2665.0
            )

        assert result["action"] == "SKIP"
        assert "parse error" in result["reasoning"].lower()


# ========================================================================
# TestFormat
# ========================================================================


class TestFormat:
    """Tests for EntryConfirmer.format_decision_telegram()."""

    def test_format_enter(self):
        """Format ENTER decision includes checkmark and SL/TP."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)
        confirmer = EntryConfirmer(mt5, rg)

        decision = EntryDecision(
            action="ENTER",
            reasoning="M15 bearish ChoCH confirmed",
            direction="sell",
            lot_size=0.03,
            sl=2672.0,
            tp1=2650.0,
            tp2=2638.0,
            risk_approved=True,
        )

        text = confirmer.format_decision_telegram(decision, _ZONE)

        assert "ENTRY CONFIRMATION" in text
        assert "zone_1" in text
        assert "SELL" in text
        assert "\u2705 ENTER" in text
        assert "SL: 2672.0" in text
        assert "TP1: 2650.0" in text
        assert "TP2: 2638.0" in text
        assert "\u2705 Approved" in text

    def test_format_skip(self):
        """Format SKIP decision includes skip emoji."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)
        confirmer = EntryConfirmer(mt5, rg)

        decision = EntryDecision(
            action="SKIP",
            reasoning="OB mitigated, no fresh structure",
            direction="sell",
        )

        text = confirmer.format_decision_telegram(decision, _ZONE)

        assert "ENTRY CONFIRMATION" in text
        assert "\u23ed SKIP" in text
        assert "OB mitigated" in text
        # Should NOT contain SL/TP lines
        assert "SL:" not in text

    def test_format_with_veto(self):
        """Format vetoed decision shows risk warning."""
        mt5 = _make_mock_mt5()
        rg = MagicMock(spec=RiskGuard)
        confirmer = EntryConfirmer(mt5, rg)

        decision = EntryDecision(
            action="SKIP",
            reasoning="Structure looked good but risk check failed",
            direction="sell",
            lot_size=0.03,
            sl=2672.0,
            tp1=2650.0,
            tp2=2638.0,
            risk_approved=False,
            risk_veto_reason="Daily loss limit exceeded",
        )

        text = confirmer.format_decision_telegram(decision, _ZONE)

        assert "ENTRY CONFIRMATION" in text
        assert "\u23ed SKIP" in text
        assert "\u274c Vetoed" in text
        assert "Daily loss limit exceeded" in text
