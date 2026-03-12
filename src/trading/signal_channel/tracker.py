"""Signal Performance Tracker — Track and persist signal results."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .models import (
    TradingSignal, SignalDirection, SignalStatus,
    SignalPerformance, SignalTier,
)


TZ_BANGKOK = timezone(timedelta(hours=7))

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "signals"


class SignalTracker:
    """Track all signals and their performance."""
    
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.signals: dict[str, TradingSignal] = {}
        self.performance = SignalPerformance()
        self._counter = 0
        self._load()
    
    def _signals_file(self) -> Path:
        return self.data_dir / "signals.json"
    
    def _perf_file(self) -> Path:
        return self.data_dir / "performance.json"
    
    def _load(self):
        """Load signals and performance from disk."""
        sf = self._signals_file()
        if sf.exists():
            try:
                data = json.loads(sf.read_text())
                for sid, s in data.items():
                    self.signals[sid] = self._dict_to_signal(s)
                # Set counter from last ID
                if self.signals:
                    nums = [int(s.id.replace("SIG", "")) for s in self.signals.values() 
                            if s.id.startswith("SIG")]
                    self._counter = max(nums) if nums else 0
            except Exception:
                pass
        
        pf = self._perf_file()
        if pf.exists():
            try:
                data = json.loads(pf.read_text())
                self.performance = SignalPerformance(**data)
            except Exception:
                pass
    
    def _save(self):
        """Persist to disk."""
        # Save signals (only last 200)
        recent = dict(list(self.signals.items())[-200:])
        data = {sid: self._signal_to_dict(s) for sid, s in recent.items()}
        self._signals_file().write_text(json.dumps(data, indent=2, default=str))
        
        # Save performance
        self._perf_file().write_text(json.dumps({
            "total_signals": self.performance.total_signals,
            "wins": self.performance.wins,
            "losses": self.performance.losses,
            "breakeven": self.performance.breakeven,
            "active": self.performance.active,
            "total_pips": self.performance.total_pips,
            "total_pnl_usd": self.performance.total_pnl_usd,
            "best_trade_pips": self.performance.best_trade_pips,
            "worst_trade_pips": self.performance.worst_trade_pips,
            "win_streak": self.performance.win_streak,
            "current_streak": self.performance.current_streak,
            "max_win_streak": self.performance.max_win_streak,
        }, indent=2))
    
    def generate_id(self) -> str:
        """Generate next signal ID."""
        self._counter += 1
        return f"SIG{self._counter:04d}"
    
    def add_signal(self, signal: TradingSignal) -> TradingSignal:
        """Register a new signal."""
        signal.calculate_rr()
        self.signals[signal.id] = signal
        self.performance.total_signals += 1
        self.performance.active += 1
        self._save()
        return signal
    
    def update_signal(
        self, 
        signal_id: str, 
        status: SignalStatus,
        close_price: Optional[float] = None,
    ) -> Optional[TradingSignal]:
        """Update signal status (TP hit, SL hit, etc.)."""
        signal = self.signals.get(signal_id)
        if not signal:
            return None
        
        signal.status = status
        signal.closed_at = datetime.now(timezone.utc)
        
        if close_price is not None:
            signal.close_price = close_price
            
            # Calculate PnL in pips (gold: 1 pip = $0.1)
            if signal.direction == SignalDirection.BUY:
                signal.pnl_pips = (close_price - signal.entry_price) * 10
            else:
                signal.pnl_pips = (signal.entry_price - close_price) * 10
            
            # Estimate USD PnL (0.01 lot gold ≈ $0.01 per pip)
            signal.pnl_usd = signal.pnl_pips * signal.lot_size * 100
        
        # Update performance
        if status in (SignalStatus.TP1_HIT, SignalStatus.TP2_HIT, SignalStatus.TP3_HIT):
            self.performance.wins += 1
            self.performance.active = max(0, self.performance.active - 1)
            self.performance.current_streak = max(0, self.performance.current_streak) + 1
            self.performance.max_win_streak = max(
                self.performance.max_win_streak, self.performance.current_streak
            )
            if signal.pnl_pips and signal.pnl_pips > self.performance.best_trade_pips:
                self.performance.best_trade_pips = signal.pnl_pips
        elif status == SignalStatus.SL_HIT:
            self.performance.losses += 1
            self.performance.active = max(0, self.performance.active - 1)
            self.performance.current_streak = min(0, self.performance.current_streak) - 1
            if signal.pnl_pips and signal.pnl_pips < self.performance.worst_trade_pips:
                self.performance.worst_trade_pips = signal.pnl_pips
        elif status in (SignalStatus.CLOSED, SignalStatus.CANCELLED):
            self.performance.active = max(0, self.performance.active - 1)
            if signal.pnl_pips and abs(signal.pnl_pips) < 5:
                self.performance.breakeven += 1
        
        if signal.pnl_pips:
            self.performance.total_pips += signal.pnl_pips
        if signal.pnl_usd:
            self.performance.total_pnl_usd += signal.pnl_usd
        
        self._save()
        return signal
    
    def get_active_signals(self) -> list[TradingSignal]:
        """Get all active signals."""
        return [s for s in self.signals.values() if s.status == SignalStatus.ACTIVE]
    
    def get_today_signals(self) -> list[TradingSignal]:
        """Get today's signals."""
        today = datetime.now(TZ_BANGKOK).date()
        return [
            s for s in self.signals.values()
            if s.created_at.astimezone(TZ_BANGKOK).date() == today
        ]
    
    def get_today_performance(self) -> SignalPerformance:
        """Calculate today's performance only."""
        today_signals = self.get_today_signals()
        perf = SignalPerformance()
        perf.total_signals = len(today_signals)
        
        for s in today_signals:
            if s.status == SignalStatus.ACTIVE:
                perf.active += 1
            elif s.status in (SignalStatus.TP1_HIT, SignalStatus.TP2_HIT, SignalStatus.TP3_HIT):
                perf.wins += 1
                if s.pnl_pips and s.pnl_pips > perf.best_trade_pips:
                    perf.best_trade_pips = s.pnl_pips
            elif s.status == SignalStatus.SL_HIT:
                perf.losses += 1
                if s.pnl_pips and s.pnl_pips < perf.worst_trade_pips:
                    perf.worst_trade_pips = s.pnl_pips
            
            if s.pnl_pips:
                perf.total_pips += s.pnl_pips
            if s.pnl_usd:
                perf.total_pnl_usd += s.pnl_usd
        
        return perf
    
    @staticmethod
    def _signal_to_dict(s: TradingSignal) -> dict:
        return {
            "id": s.id,
            "symbol": s.symbol,
            "direction": s.direction.value,
            "entry_price": s.entry_price,
            "stop_loss": s.stop_loss,
            "take_profit_1": s.take_profit_1,
            "take_profit_2": s.take_profit_2,
            "take_profit_3": s.take_profit_3,
            "score": s.score,
            "confidence": s.confidence,
            "timeframe": s.timeframe,
            "analysis": s.analysis,
            "setup_type": s.setup_type,
            "risk_reward": s.risk_reward,
            "lot_size": s.lot_size,
            "risk_pct": s.risk_pct,
            "tier": s.tier.value,
            "status": s.status.value,
            "created_at": s.created_at.isoformat(),
            "triggered_at": s.triggered_at.isoformat() if s.triggered_at else None,
            "closed_at": s.closed_at.isoformat() if s.closed_at else None,
            "close_price": s.close_price,
            "pnl_pips": s.pnl_pips,
            "pnl_usd": s.pnl_usd,
        }
    
    @staticmethod
    def _dict_to_signal(d: dict) -> TradingSignal:
        return TradingSignal(
            id=d["id"],
            symbol=d["symbol"],
            direction=SignalDirection(d["direction"]),
            entry_price=d["entry_price"],
            stop_loss=d["stop_loss"],
            take_profit_1=d["take_profit_1"],
            take_profit_2=d.get("take_profit_2"),
            take_profit_3=d.get("take_profit_3"),
            score=d.get("score", 0),
            confidence=d.get("confidence", "MEDIUM"),
            timeframe=d.get("timeframe", "M15"),
            analysis=d.get("analysis", ""),
            setup_type=d.get("setup_type", ""),
            risk_reward=d.get("risk_reward", 0.0),
            lot_size=d.get("lot_size", 0.01),
            risk_pct=d.get("risk_pct", 1.0),
            tier=SignalTier(d.get("tier", "free")),
            status=SignalStatus(d.get("status", "active")),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(timezone.utc),
            triggered_at=datetime.fromisoformat(d["triggered_at"]) if d.get("triggered_at") else None,
            closed_at=datetime.fromisoformat(d["closed_at"]) if d.get("closed_at") else None,
            close_price=d.get("close_price"),
            pnl_pips=d.get("pnl_pips"),
            pnl_usd=d.get("pnl_usd"),
        )
