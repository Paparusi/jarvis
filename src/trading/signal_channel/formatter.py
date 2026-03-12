"""Signal message formatter — Beautiful Telegram messages."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .models import (
    TradingSignal, SignalDirection, SignalStatus, 
    SignalPerformance, SignalTier,
)


# Timezone for display
TZ_BANGKOK = timezone(timedelta(hours=7))


def format_signal(signal: TradingSignal) -> str:
    """Format a trading signal as a beautiful Telegram message."""
    
    # Direction emoji and color indicator
    if signal.direction == SignalDirection.BUY:
        dir_emoji = "🟢"
        dir_text = "BUY (LONG)"
        arrow = "📈"
    else:
        dir_emoji = "🔴" 
        dir_text = "SELL (SHORT)"
        arrow = "📉"
    
    # Confidence indicator
    conf_map = {
        "HIGH": "🔥🔥🔥",
        "MEDIUM": "🔥🔥",
        "LOW": "🔥",
    }
    conf_indicator = conf_map.get(signal.confidence, "🔥")
    
    # Tier badge
    tier_badge = "🆓 FREE" if signal.tier == SignalTier.FREE else "💎 VIP"
    
    # Calculate R:R
    signal.calculate_rr()
    
    # Build TP lines
    tp_lines = f"🎯 TP1: ${signal.take_profit_1:,.2f} (+{signal.tp1_pips:.0f} pips)"
    if signal.take_profit_2:
        tp_lines += f"\n🎯 TP2: ${signal.take_profit_2:,.2f} (+{signal.tp2_pips:.0f} pips)"
    if signal.take_profit_3:
        tp3_pips = abs(signal.take_profit_3 - signal.entry_price) * 10
        tp_lines += f"\n🎯 TP3: ${signal.take_profit_3:,.2f} (+{tp3_pips:.0f} pips)"
    
    # Time
    local_time = signal.created_at.astimezone(TZ_BANGKOK)
    time_str = local_time.strftime("%H:%M %d/%m")
    
    msg = f"""
{dir_emoji} <b>{signal.symbol} — {dir_text}</b> {arrow}
━━━━━━━━━━━━━━━━━━━━

💰 Entry: <b>${signal.entry_price:,.2f}</b>
🛑 SL: ${signal.stop_loss:,.2f} (-{signal.sl_pips:.0f} pips)
{tp_lines}

📊 Score: <b>{signal.score}/100</b> {conf_indicator}
📐 R:R = <b>1:{signal.risk_reward}</b>
⏱ Timeframe: {signal.timeframe}
🏷 Setup: {signal.setup_type}

{f'📝 <i>{signal.analysis}</i>' if signal.analysis else ''}

━━━━━━━━━━━━━━━━━━━━
{tier_badge} | ⏰ {time_str} | #{signal.id}
""".strip()
    
    return msg


def format_signal_update(signal: TradingSignal) -> str:
    """Format signal status update (TP hit, SL hit, etc.)."""
    
    status_map = {
        SignalStatus.TP1_HIT: ("🎯✅", "TP1 HIT!", "green"),
        SignalStatus.TP2_HIT: ("🎯🎯✅", "TP2 HIT!", "green"),
        SignalStatus.TP3_HIT: ("🎯🎯🎯✅", "TP3 HIT!", "green"),
        SignalStatus.SL_HIT: ("🛑❌", "SL HIT", "red"),
        SignalStatus.CLOSED: ("🔒", "CLOSED", "neutral"),
        SignalStatus.CANCELLED: ("⛔", "CANCELLED", "neutral"),
    }
    
    emoji, status_text, color = status_map.get(
        signal.status, ("ℹ️", signal.status.value.upper(), "neutral")
    )
    
    dir_emoji = "🟢" if signal.direction == SignalDirection.BUY else "🔴"
    
    # PnL info
    pnl_text = ""
    if signal.pnl_pips is not None:
        pnl_sign = "+" if signal.pnl_pips >= 0 else ""
        pnl_text = f"\n💰 P/L: <b>{pnl_sign}{signal.pnl_pips:.0f} pips</b>"
        if signal.pnl_usd is not None:
            pnl_text += f" (${signal.pnl_usd:+,.2f})"
    
    close_text = ""
    if signal.close_price is not None:
        close_text = f"\n📍 Close: ${signal.close_price:,.2f}"
    
    msg = f"""
{emoji} <b>SIGNAL UPDATE — {status_text}</b>

{dir_emoji} {signal.symbol} #{signal.id}
💰 Entry: ${signal.entry_price:,.2f}{close_text}{pnl_text}
""".strip()
    
    return msg


def format_daily_summary(perf: SignalPerformance, date: datetime | None = None) -> str:
    """Format daily performance summary."""
    
    if date is None:
        date = datetime.now(TZ_BANGKOK)
    
    date_str = date.strftime("%d/%m/%Y")
    
    # Overall emoji
    if perf.win_rate >= 70:
        overall = "🏆"
    elif perf.win_rate >= 50:
        overall = "✅"
    else:
        overall = "📊"
    
    pnl_sign = "+" if perf.total_pips >= 0 else ""
    
    # Win/loss bar visualization
    total = perf.wins + perf.losses
    if total > 0:
        bar_len = 20
        win_bars = round(perf.wins / total * bar_len)
        loss_bars = bar_len - win_bars
        bar = "🟩" * win_bars + "🟥" * loss_bars
    else:
        bar = "⬜" * 20
    
    msg = f"""
{overall} <b>DAILY REPORT — {date_str}</b>
━━━━━━━━━━━━━━━━━━━━

📊 Signals: <b>{perf.total_signals}</b>
✅ Wins: <b>{perf.wins}</b> | ❌ Losses: <b>{perf.losses}</b>
📈 Win Rate: <b>{perf.win_rate}%</b>

{bar}

💰 Total: <b>{pnl_sign}{perf.total_pips:.0f} pips</b> (${perf.total_pnl_usd:+,.2f})
📈 Best: +{perf.best_trade_pips:.0f} pips
📉 Worst: {perf.worst_trade_pips:.0f} pips
📊 Avg: {perf.avg_pips_per_trade:.0f} pips/trade

🔥 Win Streak: {perf.current_streak} (Max: {perf.max_win_streak})
━━━━━━━━━━━━━━━━━━━━
🤖 Powered by JARVIS AI Signal
""".strip()
    
    return msg


def format_welcome(is_vip: bool = False) -> str:
    """Welcome message for new subscribers."""
    
    if is_vip:
        return """
💎 <b>Welcome to JARVIS VIP Signals!</b>

Bạn đã kích hoạt gói VIP. Bạn sẽ nhận:
• 8-10 tín hiệu chi tiết mỗi ngày
• Entry chính xác, multi-TP levels
• Phân tích kỹ thuật kèm theo
• Cảnh báo sớm khi có setup mạnh
• Báo cáo hiệu suất hàng ngày
• Hỗ trợ 1-1 khi cần

⚡ Signals sẽ được gửi tự động 24/5.
📊 Theo dõi /performance để xem win rate.

Good luck & happy trading! 🚀
""".strip()
    else:
        return """
🤖 <b>Welcome to JARVIS Trading Signals!</b>

Kênh tín hiệu trading AI miễn phí:
• 2-3 tín hiệu cơ bản mỗi ngày
• XAUUSDm (Gold) focus
• Entry, SL, TP rõ ràng

💎 Nâng cấp VIP để nhận:
• 8-10 signals/ngày + phân tích chi tiết
• Multi-TP + cảnh báo sớm
• Win rate tracking real-time

📊 /performance — Xem hiệu suất
💎 /vip — Nâng cấp VIP
ℹ️ /help — Hướng dẫn

Happy trading! 🚀
""".strip()
