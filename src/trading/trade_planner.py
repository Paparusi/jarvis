"""TradePlanner — Creates structured trade plans at session open.

Combines quantitative analysis (SMC, volume profile, session levels,
signal scoring, confluence) with LLM reasoning to produce a complete
TradePlan with bias, alert zones, scenarios, and risk budget.

Designed for XAUUSD (gold) on London and New York sessions.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.intelligence.claude_client import get_claude_client
from src.utils.logging import get_logger

log = get_logger("trading.trade_planner")

_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_SYMBOL = __import__("os").environ.get("TRADING_SYMBOL", "XAUUSD")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    """What-if scenario for the trading plan."""

    condition: str      # "If price breaks above 2665..."
    action: str         # "Cancel sell zones, re-scan for buy"
    new_bias: str       # "bullish"


@dataclass
class TradePlan:
    """Complete trade plan for a single session."""

    session: str                    # "London" / "NY"
    created_at: datetime
    bias: str = "neutral"           # "bullish" / "bearish" / "neutral"
    bias_reasoning: str = ""
    market_regime: str = ""         # "trending" / "ranging" / "volatile" / "quiet"
    weekly_structure: str = ""
    daily_structure: str = ""
    dxy_context: str = ""
    key_levels: dict = field(default_factory=dict)  # {"resistance": [...], "support": [...]}
    alert_zones: list[dict] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    risk_budget_pct: float = 2.0
    max_trades: int = 3
    invalidation: str = ""
    active: bool = True
    trades_taken: int = 0


# ---------------------------------------------------------------------------
# TradePlanner
# ---------------------------------------------------------------------------


class TradePlanner:
    """Creates TradePlan at session open via deep analysis + LLM reasoning."""

    def __init__(self, mt5_client, risk_guard=None, model: str = "") -> None:
        self._mt5 = mt5_client
        self._risk_guard = risk_guard
        self._model = model or _DEFAULT_MODEL

    async def create_plan(self, session: str = "London") -> TradePlan:
        """Main entry: fetch data -> quantitative analysis -> LLM reasoning -> TradePlan.

        Steps:
        1. _fetch_market_data() -- parallel fetch D1, H4, H1, M15 candles + tick + account + session info
        2. _run_quantitative_analysis(data) -- run SMC, volume profile, session levels, signal scoring, confluence
        3. _llm_reason(quant, zones, data) -- LLM produces bias, reasoning, scenarios, zone adjustments
        4. Build TradePlan with all fields populated
        """
        log.info("creating_trade_plan", session=session)

        # Step 1: Fetch market data
        data = await self._fetch_market_data()

        # Step 2: Run quantitative analysis
        quant = await self._run_quantitative_analysis(data)

        # Step 3: LLM reasoning
        zones = quant.get("zones", [])
        llm_result = await self._llm_reason(quant, zones, data)

        # Step 4: Build the TradePlan
        bias = llm_result.get("bias", "neutral")
        if bias not in ("bullish", "bearish", "neutral"):
            bias = "neutral"

        market_regime = llm_result.get("market_regime", "")
        if market_regime not in ("trending", "ranging", "volatile", "quiet"):
            market_regime = ""

        # Build scenarios from LLM output
        scenarios: list[Scenario] = []
        for sc in llm_result.get("scenarios", []):
            if isinstance(sc, dict):
                scenarios.append(Scenario(
                    condition=sc.get("condition", ""),
                    action=sc.get("action", ""),
                    new_bias=sc.get("new_bias", "neutral"),
                ))

        # Build alert zones from confluence zones
        alert_zones: list[dict] = []
        for z in zones:
            zone_dict = {
                "zone_id": z.get("zone_id", ""),
                "direction": z.get("direction", ""),
                "price_high": z.get("price_high", 0.0),
                "price_low": z.get("price_low", 0.0),
                "confluence_score": z.get("confluence_score", 0),
                "factors": z.get("factors", []),
                "sl_price": z.get("sl_price", 0.0),
                "tp1_price": z.get("tp1_price", 0.0),
                "tp2_price": z.get("tp2_price", 0.0),
                "rr_ratio": z.get("rr_ratio", 0.0),
                "reasoning": z.get("reasoning", ""),
                "invalidation": z.get("invalidation", ""),
            }
            alert_zones.append(zone_dict)

        # Extract structure info from quant
        smc_data = quant.get("smc", {})
        weekly_structure = ""
        daily_structure = ""

        structure_events = smc_data.get("structure_events", [])
        if structure_events:
            last_event = structure_events[-1]
            evt_type = last_event.get("event_type", "")
            evt_dir = last_event.get("direction", "")
            daily_structure = f"{evt_dir.title()} {evt_type}" if evt_type else ""

        # Risk budget from risk guard config
        risk_budget = 2.0
        max_trades = 3
        if self._risk_guard is not None:
            cfg = getattr(self._risk_guard, "config", None)
            if cfg is not None:
                risk_budget = getattr(cfg, "max_correlated_exposure_pct", 2.0)
                max_trades = getattr(cfg, "max_daily_trades", 3)

        # Extract key levels from LLM
        key_levels = llm_result.get("key_levels", {})
        if not isinstance(key_levels, dict):
            key_levels = {}

        plan = TradePlan(
            session=session,
            created_at=datetime.now(tz=timezone.utc),
            bias=bias,
            bias_reasoning=llm_result.get("bias_reasoning", ""),
            market_regime=market_regime,
            weekly_structure=weekly_structure,
            daily_structure=daily_structure,
            dxy_context=llm_result.get("dxy_context", ""),
            key_levels=key_levels,
            alert_zones=alert_zones,
            scenarios=scenarios,
            risk_budget_pct=risk_budget,
            max_trades=max_trades,
            invalidation=llm_result.get("invalidation", ""),
            active=True,
            trades_taken=0,
        )

        log.info(
            "trade_plan_created",
            session=session,
            bias=plan.bias,
            zones=len(plan.alert_zones),
            scenarios=len(plan.scenarios),
        )

        return plan

    async def _fetch_market_data(self, symbol: str = _SYMBOL) -> dict:
        """Parallel fetch via asyncio.gather.

        Fetches D1, H4, H1, M15 candles + current tick + account info + market session.
        Returns dict with keys: candles_d1, candles_h4, candles_h1, candles_m15,
        tick, account, session.
        """
        async def _safe_get_rates(tf: str, count: int) -> list:
            try:
                return await self._mt5.get_rates(symbol, tf, count)
            except Exception as e:
                log.warning("fetch_rates_error", timeframe=tf, error=str(e))
                return []

        async def _safe_get_tick() -> dict:
            try:
                return await self._mt5.get_tick(symbol)
            except Exception as e:
                log.warning("fetch_tick_error", error=str(e))
                return {}

        async def _safe_get_account() -> dict:
            try:
                return await self._mt5.get_account()
            except Exception as e:
                log.warning("fetch_account_error", error=str(e))
                return {}

        async def _safe_get_session() -> dict:
            try:
                from src.trading.analysis import get_market_session
                return get_market_session()
            except Exception as e:
                log.warning("fetch_session_error", error=str(e))
                return {}

        results = await asyncio.gather(
            _safe_get_rates("D1", 60),
            _safe_get_rates("H4", 60),
            _safe_get_rates("H1", 200),
            _safe_get_rates("M15", 100),
            _safe_get_tick(),
            _safe_get_account(),
            _safe_get_session(),
        )

        data = {
            "candles_d1": results[0],
            "candles_h4": results[1],
            "candles_h1": results[2],
            "candles_m15": results[3],
            "tick": results[4],
            "account": results[5],
            "session": results[6],
        }

        log.debug(
            "market_data_fetched",
            d1=len(data["candles_d1"]),
            h4=len(data["candles_h4"]),
            h1=len(data["candles_h1"]),
            m15=len(data["candles_m15"]),
            has_tick=bool(data["tick"]),
        )

        return data

    async def _run_quantitative_analysis(self, data: dict) -> dict:
        """Run all quantitative analyses.

        1. H1 SMC analysis (analyze_smc on H1 candles)
        2. Volume Profile (build_volume_profile on H1 candles)
        3. Session Levels (calculate_all_levels on D1 + H1, with swing from SMC)
        4. Multi-TF signal score (analyze_candles on M15)
        5. Confluence scoring (collect_all_factors -> score_zones -> calculate_trade_setup -> rank_and_filter)

        Returns dict with keys: smc, volume_profile, session_levels, signal_score, zones, indicators
        """
        from src.trading.smc import analyze_smc, smc_to_dict
        from src.trading.volume_profile import build_volume_profile, profile_to_dict
        from src.trading.session_levels import calculate_all_levels, levels_to_dict
        from src.trading.analysis import analyze_candles
        from src.trading.signals import score_signal
        from src.trading.confluence import (
            collect_all_factors,
            score_zones,
            calculate_trade_setup,
            rank_and_filter,
        )

        candles_h1 = data.get("candles_h1", [])
        candles_d1 = data.get("candles_d1", [])
        candles_m15 = data.get("candles_m15", [])
        tick = data.get("tick", {})
        account = data.get("account", {})

        current_price = tick.get("bid", 0.0) or tick.get("ask", 0.0)
        if not current_price and candles_h1:
            current_price = candles_h1[-1].get("close", 0.0)

        # 1. SMC analysis on H1
        smc_result = analyze_smc(candles_h1)
        smc_dict = smc_to_dict(smc_result, current_price)

        # 2. Volume Profile on H1
        vp_result = build_volume_profile(candles_h1)
        vp_dict = profile_to_dict(vp_result)

        # 3. Session Levels
        # Get swing high/low from SMC if available
        swing_high = 0.0
        swing_low = 0.0
        swing_highs = [s for s in smc_result.swing_points if s.is_high]
        swing_lows = [s for s in smc_result.swing_points if not s.is_high]
        if swing_highs:
            swing_high = max(s.price for s in swing_highs)
        if swing_lows:
            swing_low = min(s.price for s in swing_lows)

        session_levels = calculate_all_levels(
            candles_d1, candles_h1, current_price,
            swing_high=swing_high, swing_low=swing_low,
        )
        session_dict = levels_to_dict(session_levels)

        # 4. Signal scoring on M15
        indicators = {}
        signal_score = {}
        if candles_m15:
            indicators = analyze_candles(candles_m15)
            signal_score = score_signal(
                {"per_timeframe": {"M15": indicators}, "weighted_score": 0, "alignment_score": 0},
                session=data.get("session", {}),
                smc_data=smc_dict,
                current_price=current_price,
            )

        # 5. Confluence scoring
        htf_bias = smc_dict.get("current_trend", "neutral")
        if htf_bias not in ("bullish", "bearish"):
            htf_bias = "neutral"

        factors = collect_all_factors(
            smc_dict, session_dict, vp_dict,
            current_price, htf_bias,
        )
        scored_zones = score_zones(factors)

        # Calculate trade setups for each zone
        atr = indicators.get("atr_14", 10.0) if indicators else 10.0
        if atr is None:
            atr = 10.0
        balance = account.get("balance", 0.0)

        for zone in scored_zones:
            calculate_trade_setup(zone, current_price, atr, balance)

        # Rank and filter
        filtered_zones = rank_and_filter(scored_zones)

        # Serialize zones to dicts with full factor detail
        zones_dicts: list[dict] = []
        for z in filtered_zones:
            factor_details = []
            for f in z.factors:
                factor_details.append({
                    "name": f.name,
                    "category": f.category,
                    "price": f.price,
                    "weight": f.weight,
                    "direction": f.direction,
                })
            zone_dict = {
                "zone_id": z.zone_id,
                "direction": z.direction,
                "price_high": z.price_high,
                "price_low": z.price_low,
                "confluence_score": z.confluence_score,
                "factors": [f.name for f in z.factors],
                "factor_details": factor_details,
                "sl_price": z.sl_price,
                "tp1_price": z.tp1_price,
                "tp2_price": z.tp2_price,
                "rr_ratio": z.rr_ratio,
                "reasoning": z.reasoning,
                "invalidation": z.invalidation,
            }
            zones_dicts.append(zone_dict)

        result = {
            "smc": smc_dict,
            "volume_profile": vp_dict,
            "session_levels": session_dict,
            "signal_score": signal_score,
            "zones": zones_dicts,
            "indicators": indicators,
        }

        log.debug(
            "quantitative_analysis_complete",
            trend=smc_dict.get("current_trend"),
            zones=len(zones_dicts),
            signal=signal_score.get("score", 0),
        )

        return result

    async def _llm_reason(self, quant: dict, zones: list[dict], data: dict) -> dict:
        """Call LLM for precise trading analysis.

        Sends comprehensive data including recent candles, all key levels,
        structure events, and confluence zones. Demands specific price-based
        reasoning with exact entry/SL/TP levels.
        """
        system_prompt = (
            "You are a professional XAUUSD intraday trader specializing in Smart Money Concepts (SMC). "
            "You MUST analyze the provided data with PRECISION — every statement must reference EXACT price levels.\n\n"
            "## ANALYSIS RULES:\n"
            "1. ALWAYS reference exact prices (e.g., 'Bearish OB at 2665-2670' NOT 'OB above')\n"
            "2. Check where current price sits relative to: PDH/PDL, Asian range, POC/VAH/VAL, OBs, FVGs\n"
            "3. Identify if Asian range has been swept (price went above Asian High or below Asian Low then reversed)\n"
            "4. Check if previous day's high/low has been swept as liquidity\n"
            "5. Look for confluence: OB + FVG + Fib level + session level at same price = strong zone\n"
            "6. Note if price is above/below POC (Point of Control) — above = bullish value, below = bearish value\n"
            "7. Check recent candle momentum: are last 5 candles pushing one direction or ranging?\n"
            "8. ATR tells you average range — compare today's range to ATR for expansion/contraction\n\n"
            "## OUTPUT FORMAT (JSON):\n"
            "{\n"
            '  "bias": "bullish" | "bearish" | "neutral",\n'
            '  "bias_reasoning": "Specific 3-5 sentence analysis with exact prices. E.g.: Price at 2655 sitting above POC 2648. '
            'D1 trend bullish with last BOS at 2640. PDH 2670 not yet swept — potential liquidity target. '
            'H1 bearish OB at 2665-2670 aligns with Fib 0.618 at 2667. Bias bullish until 2640 breaks.",\n'
            '  "market_regime": "trending" | "ranging" | "volatile" | "quiet",\n'
            '  "key_levels": {\n'
            '    "resistance": [{"price": 2670, "reason": "PDH + Bearish OB"}, ...],\n'
            '    "support": [{"price": 2640, "reason": "Bullish OB + Fib 0.618"}, ...]\n'
            '  },\n'
            '  "scenarios": [\n'
            '    {"condition": "Price breaks above 2670 and holds", "action": "Look for buy at 2665 retest", "new_bias": "bullish"},\n'
            '    {"condition": "Price rejects 2665-2670 OB zone", "action": "Sell with SL 2675, TP 2640", "new_bias": "bearish"}\n'
            '  ],\n'
            '  "invalidation": "Plan invalid if price closes H1 below 2620 (structure break)",\n'
            '  "dxy_context": "Optional DXY note"\n'
            "}\n\n"
            "CRITICAL: Be SPECIFIC. No vague statements like 'watch for opportunities' or 'market is uncertain'. "
            "State exact prices, exact zones, exact conditions."
        )

        # Build comprehensive data payload
        smc_data = quant.get("smc", {})
        vp_data = quant.get("volume_profile", {})
        session_levels = quant.get("session_levels", {})
        signal = quant.get("signal_score", {})
        indicators = quant.get("indicators", {})
        session_info = data.get("session", {})
        tick = data.get("tick", {})
        account = data.get("account", {})

        current_price = tick.get("bid", 0.0) or tick.get("ask", 0.0)

        # Format recent H1 candles (last 15) for LLM to see price action
        h1_recent = []
        for c in (data.get("candles_h1") or [])[-15:]:
            h1_recent.append({
                "time": c.get("time", ""),
                "O": round(c.get("open", 0), 2),
                "H": round(c.get("high", 0), 2),
                "L": round(c.get("low", 0), 2),
                "C": round(c.get("close", 0), 2),
                "V": c.get("tick_volume", c.get("volume", 0)),
            })

        # Format recent M15 candles (last 10)
        m15_recent = []
        for c in (data.get("candles_m15") or [])[-10:]:
            m15_recent.append({
                "time": c.get("time", ""),
                "O": round(c.get("open", 0), 2),
                "H": round(c.get("high", 0), 2),
                "L": round(c.get("low", 0), 2),
                "C": round(c.get("close", 0), 2),
            })

        # Full session levels
        prev_day = session_levels.get("previous_day", {})
        asian = session_levels.get("asian_session", {})
        fib = session_levels.get("fibonacci", {})
        rounds = session_levels.get("round_numbers", [])

        # Full volume profile
        hvn_list = vp_data.get("hvn", [])
        lvn_list = vp_data.get("lvn", [])

        # SMC details
        obs = smc_data.get("order_blocks", [])
        fvgs = smc_data.get("fair_value_gaps", [])
        structure_events = smc_data.get("structure_events", [])
        liq_sweeps = smc_data.get("liquidity_sweeps", [])

        # ATR and spread
        atr = indicators.get("atr_14", 0) if indicators else 0
        spread = tick.get("spread", 0)

        user_payload = {
            "current_price": current_price,
            "spread": spread,
            "session": session_info.get("session", ""),
            "atr_h1": round(atr, 2) if atr else 0,
            "account_balance": account.get("balance", 0),
            "recent_h1_candles": h1_recent,
            "recent_m15_candles": m15_recent,
            "smc": {
                "trend": smc_data.get("current_trend", ""),
                "structure_events": structure_events[-5:] if structure_events else [],
                "order_blocks": obs[:5],
                "fair_value_gaps": fvgs[:5],
                "liquidity_sweeps": liq_sweeps[-3:] if liq_sweeps else [],
                "swing_points_count": smc_data.get("summary", {}).get("swing_points", 0),
            },
            "volume_profile": {
                "poc": vp_data.get("poc", 0),
                "vah": vp_data.get("vah", 0),
                "val": vp_data.get("val", 0),
                "hvn": hvn_list[:5],
                "lvn": lvn_list[:5],
            },
            "session_levels": {
                "pdh": prev_day.get("high", 0),
                "pdl": prev_day.get("low", 0),
                "pdo": prev_day.get("open", 0),
                "pdc": prev_day.get("close", 0),
                "asian_high": asian.get("high", 0),
                "asian_low": asian.get("low", 0),
                "asian_swept_high": asian.get("swept_high", False),
                "asian_swept_low": asian.get("swept_low", False),
                "round_numbers": rounds[:5] if isinstance(rounds, list) else [],
                "fibonacci": fib if isinstance(fib, dict) else {},
            },
            "signal": {
                "score": signal.get("score", 0),
                "direction": signal.get("direction", ""),
                "confidence": signal.get("confidence", ""),
                "recommendation": signal.get("recommendation", ""),
            },
            "confluence_zones": zones[:5],
        }

        user_content = json.dumps(user_payload, indent=2, default=str)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            client = get_claude_client()
            resp = await client.complete(
                messages=messages,
                model=self._model,
                response_format={"type": "json_object"},
            )

            content = resp.choices[0].message.content or ""
            # Strip markdown code blocks if present
            if content.startswith("```"):
                lines = content.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                content = "\n".join(lines).strip()
            result = json.loads(content)

            log.debug(
                "llm_reasoning_complete",
                bias=result.get("bias"),
                regime=result.get("market_regime"),
                scenarios=len(result.get("scenarios", [])),
            )

            return result

        except json.JSONDecodeError as e:
            log.warning("llm_json_parse_error", error=str(e), content_preview=content[:200] if content else "empty")
            return self._default_llm_result()

        except Exception as e:
            log.warning("llm_reasoning_error", error=str(e))
            return self._default_llm_result()

    @staticmethod
    def _default_llm_result() -> dict:
        """Return reasonable defaults when LLM fails."""
        return {
            "bias": "neutral",
            "bias_reasoning": "LLM analysis unavailable. Defaulting to neutral.",
            "market_regime": "",
            "scenarios": [],
            "zone_adjustments": "",
            "invalidation": "",
            "dxy_context": "",
        }

    def format_plan_telegram(self, plan: TradePlan) -> str:
        """Format full plan for Telegram display."""
        bias_emoji = {
            "bullish": "\U0001f7e2",   # green circle
            "bearish": "\U0001f534",   # red circle
            "neutral": "\u26aa",       # white circle
        }
        bias_icon = bias_emoji.get(plan.bias, "\u26aa")

        date_str = plan.created_at.strftime("%d/%m/%Y")

        lines = [
            f"\U0001f4cb TRADE PLAN \u2014 {plan.session} Session {date_str}",
            "",
            "\U0001f4ca Market Context:",
            f"  Bias: {bias_icon} {plan.bias.title()}",
        ]

        if plan.market_regime:
            lines.append(f"  Regime: {plan.market_regime.upper()}")
        if plan.weekly_structure:
            lines.append(f"  W1: {plan.weekly_structure}")
        if plan.daily_structure:
            lines.append(f"  D1: {plan.daily_structure}")
        if plan.bias_reasoning:
            lines.append(f"  Reasoning: {plan.bias_reasoning}")

        # Key levels from LLM
        if plan.key_levels:
            resistance = plan.key_levels.get("resistance", [])
            support = plan.key_levels.get("support", [])
            if resistance or support:
                lines.append("")
                lines.append("\U0001f4cd Key Levels:")
                for lvl in resistance[:4]:
                    if isinstance(lvl, dict):
                        p = lvl.get("price", 0)
                        r = lvl.get("reason", "")
                        lines.append(f"  \U0001f534 {p:.0f} — {r}")
                for lvl in support[:4]:
                    if isinstance(lvl, dict):
                        p = lvl.get("price", 0)
                        r = lvl.get("reason", "")
                        lines.append(f"  \U0001f7e2 {p:.0f} — {r}")

        # Alert zones
        if plan.alert_zones:
            lines.append("")
            lines.append(f"\U0001f3af Alert Zones (ranked):")
            for i, zone in enumerate(plan.alert_zones, 1):
                direction = zone.get("direction", "").upper()
                price_low = zone.get("price_low", 0)
                price_high = zone.get("price_high", 0)
                score = zone.get("confluence_score", 0)
                factors = zone.get("factors", [])
                sl = zone.get("sl_price", 0)
                tp1 = zone.get("tp1_price", 0)
                tp2 = zone.get("tp2_price", 0)
                rr = zone.get("rr_ratio", 0)

                lines.append(
                    f"{i}. {direction} @ [{price_low:.0f}-{price_high:.0f}] "
                    f"\u2014 Score: {score}"
                )

                if isinstance(factors, list) and factors:
                    factor_strs = []
                    for f in factors:
                        if isinstance(f, str):
                            factor_strs.append(f)
                        elif isinstance(f, dict):
                            factor_strs.append(f.get("name", str(f)))
                        else:
                            factor_strs.append(str(f))
                    lines.append(f"   {' + '.join(factor_strs)}")

                if sl or tp1 or tp2:
                    lines.append(
                        f"   SL: {sl:.0f} | TP1: {tp1:.0f} | TP2: {tp2:.0f}"
                    )
                if rr:
                    lines.append(f"   R:R = 1:{rr:.1f}")

        # Scenarios
        if plan.scenarios:
            lines.append("")
            lines.append("\U0001f4cb Scenarios:")
            for sc in plan.scenarios:
                lines.append(f"  {sc.condition} \u2192 {sc.action}")

        # Invalidation
        if plan.invalidation:
            lines.append("")
            lines.append(f"\u26a0\ufe0f Invalidation: {plan.invalidation}")

        # Risk budget
        lines.append("")
        lines.append(
            f"\U0001f4b0 Budget: {plan.risk_budget_pct}% max | "
            f"{plan.max_trades} trades max | "
            f"{plan.trades_taken}/{plan.max_trades} taken"
        )

        return "\n".join(lines)

    def format_plan_short(self, plan: TradePlan) -> str:
        """Short summary for status display."""
        return (
            f"{plan.session} | {plan.bias.title()} | "
            f"{len(plan.alert_zones)} zones | "
            f"Budget: {plan.risk_budget_pct}% | "
            f"{plan.trades_taken}/{plan.max_trades} trades"
        )
