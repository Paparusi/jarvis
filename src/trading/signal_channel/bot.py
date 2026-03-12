"""Signal Channel Bot — Telegram bot for broadcasting trading signals.

This is a standalone bot that can run alongside the main JARVIS bot.
Uses a separate bot token for the signal channel.

Features:
- /start — Welcome + subscribe
- /performance — Show win rate stats
- /signals — Show active signals
- /vip — VIP subscription info
- Admin commands:
  - /signal BUY XAUUSD 5180 SL=5165 TP1=5210 TP2=5230
  - /update SIG0001 TP1_HIT 5210
  - /close SIG0001 5205
  - /daily — Send daily summary
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from telegram.constants import ParseMode

from .models import (
    TradingSignal, SignalDirection, SignalStatus, SignalTier,
)
from .formatter import (
    format_signal, format_signal_update, format_daily_summary, format_welcome,
)
from .tracker import SignalTracker

log = logging.getLogger("signal_bot")

# Admin user IDs who can send signals
ADMIN_IDS: set[int] = set()

# Channel IDs
FREE_CHANNEL_ID: int | None = None
VIP_CHANNEL_ID: int | None = None


class SignalBot:
    """Telegram Signal Bot."""
    
    def __init__(
        self,
        token: str,
        admin_ids: list[int] | None = None,
        free_channel_id: int | None = None,
        vip_channel_id: int | None = None,
    ):
        self.token = token
        self.admin_ids = set(admin_ids or [])
        self.free_channel_id = free_channel_id
        self.vip_channel_id = vip_channel_id
        self.tracker = SignalTracker()
        self.app: Application | None = None
    
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids
    
    async def start(self):
        """Start the bot."""
        self.app = Application.builder().token(self.token).build()
        
        # Public commands
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("performance", self._cmd_performance))
        self.app.add_handler(CommandHandler("signals", self._cmd_signals))
        self.app.add_handler(CommandHandler("vip", self._cmd_vip))
        
        # Admin commands
        self.app.add_handler(CommandHandler("signal", self._cmd_new_signal))
        self.app.add_handler(CommandHandler("update", self._cmd_update_signal))
        self.app.add_handler(CommandHandler("close", self._cmd_close_signal))
        self.app.add_handler(CommandHandler("daily", self._cmd_daily_summary))
        self.app.add_handler(CommandHandler("broadcast", self._cmd_broadcast))
        
        log.info("Signal bot starting...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("Signal bot running!")
    
    async def stop(self):
        """Stop the bot."""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
    
    # ── Public Commands ──
    
    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Welcome message."""
        await update.message.reply_text(
            format_welcome(is_vip=False),
            parse_mode=ParseMode.HTML,
        )
    
    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Help message."""
        msg = """
🤖 <b>JARVIS Trading Signals — Help</b>

<b>Commands:</b>
/start — Bắt đầu
/performance — Xem hiệu suất trading
/signals — Tín hiệu đang active
/vip — Thông tin gói VIP
/help — Trợ giúp

<b>Cách đọc tín hiệu:</b>
🟢 = BUY (Long) | 🔴 = SELL (Short)
💰 Entry = Giá vào lệnh
🛑 SL = Stop Loss (cắt lỗ)
🎯 TP = Take Profit (chốt lời)
📊 Score = Độ mạnh tín hiệu (0-100)

<b>Lưu ý:</b>
⚠️ Trading có rủi ro. Chỉ trade với tiền bạn chấp nhận mất.
📐 Luôn đặt SL. Không bao giờ di chuyển SL xa hơn.
""".strip()
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    
    async def _cmd_performance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show performance stats."""
        perf = self.tracker.performance
        today_perf = self.tracker.get_today_performance()
        
        msg = format_daily_summary(perf)
        
        # Add today's stats
        if today_perf.total_signals > 0:
            msg += f"\n\n📅 <b>Hôm nay:</b> {today_perf.wins}W/{today_perf.losses}L ({today_perf.win_rate}% WR) | {today_perf.total_pips:+.0f} pips"
        
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    
    async def _cmd_signals(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show active signals."""
        active = self.tracker.get_active_signals()
        
        if not active:
            await update.message.reply_text(
                "📊 Không có tín hiệu active.\n\nTín hiệu mới sẽ được gửi khi có setup tốt! 🔍"
            )
            return
        
        msg = f"📊 <b>{len(active)} tín hiệu đang active:</b>\n\n"
        for s in active[-5:]:  # Show last 5
            dir_emoji = "🟢" if s.direction == SignalDirection.BUY else "🔴"
            msg += f"{dir_emoji} {s.symbol} #{s.id} — Entry ${s.entry_price:,.2f} | SL ${s.stop_loss:,.2f} | TP1 ${s.take_profit_1:,.2f}\n"
        
        if len(active) > 5:
            msg += f"\n... và {len(active) - 5} tín hiệu khác"
        
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    
    async def _cmd_vip(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """VIP info."""
        msg = """
💎 <b>JARVIS VIP Trading Signals</b>

<b>Gói VIP bao gồm:</b>
• 8-10 tín hiệu chi tiết/ngày
• Multi-timeframe analysis
• Cảnh báo sớm trước khi market move
• Entry chính xác + multi-TP levels
• Risk management guidance
• Báo cáo hiệu suất hàng ngày
• Hỗ trợ trực tiếp

<b>Giá:</b>
• 1 tháng: $20
• 3 tháng: $50 (tiết kiệm $10)
• 6 tháng: $90 (tiết kiệm $30)

<b>Thanh toán:</b>
• USDT (TRC20/ERC20)
• Chuyển khoản ngân hàng VN
• Momo/ZaloPay

📩 Liên hệ @gau_trader để đăng ký!
""".strip()
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    
    # ── Admin Commands ──
    
    async def _cmd_new_signal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Create and broadcast a new signal.
        
        Usage: /signal BUY XAUUSDm 5180 SL=5165 TP1=5210 TP2=5230 [score=85] [tf=M15] [setup=Breakout] [analysis=Clean break above resistance] [tier=vip]
        """
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Admin only.")
            return
        
        args = " ".join(ctx.args) if ctx.args else ""
        signal = self._parse_signal_args(args)
        
        if signal is None:
            await update.message.reply_text(
                "❌ Format: /signal BUY XAUUSDm 5180 SL=5165 TP1=5210 [TP2=5230] [score=85] [tf=M15] [setup=Breakout] [tier=vip]"
            )
            return
        
        # Register signal
        self.tracker.add_signal(signal)
        
        # Format message
        msg = format_signal(signal)
        
        # Broadcast to channels
        sent = await self._broadcast_signal(msg, signal.tier)
        
        await update.message.reply_text(
            f"✅ Signal #{signal.id} created and sent to {sent} channel(s)!",
        )
    
    async def _cmd_update_signal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Update signal status.
        
        Usage: /update SIG0001 TP1_HIT [5210]
        """
        if not self.is_admin(update.effective_user.id):
            return
        
        if not ctx.args or len(ctx.args) < 2:
            await update.message.reply_text(
                "❌ Format: /update SIG0001 TP1_HIT|TP2_HIT|SL_HIT|CLOSED [close_price]"
            )
            return
        
        signal_id = ctx.args[0].upper()
        status_str = ctx.args[1].upper()
        close_price = float(ctx.args[2]) if len(ctx.args) > 2 else None
        
        try:
            status = SignalStatus(status_str.lower())
        except ValueError:
            status_map = {
                "TP1_HIT": SignalStatus.TP1_HIT,
                "TP2_HIT": SignalStatus.TP2_HIT, 
                "TP3_HIT": SignalStatus.TP3_HIT,
                "SL_HIT": SignalStatus.SL_HIT,
                "CLOSED": SignalStatus.CLOSED,
                "CANCELLED": SignalStatus.CANCELLED,
            }
            status = status_map.get(status_str)
            if not status:
                await update.message.reply_text(f"❌ Unknown status: {status_str}")
                return
        
        signal = self.tracker.update_signal(signal_id, status, close_price)
        if not signal:
            await update.message.reply_text(f"❌ Signal {signal_id} not found")
            return
        
        # Broadcast update
        msg = format_signal_update(signal)
        await self._broadcast_signal(msg, signal.tier)
        
        await update.message.reply_text(f"✅ Signal {signal_id} updated: {status.value}")
    
    async def _cmd_close_signal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Close a signal manually.
        
        Usage: /close SIG0001 5205
        """
        if not self.is_admin(update.effective_user.id):
            return
        
        if not ctx.args or len(ctx.args) < 2:
            await update.message.reply_text("❌ Format: /close SIG0001 5205")
            return
        
        signal_id = ctx.args[0].upper()
        close_price = float(ctx.args[1])
        
        signal = self.tracker.update_signal(signal_id, SignalStatus.CLOSED, close_price)
        if not signal:
            await update.message.reply_text(f"❌ Signal {signal_id} not found")
            return
        
        msg = format_signal_update(signal)
        await self._broadcast_signal(msg, signal.tier)
        await update.message.reply_text(f"✅ Signal {signal_id} closed at ${close_price:,.2f} ({signal.pnl_pips:+.0f} pips)")
    
    async def _cmd_daily_summary(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Send daily performance summary to channels."""
        if not self.is_admin(update.effective_user.id):
            return
        
        perf = self.tracker.get_today_performance()
        msg = format_daily_summary(perf)
        await self._broadcast_signal(msg, SignalTier.FREE)  # Send to all
        await update.message.reply_text("✅ Daily summary sent!")
    
    async def _cmd_broadcast(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Broadcast a custom message.
        
        Usage: /broadcast Your message here
        """
        if not self.is_admin(update.effective_user.id):
            return
        
        text = " ".join(ctx.args) if ctx.args else ""
        if not text:
            await update.message.reply_text("❌ Format: /broadcast Your message")
            return
        
        await self._broadcast_signal(f"📢 {text}", SignalTier.FREE)
        await update.message.reply_text("✅ Broadcast sent!")
    
    # ── Internal ──
    
    async def _broadcast_signal(self, msg: str, tier: SignalTier) -> int:
        """Send signal to appropriate channels. Returns number of channels sent to."""
        sent = 0
        
        # Free signals go to free channel
        if self.free_channel_id and self.app:
            try:
                await self.app.bot.send_message(
                    self.free_channel_id, msg, parse_mode=ParseMode.HTML,
                )
                sent += 1
            except Exception as e:
                log.error(f"Failed to send to free channel: {e}")
        
        # VIP signals also go to VIP channel
        if self.vip_channel_id and self.app:
            try:
                await self.app.bot.send_message(
                    self.vip_channel_id, msg, parse_mode=ParseMode.HTML,
                )
                sent += 1
            except Exception as e:
                log.error(f"Failed to send to VIP channel: {e}")
        
        return sent
    
    def _parse_signal_args(self, args: str) -> TradingSignal | None:
        """Parse signal from command arguments.
        
        Format: BUY XAUUSDm 5180 SL=5165 TP1=5210 [TP2=5230] [TP3=5250] 
                [score=85] [tf=M15] [setup=Breakout] [analysis=text] [tier=vip]
        """
        parts = args.split()
        if len(parts) < 4:
            return None
        
        try:
            direction = SignalDirection(parts[0].upper())
            symbol = parts[1]
            entry = float(parts[2])
        except (ValueError, IndexError):
            return None
        
        # Parse key=value pairs
        kv = {}
        for p in parts[3:]:
            if "=" in p:
                k, v = p.split("=", 1)
                kv[k.lower()] = v
        
        if "sl" not in kv or "tp1" not in kv:
            return None
        
        try:
            sl = float(kv["sl"])
            tp1 = float(kv["tp1"])
            tp2 = float(kv.get("tp2", 0)) or None
            tp3 = float(kv.get("tp3", 0)) or None
        except ValueError:
            return None
        
        signal = TradingSignal(
            id=self.tracker.generate_id(),
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            score=int(kv.get("score", 0)),
            confidence=kv.get("conf", "MEDIUM").upper(),
            timeframe=kv.get("tf", "M15"),
            setup_type=kv.get("setup", ""),
            analysis=kv.get("analysis", "").replace("_", " "),
            tier=SignalTier(kv.get("tier", "free")),
        )
        
        return signal


async def run_signal_bot():
    """Run the signal bot standalone."""
    from dotenv import load_dotenv
    load_dotenv()
    
    logging.basicConfig(level=logging.INFO)
    
    token = os.environ.get("SIGNAL_BOT_TOKEN")
    if not token:
        log.error("Set SIGNAL_BOT_TOKEN in .env")
        return
    
    admin_ids = [int(x) for x in os.environ.get("SIGNAL_ADMIN_IDS", "").split(",") if x.strip()]
    free_channel = int(os.environ.get("SIGNAL_FREE_CHANNEL", "0")) or None
    vip_channel = int(os.environ.get("SIGNAL_VIP_CHANNEL", "0")) or None
    
    bot = SignalBot(
        token=token,
        admin_ids=admin_ids,
        free_channel_id=free_channel,
        vip_channel_id=vip_channel,
    )
    
    await bot.start()
    
    try:
        # Keep running
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(run_signal_bot())
