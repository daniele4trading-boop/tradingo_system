"""
Risk Manager - Validates trades and manages portfolio risk.

Rules:
- Max 1% risk per trade
- Max 3% total exposure
- Max 5% daily loss
- Position sizing based on account balance
- Stop loss validation (min 20 pips)
"""

import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from agents_trading.exness.strategy_executor import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class RiskValidation:
    """Result of risk validation."""
    valid: bool
    reason: str = ""
    max_size: float = 0.0
    adjusted_size: float = 0.0
    risk_pcts: Dict = None  # breakdown of risk

    def __post_init__(self):
        if self.risk_pcts is None:
            self.risk_pcts = {}


class RiskManager:
    """Manages portfolio risk and position validation."""

    def __init__(
        self,
        account_balance: float,
        risk_per_trade_pct: float = 1.0,
        max_daily_loss_pct: float = 5.0,
        max_open_positions: int = 3,
        min_sl_pips: float = 20.0,
    ):
        """
        Initialize risk manager.

        Args:
            account_balance: Starting account balance
            risk_per_trade_pct: % of account to risk per trade
            max_daily_loss_pct: Max daily loss before halt
            max_open_positions: Max concurrent open trades
            min_sl_pips: Minimum stop loss distance in pips
        """
        self.account_balance = account_balance
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_open_positions = max_open_positions
        self.min_sl_pips = min_sl_pips

        self.current_balance = account_balance
        self.daily_loss = 0.0
        self.daily_profit = 0.0
        self.open_positions_count = 0
        self.open_positions_exposure = 0.0  # % of balance

    def validate_signal(self, signal: TradeSignal) -> RiskValidation:
        """
        Validate trade signal against risk rules.

        Returns:
            RiskValidation with validation result and recommendations
        """
        reasons = []

        # 1. Check daily loss limit
        daily_loss_ratio = abs(self.daily_loss) / self.account_balance * 100
        if daily_loss_ratio > self.max_daily_loss_pct:
            reasons.append(
                f"Daily loss limit exceeded: {daily_loss_ratio:.2f}% > {self.max_daily_loss_pct}%"
            )

        # 2. Check max open positions
        if self.open_positions_count >= self.max_open_positions:
            reasons.append(
                f"Max open positions ({self.max_open_positions}) reached"
            )

        # 3. Validate stop loss
        sl_distance = abs(signal.entry_price - signal.stop_loss)
        sl_pips = sl_distance * 10000  # Convert to pips (assuming 4 decimals)

        if sl_pips < self.min_sl_pips:
            reasons.append(
                f"Stop loss too tight: {sl_pips:.1f} pips < {self.min_sl_pips} pips"
            )

        # 4. Calculate position size based on risk
        risk_amount = self.account_balance * (self.risk_per_trade_pct / 100)
        max_size = risk_amount / (signal.entry_price - signal.stop_loss) \
            if signal.entry_price != signal.stop_loss else 0.1

        # Adjust for micro-lot minimum
        adjusted_size = max(0.01, min(signal.position_size, max_size))

        # 5. Check exposure limits
        new_exposure = self.open_positions_exposure + (adjusted_size * signal.entry_price / self.account_balance * 100)
        if new_exposure > 30.0:  # Max 30% exposure
            reasons.append(
                f"Total exposure would exceed 30%: {new_exposure:.1f}%"
            )

        # 6. Check confidence threshold
        if signal.confidence < 0.55:
            reasons.append(f"Confidence too low: {signal.confidence:.2f} < 0.55")

        # Build response
        valid = len(reasons) == 0
        reason_str = " | ".join(reasons) if reasons else "✅ All checks passed"

        return RiskValidation(
            valid=valid,
            reason=reason_str,
            max_size=max_size,
            adjusted_size=adjusted_size,
            risk_pcts={
                "daily_loss_pct": daily_loss_ratio,
                "exposure_pct": self.open_positions_exposure,
                "signal_confidence": signal.confidence,
                "sl_pips": sl_pips,
            }
        )

    def on_trade_opened(self, entry_price: float, volume: float, exposure_pct: float):
        """Record new open position."""
        self.open_positions_count += 1
        self.open_positions_exposure += exposure_pct
        logger.info(
            f"Position opened | Count: {self.open_positions_count} | "
            f"Exposure: {self.open_positions_exposure:.1f}%"
        )

    def on_trade_closed(self, pnl: float, exposure_pct: float):
        """Record closed position and update daily P&L."""
        self.open_positions_count = max(0, self.open_positions_count - 1)
        self.open_positions_exposure = max(0, self.open_positions_exposure - exposure_pct)
        self.current_balance += pnl

        if pnl >= 0:
            self.daily_profit += pnl
        else:
            self.daily_loss += pnl

        logger.info(
            f"Position closed | PnL: {pnl:+.2f} | "
            f"Daily: {self.daily_profit:+.2f} | "
            f"Open: {self.open_positions_count}"
        )

    def reset_daily_stats(self):
        """Reset daily statistics (call at EOD)."""
        logger.info(
            f"Daily reset | Profit: {self.daily_profit:+.2f} | "
            f"Loss: {self.daily_loss:+.2f} | "
            f"Balance: {self.current_balance:.2f}"
        )
        self.daily_loss = 0.0
        self.daily_profit = 0.0

    def get_position_limit(self) -> Tuple[int, str]:
        """Get remaining position limit."""
        remaining = self.max_open_positions - self.open_positions_count
        reason = f"{self.open_positions_count}/{self.max_open_positions} positions open"
        return remaining, reason

    def get_daily_loss_limit(self) -> Tuple[float, str]:
        """Get remaining daily loss allowance."""
        max_daily_loss = self.account_balance * (self.max_daily_loss_pct / 100)
        remaining = max_daily_loss - abs(self.daily_loss)
        pct_used = abs(self.daily_loss) / max_daily_loss * 100 if max_daily_loss > 0 else 0

        reason = f"{pct_used:.1f}% of daily limit used ({abs(self.daily_loss):.2f} / {max_daily_loss:.2f})"
        return remaining, reason

    def get_status(self) -> Dict:
        """Get complete risk status."""
        return {
            "account_balance": self.current_balance,
            "daily_profit": self.daily_profit,
            "daily_loss": self.daily_loss,
            "open_positions": self.open_positions_count,
            "exposure_pct": self.open_positions_exposure,
            "max_positions": self.max_open_positions,
            "risk_per_trade": self.risk_per_trade_pct,
            "max_daily_loss": self.max_daily_loss_pct,
        }
