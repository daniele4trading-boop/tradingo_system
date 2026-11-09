"""
Risk Manager - Validate and enforce risk limits.

Checks:
- Daily loss limit (5% of balance)
- Max concurrent positions (3)
- Minimum stop loss (20 pips)
- Position sizing (1% risk per trade)
"""

import logging
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class RiskValidation:
    """Risk validation result."""
    valid: bool
    reason: str
    max_size: float
    adjusted_size: float
    risk_breakdown: dict = field(default_factory=dict)


class RiskManager:
    """Validate and manage trading risk."""

    def __init__(
        self,
        account_balance: float = 1000.0,
        risk_per_trade_pct: float = 1.0,
        max_open_positions: int = 3,
        daily_loss_limit_pct: float = 5.0,
        min_stop_loss_pips: float = 20.0,
    ):
        """
        Initialize risk manager.

        Args:
            account_balance: Starting balance
            risk_per_trade_pct: % of balance to risk per trade
            max_open_positions: Max concurrent positions
            daily_loss_limit_pct: Max daily loss as % of balance
            min_stop_loss_pips: Minimum stop loss in pips (0.1$ increments)
        """
        self.account_balance = account_balance
        self.current_balance = account_balance
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_open_positions = max_open_positions
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.min_stop_loss_pips = min_stop_loss_pips

        # Daily tracking
        self.daily_start_balance = account_balance
        self.daily_pnl = 0.0
        self.last_reset = datetime.now()

        # Position tracking
        self.open_positions = []

    def validate_signal(
        self,
        signal,  # TradeSignal
        num_open_positions: int = 0,
    ) -> RiskValidation:
        """
        Validate trade signal against risk limits.

        Args:
            signal: TradeSignal from StrategyExecutor
            num_open_positions: Current count of open positions

        Returns:
            RiskValidation result
        """
        try:
            # Reset daily PnL if new day
            self._check_daily_reset()

            # 1. Check max positions
            if num_open_positions >= self.max_open_positions:
                return RiskValidation(
                    valid=False,
                    reason=f"Max open positions ({self.max_open_positions}) reached",
                    max_size=0,
                    adjusted_size=0,
                    risk_breakdown={"open_positions": num_open_positions}
                )

            # 2. Check daily loss limit
            daily_loss_pct = abs(self.daily_pnl) / self.daily_start_balance * 100
            if self.daily_pnl < 0 and daily_loss_pct >= self.daily_loss_limit_pct:
                return RiskValidation(
                    valid=False,
                    reason=f"Daily loss limit reached ({daily_loss_pct:.1f}% of balance)",
                    max_size=0,
                    adjusted_size=0,
                    risk_breakdown={"daily_pnl": self.daily_pnl, "daily_loss_limit_pct": self.daily_loss_limit_pct}
                )

            # 3. Check minimum stop loss
            risk_pts = abs(signal.entry_price - signal.stop_loss)
            if risk_pts < self.min_stop_loss_pips * 0.1:  # Convert pips to $
                return RiskValidation(
                    valid=False,
                    reason=f"Stop loss too tight ({risk_pts*10:.0f} pips < {self.min_stop_loss_pips:.0f} min)",
                    max_size=0,
                    adjusted_size=signal.position_size,
                    risk_breakdown={"stop_loss_pips": risk_pts * 10}
                )

            # 4. Calculate max position size based on risk
            risk_amount = self.current_balance * (self.risk_per_trade_pct / 100)
            risk_pts_value = risk_pts * 100  # $ value per pip for 1 lot

            if risk_pts_value > 0:
                max_size = risk_amount / risk_pts_value
            else:
                max_size = 0

            # 5. Validate position size
            adjusted_size = min(signal.position_size, max_size)
            if adjusted_size < 0.01:  # Min 0.01 lot
                return RiskValidation(
                    valid=False,
                    reason=f"Position size too small after risk adjustment ({adjusted_size:.3f} lots < 0.01 min)",
                    max_size=max_size,
                    adjusted_size=adjusted_size,
                    risk_breakdown={"risk_per_trade_pct": self.risk_per_trade_pct}
                )

            # All checks passed
            exposure_pct = (adjusted_size * 100) / self.current_balance * 100  # Rough estimation

            return RiskValidation(
                valid=True,
                reason="OK",
                max_size=max_size,
                adjusted_size=adjusted_size,
                risk_breakdown={
                    "position_size": adjusted_size,
                    "risk_amount": risk_amount,
                    "daily_pnl": self.daily_pnl,
                    "daily_loss_limit_remaining": (self.daily_start_balance * self.daily_loss_limit_pct / 100) - abs(self.daily_pnl),
                    "open_positions": num_open_positions,
                    "exposure_pct": exposure_pct,
                }
            )

        except Exception as e:
            logger.error(f"Error validating signal: {e}")
            return RiskValidation(
                valid=False,
                reason=f"Validation error: {str(e)}",
                max_size=0,
                adjusted_size=0,
            )

    def on_trade_opened(
        self,
        entry_price: float,
        volume: float,
        exposure_pct: float = 1.0,
    ):
        """Record position opening."""
        self.open_positions.append({
            "entry_price": entry_price,
            "volume": volume,
            "opened_at": datetime.now(),
        })
        logger.info(f"Position opened: {volume:.2f}L @ {entry_price:.2f}")

    def on_trade_closed(self, pnl: float):
        """Record position closing and update balance."""
        self.current_balance += pnl
        self.daily_pnl += pnl
        if self.open_positions:
            self.open_positions.pop()
        logger.info(f"Position closed: P&L {pnl:+.2f}$ | Daily: {self.daily_pnl:+.2f}$")

    def _check_daily_reset(self):
        """Reset daily counters if new UTC day."""
        now = datetime.now()
        if (now - self.last_reset).days >= 1:
            self.daily_start_balance = self.current_balance
            self.daily_pnl = 0.0
            self.last_reset = now
            logger.info("Daily PnL reset")
