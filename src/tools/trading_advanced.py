"""Advanced Trading Tools — Multi-TF analysis, signal scoring, risk management, trade journal, SMC.

Phase 2+3 extension of the MT5 trading toolkit.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.trading.mt5_client import MT5Client
from src.trading.multi_timeframe import analyze_multi_timeframe
from src.trading.signals import score_signal
from src.trading.risk import calculate_position_size, calculate_risk_reward
from src.trading.journal import log_trade, calculate_stats, sync_from_mt5
from src.utils.logging import get_logger

log = get_logger("tools.trading_advanced")

import os as _os
_DEFAULT_SYMBOL = _os.environ.get("TRADING_SYMBOL", "XAUUSD")

_client: MT5Client | None = None

_BRIDGE_DOWN_MSG = (
    "MT5 Bridge offline. Kiểm tra:\n"
    "1. Windows: python mt5_bridge.py\n"
    "2. .env: MT5_BRIDGE_URL=http://<windows-ip>:8710"
)

_VALID_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"}


def _get_client() -> MT5Client:
    global _client
    if _client is None:
        _client = MT5Client()
    return _client


def _format_error(e: Exception) -> str:
    etype = type(e).__name__
    if "Connect" in etype or "Connection" in str(e):
        return _BRIDGE_DOWN_MSG
    if isinstance(e, httpx.HTTPStatusError):
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = e.response.text[:200]
        return f"MT5 error ({e.response.status_code}): {detail}"
    return f"MT5 error: {e}"


# ═══════════════════════════════════════════════════════════════════════
# Tool Handlers
# ═══════════════════════════════════════════════════════════════════════


async def mt5_analyze(
    symbol: str = _DEFAULT_SYMBOL,
    timeframes: str = "M15,H1,H4,D1",
) -> ToolResult:
    """Full multi-timeframe analysis with signal scoring."""
    start = time.monotonic()
    client = _get_client()

    tf_list = [tf.strip().upper() for tf in timeframes.split(",")]
    tf_list = [tf for tf in tf_list if tf in _VALID_TIMEFRAMES]

    if not tf_list:
        return ToolResult(success=False, output="", error="Không có timeframe hợp lệ")

    try:
        multi = await analyze_multi_timeframe(client, symbol, tf_list)
        if "error" in multi:
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                success=False, output="", error=multi["error"],
                execution_time_ms=elapsed,
            )

        session = multi.get("session", {})

        # Run SMC on H1 for confluence scoring
        smc_data = None
        try:
            from src.trading.smc import analyze_smc, smc_to_dict
            h1_candles = await client.get_rates(symbol, "H1", 200)
            if h1_candles and len(h1_candles) >= 50:
                smc_result = analyze_smc(h1_candles)
                smc_data = smc_to_dict(smc_result, h1_candles[-1]["close"])
        except Exception:
            pass  # SMC is optional enhancement

        signal = score_signal(multi, session, smc_data=smc_data)
        elapsed = int((time.monotonic() - start) * 1000)

        lines = [
            f"{'='*40}",
            f"Multi-Timeframe Analysis: {symbol.upper()}",
            f"{'='*40}",
        ]

        for tf, analysis in multi.get("per_timeframe", {}).items():
            lines.append(f"\n{tf}:")
            lines.append(f"  Price: {analysis.get('current_price', 0):.2f}")
            lines.append(f"  Trend: {analysis.get('trend', 'N/A')}")
            lines.append(f"  Signal: {analysis.get('signal_summary', 'N/A')}")
            rsi = analysis.get("rsi_14")
            if rsi is not None:
                lines.append(f"  RSI: {rsi:.1f}")
            macd = analysis.get("macd_histogram")
            if macd is not None:
                lines.append(f"  MACD Hist: {macd:.4f}")

        lines.extend([
            f"\n{'─'*40}",
            f"Alignment: {multi.get('alignment_score', 0):.0%}",
            f"Signal Score: {signal['score']}/100 ({signal['confidence']})",
            f"Direction: {signal['direction']}",
            f"Recommendation: {signal['recommendation']}",
        ])

        if signal.get("breakdown"):
            lines.append("\nBreakdown:")
            for k, v in signal["breakdown"].items():
                mx = signal.get("max_scores", {}).get(k, "?")
                lines.append(f"  {k}: {v}/{mx}")

        if multi.get("divergences"):
            lines.append(f"\nDivergences: {', '.join(multi['divergences'])}")

        lines.append(
            f"\nSession: {session.get('session', 'N/A')} "
            f"({session.get('volatility', 'N/A')})"
        )

        if smc_data:
            lines.append(f"\nSMC: {smc_data.get('current_trend', 'N/A').upper()}")
            if smc_data.get("last_structure_break"):
                lb = smc_data["last_structure_break"]
                lines.append(
                    f"  Last: {lb.get('event_type', '')} "
                    f"{lb.get('direction', '')} @ {lb.get('break_price', 0):.2f}"
                )
            s = smc_data.get("summary", {})
            lines.append(
                f"  OBs: {s.get('active_bullish_obs', 0)}B/"
                f"{s.get('active_bearish_obs', 0)}S"
            )
            lines.append(
                f"  FVGs: {s.get('active_bullish_fvgs', 0)}B/"
                f"{s.get('active_bearish_fvgs', 0)}S"
            )

        return ToolResult(
            success=True, output="\n".join(lines),
            data={"multi_tf": multi, "signal": signal, "smc": smc_data},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def mt5_signal(symbol: str = _DEFAULT_SYMBOL) -> ToolResult:
    """Quick signal: score + recommendation (H1+H4 only, faster)."""
    start = time.monotonic()
    client = _get_client()

    try:
        multi = await analyze_multi_timeframe(client, symbol, ["H1", "H4"])
        if "error" in multi:
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                success=False, output="", error=multi["error"],
                execution_time_ms=elapsed,
            )

        # Optional SMC enhancement
        smc_data = None
        try:
            from src.trading.smc import analyze_smc, smc_to_dict
            h1_candles = await client.get_rates(symbol, "H1", 200)
            if h1_candles and len(h1_candles) >= 50:
                smc_result = analyze_smc(h1_candles)
                smc_data = smc_to_dict(smc_result, h1_candles[-1]["close"])
        except Exception:
            pass

        signal = score_signal(multi, smc_data=smc_data)
        elapsed = int((time.monotonic() - start) * 1000)

        lines = [
            f"{symbol.upper()} Signal",
            f"  Score: {signal['score']}/100",
            f"  Direction: {signal['direction']}",
            f"  Recommendation: {signal['recommendation']}",
            f"  Confidence: {signal['confidence']}",
            f"  Alignment: {multi.get('alignment_score', 0):.0%}",
        ]
        if multi.get("divergences"):
            lines.append("  Warning: divergence detected")
        if smc_data:
            lines.append(f"  SMC: {smc_data.get('current_trend', 'N/A')}")

        return ToolResult(
            success=True, output="\n".join(lines), data=signal,
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def mt5_risk(
    balance: str = "0",
    risk_pct: str = "1.0",
    sl_distance: str = "0",
) -> ToolResult:
    """Calculate position size based on risk parameters."""
    start = time.monotonic()

    try:
        bal = float(balance)
        risk = float(risk_pct)
        sl_dist = float(sl_distance)
    except (ValueError, TypeError) as e:
        return ToolResult(
            success=False, output="",
            error=f"Tham số không hợp lệ: {e}",
        )

    # Auto-fetch balance if not provided
    if bal <= 0:
        try:
            client = _get_client()
            acc = await client.get_account()
            bal = acc.get("balance", 0)
        except Exception:
            return ToolResult(
                success=False, output="",
                error="Không thể lấy balance tự động. Cung cấp balance thủ công.",
            )

    if sl_dist <= 0:
        return ToolResult(
            success=False, output="",
            error="sl_distance phải > 0 (khoảng cách SL tính bằng giá, vd: 5.0 = $5)",
        )

    pos = calculate_position_size(bal, risk, sl_dist)
    elapsed = int((time.monotonic() - start) * 1000)

    output = (
        f"Position Size Calculator\n"
        f"  Balance: ${bal:,.2f}\n"
        f"  Risk: {risk}% = ${pos.risk_amount:,.2f}\n"
        f"  SL Distance: {sl_dist:.2f} ({pos.sl_pips:.0f} pips)\n"
        f"  Lot Size: {pos.lot_size}\n"
        f"  Pip Value: ${pos.pip_value:.2f}/pip\n"
        f"  Max Loss: ${pos.max_loss:,.2f}"
    )

    return ToolResult(
        success=True, output=output,
        data={
            "lot_size": pos.lot_size,
            "risk_amount": pos.risk_amount,
            "sl_pips": pos.sl_pips,
            "pip_value": pos.pip_value,
        },
        execution_time_ms=elapsed,
    )


async def mt5_journal_log(
    ticket: str = "0",
    notes: str = "",
    strategy: str = "",
) -> ToolResult:
    """Log or update a trade in the journal."""
    start = time.monotonic()

    try:
        ticket_int = int(ticket)
    except (ValueError, TypeError):
        return ToolResult(success=False, output="", error="Ticket phải là số nguyên")

    if ticket_int <= 0:
        return ToolResult(success=False, output="", error="Ticket phải > 0")

    try:
        trade_data: dict[str, Any] = {
            "ticket": ticket_int,
            "notes": notes,
            "strategy_name": strategy,
        }

        # Try to fetch trade info from MT5 for enrichment
        try:
            client = _get_client()
            positions = await client.get_positions()
            for pos in positions:
                if pos.get("ticket") == ticket_int:
                    trade_data.update({
                        "symbol": pos.get("symbol", _DEFAULT_SYMBOL),
                        "side": "buy" if pos.get("type", 0) == 0 else "sell",
                        "volume": pos.get("volume", 0),
                        "entry_price": pos.get("price_open", 0),
                        "sl": pos.get("sl", 0),
                        "tp": pos.get("tp", 0),
                        "profit": pos.get("profit", 0),
                    })
                    break
        except Exception:
            pass  # Enrichment is optional

        # Ensure minimum required fields
        trade_data.setdefault("symbol", _DEFAULT_SYMBOL)
        trade_data.setdefault("side", "buy")
        trade_data.setdefault("volume", 0.0)
        trade_data.setdefault("entry_price", 0.0)

        row_id = log_trade(trade_data)
        elapsed = int((time.monotonic() - start) * 1000)

        output = f"Trade #{ticket_int} logged (id={row_id})"
        if notes:
            output += f"\n  Notes: {notes}"
        if strategy:
            output += f"\n  Strategy: {strategy}"

        return ToolResult(
            success=True, output=output,
            data={"ticket": ticket_int, "row_id": row_id},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=f"Journal error: {e}",
            execution_time_ms=elapsed,
        )


async def mt5_journal_stats(days: str = "30") -> ToolResult:
    """Show trading statistics from the journal."""
    start = time.monotonic()

    try:
        days_int = int(days)
    except (ValueError, TypeError):
        days_int = 30

    days_int = max(1, min(365, days_int))

    try:
        stats = calculate_stats(days=days_int)
        elapsed = int((time.monotonic() - start) * 1000)

        if stats.get("total_trades", 0) == 0:
            return ToolResult(
                success=True,
                output=f"Không có giao dịch trong {days_int} ngày qua.\nDùng mt5_journal_sync để đồng bộ từ MT5.",
                data=stats, execution_time_ms=elapsed,
            )

        lines = [
            f"Trading Statistics ({days_int} days)",
            f"{'─'*35}",
            f"  Trades: {stats['total_trades']} (W: {stats['wins']} / L: {stats['losses']})",
            f"  Win Rate: {stats['win_rate']}%",
            f"  Total P&L: ${stats['total_pnl']:+,.2f}",
            f"  Avg Win: ${stats['avg_profit']:,.2f}",
            f"  Avg Loss: ${stats['avg_loss']:,.2f}",
            f"  Profit Factor: {stats['profit_factor']}",
            f"  Avg R: {stats['avg_r']}R",
            f"  Best Session: {stats['best_session']}",
            f"  Best Day: {stats['best_day_of_week']}",
            f"  Max Win Streak: {stats['max_win_streak']}",
            f"  Max Loss Streak: {stats['max_loss_streak']}",
        ]

        return ToolResult(
            success=True, output="\n".join(lines),
            data=stats, execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=f"Stats error: {e}",
            execution_time_ms=elapsed,
        )


async def mt5_journal_sync(days: str = "7") -> ToolResult:
    """Sync MT5 history into the trade journal."""
    start = time.monotonic()

    try:
        days_int = int(days)
    except (ValueError, TypeError):
        days_int = 7

    days_int = max(1, min(90, days_int))

    try:
        client = _get_client()
        deals = await client.get_history(days=days_int)
        result = sync_from_mt5(deals)
        elapsed = int((time.monotonic() - start) * 1000)

        output = (
            f"Journal Sync ({days_int} days)\n"
            f"  Synced: {result['synced']} trades\n"
            f"  Skipped: {result['skipped']} (already exists)\n"
            f"  Errors: {result['errors']}"
        )

        return ToolResult(
            success=True, output=output,
            data=result, execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


# ═══════════════════════════════════════════════════════════════════════
# Tool Definitions
# ═══════════════════════════════════════════════════════════════════════


mt5_analyze_tool = ToolDefinition(
    name="mt5_analyze",
    description="Phân tích đa khung thời gian (M15/H1/H4/D1) với chấm điểm tín hiệu 0-100.",
    parameters=[
        ToolParameter(
            name="symbol", type="string",
            description="Trading symbol (mặc định XAUUSD)",
            required=False, default=_DEFAULT_SYMBOL,
        ),
        ToolParameter(
            name="timeframes", type="string",
            description="Khung thời gian, cách nhau bởi dấu phẩy (mặc định M15,H1,H4,D1)",
            required=False, default="M15,H1,H4,D1",
        ),
    ],
    handler=mt5_analyze,
    timeout_seconds=30,
)

mt5_signal_tool = ToolDefinition(
    name="mt5_signal",
    description="Tín hiệu nhanh XAUUSD: điểm số 0-100 + khuyến nghị BUY/SELL/NEUTRAL.",
    parameters=[
        ToolParameter(
            name="symbol", type="string",
            description="Trading symbol (mặc định XAUUSD)",
            required=False, default=_DEFAULT_SYMBOL,
        ),
    ],
    handler=mt5_signal,
    timeout_seconds=20,
)

mt5_risk_tool = ToolDefinition(
    name="mt5_risk",
    description="Tính lot size dựa trên balance, risk %, và khoảng cách SL. Auto-fetch balance từ MT5.",
    parameters=[
        ToolParameter(
            name="balance", type="string",
            description="Balance (USD). Để 0 = tự động lấy từ MT5.",
            required=False, default="0",
        ),
        ToolParameter(
            name="risk_pct", type="string",
            description="Phần trăm rủi ro (mặc định 1.0 = 1%)",
            required=False, default="1.0",
        ),
        ToolParameter(
            name="sl_distance", type="string",
            description="Khoảng cách SL tính bằng giá (vd: 5.0 = $5 từ entry)",
        ),
    ],
    handler=mt5_risk,
    timeout_seconds=15,
)

mt5_journal_log_tool = ToolDefinition(
    name="mt5_journal_log",
    description="Ghi chú giao dịch vào journal. Thêm notes, strategy cho trade.",
    parameters=[
        ToolParameter(
            name="ticket", type="string",
            description="Ticket number của giao dịch",
        ),
        ToolParameter(
            name="notes", type="string",
            description="Ghi chú cho giao dịch",
            required=False, default="",
        ),
        ToolParameter(
            name="strategy", type="string",
            description="Tên chiến lược (vd: london_breakout)",
            required=False, default="",
        ),
    ],
    handler=mt5_journal_log,
    timeout_seconds=10,
)

mt5_journal_stats_tool = ToolDefinition(
    name="mt5_journal_stats",
    description="Thống kê giao dịch: win rate, profit factor, R trung bình, phiên tốt nhất.",
    parameters=[
        ToolParameter(
            name="days", type="string",
            description="Số ngày thống kê (mặc định 30)",
            required=False, default="30",
        ),
    ],
    handler=mt5_journal_stats,
    timeout_seconds=10,
)

mt5_journal_sync_tool = ToolDefinition(
    name="mt5_journal_sync",
    description="Đồng bộ lịch sử giao dịch MT5 vào journal. Tự động import deals đã đóng.",
    parameters=[
        ToolParameter(
            name="days", type="string",
            description="Số ngày cần sync (mặc định 7)",
            required=False, default="7",
        ),
    ],
    handler=mt5_journal_sync,
    timeout_seconds=20,
)


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: Smart Money Concepts Tool
# ═══════════════════════════════════════════════════════════════════════


async def mt5_smc(
    symbol: str = _DEFAULT_SYMBOL,
    timeframe: str = "H1",
    count: str = "200",
) -> ToolResult:
    """Detect SMC patterns: BOS/ChoCH, Order Blocks, FVG, Liquidity Sweeps, S/D zones."""
    start = time.monotonic()
    client = _get_client()

    tf = timeframe.strip().upper()
    if tf not in _VALID_TIMEFRAMES:
        return ToolResult(
            success=False, output="",
            error=f"Timeframe không hợp lệ: {tf}",
        )

    try:
        cnt = max(50, min(500, int(count)))
    except (ValueError, TypeError):
        cnt = 200

    try:
        candles = await client.get_rates(symbol, tf, cnt)
        if not candles or len(candles) < 50:
            return ToolResult(
                success=False, output="",
                error=f"Không đủ dữ liệu ({len(candles) if candles else 0} nến, cần >= 50)",
            )

        from src.trading.smc import analyze_smc, smc_to_dict

        smc = analyze_smc(candles)
        current_price = candles[-1]["close"]
        smc_dict = smc_to_dict(smc, current_price)
        elapsed = int((time.monotonic() - start) * 1000)

        lines = [
            f"{'='*45}",
            f"Smart Money Concepts: {symbol.upper()} ({tf})",
            f"{'='*45}",
            f"Price: {current_price:.2f}",
            f"Market Structure: {smc_dict.get('current_trend', 'N/A').upper()}",
        ]

        if smc_dict.get("last_structure_break"):
            lb = smc_dict["last_structure_break"]
            lines.append(
                f"Last Break: {lb.get('event_type', '')} "
                f"{lb.get('direction', '')} @ {lb.get('break_price', 0):.2f}"
            )

        obs = smc_dict.get("order_blocks", [])
        if obs:
            lines.append("\nOrder Blocks (unmitigated):")
            for ob in obs:
                emoji = "+" if ob.get("type") == "bullish" else "-"
                zone = ob.get("zone", [0, 0])
                lines.append(
                    f"  [{emoji}] {ob.get('type', '').upper()} "
                    f"[{zone[0]:.2f} - {zone[1]:.2f}] "
                    f"str={ob.get('strength', 0)}"
                )

        fvgs = smc_dict.get("fair_value_gaps", [])
        if fvgs:
            lines.append("\nFair Value Gaps:")
            for fvg in fvgs:
                zone = fvg.get("zone", [0, 0])
                lines.append(
                    f"  {fvg.get('type', '').upper()} "
                    f"[{zone[0]:.2f} - {zone[1]:.2f}] "
                    f"fill={fvg.get('mitigation_pct', 0):.0f}%"
                )

        sweeps = smc_dict.get("liquidity_sweeps", [])
        if sweeps:
            lines.append("\nLiquidity Sweeps:")
            for ls in sweeps:
                lines.append(
                    f"  Swept {ls.get('direction', '')} "
                    f"{ls.get('swept_level', 0):.2f} "
                    f"(wick: {ls.get('wick_extreme', 0):.2f}, "
                    f"{ls.get('num_touches', 0)} touches)"
                )

        zones = smc_dict.get("supply_demand_zones", [])
        if zones:
            lines.append("\nSupply/Demand Zones:")
            for z in zones:
                fresh = "FRESH" if not z.get("tested") else f"tested x{z.get('test_count', 0)}"
                zone = z.get("zone", [0, 0])
                lines.append(
                    f"  {z.get('type', '').upper()} "
                    f"[{zone[0]:.2f} - {zone[1]:.2f}] "
                    f"{fresh} str={z.get('strength', 0)}"
                )

        summary = smc_dict.get("summary", {})
        lines.extend([
            f"\n{'='*45}",
            f"Active OBs: {summary.get('active_bullish_obs', 0)} bull / "
            f"{summary.get('active_bearish_obs', 0)} bear",
            f"Active FVGs: {summary.get('active_bullish_fvgs', 0)} bull / "
            f"{summary.get('active_bearish_fvgs', 0)} bear",
        ])

        return ToolResult(
            success=True, output="\n".join(lines),
            data={"smc": smc_dict, "price": current_price},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


mt5_smc_tool = ToolDefinition(
    name="mt5_smc",
    description=(
        "Smart Money Concepts: Order Block, FVG, BOS/ChoCH, "
        "liquidity sweep, supply/demand zones. XAUUSD."
    ),
    parameters=[
        ToolParameter(
            name="symbol", type="string",
            description="Symbol (mặc định XAUUSD)",
            required=False, default=_DEFAULT_SYMBOL,
        ),
        ToolParameter(
            name="timeframe", type="string",
            description="Khung TG: M15, H1, H4, D1 (mặc định H1)",
            required=False, default="H1",
        ),
        ToolParameter(
            name="count", type="string",
            description="Số nến (50-500, mặc định 200)",
            required=False, default="200",
        ),
    ],
    handler=mt5_smc,
    timeout_seconds=20,
)


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: Trading Brain Tools
# ═══════════════════════════════════════════════════════════════════════

_brain = None


def _get_brain():
    return _brain


def set_trading_brain(brain):
    global _brain
    _brain = brain


async def trade_plan(session: str = "") -> ToolResult:
    """Create or show current trade plan."""
    start = time.monotonic()

    try:
        brain = _get_brain()
        if brain is None:
            return ToolResult(
                success=False, output="",
                error="Trading Brain chưa được khởi tạo. Gọi /trade start trước.",
            )

        result = await brain.plan_now(session or "")
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(success=True, output=result, execution_time_ms=elapsed)
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=f"Plan error: {e}",
            execution_time_ms=elapsed,
        )


async def trade_status() -> ToolResult:
    """Show Trading Brain status: plan, zones, positions, risk."""
    start = time.monotonic()

    try:
        brain = _get_brain()
        if brain is None:
            return ToolResult(
                success=True,
                output="Trading Brain: OFF\nDùng /trade start để bắt đầu.",
            )

        status = brain.get_status()

        # Cross-check with live MT5 positions (source of truth)
        mt5_positions = []
        try:
            mt5_positions = await brain._mt5.get_positions()
        except Exception:
            pass
        mt5_count = len(mt5_positions)

        elapsed = int((time.monotonic() - start) * 1000)

        lines = [
            "Trading Brain Status",
            f"  Running: {'ON' if status['running'] else 'OFF'}",
            f"  Plan: {status['plan']}",
            f"  Active Zones: {status['active_zones']}",
            f"  MT5 Positions: {mt5_count}",
            f"  Pending Orders: {status.get('pending_orders', 0)}",
            f"  Pending Approvals: {len(brain.approval_manager.get_pending())}",
            f"  Trades Taken: {status['trades_taken']}",
        ]

        # Show live MT5 position details
        for pos in mt5_positions:
            side = "BUY" if pos.get("type") == 0 else "SELL"
            lines.append(
                f"  Position: {side} {pos.get('volume', 0)} {pos.get('symbol', '')} "
                f"@ {pos.get('price_open', 0)} | P/L: {pos.get('profit', 0):+.2f}"
            )

        risk = status.get("risk", {})
        if risk:
            state = risk.get("state", {})
            lines.append(f"  Daily P/L: {state.get('daily_pnl', 0):+.2f}")
            lines.append(f"  Daily Trades: {state.get('daily_trades', 0)}")
            lines.append(f"  Consecutive Losses: {state.get('consecutive_losses', 0)}")

        status["mt5_positions"] = mt5_count
        return ToolResult(
            success=True, output="\n".join(lines),
            data=status, execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=f"Status error: {e}",
            execution_time_ms=elapsed,
        )


async def trade_config(param: str = "", value: str = "") -> ToolResult:
    """View or update risk config."""
    start = time.monotonic()

    try:
        brain = _get_brain()
        if brain is None:
            return ToolResult(
                success=False, output="",
                error="Trading Brain chưa được khởi tạo.",
            )

        rg = brain._risk_guard
        if rg is None:
            return ToolResult(success=False, output="", error="RiskGuard not available.")

        if not param:
            # Show all config
            status = rg.get_status()
            config = status.get("config", {})
            lines = ["Risk Config:"]
            for k, v in config.items():
                lines.append(f"  {k}: {v}")
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                success=True, output="\n".join(lines),
                data=config, execution_time_ms=elapsed,
            )

        # Update single param
        if not value:
            return ToolResult(
                success=False, output="",
                error=f"Cần value. Ví dụ: trade_config param=max_daily_loss_pct value=5.0",
            )

        # Try to convert value
        try:
            if value.lower() in ("true", "false"):
                parsed_value = value.lower() == "true"
            elif "." in value:
                parsed_value = float(value)
            else:
                parsed_value = int(value)
        except ValueError:
            parsed_value = value

        rg.update_config(**{param: parsed_value})
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=True,
            output=f"Updated: {param} = {parsed_value}",
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=f"Config error: {e}",
            execution_time_ms=elapsed,
        )


async def trade_control(action: str = "status") -> ToolResult:
    """Start/stop/kill Trading Brain. Actions: start, stop, kill, status."""
    start = time.monotonic()

    try:
        brain = _get_brain()

        action = action.lower().strip()

        if action == "status":
            if brain is None:
                return ToolResult(success=True, output="Trading Brain: OFF")
            status = brain.get_status()
            running = "ON" if status["running"] else "OFF"
            return ToolResult(
                success=True,
                output=f"Trading Brain: {running} | Zones: {status['active_zones']} | Positions: {status['active_positions']}",
            )

        if brain is None:
            return ToolResult(
                success=False, output="",
                error="Trading Brain chưa được khởi tạo. Gọi init_trading_brain() trước.",
            )

        if action == "start":
            await brain.start()
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                success=True,
                output="Trading Brain started. Session scheduler active.",
                execution_time_ms=elapsed,
            )

        elif action == "stop":
            await brain.stop()
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                success=True,
                output="Trading Brain stopped.",
                execution_time_ms=elapsed,
            )

        elif action == "kill":
            result = await brain.kill()
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                success=True, output=result,
                execution_time_ms=elapsed,
            )

        else:
            return ToolResult(
                success=False, output="",
                error=f"Unknown action: {action}. Use: start, stop, kill, status",
            )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=f"Control error: {e}",
            execution_time_ms=elapsed,
        )


async def trade_pending(action: str = "list") -> ToolResult:
    """View or cancel pending orders. Actions: list, cancel_all."""
    start = time.monotonic()

    try:
        brain = _get_brain()
        if brain is None:
            return ToolResult(
                success=False, output="",
                error="Trading Brain chưa được khởi tạo.",
            )

        action = action.lower().strip()

        if action == "list":
            orders = brain.pending_manager.active_orders
            if not orders:
                elapsed = int((time.monotonic() - start) * 1000)
                return ToolResult(
                    success=True, output="Không có pending orders.",
                    execution_time_ms=elapsed,
                )

            lines = [f"Pending Orders ({len(orders)}):"]
            for ticket, info in orders.items():
                direction = info.get("direction", "?").upper()
                order_type = info.get("order_type", "?")
                price = info.get("price", 0)
                sl = info.get("sl", 0)
                tp1 = info.get("tp1", 0)
                zone_id = info.get("zone_id", "?")
                volume = info.get("volume", 0)
                lines.append(
                    f"  #{ticket} {direction} {order_type} @ {price:.2f} "
                    f"Vol: {volume} SL: {sl:.2f} TP: {tp1:.2f} Zone: {zone_id}"
                )
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                success=True, output="\n".join(lines),
                data={"orders": orders}, execution_time_ms=elapsed,
            )

        elif action == "cancel_all":
            count = await brain.pending_manager.cancel_all()
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                success=True,
                output=f"Đã hủy {count} pending order(s).",
                execution_time_ms=elapsed,
            )

        else:
            return ToolResult(
                success=False, output="",
                error=f"Unknown action: {action}. Use: list, cancel_all",
            )

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=f"Pending error: {e}",
            execution_time_ms=elapsed,
        )


# Tool Definitions

trade_plan_tool = ToolDefinition(
    name="trade_plan",
    description="Tạo trade plan mới hoặc xem plan hiện tại. Phân tích đa TF + SMC + Volume Profile + Confluence.",
    parameters=[
        ToolParameter(
            name="session", type="string",
            description="Session: London / New York (tự động detect nếu để trống)",
            required=False, default="",
        ),
    ],
    handler=trade_plan,
    timeout_seconds=60,
)

trade_status_tool = ToolDefinition(
    name="trade_status",
    description="Trạng thái Trading Brain: plan, zones, positions, risk, P/L.",
    parameters=[],
    handler=trade_status,
    timeout_seconds=10,
)

trade_config_tool = ToolDefinition(
    name="trade_config",
    description="Xem hoặc cập nhật Risk Config. Ví dụ: max_daily_loss_pct, max_lot_size, min_rr_ratio.",
    parameters=[
        ToolParameter(
            name="param", type="string",
            description="Tên tham số (để trống = xem tất cả)",
            required=False, default="",
        ),
        ToolParameter(
            name="value", type="string",
            description="Giá trị mới cho tham số",
            required=False, default="",
        ),
    ],
    handler=trade_config,
    timeout_seconds=10,
)

trade_control_tool = ToolDefinition(
    name="trade_control",
    description="Start/Stop/Kill Trading Brain. Actions: start, stop, kill, status.",
    parameters=[
        ToolParameter(
            name="action", type="string",
            description="Hành động: start, stop, kill, status",
            required=False, default="status",
        ),
    ],
    handler=trade_control,
    timeout_seconds=30,
)

trade_pending_tool = ToolDefinition(
    name="trade_pending",
    description="Xem hoặc hủy pending orders. Actions: list, cancel_all.",
    parameters=[
        ToolParameter(
            name="action", type="string",
            description="Hành động: list (xem), cancel_all (hủy tất cả)",
            required=False, default="list",
        ),
    ],
    handler=trade_pending,
    timeout_seconds=15,
)
