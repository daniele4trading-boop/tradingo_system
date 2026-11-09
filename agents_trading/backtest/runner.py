"""
Backtest Runner - Execute agents on historical data with Qlab context.

Usage:
    from agents_trading.backtest.runner import BacktestRunner

    runner = BacktestRunner(
        symbol="XAUUSD",
        timeframe="H1",
        start_date="2025-01-01",
        end_date="2026-11-08",
    )
    runner.run()
    runner.print_results()
"""

import pandas as pd
import logging
from typing import Optional
from datetime import datetime
from pathlib import Path

from agents_trading.backtest.data_loader import DataLoader
from agents_trading.backtest.engine import BacktestEngine
from agents_trading.exness.chart_analyst import ChartAnalyst
from agents_trading.exness.strategy_executor import StrategyExecutor
from agents_trading.exness.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class BacktestRunner:
    """Run backtest with agents."""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        data_file: Optional[str] = None,
        initial_balance: float = 1000.0,
        api_key: Optional[str] = None,
    ):
        """
        Initialize backtest runner.

        Args:
            symbol: Symbol (e.g., "XAUUSD")
            timeframe: Timeframe (e.g., "H1")
            start_date: Start date (format: "YYYY-MM-DD")
            end_date: End date (format: "YYYY-MM-DD")
            data_file: Path to CSV data file. If None, looks for default location
            initial_balance: Starting account balance
            api_key: Anthropic API key for agents
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.start_date = pd.to_datetime(start_date)
        self.end_date = pd.to_datetime(end_date)
        self.initial_balance = initial_balance

        # Initialize components
        self.data_loader = DataLoader()
        self.backtest_engine = BacktestEngine(initial_balance=initial_balance)
        self.chart_analyst = ChartAnalyst(api_key=api_key)
        self.strategy_executor = StrategyExecutor(api_key=api_key)
        self.risk_manager = RiskManager(account_balance=initial_balance)

        # Load data
        self.df = self._load_data(data_file)

        logger.info(f"✅ BacktestRunner initialized: {symbol} {timeframe}")
        logger.info(f"   Date range: {self.start_date.date()} to {self.end_date.date()}")
        logger.info(f"   Data points: {len(self.df)}")

    def _load_data(self, data_file: Optional[str]) -> pd.DataFrame:
        """Load and filter data."""
        if data_file:
            df = self.data_loader.load_csv(self.symbol, self.timeframe, data_file)
        else:
            # Try default location
            default_file = f"C:\\TG_TradinGo\\historical_data\\{self.symbol}_{self.timeframe}.csv"
            try:
                df = self.data_loader.load_csv(self.symbol, self.timeframe, default_file)
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Data file not found. Specify data_file parameter or provide CSV at:\n"
                    f"  {default_file}"
                )

        # Filter to date range
        df = df[(df.index >= self.start_date) & (df.index <= self.end_date)].copy()

        logger.info(f"Filtered to {len(df)} bars in date range")

        return df

    def run(self, max_bars: Optional[int] = None, verbose: bool = False):
        """
        Run backtest simulation.

        Args:
            max_bars: Max bars to process (for quick testing)
            verbose: Print per-bar debug info
        """
        logger.info(f"🟢 Starting backtest: {self.symbol} {self.timeframe}")

        bars_to_process = min(len(self.df), max_bars) if max_bars else len(self.df)

        for idx, (timestamp, row) in enumerate(self.df.iterrows()):
            if idx >= bars_to_process:
                break

            if idx % 100 == 0:
                logger.info(f"  Processing bar {idx}/{bars_to_process} ({timestamp.date()})")

            # Get current price
            current_price = row["close"]
            current_bid = row["close"] - (row["close"] - row["low"]) * 0.5
            current_ask = row["close"] + (row["high"] - row["close"]) * 0.5

            # Update open positions
            for pos_key in list(self.backtest_engine.open_positions.keys()):
                closed_trade = self.backtest_engine.update_position(
                    pos_key,
                    current_price,
                    timestamp,
                )

            # Skip if not enough data for analysis (need previous bars)
            if idx < 20:
                continue

            # Get previous bars for analysis (simulate chart screenshot)
            prev_bars = self.df.iloc[max(0, idx-50):idx]

            # Chart analyst (mock: use technical indicators)
            chart_analysis = {
                "sentiment": "BULLISH" if current_price > prev_bars["close"].mean() else "BEARISH",
                "confidence": 0.65,
                "patterns": [{"type": "breakout", "reliability": "medium"}],
                "support_resistance": {
                    "resistance": [prev_bars["high"].max()],
                    "support": [prev_bars["low"].min()],
                },
                "trading_setup": {
                    "type": "breakout" if current_price > prev_bars["high"].max() else "pullback",
                    "probability": 0.65,
                },
                "risks": ["pullback", "news"],
                "notes": "Backtest analysis",
            }

            # Strategy executor
            signal = self.strategy_executor.execute_strategy(
                chart_analysis=chart_analysis,
                symbol=self.symbol,
                timeframe=self.timeframe,
                current_price={"bid": current_bid, "ask": current_ask},
                open_positions=len(self.backtest_engine.open_positions),
            )

            # Risk validation
            if signal and signal.action.value != "HOLD":
                risk_validation = self.risk_manager.validate_signal(signal)

                if risk_validation.valid:
                    # Execute trade
                    success, reason = self.backtest_engine.execute_trade(
                        symbol=self.symbol,
                        direction=signal.action.value,
                        entry_price=current_ask if signal.action.value == "BUY" else current_bid,
                        volume=signal.position_size,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        timestamp=timestamp,
                    )

                    if success:
                        self.risk_manager.on_trade_opened(
                            entry_price=signal.entry_price,
                            volume=signal.position_size,
                            exposure_pct=2.0,
                        )

        # Close all remaining positions at last price
        last_price = self.df.iloc[-1]["close"]
        for pos_key in list(self.backtest_engine.open_positions.keys()):
            self.backtest_engine.update_position(
                pos_key,
                last_price,
                self.df.index[-1],
                close_reason="EOB",
            )

        logger.info("🛑 Backtest complete")

    def print_results(self):
        """Print backtest results."""
        self.backtest_engine.print_report()

        # Print trade details
        if self.backtest_engine.trades:
            print("\nTOP 10 TRADES:")
            print("-" * 80)
            sorted_trades = sorted(
                self.backtest_engine.trades,
                key=lambda t: t.pnl if t.pnl else 0,
                reverse=True
            )
            for i, trade in enumerate(sorted_trades[:10]):
                print(f"{i+1}. {trade.direction} {trade.volume} {trade.symbol} | "
                      f"Entry: {trade.entry_price:.2f} | Exit: {trade.exit_price:.2f} | "
                      f"P&L: {trade.pnl:+.2f}$ ({trade.pnl_pct:+.1f}%)")

    def export_trades_csv(self, filepath: str = "backtest_trades.csv"):
        """Export trades to CSV."""
        if not self.backtest_engine.trades:
            logger.warning("No trades to export")
            return

        trades_data = [t.to_dict() for t in self.backtest_engine.trades]
        df = pd.DataFrame(trades_data)
        df.to_csv(filepath, index=False)
        logger.info(f"Exported {len(df)} trades to {filepath}")
