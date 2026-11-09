#!/usr/bin/env python3
"""
Trading Orchestrator - Main entry point for autonomous trading.

Coordinates:
1. ChartAnalyst - analyze candlestick patterns
2. StrategyExecutor - generate trade signals
3. RiskManager - validate against limits
4. MT5Connector - execute orders

Usage:
    python main.py --duration 120 --symbols XAUUSD,EURUSD --mt5-path "C:\\Program Files\\MT5"
"""

import sys
import argparse
import logging
import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from exness.chart_analyst import ChartAnalyst
from exness.strategy_executor import StrategyExecutor
from exness.risk_manager import RiskManager
from exness.mt5_connector import MT5Connector

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """Main trading loop coordinator."""

    def __init__(
        self,
        symbols: List[str],
        mt5_path: str = "C:\\Program Files\\MT5",
        account: int = 480578231,
        api_key: Optional[str] = None,
        initial_balance: float = 1000.0,
    ):
        """
        Initialize orchestrator.

        Args:
            symbols: List of symbols to trade
            mt5_path: Path to MT5 terminal
            account: Exness account number
            api_key: Anthropic API key
            initial_balance: Starting balance
        """
        self.symbols = symbols
        self.mt5_path = mt5_path
        self.account = account
        self.initial_balance = initial_balance

        # Initialize components
        self.chart_analyst = ChartAnalyst(api_key=api_key)
        self.strategy_executor = StrategyExecutor(api_key=api_key)
        self.risk_manager = RiskManager(account_balance=initial_balance)
        self.mt5 = MT5Connector(
            broker_terminal_path=mt5_path,
            account=account,
            broker_server="Exness-MT5Trial11",
        )

        # Statistics
        self.stats = {
            "start_time": datetime.now(),
            "trades_generated": 0,
            "trades_executed": 0,
            "trades_won": 0,
            "trades_lost": 0,
            "daily_pnl": 0.0,
            "session_pnl": 0.0,
            "max_drawdown": 0.0,
        }

        logger.info("🚀 Trading Orchestrator initialized")
        logger.info(f"   Symbols: {', '.join(symbols)}")
        logger.info(f"   Account: {account}")

    def connect(self, password: str) -> bool:
        """Connect to MT5."""
        logger.info("Connecting to MT5...")
        if self.mt5.connect(password):
            logger.info("✅ Connected successfully")
            return True
        else:
            logger.error("❌ Connection failed")
            return False

    def disconnect(self):
        """Disconnect from MT5."""
        self.mt5.disconnect()
        logger.info("Disconnected from MT5")

    def run(self, duration_minutes: int = 60, cycle_seconds: int = 30):
        """
        Run main trading loop.

        Args:
            duration_minutes: How long to run (minutes)
            cycle_seconds: How often to analyze (seconds)
        """
        logger.info(f"🟢 Starting trading loop for {duration_minutes} minutes")
        logger.info(f"   Analysis cycle: every {cycle_seconds}s")

        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes)

        cycle_count = 0

        try:
            while datetime.now() < end_time:
                cycle_count += 1
                cycle_start = datetime.now()

                logger.info(f"\n{'='*60}")
                logger.info(f"CYCLE {cycle_count} - {cycle_start.strftime('%H:%M:%S')}")
                logger.info(f"{'='*60}")

                # For each symbol
                for symbol in self.symbols:
                    try:
                        self._analyze_and_trade(symbol, cycle_count)
                    except Exception as e:
                        logger.error(f"Error trading {symbol}: {e}")

                # Update statistics
                self.stats["daily_pnl"] = self.risk_manager.daily_pnl
                self.stats["session_pnl"] = self.risk_manager.current_balance - self.initial_balance

                # Wait for next cycle
                elapsed = (datetime.now() - cycle_start).total_seconds()
                sleep_time = max(0, cycle_seconds - elapsed)
                if sleep_time > 0:
                    logger.info(f"Waiting {sleep_time:.1f}s until next cycle...")
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            logger.info("\n⚠️  Interrupted by user")

        finally:
            # Close all positions
            logger.info("\nClosing all open positions...")
            positions = self.mt5.get_open_positions()
            logger.info(f"Open positions: {len(positions)}")

            self.print_session_report()

    def _analyze_and_trade(self, symbol: str, cycle: int):
        """Analyze one symbol and execute if signal."""
        # Get current quote
        quote = self.mt5.get_symbol_info(symbol)
        if not quote:
            logger.warning(f"Could not get quote for {symbol}")
            return

        mid_price = (quote["bid"] + quote["ask"]) / 2
        logger.info(f"\n{symbol}: Bid {quote['bid']:.2f} | Ask {quote['ask']:.2f}")

        # For demo: use mock chart analysis
        chart_analysis = {
            "sentiment": "BULLISH" if cycle % 3 == 0 else ("BEARISH" if cycle % 3 == 1 else "NEUTRAL"),
            "confidence": 0.60 + (cycle % 10) * 0.02,
            "patterns": [{"type": "breakout", "reliability": "medium"}],
            "support_resistance": {
                "support": [mid_price - 20],
                "resistance": [mid_price + 20],
            },
            "trading_setup": {
                "type": "breakout",
                "probability": 0.65,
            },
            "risks": ["pullback", "news"],
            "qlab_context": {},
        }

        logger.info(f"Chart: {chart_analysis['sentiment']} ({chart_analysis['confidence']:.0%})")

        # Get trading signal
        signal = self.strategy_executor.execute_strategy(
            chart_analysis=chart_analysis,
            symbol=symbol,
            timeframe="H1",
            current_price={"bid": quote["bid"], "ask": quote["ask"]},
            open_positions=len(self.mt5.get_open_positions()),
        )

        self.stats["trades_generated"] += 1

        if not signal or signal.action.value == "HOLD":
            logger.info("No signal or HOLD")
            return

        # Validate against risk limits
        validation = self.risk_manager.validate_signal(
            signal,
            num_open_positions=len(self.mt5.get_open_positions()),
        )

        if not validation.valid:
            logger.warning(f"Risk check failed: {validation.reason}")
            return

        logger.info(f"Risk check passed: {validation.reason}")

        # Execute trade
        entry_price = quote["ask"] if signal.action.value == "BUY" else quote["bid"]

        order_result = self.mt5.place_order(
            symbol=symbol,
            action=signal.action.value,
            entry_price=entry_price,
            volume=validation.adjusted_size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            comment=f"Agent-{signal.action.value}",
        )

        if order_result.success:
            logger.info(f"✅ Trade executed: {signal.action.value} {validation.adjusted_size:.2f}L")
            self.stats["trades_executed"] += 1
            self.risk_manager.on_trade_opened(
                entry_price=entry_price,
                volume=validation.adjusted_size,
            )
        else:
            logger.error(f"❌ Trade failed: {order_result.error}")

    def print_session_report(self):
        """Print session summary."""
        duration = datetime.now() - self.stats["start_time"]

        print("\n" + "="*60)
        print("SESSION REPORT")
        print("="*60)
        print(f"Duration: {duration}")
        print(f"Signals generated: {self.stats['trades_generated']}")
        print(f"Trades executed: {self.stats['trades_executed']}")
        print(f"Won: {self.stats['trades_won']} | Lost: {self.stats['trades_lost']}")
        print(f"Session P&L: ${self.stats['session_pnl']:+.2f}")
        print(f"Daily P&L: ${self.stats['daily_pnl']:+.2f}")
        print(f"Max Drawdown: {self.stats['max_drawdown']:.1f}%")
        print(f"Final Balance: ${self.risk_manager.current_balance:.2f}")
        print("="*60 + "\n")

        # Save session report
        report = {
            "timestamp": self.stats["start_time"].isoformat(),
            "duration_seconds": int(duration.total_seconds()),
            "symbols": self.symbols,
            "account": self.account,
            **self.stats,
        }

        filename = f"agents_trading_session_{self.stats['start_time'].strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"Session report saved: {filename}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Autonomous trading with Claude + MT5")

    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Trading duration in minutes (default: 60)",
    )
    parser.add_argument(
        "--symbols",
        default="XAUUSD,EURUSD",
        help="Symbols to trade (comma-separated, default: XAUUSD,EURUSD)",
    )
    parser.add_argument(
        "--account",
        type=int,
        default=480578231,
        help="Exness account number (default: 480578231)",
    )
    parser.add_argument(
        "--mt5-path",
        default="C:\\Program Files\\MT5",
        help="MT5 terminal path (default: C:\\\\Program Files\\\\MT5)",
    )
    parser.add_argument(
        "--balance",
        type=float,
        default=1000.0,
        help="Initial balance (default: 1000.0)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (default: $ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: don't connect to MT5, use mock mode",
    )
    parser.add_argument(
        "--cycle-seconds",
        type=int,
        default=30,
        help="Analysis cycle interval in seconds (default: 30)",
    )

    args = parser.parse_args()

    # Get API key
    api_key = args.api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        logger.warning("No API key found. Chart analysis will use mock mode.")

    # Get password
    if not args.dry_run:
        password = os.getenv(f"EXNESS_MT5_PASSWORD_{args.account}")
        if not password:
            logger.error(f"Password not found in EXNESS_MT5_PASSWORD_{args.account}")
            logger.error("Set environment variable: export EXNESS_MT5_PASSWORD_480578231='password'")
            return 1
    else:
        password = "DEMO"

    # Initialize orchestrator
    symbols = [s.strip() for s in args.symbols.split(",")]
    orchestrator = TradingOrchestrator(
        symbols=symbols,
        mt5_path=args.mt5_path,
        account=args.account,
        api_key=api_key,
        initial_balance=args.balance,
    )

    # Connect and run
    if args.dry_run:
        logger.info("DRY RUN MODE - using mock MT5 connector")

    if orchestrator.connect(password):
        try:
            orchestrator.run(
                duration_minutes=args.duration,
                cycle_seconds=args.cycle_seconds,
            )
        finally:
            orchestrator.disconnect()
        return 0
    else:
        logger.error("Failed to connect to MT5")
        return 1


if __name__ == "__main__":
    sys.exit(main())
