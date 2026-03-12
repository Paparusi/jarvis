#!/usr/bin/env python3
"""Launch the Signal Channel Bot.

Setup:
1. Create a new Telegram bot via @BotFather → get token
2. Create a Telegram channel for free signals
3. Create a Telegram channel for VIP signals  
4. Add the bot as admin to both channels
5. Get channel IDs (forward a message from channel to @userinfobot)

Add to .env:
    SIGNAL_BOT_TOKEN=your_bot_token
    SIGNAL_ADMIN_IDS=1991690969
    SIGNAL_FREE_CHANNEL=-100xxxxxxxxxx
    SIGNAL_VIP_CHANNEL=-100xxxxxxxxxx

Run:
    python scripts/run_signal_bot.py
    
    # Or with the venv:
    .venv/bin/python scripts/run_signal_bot.py

Admin commands (send to the bot directly):
    /signal BUY XAUUSDm 5180 SL=5165 TP1=5210 TP2=5230 score=85 tf=M15 setup=Breakout
    /update SIG0001 TP1_HIT 5210
    /close SIG0001 5205
    /daily
    /broadcast Thông báo: Tạm dừng signals do tin Non-Farm
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from src.trading.signal_channel.bot import run_signal_bot


if __name__ == "__main__":
    print("🤖 JARVIS Signal Bot starting...")
    print("━" * 40)
    print("Setup checklist:")
    print("  1. SIGNAL_BOT_TOKEN in .env")
    print("  2. SIGNAL_ADMIN_IDS in .env") 
    print("  3. SIGNAL_FREE_CHANNEL in .env")
    print("  4. SIGNAL_VIP_CHANNEL in .env (optional)")
    print("━" * 40)
    asyncio.run(run_signal_bot())
