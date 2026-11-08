"""Trading Agents - Claude-powered autonomous trading on Exness."""

__version__ = "0.1.0"
__author__ = "Claude Code"
__date__ = "2026-11-08"

from .connectors.mt5_direct import MT5Connector
from .exness.chart_analyst import ChartAnalyst
from .exness.strategy_executor import StrategyExecutor, TradeSignal, TradeAction

__all__ = [
    "MT5Connector",
    "ChartAnalyst",
    "StrategyExecutor",
    "TradeSignal",
    "TradeAction",
]
