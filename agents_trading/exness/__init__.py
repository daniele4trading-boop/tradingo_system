"""
Exness Trading Agents - Autonomous trading with Claude + MT5

Modules:
- chart_analyst: Claude vision analysis of candlestick charts
- strategy_executor: Convert chart analysis → trade signals
- risk_manager: Validate and enforce risk limits
- mt5_connector: Direct MT5 terminal connection
"""

from .chart_analyst import ChartAnalyst, ChartAnalysis
from .strategy_executor import StrategyExecutor, TradeSignal, TradeAction
from .risk_manager import RiskManager, RiskValidation
from .mt5_connector import MT5Connector, OrderResult

__all__ = [
    "ChartAnalyst",
    "ChartAnalysis",
    "StrategyExecutor",
    "TradeSignal",
    "TradeAction",
    "RiskManager",
    "RiskValidation",
    "MT5Connector",
    "OrderResult",
]
