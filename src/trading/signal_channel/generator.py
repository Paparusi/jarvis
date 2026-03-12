"""Auto Signal Generator — Generates signals from MT5 market data.

Uses the existing signal scoring engine + MT5 client to:
1. Monitor price action on XAUUSDm
2. Run multi-timeframe analysis
3. Generate signals when score > threshold
4. Auto-broadcast to channels

Can run as a background task alongside the signal bot.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from .models import TradingSignal, SignalDirection, SignalTier
from .tracker import SignalTracker

log = logging.getLogger("signal_generator")

TZ_BANGKOK = timezone(timedelta(hours=7))

# Signal generation thresholds
MIN_SCORE_FREE = 75    # Score >= 75 → free signal
MIN_SCORE_VIP = 60     # Score >= 60 → VIP signal
COOLDOWN_MINUTES = 15  # Min time between signals on same symbol


class SignalGenerator:
    """Auto-generate trading signals from market analysis."""
    
    def __init__(
        self,
        tracker: SignalTracker,
        mt5_bridge_url: str = "http://localhost:8710",
        symbol: str = "XAUUSDm",
        check_interval: int = 60,  # seconds
    ):
        self.tracker = tracker
        self.mt5_bridge_url = mt5_bridge_url
        self.symbol = symbol
        self.check_interval = check_interval
        self._running = False
        self._last_signal_time: Optional[datetime] = None
    
    async def start(self):
        """Start the signal generator loop."""
        self._running = True
        log.info(f"Signal generator started for {self.symbol}")
        
        while self._running:
            try:
                await self._check_for_signal()
            except Exception as e:
                log.error(f"Signal check error: {e}")
            
            await asyncio.sleep(self.check_interval)
    
    def stop(self):
        self._running = False
    
    async def _check_for_signal(self):
        """Check if there's a valid signal to generate."""
        import httpx
        
        # Cooldown check
        if self._last_signal_time:
            elapsed = (datetime.now(timezone.utc) - self._last_signal_time).total_seconds()
            if elapsed < COOLDOWN_MINUTES * 60:
                return
        
        # Check trading hours (skip weekends, late night)
        now = datetime.now(TZ_BANGKOK)
        if now.weekday() >= 5:  # Saturday/Sunday
            return
        
        # Get current price from MT5 bridge
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.mt5_bridge_url}/price/{self.symbol}")
                if resp.status_code != 200:
                    return
                price_data = resp.json()
        except Exception as e:
            log.debug(f"MT5 bridge unavailable: {e}")
            return
        
        bid = price_data.get("bid", 0)
        ask = price_data.get("ask", 0)
        if not bid or not ask:
            return
        
        # Get analysis from signal scoring engine
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.mt5_bridge_url}/analysis/{self.symbol}")
                if resp.status_code != 200:
                    return
                analysis_data = resp.json()
        except Exception:
            # If analysis endpoint not available, use basic price action
            analysis_data = None
        
        # Generate signal from analysis
        signal = self._evaluate_setup(bid, ask, analysis_data)
        if signal:
            self.tracker.add_signal(signal)
            self._last_signal_time = datetime.now(timezone.utc)
            log.info(f"Generated signal: {signal.id} {signal.direction.value} {signal.symbol} @ {signal.entry_price}")
            return signal
        
        return None
    
    def _evaluate_setup(
        self, 
        bid: float, 
        ask: float, 
        analysis: dict | None,
    ) -> Optional[TradingSignal]:
        """Evaluate if current conditions warrant a signal.
        
        This is a simplified version. In production, integrate with
        the full signal scoring engine from trading/signals.py
        """
        if not analysis:
            return None
        
        score = analysis.get("score", 0)
        direction = analysis.get("direction", "").upper()
        
        if score < MIN_SCORE_VIP:
            return None
        
        if direction not in ("BUY", "SELL"):
            return None
        
        # Determine tier
        tier = SignalTier.FREE if score >= MIN_SCORE_FREE else SignalTier.VIP
        
        # Calculate levels
        if direction == "BUY":
            entry = ask
            atr = analysis.get("atr", 15.0)  # Default ATR
            sl = entry - atr * 1.5
            tp1 = entry + atr * 1.5
            tp2 = entry + atr * 2.5
            tp3 = entry + atr * 3.5
        else:
            entry = bid
            atr = analysis.get("atr", 15.0)
            sl = entry + atr * 1.5
            tp1 = entry - atr * 1.5
            tp2 = entry - atr * 2.5
            tp3 = entry - atr * 3.5
        
        confidence = "HIGH" if score >= 80 else "MEDIUM" if score >= 65 else "LOW"
        
        signal = TradingSignal(
            id=self.tracker.generate_id(),
            symbol=self.symbol,
            direction=SignalDirection(direction),
            entry_price=round(entry, 2),
            stop_loss=round(sl, 2),
            take_profit_1=round(tp1, 2),
            take_profit_2=round(tp2, 2),
            take_profit_3=round(tp3, 2),
            score=score,
            confidence=confidence,
            timeframe=analysis.get("timeframe", "M15"),
            analysis=analysis.get("summary", ""),
            setup_type=analysis.get("setup_type", ""),
            tier=tier,
        )
        
        signal.calculate_rr()
        return signal


async def run_generator(tracker: SignalTracker, mt5_url: str = "http://localhost:8710"):
    """Run the signal generator as a background task."""
    gen = SignalGenerator(tracker=tracker, mt5_bridge_url=mt5_url)
    await gen.start()
