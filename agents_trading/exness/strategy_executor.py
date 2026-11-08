"""
Strategy Executor Agent - Decides trades based on chart analysis + Qlab strategies.

Takes:
1. Chart analysis (sentiment, patterns, entry zones)
2. Strategy configuration (risk limits, win rate targets)
3. Position management rules (sizing, stops, TP)

Outputs:
- Trade decision: {action: BUY|SELL|HOLD, entry, sl, tp, size, confidence}
"""

import json
import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum
import anthropic

logger = logging.getLogger(__name__)


class TradeAction(Enum):
    """Trade action types."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    """Trade signal output from executor."""
    action: TradeAction
    symbol: str
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    confidence: float  # 0.0-1.0
    reason: str
    risk_reward_ratio: float
    max_loss_pct: float
    timeframe: str

    def to_dict(self) -> Dict:
        """Convert to dict for serialization."""
        d = asdict(self)
        d["action"] = self.action.value
        return d


class StrategyExecutor:
    """Claude-powered strategy executor for trade decisions."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-opus-5-5",
        strategy_config: Optional[Dict] = None,
    ):
        """
        Initialize executor.

        Args:
            api_key: Anthropic API key
            model: Claude model
            strategy_config: Qlab strategy parameters
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.strategy_config = strategy_config or self._default_strategy_config()
        self.trade_log = []

    def _default_strategy_config(self) -> Dict:
        """Default Qlab-like strategy config."""
        return {
            "name": "Statistical Mean Reversion + Momentum Breakout",
            "asset_classes": ["GOLD", "MAJOR_FX", "INDICES"],
            "position_sizing": {
                "base_risk_pct": 1.0,  # 1% per trade
                "max_position_pct": 3.0,  # 3% max exposure
                "dynamic_sizing": True,
            },
            "risk_management": {
                "max_loss_per_trade": 2.0,  # 2% account risk
                "profit_target_multiple": 2.5,  # Risk:Reward = 1:2.5
                "trailing_stop": False,
            },
            "entry_rules": {
                "mean_reversion": {
                    "enabled": True,
                    "zscore_threshold": 2.0,  # 2-sigma from mean
                    "lookback_periods": 20,
                },
                "momentum_breakout": {
                    "enabled": True,
                    "resistance_break_pips": 10,
                    "volume_confirmation": True,
                },
            },
            "holding_period": {
                "min_minutes": 15,
                "max_minutes": 240,  # 4 hours typical
            },
            "win_rate_target": 0.52,  # 52% win rate target
        }

    def execute_strategy(
        self,
        chart_analysis: Dict,
        symbol: str,
        timeframe: str,
        current_price: Dict,  # {"bid": X, "ask": Y}
        open_positions: Optional[int] = None,
    ) -> Optional[TradeSignal]:
        """
        Decide trade action based on chart analysis + strategy rules.

        Args:
            chart_analysis: Output from ChartAnalyst
            symbol: Trading symbol
            timeframe: Chart timeframe
            current_price: Current bid/ask
            open_positions: Number of currently open positions (for max limit)

        Returns:
            TradeSignal if trade should be taken, None otherwise
        """
        try:
            prompt = self._build_execution_prompt(
                chart_analysis, symbol, timeframe, current_price
            )

            message = self.client.messages.create(
                model=self.model,
                max_tokens=1200,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )

            response_text = message.content[0].text
            signal_dict = self._parse_signal(response_text)

            if signal_dict.get("action") == "HOLD":
                logger.info(f"⏸️ HOLD signal for {symbol}: {signal_dict.get('reason')}")
                return None

            # Convert to TradeSignal
            signal = TradeSignal(
                action=TradeAction[signal_dict.get("action", "HOLD")],
                symbol=symbol,
                entry_price=signal_dict.get("entry_price", current_price.get("ask", 0)),
                stop_loss=signal_dict.get("stop_loss", 0),
                take_profit=signal_dict.get("take_profit", 0),
                position_size=signal_dict.get("position_size", 0.1),
                confidence=signal_dict.get("confidence", 0.5),
                reason=signal_dict.get("reason", ""),
                risk_reward_ratio=signal_dict.get("risk_reward_ratio", 0),
                max_loss_pct=signal_dict.get("max_loss_pct", 1.0),
                timeframe=timeframe,
            )

            # Log signal
            self.trade_log.append({
                "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
                "signal": signal.to_dict(),
                "chart_analysis": chart_analysis,
            })

            logger.info(
                f"🎯 Trade Signal: {signal.action.value} {symbol} @ {signal.entry_price} | "
                f"SL: {signal.stop_loss}, TP: {signal.take_profit} | Conf: {signal.confidence:.1%}"
            )

            return signal

        except Exception as e:
            logger.error(f"Strategy execution error: {e}")
            return None

    def _build_execution_prompt(
        self,
        chart_analysis: Dict,
        symbol: str,
        timeframe: str,
        current_price: Dict,
    ) -> str:
        """Build decision prompt for Claude."""

        strategy_str = json.dumps(self.strategy_config, indent=2)

        return f"""
You are a professional trade execution engine. Decide whether to trade based on:

1. **Chart Analysis**: {json.dumps(chart_analysis)}
2. **Symbol**: {symbol} {timeframe}
3. **Current Price**: Bid {current_price.get('bid', 'N/A')}, Ask {current_price.get('ask', 'N/A')}
4. **Strategy Rules**: {strategy_str}

Your decision criteria:
- ENTRY: Chart patterns + strategy alignment (mean reversion near 2σ OR breakout with volume)
- SL placement: Below nearest support (for BUY) or above nearest resistance (for SELL), min 20 pips
- TP placement: Risk:Reward ratio of 1:2.5 (e.g. risk 20 pips → TP 50 pips)
- SIZE: 0.1 lots for demo, scale with confidence
- HOLD: If chart sentiment is weak (<0.6 confidence) or no clear entry zone

Return JSON only:

{{
  "action": "BUY|SELL|HOLD",
  "entry_price": float,
  "stop_loss": float,
  "take_profit": float,
  "position_size": 0.1-1.0,
  "confidence": 0.0-1.0,
  "risk_reward_ratio": float,
  "max_loss_pct": float,
  "reason": "Brief explanation of decision"
}}

Be conservative in demo. Only trade when pattern + strategy alignment is clear.
"""

    def _parse_signal(self, response_text: str) -> Dict:
        """Parse Claude's decision JSON."""
        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            json_str = response_text[start:end]
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Could not parse signal JSON")
            return {"action": "HOLD", "reason": "Parse error"}

    def get_trade_statistics(self) -> Dict:
        """Get stats from trade log."""
        if not self.trade_log:
            return {"total_signals": 0}

        actions = [log["signal"]["action"] for log in self.trade_log]
        return {
            "total_signals": len(self.trade_log),
            "buys": actions.count("BUY"),
            "sells": actions.count("SELL"),
            "holds": actions.count("HOLD"),
            "avg_confidence": sum(log["signal"]["confidence"] for log in self.trade_log) / len(self.trade_log),
        }
