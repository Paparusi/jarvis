"""Trading Tools — MT5 market data, orders, and analysis via REST bridge.

Connects to the MT5 Bridge (Windows FastAPI) running at MT5_BRIDGE_URL.
All tools degrade gracefully when bridge is offline.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from src.tools.base import ToolDefinition, ToolParameter, ToolResult
from src.trading.analysis import analyze_candles, get_market_session
from src.trading.mt5_client import MT5Client
from src.utils.logging import get_logger

log = get_logger("tools.trading")

import os as _os
_DEFAULT_SYMBOL = _os.environ.get("TRADING_SYMBOL", "XAUUSD")

# Shared client (lazy init)
_client: MT5Client | None = None

_VALID_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"}

_BRIDGE_DOWN_MSG = (
    "MT5 Bridge offline. Kiểm tra:\n"
    "1. Windows: python mt5_bridge.py (hoặc uvicorn mt5_bridge:app --host 0.0.0.0 --port 8710)\n"
    "2. .env: MT5_BRIDGE_URL=http://<windows-ip>:8710"
)


def _get_client() -> MT5Client:
    global _client
    if _client is None:
        _client = MT5Client()
    return _client


def _format_error(e: Exception) -> str:
    """Format connection or HTTP errors for user display."""
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


# ── Tool Handlers ─────────────────────────────────────────────────────


async def mt5_price(symbol: str = _DEFAULT_SYMBOL) -> ToolResult:
    """Get current bid/ask price for a symbol."""
    start = time.monotonic()
    client = _get_client()
    try:
        tick = await client.get_tick(symbol)
        elapsed = int((time.monotonic() - start) * 1000)
        bid = tick.get("bid", 0)
        ask = tick.get("ask", 0)
        spread = ask - bid
        output = (
            f"{symbol.upper()}\n"
            f"  Bid: {bid:.2f}\n"
            f"  Ask: {ask:.2f}\n"
            f"  Spread: {spread:.2f}"
        )
        return ToolResult(
            success=True, output=output, data=tick, execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def mt5_candles(
    symbol: str = _DEFAULT_SYMBOL, timeframe: str = "H1", count: int = 100,
) -> ToolResult:
    """Get OHLCV candle data for a symbol."""
    start = time.monotonic()

    tf = timeframe.upper()
    if tf not in _VALID_TIMEFRAMES:
        return ToolResult(
            success=False, output="",
            error=f"Timeframe không hợp lệ: {timeframe}. Chọn: {', '.join(sorted(_VALID_TIMEFRAMES))}",
        )

    count = max(1, min(int(count), 500))
    client = _get_client()

    try:
        candles = await client.get_rates(symbol, tf, count)
        elapsed = int((time.monotonic() - start) * 1000)

        if not candles:
            return ToolResult(
                success=True, output=f"Không có dữ liệu cho {symbol} {tf}",
                execution_time_ms=elapsed,
            )

        # Format summary (last 5 candles)
        lines = [f"{symbol.upper()} {tf} — {len(candles)} nến"]
        for c in candles[-5:]:
            ts = datetime.fromtimestamp(c["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"  {ts} | O:{c['open']:.2f} H:{c['high']:.2f} "
                f"L:{c['low']:.2f} C:{c['close']:.2f} V:{c.get('tick_volume', 0)}"
            )

        return ToolResult(
            success=True, output="\n".join(lines),
            data={"candles": candles, "count": len(candles)},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def mt5_account() -> ToolResult:
    """Get MT5 account balance, equity, margin info."""
    start = time.monotonic()
    client = _get_client()
    try:
        acc = await client.get_account()
        elapsed = int((time.monotonic() - start) * 1000)
        output = (
            f"MT5 Account: {acc.get('name', 'N/A')} ({acc.get('login', 'N/A')})\n"
            f"  Server: {acc.get('server', 'N/A')}\n"
            f"  Balance: ${acc.get('balance', 0):,.2f}\n"
            f"  Equity: ${acc.get('equity', 0):,.2f}\n"
            f"  Margin: ${acc.get('margin', 0):,.2f}\n"
            f"  Free Margin: ${acc.get('margin_free', 0):,.2f}\n"
            f"  Profit: ${acc.get('profit', 0):+,.2f}\n"
            f"  Leverage: 1:{acc.get('leverage', 0)}"
        )
        return ToolResult(
            success=True, output=output, data=acc, execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def mt5_positions(symbol: str = "") -> ToolResult:
    """List open positions with P&L."""
    start = time.monotonic()
    client = _get_client()
    try:
        positions = await client.get_positions(symbol=symbol or None)
        elapsed = int((time.monotonic() - start) * 1000)

        if not positions:
            return ToolResult(
                success=True, output="Không có vị thế đang mở.",
                data={"positions": [], "count": 0},
                execution_time_ms=elapsed,
            )

        total_profit = sum(p.get("profit", 0) for p in positions)
        lines = [f"Vị thế đang mở: {len(positions)} | Tổng P&L: ${total_profit:+,.2f}"]
        for p in positions:
            side = "BUY" if p.get("type", 0) == 0 else "SELL"
            lines.append(
                f"  #{p.get('ticket', '?')} {p.get('symbol', '?')} {side} "
                f"{p.get('volume', 0)} lot | Open: {p.get('price_open', 0):.2f} "
                f"| Current: {p.get('price_current', 0):.2f} "
                f"| P&L: ${p.get('profit', 0):+,.2f}"
            )
            if p.get("sl") or p.get("tp"):
                lines.append(
                    f"    SL: {p.get('sl', 0):.2f} | TP: {p.get('tp', 0):.2f}"
                )

        return ToolResult(
            success=True, output="\n".join(lines),
            data={"positions": positions, "count": len(positions), "total_profit": total_profit},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def mt5_order(
    symbol: str = _DEFAULT_SYMBOL,
    side: str = "buy",
    volume: str = "0.01",
    sl: str = "0",
    tp: str = "0",
) -> ToolResult:
    """Place a market order. REQUIRES CONFIRMATION (real money!)."""
    start = time.monotonic()

    # Validate side
    side_lower = side.lower()
    if side_lower not in ("buy", "sell"):
        return ToolResult(
            success=False, output="",
            error=f"side phải là 'buy' hoặc 'sell', nhận được '{side}'",
        )

    # Parse and validate volume
    try:
        vol = float(volume)
    except (ValueError, TypeError):
        return ToolResult(
            success=False, output="",
            error=f"Volume không hợp lệ: {volume}",
        )
    if vol <= 0:
        return ToolResult(success=False, output="", error="Volume phải > 0")
    if vol > 1.0:
        return ToolResult(
            success=False, output="",
            error="Volume tối đa 1.0 lot (safety cap). Tăng limit trong code nếu cần.",
        )

    # Parse SL/TP
    try:
        sl_val = float(sl) if sl and float(sl) > 0 else None
        tp_val = float(tp) if tp and float(tp) > 0 else None
    except (ValueError, TypeError):
        sl_val = None
        tp_val = None

    client = _get_client()
    try:
        result = await client.place_order(
            symbol=symbol,
            side=side_lower,
            volume=vol,
            sl=sl_val,
            tp=tp_val,
        )
        elapsed = int((time.monotonic() - start) * 1000)

        retcode = result.get("retcode", -1)
        if retcode == 10009:  # TRADE_RETCODE_DONE
            output = (
                f"Lệnh thành công!\n"
                f"  {side_lower.upper()} {vol} lot {symbol.upper()}\n"
                f"  Ticket: #{result.get('order', 'N/A')}\n"
                f"  Deal: #{result.get('deal', 'N/A')}\n"
                f"  Price: {result.get('price', 'N/A')}"
            )
            if sl_val:
                output += f"\n  SL: {sl_val:.2f}"
            if tp_val:
                output += f"\n  TP: {tp_val:.2f}"
        else:
            output = (
                f"Lệnh bị từ chối (retcode={retcode})\n"
                f"  Comment: {result.get('comment', 'N/A')}"
            )

        return ToolResult(
            success=retcode == 10009,
            output=output, data=result,
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def mt5_close(ticket: int = 0) -> ToolResult:
    """Close an open position. REQUIRES CONFIRMATION."""
    start = time.monotonic()

    if not ticket or ticket <= 0:
        return ToolResult(
            success=False, output="",
            error="Ticket number không hợp lệ. Dùng mt5_positions để xem danh sách.",
        )

    client = _get_client()
    try:
        result = await client.close_position(ticket)
        elapsed = int((time.monotonic() - start) * 1000)

        retcode = result.get("retcode", -1)
        if retcode == 10009:
            output = (
                f"Đóng vị thế #{ticket} thành công!\n"
                f"  Deal: #{result.get('deal', 'N/A')}"
            )
        else:
            output = (
                f"Đóng vị thế #{ticket} thất bại (retcode={retcode})\n"
                f"  Comment: {result.get('comment', 'N/A')}"
            )

        return ToolResult(
            success=retcode == 10009,
            output=output, data=result,
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def mt5_history(days: int = 7, symbol: str = "") -> ToolResult:
    """Get trade history for last N days."""
    start = time.monotonic()
    days = max(1, min(int(days), 90))
    client = _get_client()

    try:
        deals = await client.get_history(days=days, symbol=symbol or None)
        elapsed = int((time.monotonic() - start) * 1000)

        if not deals:
            return ToolResult(
                success=True, output=f"Không có giao dịch trong {days} ngày qua.",
                data={"deals": [], "count": 0},
                execution_time_ms=elapsed,
            )

        total_profit = sum(d.get("profit", 0) for d in deals)
        wins = sum(1 for d in deals if d.get("profit", 0) > 0)
        losses = sum(1 for d in deals if d.get("profit", 0) < 0)

        lines = [
            f"Lịch sử {days} ngày: {len(deals)} deals",
            f"  P&L: ${total_profit:+,.2f} | Win: {wins} | Loss: {losses}",
            "",
        ]
        for d in deals[-10:]:  # Last 10 deals
            ts = datetime.fromtimestamp(d.get("time", 0), tz=timezone.utc).strftime(
                "%m-%d %H:%M"
            )
            dtype = "BUY" if d.get("type", 0) == 0 else "SELL"
            lines.append(
                f"  {ts} {d.get('symbol', '?')} {dtype} "
                f"{d.get('volume', 0)} lot @ {d.get('price', 0):.2f} "
                f"| ${d.get('profit', 0):+,.2f}"
            )

        return ToolResult(
            success=True, output="\n".join(lines),
            data={"deals": deals, "count": len(deals), "total_profit": total_profit},
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def market_session_info() -> ToolResult:
    """Identify current forex trading session and characteristics."""
    start = time.monotonic()
    session = get_market_session()
    elapsed = int((time.monotonic() - start) * 1000)

    now = datetime.now(tz=timezone.utc).strftime("%H:%M UTC")
    output = (
        f"Thời gian: {now}\n"
        f"Phiên: {session['session']}\n"
        f"Volatility: {session['volatility']}\n"
        f"{session['description']}\n\n"
        f"Khuyến nghị: {session['recommendation']}"
    )
    if session.get("is_dead_zone"):
        output += "\n\nKHÔNG NÊN TRADE trong Dead Zone!"

    return ToolResult(
        success=True, output=output, data=session, execution_time_ms=elapsed,
    )


async def technical_indicators(
    symbol: str = _DEFAULT_SYMBOL, timeframe: str = "H1", count: int = 100,
) -> ToolResult:
    """Calculate technical indicators from live MT5 data."""
    start = time.monotonic()

    tf = timeframe.upper()
    if tf not in _VALID_TIMEFRAMES:
        return ToolResult(
            success=False, output="",
            error=f"Timeframe không hợp lệ: {timeframe}",
        )
    count = max(50, min(int(count), 500))  # Min 50 for indicators

    client = _get_client()
    try:
        candles = await client.get_rates(symbol, tf, count)
        elapsed_fetch = time.monotonic() - start

        if not candles or len(candles) < 30:
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                success=False, output="",
                error=f"Không đủ dữ liệu: {len(candles) if candles else 0} nến (cần ít nhất 30)",
                execution_time_ms=elapsed,
            )

        analysis = analyze_candles(candles)
        elapsed = int((time.monotonic() - start) * 1000)

        # Format output
        lines = [
            f"{symbol.upper()} {tf} Analysis ({len(candles)} candles)",
            f"  Price: {analysis['current_price']:.2f} ({analysis['change_pct']:+.2f}%)",
            f"  24h Range: {analysis['low_24']:.2f} - {analysis['high_24']:.2f}",
            "",
            "Indicators:",
        ]

        if analysis.get("rsi_14") is not None:
            rsi = analysis["rsi_14"]
            rsi_label = " (OVERBOUGHT)" if rsi > 70 else " (OVERSOLD)" if rsi < 30 else ""
            lines.append(f"  RSI(14): {rsi:.1f}{rsi_label}")

        if analysis.get("sma_20") is not None:
            lines.append(f"  SMA(20): {analysis['sma_20']:.2f}")
        if analysis.get("sma_50") is not None:
            lines.append(f"  SMA(50): {analysis['sma_50']:.2f}")

        if analysis.get("atr_14") is not None:
            lines.append(f"  ATR(14): {analysis['atr_14']:.2f}")

        if analysis.get("bollinger_upper") is not None:
            lines.append(
                f"  Bollinger: {analysis['bollinger_lower']:.2f} / "
                f"{analysis['bollinger_middle']:.2f} / {analysis['bollinger_upper']:.2f}"
            )

        if analysis.get("macd_line") is not None:
            lines.append(
                f"  MACD: {analysis['macd_line']:.2f} | "
                f"Signal: {analysis.get('macd_signal', 0) or 0:.2f} | "
                f"Hist: {analysis.get('macd_histogram', 0) or 0:.2f}"
            )

        lines.extend([
            "",
            f"Trend: {analysis['trend']}",
            f"Signal: {analysis['signal_summary']}",
        ])

        if analysis.get("signals"):
            lines.append("Reasons: " + ", ".join(analysis["signals"]))

        if analysis.get("support"):
            lines.append(f"Support: {', '.join(f'{s:.2f}' for s in analysis['support'][:3])}")
        if analysis.get("resistance"):
            lines.append(f"Resistance: {', '.join(f'{r:.2f}' for r in analysis['resistance'][:3])}")

        return ToolResult(
            success=True, output="\n".join(lines), data=analysis,
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ToolResult(
            success=False, output="", error=_format_error(e),
            execution_time_ms=elapsed,
        )


async def trading_calendar() -> ToolResult:
    """Check upcoming high-impact economic events affecting XAUUSD."""
    start = time.monotonic()

    # Static calendar of recurring high-impact events
    now = datetime.now(tz=timezone.utc)
    weekday = now.weekday()  # 0=Mon

    events = [
        "FOMC Decision — 8x/năm, 18:00 UTC (biến động $50-100)",
        "NFP — Thứ 6 đầu tháng, 12:30 UTC (biến động $30-80)",
        "CPI — ~12 hàng tháng, 12:30 UTC (biến động $20-60)",
        "PPI — ~14 hàng tháng, 12:30 UTC (biến động $10-30)",
        "GDP — Hàng quý, 12:30 UTC (biến động $10-25)",
        "Initial Jobless Claims — Thứ 5 hàng tuần, 12:30 UTC",
        "ISM Manufacturing — Ngày 1 hàng tháng, 14:00 UTC",
        "LBMA Gold Fix — Hàng ngày 10:30 + 15:00 London (09:30/14:00 UTC)",
    ]

    warnings = []
    # First Friday NFP warning
    if weekday == 4:  # Friday
        if now.day <= 7:
            warnings.append("CẢNH BÁO: Hôm nay có thể là ngày NFP! Check lịch cụ thể.")

    # Thursday jobless claims
    if weekday == 3:
        warnings.append("Hôm nay Thứ 5: Initial Jobless Claims lúc 12:30 UTC.")

    session = get_market_session()

    output_lines = [
        "Sự kiện kinh tế quan trọng ảnh hưởng XAUUSD:",
        "",
    ]
    output_lines.extend(f"  • {e}" for e in events)

    if warnings:
        output_lines.extend(["", "Cảnh báo hôm nay:"])
        output_lines.extend(f"  ⚠ {w}" for w in warnings)

    output_lines.extend([
        "",
        f"Phiên hiện tại: {session['session']} ({session['volatility']})",
        "",
        "Tip: Dùng web_search 'economic calendar today' để xem lịch cụ thể.",
    ])

    elapsed = int((time.monotonic() - start) * 1000)
    return ToolResult(
        success=True, output="\n".join(output_lines),
        data={"events": events, "warnings": warnings, "session": session},
        execution_time_ms=elapsed,
    )


# ── Tool Definitions ──────────────────────────────────────────────────

mt5_price_tool = ToolDefinition(
    name="mt5_price",
    description=f"Lấy giá bid/ask hiện tại của symbol trên MT5. Mặc định {_DEFAULT_SYMBOL}.",
    parameters=[
        ToolParameter(
            name="symbol", type="string",
            description=f"Trading symbol (mặc định: {_DEFAULT_SYMBOL})",
            required=False, default=_DEFAULT_SYMBOL,
        ),
    ],
    handler=mt5_price,
    timeout_seconds=15,
)

mt5_candles_tool = ToolDefinition(
    name="mt5_candles",
    description="Lấy dữ liệu nến OHLCV từ MT5. Hỗ trợ M1-MN1.",
    parameters=[
        ToolParameter(
            name="symbol", type="string",
            description="Trading symbol (mặc định XAUUSD)",
            required=False, default=_DEFAULT_SYMBOL,
        ),
        ToolParameter(
            name="timeframe", type="string",
            description="Khung thời gian nến",
            required=False, default="H1",
            enum=["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"],
        ),
        ToolParameter(
            name="count", type="integer",
            description="Số nến cần lấy (1-500, mặc định 100)",
            required=False, default=100,
        ),
    ],
    handler=mt5_candles,
    timeout_seconds=15,
)

mt5_account_tool = ToolDefinition(
    name="mt5_account",
    description="Xem thông tin tài khoản MT5: balance, equity, margin, profit.",
    parameters=[],
    handler=mt5_account,
    timeout_seconds=10,
)

mt5_positions_tool = ToolDefinition(
    name="mt5_positions",
    description="Xem các vị thế đang mở trên MT5 với P&L realtime.",
    parameters=[
        ToolParameter(
            name="symbol", type="string",
            description="Lọc theo symbol (bỏ trống = tất cả)",
            required=False, default="",
        ),
    ],
    handler=mt5_positions,
    timeout_seconds=10,
)

mt5_order_tool = ToolDefinition(
    name="mt5_order",
    description="Đặt lệnh mua/bán trên MT5. TIỀN THẬT — luôn xác nhận với user.",
    parameters=[
        ToolParameter(
            name="symbol", type="string",
            description="Trading symbol (vd: XAUUSD)",
        ),
        ToolParameter(
            name="side", type="string",
            description="Hướng lệnh: buy hoặc sell",
            enum=["buy", "sell"],
        ),
        ToolParameter(
            name="volume", type="string",
            description="Khối lượng lot (vd: 0.01). Tối đa 1.0 lot.",
        ),
        ToolParameter(
            name="sl", type="string",
            description="Giá stop loss (0 hoặc bỏ trống = không đặt SL)",
            required=False, default="0",
        ),
        ToolParameter(
            name="tp", type="string",
            description="Giá take profit (0 hoặc bỏ trống = không đặt TP)",
            required=False, default="0",
        ),
    ],
    handler=mt5_order,
    requires_confirmation=True,
    timeout_seconds=15,
)

mt5_close_tool = ToolDefinition(
    name="mt5_close",
    description="Đóng vị thế đang mở trên MT5 theo ticket. TIỀN THẬT!",
    parameters=[
        ToolParameter(
            name="ticket", type="integer",
            description="Ticket number của vị thế cần đóng",
        ),
    ],
    handler=mt5_close,
    requires_confirmation=True,
    timeout_seconds=15,
)

mt5_history_tool = ToolDefinition(
    name="mt5_history",
    description="Xem lịch sử giao dịch MT5 (deals đã đóng).",
    parameters=[
        ToolParameter(
            name="days", type="integer",
            description="Số ngày lịch sử (1-90, mặc định 7)",
            required=False, default=7,
        ),
        ToolParameter(
            name="symbol", type="string",
            description="Lọc theo symbol (bỏ trống = tất cả)",
            required=False, default="",
        ),
    ],
    handler=mt5_history,
    timeout_seconds=15,
)

market_session_tool = ToolDefinition(
    name="market_session",
    description="Xem phiên forex hiện tại (Sydney/Tokyo/London/NY), volatility, khuyến nghị.",
    parameters=[],
    handler=market_session_info,
    timeout_seconds=5,
)

technical_indicators_tool = ToolDefinition(
    name="technical_indicators",
    description="Phân tích kỹ thuật XAUUSD: RSI, SMA, EMA, ATR, Bollinger, MACD, trend, S/R.",
    parameters=[
        ToolParameter(
            name="symbol", type="string",
            description="Trading symbol (mặc định XAUUSD)",
            required=False, default=_DEFAULT_SYMBOL,
        ),
        ToolParameter(
            name="timeframe", type="string",
            description="Khung thời gian",
            required=False, default="H1",
            enum=["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
        ),
        ToolParameter(
            name="count", type="integer",
            description="Số nến để tính toán (50-500, mặc định 100)",
            required=False, default=100,
        ),
    ],
    handler=technical_indicators,
    timeout_seconds=20,
)

trading_calendar_tool = ToolDefinition(
    name="trading_calendar",
    description="Lịch kinh tế quan trọng ảnh hưởng XAUUSD (Fed, NFP, CPI, v.v.).",
    parameters=[],
    handler=trading_calendar,
    timeout_seconds=10,
)
