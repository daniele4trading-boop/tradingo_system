"""
Strategy Executor - Convert chart analysis → trading signal.

Maps:
- Chart sentiment → entry direction (BUY/SELL/HOLD)
- Confidence → position size
- Support/resistance → stop loss / take profit
"""

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class TradeAction(Enum):
    """Trade action."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    """Trade signal from strategy."""
    action: TradeAction
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float  # lots
    confidence: float  # 0-1
    risk_reward_ratio: float
    reasoning: str = ""


class StrategyExecutor:
    """Execute trading strategy from chart analysis."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize strategy executor.

        Args:
            api_key: Anthropic API key (unused currently)
        """
        self.api_key = api_key

        # Strategy parameters (from Qlab defaults)
        self.config = {
            "min_confidence": 0.55,
            "min_risk_reward": 1.5,
            "mean_reversion_zscore": 2.0,
            "momentum_breakout_enabled": True,
            "volume_confirmation": True,
            "base_position_size": 0.1,  # lots
            "max_position_size": 0.5,
        }

    def execute_strategy(
        self,
        chart_analysis: Dict[str, Any],
        symbol: str,
        timeframe: str,
        current_price: Dict[str, float],  # {"bid": ..., "ask": ...}
        open_positions: int = 0,
    ) -> Optional[TradeSignal]:
        """
        Generate trade signal from chart analysis.

        Args:
            chart_analysis: ChartAnalysis dict from ChartAnalyst
            symbol: Symbol (e.g., "XAUUSD")
            timeframe: Timeframe (e.g., "H1")
            current_price: Current bid/ask prices
            open_positions: Count of currently open positions

        Returns:
            TradeSignal or None if no setup
        """
        try:
            # Extract key data
            sentiment = chart_analysis.get("sentiment", "NEUTRAL")
            confidence = chart_analysis.get("confidence", 0.5)
            patterns = chart_analysis.get("patterns", [])
            setup = chart_analysis.get("trading_setup", {})
            sr = chart_analysis.get("support_resistance", {})

            # Check confidence threshold
            if confidence < self.config["min_confidence"]:
                logger.debug(f"Confidence {confidence:.2f} below threshold {self.config['min_confidence']}")
                return None

            # Determine action from sentiment
            if sentiment == "BULLISH":
                action = TradeAction.BUY
            elif sentiment == "BEARISH":
                action = TradeAction.SELL
            else:
                return None  # NEUTRAL → no signal

            # Get entry price
            mid_price = (current_price["bid"] + current_price["ask"]) / 2

            # Calculate stop loss and take profit
            if action == TradeAction.BUY:
                support = sr.get("support", [mid_price - 20])[0] if sr.get("support") else mid_price - 20
                resistance = sr.get("resistance", [mid_price + 20])[0] if sr.get("resistance") else mid_price + 20

                stop_loss = support * 0.98  # 2% below support
                take_profit = resistance * 1.02  # 2% above resistance
                entry_price = current_price["ask"]

            else:  # SELL
                support = sr.get("support", [mid_price - 20])[0] if sr.get("support") else mid_price - 20
                resistance = sr.get("resistance", [mid_price + 20])[0] if sr.get("resistance") else mid_price + 20

                stop_loss = resistance * 1.02  # 2% above resistance
                take_profit = support * 0.98  # 2% below support
                entry_price = current_price["bid"]

            # Calculate risk/reward
            risk_pts = abs(entry_price - stop_loss)
            reward_pts = abs(take_profit - entry_price)

            if risk_pts > 0:
                risk_reward = reward_pts / risk_pts
            else:
                risk_reward = 0

            # Check risk/reward threshold
            if risk_reward < self.config["min_risk_reward"]:
                logger.debug(f"R:R {risk_reward:.2f} below threshold {self.config['min_risk_reward']}")
                return None

            # Calculate position size (based on confidence)
            position_size = self.config["base_position_size"] * (confidence / 0.75)
            position_size = min(position_size, self.config["max_position_size"])
            position_size = max(position_size, self.config["base_position_size"] * 0.5)

            # Create signal
            signal = TradeSignal(
                action=action,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size=position_size,
                confidence=confidence,
                risk_reward_ratio=risk_reward,
                reasoning=f"{sentiment} sentiment ({confidence:.0%}) from {setup.get('type', 'pattern')} setup"
            )

            logger.info(f"📊 Signal: {action.value} {position_size:.2f}L @ {entry_price:.2f}")
            logger.info(f"   SL: {stop_loss:.2f} | TP: {take_profit:.2f} | R:R {risk_reward:.2f}")

            return signal

        except Exception as e:
            logger.error(f"Error executing strategy: {e}")
            return None
