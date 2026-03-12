"""Signal data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class SignalDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalStatus(str, Enum):
    ACTIVE = "active"       # Signal is live
    TP1_HIT = "tp1_hit"     # First target hit
    TP2_HIT = "tp2_hit"     # Second target hit  
    TP3_HIT = "tp3_hit"     # Third target hit
    SL_HIT = "sl_hit"       # Stop loss hit
    CLOSED = "closed"       # Manually closed
    EXPIRED = "expired"     # Signal expired without trigger
    CANCELLED = "cancelled" # Signal cancelled before entry


class SignalTier(str, Enum):
    FREE = "free"
    VIP = "vip"


@dataclass
class TradingSignal:
    """A trading signal to broadcast."""
    id: str
    symbol: str
    direction: SignalDirection
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    
    # Analysis
    score: int = 0               # Signal strength 0-100
    confidence: str = "MEDIUM"   # LOW, MEDIUM, HIGH
    timeframe: str = "M15"       # Primary analysis timeframe
    analysis: str = ""           # Brief analysis text
    setup_type: str = ""         # e.g. "Breakout", "Pullback", "Reversal"
    
    # Risk management
    risk_reward: float = 0.0     # R:R ratio
    lot_size: float = 0.01       # Suggested lot size
    risk_pct: float = 1.0        # Risk % of account
    
    # Metadata
    tier: SignalTier = SignalTier.FREE
    status: SignalStatus = SignalStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    triggered_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    pnl_pips: Optional[float] = None
    pnl_usd: Optional[float] = None
    
    @property
    def sl_pips(self) -> float:
        """Distance to SL in pips (for gold, 1 pip = $0.1)."""
        return abs(self.entry_price - self.stop_loss) * 10
    
    @property  
    def tp1_pips(self) -> float:
        """Distance to TP1 in pips."""
        return abs(self.take_profit_1 - self.entry_price) * 10
    
    @property
    def tp2_pips(self) -> Optional[float]:
        if self.take_profit_2 is None:
            return None
        return abs(self.take_profit_2 - self.entry_price) * 10
    
    def calculate_rr(self) -> float:
        """Calculate risk-reward ratio."""
        sl_dist = abs(self.entry_price - self.stop_loss)
        tp_dist = abs(self.take_profit_1 - self.entry_price)
        if sl_dist == 0:
            return 0
        self.risk_reward = round(tp_dist / sl_dist, 2)
        return self.risk_reward


@dataclass 
class SignalPerformance:
    """Track overall signal performance."""
    total_signals: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    active: int = 0
    
    total_pips: float = 0.0
    total_pnl_usd: float = 0.0
    best_trade_pips: float = 0.0
    worst_trade_pips: float = 0.0
    
    win_streak: int = 0
    current_streak: int = 0
    max_win_streak: int = 0
    
    @property
    def win_rate(self) -> float:
        closed = self.wins + self.losses
        if closed == 0:
            return 0.0
        return round(self.wins / closed * 100, 1)
    
    @property
    def avg_pips_per_trade(self) -> float:
        closed = self.wins + self.losses
        if closed == 0:
            return 0.0
        return round(self.total_pips / closed, 1)


@dataclass
class Subscriber:
    """A channel subscriber."""
    user_id: int
    username: Optional[str] = None
    tier: SignalTier = SignalTier.FREE
    joined_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    is_active: bool = True
