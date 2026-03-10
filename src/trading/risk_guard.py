"""RiskGuard — Circuit breaker with veto power over all trading actions.

The LLM can NEVER bypass this module. Every entry must pass all risk checks
before execution. This is the final safety gate in the trading pipeline.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.utils.logging import get_logger

log = get_logger("trading.risk_guard")


@dataclass
class RiskConfig:
    """Risk management configuration. All limits are hard ceilings."""

    # Per-trade
    max_risk_per_trade_pct: float = 1.0
    max_lot_size: float = 0.1
    min_rr_ratio: float = 1.5
    # Portfolio
    max_open_positions: int = 3
    max_same_direction: int = 2
    max_correlated_exposure_pct: float = 3.0
    # Daily
    max_daily_loss_pct: float = 3.0
    max_daily_trades: int = 5
    max_consecutive_losses: int = 3
    # Session
    allowed_sessions: list[str] = field(
        default_factory=lambda: ["London", "New York", "London+NY"]
    )
    kill_zone_only: bool = False
    # News
    stop_before_high_impact_mins: int = 15
    flatten_before_news: bool = True
    # Emergency
    max_weekly_loss_pct: float = 8.0
    kill_switch: bool = False


@dataclass
class RiskState:
    """Mutable state tracking daily/weekly risk metrics."""

    date: str = ""  # YYYY-MM-DD
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    daily_trades: int = 0
    consecutive_losses: int = 0
    weekly_pnl: float = 0.0
    weekly_pnl_pct: float = 0.0
    open_risk_pct: float = 0.0


@dataclass
class VetoResult:
    """Result of a risk check. approved=False means the trade is blocked."""

    approved: bool
    reason: str = ""  # Why vetoed
    rule: str = ""  # Which rule triggered ("max_daily_loss", "kill_switch", etc.)


class RiskGuard:
    """Circuit breaker with veto power. NEVER bypassed by LLM."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self.state = RiskState(date=str(date.today()))

    def check_entry(
        self,
        risk_pct: float,
        lot_size: float,
        rr_ratio: float,
        direction: str,  # "buy" / "sell"
        session: str,  # Current session name
        open_positions: list[dict],  # [{ticket, type, volume, ...}]
        calendar_warnings: list[str],
    ) -> VetoResult:
        """Check ALL rules before allowing entry.

        Rules checked in order (first failure = veto):
        1.  kill_switch
        2.  max_weekly_loss_pct
        3.  max_daily_loss_pct
        4.  max_consecutive_losses
        5.  max_daily_trades
        6.  max_risk_per_trade_pct
        7.  max_lot_size
        8.  min_rr_ratio
        9.  max_open_positions
        10. max_same_direction
        11. max_correlated_exposure_pct
        12. allowed_sessions
        13. stop_before_high_impact (news)
        """
        cfg = self.config
        st = self.state

        # Auto-reset if date changed
        today = str(date.today())
        if st.date and st.date != today:
            self.reset_daily()

        # 1. Kill switch
        if cfg.kill_switch:
            return VetoResult(
                approved=False,
                reason="Kill switch is active — all trading halted",
                rule="kill_switch",
            )

        # 2. Weekly loss limit
        if st.weekly_pnl_pct <= -cfg.max_weekly_loss_pct:
            return VetoResult(
                approved=False,
                reason=f"Weekly loss {st.weekly_pnl_pct:.2f}% exceeds limit -{cfg.max_weekly_loss_pct}%",
                rule="max_weekly_loss",
            )

        # 3. Daily loss limit
        if st.daily_pnl_pct <= -cfg.max_daily_loss_pct:
            return VetoResult(
                approved=False,
                reason=f"Daily loss {st.daily_pnl_pct:.2f}% exceeds limit -{cfg.max_daily_loss_pct}%",
                rule="max_daily_loss",
            )

        # 4. Consecutive losses
        if st.consecutive_losses >= cfg.max_consecutive_losses:
            return VetoResult(
                approved=False,
                reason=f"{st.consecutive_losses} consecutive losses (max {cfg.max_consecutive_losses})",
                rule="max_consecutive_losses",
            )

        # 5. Daily trade count
        if st.daily_trades >= cfg.max_daily_trades:
            return VetoResult(
                approved=False,
                reason=f"Daily trades {st.daily_trades} reached limit {cfg.max_daily_trades}",
                rule="max_daily_trades",
            )

        # 6. Risk per trade
        if risk_pct > cfg.max_risk_per_trade_pct:
            return VetoResult(
                approved=False,
                reason=f"Risk {risk_pct:.2f}% exceeds max {cfg.max_risk_per_trade_pct}% per trade",
                rule="max_risk_per_trade",
            )

        # 7. Lot size
        if lot_size > cfg.max_lot_size:
            return VetoResult(
                approved=False,
                reason=f"Lot size {lot_size} exceeds max {cfg.max_lot_size}",
                rule="max_lot_size",
            )

        # 8. R:R ratio
        if rr_ratio < cfg.min_rr_ratio:
            return VetoResult(
                approved=False,
                reason=f"R:R ratio {rr_ratio:.2f} below min {cfg.min_rr_ratio}",
                rule="min_rr_ratio",
            )

        # 9. Max open positions
        if len(open_positions) >= cfg.max_open_positions:
            return VetoResult(
                approved=False,
                reason=f"{len(open_positions)} open positions (max {cfg.max_open_positions})",
                rule="max_open_positions",
            )

        # 10. Same direction limit
        direction_lower = direction.lower()
        # type=0 → buy, type=1 → sell
        same_count = sum(
            1
            for p in open_positions
            if (p.get("type") == 0 and direction_lower == "buy")
            or (p.get("type") == 1 and direction_lower == "sell")
        )
        if same_count >= cfg.max_same_direction:
            return VetoResult(
                approved=False,
                reason=f"{same_count} {direction_lower} positions already open (max {cfg.max_same_direction})",
                rule="max_same_direction",
            )

        # 11. Correlated exposure
        total_risk = st.open_risk_pct + risk_pct
        if total_risk > cfg.max_correlated_exposure_pct:
            return VetoResult(
                approved=False,
                reason=f"Total exposure {total_risk:.2f}% exceeds max {cfg.max_correlated_exposure_pct}%",
                rule="max_correlated_exposure",
            )

        # 12. Allowed sessions
        if session not in cfg.allowed_sessions:
            return VetoResult(
                approved=False,
                reason=f"Session '{session}' not in allowed sessions {cfg.allowed_sessions}",
                rule="allowed_sessions",
            )

        # 13. High-impact news
        if calendar_warnings:
            return VetoResult(
                approved=False,
                reason=f"High-impact news: {', '.join(calendar_warnings)}",
                rule="stop_before_high_impact",
            )

        log.info(
            "entry_approved",
            risk_pct=risk_pct,
            lot_size=lot_size,
            rr_ratio=rr_ratio,
            direction=direction,
            session=session,
        )
        return VetoResult(approved=True)

    def record_trade_result(self, profit: float, equity: float) -> None:
        """Update state after a trade closes."""
        # Auto-reset if date changed
        today = str(date.today())
        if self.state.date and self.state.date != today:
            self.reset_daily()

        # Consecutive losses tracking
        if profit < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        # Daily metrics
        self.state.daily_pnl += profit
        self.state.daily_trades += 1
        if equity > 0:
            self.state.daily_pnl_pct = self.state.daily_pnl / equity * 100

        # Weekly metrics
        self.state.weekly_pnl += profit
        if equity > 0:
            self.state.weekly_pnl_pct = self.state.weekly_pnl / equity * 100

        log.info(
            "trade_recorded",
            profit=profit,
            equity=equity,
            daily_pnl=self.state.daily_pnl,
            consecutive_losses=self.state.consecutive_losses,
        )

    def update_open_risk(self, open_positions: list[dict], equity: float) -> None:
        """Recalculate open_risk_pct from current positions.

        Simple approximation for XAUUSD:
        risk = sum(volume * 0.01 / equity * 100 * 100) for each position
        i.e. each 0.01 lot risks approximately $1 per pip, assume ~100 pip SL
        """
        if equity <= 0:
            self.state.open_risk_pct = 0.0
            return

        total_risk = 0.0
        for pos in open_positions:
            volume = pos.get("volume", 0.0)
            # Approximate: volume * pip_value * assumed_sl_pips / equity * 100
            # Simplified: volume * 0.01 / equity * 100 * 100
            total_risk += volume * 0.01 / equity * 100 * 100

        self.state.open_risk_pct = round(total_risk, 4)

    def should_flatten_for_news(self, calendar_warnings: list[str]) -> bool:
        """Return True if flatten_before_news is enabled and warnings exist."""
        return self.config.flatten_before_news and len(calendar_warnings) > 0

    def reset_daily(self) -> None:
        """Reset daily counters. Keep weekly and consecutive_losses."""
        self.state.date = str(date.today())
        self.state.daily_pnl = 0.0
        self.state.daily_pnl_pct = 0.0
        self.state.daily_trades = 0

    def reset_weekly(self) -> None:
        """Reset weekly counters."""
        self.state.weekly_pnl = 0.0
        self.state.weekly_pnl_pct = 0.0

    def get_status(self) -> dict[str, Any]:
        """Return current state + config as dict."""
        return {
            "state": asdict(self.state),
            "config": asdict(self.config),
        }

    def update_config(self, **kwargs: Any) -> None:
        """Update config params. E.g., update_config(max_daily_loss_pct=5.0)"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
                log.info("config_updated", param=key, value=value)
            else:
                log.warning("config_unknown_param", param=key)

    def save_state(self, persistence) -> None:
        """Persist current risk state to DB."""
        persistence.save_risk_state({
            "daily_pnl": self.state.daily_pnl,
            "daily_pnl_pct": self.state.daily_pnl_pct,
            "daily_trades": float(self.state.daily_trades),
            "weekly_pnl": self.state.weekly_pnl,
            "weekly_pnl_pct": self.state.weekly_pnl_pct,
            "consecutive_losses": float(self.state.consecutive_losses),
        })

    def load_state(self, persistence) -> None:
        """Load risk state from DB."""
        data = persistence.load_risk_state()
        if not data:
            return
        self.state.weekly_pnl = data.get("weekly_pnl", 0.0)
        self.state.weekly_pnl_pct = data.get("weekly_pnl_pct", 0.0)
        self.state.consecutive_losses = int(data.get("consecutive_losses", 0))
        self.state.daily_pnl = data.get("daily_pnl", 0.0)
        self.state.daily_pnl_pct = data.get("daily_pnl_pct", 0.0)
        self.state.daily_trades = int(data.get("daily_trades", 0))

    @staticmethod
    def load_config(path: str = "config/risk.json") -> RiskConfig:
        """Load RiskConfig from JSON file. Falls back to defaults if file doesn't exist."""
        p = Path(path)
        if not p.exists():
            log.info("config_not_found_using_defaults", path=path)
            return RiskConfig()

        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            config = RiskConfig()
            for key, value in data.items():
                if hasattr(config, key):
                    setattr(config, key, value)
            log.info("config_loaded", path=path)
            return config
        except (json.JSONDecodeError, OSError) as e:
            log.error("config_load_error", path=path, error=str(e))
            return RiskConfig()

    @staticmethod
    def save_config(config: RiskConfig, path: str = "config/risk.json") -> None:
        """Save RiskConfig to JSON file. Creates directory if needed."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(asdict(config), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("config_saved", path=path)
