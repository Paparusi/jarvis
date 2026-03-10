"""EntryConfirmer — LLM re-analysis when price reaches an alert zone.

When the AlertManager fires (price enters a pre-identified zone), the
EntryConfirmer fetches fresh lower-timeframe data, asks an LLM for a
final ENTER / SKIP / WAIT decision, and gates the result through
RiskGuard before returning an actionable EntryDecision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.intelligence.claude_client import get_claude_client
from src.trading.analysis import get_market_session
from src.trading.risk_guard import RiskGuard, VetoResult
from src.trading.smc import analyze_smc, smc_to_dict
from src.utils.logging import get_logger

log = get_logger("trading.entry_confirmer")

_DEFAULT_MODEL = "claude-sonnet-4-20250514"

_SYSTEM_PROMPT = """You are a professional XAUUSD scalp/intraday trader using Smart Money Concepts (SMC).

## CONTEXT
Price has entered a pre-identified confluence zone. You must decide: ENTER, SKIP, or WAIT.
You are given: the zone details, recent M15 candle data (OHLCV), M15 SMC analysis, current price, spread, and open positions.

## DECISION RULES (check in order):

### ENTER conditions (ALL must be true):
1. M15 shows structure shift aligning with zone direction:
   - For SELL zone: M15 ChoCH/BOS bearish, OR bearish engulfing at zone, OR rejection wick > 50% of candle body
   - For BUY zone: M15 ChoCH/BOS bullish, OR bullish engulfing at zone, OR rejection wick > 50% of candle body
2. Current price is INSIDE the zone (between price_low and price_high)
3. Spread < 30 (if spread > 50, always SKIP)
4. No opposing momentum (last 3 M15 candles not strongly against zone direction)

### SKIP conditions (any = SKIP):
- Price has already moved through the zone (zone mitigated)
- M15 structure clearly opposes zone direction (bullish structure at sell zone)
- Last 5 M15 candles show strong momentum AGAINST zone direction
- Zone R:R < 1.0 from current price
- Already have 2+ positions open in same direction

### WAIT conditions:
- Price just touched zone edge but no candle confirmation yet
- M15 candle still forming (not closed) — wait for close
- Structure is unclear/transitioning

## ANALYSIS CHECKLIST (reference exact prices):
1. Where is price relative to zone? (above/inside/below + exact price)
2. What does last M15 candle look like? (body size, wick ratio, close direction)
3. Any M15 ChoCH or BOS in last 5 candles?
4. Is spread acceptable?

## OUTPUT (JSON only):
{"action": "ENTER" | "SKIP" | "WAIT", "reasoning": "Specific analysis with exact prices", "confidence": 0.0-1.0}

CRITICAL: reasoning MUST reference exact prices. Not 'price near zone' but 'price at 2658.50 inside sell zone 2655-2662'.
"""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class EntryDecision:
    """Result of the entry confirmation pipeline."""

    action: str  # "ENTER", "SKIP", "WAIT"
    reasoning: str
    direction: str = ""
    lot_size: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    risk_approved: bool = True
    risk_veto_reason: str = ""


# ---------------------------------------------------------------------------
# EntryConfirmer
# ---------------------------------------------------------------------------


class EntryConfirmer:
    """LLM re-analysis when price reaches an alert zone."""

    def __init__(
        self,
        mt5_client: Any,
        risk_guard: RiskGuard,
        model: str = "",
    ) -> None:
        self._mt5 = mt5_client
        self._risk_guard = risk_guard
        self._model = model or _DEFAULT_MODEL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def confirm(
        self,
        zone: dict,
        current_price: float,
        open_positions: list[dict] | None = None,
        calendar_warnings: list[str] | None = None,
    ) -> EntryDecision:
        """Main entry: fetch LTF data -> LLM decide -> RiskGuard veto.

        Steps:
        1. Fetch M15 candles (20 bars) and current tick
        2. Run M15 SMC analysis (analyze_smc)
        3. Call _llm_decide with zone + ltf_data + price + positions
        4. If LLM says ENTER: run risk_guard.check_entry()
           - If vetoed: return SKIP with risk_veto_reason
           - If approved: return ENTER with zone's SL/TP/lot params
        5. If LLM says SKIP or WAIT: return as-is
        """
        open_positions = open_positions or []
        calendar_warnings = calendar_warnings or []

        _default_sym = __import__("os").environ.get("TRADING_SYMBOL", "XAUUSD")
        symbol = zone.get("symbol", _default_sym)

        try:
            ltf_data = await self._fetch_ltf_data(symbol)
        except Exception as exc:
            log.error("ltf_data_fetch_failed", error=str(exc))
            return EntryDecision(
                action="SKIP",
                reasoning=f"Failed to fetch LTF data: {exc}",
            )

        try:
            llm_result = await self._llm_decide(
                zone=zone,
                ltf_data=ltf_data,
                current_price=current_price,
                open_positions=open_positions,
            )
        except Exception as exc:
            log.error("llm_decide_failed", error=str(exc))
            return EntryDecision(
                action="SKIP",
                reasoning=f"LLM decision error: {exc}",
            )

        action = llm_result.get("action", "SKIP").upper()
        reasoning = llm_result.get("reasoning", "")

        # If not ENTER, return as-is
        if action != "ENTER":
            log.info("entry_skipped", action=action, reasoning=reasoning)
            return EntryDecision(
                action=action,
                reasoning=reasoning,
                direction=zone.get("direction", ""),
            )

        # ENTER path: gate through RiskGuard
        direction = zone.get("direction", "sell")
        lot_size = zone.get("lot_size", 0.01)
        rr_ratio = zone.get("rr_ratio", 1.0)
        sl_price = zone.get("sl_price", 0.0)
        tp1_price = zone.get("tp1_price", 0.0)
        tp2_price = zone.get("tp2_price", 0.0)

        session = ltf_data.get("session", {}).get("session", "London")

        # Calculate approximate risk percentage (simplified)
        risk_pct = 1.0  # default; real calc would use account equity

        veto: VetoResult = self._risk_guard.check_entry(
            risk_pct=risk_pct,
            lot_size=lot_size,
            rr_ratio=rr_ratio,
            direction=direction,
            session=session,
            open_positions=open_positions,
            calendar_warnings=calendar_warnings,
        )

        if not veto.approved:
            log.warning(
                "entry_vetoed",
                reason=veto.reason,
                rule=veto.rule,
            )
            return EntryDecision(
                action="SKIP",
                reasoning=reasoning,
                direction=direction,
                lot_size=lot_size,
                sl=sl_price,
                tp1=tp1_price,
                tp2=tp2_price,
                risk_approved=False,
                risk_veto_reason=veto.reason,
            )

        log.info(
            "entry_confirmed",
            direction=direction,
            lot_size=lot_size,
            sl=sl_price,
            tp1=tp1_price,
            tp2=tp2_price,
        )
        return EntryDecision(
            action="ENTER",
            reasoning=reasoning,
            direction=direction,
            lot_size=lot_size,
            sl=sl_price,
            tp1=tp1_price,
            tp2=tp2_price,
            risk_approved=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_ltf_data(self, symbol: str = "") -> dict:
        """Fetch M15 candles (50 bars), current tick, session info, SMC analysis.

        Returns dict with keys: candles_m15, smc_analysis, spread, session, tick.
        """
        import os
        symbol = symbol or os.environ.get("TRADING_SYMBOL", "XAUUSD")
        candles = await self._mt5.get_rates(symbol, "M15", 50)
        tick = await self._mt5.get_tick(symbol)

        # Run SMC analysis on the M15 candles
        smc_analysis = analyze_smc(candles)
        smc_dict = smc_to_dict(smc_analysis, current_price=tick.get("bid", 0.0))

        session = get_market_session()

        return {
            "candles_m15": candles,
            "smc_analysis": smc_dict,
            "spread": tick.get("spread", 0.0),
            "tick": tick,
            "session": session,
        }

    async def _llm_decide(
        self,
        zone: dict,
        ltf_data: dict,
        current_price: float,
        open_positions: list[dict] | None = None,
    ) -> dict:
        """Call LLM for entry decision.

        Returns dict with keys: action, reasoning, confidence.
        If parsing fails, returns {"action": "SKIP", "reasoning": "LLM parse error"}.
        """
        # Format recent M15 candles for LLM (last 15 OHLCV)
        m15_recent = []
        for c in (ltf_data.get("candles_m15") or [])[-15:]:
            m15_recent.append({
                "time": c.get("time", ""),
                "O": round(c.get("open", 0), 2),
                "H": round(c.get("high", 0), 2),
                "L": round(c.get("low", 0), 2),
                "C": round(c.get("close", 0), 2),
                "V": c.get("tick_volume", c.get("volume", 0)),
            })

        smc = ltf_data.get("smc_analysis", {})
        user_content = json.dumps(
            {
                "zone": {
                    "zone_id": zone.get("zone_id", ""),
                    "direction": zone.get("direction", ""),
                    "price_high": zone.get("price_high", 0),
                    "price_low": zone.get("price_low", 0),
                    "confluence_score": zone.get("confluence_score", 0),
                    "factors": zone.get("factors", []),
                    "sl_price": zone.get("sl_price", 0),
                    "tp1_price": zone.get("tp1_price", 0),
                    "tp2_price": zone.get("tp2_price", 0),
                    "rr_ratio": zone.get("rr_ratio", 0),
                },
                "current_price": current_price,
                "spread": ltf_data.get("spread", 0.0),
                "session": ltf_data.get("session", {}).get("session", ""),
                "recent_m15_candles": m15_recent,
                "m15_smc": {
                    "trend": smc.get("current_trend", ""),
                    "structure_events": smc.get("structure_events", [])[-5:],
                    "order_blocks": smc.get("order_blocks", [])[:3],
                    "fair_value_gaps": smc.get("fair_value_gaps", [])[:3],
                },
                "open_positions_count": len(open_positions or []),
                "open_positions": [
                    {"direction": p.get("direction", ""), "profit": p.get("profit", 0)}
                    for p in (open_positions or [])[:3]
                ],
            },
            indent=2,
            default=str,
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        log.debug("llm_decide_request", model=self._model)

        client = get_claude_client()
        resp = await client.complete(
            messages=messages,
            model=self._model,
            response_format={"type": "json_object"},
        )

        raw = resp.choices[0].message.content or ""

        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.warning("llm_json_parse_error", raw_response=raw[:200])
            return {"action": "SKIP", "reasoning": "LLM parse error"}

        # Normalise action
        action = str(result.get("action", "SKIP")).upper()
        if action not in ("ENTER", "SKIP", "WAIT"):
            action = "SKIP"

        return {
            "action": action,
            "reasoning": result.get("reasoning", ""),
            "confidence": float(result.get("confidence", 0.0)),
        }

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_decision_telegram(self, decision: EntryDecision, zone: dict) -> str:
        """Format for Telegram notification."""
        zone_id = zone.get("zone_id", "unknown")
        direction = zone.get("direction", "").upper()
        price_low = zone.get("price_low", 0)
        price_high = zone.get("price_high", 0)

        lines = [
            "\U0001f514 ENTRY CONFIRMATION",
            f"Zone: {zone_id} ({direction} @ {price_low}-{price_high})",
        ]

        if decision.action == "ENTER":
            lines.append("Decision: \u2705 ENTER")
        elif decision.action == "SKIP":
            lines.append("Decision: \u23ed SKIP")
        elif decision.action == "WAIT":
            lines.append("Decision: \u23f3 WAIT")

        lines.append(f"Reasoning: {decision.reasoning}")

        if decision.action == "ENTER":
            lines.append(
                f"SL: {decision.sl} | TP1: {decision.tp1} | TP2: {decision.tp2}"
            )
            if decision.risk_approved:
                lines.append("Risk: \u2705 Approved")
            else:
                lines.append(f"Risk: \u274c Vetoed - {decision.risk_veto_reason}")
        elif not decision.risk_approved:
            lines.append(f"Risk: \u274c Vetoed - {decision.risk_veto_reason}")

        return "\n".join(lines)
