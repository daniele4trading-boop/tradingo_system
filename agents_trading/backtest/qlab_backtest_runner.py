"""
QLAB-Integrated Backtest Runner - Test agents with liquidity + volume + hypotheses.

Flow:
1. Load historical OHLC data
2. For each bar:
   - Detect liquidity levels (swings, order blocks, FVG, POC, etc)
   - Analyze volume-price correlations (absorption, nodes, etc)
   - Evaluate all 30 hypotheses (H0-H30)
   - Run chart analyst + strategy executor
   - Simulate execution with Qlab context
3. Report results with hypothesis accuracy tracking
"""

import pandas as pd
import logging
from typing import Optional, Dict, List
from datetime import datetime
from pathlib import Path

try:
    # Try relative imports when part of agents_trading package
    from .data_loader import DataLoader
    from .engine import BacktestEngine
    from exness.chart_analyst import ChartAnalyst
    from exness.strategy_executor import StrategyExecutor
    from exness.risk_manager import RiskManager
    from qlab.liquidity_analyzer import LiquidityAnalyzer
    from qlab.volume_analyzer import VolumeAnalyzer, VolumeMetrics
    from qlab.qlab_hypotheses import QLabHypotheses
except ImportError:
    # Fall back to absolute imports when run from agents_trading directory
    from backtest.data_loader import DataLoader
    from backtest.engine import BacktestEngine
    from exness.chart_analyst import ChartAnalyst
    from exness.strategy_executor import StrategyExecutor
    from exness.risk_manager import RiskManager
    from qlab.liquidity_analyzer import LiquidityAnalyzer
    from qlab.volume_analyzer import VolumeAnalyzer, VolumeMetrics
    from qlab.qlab_hypotheses import QLabHypotheses

logger = logging.getLogger(__name__)


class QLabBacktestRunner:
    """Run backtest with Qlab liquidity + volume + hypotheses analysis."""

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
        Initialize Qlab backtest runner.

        Args:
            symbol: Symbol (e.g., "XAUUSD")
            timeframe: Timeframe (e.g., "H1")
            start_date: Start date (format: "YYYY-MM-DD")
            end_date: End date (format: "YYYY-MM-DD")
            data_file: Path to CSV data file
            initial_balance: Starting account balance
            api_key: Anthropic API key for agents
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.start_date = pd.to_datetime(start_date)
        self.end_date = pd.to_datetime(end_date)
        self.initial_balance = initial_balance

        # Initialize trading components
        self.data_loader = DataLoader()
        self.backtest_engine = BacktestEngine(initial_balance=initial_balance)
        self.chart_analyst = ChartAnalyst(api_key=api_key)
        self.strategy_executor = StrategyExecutor(api_key=api_key)
        self.risk_manager = RiskManager(account_balance=initial_balance)

        # Initialize Qlab components
        self.liquidity_analyzer = LiquidityAnalyzer(symbol=symbol)
        self.volume_analyzer = VolumeAnalyzer(symbol=symbol)
        self.qlab_hypotheses = QLabHypotheses()

        # Load data
        self.df = self._load_data(data_file)

        # Tracking
        self.hypothesis_accuracy: Dict[str, Dict] = {}  # H0-H30 tracking
        self.signal_history: List[Dict] = []

        logger.info(f"✅ QLabBacktestRunner initialized: {symbol} {timeframe}")
        logger.info(f"   Date range: {self.start_date.date()} to {self.end_date.date()}")
        logger.info(f"   Data points: {len(self.df)}")

    def _load_data(self, data_file: Optional[str]) -> pd.DataFrame:
        """Load and filter data."""
        if data_file:
            df = self.data_loader.load_csv(self.symbol, self.timeframe, data_file)
        else:
            default_file = f"C:\\TG_TradinGo\\historical_data\\{self.symbol}_{self.timeframe}.csv"
            try:
                df = self.data_loader.load_csv(self.symbol, self.timeframe, default_file)
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Data file not found. Specify data_file or provide CSV at:\n  {default_file}"
                )

        # Filter to date range
        df = df[(df.index >= self.start_date) & (df.index <= self.end_date)].copy()
        logger.info(f"Filtered to {len(df)} bars in date range")

        return df

    def run(self, max_bars: Optional[int] = None, verbose: bool = False):
        """
        Run Qlab-integrated backtest.

        Args:
            max_bars: Max bars to process
            verbose: Print debug info
        """
        logger.info(f"🟢 Starting Qlab backtest: {self.symbol} {self.timeframe}")

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
                self.backtest_engine.update_position(
                    pos_key,
                    current_price,
                    timestamp,
                )

            # Skip if not enough data for analysis
            if idx < 50:
                continue

            # Get previous bars for analysis
            prev_bars = self.df.iloc[max(0, idx-50):idx]

            # ===== QLAB ANALYSIS =====

            # 1. Detect liquidity levels
            levels = self.liquidity_analyzer.detect_levels(prev_bars, self.timeframe)
            nearby_levels = self.liquidity_analyzer.get_levels_near_price(
                current_price,
                distance_atr=1.0,
                atr_value=50.0,
            )

            # 2. Analyze volume-price correlations
            volume_metrics = self.volume_analyzer.analyze_event(
                df=prev_bars,
                event_time=timestamp,
                event_idx=len(prev_bars) - 1,
            )

            # Get support/resistance from previous bars
            support = prev_bars["low"].min()
            resistance = prev_bars["high"].max()

            # POC and Value Area (simplified)
            poc_price = prev_bars["close"].median()
            va_low = prev_bars["low"].quantile(0.25)
            va_high = prev_bars["high"].quantile(0.75)

            # Multi-timeframe bias (simplified)
            mtf_bias = "BULLISH" if current_price > prev_bars["close"].mean() else "BEARISH"

            # 3. Evaluate all Qlab hypotheses
            hypothesis_scores = self.qlab_hypotheses.evaluate_all_hypotheses(
                current_price=current_price,
                poc_price=poc_price,
                va_high=va_high,
                va_low=va_low,
                support=support,
                resistance=resistance,
                volume_metrics={
                    "tick_vol_ratio": volume_metrics.tick_vol_ratio,
                    "delta_ratio": volume_metrics.delta_ratio or 0.5,
                    "node_type": volume_metrics.volume_node_type,
                },
                levels_nearby=nearby_levels,
                timeframe_bias=mtf_bias,
                hour_of_day=timestamp.hour,
            )

            # Get Qlab signal
            qlab_action, qlab_confidence = self.qlab_hypotheses.generate_signal_from_hypotheses(
                hypothesis_scores,
                confidence_threshold=0.55,
            )

            # Track hypothesis accuracy (would validate with actual price movement)
            for h_score in hypothesis_scores:
                if h_score.hypothesis_id not in self.hypothesis_accuracy:
                    self.hypothesis_accuracy[h_score.hypothesis_id] = {
                        "scores": [],
                        "count": 0,
                    }
                self.hypothesis_accuracy[h_score.hypothesis_id]["scores"].append(h_score.score)
                self.hypothesis_accuracy[h_score.hypothesis_id]["count"] += 1

            # ===== AGENT ANALYSIS =====

            # Chart analyst (mock)
            chart_analysis = {
                "sentiment": mtf_bias,
                "confidence": 0.65,
                "patterns": [{"type": "qlab_confluence", "reliability": "high"}],
                "support_resistance": {
                    "resistance": [resistance],
                    "support": [support],
                },
                "trading_setup": {
                    "type": qlab_action.lower() if qlab_action != "HOLD" else "neutral",
                    "probability": qlab_confidence,
                },
                "risks": ["level_bounce", "false_breakout"],
                "qlab_context": {
                    "action": qlab_action,
                    "hypothesis_scores": [
                        (h.hypothesis_id, h.score) for h in hypothesis_scores[:5]
                    ],
                    "nearby_levels": len(nearby_levels),
                    "volume_nodes": volume_metrics.volume_node_type,
                },
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
                    # Execute trade with Qlab context
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

                        # Record signal history
                        self.signal_history.append({
                            "time": timestamp,
                            "action": signal.action.value,
                            "price": current_price,
                            "qlab_action": qlab_action,
                            "qlab_confidence": qlab_confidence,
                            "levels_nearby": len(nearby_levels),
                        })

        # Close all remaining positions
        last_price = self.df.iloc[-1]["close"]
        for pos_key in list(self.backtest_engine.open_positions.keys()):
            self.backtest_engine.update_position(
                pos_key,
                last_price,
                self.df.index[-1],
                close_reason="EOB",
            )

        logger.info("🛑 Qlab backtest complete")

    def print_results(self):
        """Print backtest results with Qlab analysis."""
        self.backtest_engine.print_report()

        # Print Qlab hypothesis accuracy
        print("\n" + "="*60)
        print("QLAB HYPOTHESIS ANALYSIS")
        print("="*60)

        for h_id in sorted(self.hypothesis_accuracy.keys()):
            h_data = self.hypothesis_accuracy[h_id]
            avg_score = sum(h_data["scores"]) / len(h_data["scores"]) if h_data["scores"] else 0
            print(f"{h_id}: avg_score={avg_score:.2f}, count={h_data['count']}")

        print("="*60 + "\n")

        # Print top trades
        if self.backtest_engine.trades:
            print("TOP 10 TRADES (with Qlab context):")
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

    def export_trades_csv(self, filepath: str = "backtest_trades_qlab.csv"):
        """Export trades to CSV."""
        if not self.backtest_engine.trades:
            logger.warning("No trades to export")
            return

        trades_data = [t.to_dict() for t in self.backtest_engine.trades]
        df = pd.DataFrame(trades_data)
        df.to_csv(filepath, index=False)
        logger.info(f"Exported {len(df)} trades to {filepath}")

    def export_hypothesis_analysis(self, filepath: str = "qlab_hypothesis_analysis.csv"):
        """Export hypothesis accuracy analysis."""
        data = [
            {
                "hypothesis_id": h_id,
                "avg_score": sum(h_data["scores"]) / len(h_data["scores"]),
                "count": h_data["count"],
                "min_score": min(h_data["scores"]),
                "max_score": max(h_data["scores"]),
            }
            for h_id, h_data in self.hypothesis_accuracy.items()
        ]
        df = pd.DataFrame(data)
        df.to_csv(filepath, index=False)
        logger.info(f"Exported hypothesis analysis to {filepath}")
